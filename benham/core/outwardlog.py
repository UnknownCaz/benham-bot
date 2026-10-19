"""
outwardlog.py - the full text of everything Benham sent off the machine.

Issue 11 (2026-10-19): what_i_did kept only the status and URL of a file_issue
call, so when two issues carried the owner's legal name nobody could check,
after the fact, what had been sent. This is that record. It is LOCAL - a jsonl
under the process's state dir on the machine the bot runs on - and is never
uploaded, posted or filed anywhere. It holds the words AFTER the privacy
screen (what actually went), and a refused send is recorded too, with the rule
that refused it, so "what did it try to send" has an answer as well.

Writers: core/wire.py (every Discord send and edit) and issues.file_issue
(every filing). Reader: selfrecord, so what_i_did can see a message that went
out by a route that never passed capabilities.run (issue 10).

Recording must never break sending: every failure here is swallowed.
"""

import contextvars
import json
import os
import threading
from datetime import datetime, timezone

from benham import paths

FILENAME = "outward.jsonl"
ROTATE_BYTES = 5_000_000      # one generation kept: outward.jsonl.1

_lock = threading.Lock()

# The capability whose handler is running, set by capabilities.run. A send made
# inside a capability already has its own "action <name>" line in benham.log,
# so selfrecord shows only the entries that carry no capability (via None).
# asyncio.to_thread copies the context, so a filing made in a worker thread
# still knows which capability made it.
current_action = contextvars.ContextVar("benham_current_action", default=None)


def path():
    """Computed per call so a test's redirected state dir is honoured."""
    return os.path.join(paths.process_state_dir(), FILENAME)


def record(route, target, text, *, verdict="sent", rule="", via=None, extra=None):
    """Append one entry. route: discord|issue|webhook. target: where it went.
    via: the capability that sent it; defaults to current_action."""
    if via is None:
        via = current_action.get()
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
             "route": route, "target": str(target)[:200], "verdict": verdict,
             "rule": rule, "via": via, "text": text}
    if extra:
        entry.update(extra)
    try:
        p = path()
        with _lock:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                if os.path.getsize(p) > ROTATE_BYTES:
                    os.replace(p, p + ".1")
            except OSError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - the record must never stop the send
        pass


def entries():
    """Every recorded entry, oldest first (the .1 generation, then the live file)."""
    out = []
    p = path()
    for name in (p + ".1", p):
        try:
            with open(name, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out
