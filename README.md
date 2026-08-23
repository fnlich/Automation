# Homework ChatGPT Automation

Automatically feeds the homework problems in `problems/` to ChatGPT in the browser.

## Workflow

For each problem file the script:

1. Reads the problem and copies it to the clipboard (wrapped in a solve-this prompt).
2. Activates the browser window whose title contains **ChatGPT**.
3. Pastes the clipboard into the ChatGPT input (ChatGPT's web UI routes any
   paste on the page into the message composer).
4. Optionally presses Enter to submit, then waits for the answer before the
   next problem.

## Setup

```bash
pip install -r requirements.txt
```

- **Linux** additionally needs `xdotool` (`sudo apt install xdotool`) and an X11 session.
- **macOS** will prompt for Accessibility permissions the first time
  (System Settings → Privacy & Security → Accessibility).
- Open ChatGPT in your browser and log in before running.

## Usage

```bash
# Paste all 20 problems one by one (does NOT submit; you press Enter yourself)
python homework_automation.py

# Paste AND submit each problem, waiting 90s between problems for the answer
python homework_automation.py --send --wait 90

# Only run a single problem
python homework_automation.py --problem hw07 --send
```

Options:

| Flag        | Default | Meaning                                              |
|-------------|---------|------------------------------------------------------|
| `--problem` | (all)   | Only files whose name contains this string           |
| `--send`    | off     | Press Enter after pasting to submit                  |
| `--wait`    | 60      | Seconds to wait between problems when using `--send` |
| `--delay`   | 1.5     | Seconds between window activation and paste          |

Keep your hands off the mouse/keyboard while it runs — it drives your real
desktop. Move the mouse to a screen corner to trigger PyAutoGUI's failsafe
abort if something goes wrong.
