# Homework ChatGPT Automation

Automatically feeds the homework problems in `problems/` to ChatGPT in the browser.

## Workflow

For each problem file, one by one, the script:

1. Reads the problem and copies it to the clipboard (wrapped in a solve-this
   prompt that asks for code between unique `# ===BEGIN/END hwNN===` markers).
2. Activates the browser window whose title contains **ChatGPT**.
3. Pastes the clipboard into the ChatGPT input (ChatGPT's web UI routes any
   paste on the page into the message composer) and presses Enter.
4. Waits for the answer by polling the page (Ctrl+A, Ctrl+C) until the closing
   marker appears and stops changing, then saves the extracted code to
   `solutions/hwNN.py` before moving on to the next problem.

## Setup

```bash
pip install -r requirements.txt
```

- **Windows** works out of the box (`pygetwindow` is installed automatically
  there). If the ChatGPT window won't come to the front, click it once manually
  the first time — Windows restricts focus-stealing by background processes.
- **Linux** additionally needs `xdotool` (`sudo apt install xdotool`) and an X11 session.
- **macOS** will prompt for Accessibility permissions the first time
  (System Settings → Privacy & Security → Accessibility).
- Open ChatGPT in your browser and log in before running.

## Usage

```bash
# Solve all problems one by one, saving answers to solutions/hwNN.py
python homework_automation.py

# Only run a single problem
python homework_automation.py --problem hw07

# Give slow problems up to 5 minutes each
python homework_automation.py --timeout 300

# Just paste (don't submit or capture); you press Enter yourself
python homework_automation.py --no-send
```

Options:

| Flag        | Default | Meaning                                            |
|-------------|---------|----------------------------------------------------|
| `--problem` | (all)   | Only files whose name contains this string         |
| `--no-send` | off     | Paste only; don't submit or capture the answer     |
| `--timeout` | 180     | Max seconds to wait for each answer                |
| `--poll`    | 5       | Seconds between checks for the finished answer     |
| `--delay`   | 1.5     | Seconds between window activation and paste        |

Answers land in `solutions/`, named after the problem (`hw07.md` → `hw07.py`).

Keep your hands off the mouse/keyboard while it runs — it drives your real
desktop. Move the mouse to a screen corner to trigger PyAutoGUI's failsafe
abort if something goes wrong.
