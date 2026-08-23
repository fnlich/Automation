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
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Missing dependency. Run: pip install playwright")

PROBLEMS_DIR = Path(__file__).parent / "problems"
SOLUTIONS_DIR = Path(__file__).parent / "solutions"

PROMPT_TEMPLATE = """Please solve the following homework problem in Python.
Reply with ONLY a single Python code block, no explanation outside it
(use comments inside the code if needed).

{problem}"""

COMPOSER = "#prompt-textarea"
SEND_BUTTON = '[data-testid="send-button"]'
STOP_BUTTON = '[data-testid="stop-button"]'
ASSISTANT_MSG = '[data-message-author-role="assistant"]'


def find_chatgpt_page(browser):
    for context in browser.contexts:
        for page in context.pages:
            if "chatgpt.com" in page.url or "chat.openai.com" in page.url:
                return page
    return None


def new_chat(page) -> None:
    """Open a fresh conversation so each problem starts with empty context.

    Keeps responses fast and the DOM small — in one long conversation
    ChatGPT re-renders/virtualizes the message list, which broke the old
    count-based answer detection from around the 4th problem on.
    """
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
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

    SOLUTIONS_DIR.mkdir(exist_ok=True)

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
        page = find_chatgpt_page(browser)
        if page is None:
            sys.exit("No ChatGPT tab found. Open https://chatgpt.com and log in first.")
        print(f"Attached to ChatGPT tab: {page.url}")

        for i, path in enumerate(problems, 1):
            name = path.stem
            print(f"[{i}/{len(problems)}] {path.name}")

            if not args.same_chat:
                new_chat(page)
                print("  started a new chat")

            prompt = PROMPT_TEMPLATE.format(problem=path.read_text(encoding="utf-8"))
            solution = ask(page, prompt, args.timeout, args.poll)
            if solution is None:
                print(f"  WARNING: no answer within {args.timeout:.0f}s, skipping")
                continue

            out = SOLUTIONS_DIR / f"{name}.py"
            out.write_text(solution.rstrip("\n") + "\n", encoding="utf-8")
            try:
                ast.parse(solution)
                print(f"  saved {out} (valid Python)")
            except SyntaxError as e:
                print(f"  saved {out} — WARNING: not valid Python ({e.msg}, "
                      f"line {e.lineno}); check it manually")

    print("Done.")


if __name__ == "__main__":
    main()
