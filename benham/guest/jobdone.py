"""
jobdone.py - the two cheap model calls a job's close needs (INTENT decision 61,
Stage 9 C): what Benham found for the person, and whether they said they're done.

Both are the shape of answers.judge: a separate, small call on the cheapest
model, NOT the guest brain. The brain is the thing the guest talks to; these read
what was said and return a verdict or a few lines. Neither passes a tool, neither
runs a capability, and both fail toward doing nothing: a summary that cannot be
made says so, a judge that cannot judge leaves the job open (the quiet timer
still closes it).

    summarize(job, log)          -> up to 3 lines, for the heads-up to Caz
"""

import json
import re
from datetime import datetime, timezone

from benham.core import msgparts
from benham.guest import guest

# A classification and a summary, not a conversation - the cheapest model, the
# same knob answers.judge reads (guest.judge_model in control.json).
MODEL = guest._CFG.get("judge_model") or "claude-haiku-4-5"
TIMEOUT = 30
SUMMARY_MAX_TOKENS = 300
TURN_CHARS = 1500       # of each stored turn
CHAT_CHARS = 12000      # of the whole chat handed to the summary


def _when(text):
    try:
        d = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def chat_since(job):
    """The guest's stored turns with this job's person since it opened, oldest
    first, as (role, text). guest_memory keeps the last HISTORY_TURNS pairs, so
    this is the recent end of a long chat - enough for "what did it find"."""
    try:
        uid = int(job["with"])
    except (TypeError, ValueError, KeyError):
        return []
    opened = _when(job.get("opened"))
    out = []
    for t in guest._store.history(guest._key(uid), keep_ts=True):
        when = _when(t.get("ts"))
        if opened is not None and (when is None or when < opened):
            continue
        content = t.get("content")
        if not isinstance(content, str):
            continue
        out.append((t.get("role"), content[:TURN_CHARS]))
    return out


def queries(job):
    """Every web-search query logged into the job, in order."""
    return [q for e in job.get("log") or [] if e.get("event") == "searched"
            for q in e.get("queries") or []]


def _usage_line(log, label, resp):
    u = getattr(resp, "usage", None)
    if u is not None and log:
        log(f"agent usage [{label}] in={getattr(u, 'input_tokens', 0)} "
            f"out={getattr(u, 'output_tokens', 0)} model={MODEL}")


def summarize(job, log=None):
    """Up to three short lines: what Benham found for them. [] when no search
    ran (the heads-up then says "nothing searched" - no call is made)."""
    def _log(msg):
        if log:
            log(msg)

    if int(job.get("searches") or 0) <= 0:
        return []
    chat = chat_since(job)
    lines, size = [], 0
    for role, text in reversed(chat):
        who = "Them" if role == "user" else "Benham"
        piece = f"{who}: {text}"
        if size + len(piece) > CHAT_CHARS:
            break
        lines.insert(0, piece)
        size += len(piece)
    prompt = "\n".join([
        f"Benham, a Discord bot, was helping someone with this: {job.get('goal')}",
        "",
        "Web searches it ran: " + ("; ".join(queries(job)) or "(none logged)"),
        "",
        msgparts.fence("the chat since the job opened", lines or ["(no stored turns)"]),
        "",
        "In at most 3 short lines, say what Benham found for them - the names, "
        "links or answers that came out of it. Plain text, one finding per line, no "
        "preamble, no bullets. If nothing useful came out of it, say that in one line.",
    ])
    try:
        resp = guest._get_client().messages.create(
            model=MODEL, max_tokens=SUMMARY_MAX_TOKENS,
            system="You summarize. Nothing you write is shown to the person in the chat.",
            messages=[{"role": "user", "content": prompt}], timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001 - a missing summary must not lose the heads-up
        _log(f"job {job.get('id')}: summary failed ({type(e).__name__}: {e})")
        return [f"(I couldn't summarize it - {type(e).__name__}; the chat is in "
                "guest memory)"]
    _usage_line(log, f"job-summary:{job.get('id')}", resp)
    raw = "".join(getattr(b, "text", "") for b in resp.content
                  if getattr(b, "type", "") == "text")
    out = [re.sub(r"^\s*(?:[-*>]|\d+[.)])\s*", "", l).strip() for l in raw.splitlines()]
    return [l for l in out if l][:3]
