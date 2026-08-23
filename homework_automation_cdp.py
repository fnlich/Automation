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


def ask(page, prompt: str, timeout: float, poll: float) -> str | None:
    """Send one prompt and return the code from the new assistant reply."""
    before = page.locator(ASSISTANT_MSG).count()

    composer = page.locator(COMPOSER)
    composer.click()
    # insert_text handles newlines safely (Enter alone would submit early)
    page.keyboard.insert_text(prompt)
    page.locator(SEND_BUTTON).click()

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        # still streaming while the Stop button is visible
        if page.locator(STOP_BUTTON).count() > 0:
            continue
        messages = page.locator(ASSISTANT_MSG)
        if messages.count() <= before:
            continue
        reply = messages.nth(messages.count() - 1)
        code_blocks = reply.locator("pre code")
        if code_blocks.count() > 0:
            return code_blocks.nth(code_blocks.count() - 1).inner_text()
        # no code block — fall back to the whole reply text
        text = reply.inner_text().strip()
        if text:
            return text
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", help="Only files whose name contains this string")
    parser.add_argument("--port", type=int, default=9222, help="CDP port (default: 9222)")
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
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{args.port}")
        except Exception as e:
            sys.exit(
                f"Could not attach to the browser on port {args.port}: {e}\n"
                "Start Chrome with: chrome --remote-debugging-port=9222"
            )
        page = find_chatgpt_page(browser)
        if page is None:
            sys.exit("No ChatGPT tab found. Open https://chatgpt.com and log in first.")
        print(f"Attached to ChatGPT tab: {page.url}")

        for i, path in enumerate(problems, 1):
            name = path.stem
            print(f"[{i}/{len(problems)}] {path.name}")

            prompt = PROMPT_TEMPLATE.format(problem=path.read_text(encoding="utf-8"))
            solution = ask(page, prompt, args.timeout, args.poll)
            if solution is None:
                print(f"  WARNING: no answer within {args.timeout:.0f}s, skipping")
                continue

            out = SOLUTIONS_DIR / f"{name}.py"
            out.write_text(solution.rstrip("\n") + "\n", encoding="utf-8")
            print(f"  saved {out}")

    print("Done.")


if __name__ == "__main__":
    main()
