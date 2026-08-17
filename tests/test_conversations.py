"""
test_conversations.py - the ask survives the session that made it.

Stage 3's primitive. The checks that matter are the invariants, not the getters:

  THE SLOT IS THE BINDING HANDLE. There used to be one live ask per person, which
  made a reply bindable by having exactly one candidate. Asks queue now, so the
  numbering carries that weight instead: slots are recomputed, never stored, and
  a reply naming one binds with no model involved.

  ANSWERING SEVERAL AT ONCE WORKS, because a numbered list invites exactly that
  and the first version could not parse the input it asked for.

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
    real_batches = C.BATCHES
    try:
        C.STORE = os.path.join(tmp, "conversations.json")
        # BATCHES was NOT redirected until 2026-08-18, and it was not a
        # theoretical leak: the live state/ask_batches.json on Tyler's machine
        # held {doom: 7007, tyler: 7004} - stub message ids from _Sent below,
        # written by this file on every run. The real bot would then try to edit
        # message 7007 in a real DM. It fails safe (the fetch raises and it sends
        # a fresh one), which is exactly why nobody noticed for a day.
        C.BATCHES = os.path.join(tmp, "ask_batches.json")

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

        section("Answering several at once - the thing the numbered list invites")
        # Regression, 2026-08-17. Tyler answered all three of his queued questions
        # in one numbered message - the obvious response to a numbered list, and
        # exactly what the batch message asks for. A greedy `.+` bound slot 1 to
        # the WHOLE message: c7 swallowed the answers to c8 and c9, and both went
        # on being nudged for questions he had already answered.
        real = ("1. use the global one\n"
                "2. Item 14 is starting to seem redundant why do we even need it anymore?\n"
                "3. the callouts seem fine to me honestly.")
        got = C.parse_slot_answers(real)
        check("all three parse", sorted(got), [1, 2, 3])
        check("slot 1 stops at slot 2", "Item 14" in got[1], False)
        check("slot 2 is its own answer", got[2].startswith("Item 14"), True)
        check("slot 3 survives", got[3], "the callouts seem fine to me honestly.")

        # Must fail toward "not a list". Finding structure in prose would shred
        # ordinary messages, which is far worse than missing a numbered one.
        check("a mid-sentence number is prose, not a slot",
              C.parse_slot_answers("I think option 2. is better honestly"), {})
        check("normal chat parses as nothing",
              C.parse_slot_answers("hey what's up"), {})
        check("one number alone is left to the single-slot path",
              C.parse_slot_answers("1. sqlite"), {})
        check("dashes work as separators too",
              sorted(C.parse_slot_answers("1 - yes\n2 - no\n3 - maybe")), [1, 2, 3])

        # Slots are resolved BEFORE anything is answered. Answering renumbers the
        # queue, so resolving slot 2 after answering slot 1 would find a different
        # conversation than the message showed him.
        #
        # A counterparty of its own, so this section cannot disturb the fixtures
        # the later sections rely on. The first version called a bare C.forget()
        # and wiped them.
        SOMEONE = 999000111
        a1 = C.open_conversation(SOMEONE, "one", "first?", now=NOW)
        a2 = C.open_conversation(SOMEONE, "two", "second?", now=NOW)
        a3 = C.open_conversation(SOMEONE, "three", "third?", now=NOW)
        done = C.answer_slots(SOMEONE, {1: "AAA", 2: "BBB", 3: "CCC"})
        check("all three bound in one go", len(done), 3)
        check("slot 1 got its own answer", C.get(a1["id"])["answer"], "AAA")
        check("slot 2 did NOT get slot 1's", C.get(a2["id"])["answer"], "BBB")
        check("slot 3 landed on the right one", C.get(a3["id"])["answer"], "CCC")
        check("nothing is left waiting", C.queue_for(SOMEONE), [])
        # How it bound lives in the event log, not as a field - so the record can
        # never disagree with itself about which route was taken.
        check("and it is recorded as a slot binding, not judged",
              [e["detail"] for e in C.get(a2["id"])["log"]
               if e["event"] == "answered"], ["via slot"])

        # Answering a subset must leave the rest alone and renumbered.
        ELSE = 999000222
        b1 = C.open_conversation(ELSE, "one", "first?", now=NOW)
        b2 = C.open_conversation(ELSE, "two", "second?", now=NOW)
        b3 = C.open_conversation(ELSE, "three", "third?", now=NOW)
        C.answer_slots(ELSE, {2: "only this one"})
        check("the answered one is out of the queue",
              [x["id"] for x in C.queue_for(ELSE)], [b1["id"], b3["id"]])
        check("the untouched ones have no answer", C.get(b3["id"])["answer"], None)
        check("and the last one renumbers to slot 2", C.slot_of(b3["id"]), 2)
        # A slot past the end is ignored rather than wrapping onto something real.
        C.answer_slots(ELSE, {9: "nowhere"})
        check("an out-of-range slot binds nothing", C.get(b1["id"])["answer"], None)

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
        edits = []
        deleted = []

        class _Sent:
            """What discord's channel.send actually returns: a Message with an id.

            The first version of this stub returned None, and advance_conversation
            reads .id off it to make the nudge repliable - so the stub being looser
            than the real API hid a real code path. Same class of gap as a test that
            asserts against a helper instead of the live one.

            It also could not be fetched, edited or deleted, so the ENTIRE
            edit-in-place path was unreachable from the suite: every attempt threw
            AttributeError into a bare `except Exception` and fell through to
            sending a fresh message. The batched delivery looked tested and was
            not, which is how the silent-edit bug below survived.
            """
            _next = [7000]

            def __init__(self, ch, content):
                self.id = _Sent._next[0]
                _Sent._next[0] += 1
                self.channel, self.content = ch, content

            async def edit(self, content=None, **kw):
                self.content = content
                edits.append((self.channel.uid, self.id, content))

            async def delete(self):
                self.channel.store.pop(self.id, None)
                deleted.append((self.channel.uid, self.id))

        class _DM:
            """One channel object per user, kept across calls.

            The old stub built a fresh _DM every time get_user ran, so nothing sent
            in one beat existed in the next - which is precisely the state the
            batch message lives in.
            """
            _channels = {}

            def __init__(self, uid):
                self.uid = uid
                self.store = {}

            @classmethod
            def get(cls, uid):
                return cls._channels.setdefault(int(uid), cls(int(uid)))

            async def send(self, content=None, **kw):
                sent.append((self.uid, content))
                msg = _Sent(self, content)
                self.store[msg.id] = msg
                return msg

            async def fetch_message(self, mid):
                # Discord raises NotFound for a message that is gone. So does this,
                # and the caller catches it - if the stub returned None instead, the
                # None-check downstream would paper over a path that really throws.
                if int(mid) not in self.store:
                    raise LookupError(f"no message {mid}")
                return self.store[int(mid)]

        class _User:
            def __init__(self, uid):
                self.id = uid
                self.dm_channel = _DM.get(uid)

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

        def make_due(*convs):
            """Wind a deadline back so the next beat is genuinely due.

            advance_conversation refuses to nudge before the deadline (the second
            half of the 2026-08-17 race fix), so a test that wants the nudge has to
            say the fifteen minutes elapsed. It cannot simply pass a clock the way
            the rest of this file does: the beat runs through capabilities.run,
            which takes an action's parameters and not a time.

            Every nudge below used to be an immediate second call, and that is
            exactly the assumption the race exploited - "advance always does the
            next beat, right now" is what turned a sibling's delivery into a nudge
            one second later.
            """
            for cv in convs:
                C._mutate(cv["id"] if isinstance(cv, dict) else cv,
                          lambda c: c.__setitem__(
                              "due_at", C._iso(C._now() - timedelta(seconds=1))))

        r = asyncio.run(advance(conv["id"]))
        check("the first beat asks", r["status"], "asked")
        check("the question went to the counterparty", sent[-1][0], DOOM)
        check("...and quotes it verbatim rather than rephrasing it",
              "does the lore button work?" in sent[-1][1], True)
        check("the ask becomes repliable",
              len(C.get(conv["id"])["ask_message_ids"]), 1)

        make_due(conv); asyncio.run(advance(conv["id"]))          # nudge 1
        make_due(conv); asyncio.run(advance(conv["id"]))          # nudge 2
        check("both nudges were spent", C.get(conv["id"])["nudges"], 2)
        sent.clear()
        make_due(conv)
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
        make_due(own); asyncio.run(advance(own["id"]))          # nudge 1
        make_due(own); asyncio.run(advance(own["id"]))          # nudge 2
        sent.clear()
        make_due(own)
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
        # Clear the batch record too, so this section tests beat zero and nothing
        # else. C.forget() drops conversations but not the message showing them,
        # and the leftover sent this section down the edit path - which meant it
        # was silently also testing batch staleness, and blew up with an
        # IndexError instead of a diagnosis when that broke. One section, one
        # subject; staleness has its own below.
        C.set_batch_message(DOOM, None)
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
        check("...and as a FACT, not something to infer from the message list",
              bool(C.get(fresh["id"]).get("delivered_at")), True)
        # Immediately, before the deadline. This used to nudge, and that is half of
        # what made the race below possible.
        sent.clear()
        r = asyncio.run(advance(fresh["id"]))
        check("a beat that is not due yet does NOTHING", r["status"], "waiting")
        check("...and says nothing to him", sent, [])
        make_due(fresh)
        r = asyncio.run(advance(fresh["id"]))
        check("only the SECOND beat, once it is due, is a nudge", r["status"], "nudged")
        C.forget()

        section("Beat zero is PER QUESTION, not per message")
        # THE RACE, live in Tyler's DMs on 2026-08-17. Three sessions asked within
        # 300ms of each other. Whichever advance job ran first delivered the batch
        # and stamped its message id onto EVERY question the message showed - right
        # for binding, since a reply to that message may answer any of them. But
        # that same list was doubling as the "has this ever been delivered" flag, so
        # the other two found it non-empty, skipped beat zero, and NUDGED:
        #
        #   c11  07:38:42 opened -> 07:38:43 nudged #1
        #   c12  07:38:42 opened -> 07:38:43 delivered   (this one won the race)
        #   c13  07:38:42 opened -> 07:38:45 nudged #1
        #
        # MAX_NUDGES is 2, so c11 and c13 each burned one on a question that had
        # been on his screen for under a second, and their bank deadline arrived
        # ~15 minutes early. Both banked at 08:10:28. Tyler answered c11 at
        # 08:11:43 - 75 seconds too late to bind, and the answer was lost. c12,
        # which delivered correctly and kept its full budget, was still live at
        # 08:15:43 and bound fine. It is the control case.
        #
        # He also got "still after this one when you get a sec:" about questions he
        # had never been shown, which reads as nagging about nothing.
        RACE = 999000777
        C.forget()
        C.set_batch_message(RACE, None)
        sent.clear(); edits.clear(); deleted.clear()
        first = C.open_conversation(RACE, "db", "which database?", now=NOW)
        won = C.open_conversation(RACE, "cache", "drop the cache?", now=NOW)
        last = C.open_conversation(RACE, "deploy", "ready to deploy?", now=NOW)

        # The middle one's job happens to run first - as c12's did - and delivers
        # the batch on behalf of all three.
        r = asyncio.run(advance(won["id"]))
        check("whichever job runs first delivers", r["status"], "asked")
        check("...and the one message it sends shows all three", r["queued"], 3)
        check("...so all three questions really are on his screen",
              [q in sent[-1][1] for q in ("which database?", "drop the cache?",
                                          "ready to deploy?")], [True, True, True])

        # Now the other two run their own delivery jobs, a second later.
        r = asyncio.run(advance(first["id"]))
        check("a question the message already showed does NOT nudge",
              r["status"], "waiting")
        r = asyncio.run(advance(last["id"]))
        check("...and neither does the third", r["status"], "waiting")
        check("nobody spent a nudge on a question one second old",
              [C.get(x["id"])["nudges"] for x in (first, won, last)], [0, 0, 0])
        check("...so the full budget is intact and the bank deadline is not early",
              [C.get(x["id"])["state"] for x in (first, won, last)],
              [C.OPEN, C.OPEN, C.OPEN])
        check("...and he was told once, not three times", len(sent), 1)

        # The flag, and the clock. Delivery is a fact about a QUESTION, and this
        # message delivered all three - so all three are marked, and all three
        # start their fifteen minutes from now. Restarting only the winner's clock
        # is the same bug wearing a hat: if the bot were down when the asks were
        # made, the other two would arrive with their deadline already in the past
        # and nudge on the very next tick.
        check("every question the message showed is recorded as delivered",
              [bool(C.get(x["id"]).get("delivered_at")) for x in (first, won, last)],
              [True, True, True])
        # Asserted on the LOG, not on due_at. "the deadline is in the future" is
        # true of a nudged conversation too - nudging moves the clock as well - so
        # that check passed with the fix backed out, which is this file's own
        # recurring failure mode. The delivery event is the thing that can only
        # have one cause.
        check("...and every one of them starts its fifteen minutes from delivery",
              [[e["event"] for e in C.get(x["id"])["log"]].count("delivered")
               for x in (first, won, last)], [1, 1, 1])

        # And once the deadline really has passed, the nudge is not cancelled -
        # only deferred to the moment it was always meant to happen.
        sent.clear()
        make_due(first)
        r = asyncio.run(advance(first["id"]))
        check("the nudge still happens when it is genuinely due", r["status"], "nudged")
        check("...and it quotes the question rather than rephrasing it",
              "which database?" in sent[-1][1], True)
        C.forget()
        C.set_batch_message(RACE, None)

        section("The batch message - one numbered DM, edited in place")
        # The queue primitive was well covered from the day it shipped; the
        # DELIVERY of it was not covered at all, because the stub could not be
        # edited or fetched. Two real bugs were living in that gap.
        BATCH = 999000444
        C.forget()
        C.set_batch_message(BATCH, None)
        sent.clear(); edits.clear(); deleted.clear()

        one = C.open_conversation(BATCH, "db choice", "sqlite or json?", now=NOW)
        body = C.render_queue(BATCH)
        check("a queue of one is just the question - no list to number",
              body, "sqlite or json?")
        asyncio.run(advance(one["id"]))
        first_msg = C.batch_message(BATCH)
        check("it was sent, not edited into something", len(sent), 1)
        check("and the batch message is on record", bool(first_msg), True)
        check("what it shows is on record too", C.shown_queue(BATCH)[0]["id"], one["id"])

        # A question joining the BACK amends the list silently, which is right:
        # nothing at the top changed, and a Discord edit fires no notification.
        two = C.open_conversation(BATCH, "cache", "drop the cache?", now=NOW)
        asyncio.run(advance(two["id"]))
        check("a question joining the back edits in place", len(edits), 1)
        check("...rather than sending a second message", len(sent), 1)
        check("...and it is the SAME message, so one numbering exists",
              C.batch_message(BATCH), first_msg)
        body = edits[-1][2]
        check("now it is a numbered list", "**1.** sqlite or json?" in body, True)
        check("...with the second question as 2", "**2.** drop the cache?" in body, True)
        check("...and it says how to answer", "by number" in body, True)
        check("both questions bind to that one message",
              [C.by_ask_message(first_msg) is not None,
               first_msg in C.get(two["id"])["ask_message_ids"]], [True, True])

        # A BLOCKING question jumps the front, and an edit would deliver it
        # invisibly - the exact opposite of what claiming BLOCKING is for. So the
        # message is replaced, which notifies.
        sent.clear(); edits.clear()
        three = C.open_conversation(BATCH, "prod", "restart prod?", now=NOW,
                                    priority=C.BLOCKING,
                                    placement_reason="deploy is wedged")
        asyncio.run(advance(three["id"]))
        check("jumping the queue REPLACES the message so it notifies", len(sent), 1)
        check("...instead of amending it silently", len(edits), 0)
        check("...and the stale numbering is deleted, not left to contradict it",
              deleted[-1][1], first_msg)
        check("the batch message moved", C.batch_message(BATCH) != first_msg, True)
        check("the blocking one is now 1", "**1.** restart prod?" in sent[-1][1], True)
        check("...and says so, because self-assessment only works if he sees it",
              "blocking a session" in sent[-1][1], True)
        check("its stated reason rides along", "deploy is wedged" in sent[-1][1], True)

        section("A number means what it means ON HIS SCREEN")
        # The half that was missing. Slots are recomputed, which is right - but
        # recomputing is only honest while the message on screen matches the
        # queue, and NOTHING re-renders it when he answers. He answers 1, the live
        # queue renumbers underneath a list he can still read, and his next "2"
        # lands on what used to be 3.
        screen = [c["id"] for c in C.shown_queue(BATCH)]
        check("the screen shows blocking first, then the other two",
              screen, [three["id"], one["id"], two["id"]])
        C.answer_slots(BATCH, {1: "yes restart it"})
        check("slot 1 got its answer", C.get(three["id"])["answer"], "yes restart it")
        check("the LIVE queue has renumbered - this is the trap",
              C.slot_of(two["id"]), 2)
        # .get() rather than ["id"] so a regression here REPORTS. The first run of
        # this with the fix backed out bound slot 2 to c2 and slot 3 to nothing,
        # and the None then crashed the file - one diagnosis followed by a
        # traceback that hid the rest.
        def at(slot):
            hit = C.by_slot(BATCH, slot)
            return hit["id"] if hit else None
        check("but slot 2 still means the second thing he can SEE",
              at(2), one["id"])
        check("...and slot 3 still means the third", at(3), two["id"])
        check("a slot he already answered binds to nothing rather than to its "
              "neighbour", at(1), None)
        C.answer_slots(BATCH, {3: "yeah drop it"})
        check("answering by the on-screen number lands on the right question",
              C.get(two["id"])["answer"], "yeah drop it")
        check("...and did NOT touch the one still open", C.get(one["id"])["answer"], None)

        section("A lone question after a quiet spell still makes his phone buzz")
        # The regression, found 2026-08-18. The batch id is never cleared when a
        # queue empties, so the next single question found a stale message and took
        # the EDIT path - and an edit sends no notification. The first question
        # after a quiet spell is the one most likely to be urgent and was the one
        # guaranteed to arrive silently.
        C.answer(one["id"], "sqlite", bound_by="slot")
        check("the queue is empty but the message record survives",
              [C.queue_for(BATCH), bool(C.batch_message(BATCH))], [[], True])
        stale_msg = C.batch_message(BATCH)
        sent.clear(); edits.clear()
        lone = C.open_conversation(BATCH, "deploy", "ready to deploy?", now=NOW)
        asyncio.run(advance(lone["id"]))
        check("it is SENT, so he is actually notified", len(sent), 1)
        check("...and not quietly edited into the old list", len(edits), 0)
        check("the old message is gone rather than showing answered questions",
              deleted[-1][1], stale_msg)
        check("and the new message is the one that binds",
              C.by_ask_message(C.batch_message(BATCH))["id"], lone["id"])
        C.forget()
        C.set_batch_message(BATCH, None)

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
        C.BATCHES = real_batches
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
