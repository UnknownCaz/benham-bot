"""status.py — quick, READ-ONLY health check for benham-bot.

Answers "is Benham up and what is it doing" without touching Discord:
  - is a benham.bot process running (pid)?
  - which guilds/channels does it see (from channels.json, written each boot)?
  - last login / command-sync lines from the newest log file

Prints a short report and exits. Never prints tokens. Run:  python benham.py status
"""

import os
import re
import sys
import json
import glob
import subprocess
from datetime import datetime, timezone

from benham import paths


def bot_pid():
    """Best-effort: pid of a running `python -m benham.bot`, or None. Uses PowerShell on Windows."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match '-m benham\\.bot' } | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=25,
        )
        s = out.stdout.strip()
        return int(s) if s.isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def newest_log_tail(patterns=("restart_run.log", "bot.log", "supervise.log"), keep=("Logged in as", "Synced")):
    logs = [os.path.join(paths.LOG_DIR, p) for p in patterns if os.path.exists(os.path.join(paths.LOG_DIR, p))]
    # Rotated-out captures live in ROOT/logs; live ones in LOG_DIR. A set, because
    # after the Stage 5 move those are the same directory.
    for d in {paths.LOG_DIR, os.path.join(paths.ROOT, "logs")}:
        logs += [p for p in glob.glob(os.path.join(d, "*.log")) if p not in logs]
    logs += [p for p in glob.glob(os.path.join(paths.LOG_DIR, "boot*.out")) if p not in logs]
    if not logs:
        return None, []
    newest = max(logs, key=lambda p: os.path.getmtime(p))
    lines = []
    try:
        with open(newest, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if any(k in line for k in keep):
                    lines.append(line.rstrip())
    except Exception:  # noqa: BLE001
        pass
    return os.path.basename(newest), lines[-6:]


def main():
    print("=== benham-bot status ===")

    pid = bot_pid()
    print(f"process:      {'RUNNING (pid ' + str(pid) + ')' if pid else 'NOT running'}")

    ch = load_json(os.path.join(paths.STATE_DIR, "channels.json"))
    ch_mt = mtime(os.path.join(paths.STATE_DIR, "channels.json"))
    if ch:
        print(f"guilds:       {len(ch)} (channels.json written {ch_mt:%Y-%m-%d %H:%M}Z)")
        for g in ch:
            tc = len(g.get("text_channels", []))
            vc = len(g.get("voice_channels", []))
            print(f"  - {g.get('guild')} ({g.get('guild_id')}): {tc} text, {vc} voice")
    else:
        print("guilds:       channels.json not found (bot hasn't booted here yet)")

    logname, tail = newest_log_tail()
    if logname:
        print(f"last log ({logname}):")
        for line in tail:
            print(f"  {line}")
    else:
        print("last log:     none found")

    return 0 if pid else 1


if __name__ == "__main__":
    raise SystemExit(main())
