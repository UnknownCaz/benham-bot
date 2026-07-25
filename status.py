"""status.py — quick, READ-ONLY health check for benham-bot.

Answers "is Benham up and what is it doing" without touching Discord:
  - is a bot.py process running (pid)?
  - which guilds/channels does it see (from channels.json, written each boot)?
  - AUTO_REPLY on/off + the guild allowlist (from environ.env + exaroton_watch.json)
  - last login / command-sync lines from the newest log file

Prints a short report and exits. Never prints tokens. Run:  python status.py
"""

import os
import re
import sys
import json
import glob
import subprocess
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def bot_pid():
    """Best-effort: pid of a running `python bot.py`, or None. Uses PowerShell on Windows."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'bot\\.py' } | "
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


def auto_reply_flag():
    """Read only the BENHAM_AUTO_REPLY line from environ.env — never any secret."""
    try:
        with open(os.path.join(BASE_DIR, "environ.env"), "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("BENHAM_AUTO_REPLY"):
                    return line.split("=", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return "0 (default)"


def newest_log_tail(patterns=("restart_run.log", "bot.log", "supervise.log"), keep=("Logged in as", "AUTO_REPLY", "Synced")):
    logs = [os.path.join(BASE_DIR, p) for p in patterns if os.path.exists(os.path.join(BASE_DIR, p))]
    logs += [p for p in glob.glob(os.path.join(BASE_DIR, "*.log")) if p not in logs]
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

    watch = load_json(os.path.join(BASE_DIR, "exaroton_watch.json")) or {}
    allow = watch.get("auto_reply_guilds", [watch.get("guild_id")] if watch.get("guild_id") else [])
    print(f"AUTO_REPLY:   env BENHAM_AUTO_REPLY={auto_reply_flag()} | allowed guilds={allow}")

    ch = load_json(os.path.join(BASE_DIR, "channels.json"))
    ch_mt = mtime(os.path.join(BASE_DIR, "channels.json"))
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
