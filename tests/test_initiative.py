"""test_initiative.py - the lane where Claude speaks first stays quiet by default.

The initiative lane exists so Claude can start a conversation Tyler did not ask
for: keep a follow-up it offered, or be curious about him on its own. That is a
genuinely new authority - a timer that may DM him - and the value of this build
is almost entirely in what it REFUSES. So that is what this file checks.

The failure this guards against is not an attacker. It is drift: a job told
"only speak when it matters", run daily for a year, eventually finding something
every time. Tyler mutes it inside a week and the channel is dead. Every rule
below is a piece of the design that does not depend on the model being in a good
mood, and each one is checked through the real policy function the bot calls.

  IT NEVER NUDGES AND NEVER TAKES A SLOT. An unprompted question is excluded
  from due() and from the numbered queue, so it cannot chase him and cannot
  crowd a session that is actually blocked.

  IT NEVER STACKS. One unanswered question at a time, a 48-hour floor between
  deliveries, and dormancy after two in a row go unanswered.

  IT NEVER ASKS FOR ACCESS. Deliberate, and a DENY rather than an omission, so
  that adding the feature back means deleting a rule and this test.

  ITS CAPABILITY CANNOT BE REPURPOSED. `deliver_unprompted` refuses anything
  that is not an unprompted conversation aimed at the owner - otherwise it would
  be a second, unqueued, unnudged way to push any conversation at anybody.
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from benham.core import capabilities, conversations as C, identity, initiative, policy

TYLER = 273967061619965952
DOOM = 777000777000777000

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def gate(text, now=None, counterparty=TYLER, direction=None):
    """The real chokepoint's verdict on a hypothetical question -> rule name or 'allow'."""
    conv = {"id": "(probe)", "question": text, "counterparty": counterparty,
            "direction": (direction if direction is not None else C.UNPROMPTED)}
    d = policy.authorize_unprompted(conv, now=now)
    return "allow" if d.allowed else d.rule


def deliver(cid):
    """Run the real capability far enough to see the gate's answer -> status string."""
    import asyncio

    class _Msg:
        id = 999888777

    class _Chan:
        async def send(self, *a, **k):
            return _Msg()

    class _User:
        dm_channel = _Chan()

    class _Ctx:
        async def user(self, _uid):
            return _User()

    act = capabilities.REGISTRY["deliver_unprompted"]
    try:
        res = asyncio.run(act.handler(_Ctx(), {"id": cid}))
        return res.get("status")
    except capabilities.ActionError as e:
        return f"refused: {str(e).split(']')[0].lstrip('[')}"


def main():
    tmp = tempfile.mkdtemp(prefix="benham-initiative-")
    real = (C.STORE, C.BATCHES, initiative.STORE, initiative.LOG_MD,
            identity.OWNER_IDS)
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    try:
        C.STORE = os.path.join(tmp, "conversations.json")
        C.BATCHES = os.path.join(tmp, "ask_batches.json")
        initiative.STORE = os.path.join(tmp, "initiative.json")
        initiative.LOG_MD = os.path.join(tmp, "initiative-log.md")
        identity.OWNER_IDS = {TYLER}

        section("The capability's declaration - the shape of the new authority")
        act = capabilities.REGISTRY["deliver_unprompted"]
        # NARROWER than advance_conversation, which is reachable from every owner
        # route. This one has no manual use: a human who wants to say something to
        # Tyler can just say it. Widening this set is how a timer that may ask him
        # one question turns into a general-purpose way to message him.
        check("reachable only from the CLI and from a timer",
              sorted(act.origins), ["local_cli", "system"])
        check("...declared outward, so taint rules apply to it", act.outward, True)
        check("...and it takes a conversation id and nothing else",
              sorted(act.params), ["id"])

        section("It asks. It does not report.")
        check("a plain question is allowed",
              gate("did you ever send Doom something with nothing attached to it?"),
              "allow")
        check("a status digest with no question in it is refused",
              gate("the wallpaper shipped and rooms v1 is verified."),
              "unprompted_is_a_question")
        check("...and so is a digest with a question stapled on the end",
              gate("x" * 520 + "?"), "unprompted_is_a_question")
        check("three questions is a survey, not a question",
              gate("did you text him? and did it land? and was it weird?"),
              "unprompted_is_a_question")
        check("two is still one thought, so two is allowed",
              gate("did you ever text him? or did it slip?"), "allow")

        section("It NEVER asks for access or capability - a decision, not a gap")
        # Tyler's constraint 5. The reasoning is in the rule's docstring and it is
        # worth restating where it will be read during a change: an ask like this
        # belongs in live conversation, where "no" costs one word and ends there.
        # Arriving unbidden on his phone the same words are an open item he has to
        # carry - and a channel Claude may open at will, which can also be used to
        # request more reach, is a channel that grows itself.
        for bad in ("can I use your webcam to check the zoom?",
                    "could I take a look at your Steam library?",
                    "would you mind if I read the OBS logs?",
                    "can you give me access to the vault?",
                    "let me watch the stream tonight?",
                    "am I allowed to open your files?"):
            check(f"refused: {bad[:44]}", gate(bad), "unprompted_no_escalation")

        section("No guilt framing, no wellness-check tone")
        for bad in ("just thinking of you - how did the wallpaper land?",
                    "hope you're okay - did the sync finish?",
                    "you still haven't answered - did it work?",
                    "checking in on you, did the build pass?"):
            check(f"refused: {bad[:44]}", gate(bad), "unprompted_no_guilt")

        section("It reaches Tyler and nobody else, ever")
        # A collaborator did not sign up to be thought about by a timer.
        check("an unprompted question aimed at Doom is refused",
              gate("did the wallpaper install cleanly?", counterparty=DOOM),
              "unprompted_owner_only")

        section("The capability cannot be repurposed")
        normal = C.open_conversation(TYLER, purpose="p", question="which db?",
                                     direction=C.ASKING)
        check("a normal queued ask cannot be pushed through this path",
              deliver(normal["id"]), "refused: unprompted_direction")
        check("...nor can a nonexistent one", deliver("c9999"),
              "refused: unprompted_direction")

        section("It never nudges and never takes a slot")
        q = initiative.open_question("did you ever text him with nothing attached?")
        # The two direction filters that were already there for OWED do all the
        # work; this is the check that they really do cover the new direction.
        check("it is not in the numbered queue Tyler answers by slot",
              [c["id"] for c in C.queue_for(TYLER)], [normal["id"]])
        check("...so it has no slot at all", C.slot_of(q["id"]), None)
        q = C.mark_delivered(q["id"])
        late = now + timedelta(days=30)
        check("...and it is never due for a nudge or a bank, however long it sits",
              [cid for (cid, _w) in [(c["id"], w) for c, w in C.due(now=late)]],
              [normal["id"]])

        section("One unanswered question at a time. Never stack.")
        check("a second question is refused while the first is unanswered",
              gate("something else entirely?"), "unprompted_one_at_a_time")
        # He answers it: the block clears, but the cadence floor takes over -
        # which is the point. Answering is not permission to ask again at once.
        C.answer(q["id"], "yeah i did actually")
        check("answering clears the block...", len(initiative.outstanding()), 0)
        check("...and the 48h floor is what holds the line after that",
              gate("and how did that go?"), "unprompted_cadence")
        check("...it clears once the floor has passed",
              gate("and how did that go?",
                   now=initiative._parse(q["delivered_at"]) + timedelta(hours=49)),
              "allow")

        section("An unanswered question LAPSES, silently")
        # Nothing is sent when one times out. "You never answered me" is the exact
        # guilt framing this lane is forbidden to carry, so the timeout is a state
        # change and not a message.
        q2 = initiative.open_question("did the eye-zoom read right on stream?")
        q2 = C.mark_delivered(q2["id"])
        stale = initiative._parse(q2["delivered_at"]) + timedelta(days=4)
        check("it stops counting as outstanding once the window passes",
              len(initiative.outstanding(now=stale)), 0)
        notes = initiative.sweep(now=stale)
        check("sweep writes that down", len(notes), 1)
        check("...as a bank, which keeps the question rather than deleting it",
              C.get(q2["id"])["state"], C.BANKED)

        section("Two lapses in a row and the lane goes dormant")
        q3 = initiative.open_question("did you ever hear back about the schedule?")
        q3 = C.mark_delivered(q3["id"])
        initiative.sweep(now=stale + timedelta(days=4))
        check("two consecutive lapses", initiative.consecutive_lapses(), 2)
        check("...and the lane closes itself", gate("anything at all?"),
              "unprompted_lane_quiet")
        check("...which status reports plainly rather than dying silently",
              initiative.lane_state()["dormant"], True)
        initiative.reset()
        check("a human reset clears the count", initiative.consecutive_lapses(), 0)

        section("The run log records silence, which is the whole point")
        # A run that decides nothing and logs nothing is indistinguishable from a
        # run that never happened - which is how the last scheduled task on this
        # machine died unnoticed for two weeks.
        initiative.record_run(initiative.R_SILENT, "nothing earned a message today.",
                              read=["9 corkboard boards", "3 threads"])
        text = initiative.read_markdown()
        check("the silent decision is in the human log", "silent" in text, True)
        check("...along with what the run actually read",
              "9 corkboard boards" in text, True)
        check("...and a blocked run is recorded as its own thing, not as silence",
              initiative.record_run(initiative.R_BLOCKED, "r", gate="x")["decision"],
              "blocked")

        section("Threads - the open loops that make a follow-up survive the session")
        t = initiative.add_thread("said I'd ask whether he texted Doom",
                                  why="he asked to be nudged to reach out",
                                  not_before="2026-08-22")
        check("a thread with a future not_before is not yet a candidate",
              [x["id"] for x in initiative.threads(state=initiative.T_OPEN,
                                                   askable_on="2026-08-21")], [])
        check("...and is one once the date arrives",
              [x["id"] for x in initiative.threads(state=initiative.T_OPEN,
                                                   askable_on="2026-08-23")], [t["id"]])
        initiative.drop_thread(t["id"], "he brought it up himself")
        check("dropping is a real outcome, distinct from closing",
              initiative.thread(t["id"])["state"], initiative.T_DROPPED)

    finally:
        (C.STORE, C.BATCHES, initiative.STORE, initiative.LOG_MD,
         identity.OWNER_IDS) = real
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(_fails) if _fails else 'all green'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
