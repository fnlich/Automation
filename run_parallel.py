#!/usr/bin/env python3
"""Run several homework_automation_cdp.py workers simultaneously in the background.

Each worker is a detached process that keeps running after this launcher
exits (and after you close the terminal). The problem list is split across
workers with --shard, so no two workers ever touch the same problem, and
every worker opens its OWN ChatGPT tab (--new-tab) so they don't fight
over one tab. Output goes to logs/worker-K.log; watch progress with e.g.
`Get-Content logs\\worker-1.log -Wait` (PowerShell) or `tail -f logs/worker-1.log`.

Two layouts (mix as you like):

  One browser, several tabs (one login does all the work):
      python run_parallel.py --workers 3
  Several logged-in browsers, each on its own debug port
  (spread across accounts to avoid per-account rate limits):
      python run_parallel.py --ports 9222 9223 9224

Every browser must have been started with --remote-debugging-port (and its
own --user-data-dir) and be logged in to ChatGPT, as described in README.md.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
WORKER = HERE / "homework_automation_cdp.py"
LOGS = HERE / "logs"
PIDFILE = LOGS / "workers.json"


def pid_alive(pid: int) -> bool:
    """Cross-platform liveness check. Never signals/kills the process.

    (os.kill(pid, 0) is NOT safe on Windows — there it terminates the
    process — so Windows goes through OpenProcess instead.)
    """
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def previous_run_pids() -> list[int]:
    try:
        return [p for p in json.loads(PIDFILE.read_text()) if pid_alive(p)]
    except (OSError, ValueError):
        return []


def spawn_detached(cmd, log_path: Path) -> "subprocess.Popen":
    """Start cmd fully detached from this process."""
    env = dict(os.environ, PYTHONUTF8="1")  # UTF-8 logs even on Windows (ACP)
    log = open(log_path, "w", encoding="utf-8")
    if sys.platform == "win32":
        flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags, cwd=HERE, env=env,
        )
    else:
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True, cwd=HERE, env=env,
        )
    log.close()  # the child holds its own handle
    return proc


def log_tail(log_path: Path, lines: int = 5) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join("    " + l for l in content.splitlines()[-lines:])
    except OSError:
        return "    (no log)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--workers", type=int,
        help="Number of workers as tabs in ONE browser, with --port "
        "(default: 2 when neither --workers nor --ports is given)",
    )
    group.add_argument(
        "--ports", type=int, nargs="+",
        help="One worker per browser: list of CDP debug ports, e.g. 9222 9223",
    )
    parser.add_argument("--port", type=int, default=9222,
                        help="CDP port for --workers mode (default: 9222)")
    parser.add_argument("--host", default="127.0.0.1", help="CDP host")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="Per-answer timeout passed to workers")
    args = parser.parse_args()

    if args.workers is not None and args.workers < 1:
        parser.error(f"--workers must be >= 1, got {args.workers}")

    if args.ports:
        endpoints = [(p, i + 1) for i, p in enumerate(args.ports)]
    else:
        n = args.workers if args.workers is not None else 2
        endpoints = [(args.port, i + 1) for i in range(n)]
    total = len(endpoints)

    LOGS.mkdir(exist_ok=True)
    alive = previous_run_pids()
    if alive:
        sys.exit(
            f"Workers from a previous run are still alive (pids {alive}). "
            "Two runs would overlap shards and race on the same solution "
            "files. Wait for them or stop them first "
            "(kill <pid> / Stop-Process -Id <pid>)."
        )

    procs = []
    for port, k in endpoints:
        cmd = [
            sys.executable, "-u", str(WORKER),
            "--host", args.host, "--port", str(port),
            "--shard", f"{k}/{total}",
            "--new-tab", "--skip-existing",
            "--timeout", str(args.timeout),
        ]
        log_path = LOGS / f"worker-{k}.log"
        proc = spawn_detached(cmd, log_path)
        procs.append((k, port, proc, log_path))
        print(f"worker {k}/{total}: port {port}, pid {proc.pid}, log {log_path}")
        time.sleep(2)  # stagger startup so tabs don't open at the same instant

    # A worker that dies at startup (bad port, browser not logged in) would
    # silently drop its whole shard — catch that instead of claiming success.
    time.sleep(3)
    dead = [(k, port, p, lp) for k, port, p, lp in procs if p.poll() is not None]
    running = [(k, port, p, lp) for k, port, p, lp in procs if p.poll() is None]
    PIDFILE.write_text(json.dumps([p.pid for _, _, p, _ in running]))

    for k, port, p, lp in dead:
        print(f"\nWORKER {k} DIED at startup (port {port}, exit {p.returncode}); "
              f"its problems will NOT be solved. Last log lines ({lp}):")
        print(log_tail(lp))

    if not running:
        sys.exit("\nAll workers died at startup — nothing is running.")
    print(
        f"\n{len(running)}/{total} worker(s) running in the background. "
        "They keep running after this terminal closes.\n"
        "Watch:  tail -f logs/worker-1.log   "
        "(PowerShell: Get-Content logs\\worker-1.log -Wait)\n"
        "Stop:   kill <pid>                  "
        "(PowerShell: Stop-Process -Id <pid>)"
    )
    if dead:
        print(f"Re-run this launcher after fixing the {len(dead)} dead "
              "worker(s) above — --skip-existing resumes where they left off.")
        sys.exit(1)


if __name__ == "__main__":
    main()
