#!/usr/bin/env python3
"""Automate homework problems through ChatGPT via Chrome DevTools Protocol.

Unlike homework_automation.py (which drives the real keyboard/mouse), this
attaches Playwright to your ALREADY-OPEN Chrome/Edge and talks to the ChatGPT
tab directly through the DOM: it types into the composer, clicks Send, waits
for streaming to finish, and reads the answer's code block — no clipboard,
no window focus, no Ctrl+A/Ctrl+C. You can keep using your computer while
it runs.

One-time setup — start your browser with remote debugging enabled:

  Windows:  chrome.exe --remote-debugging-port=9222
  macOS:    open -a "Google Chrome" --args --remote-debugging-port=9222
  Linux:    google-chrome --remote-debugging-port=9222

then open https://chatgpt.com and log in. Run:

  pip install playwright        (no "playwright install" needed — it
                                 attaches to your existing browser)
  python homework_automation_cdp.py
"""

import argparse
import ast
import os
import re
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:
    sys.exit("Missing dependency. Run: pip install playwright")

PROBLEMS_DIR = Path(os.environ.get("HOMEWORK_PROBLEMS_DIR", Path(__file__).parent / "problems"))
SOLUTIONS_DIR = Path(os.environ.get("HOMEWORK_SOLUTIONS_DIR", Path(__file__).parent / "solutions"))
CLAIMS_DIR = SOLUTIONS_DIR / ".claims"
CHATGPT_URL = os.environ.get("CHATGPT_URL", "https://chatgpt.com/")

PROMPT_TEMPLATE = """Please solve the following homework problem in Python.
Reply with ONLY a single Python code block, no explanation outside it
(use comments inside the code if needed).

{problem}"""

COMPOSER = "#prompt-textarea"
SEND_BUTTON = '[data-testid="send-button"]'
STOP_BUTTON = '[data-testid="stop-button"]'
ASSISTANT_MSG = '[data-message-author-role="assistant"]'


def is_solved(path: Path) -> bool:
    """A problem counts as solved only if its .py exists AND parses as
    Python — so --skip-existing retries garbage (prose, refusals,
    truncated replies) instead of locking it in forever."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not source.strip():
        return False
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def find_chatgpt_page(browser):
    for context in browser.contexts:
        for page in context.pages:
            if ("chatgpt.com" in page.url or "chat.openai.com" in page.url
                    or page.url.startswith(CHATGPT_URL)):
                return page
    return None


# --- dynamic work queue (shared by all workers via the filesystem) ---------
#
# A worker claims a problem by exclusively creating solutions/.claims/<name>.claim
# (O_CREAT|O_EXCL is atomic on Windows and POSIX alike), solves it, then
# releases the claim; is_solved() keeps others away afterwards. If a worker
# dies mid-problem its claim goes stale and is taken over after the TTL —
# the takeover renames the stale file first (os.replace, atomic: exactly one
# renamer wins) so two workers can't both delete-and-reclaim it. Every claim
# stores its owner's token, and release/attempt-count only happen while the
# token still matches, so a worker that lost its claim to a TTL takeover
# can't disturb the new owner. A problem is given up after --max-retries
# failed attempts.

WORKER_TOKEN = f"{os.getpid()}-{os.urandom(4).hex()}"


def try_claim(name: str, ttl: float) -> bool:
    path = CLAIMS_DIR / f"{name}.claim"
    try:
        if time.time() - path.stat().st_mtime > ttl:
            grave = CLAIMS_DIR / f"{name}.stale-{WORKER_TOKEN}"
            os.replace(path, grave)  # atomic: only one taker succeeds
            grave.unlink()
    except OSError:
        pass  # no claim, not stale, or someone else won the takeover
    try:
        with open(path, "x") as f:
            f.write(WORKER_TOKEN)
        return True
    except OSError:
        return False


def owns_claim(name: str) -> bool:
    try:
        return (CLAIMS_DIR / f"{name}.claim").read_text() == WORKER_TOKEN
    except OSError:
        return False


def release_claim(name: str) -> None:
    if not owns_claim(name):
        return  # a TTL takeover happened; the claim belongs to someone else
    try:
        (CLAIMS_DIR / f"{name}.claim").unlink()
    except OSError:
        pass


def get_attempts(name: str) -> int:
    try:
        return int((CLAIMS_DIR / f"{name}.attempts").read_text())
    except (OSError, ValueError):
        return 0


def bump_attempts(name: str) -> None:
    (CLAIMS_DIR / f"{name}.attempts").write_text(str(get_attempts(name) + 1))


def new_chat(page) -> None:
    """Open a fresh conversation so each problem starts with empty context.

    Keeps responses fast and the DOM small — in one long conversation
    ChatGPT re-renders/virtualizes the message list, which broke the old
    count-based answer detection from around the 4th problem on.
    """
    page.goto(CHATGPT_URL, wait_until="domcontentloaded")
    page.wait_for_selector(COMPOSER, timeout=30_000)


def _last_message_id(page) -> str | None:
    messages = page.locator(ASSISTANT_MSG)
    n = messages.count()
    if n == 0:
        return None
    return messages.nth(n - 1).get_attribute("data-message-id")


def _read_reply(reply) -> str | None:
    code_blocks = reply.locator("pre code")
    if code_blocks.count() > 0:
        return code_blocks.nth(code_blocks.count() - 1).inner_text()
    # no code block — fall back to the whole reply text
    text = reply.inner_text().strip()
    return text or None


def ask(page, prompt: str, timeout: float, poll: float) -> str | None:
    """Send one prompt and return the code from the new assistant reply.

    The new reply is identified by its data-message-id being different
    from the last assistant message before sending — NOT by message
    count, which is unreliable once ChatGPT virtualizes long threads.
    A reply only counts as finished when the Stop button is gone AND its
    text is unchanged across two consecutive polls, so long "thinking"
    phases and Stop-button flicker between phases can't truncate it.
    """
    id_before = _last_message_id(page)

    composer = page.locator(COMPOSER)
    composer.click()
    # insert_text handles newlines safely (Enter alone would submit early)
    page.keyboard.insert_text(prompt)
    page.locator(SEND_BUTTON).click()

    deadline = time.time() + timeout
    stable = None
    while time.time() < deadline:
        time.sleep(poll)
        # still generating/thinking while the Stop button is visible
        if page.locator(STOP_BUTTON).count() > 0:
            stable = None
            continue
        messages = page.locator(ASSISTANT_MSG)
        n = messages.count()
        if n == 0:
            continue
        reply = messages.nth(n - 1)
        reply_id = reply.get_attribute("data-message-id")
        if reply_id is not None and reply_id == id_before:
            continue  # still the previous answer; ours hasn't rendered yet
        text = _read_reply(reply)
        if text is None:
            continue
        if text == stable:
            return text  # finished: no Stop button and unchanged text
        stable = text
    return stable


def solve_one(page, path: Path, args) -> bool:
    """Solve one problem; True only if a valid solution was saved."""
    name = path.stem
    if not args.same_chat:
        new_chat(page)
        print("  started a new chat")

    prompt = PROMPT_TEMPLATE.format(problem=path.read_text(encoding="utf-8"))
    solution = ask(page, prompt, args.timeout, args.poll)
    if solution is None:
        print(f"  WARNING: no answer within {args.timeout:.0f}s")
        return False

    body = solution.rstrip("\n") + "\n"
    try:
        ast.parse(body)
    except SyntaxError as e:
        # Don't create the .py: a bad reply (prose, refusal, truncation)
        # must not count as solved — it stays retryable.
        failed = SOLUTIONS_DIR / f"{name}.failed.txt"
        failed.write_text(body, encoding="utf-8")
        print(f"  WARNING: reply is not valid Python ({e.msg}, "
              f"line {e.lineno}); kept in {failed}")
        return False
    (SOLUTIONS_DIR / f"{name}.py").write_text(body, encoding="utf-8")
    print(f"  saved {SOLUTIONS_DIR / f'{name}.py'} (valid Python)")
    return True


def run_queue(page, problems: list[Path], args) -> None:
    """Pull problems from the shared queue until none are left.

    Fast workers automatically solve more problems than slow ones, and a
    problem that failed on one worker/account is retried by whichever
    worker gets to it next — up to --max-retries attempts in total.
    """
    ttl = 2 * args.timeout + 120  # a claim older than this belongs to a dead worker
    while True:
        pending = [
            p for p in problems
            if not is_solved(SOLUTIONS_DIR / f"{p.stem}.py")
            and get_attempts(p.stem) < args.max_retries
        ]
        if not pending:
            break
        progress = False
        for path in pending:
            name = path.stem
            if is_solved(SOLUTIONS_DIR / f"{name}.py"):
                continue  # someone else finished it while we scanned
            if not try_claim(name, ttl):
                continue  # someone else is working on it
            progress = True
            print(f"[queue] claimed {path.name}")
            ok = False
            fatal = None
            try:
                ok = solve_one(page, path, args)
            except PlaywrightError as e:
                # Dead page/browser or dropped CDP: this worker can't
                # continue. Do NOT count an attempt — the problem goes back
                # to the queue untouched for the other workers; a zombie
                # worker must never burn the shared retry budget.
                fatal = e
            except Exception as e:
                print(f"  ERROR on {name}: {e}")
            finally:
                if fatal is None and not ok and owns_claim(name):
                    bump_attempts(name)  # safe: we still hold the claim
                release_claim(name)
            if fatal is not None:
                sys.exit(
                    f"[queue] lost the browser while on {name} ({fatal}); "
                    "released the problem for the other workers and exiting."
                )
        if not progress:
            # everything pending is claimed by other live workers — wait for
            # them to finish, fail, or go stale, then rescan
            time.sleep(10)

    solved = sum(1 for p in problems if is_solved(SOLUTIONS_DIR / f"{p.stem}.py"))
    given_up = [p.stem for p in problems
                if not is_solved(SOLUTIONS_DIR / f"{p.stem}.py")
                and get_attempts(p.stem) >= args.max_retries]
    print(f"[queue] finished: {solved}/{len(problems)} solved"
          + (f"; gave up on {', '.join(given_up)} after "
             f"{args.max_retries} attempts each" if given_up else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", help="Only files whose name contains this string")
    parser.add_argument("--port", type=int, default=9222, help="CDP port (default: 9222)")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="CDP host (default: 127.0.0.1 — not 'localhost', which can "
        "resolve to IPv6 ::1 while Chrome listens on IPv4 only)",
    )
    parser.add_argument(
        "--shard",
        help="Work on a subset of the problems, for running several workers "
        "in parallel: K/N means this is worker K of N and takes every "
        "N-th problem starting at the K-th (e.g. 1/3, 2/3, 3/3)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip problems whose solutions/<name>.py already parses as Python",
    )
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Dynamic work queue for parallel workers: atomically claim the "
        "next unsolved problem instead of a fixed --shard split, so fast "
        "workers/accounts automatically do more and failures are retried "
        "by other workers",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Queue mode: give up on a problem after this many failed "
        "attempts across all workers (default: 3)",
    )
    parser.add_argument(
        "--new-tab",
        action="store_true",
        help="Open an own ChatGPT tab in the attached browser instead of "
        "taking over an existing one (required when several workers share "
        "one browser; the tab reuses the browser's login)",
    )
    parser.add_argument(
        "--same-chat",
        action="store_true",
        help="Keep all problems in one conversation instead of starting "
        "a fresh chat per problem (slower and less reliable)",
    )
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="Max seconds to wait per answer (default: 180)")
    parser.add_argument("--poll", type=float, default=2.0,
                        help="Seconds between completion checks (default: 2)")
    args = parser.parse_args()

    problems = sorted(PROBLEMS_DIR.glob("hw*.md"))
    if args.problem:
        problems = [f for f in problems if args.problem in f.name]
    if not problems:
        sys.exit(f"No problem files found in {PROBLEMS_DIR}")
    if args.shard:
        m = re.fullmatch(r"(\d+)/(\d+)", args.shard)
        if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
            sys.exit(f"Bad --shard {args.shard!r}; expected K/N with 1 <= K <= N")
        k, n = int(m.group(1)), int(m.group(2))
        problems = problems[k - 1 :: n]
        if not problems:
            print(f"shard {args.shard}: no problems assigned, nothing to do")
            return

    SOLUTIONS_DIR.mkdir(parents=True, exist_ok=True)
    if args.queue:
        CLAIMS_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        endpoint = f"http://{args.host}:{args.port}"
        try:
            browser = p.chromium.connect_over_cdp(endpoint)
        except Exception as e:
            sys.exit(
                f"Could not attach to the browser at {endpoint}: {e}\n\n"
                "Checklist:\n"
                "  1. Close ALL Chrome windows first (check the system tray too) —\n"
                "     if any Chrome process is still running, the flag is ignored.\n"
                "  2. Start Chrome with BOTH flags (Windows PowerShell):\n"
                '     & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                "--remote-debugging-port=9222 --user-data-dir=C:\\chrome-debug\n"
                "  3. Verify it's listening: open http://127.0.0.1:9222/json/version\n"
                "     in that browser — you should see JSON.\n"
                "  4. Open https://chatgpt.com in it and log in."
            )
        own_tab = None
        if args.new_tab:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            own_tab = context.new_page()
            page = own_tab
            try:
                page.goto(CHATGPT_URL, wait_until="domcontentloaded")
                page.wait_for_selector(COMPOSER, timeout=60_000)
            except Exception:
                own_tab.close()
                sys.exit(
                    "Opened a tab but the ChatGPT composer never appeared. "
                    "Make sure this browser profile is logged in to "
                    "https://chatgpt.com (open it manually once)."
                )
            print("Opened an own ChatGPT tab")
        else:
            page = find_chatgpt_page(browser)
            if page is None:
                sys.exit("No ChatGPT tab found. Open https://chatgpt.com and log in first.")
            print(f"Attached to ChatGPT tab: {page.url}")

        try:
            if args.queue:
                run_queue(page, problems, args)
            else:
                for i, path in enumerate(problems, 1):
                    print(f"[{i}/{len(problems)}] {path.name}")
                    if args.skip_existing and is_solved(
                        SOLUTIONS_DIR / f"{path.stem}.py"
                    ):
                        print("  already solved, skipping")
                        continue
                    solve_one(page, path, args)
        finally:
            if own_tab is not None:
                try:
                    own_tab.close()  # don't leave orphan tabs in the browser
                except PlaywrightError:
                    pass  # browser already gone

    print("Done.")


if __name__ == "__main__":
    main()
