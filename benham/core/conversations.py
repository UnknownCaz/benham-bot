"""
conversations.py - an ASK, and everything that happens to it afterwards.

Stage 3 of the refactor, and the primitive the rest of it is built on. Tyler's
framing (2026-08-16): the discord-outreach flow turned out to be a full use case,
and Benham can become it.

Benham is built on ACTIONS: fifty-odd stateless verbs you invoke and forget. That
is the right shape for `purge_messages`. It is the wrong shape for "ask Doom
whether the fix worked", which has a counterparty, a purpose, a deadline, and an
answer that arrives minutes later in a different process. Every stalled loop in
INTENT.md §3 is one of those with nowhere to live:

  Doom files a bug and never hears back      - a conversation with no close
  A session needs Tyler and cannot reach him - a conversation with no target
  Notifications with no priority             - conversation urgency, unmodelled

So: a conversation is a first-class object with state, and it OUTLIVES the
session that opened it. That is the whole point. The session that asks may be
gone by the time the answer arrives; the thing that closes the loop reads this
file, not a variable in someone's memory.

CODE OWNS TIMING, THE MODEL OWNS MEANING (Tyler's call). Everything in here is
mechanical: when a nudge is due, how many are allowed, when to give up, what to
write down. Nothing in this module asks a model anything. Whether an answer is
sufficient, which project a report belongs to, whether two reports are the same
bug - those are judgment, they belong to a caller with a model attached, and
keeping them out of here is what lets the loop close with no session running.

ONE OPEN CONVERSATION PER COUNTERPARTY, enforced in code. This is not politeness
about not pestering people, though it is that too. It is what makes an answer
BINDABLE: when someone replies in a DM, "which question is this answering" has
exactly one candidate. The discord-outreach skill already worked this way as a
rule that a human had to remember; here it is an invariant that open() refuses to
violate.

TERMINAL STATES ONLY. Doom, asked directly on 2026-08-16 what he wanted back:
"sorta i kinda want to know when it gets solved", and separately that a wont-fix
or not-a-bug is worth hearing too. So CLOSED (with an outcome) and BANKED are
reportable and ANSWERED is not - progress is not the message. That is why
`close()` takes an outcome string and `answer()` does not.
"""

import threading
from datetime import datetime, timedelta, timezone

from benham import paths
from benham.core import jsonio

import os

STORE = os.path.join(paths.STATE_DIR, "conversations.json")

# --- states -----------------------------------------------------------------
# OPEN     asked, waiting, no nudge yet
# NUDGED   asked again; `nudges` says how many times
# ANSWERED they replied. NOT reportable - see the module docstring
# CLOSED   they were told the outcome. The loop is shut
# BANKED   gave up waiting. The question is preserved, not lost
OPEN, NUDGED, ANSWERED, CLOSED, BANKED = (
    "open", "nudged", "answered", "closed", "banked")

LIVE_STATES = (OPEN, NUDGED)          # still waiting on a human
TERMINAL_STATES = (CLOSED, BANKED)    # worth telling the counterparty about

# --- direction: who owes whom -----------------------------------------------
# A conversation has a counterparty, but the OBLIGATION can run either way, and
# almost every rule here depends on which:
#
#   ASKING  we asked them something. We are waiting. Nudges apply, and only one
#           may be live per person - that one-at-a-time rule is what makes their
#           reply bindable.
#   OWED    they told US something (a bug, an idea) and we owe them an outcome.
#           Nudging would mean chasing someone for an answer we owe them, which
#           is absurd; and capping them at one would mean Doom's second report of
#           the day is refused, when he filed three in two days.
#
# Adding this rather than a separate Report type is what makes INTENT.md's claim
# true - that the collaborator loop and the reverse channel are one machinery
# with a different counterparty. The state machine is shared; only the timing
# rules and the invariant differ.
ASKING, OWED = "asking", "owed"

# Self-assessed by the asking session, which reads the queue before choosing.
# Named rather than numeric on purpose: integers invite an arms race (there is
# always a bigger number), while these three are a claim about the ASKER'S OWN
# STATE - something a session actually knows about itself, and something Tyler
# can sanity-check at a glance when he sees three sessions all claiming the top.
BLOCKING = "blocking"   # this session cannot continue without an answer
NORMAL = "normal"       # it gates something, but there is other work meanwhile
WHENEVER = "whenever"   # no rush; answer it when convenient
PRIORITIES = (BLOCKING, NORMAL, WHENEVER)
_RANK = {BLOCKING: 0, NORMAL: 1, WHENEVER: 2}

# --- nudge policy -----------------------------------------------------------
# Tyler's, retuned 2026-08-15 and proven against Doom before it was ever code.
NUDGE_AFTER = timedelta(minutes=15)
MAX_NUDGES = 2
# An away signal ("brb", visibly mid-game) may only ever EXTEND a wait, and by at
# most this much. Never shortens: a person who said they were busy has given you
# information about when to ask again, not permission to ask sooner.
MAX_DEFER = timedelta(hours=1)

_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _parse(s):
    return datetime.fromisoformat(s) if s else None


def _load():
    data = jsonio.read_json(STORE, default={})
    return data if isinstance(data, dict) else {}


def _save(data):
    jsonio.write_json(STORE, data)


def _event(conv, what, detail=None):
    """Append to the conversation's own record.

    Append-only, and never rewritten. This is the same principle selfrecord.py
    exists for: a thing that can say what happened to it does not have to be
    believed about what happened to it.
    """
    conv.setdefault("log", []).append(
        {"ts": _iso(_now()), "event": what, **({"detail": detail} if detail else {})})


def _next_id(data):
    """Short, stable, and monotonic. Not random: an id a human has to read back
    over Discord ("that was c7") should be typeable and orderable."""
    n = 1 + max((int(c.get("seq", 0)) for c in data.values()), default=0)
    return f"c{n}", n


# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------

def open_conversation(counterparty, purpose, question, project=None, origin=None,
                      now=None, direction=ASKING, priority=NORMAL,
                      placement_reason=None):
    """Start one. Returns the conversation dict.

    Several ASKING conversations may be live for one person - they form a queue,
    delivered as ONE numbered message rather than as N separate DMs. See
    queue_for() for the ordering and slot_of() for what makes an answer bindable
    now that "the live one" is no longer a unique thing.

    `priority` is SELF-ASSESSED by the caller (Tyler's call, 2026-08-17). The
    session decides where it belongs, having first read the queue - which is the
    whole point, and the reason self-assessment works here rather than inflating:
    ranking yourself in a vacuum has one safe answer, ranking yourself against
    three visible claims does not.

    `placement_reason` is why it put itself there, in its own words. Advisory but
    RECORDED: nothing stops a session claiming BLOCKING, and nothing hides it
    either. Line-jumping should cost visibility, not be impossible.

    `now` is injectable for the same reason nudge/defer/due take it: the deadline
    is arithmetic on a clock, and a test that has to wait fifteen real minutes to
    exercise it is a test nobody runs. It defaults, and test_conversations calls
    it BOTH ways - the first version of that test always passed a clock, so the
    default path was never run and shipped broken.
    """
    now = now or _now()
    if priority not in PRIORITIES:
        raise ValueError(
            f"unknown priority {priority!r} - one of {', '.join(PRIORITIES)}. "
            "A free-form field would become an arms race; these three are a claim "
            "about the ASKER's state, which is a thing a session actually knows.")
    with _lock:
        data = _load()
        cid, seq = _next_id(data)
        conv = {
            "id": cid,
            "seq": seq,
            "counterparty": int(counterparty),
            "direction": direction,
            "purpose": str(purpose),
            "question": str(question),
            "project": (str(project) if project else None),
            "origin": (str(origin) if origin else None),
            "priority": priority,
            "placement_reason": (str(placement_reason) if placement_reason else None),
            "state": OPEN,
            "opened_at": _iso(now),
            "due_at": _iso(now + NUDGE_AFTER),
            "nudges": 0,
            "ask_message_ids": [],
            "answer": None,
            "answered_at": None,
            "outcome": None,
            "closed_at": None,
            "log": [],
        }
        detail = str(purpose)
        if direction == ASKING:
            ahead = len(_queue(data, counterparty))
            detail += f" [{priority}, {ahead} ahead]"
            if placement_reason:
                detail += f" - {placement_reason}"
        _event(conv, "opened", detail)
        data[cid] = conv
        _save(data)
        return conv


def record_ask_message(cid, message_id):
    """Remember which Discord message carried the question.

    A LIST, not a field, and appended to on every nudge as well as the first ask.
    Tyler settled the binding rule as "reply binds, otherwise the model judges" -
    and a reply to the NUDGE is every bit as much an answer as a reply to the
    original. Storing only the first would have made the fast, certain path fail
    exactly when someone did the most natural thing: answer the message that just
    arrived.
    """
    def go(conv):
        ids = conv.setdefault("ask_message_ids", [])
        if int(message_id) not in ids:
            ids.append(int(message_id))
    return _mutate(cid, go)


def record_diagnosis(cid, text, confidence=None):
    """Store what a read-only triage session concluded about this report.

    Kept ON the conversation rather than in a side file, so the thing Tyler is
    asked to approve a fix for and the thing Doom eventually gets told about are
    the same record. A diagnosis in a separate place is a diagnosis that can drift
    from the report it explains.

    Explicitly NOT an outcome. It is a hypothesis until a human acts on it, and
    close() still needs a real outcome - "we think it is X" is not something
    anyone should be told is resolved.
    """
    def go(conv):
        conv["diagnosis"] = str(text)
        if confidence:
            conv["diagnosis_confidence"] = str(confidence)
        _event(conv, "triaged", (str(confidence) + ": " if confidence else "")
               + str(text)[:200])
    return _mutate(cid, go)


def restart_clock(cid, now=None):
    """Start the nudge countdown from now - called when the question is delivered.

    open() sets a deadline assuming the ask goes out promptly, which is usually
    true and occasionally very wrong: if the bot is down when a session asks, the
    conversation sits undelivered and its fifteen minutes elapse before the person
    has seen a word. Then the first thing they ever receive about it is a nudge.
    """
    now = now or _now()

    def go(conv):
        conv["due_at"] = _iso(now + NUDGE_AFTER)
        _event(conv, "delivered")
    return _mutate(cid, go)


def by_ask_message(message_id):
    """The live conversation whose question was carried by this message, or None.

    THE CERTAIN HALF OF BINDING. No model, no inference: Discord handed over a
    message reference, and this says which question it points at.
    """
    mid = int(message_id)
    for c in _load().values():
        if c.get("state") in LIVE_STATES and mid in (c.get("ask_message_ids") or []):
            return c
    return None


def _queue(data, counterparty):
    """Every ASKING conversation waiting on this person, in the order to ask.

    Ordered by self-assessed priority, then by seq - so within a priority it is
    strictly first-come-first-served and a later session cannot overtake an
    earlier one by claiming the same level. The only way past someone is to claim
    a HIGHER level, which is visible in the queue and in Tyler's message.

    OWED is excluded deliberately: it is what makes binding unambiguous. If a
    report Doom filed counted as waiting-on-him, his next message would be a
    candidate answer to his own bug report.
    """
    live = [c for c in data.values()
            if int(c.get("counterparty", 0)) == int(counterparty)
            and c.get("state") in LIVE_STATES
            and c.get("direction", ASKING) == ASKING]
    return sorted(live, key=lambda c: (_RANK.get(c.get("priority", NORMAL), 1),
                                       c.get("seq", 0)))


def _live_for(data, counterparty):
    """The FRONT of the queue, or None.

    Kept because plenty of callers only ever wanted "the one to nudge about", and
    with a queue that is simply the first. Anything that needs to know a queue
    exists should call queue_for() and think about the plural case.
    """
    q = _queue(data, counterparty)
    return q[0] if q else None


def queue_for(counterparty):
    """The full ordered queue waiting on this person. Read before you ask."""
    with _lock:
        return _queue(_load(), counterparty)


def slot_of(cid):
    """This conversation's 1-based number in its counterparty's queue.

    THE SLOT IS THE BINDING HANDLE, and it is what replaces the old one-live-ask
    invariant. That rule existed for exactly one reason - so a reply had a single
    candidate and "which question is this answering" could not be got wrong. A
    queue breaks that, so the numbering puts it back: "2: sqlite" is as certain as
    a Discord reply was, with no model involved.

    Recomputed rather than stored, so it can never disagree with the order the
    message actually showed. A stored slot would drift the moment anything ahead
    of it was answered, and a stale number is worse than none: it silently binds
    an answer to the wrong question.
    """
    with _lock:
        data = _load()
        conv = data.get(cid)
        if not conv:
            return None
        q = _queue(data, conv["counterparty"])
        for i, c in enumerate(q, 1):
            if c["id"] == cid:
                return i
    return None


def by_slot(counterparty, slot):
    """The conversation at this 1-based queue position, or None."""
    q = queue_for(counterparty)
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        return None
    return q[slot - 1] if 1 <= slot <= len(q) else None


BATCHES = os.path.join(paths.STATE_DIR, "ask_batches.json")


def batch_message(counterparty):
    """The Discord message currently showing this person their queue, or None.

    Its own tiny file rather than a reserved key in the conversation store: that
    store is iterated as {id: conversation} in a dozen places, and a key that was
    not a conversation would need a guard at every one of them. One of those
    guards would eventually be missed.
    """
    rec = jsonio.read_json(BATCHES, default={}).get(str(counterparty))
    return int(rec) if rec else None


def set_batch_message(counterparty, message_id):
    data = jsonio.read_json(BATCHES, default={})
    if message_id is None:
        data.pop(str(counterparty), None)
    else:
        data[str(counterparty)] = int(message_id)
    jsonio.write_json(BATCHES, data)


def render_queue(counterparty, queue=None):
    """The whole queue as one numbered message.

    Numbered because the number is the binding handle - see slot_of(). The
    self-assessed priority is shown rather than hidden: if three sessions all
    claim BLOCKING, Tyler should be able to see that they did, which is the only
    thing keeping self-assessment honest.
    """
    q = queue if queue is not None else queue_for(counterparty)
    if not q:
        return None
    if len(q) == 1:
        c = q[0]
        body = c["question"]
        if c.get("project"):
            body += f"\n\n_(about {c['project']})_"
        return body

    lines = [f"**{len(q)} things waiting on you.** Answer any of them by number "
             f"- \"2: sqlite\" - or just reply and I'll work it out.", ""]
    for i, c in enumerate(q, 1):
        tag = ""
        if c.get("priority") == BLOCKING:
            tag = " ⚠️ _blocking a session_"
        elif c.get("priority") == WHENEVER:
            tag = " _(no rush)_"
        where = f" · {c['project']}" if c.get("project") else ""
        lines.append(f"**{i}.** {c['question']}{tag}")
        if c.get("placement_reason"):
            lines.append(f"     _{c['placement_reason']}_{where}")
        elif where:
            lines.append(f"     _{where.lstrip(' ·')}_")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def get(cid):
    return _load().get(cid)


def live_for(counterparty):
    """The one live conversation with this person, or None.

    THE BINDING FUNCTION. When a DM arrives, this is what says which question it
    might be answering - and because open() enforces one-at-a-time, the answer is
    never ambiguous.
    """
    return _live_for(_load(), counterparty)


def all_conversations(state=None, counterparty=None, project=None):
    out = []
    for c in _load().values():
        if state and c.get("state") != state:
            continue
        if counterparty is not None and int(c.get("counterparty", 0)) != int(counterparty):
            continue
        if project and c.get("project") != project:
            continue
        out.append(c)
    out.sort(key=lambda c: c.get("seq", 0))
    return out


def due(now=None):
    """Live conversations whose nudge (or bank) is due. The state machine's input.

    Returns (conv, what) pairs where `what` is "nudge" or "bank" - the caller
    sends the message and then calls nudge()/bank(), so this module never needs to
    know how a message is delivered.
    """
    now = now or _now()
    out = []
    for c in _load().values():
        if c.get("state") not in LIVE_STATES:
            continue
        # An OWED conversation has no deadline on the other person - the deadline
        # is on US, and nothing here should ever nudge someone about a thing we
        # have not done yet.
        if c.get("direction", ASKING) != ASKING:
            continue
        deadline = _parse(c.get("due_at"))
        if not deadline or now < deadline:
            continue
        out.append((c, "nudge" if int(c.get("nudges", 0)) < MAX_NUDGES else "bank"))
    out.sort(key=lambda p: p[0].get("seq", 0))
    return out


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------

def _mutate(cid, fn):
    with _lock:
        data = _load()
        conv = data.get(cid)
        if conv is None:
            raise KeyError(f"no conversation {cid!r}")
        fn(conv)
        data[cid] = conv
        _save(data)
        return conv


def nudge(cid, now=None):
    """Record that a nudge was sent, and set the next deadline."""
    now = now or _now()

    def go(conv):
        if conv.get("state") not in LIVE_STATES:
            raise ValueError(f"{cid} is {conv.get('state')}, not waiting on anyone")
        if int(conv.get("nudges", 0)) >= MAX_NUDGES:
            raise ValueError(f"{cid} has already had {MAX_NUDGES} nudges - bank it")
        conv["nudges"] = int(conv.get("nudges", 0)) + 1
        conv["state"] = NUDGED
        conv["due_at"] = _iso(now + NUDGE_AFTER)
        _event(conv, "nudged", f"#{conv['nudges']}")
    return _mutate(cid, go)


def defer(cid, minutes, reason, now=None):
    """Push the next deadline out because they said they were busy.

    ONLY EVER FORWARD, and never past MAX_DEFER from now. Both halves matter. An
    away signal is information about when to ask again; letting it move a deadline
    closer would turn "brb" into a reason to pester someone sooner, and letting it
    move indefinitely would turn one "busy" into a conversation that never resolves.
    """
    now = now or _now()
    minutes = max(0, int(minutes))
    target = min(now + timedelta(minutes=minutes), now + MAX_DEFER)

    def go(conv):
        if conv.get("state") not in LIVE_STATES:
            raise ValueError(f"{cid} is {conv.get('state')}, not waiting on anyone")
        current = _parse(conv.get("due_at"))
        if current and target <= current:
            _event(conv, "defer-ignored", f"{reason} (would not extend)")
            return
        conv["due_at"] = _iso(target)
        _event(conv, "deferred", f"{reason} -> {_iso(target)}")
    return _mutate(cid, go)


def answer(cid, text, bound_by="reply"):
    """They replied. Records what they said and how it was bound to the question.

    `bound_by` is kept because the two ways differ in how much they can be
    trusted: "reply" means Discord carried a message reference, which is certain,
    and "only-live" means this was their one open conversation, which is an
    inference - correct by the one-at-a-time invariant, but an inference. A later
    dispute over what someone answered should be able to see which it was.
    """
    def go(conv):
        if conv.get("state") not in LIVE_STATES:
            raise ValueError(f"{cid} is {conv.get('state')}, not waiting on an answer")
        conv["state"] = ANSWERED
        conv["answer"] = str(text)
        conv["answered_at"] = _iso(_now())
        conv["due_at"] = None          # nothing is owed BY them any more
        _event(conv, "answered", f"via {bound_by}")
    return _mutate(cid, go)


def close(cid, outcome, told=False):
    """The loop is shut: they have been told, or are about to be, what happened.

    `outcome` is required and free text - "fixed", "wont-fix: sandbox has no
    network", "deferred to the board". Required because a close with no outcome is
    the failure this whole stage exists to end: the thing that looks resolved from
    the inside and reads as silence from the outside.
    """
    if not str(outcome).strip():
        raise ValueError("close() needs an outcome - a silent close is the bug this fixes")

    def go(conv):
        conv["state"] = CLOSED
        conv["outcome"] = str(outcome)
        conv["closed_at"] = _iso(_now())
        conv["due_at"] = None
        _event(conv, "closed", str(outcome))
        if told:
            _event(conv, "counterparty-told", str(outcome))
    return _mutate(cid, go)


def bank(cid, reason="no answer after the nudge budget", now=None):
    """Give up waiting, keep the question.

    NOT a failure state and not a deletion. Tyler's choice for an unanswered ask
    was "nudge like the outreach flow does, then report" - so the question stays
    readable, and asking it again later is a new conversation with this one in its
    history rather than a thing nobody remembers wanting to know.
    """
    def go(conv):
        conv["state"] = BANKED
        conv["closed_at"] = _iso(_now())
        conv["due_at"] = None
        _event(conv, "banked", str(reason))
    return _mutate(cid, go)


def mark_told(cid, what):
    """Record that the counterparty was actually informed of the outcome.

    Separate from close() on purpose. Closing is a decision made on this side;
    telling them is a message that either went out or did not. Collapsing the two
    would let a delivery failure look like a closed loop, which is the precise
    shape of the bug being fixed - work finishing and nobody hearing.
    """
    def go(conv):
        _event(conv, "counterparty-told", str(what))
    return _mutate(cid, go)


def forget(cid=None):
    """Drop one conversation, or all. Test/maintenance only."""
    with _lock:
        if cid is None:
            _save({})
            return
        data = _load()
        data.pop(cid, None)
        _save(data)
