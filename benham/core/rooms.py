"""
rooms.py - a place work lives between sessions. INTENT items 20 and 22.

A room is a jsonl file of messages plus an index entry. Sessions and Tyler post
into it; whoever looks next reads it. Nothing is alive between messages, on
purpose: the whole design exists so that "is that session still up?" is never a
question anyone has to get right. The file survives with nothing running, which
is the same reason conversations.json is a file.

V1 IS PULL-ONLY (decision #27). Nothing in this module - or anywhere else -
wakes a session because a message arrived. A worker resumes only when a human
explicitly spawns or continues into its room. The wake machinery (budgets,
in-flight locks, stuck detection) is Phase B's spec, deliberately unbuilt
rather than half-built; c13 measured why (a push can sit undelivered against a
busy session while the sender sees success, and waking an idle one burns the
whole transcript in cold-cache input tokens).

WHO WRITES WHAT. Message files and the index are written by the BOT ONLY -
every mutation arrives as a capability through policy, so the single-writer
property and the audit trail are the same fact. Readers write exactly one
thing: their own cursor file, which no one else touches. That split is what
lets a CLI read run with no bot involved and still not race anything.

THE LISTING IS METADATA AND DOES NOT TAINT; CONTENT DOES. Item 22's refinement
of 20.7, with the reasoning recorded there: the laundering path runs through
content, and a listing that tainted would leave every session born tainted at
startup. So listing() returns names and counts ONLY - never purpose, never a
message - and names are charset-limited below so a name cannot carry much of
anything. read_room is the taint carrier.

Rooms archive, never delete (decision #17). An archived room leaves the
listing, and posting or spawning into one fails as loudly as into a ghost -
create_room is explicit and never implicit, because a typo silently creating
a room is the rot this repo keeps finding.

No rotation on room files, deliberately: jsonio.append_jsonl rotates at 5MB,
which is the right call for an inbox and exactly wrong for the file that IS a
room's memory. A room that grows heavy gets archived by a human, visibly.
"""

import os
import re
from datetime import datetime, timezone

from benham.core import jsonio
from benham import paths

INDEX_FILE = os.path.join(paths.STATE_DIR, "rooms.json")
ROOMS_DIR = os.path.join(paths.STATE_DIR, "rooms")
CURSOR_DIR = os.path.join(ROOMS_DIR, "cursors")

# Kebab, short, starts alphanumeric. A room name appears in listings that are
# read without tainting, so it must be a poor carrier for instruction-shaped
# text - length and charset do that with arithmetic rather than judgement.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# The standing default room. pc.. tasks land here (item 22b), so it is created
# at boot by ensure() - the one deliberate, logged exception to "never
# implicit", made in code at a known moment rather than by a typo at runtime.
SCRATCH = "scratch"

# After this many runs under one session id, a continue starts FRESH instead of
# resuming (item 20.6's bound). Sparse resumes are cold-cache: a worker
# re-bills its whole transcript per resume, so the transcript must stay small.
# The room is the memory; the process never has to be. Config, not code -
# rooms.handoff_after_runs in control.json overrides.
HANDOFF_AFTER_RUNS = 8


def _now():
    return datetime.now(timezone.utc).isoformat()


def _index():
    d = jsonio.read_json(INDEX_FILE, default={})
    return d if isinstance(d, dict) and isinstance(d.get("rooms"), dict) \
        else {"rooms": {}}


def _save_index(d):
    jsonio.write_json(INDEX_FILE, d)


def _room_file(name):
    return os.path.join(ROOMS_DIR, f"{name}.jsonl")


def valid_name(name):
    return bool(NAME_RE.match(name or ""))


def get(name):
    """The index entry for one room, or None."""
    return _index()["rooms"].get(name)


def exists(name):
    return get(name) is not None


def create(name, purpose, author):
    """Create a room. Explicit, never implicit - a bad or taken name FAILS."""
    if not valid_name(name):
        raise ValueError(
            f"{name!r} is not a valid room name - lowercase letters, digits and "
            "hyphens, up to 40 characters, starting with a letter or digit.")
    idx = _index()
    if name in idx["rooms"]:
        state = "archived" if idx["rooms"][name].get("archived") else "already exists"
        raise ValueError(f"room {name!r} {state} - room names are never reused "
                         "implicitly.")
    idx["rooms"][name] = {
        "purpose": str(purpose or "")[:300],
        "created_at": _now(),
        "created_by": str(author),
        "archived": False,
        "seq": 0,
        # The worker record: which Claude Code session id carries this room's
        # thread, and how many runs it has done under that id. run_task hands
        # the id back (it is the `resume` handle); spawn_in_room records it.
        "worker": None,
    }
    _save_index(idx)
    os.makedirs(ROOMS_DIR, exist_ok=True)
    return idx["rooms"][name]


def ensure(name, purpose, author):
    """Create if missing, return the entry either way. For SCRATCH at boot only -
    every other creation goes through create() and fails loud on collision."""
    entry = get(name)
    if entry is not None:
        return entry
    return create(name, purpose, author)


def _require_open(name):
    entry = get(name)
    if entry is None:
        known = ", ".join(sorted(_index()["rooms"])) or "none yet"
        raise ValueError(f"no room named {name!r}. Rooms are created explicitly "
                         f"with create_room, never by typo. Known: {known}.")
    if entry.get("archived"):
        raise ValueError(f"room {name!r} is archived - it accepts no posts and "
                         "no spawns. Unarchiving is a deliberate human step.")
    return entry


def post(name, author, text):
    """Append one message. BOT-SIDE ONLY - callers arrive through a capability.

    author + ts on every line: provenance per message is the same instinct as
    bound_by on a conversation. What a line MEANS is the reader's problem;
    who wrote it and when must never be.
    """
    entry = _require_open(name)
    text = str(text or "").strip()
    if not text:
        raise ValueError("an empty message is not worth a line in the record.")
    idx = _index()
    idx["rooms"][name]["seq"] = int(idx["rooms"][name].get("seq") or 0) + 1
    seq = idx["rooms"][name]["seq"]
    msg = {"seq": seq, "ts": _now(), "author": str(author), "text": text[:8000]}
    os.makedirs(ROOMS_DIR, exist_ok=True)
    # Plain append, no rotation - see the module docstring. write_json-style
    # atomicity is not needed for an append; a torn final line is skipped by
    # iter_jsonl and the seq in the index names the last GOOD message.
    import json
    with open(_room_file(name), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _save_index(idx)
    return msg


def messages(name, since_seq=0, limit=50):
    """Messages after since_seq, oldest first, capped to the newest `limit`."""
    if get(name) is None:
        raise ValueError(f"no room named {name!r}")
    out = [m for m in jsonio.iter_jsonl(_room_file(name))
           if isinstance(m, dict) and int(m.get("seq") or 0) > int(since_seq)]
    out.sort(key=lambda m: int(m.get("seq") or 0))
    return out[-int(limit):] if limit else out


# ---------------------------------------------------------------------------
# Cursors - each reader owns exactly one file, so reads never race the bot.
# ---------------------------------------------------------------------------

_READER_RE = re.compile(r"[^a-zA-Z0-9._-]")


def _cursor_file(reader):
    safe = _READER_RE.sub("_", str(reader))[:60] or "unknown"
    return os.path.join(CURSOR_DIR, f"{safe}.json")


def cursor(reader):
    """{room: last_read_seq} for one reader."""
    return jsonio.read_json(_cursor_file(reader), default={})


def mark_read(reader, name, seq):
    """Advance one reader's cursor. Monotonic - a re-read never rewinds it."""
    os.makedirs(CURSOR_DIR, exist_ok=True)
    cur = cursor(reader)
    cur[name] = max(int(seq), int(cur.get(name) or 0))
    jsonio.write_json(_cursor_file(reader), cur)


def read_and_mark(reader, name, limit=50):
    """The messages this reader has not seen (or the recent tail), cursor moved.

    Returns (entry, msgs). Content, so the capability on top declares
    taints=True - this function does not know about taint and must not.
    """
    entry = _require_open(name)
    cur = int(cursor(reader).get(name) or 0)
    msgs = messages(name, since_seq=cur, limit=limit)
    if not msgs:
        # Nothing new: show the recent tail instead of an empty screen, so
        # "read the room" always answers with the state of the room.
        msgs = messages(name, since_seq=0, limit=min(int(limit), 10))
    if entry.get("seq"):
        mark_read(reader, name, entry["seq"])
    return entry, msgs


def listing(reader):
    """Names + unread counts, nothing else. Cheap by design (c12), and safe to
    put in front of a model without tainting (item 22) BECAUSE it carries no
    free text: no purpose, no messages, charset-limited names only."""
    idx = _index()
    cur = cursor(reader)
    out = []
    for name, entry in sorted(idx["rooms"].items()):
        if entry.get("archived"):
            continue
        unread = max(0, int(entry.get("seq") or 0) - int(cur.get(name) or 0))
        out.append({"name": name, "unread": unread,
                    "has_worker": bool(entry.get("worker"))})
    return out


def archive(name):
    """Take a room out of the listing. The file stays - decision #17."""
    idx = _index()
    if name not in idx["rooms"]:
        raise ValueError(f"no room named {name!r}")
    idx["rooms"][name]["archived"] = True
    idx["rooms"][name]["archived_at"] = _now()
    _save_index(idx)


# ---------------------------------------------------------------------------
# The worker record - which session id carries this room's thread.
# ---------------------------------------------------------------------------

def worker(name):
    entry = get(name)
    return (entry or {}).get("worker")


def handoff_after():
    from benham.core import identity
    cfg = identity.CONTROL.get("rooms", {}) or {}
    return int(cfg.get("handoff_after_runs", HANDOFF_AFTER_RUNS))


def resumable(name):
    """The session id a continue should resume, or None if a fresh session is
    the right call - no worker yet, or the worker has hit the handoff bound
    (the transcript is the cost, the room is the memory)."""
    w = worker(name)
    if not w or not w.get("session_id"):
        return None
    if int(w.get("runs") or 0) >= handoff_after():
        return None
    return w["session_id"]


def record_run(name, session_id, resumed):
    """After a spawn returns: remember the id run_task handed back.

    A resumed run increments the count toward handoff; a fresh one restarts it
    at 1 under the new id. A run that came back with no id (SDK hiccup) leaves
    the record alone rather than overwriting a good id with nothing.
    """
    if not session_id:
        return
    idx = _index()
    if name not in idx["rooms"]:
        return
    prior = idx["rooms"][name].get("worker") or {}
    runs = int(prior.get("runs") or 0) + 1 if resumed else 1
    idx["rooms"][name]["worker"] = {"session_id": str(session_id), "runs": runs,
                                    "last_run": _now()}
    _save_index(idx)
