"""
watch_pc.py - watch, live, what Benham is doing on the PC.

    python watch_pc.py            # follow the active session
    python watch_pc.py --last     # replay the most recent session and exit
    python watch_pc.py --list     # list recent sessions

A pc_task runs a real Claude Code session, and until it finishes the only thing
bot.log shows is a permission prompt (if one is needed) and a one-line summary
after the fact. That is a poor view of something running commands on the machine:
by the time the summary appears, whatever happened has happened.

Claude Code already writes a full JSONL transcript per session under
~/.claude/projects/<encoded-cwd>/, appended as it goes. Nothing had to be added to
the bot to make this observable - the record existed, it just had no reader. So
this follows the newest transcript and prints it in a form meant for a human
watching in real time.

Read-only. It opens transcript files and nothing else; it cannot affect a running
session, so watching is always safe even mid-task.
"""

import argparse
import glob
import json
import os
import sys
import time

# Claude Code encodes the working directory into the project folder name by
# replacing separators and colons with dashes.
WORKDIR = os.path.join(os.path.expanduser("~"), "Claude", "Benhams-inbox")
PROJECT_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "projects",
    WORKDIR.replace(":", "-").replace("\\", "-").replace("/", "-"),
)

# Tools that only look. Shown dimmer, because in a live feed the interesting
# events are the ones that touch something.
READ_ONLY = {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "NotebookRead",
             "TodoWrite", "BashOutput"}


def sessions():
    """Transcript files, newest last."""
    return sorted(glob.glob(os.path.join(PROJECT_DIR, "*.jsonl")),
                  key=os.path.getmtime)


def _fmt_tool(block):
    name = block.get("name", "?")
    inp = block.get("input") or {}
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
              or inp.get("notebook_path") or inp.get("description")
              or inp.get("task") or "")
    detail = " ".join(str(detail).split())          # collapse newlines for one line
    mark = "  " if name in READ_ONLY else ">>"      # >> = it is about to change something
    return f"{mark} {name:<11} {detail[:120]}"


def render(record):
    """One transcript record -> zero or more display lines."""
    out = []
    msg = record.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        if record.get("type") == "user":
            out.append(f"\n=== TASK: {' '.join(content.split())[:200]}")
        return out
    if not isinstance(content, list):
        return out
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use":
            out.append(_fmt_tool(b))
        elif b.get("type") == "text":
            text = (b.get("text") or "").strip()
            if text:
                out.append(f"   {' '.join(text.split())[:160]}")
        elif b.get("type") == "tool_result" and b.get("is_error"):
            body = b.get("content")
            body = body if isinstance(body, str) else json.dumps(body, default=str)
            out.append(f"!! FAILED     {' '.join(str(body).split())[:120]}")
    return out


def emit(path, offset):
    """Print whatever is new in `path` since `offset`; return the new offset."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return offset
    if size <= offset:
        return offset            # nothing new (or the file was replaced)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        chunk = f.read()
        offset = f.tell()
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue             # a partially-written final line; it will arrive whole next poll
        for out in render(record):
            print(out, flush=True)
    return offset


def follow():
    print(f"watching {PROJECT_DIR}")
    print("(waiting for a PC task - ask Benham to do something on the machine)\n")
    current, offset = None, 0
    while True:
        files = sessions()
        newest = files[-1] if files else None
        if newest and newest != current:
            # A new session file means a new pc_task started.
            if current is not None:
                print(f"\n--- new session: {os.path.basename(newest)} ---")
            current, offset = newest, 0
        if current:
            offset = emit(current, offset)
        time.sleep(0.5)


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--last", action="store_true", help="replay the most recent session and exit")
    p.add_argument("--list", action="store_true", help="list recent sessions")
    args = p.parse_args(argv)

    if not os.path.isdir(PROJECT_DIR):
        print(f"No PC sessions yet - {PROJECT_DIR} does not exist.", file=sys.stderr)
        return 1

    files = sessions()
    if args.list:
        for f in files[-15:]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f)))
            print(f"  {ts}  {os.path.getsize(f):>8}b  {os.path.basename(f)}")
        return 0

    if args.last:
        if not files:
            print("No sessions recorded.", file=sys.stderr)
            return 1
        print(f"=== {os.path.basename(files[-1])} ===")
        emit(files[-1], 0)
        return 0

    try:
        follow()
    except KeyboardInterrupt:
        print("\nstopped watching")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
