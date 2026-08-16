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

from benham.core import conversations as C

DOOM = 777000777000777000
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

        section("One live ask per person - the binding invariant")
        try:
            C.open_conversation(DOOM, "something else", "unrelated question", now=NOW)
            raised = False
        except ValueError as e:
            raised = True
            check("...and it names the conversation already in flight", c["id"] in str(e), True)
        check("a second live ask to the same person is refused", raised, True)
        check("a different person is fine",
              C.open_conversation(TYLER, "which way", "A or B?", now=NOW)["state"], C.OPEN)
        check("live_for finds the right one", C.live_for(DOOM)["id"], c["id"])

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
