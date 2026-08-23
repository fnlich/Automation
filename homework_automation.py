#!/usr/bin/env python3
"""Automate sending homework problems to ChatGPT in the browser.

Workflow per problem:
  1. Read the problem file and copy its text to the clipboard.
  2. Activate the browser window/tab whose title contains "ChatGPT".
  3. Paste the clipboard into the ChatGPT input (and optionally press Enter).

Assumes the ChatGPT browser tab is already open. In the ChatGPT web UI,
pasting anywhere on the page goes into the message composer, so a plain
Ctrl+V / Cmd+V after focusing the window is enough.
"""

import argparse
import platform
import subprocess
import sys
import time
from pathlib import Path

try:
    import pyperclip
    import pyautogui
except ImportError:
    sys.exit("Missing dependencies. Run: pip install -r requirements.txt")

PROBLEMS_DIR = Path(__file__).parent / "problems"
WINDOW_TITLE = "ChatGPT"

PROMPT_TEMPLATE = """Please solve the following homework problem in Python. \
Provide a complete, working solution with a brief explanation.

{problem}"""


def copy_to_clipboard(text: str) -> None:
    pyperclip.copy(text)


def activate_chatgpt_window(title: str = WINDOW_TITLE) -> bool:
    """Bring the browser window containing ChatGPT to the foreground."""
    system = platform.system()

    if system == "Windows":
        import pygetwindow as gw

        windows = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
        if not windows:
            return False
        win = windows[0]
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except gw.PyGetWindowException:
            # Windows often refuses SetForegroundWindow for background
            # processes; a quick minimize/restore cycle reliably raises it.
            try:
                win.minimize()
                win.restore()
            except gw.PyGetWindowException:
                return False
        # Give the window manager a moment, then verify focus took.
        time.sleep(0.3)
        active = gw.getActiveWindow()
        return active is not None and title.lower() in active.title.lower()

    if system == "Darwin":
        script = f'''
        tell application "System Events"
            repeat with proc in (every process whose background only is false)
                repeat with win in (every window of proc)
                    if name of win contains "{title}" then
                        set frontmost of proc to true
                        perform action "AXRaise" of win
                        return "found"
                    end if
                end repeat
            end repeat
        end tell
        return "not found"
        '''
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
        return "found" in result.stdout

    # Linux: use xdotool
    result = subprocess.run(
        ["xdotool", "search", "--name", title, "windowactivate", "--sync"],
        capture_output=True,
    )
    return result.returncode == 0


def paste_and_send(send: bool) -> None:
    paste_key = "command" if platform.system() == "Darwin" else "ctrl"
    pyautogui.hotkey(paste_key, "v")
    time.sleep(0.5)
    if send:
        pyautogui.press("enter")


def load_problems(only: str | None) -> list[Path]:
    files = sorted(PROBLEMS_DIR.glob("hw*.md"))
    if only:
        files = [f for f in files if only in f.name]
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--problem",
        help="Only run problems whose filename contains this string (e.g. hw01)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Press Enter after pasting to submit the prompt",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=60.0,
        help="Seconds to wait between problems for ChatGPT to answer (default: 60)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Seconds to wait after activating the window before pasting (default: 1.5)",
    )
    args = parser.parse_args()

    problems = load_problems(args.problem)
    if not problems:
        sys.exit(f"No problem files found in {PROBLEMS_DIR}")

    print(f"Found {len(problems)} problem(s). Starting in 3 seconds...")
    time.sleep(3)

    for i, path in enumerate(problems, 1):
        print(f"[{i}/{len(problems)}] {path.name}")

        text = PROMPT_TEMPLATE.format(problem=path.read_text(encoding="utf-8"))
        copy_to_clipboard(text)
        print("  copied to clipboard")

        if not activate_chatgpt_window():
            sys.exit(
                f"Could not find a window titled '{WINDOW_TITLE}'. "
                "Make sure the ChatGPT tab is open and its title is visible."
            )
        print("  activated ChatGPT window")

        time.sleep(args.delay)
        paste_and_send(args.send)
        print("  pasted" + (" and sent" if args.send else ""))

        if i < len(problems):
            wait = args.wait if args.send else args.delay
            print(f"  waiting {wait:.0f}s before next problem...")
            time.sleep(wait)

    print("Done.")


if __name__ == "__main__":
    main()
