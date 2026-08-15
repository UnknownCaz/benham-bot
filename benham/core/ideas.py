"""ideas.py - the guest ideas inbox: `idea..` filings, quarantined on disk.

Born from the first discord-outreach run: Doom's design input only survived
because a Claude session happened to be watching his DM at that moment. Now a
guest can file an idea any time with the `idea..` prefix (the guest-side
sibling of the owner's `pc..`), and it lands here instead of evaporating into
the brain's small talk.

THE QUARANTINE PROPERTY. Filed text is attacker-writable and stays in this
file until a human or a live Claude session reads it out deliberately. The bot
never copies it into the Corkboard vault, a prompt, or anywhere a future
Claude treats content as trusted - a guest who can plant instructions on a
board Claude acts on has crossed the exact boundary policy.py exists to hold.
Tyler gets a DM naming the guest and quoting the idea (Discord renders it as
chat, not instructions), and Claude sweeps the inbox onto project boards AS
REPORTED SPEECH during sessions. Keep it that way.

Storage: state/guest_ideas.jsonl, one JSON object per line. A sweep cursor
(state/guest_ideas.cursor) remembers how many lines the last CLI sweep saw, so
`benham.py ideas` can show only what's new.
"""

import json
import os
import threading
import time
from datetime import date, datetime, timezone

from benham import paths

IDEAS_FILE = os.path.join(paths.STATE_DIR, "guest_ideas.jsonl")
CURSOR_FILE = os.path.join(paths.STATE_DIR, "guest_ideas.cursor")

PREFIX = "idea.."
MAX_LEN = 1000          # one idea, not an essay - the DM ping must stay readable
DAILY_CAP = 10          # per guest; the inbox is a gift, not a firehose

_lock = threading.Lock()


def extract(content):
    """The idea text if this message is an `idea..` filing, else None.

    Prefix match is case-insensitive but the prefix must open the message -
    'that idea..' mid-sentence is conversation, not a filing.
    """
    if not content:
        return None
    stripped = content.strip()
    if not stripped.lower().startswith(PREFIX):
        return None
    return stripped[len(PREFIX):].strip()


def _entries():
    if not os.path.exists(IDEAS_FILE):
        return []
    out = []
    with open(IDEAS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue    # a torn line loses one idea, not the file
    return out


def filed_today(author_id):
    today = date.today().isoformat()
    return sum(1 for e in _entries()
               if e.get("author_id") == int(author_id)
               and str(e.get("day")) == today)


def file_idea(author_id, author_name, text):
    """Store one idea. Returns (ok, reply_text) - reply_text goes to the guest."""
    text = " ".join((text or "").split())
    if not text:
        return False, "idea.. what? give me the idea after the prefix"
    if len(text) > MAX_LEN:
        return False, (f"thats an essay, not an idea - keep it under {MAX_LEN} "
                       "characters and ill file it")
    with _lock:
        if filed_today(author_id) >= DAILY_CAP:
            return False, (f"youve hit the {DAILY_CAP}-ideas-a-day cap - "
                           "save the rest for tomorrow")
        entry = {"ts": datetime.now(timezone.utc).isoformat(),
                 "day": date.today().isoformat(),
                 "author_id": int(author_id), "author": str(author_name)[:80],
                 "text": text}
        with open(IDEAS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True, "filed for caz 💡 - itll reach him and the project boards"


def new_since_sweep():
    """Entries filed since the last sweep, and the total count."""
    entries = _entries()
    seen = 0
    if os.path.exists(CURSOR_FILE):
        try:
            with open(CURSOR_FILE, "r", encoding="utf-8") as f:
                seen = int(f.read().strip() or 0)
        except (ValueError, OSError):
            seen = 0
    return entries[seen:], len(entries)


def mark_swept(total):
    """Record that a sweep has seen the first `total` entries."""
    with open(CURSOR_FILE, "w", encoding="utf-8") as f:
        f.write(str(int(total)))
