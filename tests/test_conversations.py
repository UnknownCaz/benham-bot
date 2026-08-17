"""
test_conversations.py - the ask survives the session that made it.

Stage 3's primitive. The checks that matter are the invariants, not the getters:

  ONE LIVE ASK PER PERSON, because that is what makes a reply bindable. If two
  can be open at once, "which question is this answering" has no code-level
  answer and the whole reverse channel rests on a guess.

  A DEFER ONLY EVER EXTENDS. An away signal is information about when to ask
  again; if it could move a deadline closer, "brb" would become a reason to
  pester someone sooner - the exact inversion of what Tyler's policy says.

  A CLOSE CARRIES AN OUTCOME. A silent close is the bug this entire stage exists
  to end: work that looks finished from the inside and reads as silence from the
  outside.

  TELLING IS NOT CLOSING. Two events, because a delivery failure must not be able
  to look like a shut loop.

Runs against a temporary store, never the real one.

    python test_conversations.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from benham.core import capabilities, conversations as C, identity, policy

DOOM = 1097631170788851815
TYLER = 273967061619965952
NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def main():
    tmp = tempfile.mkdtemp(prefix="benham-conv-")
    real_store = C.STORE
    try:
        C.STORE = os.path.join(tmp, "conversations.json")

        section("An ask is a thing, and it starts waiting")
        c = C.open_conversation(DOOM, "check the lore-button fix",
                                "does the lore button work now?", project="storyizier",
                                now=NOW)
        check("it has an id", bool(c["id"]), True)
        check("it starts open", c["state"], C.OPEN)
        check("nobody has been nudged yet", c["nudges"], 0)
        check("the project rode along", c["project"], "storyizier")
        check("and it logged the opening", c["log"][0]["event"], "opened")

        section("The queue - what replaced one-live-ask-per-person")
        # Until 2026-08-17 a second ask RAISED, because one live ask is what made a
        # reply bindable. Tyler's call: sessions queue instead, reading the queue
        # before placing themselves. The binding guarantee had to be replaced, not
        # dropped - that is what slot_of() is for, and it is the point of this
        # section. Losing it silently would have been the worst outcome here.
        second = C.open_conversation(DOOM, "something else", "unrelated question", now=NOW)
        check("a second ask now queues instead of raising", second["state"], C.OPEN)
        check("both are waiting on him", [x["id"] for x in C.queue_for(DOOM)],
              [c["id"], second["id"]])
        check("a different person has their own queue",
              C.open_conversation(TYLER, "which way", "A or B?", now=NOW)["state"], C.OPEN)
        check("live_for is now the FRONT of the queue", C.live_for(DOOM)["id"], c["id"])

        # Slots are the handle: "2: sqlite" has to be as certain as a Discord reply.
        check("front of the queue is slot 1", C.slot_of(c["id"]), 1)
        check("the second is slot 2", C.slot_of(second["id"]), 2)
        check("slots resolve back to the conversation",
              C.by_slot(DOOM, 2)["id"], second["id"])
        check("a slot past the end is None, not a wrap-around",
              C.by_slot(DOOM, 3), None)
        check("slot 0 is not the last item", C.by_slot(DOOM, 0), None)

        # Ordering: priority first, then strictly first-come. A later session
        # cannot overtake an earlier one by claiming the SAME level - only by
        # claiming a higher one, which is visible in Tyler's message.
        urgent = C.open_conversation(DOOM, "prod is down", "restart it?",
                                     now=NOW, priority=C.BLOCKING,
                                     placement_reason="cannot continue, deploy is wedged")
        check("blocking jumps the queue", C.slot_of(urgent["id"]), 1)
        check("...and pushes the others back", C.slot_of(c["id"]), 2)
        tie = C.open_conversation(DOOM, "later thing", "another normal one", now=NOW)
        check("equal priority does NOT overtake - first come, first served",
              C.slot_of(tie["id"]), 4)

        # Advisory but recorded. Nothing stops a session claiming the top; the
        # claim and its reasoning are simply on the record where Tyler sees them.
        check("the claim is stored", C.get(urgent["id"])["priority"], C.BLOCKING)
        check("so is the reason it gave",
              "deploy is wedged" in C.get(urgent["id"])["placement_reason"], True)
        check("and the opening log entry carries both",
              "blocking" in C.get(urgent["id"])["log"][0]["detail"], True)

        # A made-up level is refused rather than silently ranked last - an unknown
        # priority that sorted to the bottom would bury exactly the urgent thing
        # someone fat-fingered.
        try:
            C.open_conversation(DOOM, "x", "y", now=NOW, priority="URGENT!!")
            bad = False
        except ValueError:
            bad = True
        check("an unknown priority is refused, not ranked last", bad, True)

        # Slots are RECOMPUTED. A stored slot would go stale the moment anything
        # ahead of it was answered, and a stale number binds an answer to the
        # wrong question - which is the exact failure the old rule prevented.
        C.answer(urgent["id"], "yes restart", bound_by="reply")
        check("answering the front renumbers what is left", C.slot_of(c["id"]), 1)
        check("...and the answered one has no slot at all", C.slot_of(urgent["id"]), None)
        C.forget(tie["id"])
        C.forget(second["id"])

        section("Nudges: 15 minutes, twice, then bank")
        check("nothing is due yet", C.due(now=NOW), [])
        # Force the clock rather than the wall: a test that waits 15 real minutes
        # is a test nobody runs.
        C._mutate(c["id"], lambda cv: cv.__setitem__("due_at", C._iso(NOW - timedelta(minutes=1))))
        pending = C.due(now=NOW)
        check("now it is due", [(p[0]["id"], p[1]) for p in pending], [(c["id"], "nudge")])

        C.nudge(c["id"], now=NOW)
        c = C.get(c["id"])
        check("first nudge counted", c["nudges"], 1)
        check("state moved to nudged", c["state"], C.NUDGED)
        check("and the clock was reset, not left due",
              C.due(now=NOW), [])

        C._mutate(c["id"], lambda cv: cv.__setitem__("due_at", C._iso(NOW - timedelta(minutes=1))))
        C.nudge(c["id"], now=NOW)
        C._mutate(c["id"], lambda cv: cv.__setitem__("due_at", C._iso(NOW - timedelta(minutes=1))))
        check("after two nudges the next move is to bank, not nudge again",
              C.due(now=NOW)[0][1], "bank")
        try:
            C.nudge(c["id"], now=NOW)
            over = True
        except ValueError:
            over = False
        check("...and a third nudge is refused outright", over, False)

        section("A defer only ever extends")
        # A future deadline is the only thing a shortening defer can be tested
        # against. The first version of this set it in the PAST and then deferred
        # to now - which really is an extension, so the module was right and the
        # test was wrong.
        C._mutate(c["id"], lambda cv: cv.__setitem__("due_at", C._iso(NOW + timedelta(minutes=30))))
        far = C.get(c["id"])["due_at"]
        C.defer(c["id"], minutes=5, reason="said brb", now=NOW)
        check("a defer that would pull the deadline CLOSER changes nothing",
              C.get(c["id"])["due_at"], far)
        check("...and says so in the log",
              C.get(c["id"])["log"][-1]["event"], "defer-ignored")
        C.defer(c["id"], minutes=45, reason="mid-game", now=NOW)
        check("a real away-signal pushes it out",
              C.get(c["id"])["due_at"], C._iso(NOW + timedelta(minutes=45)))
        C.defer(c["id"], minutes=60 * 24, reason="gone for the day", now=NOW)
        check("but never further than the one-hour cap",
              C.get(c["id"])["due_at"], C._iso(NOW + C.MAX_DEFER))

        section("Answering binds, and records HOW it bound")
        C.answer(c["id"], "yeah it works now", bound_by="reply")
        c = C.get(c["id"])
        check("state is answered", c["state"], C.ANSWERED)
        check("their words are kept", c["answer"], "yeah it works now")
        check("nothing is owed by them any more", c["due_at"], None)
        check("how it was bound is on the record", c["log"][-1]["detail"], "via reply")
        check("an answered conversation is no longer live", C.live_for(DOOM), None)
        check("...so the person can be asked something new",
              C.open_conversation(DOOM, "next thing", "and the party vote?", now=NOW)["state"], C.OPEN)

        section("A close must carry an outcome")
        try:
            C.close(c["id"], "   ")
            silent = True
        except ValueError:
            silent = False
        check("a silent close is refused - that IS the bug", silent, False)
        C.close(c["id"], "fixed in 0f2438e")
        check("with an outcome it closes", C.get(c["id"])["state"], C.CLOSED)
        check("and the outcome is kept for the telling",
              C.get(c["id"])["outcome"], "fixed in 0f2438e")

        section("Telling them is a separate fact from closing")
        told = [e for e in C.get(c["id"])["log"] if e["event"] == "counterparty-told"]
        check("closing alone does NOT claim they were told", told, [])
        C.mark_told(c["id"], "fixed in 0f2438e")
        told = [e for e in C.get(c["id"])["log"] if e["event"] == "counterparty-told"]
        check("marking told is its own event", len(told), 1)

        section("Banking keeps the question")
        t = C.live_for(TYLER)
        C.bank(t["id"], reason="no answer after 2 nudges")
        t = C.get(t["id"])
        check("state is banked", t["state"], C.BANKED)
        check("the question survives", t["question"], "A or B?")
        check("banked is terminal, so the person is free again", C.live_for(TYLER), None)

        section("The default clock works too - not just the injected one")
        # Every other check in this file passes now=NOW, which is what makes the
        # nudge arithmetic testable. It also meant the DEFAULT path was never once
        # exercised, and it shipped broken: `now` had no fallback, so any real
        # caller got AttributeError on None. Green tests, dead feature. Third time
        # tonight a test was green about the wrong thing, and the only one that was
        # mine.
        C.forget()
        wall = C.open_conversation(DOOM, "no clock passed", "does the default work?")
        check("it opens without an injected clock", wall["state"], C.OPEN)
        check("opened_at is a real timestamp", bool(wall["opened_at"]), True)
        check("and a deadline was still set", bool(wall["due_at"]), True)
        C.forget()

        section("advance_conversation - the beat, driven through the real chokepoint")
        # Through capabilities.run, not the handler directly. The repo already
        # learned this the expensive way: a gate that is written but not wired is
        # indistinguishable, from a test, from one that works.
        sent = []

        class _Sent:
            """What discord's channel.send actually returns: a Message with an id.

            The first version of this stub returned None, and advance_conversation
            reads .id off it to make the nudge repliable - so the stub being looser
            than the real API hid a real code path. Same class of gap as a test that
            asserts against a helper instead of the live one.
            """
            _next = [7000]

            def __init__(self):
                self.id = _Sent._next[0]
                _Sent._next[0] += 1

        class _DM:
            def __init__(self, uid):
                self.uid = uid

            async def send(self, content=None, **kw):
                sent.append((self.uid, content))
                return _Sent()

        class _User:
            def __init__(self, uid):
                self.id = uid
                self.dm_channel = _DM(uid)

        class _Client:
            def get_user(self, uid):
                return _User(int(uid))

        C.forget()
        conv = C.open_conversation(DOOM, "check the fix", "does the lore button work?",
                                   now=NOW)

        async def advance(cid):
            res, _ = await capabilities.run(
                _Client(), lambda *a: None, "advance_conversation", {"id": cid},
                force=True, call_ctx=policy.CallContext.system())
            return res

        import asyncio
        r = asyncio.run(advance(conv["id"]))
        check("the first beat asks", r["status"], "asked")
        check("the question went to the counterparty", sent[-1][0], DOOM)
        check("...and quotes it verbatim rather than rephrasing it",
              "does the lore button work?" in sent[-1][1], True)
        check("the ask becomes repliable",
              len(C.get(conv["id"])["ask_message_ids"]), 1)

        asyncio.run(advance(conv["id"]))          # nudge 1
        asyncio.run(advance(conv["id"]))          # nudge 2
        check("both nudges were spent", C.get(conv["id"])["nudges"], 2)
        sent.clear()
        r = asyncio.run(advance(conv["id"]))      # budget spent -> bank
        check("once the budget is spent it banks", r["status"], "banked")
        check("state on disk agrees", C.get(conv["id"])["state"], C.BANKED)
        check("the owner is told a collaborator went quiet", r["owner_told"], True)
        owner_id = sorted(identity.OWNER_IDS)[0]
        check("...and that report went to the owner", sent[-1][0], owner_id)

        try:
            asyncio.run(advance(conv["id"]))
            advanced_dead = True
        except Exception:
            advanced_dead = False
        check("a banked conversation cannot be advanced again", advanced_dead, False)

        section("No third message when the owner is the one who went quiet")
        C.forget()
        sent.clear()
        own = C.open_conversation(owner_id, "which way", "A or B?", now=NOW)
        asyncio.run(advance(own["id"]))          # ask
        asyncio.run(advance(own["id"]))          # nudge 1
        asyncio.run(advance(own["id"]))          # nudge 2
        sent.clear()
        r = asyncio.run(advance(own["id"]))
        check("it still banks", r["status"], "banked")
        # He has had the question twice already; a third message telling him he did
        # not reply to himself is noise, and the banked question stays readable.
        check("but he is not told he failed to answer himself", r["owner_told"], False)
        check("...so nothing was sent", sent, [])
        C.forget()

        section("Beat zero: the question actually gets ASKED")
        # This was missing when the loop first shipped. A conversation was opened
        # and never delivered, so the first tick sent "still after this one when you
        # get a sec" about a question the person had never seen. Delivering is not a
        # nudge and must not spend the budget.
        C.forget()
        fresh = C.open_conversation(DOOM, "check the fix", "does the lore button work?",
                                    now=NOW)
        check("a new conversation has nothing delivered yet",
              C.get(fresh["id"])["ask_message_ids"], [])
        r = asyncio.run(advance(fresh["id"]))
        check("the first beat ASKS rather than nudging", r["status"], "asked")
        check("...and it does not read as a reminder",
              "still after this one" in (sent[-1][1] or ""), False)
        check("...and the question itself went out",
              "does the lore button work?" in sent[-1][1], True)
        check("delivering does NOT spend a nudge", C.get(fresh["id"])["nudges"], 0)
        check("the delivery is on the record",
              [e["event"] for e in C.get(fresh["id"])["log"]][-1], "delivered")
        r = asyncio.run(advance(fresh["id"]))
        check("only the SECOND beat is a nudge", r["status"], "nudged")
        C.forget()

        section("Binding: a reply is certain, everything else is judged")
        # Tyler's rule, 2026-08-16: "both, reply binds and the model judges and
        # tells me." The two halves are asserted separately because they are not
        # equally trustworthy, and the record has to say which one happened.
        C.forget()
        conv = C.open_conversation(DOOM, "check the fix", "does the lore button work?",
                                   now=NOW)
        C.record_ask_message(conv["id"], 5001)
        check("the ask message is remembered", C.get(conv["id"])["ask_message_ids"], [5001])
        check("a reply to it finds the question", C.by_ask_message(5001)["id"], conv["id"])
        check("a reply to some OTHER message finds nothing - not a fallback",
              C.by_ask_message(9999), None)

        # A nudge is answerable too. Replying to the message that just arrived is
        # the most natural way anyone answers.
        C.record_ask_message(conv["id"], 5002)
        check("a nudge is bindable as well as the original",
              C.by_ask_message(5002)["id"], conv["id"])

        C.answer(conv["id"], "yeah works now", bound_by="reply")
        check("the record says HOW it bound", C.get(conv["id"])["log"][-1]["detail"], "via reply")
        check("an answered question is no longer bindable", C.by_ask_message(5001), None)

        judged = C.open_conversation(TYLER, "which way", "A or B?", now=NOW)
        C.answer(judged["id"], "B, and restart the server after", bound_by="judged")
        check("a judged binding is recorded as judged, not laundered as certain",
              C.get(judged["id"])["log"][-1]["detail"], "via judged")
        C.forget()

        section("Direction: a report we OWE is not an ask we are waiting on")
        # Item 9. Doom filed three reports in two days. If a report counted as an
        # "ask", his second would have been refused by the one-live-per-person rule
        # and Benham would have nudged him about a bug HE reported.
        C.forget()
        r1 = C.open_conversation(DOOM, "report", "lore button 404s",
                                 direction=C.OWED, now=NOW)
        r2 = C.open_conversation(DOOM, "report", "spoke another language",
                                 direction=C.OWED, now=NOW)
        check("two reports from one person are both allowed",
              [r1["id"], r2["id"]], ["c1", "c2"])
        check("an owed report is NOT the live ask for binding", C.live_for(DOOM), None)
        C._mutate(r1["id"], lambda cv: cv.__setitem__("due_at", C._iso(NOW - timedelta(minutes=1))))
        check("...and it is never due for a nudge, however old", C.due(now=NOW), [])
        # An ask to the same person still works and is still capped.
        q = C.open_conversation(DOOM, "ask", "did that fix it?", now=NOW)
        check("an ask alongside reports is still found for binding",
              C.live_for(DOOM)["id"], q["id"])
        # A second ask queues (2026-08-17). The property that matters here is
        # unchanged and is the reason OWED is excluded from the queue at all:
        # however many asks pile up, a person's own REPORTS never join the line
        # and never become candidate answers to themselves.
        q2 = C.open_conversation(DOOM, "second ask", "and this?", now=NOW)
        check("a second ask joins the queue", [x["id"] for x in C.queue_for(DOOM)],
              [q["id"], q2["id"]])
        check("...and his own reports are still not in it",
              any(x["direction"] == C.OWED for x in C.queue_for(DOOM)), False)
        check("the reports are still there, just not queued",
              len(C.all_conversations(counterparty=DOOM)) > len(C.queue_for(DOOM)), True)
        C.forget()

        section("It outlives the process that opened it")
        kept = C.open_conversation(DOOM, "next thing", "and the party vote?", now=NOW)
        cid = kept["id"]
        # Every read goes to disk - there is no in-process cache to invalidate,
        # which is the property that lets a conversation outlive the session that
        # opened it. Asserting the file itself, not a getter that could be lying.
        raw = __import__("json").load(open(C.STORE, encoding="utf-8"))
        check("it is really on disk, not just in memory", raw[cid]["purpose"], "next thing")
        check("...with its log intact", len(raw[cid]["log"]) >= 1, True)
    finally:
        C.STORE = real_store
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
