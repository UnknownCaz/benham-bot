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

ASKS QUEUE, AND THE SLOT IS THE BINDING HANDLE. Until 2026-08-17 there was one
open conversation per counterparty, enforced in code, and the reason was never
politeness - it was what made an answer BINDABLE: "which question is this
answering" had exactly one candidate.

Tyler replaced it with a queue, so several sessions can be waiting on him at once
without the second one being refused. The binding guarantee had to be REPLACED
rather than dropped, and that is slot_of(): the queue is delivered as one
numbered message and "2: sqlite" names its question exactly, with no model
involved. Slots are recomputed rather than stored, because a stored slot goes
stale the moment anything ahead of it is answered - and a stale number binds an
answer to the WRONG question, which is the precise failure the old rule prevented.

The corollary lives in bot.py: a Discord reply is certain only while there is one
candidate. Once the batch shows several, replying to it says "one of these"
without saying which, so it stops being the certain path and becomes a judgement
the model must announce.

TERMINAL STATES ONLY. Doom, asked directly on 2026-08-16 what he wanted back:
"sorta i kinda want to know when it gets solved", and separately that a wont-fix
or not-a-bug is worth hearing too. So CLOSED (with an outcome) and BANKED are
reportable and ANSWERED is not - progress is not the message. That is why
`close()` takes an outcome string and `answer()` does not.
"""

import re
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
#   ASKING  we asked them something. We are waiting. Nudges apply, and several may
#           be live per person - they form a queue (see _queue), delivered as one
#           numbered message. The SLOT is what makes their reply bindable now.
#   OWED    they told US something (a bug, an idea) and we owe them an outcome.
#           Nudging would mean chasing someone for an answer we owe them, which
#           is absurd. OWED is also excluded from the queue entirely: if a report
#           Doom filed counted as waiting-on-him, his next message would be a
#           candidate answer to his own bug report.
#
#   UNPROMPTED  NOBODY asked us to ask. Claude woke up on a timer, read real
#           state, and decided one question was worth Tyler's attention. Every
#           timing rule above is wrong for it: nudging a question he never
#           solicited is how a thing gets muted, and "you did not answer me"
#           is the guilt framing it must never carry. So it is excluded from
#           due() (no nudges, no banks) and from _queue() (no slot, no place in
#           the numbered batch message) by the two direction filters that were
#           already there for OWED - which is the whole reason this fits as a
#           direction rather than a new type. It still answers, still binds, and
#           still shows up in the record. It just never chases.
#
#           Its rate limiting lives in policy.authorize_unprompted, not here:
#           "may Claude interrupt Tyler unprompted right now" is a policy
#           question, and policy.py is where a human edits the answer.
#
# Adding this rather than a separate Report type is what makes INTENT.md's claim
# true - that the collaborator loop and the reverse channel are one machinery
# with a different counterparty. The state machine is shared; only the timing
# rules and the invariant differ.
ASKING, OWED, UNPROMPTED = "asking", "owed", "unprompted"

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


def nudge_cap_of(conv):
    """The nudge budget THIS conversation runs on. Takes a conv dict.

    Per-conversation `nudge_cap` when set, MAX_NUDGES otherwise. Exists because
    of c19 (2026-08-22): both corkboards said "ONE nudge max, then bank" - written
    precisely because Draco had been double-asked two days earlier - and the bot
    nudged twice anyway. The ceiling lived in prose; the timer read this module.
    A written ask only binds when the thing enforcing the schedule can see it, so
    the cap is a field on the record the timer already reads.

    Never ABOVE MAX_NUDGES: the global cap is Tyler's conduct policy, proven
    against a real person, and a per-conversation field must only ever be able to
    ask for LESS pressure than that, not more. open_conversation refuses a
    too-large cap outright; the min() here is belt for records edited by hand.
    """
    cap = conv.get("nudge_cap")
    if cap is None:
        return MAX_NUDGES
    return max(0, min(int(cap), MAX_NUDGES))
# An away signal ("brb", visibly mid-game) may only ever EXTEND a wait, and by at
# most this much. Never shortens: a person who said they were busy has given you
# information about when to ask again, not permission to ask sooner.
MAX_DEFER = timedelta(hours=1)

# A BANK IS NOT A DOOR SLAMMING (Tyler's call, 2026-08-17). Giving up waiting and
# refusing to hear are different things, and only the first was ever intended:
# bank() keeps the question precisely because it is still a real question. So an
# answer arriving just after the deadline is still an answer, and throwing it away
# is strictly worse than any alternative.
#
# It bit for real. c11 banked at 08:10:28 and Tyler answered it at 08:11:43 - 75
# seconds late. The answer went nowhere, silently, and Benham then told him it had
# been recorded. He was answering a question that was still on his screen; nothing
# about that moment told him it had expired.
BANK_GRACE = timedelta(minutes=10)

# How long a finished conversation stays worth mentioning to the model. Long
# enough to cover the batch message he is probably still looking at, short enough
# that the volatile block does not become a changelog.
STILL_ON_SCREEN = timedelta(minutes=30)

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
                      placement_reason=None, asker_session=None, nudge_cap=None):
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

    `asker_session` is the calling session's `local_` id (caller.session_id()),
    or None when nobody could be resolved - null is recorded, never a guess. It
    is how the Raven courier delivers the answer back to the session that asked;
    cwd-matching on `origin` is its fallback for records that predate the field.

    `nudge_cap` caps THIS conversation's nudges below the global MAX_NUDGES
    (0 = never nudge, bank at the deadline). Refused above the global cap: that
    number is Tyler's conduct policy, and a per-ask field may only ever lower
    the pressure on a person. See nudge_cap_of() for the c19 incident behind it.
    """
    now = now or _now()
    if priority not in PRIORITIES:
        raise ValueError(
            f"unknown priority {priority!r} - one of {', '.join(PRIORITIES)}. "
            "A free-form field would become an arms race; these three are a claim "
            "about the ASKER's state, which is a thing a session actually knows.")
    if nudge_cap is not None:
        nudge_cap = int(nudge_cap)
        if not 0 <= nudge_cap <= MAX_NUDGES:
            raise ValueError(
                f"nudge_cap must be between 0 and {MAX_NUDGES} - it can only "
                "LOWER the pressure on a person, never raise it above the "
                "proven global policy.")
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
            "asker_session": (str(asker_session) if asker_session else None),
            "nudge_cap": nudge_cap,
            "state": OPEN,
            "opened_at": _iso(now),
            "due_at": _iso(now + NUDGE_AFTER),
            "nudges": 0,
            "ask_message_ids": [],
            # Whether this question has ever been put in front of the person, as
            # its OWN fact. See was_delivered() for why it cannot be inferred from
            # ask_message_ids, which is what it was until 2026-08-17.
            "delivered_at": None,
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

    STRICTLY A BINDING RECORD, and nothing else. The batch message id lands here
    for every question that message displays, which is right - a reply to it may
    answer any of them - and it means this list says NOTHING about whether a
    particular question was the reason a message went out. It was read as a
    delivery flag until 2026-08-17 and the race that followed cost two questions
    their nudge budget within a second of being asked. was_delivered() is the
    delivery fact; this is the binding one.
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


def mark_delivered(cid, now=None):
    """This question is now in front of the person. Records it, and starts the clock.

    Was `restart_clock`, which named the side effect and hid the point. What
    matters is the FACT - see was_delivered() - and the clock restart follows from
    it: open() sets a deadline assuming the ask goes out promptly, which is
    usually true and occasionally very wrong. If the bot is down when a session
    asks, the conversation sits undelivered and its fifteen minutes elapse before
    the person has seen a word. Then the first thing they ever receive about it is
    a nudge.

    Called for EVERY question a delivery put on screen, not only the one whose job
    happened to run. One batch message shows the whole queue, so it delivers the
    whole queue - and a sibling left unmarked is one whose deadline is still
    counting from a moment it was invisible.
    """
    now = now or _now()

    def go(conv):
        conv["delivered_at"] = _iso(now)
        conv["due_at"] = _iso(now + NUDGE_AFTER)
        _event(conv, "delivered")
    return _mutate(cid, go)


def was_delivered(conv):
    """Has this question ever actually been shown to the person? Takes a conv dict.

    ITS OWN FACT, and that is the whole content of this function. Until 2026-08-17
    the test was `bool(conv["ask_message_ids"])` - and that list is stamped with
    the batch message id for EVERY question the message displays, because a reply
    to it may answer any of them. Correct for binding, and load-bearing for it.
    Disastrous as a delivery flag: when several asks were opened within a second,
    whichever advance job ran first marked all the others, so their own delivery
    jobs concluded the question had already been asked and sent a NUDGE instead -
    one second after it first appeared. Two facts sharing one field, and the
    quieter one lost.

    The ask_message_ids fallback is for records written before this field existed.
    It reads them exactly as the old code did, so a conversation mid-flight across
    the upgrade is not re-asked; the deadline check in advance_conversation is
    what keeps those from nudging early.
    """
    return bool(conv.get("delivered_at") or conv.get("ask_message_ids"))


def answerable(conv, now=None):
    """Can this conversation still take an answer? Takes a conv dict.

    LIVE, or banked inside BANK_GRACE. The second half is the whole point: the
    question a banked conversation asked is still on the person's screen, still
    unanswered, and still one this side actually wants the answer to - bank()
    keeps it for exactly that reason. Refusing an answer that arrives ninety
    seconds late does not protect anything; it just loses it.

    ANSWERED and CLOSED are not answerable, and that is different in kind rather
    than in timing. Those already have an answer or an outcome, and quietly
    replacing one would be the silent misfiling that slots exist to prevent.
    """
    state = conv.get("state")
    if state in LIVE_STATES:
        return True
    if state != BANKED:
        return False
    when = _parse(conv.get("closed_at"))
    return bool(when) and (now or _now()) - when <= BANK_GRACE


def recently_terminal(counterparty, within=None, now=None):
    """Conversations for this person that ended recently - probably still on screen.

    Everything else in this module is about questions that are WAITING. This is
    the opposite, and it exists because of what the gap did on 2026-08-17: the
    model is only ever handed live conversations, so when Tyler answered a
    question that had banked 75 seconds earlier, nothing in front of it said that
    question existed, let alone that it was dead. It said "Locked c11 in" and
    called nothing - which is INTENT.md §3.3 arriving through a new route.

    Not a fix for that - a model can still assert whatever it likes. It removes
    the reason: a true account of the thing he is plainly talking about is now
    available to it instead of absent.
    """
    within = within or STILL_ON_SCREEN
    now = now or _now()
    out = []
    for c in _load().values():
        if int(c.get("counterparty", 0)) != int(counterparty):
            continue
        if c.get("state") not in TERMINAL_STATES:
            continue
        when = _parse(c.get("closed_at"))
        if not when or now - when > within:
            continue
        out.append(c)
    return sorted(out, key=lambda c: c.get("closed_at") or "")


def beat_due(conv, now=None):
    """Is this conversation's next nudge or bank actually due? Takes a conv dict.

    THE SECOND NET UNDER THE RACE. The tick loop only ever advances what due()
    hands it, so this changed nothing there - but advance_conversation is also
    called directly the moment an ask is opened, to deliver it, and that path
    trusted the caller completely. When delivery was skipped for any reason the
    next branch was a nudge, unconditionally, however new the question was.

    Nothing should ever nudge someone about a question whose deadline has not
    arrived. Stating that here means it holds however the caller got here.
    """
    now = now or _now()
    deadline = _parse(conv.get("due_at"))
    return bool(deadline) and now >= deadline


def by_ask_message(message_id):
    """The live conversation whose question was carried by this message, or None.

    THE CERTAIN HALF OF BINDING. No model, no inference: Discord handed over a
    message reference, and this says which question it points at.
    """
    mid = int(message_id)
    for c in _load().values():
        if answerable(c) and mid in (c.get("ask_message_ids") or []):
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


def parse_slot_answers(text):
    """Split a reply that answers several numbered questions at once.

        "1. sqlite
2. drop it
3. looks fine"  ->  {1: "sqlite", 2: "drop it", ...}

    Returns {} when the text is not a numbered list, so a normal message is
    untouched.

    This exists because the first version did not, and the failure was total
    rather than partial. The batch message says "answer any of them by number",
    Tyler answered all three at once - the obvious thing to do with a numbered
    list - and a greedy `.+` bound slot 1 to the ENTIRE message: c7 swallowed the
    answers to c8 and c9, and both of those went on being nudged for questions he
    had already answered. The feature invited exactly the input it could not
    parse.

    Segments are cut at line starts only. A "2." mid-sentence is prose, and
    treating it as a slot marker would shred ordinary messages - the parse has to
    fail toward "this is not a list" rather than toward finding structure that
    is not there.
    """
    if not text:
        return {}
    marks = list(re.finditer(r"(?m)^[ 	]*#?(\d{1,2})[ 	]*[:.)\-][ 	]*", text))
    if len(marks) < 2:
        return {}
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if body:
            out[int(m.group(1))] = body
    return out


def answer_slots(counterparty, mapping, bound_by="slot"):
    """Answer several queued questions in one go. Returns [(slot, conv), ...].

    THE SLOTS ARE ALL RESOLVED BEFORE ANYTHING IS ANSWERED, which is the whole
    reason this is a function rather than a loop at the call site. Answering
    renumbers the queue, so resolving slot 2 *after* answering slot 1 would find a
    different conversation than the one the message showed him - silently filing
    his answer against the wrong question, which is the exact failure slots exist
    to prevent.

    Against shown_queue(), which is the same argument one message wider: within a
    single reply the resolve-first rule holds the numbering still, and between two
    replies only the screen does.
    """
    q = shown_queue(counterparty)
    resolved = []
    for slot, body in sorted(mapping.items()):
        if 1 <= slot <= len(q) and q[slot - 1] is not None:
            resolved.append((slot, q[slot - 1], body))
    done = []
    for slot, conv, body in resolved:
        answer(conv["id"], body, bound_by=bound_by)
        done.append((slot, conv))
    return done


def uncollected(counterparty=None):
    """Answers that came back and that nobody has acted on yet.

    ANSWERED-but-not-CLOSED already meant exactly this, so there is no new state:
    the answer is on the record and no session has turned it into an outcome.

    It needs surfacing because of a hole Tyler found on 2026-08-17: a session that
    asks with --no-wait, or that simply exits before he replies, leaves the answer
    sitting in the store with nothing to deliver it to. ask.py's docstring already
    said this - "it just gets read by whoever asks next" - but nothing ever showed
    it to whoever asked next, so "read by whoever asks next" was aspirational. The
    queue view is where a session already looks, so it is where these belong.
    """
    with _lock:
        out = [c for c in _load().values()
               if c.get("state") == ANSWERED
               and (counterparty is None
                    or int(c.get("counterparty", 0)) == int(counterparty))]
    return sorted(out, key=lambda c: c.get("answered_at") or "")


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
    """The conversation at this 1-based position ON HIS SCREEN, or None.

    Resolved against shown_queue() rather than the live queue, because the number
    he typed is the number he is looking at. See shown_queue for why that is not
    the same thing, and for the silent misfiling it prevents.
    """
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        return None
    q = shown_queue(counterparty)
    return q[slot - 1] if 1 <= slot <= len(q) else None


BATCHES = os.path.join(paths.STATE_DIR, "ask_batches.json")


def _batch_rec(counterparty):
    rec = jsonio.read_json(BATCHES, default={}).get(str(counterparty))
    if rec is None:
        return None
    # Pre-2026-08-17 the value was a bare message id. Read it rather than
    # discarding it: a live bot mid-upgrade has one of these on disk, and
    # dropping it would orphan the message currently on his screen.
    if isinstance(rec, int):
        return {"message": rec, "shown": []}
    return rec if isinstance(rec, dict) else None


def batch_message(counterparty):
    """The Discord message currently showing this person their queue, or None.

    Its own tiny file rather than a reserved key in the conversation store: that
    store is iterated as {id: conversation} in a dozen places, and a key that was
    not a conversation would need a guard at every one of them. One of those
    guards would eventually be missed.
    """
    rec = _batch_rec(counterparty)
    return int(rec["message"]) if rec and rec.get("message") else None


def set_batch_message(counterparty, message_id, shown=None):
    """Record which message is showing the queue, and WHICH QUESTIONS IT SHOWS.

    `shown` is the ordered list of conversation ids the message names as 1, 2,
    3... It is stored because the numbering he answers by is the numbering he can
    see, and the two drift apart the moment he answers one of them - see
    shown_queue().
    """
    data = jsonio.read_json(BATCHES, default={})
    if message_id is None:
        data.pop(str(counterparty), None)
    else:
        data[str(counterparty)] = {"message": int(message_id),
                                   "shown": [str(c) for c in (shown or [])]}
    jsonio.write_json(BATCHES, data)


def shown_queue(counterparty):
    """The queue as the message on his screen numbers it. Entries may be None.

    THE SCREEN IS THE NUMBERING, and this is the half of the slot design that was
    missing until 2026-08-18. Slots are recomputed rather than stored, which is
    right - but recomputing only keeps them honest while the message on screen
    still matches the queue, and NOTHING RE-RENDERS IT WHEN HE ANSWERS. So:

        screen:  1. which database?   2. drop the cache?   3. ready to deploy?
        he answers "1: sqlite"        -> live queue is now [drop, deploy]
        he answers "2: yeah drop it"  -> live slot 2 is READY TO DEPLOY

    which files an answer against the wrong question, silently, and is the exact
    failure the old one-live-ask rule existed to prevent. Resolving against what
    was shown gets slot 2 right, and gets a slot whose question has since been
    answered wrong in the only safe direction: None, so the caller falls through
    to the model, which has to say how it read the message.

    Positions are preserved rather than compacted - a None in the middle is the
    point. Compacting would renumber everything after it, which is the bug.
    """
    rec = _batch_rec(counterparty)
    live = {c["id"]: c for c in queue_for(counterparty)}
    if not rec or not rec.get("shown"):
        # Nothing has been delivered (or it predates the shown list), so the live
        # queue is the best available account of what he would be looking at.
        return list(live.values())
    data = _load()

    def at(cid):
        c = live.get(str(cid))
        if c is not None:
            return c
        # Not live any more - but a question that banked moments ago is still
        # printed on the message he is typing a number into, and inside the grace
        # window it can still take that answer. Resolving it to None here would
        # send a perfectly good "2: yes" to the model to guess about.
        c = data.get(str(cid))
        return c if c is not None and answerable(c) else None

    return [at(cid) for cid in rec["shown"]]


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
    """The FRONT of this person's queue, or None.

    Was "the one live conversation", and was the binding function, back when
    open() enforced one live ask per person. It does not any more (2026-08-17),
    so this is no longer a unique thing and no longer binds anything: it is the
    one to nudge about, and the one to hand a model as context. by_slot() and
    by_ask_message() are what bind now.
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
        if not beat_due(c, now):
            continue
        out.append((c, "nudge" if int(c.get("nudges", 0)) < nudge_cap_of(c) else "bank"))
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
        cap = nudge_cap_of(conv)
        if int(conv.get("nudges", 0)) >= cap:
            raise ValueError(f"{cid} has already had its {cap} nudge(s) - bank it")
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

    `bound_by` is kept because the routes differ in how much they can be trusted,
    and a later dispute over what someone answered should be able to see which one
    happened:

      "reply"  Discord carried a message reference to the ask. Certain, while the
               message it points at names one question.
      "slot"   he typed the number the message showed. Certain, resolved against
               that same screen - see shown_queue().
      "judged" the model decided this was the answer, and had to say so out loud
               in the same breath. An inference, recorded as one.
    """
    def go(conv):
        if not answerable(conv):
            raise ValueError(f"{cid} is {conv.get('state')}, not waiting on an answer")
        # Worth recording separately. A late answer is a real answer, but the
        # record should be able to say the deadline had passed when it arrived -
        # both because it is true, and because a run of them means the nudge
        # policy is tuned wrong rather than that people are slow.
        late = conv.get("state") == BANKED
        conv["state"] = ANSWERED
        conv["answer"] = str(text)
        conv["answered_at"] = _iso(_now())
        conv["due_at"] = None          # nothing is owed BY them any more
        _event(conv, "answered",
               f"via {bound_by}" + (" (after banking, inside the grace window)"
                                    if late else ""))
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
