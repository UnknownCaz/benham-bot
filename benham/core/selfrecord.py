"""
selfrecord.py - what Benham actually did, read from the log it already writes.

On 2026-08-15 Tyler asked Benham what a pc_task had done. Benham answered that it
had never run one - twice, with confidence - while he was looking at the eight
approval prompts it had just sent him. It was not lying. Its conversation memory
was corrupted (fixed in aacf21e), and memory was the only thing it could consult.

So this module exists to make that question answerable from EVIDENCE. Nothing new
is recorded to build it: capabilities.run() has always logged every invocation
(`action <name> by <actor>: <result>`), every refusal (`DENIED`) and every preview
(`PROPOSED`), and codesession logs each PC permission prompt. The record was
already there; it had no reader. Same discovery watch_pc.py made about Claude
Code's own transcripts.

THE THING THIS MODULE IS ACTUALLY FOR. Not "find the entry" - any grep does that.
It is to make "I have no record of that" mean something narrower than "it did not
happen". A log covers a window; ask about a moment outside it and honest silence
and damning silence look identical. So every result carries `covers`, the real
time span the files span, and `covered` says whether the question sat inside it.
A caller that ignores those can still make the 2026-08-15 mistake with better
data underneath it - which is why the capability wired to this one is told, in
its own description, to quote them.

TAINT. Action results embed text other people wrote (read_channel logs the
messages it read), so anything reading this is reading third-party text and the
capability on top of it declares taints=True. Without that it is a laundering
path: stranger text -> log -> an untainted turn -> pc_task.
"""

import os
import re
from datetime import datetime, timedelta, timezone

from benham import paths

# The bot's log() prefix, and the three shapes capabilities.run emits.
_STAMP = r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z)\]"
_LINE_RE = re.compile(
    _STAMP + r"\s+(?P<kind>action|DENIED|PROPOSED|PC-PERMISSION)\b(?P<rest>.*)",
    re.DOTALL)
_ACTION_RE = re.compile(r"^\s+(?P<name>\S+)\s+by\s+(?P<actor>\S+):\s*(?P<detail>.*)$", re.DOTALL)
_DENIED_RE = re.compile(r"^\s+(?P<name>\S+)\s+by\s+(?P<actor>\S+)\s*(?P<detail>.*)$", re.DOTALL)

# Newest last. supervise.log is the live one; bot.log is a pre-supervisor relic
# kept because it still holds July.
LOG_NAMES = ("bot.log", "supervise.log")


def _log_files():
    out = []
    for name in LOG_NAMES:
        p = os.path.join(paths.LOG_DIR, name)
        if os.path.exists(p):
            out.append(p)
    return out


def _parse_line(line):
    m = _LINE_RE.match(line)
    if not m:
        return None
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    kind, rest = m.group("kind"), m.group("rest")
    entry = {"ts": ts, "kind": kind, "name": None, "actor": None, "detail": rest.strip()}
    if kind == "action":
        a = _ACTION_RE.match(rest)
        if a:
            entry.update(name=a.group("name"), actor=a.group("actor"),
                         detail=a.group("detail").strip())
    elif kind in ("DENIED", "PROPOSED"):
        d = _DENIED_RE.match(rest)
        if d:
            entry.update(name=d.group("name"), actor=d.group("actor"),
                         detail=d.group("detail").strip())
    return entry


def read(limit=20, action=None, since_minutes=None, actor=None, max_detail=400, now=None):
    """Recent things Benham did, newest first, with the window they were read from.

    Returns a dict with:
      entries   - newest-first list of {ts, kind, action, actor, detail}
      covers    - {"from", "to"} the real span of the log files, or None if empty
      covered   - True/False/None: did the requested window fall INSIDE `covers`?
                  None when no window was requested. False means "the record does
                  not reach back that far", which is NOT the same as "nothing
                  happened" and must never be reported as if it were.
      truncated - more matched than `limit` returned
    """
    now = now or datetime.now(timezone.utc)
    entries, first_ts, last_ts = [], None, None

    for path in _log_files():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    e = _parse_line(line)
                    if e is None:
                        continue
                    # Span is measured over EVERY parsed line, not just matches -
                    # otherwise a filter that matches nothing would report a window
                    # of nothing and turn "I did not find it" into "there is no log".
                    if first_ts is None or e["ts"] < first_ts:
                        first_ts = e["ts"]
                    if last_ts is None or e["ts"] > last_ts:
                        last_ts = e["ts"]
                    entries.append(e)
        except OSError:
            continue

    cutoff = None
    if since_minutes is not None:
        cutoff = now - timedelta(minutes=int(since_minutes))

    matched = []
    for e in entries:
        if cutoff and e["ts"] < cutoff:
            continue
        if action and (e["name"] or "").lower() != str(action).lower():
            continue
        if actor and str(e["actor"] or "") != str(actor):
            continue
        matched.append(e)

    matched.sort(key=lambda e: e["ts"], reverse=True)
    total = len(matched)
    limit = max(1, min(int(limit), 200))
    shown = matched[:limit]

    covered = None
    if cutoff is not None:
        # The window is only genuinely covered if the log reaches back PAST its
        # start. Equal is not enough: a log beginning exactly at the cutoff may
        # have been rotated a moment earlier.
        covered = bool(first_ts and first_ts < cutoff)

    return {
        "entries": [{"ts": e["ts"].isoformat(),
                     "kind": e["kind"],
                     "action": e["name"],
                     "actor": e["actor"],
                     "detail": (e["detail"] or "")[:max_detail]} for e in shown],
        "matched": total,
        "truncated": total > len(shown),
        "covers": ({"from": first_ts.isoformat(), "to": last_ts.isoformat()}
                   if first_ts and last_ts else None),
        "covered": covered,
    }
