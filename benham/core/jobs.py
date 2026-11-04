"""
jobs.py - what Benham has been asked to do, and with whom, as live state
(INTENT decisions 61 and 62, Stage 9 B).

THE OBJECT. Caz, 2026-11-03 17:17 local: "I want Benham to exist more as something
like that ask, 'Talk to Biggie about X', or 'Create Role for XYZ, in Server F, and
interview me about its permissions after it's made.'" So a job is a goal in his
words, who it is WITH (a known person, or Caz himself), the tools it may use, and
a state. The guest with a purpose is one kind of job; the role-then-interview ask
is another (Stage 10, the same object).

THE BREAK IT ENDS. On 2026-11-03 14:23 the opener to Biggie went through
`outreach`, so his answer was bound as an outreach answer and the guest brain was
told to acknowledge it - it relayed instead of helping. A job is NEVER an outreach
conversation: open_job sends a plain DM, and while the job is open bot.py hands the
guest brain the GOAL (job_note), not the bound/waiting note.

READ ON EVERY USE, like people.py: no cache, no import-time snapshot. bot.py asks
open_all_for() on every guest DM, so a job opened one second ago shapes the very
next reply, and a closed one stops shaping it.

SEVERAL AT ONCE (Stage C, Caz's call 2026-11-03 in chat: "Call 1: allow several
open jobs per person"). Stage B allowed one open job per person; that is lifted. A
message from the person touches EVERY open job of theirs, the brain's note lists
every open goal, and done detection says which one(s) it closed. Only the same
goal twice, still open with the same person, is refused (same_goal).

    state/jobs.json   {"j<n>": {"id": "j<n>", "goal": "<Caz's words>",
                                "with": "<user_id>" | "caz", "tools": ["web_search"],
                                "state": "open" | "nudged" | "closed",
                                "opened": "<ISO, Detroit clock + offset>",
                                "opened_by": "caz" | "cli",
                                "last_activity": "<ISO>", "turns": <int>,
                                "opener": "<the text sent>" | None,
                                "log": [{"at": ..., "event": ..., ...}],
                                ...close fields}}

`nudged` is Stage C's (quiet one day, asked once); it counts as open here so the
store needs no change when Stage C starts writing it. Jobs are never deleted -
a closed job is the record of what was done.

Per face, like people.py: state_for(face) at CALL time, so the test redirect of
STATE_DIR flows through. Writes are serialised by one lock (jsonio stages through a
single `path + ".tmp"`); only the job verbs and bot.py's touch() write, all in the
bot process.
"""

import os
import threading

from benham import paths
from benham.core import eventtime
from benham.core import jsonio

FILE_NAME = "jobs.json"
CAZ = "caz"                       # `with` for a job that is Benham's own to run
OPEN_STATES = ("open", "nudged")  # nudged = Stage C's one ask; still open
TOOLS = ("web_search",)           # what Stage B grants; Stage C grants others

_lock = threading.Lock()


def path(face=None):
    """Where this face's jobs live. None = the face this process runs as."""
    return os.path.join(paths.state_for(face or paths.PROCESS_FACE), FILE_NAME)


def stamp():
    """Now, on this PC's Detroit clock with its offset (the house time rule)."""
    return eventtime.now().astimezone(eventtime.DETROIT).isoformat(timespec="seconds")


def _num(job_id):
    try:
        return int(str(job_id).lstrip("j"))
    except (TypeError, ValueError):
        return None


def _clean(raw):
    """{"j<n>": job} from whatever is on disk. A damaged row is dropped, never
    fatal: one bad job must not take every open job's note away (the people.py
    rule)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        n = _num(k)
        if n is None or n <= 0 or not isinstance(v, dict) or not v.get("goal"):
            continue
        job = dict(v)
        job["id"] = f"j{n}"
        job["with"] = str(job.get("with") or "")
        job["tools"] = [str(t) for t in (job.get("tools") or []) if t]
        job["state"] = str(job.get("state") or "open")
        job["log"] = list(job.get("log") or [])
        job["turns"] = int(job.get("turns") or 0)
        out[job["id"]] = job
    return out


def load(face=None):
    """Every job, {"j<n>": job}, read from disk NOW, oldest first. An unreadable
    face name answers empty rather than raising."""
    try:
        p = path(face)
    except ValueError:
        return {}
    jobs = _clean(jsonio.read_json(p, default={}))
    return dict(sorted(jobs.items(), key=lambda kv: _num(kv[0])))


def get(job_id, face=None):
    """One job by "j3" or "3", or None."""
    n = _num(job_id)
    return None if n is None else load(face).get(f"j{n}")


def is_open(job):
    return bool(job) and job.get("state") in OPEN_STATES


def open_all_for(user_id, face=None):
    """Every OPEN job with this person, oldest first ([] if none). bot.py's
    question on every guest DM since Stage C: a person may hold several open
    jobs at once (Caz's call, 2026-11-03 chat: "Call 1: allow several open jobs
    per person"). `caz` jobs are never a guest's, whatever is asked."""
    try:
        who = str(int(user_id))
    except (TypeError, ValueError):
        return []
    return [j for j in load(face).values() if j["with"] == who and is_open(j)]


def open_for(user_id, face=None):
    """The newest OPEN job with this person, or None (open_all_for's last)."""
    hits = open_all_for(user_id, face)
    return hits[-1] if hits else None


def same_goal(a, b):
    """Two goals that say the same thing, ignoring case and spacing - the one
    duplicate open_job refuses."""
    return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()


def _write(current, p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    jsonio.write_json(p, {k: current[k] for k in sorted(current, key=_num)})


def create(job, face=None):
    """Store a new job under the next free id. Returns it as written."""
    with _lock:
        p = path(face)
        current = _clean(jsonio.read_json(p, default={}))
        n = max((_num(k) for k in current), default=0) + 1
        rec = dict(job)
        rec["id"] = f"j{n}"
        rec.setdefault("state", "open")
        rec.setdefault("log", [])
        rec.setdefault("turns", 0)
        current[rec["id"]] = rec
        _write(current, p)
    return rec


def update(job_id, face=None, log=None, **fields):
    """Merge `fields` into one job and append `log` (a dict) to its log, if given.
    Returns the job as written, or None if there is no such job."""
    n = _num(job_id)
    if n is None:
        return None
    with _lock:
        p = path(face)
        current = _clean(jsonio.read_json(p, default={}))
        job = current.get(f"j{n}")
        if job is None:
            return None
        job.update(fields)
        if log:
            job["log"].append({"at": stamp(), **log})
        _write(current, p)
    return job


def touch(job_id, face=None):
    """A message from the person the job is with: last_activity now, one more
    turn. Stage C's quiet timer reads last_activity."""
    n = _num(job_id)
    if n is None:
        return None
    with _lock:
        p = path(face)
        current = _clean(jsonio.read_json(p, default={}))
        job = current.get(f"j{n}")
        if job is None:
            return None
        job["last_activity"] = stamp()
        job["turns"] = int(job.get("turns") or 0) + 1
        _write(current, p)
    return job
