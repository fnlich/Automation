#!/usr/bin/env python3
"""Automate sending homework problems to ChatGPT in the browser.

Workflow per problem (one by one):
  1. Read the problem file and copy its text to the clipboard.
  2. Activate the browser window/tab whose title contains "ChatGPT".
  3. Paste the clipboard into the ChatGPT input and press Enter.
  4. Wait for the answer, then save it to solutions/<problem-name>.py.

The prompt asks ChatGPT to wrap its code between unique marker lines; the
script polls by copying the whole page (Ctrl+A, Ctrl+C) until the closing
marker appears and is stable, then extracts the code between the markers.

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
SOLUTIONS_DIR = Path(__file__).parent / "solutions"
WINDOW_TITLE = "ChatGPT"

PROMPT_TEMPLATE = """Please solve the following homework problem in Python.
Reply with ONLY Python code, no explanation outside the code (use comments
inside the code if needed). Put the entire solution between these two exact
marker lines so it can be extracted automatically:

# ===BEGIN {name}===
# ===END {name}===

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


def copy_page_text() -> str:
    """Copy the whole ChatGPT page (Ctrl+A, Ctrl+C) and return it.

    Clicks near the top-center of the window first so focus leaves the
    message composer — otherwise Ctrl+A would select the (empty) input
    instead of the conversation.
    """
    mod = "command" if platform.system() == "Darwin" else "ctrl"
    w, h = pyautogui.size()
    pyautogui.click(w // 2, h // 3)
    time.sleep(0.2)
    pyperclip.copy("")  # clear so we can tell whether the copy worked
    pyautogui.hotkey(mod, "a")
    time.sleep(0.2)
    pyautogui.hotkey(mod, "c")
    time.sleep(0.3)
    pyautogui.press("escape")  # drop the selection
    return pyperclip.paste()


def extract_solution(page: str, name: str) -> str | None:
    """Pull the code between the LAST pair of BEGIN/END markers for `name`.

    The prompt itself also contains the marker lines, so take the last
    occurrence — that is ChatGPT's answer, not our own question.
    """
    begin, end = f"# ===BEGIN {name}===", f"# ===END {name}==="
    start = page.rfind(begin)
    if start == -1:
        return None
    stop = page.find(end, start + len(begin))
    if stop == -1:
        return None
    body = page[start + len(begin) : stop].strip("\n")
    # Ignore the echo of the prompt: a real answer has code between markers.
    return body if body.strip() else None


def wait_for_solution(name: str, timeout: float, poll: float) -> str | None:
    """Poll the page until ChatGPT's answer (with the END marker) appears."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        time.sleep(poll)
        solution = extract_solution(copy_page_text(), name)
        if solution is not None:
            if solution == last:  # unchanged across two polls => finished
                return solution
            last = solution
    return last


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
        "--no-send",
        action="store_true",
        help="Only paste; don't submit or capture the answer",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Max seconds to wait for ChatGPT's answer per problem (default: 180)",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=5.0,
        help="Seconds between checks for the finished answer (default: 5)",
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

    SOLUTIONS_DIR.mkdir(exist_ok=True)
    print(f"Found {len(problems)} problem(s). Starting in 3 seconds...")
    time.sleep(3)

    for i, path in enumerate(problems, 1):
        name = path.stem  # hw01.md -> hw01
        print(f"[{i}/{len(problems)}] {path.name}")

        text = PROMPT_TEMPLATE.format(
            name=name, problem=path.read_text(encoding="utf-8")
        )
        copy_to_clipboard(text)
        print("  copied to clipboard")

        if not activate_chatgpt_window():
            sys.exit(
                f"Could not find a window titled '{WINDOW_TITLE}'. "
                "Make sure the ChatGPT tab is open and its title is visible."
            )
        print("  activated ChatGPT window")

        time.sleep(args.delay)
        paste_and_send(send=not args.no_send)
        print("  pasted" + ("" if args.no_send else " and sent"))

        if args.no_send:
            continue

        print("  waiting for ChatGPT's answer...")
        solution = wait_for_solution(name, args.timeout, args.poll)
        if solution is None:
            print(f"  WARNING: no answer captured within {args.timeout:.0f}s, skipping")
            continue

        out = SOLUTIONS_DIR / f"{name}.py"
        out.write_text(solution + "\n", encoding="utf-8")
        print(f"  saved {out}")

    print("Done.")


if __name__ == "__main__":
    main()
