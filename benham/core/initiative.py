"""initiative.py - the state behind Claude reaching out first.

Every Claude session is reactive. Nothing runs unless Tyler types. Two things
follow from that, and both of them are losses:

  A follow-up Claude offers is structurally impossible to keep. "I'll check
  tomorrow whether that worked" is not a promise a reactive system can make -
  there is no tomorrow, only the next time he starts a session, and by then the
  thing has usually gone quiet. Every such offer has been, honestly, a lie.

  Claude can never be curious about him on its own. Everything Claude has ever
  learned about Tyler, it learned because he brought it up first.

This module is the state that fixes both. `benham.cli.initiate` is the command
surface, `policy.authorize_unprompted` is the gate, and a daily scheduled job is
the thing that wakes up. Two stores, one file:

  THREADS - open loops Claude is holding. Any session, at any time, can drop one
  ("I said I'd find out whether the zoom read right on stream"). They are the
  highest-quality source of things worth asking later, and they are the reason a
  follow-up survives the session that offered it. Without them the daily job
  would have to re-derive intent from logs, which it cannot do.

  RUNS - what the job decided, every time it woke, and WHY. Including - mostly -
  "nothing worth asking today". This is not telemetry. Tyler has to be able to
  audit that the silence is working as designed rather than that the job broke
  three weeks ago, and the only difference between those two from outside is
  this log. It is also written as readable markdown (see LOG_MD) rather than
  only as json, because a log a human will not open does not do that job.

WHAT THIS MODULE DOES NOT DECIDE. Whether a question may go out is
policy.authorize_unprompted, not anything here. This module reports facts the
rules are computed from - what is outstanding, when the last one went, how many
lapsed in a row - and deliberately holds no opinion about them. The numbers live
in policy.py because that is the file a human edits to change what Claude is
allowed to do.

WHY LAPSING IS SILENT. An unanswered unprompted question expires after
policy.UNPROMPTED_LAPSE_AFTER and nothing is sent when it does. The alternative
- telling him a question timed out - is "you never answered me" with extra steps,
and that tone is exactly what this whole design is trying not to have.
"""

import os
import threading
from datetime import datetime, timezone

from benham import paths
from benham.core import conversations, jsonio, policy

STORE = os.path.join(paths.process_state_dir(), "initiative.json")

# The human-readable half. Gitignored alongside the json - these are Claude's
# private notes about Tyler and questions about his life, and neither belongs in
# a repo that has a remote.
LOG_MD = os.path.join(paths.process_state_dir(), "initiative-log.md")

# How many runs to keep in the json. The markdown log is never truncated - it is
# the audit trail, it is a few lines a day, and a year of it is a small file.
MAX_RUNS = 400

# --- thread states ---------------------------------------------------------
# OPEN     Claude is holding this loop; it is a candidate for the daily job
# ASKED    it became the question that went out; waiting on Tyler
# CLOSED   resolved - he answered, or it resolved itself
# DROPPED  no longer worth asking. Dropping is a real outcome, not a failure:
#          most open loops should die this way rather than becoming messages.
T_OPEN, T_ASKED, T_CLOSED, T_DROPPED = "open", "asked", "closed", "dropped"

# --- run decisions ---------------------------------------------------------
# SILENT   the job read state and decided nothing was worth asking. THE NORMAL
#          OUTPUT, and the one the log exists to make visible.
# ASKED    a question went out
# BLOCKED  the job wanted to ask and policy refused. Recorded distinctly from
#          SILENT because it means something completely different: silence by
#          judgement is the design working, silence by gate is the design
#          catching the job, and a run of BLOCKED entries is a signal that the
#          job's taste has drifted.
# ERROR    the run itself failed. Loud, on purpose - see the memory-pipeline
#          precedent, where a scheduled task died silently for two weeks.
R_SILENT, R_ASKED, R_BLOCKED, R_ERROR = "silent", "asked", "blocked", "error"

_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _parse(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load():
    data = jsonio.read_json(STORE, default={})
    data.setdefault("threads", {})
    data.setdefault("runs", [])
    data.setdefault("seq", 0)
    # Set by hand or by `initiate reset`; see lane_dormant().
    data.setdefault("reset_at", None)
    return data


def _save(data):
    jsonio.write_json(STORE, data)


# ==========================================================================
# Threads - the open loops
# ==========================================================================

def add_thread(text, why=None, source=None, not_before=None):
    """Record an open loop for a later run to consider. Returns the thread.

    `text` is the loop in Claude's own words, phrased as the thing that is
    unresolved - not as the question. The question gets written later, by the run
    that decides to ask it, with whatever the world looks like then.

    `why` is why it is worth Tyler's attention at all. Write it honestly: a
    thread with no real answer to this is one that should never have been noted,
    and the daily job is instructed to drop those on sight.

    `not_before` is an ISO date. The commonest way for this mechanism to be
    annoying is to ask about something the same day it was mentioned, before he
    has had any chance to do it, so a thread can say when it becomes askable.
    """
    with _lock:
        data = _load()
        data["seq"] = int(data.get("seq", 0)) + 1
        tid = f"t{data['seq']}"
        thread = {
            "id": tid,
            "text": str(text),
            "why": (str(why) if why else None),
            "source": (str(source) if source else None),
            "noted_at": _iso(_now()),
            "not_before": (str(not_before) if not_before else None),
            "state": T_OPEN,
            "conv_id": None,
            "asked_at": None,
            "closed_at": None,
            "outcome": None,
        }
        data["threads"][tid] = thread
        _save(data)
        return thread


def threads(state=None, askable_on=None):
    """Threads, newest first. `state` filters; `askable_on` applies not_before.

    `askable_on` is a date string (YYYY-MM-DD). A thread whose not_before is
    later than it is excluded - it exists, it is just not yet a candidate.
    """
    data = _load()
    out = list(data["threads"].values())
    if state:
        out = [t for t in out if t.get("state") == state]
    if askable_on:
        out = [t for t in out
               if not t.get("not_before") or str(t["not_before"]) <= str(askable_on)]
    return sorted(out, key=lambda t: t.get("noted_at") or "", reverse=True)


def thread(tid):
    return _load()["threads"].get(str(tid))


def _mutate_thread(tid, fn):
    with _lock:
        data = _load()
        t = data["threads"].get(str(tid))
        if t is None:
            raise KeyError(f"no thread {tid!r}")
        fn(t)
        data["threads"][str(tid)] = t
        _save(data)
        return t


def mark_thread_asked(tid, conv_id):
    def go(t):
        t["state"] = T_ASKED
        t["conv_id"] = str(conv_id)
        t["asked_at"] = _iso(_now())
    return _mutate_thread(tid, go)


def close_thread(tid, outcome):
    """He answered, or it resolved itself. The loop is genuinely shut."""
    def go(t):
        t["state"] = T_CLOSED
        t["outcome"] = str(outcome)
        t["closed_at"] = _iso(_now())
    return _mutate_thread(tid, go)


def drop_thread(tid, reason):
    """Not worth asking after all. The healthy majority outcome.

    Separate from close_thread because the distinction is the one worth being
    able to read later: closed means the loop resolved, dropped means Claude
    decided it never deserved his attention. A store where everything closes is
    a store whose judgement is not being exercised.
    """
    def go(t):
        t["state"] = T_DROPPED
        t["outcome"] = str(reason)
        t["closed_at"] = _iso(_now())
    return _mutate_thread(tid, go)


# ==========================================================================
# The unprompted conversation itself
# ==========================================================================

def open_question(text, purpose=None, thread_id=None):
    """Open an UNPROMPTED conversation with the owner. Does NOT deliver it.

    Delivery is `deliver_unprompted`, which runs inside the bot and calls
    policy.authorize_unprompted first. Splitting the two is what lets the CLI
    dry-run the gate without a Discord connection and without leaving a record
    behind when the answer is no.
    """
    owner = sorted(policy.identity.OWNER_IDS)
    if not owner:
        raise RuntimeError("no owner is configured in control.json")
    conv = conversations.open_conversation(
        owner[0],
        purpose=purpose or text,
        question=text,
        direction=conversations.UNPROMPTED,
        # Meaningless in this lane - an unprompted question is never in the
        # numbered queue and never competes for a slot - but the field is
        # required and WHENEVER is the honest value. Nothing is blocked on it.
        priority=conversations.WHENEVER,
        origin="initiative (Claude asked on its own)",
        placement_reason=(f"thread {thread_id}" if thread_id else None))
    return conv


def unprompted_conversations(counterparty=None):
    """Every unprompted conversation ever, oldest first."""
    out = [c for c in conversations.all_conversations()
           if c.get("direction") == conversations.UNPROMPTED
           and (counterparty is None
                or int(c.get("counterparty", 0)) == int(counterparty))]
    return sorted(out, key=lambda c: c.get("seq", 0))


def outstanding(now=None):
    """Delivered, still live, and not yet lapsed. What blocks a second question.

    The lapse check is done HERE rather than trusting sweep() to have run,
    because policy consults this at the moment a message would go out and must
    not depend on some other process having tidied up first. sweep() writes the
    same conclusion down; this one computes it.
    """
    now = now or _now()
    out = []
    for c in unprompted_conversations():
        if c.get("state") not in conversations.LIVE_STATES:
            continue
        when = _parse(c.get("delivered_at"))
        if not when:
            continue  # opened but never sent - it is not on his screen
        if now - when > policy.UNPROMPTED_LAPSE_AFTER:
            continue  # lapsed; sweep() will write that down
        out.append(c)
    return sorted(out, key=lambda c: c.get("delivered_at") or "")


def live_unprompted_for(counterparty):
    """The unprompted question still waiting on this person, or None.

    THE ANSWER PATH, and the reason it exists is a hole rather than a feature.
    conversations.live_for() reads the ASKING queue, and an unprompted question
    is deliberately not in it - so the model was never told one existed, and the
    ordinary way a person answers a DM (typing back, not using Discord's reply)
    would have gone straight past it. A Discord reply always bound correctly;
    everything else silently did not, until the question lapsed.

    Returns the OLDEST live one, though policy allows only one at a time, so in
    practice there is never a choice to make.
    """
    live = [c for c in unprompted_conversations(counterparty=counterparty)
            if c.get("state") in conversations.LIVE_STATES
            and c.get("delivered_at")]
    return live[0] if live else None


def last_delivered_at():
    """When the most recent unprompted question actually reached him, or None."""
    times = [_parse(c.get("delivered_at")) for c in unprompted_conversations()]
    times = [t for t in times if t]
    return max(times) if times else None


def consecutive_lapses():
    """How many unprompted questions in a row went unanswered, most recent back.

    Counts only DELIVERED ones - a question that never reached him says nothing
    about whether this channel lands. Stops at the first one he engaged with, and
    at `reset_at`: a human clearing the lane by hand means the count starts again
    from there rather than from the beginning of time.
    """
    data = _load()
    floor = _parse(data.get("reset_at"))
    n = 0
    for c in reversed(unprompted_conversations()):
        when = _parse(c.get("delivered_at"))
        if not when:
            continue
        if floor and when <= floor:
            break
        if c.get("state") == conversations.ANSWERED:
            break
        if c.get("state") in conversations.LIVE_STATES:
            # Still inside its window - undecided, and undecided is not a lapse.
            if _now() - when <= policy.UNPROMPTED_LAPSE_AFTER:
                break
        n += 1
    return n


def reset():
    """Clear the dormant state by hand. Human-only; the job never calls this."""
    with _lock:
        data = _load()
        data["reset_at"] = _iso(_now())
        _save(data)
    return data["reset_at"]


def sweep(now=None):
    """Write down what the clock has already decided. Returns a list of notes.

    Two jobs, both idempotent, both safe to run at the start of every run:

      LAPSE anything past the window. bank() is the existing verb for "gave up
      waiting, kept the question", which is exactly right - and crucially, no
      message is sent. A lapsed question is still answerable inside
      conversations.BANK_GRACE and its text is still on his screen.

      RECONCILE threads whose conversation has been answered, so a thread does
      not sit in ASKED forever after Tyler has already replied.
    """
    now = now or _now()
    notes = []
    for c in unprompted_conversations():
        when = _parse(c.get("delivered_at"))
        if (c.get("state") in conversations.LIVE_STATES and when
                and now - when > policy.UNPROMPTED_LAPSE_AFTER):
            conversations.bank(
                c["id"],
                reason="unprompted question lapsed unanswered - nothing was sent "
                       "about it, and nothing should be")
            notes.append(f"{c['id']} lapsed (asked {str(c.get('delivered_at'))[:16]}Z)")

    for t in threads(state=T_ASKED):
        conv = conversations.get(t.get("conv_id") or "")
        if conv and conv.get("state") == conversations.ANSWERED:
            close_thread(t["id"], f"answered: {str(conv.get('answer'))[:200]}")
            notes.append(f"{t['id']} closed - he answered {conv['id']}")
    return notes


# ==========================================================================
# Runs - the audit trail
# ==========================================================================

def record_run(decision, reason, question=None, conv_id=None, thread_id=None,
               gate=None, read=None):
    """Log one wake-up. Called on EVERY run, including - especially - silent ones.

    `read` is a short list of what the run actually looked at. It is here because
    of the one question this log has to be able to answer at a glance months
    later: is this thing quiet because it is working, or quiet because it broke?
    A silent entry that names nine boards and three threads answers it. A silent
    entry that says only "nothing to ask" does not.
    """
    rec = {
        "at": _iso(_now()),
        "decision": str(decision),
        "reason": str(reason),
        "question": (str(question) if question else None),
        "conv_id": (str(conv_id) if conv_id else None),
        "thread_id": (str(thread_id) if thread_id else None),
        "gate": (str(gate) if gate else None),
        "read": list(read) if read else [],
    }
    with _lock:
        data = _load()
        data["runs"].append(rec)
        data["runs"] = data["runs"][-MAX_RUNS:]
        _save(data)
    _append_markdown(rec)
    return rec


def runs(limit=20):
    return _load()["runs"][-int(limit):]


_MD_HEADER = """# Claude's initiative log

Every time the daily job woke up, what it decided, and why. **Silence is the
designed output** - a long run of `silent` entries is this working, not this
broken. What tells the two apart is the `read:` line: a silent entry that names
what it looked at was a run that happened and chose not to speak.

`blocked` means the job wanted to ask and policy.authorize_unprompted refused.
That is not a bug either, but several in a row means the job's taste has drifted
and the prompt is worth re-reading.

Newest entries are at the bottom. Nothing here is ever rewritten.
"""


def _append_markdown(rec):
    """The half of the log a human will actually open."""
    new = not os.path.exists(LOG_MD)
    os.makedirs(os.path.dirname(LOG_MD), exist_ok=True)
    lines = []
    if new:
        lines.append(_MD_HEADER)
    stamp = rec["at"][:16].replace("T", " ")
    lines.append(f"\n## {stamp}Z - {rec['decision']}")
    if rec.get("read"):
        lines.append(f"read: {', '.join(str(r) for r in rec['read'])}")
    if rec.get("question"):
        lines.append(f"asked ({rec.get('conv_id') or '?'}): {rec['question']}")
    if rec.get("thread_id"):
        lines.append(f"thread: {rec['thread_id']}")
    if rec.get("gate"):
        lines.append(f"refused by: {rec['gate']}")
    lines.append("")
    lines.append(rec["reason"])
    with open(LOG_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def read_markdown(tail_bytes=8000):
    """The end of the human log, for `initiate log`."""
    if not os.path.exists(LOG_MD):
        return "(no runs logged yet)"
    with open(LOG_MD, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return text if len(text) <= tail_bytes else "...\n" + text[-tail_bytes:]


# ==========================================================================
# The one status call the job makes before deciding anything
# ==========================================================================

def lane_state(now=None):
    """Everything the job needs to know about whether it may speak at all."""
    now = now or _now()
    last = last_delivered_at()
    lapses = consecutive_lapses()
    out = {
        "outstanding": [
            {"id": c["id"], "question": c["question"],
             "delivered_at": c.get("delivered_at")}
            for c in outstanding(now=now)],
        "last_delivered_at": _iso(last) if last else None,
        "hours_since_last": (round((now - last).total_seconds() / 3600, 1)
                             if last else None),
        "min_gap_hours": int(policy.unprompted_min_gap().total_seconds() // 3600),
        "consecutive_lapses": lapses,
        "dormant": lapses >= policy.UNPROMPTED_MAX_LAPSES,
        "open_threads": len(threads(state=T_OPEN)),
        "runs_logged": len(_load()["runs"]),
    }
    # The single line the job should read first. Computed by asking the real
    # gate about a hypothetical question rather than by re-deriving the rules
    # here, so it can never say "clear" while the gate would say no.
    probe = {"id": "(probe)", "direction": conversations.UNPROMPTED,
             "counterparty": (sorted(policy.identity.OWNER_IDS) or [0])[0],
             "question": "probe?"}
    verdict = policy.authorize_unprompted(probe, now=now)
    out["may_ask"] = verdict.allowed
    out["why_not"] = None if verdict.allowed else verdict.reason
    return out
