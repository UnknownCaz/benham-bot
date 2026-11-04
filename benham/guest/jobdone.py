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
    judge(user_id, text, reply, open_jobs, log)
                                 -> {"done": [ids], "outcome": {id: words},
                                     "reply_closes": bool}

WHY THE JUDGE NAMES JOBS. A person may hold several open jobs (Caz's call,
2026-11-03 chat), so "done" is not a yes/no: the judge gets every open goal with
its id and returns the ids it is SURE their message closes. Code keeps only ids it
passed in, so the worst a guest can talk it into is closing their own job early -
which costs them nothing but a fresh ask, and Caz hears about it in the heads-up.
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


JUDGE_MAX_TOKENS = 200
MAX_CHARS = 2000        # of their message and of the reply; the gist decides

JUDGE_SYSTEM = (
    "You sort messages. You are not talking to anyone, and nothing you write is "
    "shown to a person. Benham, a Discord bot, is helping someone with one or more "
    "jobs. Read their newest message and decide which jobs, if any, they just said "
    "are done. Reply with JSON only."
)


def judge_prompt(text, reply, open_jobs):
    lines = [f"Benham is helping them with {len(open_jobs)} job(s):", ""]
    for j in open_jobs:
        lines.append(f"[{j['id']}] {str(j.get('goal') or '')[:600]}")
    lines += ["", msgparts.fence("their newest message", [str(text)[:MAX_CHARS]]), "",
              msgparts.fence("Benham's reply to it", [str(reply or "")[:MAX_CHARS]]), ""]
    lines.append(
        "Which jobs did they clearly say are done, or that they're satisfied with - "
        "\"found it\", \"that's it, thanks\", \"all sorted\", \"got it working\"? Only a "
        "clear signal from THEM counts. A thanks in passing while they keep going, a "
        "question, new details, \"maybe\", or Benham suggesting they might be done "
        "does not. When unsure, it is not done. A message that clearly covers "
        "everything (\"that's everything, thanks\") closes all of them.")
    lines.append("")
    lines.append("Also say whether Benham's reply already wraps the conversation up "
                 "(a goodbye, or \"glad that's sorted\").")
    lines.append("")
    lines.append('Reply with JSON only: {"done": ["j3"], "outcome": {"j3": "a few of '
                 'their words"}, "reply_closes": true} - or {"done": [], "outcome": {}, '
                 '"reply_closes": false}.')
    return "\n".join(lines)


def parse_verdict(raw, open_jobs, text):
    """The verdict out of the judge's reply, kept to the ids it was given. Fails
    toward NOT done on anything unexpected: a job left open still closes on the
    quiet timer; one closed wrongly ends a chat the person was still in."""
    none = {"done": [], "outcome": {}, "reply_closes": False}
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return none
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return none
    if not isinstance(data, dict) or not isinstance(data.get("done"), list):
        return none
    allowed = [j["id"] for j in open_jobs]
    done = []
    for i in data["done"]:
        s = str(i).strip()
        if s in allowed and s not in done:
            done.append(s)
    said = " ".join(str(text or "").split())
    said = said if len(said) <= 120 else said[:117] + "..."
    given = data.get("outcome") if isinstance(data.get("outcome"), dict) else {}
    outcome = {}
    for i in done:
        o = " ".join(str(given.get(i) or "").split())
        outcome[i] = (o[:117] + "...") if len(o) > 120 else (o or said)
    return {"done": done, "outcome": outcome,
            "reply_closes": bool(data.get("reply_closes")) if done else False}


def judge(user_id, text, reply, open_jobs, log=None):
    """Which of `open_jobs` (this person's, oldest first) did their message say
    are done? See parse_verdict for the failure direction."""
    def _log(msg):
        if log:
            log(msg)

    none = {"done": [], "outcome": {}, "reply_closes": False}
    if not open_jobs or not str(text or "").strip():
        return none
    try:
        resp = guest._get_client().messages.create(
            model=MODEL, max_tokens=JUDGE_MAX_TOKENS, system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": judge_prompt(text, reply, open_jobs)}],
            timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001 - an unjudged message leaves the job open
        _log(f"job judge failed for {user_id} ({type(e).__name__}: {e}) - left open")
        return none
    _usage_line(log, f"job-judge:{user_id}", resp)
    raw = "".join(getattr(b, "text", "") for b in resp.content
                  if getattr(b, "type", "") == "text")
    verdict = parse_verdict(raw, open_jobs, text)
    _log(f"job judge [{user_id}]: "
         + (f"done {', '.join(verdict['done'])}" if verdict["done"] else "not done")
         + f" (of {', '.join(j['id'] for j in open_jobs)})")
    return verdict
