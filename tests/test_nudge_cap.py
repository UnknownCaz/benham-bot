"""test_nudge_cap.py - a written nudge ceiling must bind the timer, not a board.

The incident (c19, 2026-08-22): both corkboards said "ONE nudge max, then bank"
- written precisely because Draco had been double-asked on 08-20 - and the bot
nudged twice anyway (18:28Z, 18:44Z). The ceiling lived in prose; the timer read
MAX_NUDGES. A per-conversation `nudge_cap` now lives ON the record the timer
reads, so a zero-pressure ask is zero-pressure because code enforces it.

The boundary that matters as much as the cap itself: the field may only ever
LOWER the pressure on a person. MAX_NUDGES is Tyler's conduct policy, proven
against a real human before it was code, and a per-ask field that could raise
it would quietly convert a global promise into a suggestion.
"""

import contextlib
import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _testconfig                 # noqa: F401,E402 - must precede every benham import

from benham.core import conversations  # noqa: E402
from benham.cli import ask, outreach   # noqa: E402

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def verdict_for(cid, now):
    """due()'s verdict for one conversation, or None if it is not due at all."""
    for c, what in conversations.due(now):
        if c["id"] == cid:
            return what
    return None


GUEST = _testconfig.GUEST_ID
T0 = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(minutes=16)      # past the first 15-minute deadline
MUCH_LATER = T0 + timedelta(minutes=40)  # past the post-nudge deadline too

section("the c19 shape: same history, capped and uncapped, different verdicts")
capped = conversations.open_conversation(GUEST, "p", "capped q?", now=T0,
                                         nudge_cap=1)
control = conversations.open_conversation(GUEST, "p", "uncapped q?", now=T0)
check("the cap is stored on the record", capped.get("nudge_cap"), 1)
check("no cap stores None, not a copy of the default - so retuning MAX_NUDGES "
      "later retunes old records too", control.get("nudge_cap"), None)

check("first beat: the capped ask still gets its one nudge",
      verdict_for(capped["id"], LATER), "nudge")
conversations.nudge(capped["id"], now=LATER)
conversations.nudge(control["id"], now=LATER)

check("second beat: the capped ask BANKS where the timer used to nudge again "
      "- this line is the c19 fix", verdict_for(capped["id"], MUCH_LATER), "bank")
check("...and the uncapped sibling with the identical history still nudges - "
      "default behaviour unchanged", verdict_for(control["id"], MUCH_LATER),
      "nudge")

try:
    conversations.nudge(capped["id"], now=MUCH_LATER)
    check("nudge() past the cap raises", "no error", "ValueError")
except ValueError:
    check("nudge() past the cap raises", "ValueError", "ValueError")

section("cap 0 - never nudge, bank at the deadline")
zero = conversations.open_conversation(GUEST, "p", "zero-pressure q?", now=T0,
                                       nudge_cap=0)
check("first beat is already the bank", verdict_for(zero["id"], LATER), "bank")

section("the cap can only lower pressure, never raise it")
try:
    conversations.open_conversation(GUEST, "p", "q?", now=T0,
                                    nudge_cap=conversations.MAX_NUDGES + 1)
    check("a cap above MAX_NUDGES is refused at open", "no error", "ValueError")
except ValueError:
    check("a cap above MAX_NUDGES is refused at open", "ValueError", "ValueError")
try:
    conversations.open_conversation(GUEST, "p", "q?", now=T0, nudge_cap=-1)
    check("a negative cap is refused at open", "no error", "ValueError")
except ValueError:
    check("a negative cap is refused at open", "ValueError", "ValueError")
check("a hand-edited record with cap 99 is clamped, not obeyed",
      conversations.nudge_cap_of({"nudge_cap": 99}), conversations.MAX_NUDGES)
check("a record with no field runs on the global policy",
      conversations.nudge_cap_of({}), conversations.MAX_NUDGES)

section("the CLI wires it through")
outreach.main([str(GUEST), "one poke only please?", "--nudge-cap", "1"])
_latest = max(conversations.all_conversations(), key=lambda c: c.get("seq", 0))
check("outreach --nudge-cap 1 lands on the record", _latest.get("nudge_cap"), 1)
_code = None
try:
    outreach.main([str(GUEST), "q?", "--nudge-cap", "9"])
except SystemExit as e:
    _code = e.code
check("outreach refuses a cap above the global policy (usage error)", _code, 2)

section("...and so does ask - c19's promise was made about a queue ask")
ask.main(["gentle one?", "--nudge-cap", "0", "--no-wait"])
_latest = max(conversations.all_conversations(), key=lambda c: c.get("seq", 0))
check("ask --nudge-cap 0 lands on the record (0, not None - zero-pressure is "
      "a real setting)", _latest.get("nudge_cap"), 0)
_code = None
try:
    ask.main(["q?", "--nudge-cap", "9", "--no-wait"])
except SystemExit as e:
    _code = e.code
check("ask refuses a cap above the global policy (usage error)", _code, 2)

section("c38: the line a session READS must agree with the timer")
# c19 in reverse. The cap reached the timer exactly as the section above proves,
# and then ask's timeout line said "the bot keeps nudging" to every caller
# regardless - so the tool was right and its own report was wrong. A capped ask
# is only zero-pressure to the person; a session that believes it still chases
# will either avoid the flag or tell Tyler he is being nudged when he is not.
_zero = {"nudge_cap": 0, "due_at": "2026-09-16T05:15:00+00:00"}
_open = {"due_at": "2026-09-16T05:15:00+00:00"}
check("cap 0 promises no nudge at all",
      "NEVER be nudged" in conversations.nudge_outlook(_zero), True)
check("...and says what happens instead, with the deadline",
      "banks at 05:15Z" in conversations.nudge_outlook(_zero), True)
check("an uncapped ask still reports the chase it really gets",
      conversations.nudge_outlook(_open),
      f"{conversations.MAX_NUDGES} of {conversations.MAX_NUDGES} nudge(s) left, "
      "next at 05:15Z")
check("a spent budget banks rather than claiming another nudge",
      "banks" in conversations.nudge_outlook(
          {"nudge_cap": 1, "nudges": 1, "due_at": "2026-09-16T05:31:00+00:00"}), True)
check("a direction that never chases says so, instead of a nudge count",
      conversations.nudge_outlook({"direction": conversations.UNPROMPTED}),
      "it never chases, so nothing will ask again")


def _said(fn, argv):
    """Everything one CLI call printed, both streams, as the caller reads it."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(argv)
        except SystemExit:
            pass
    return out.getvalue() + err.getvalue()


# The literal phrase from the c38 report, asserted against the commands that
# printed it. Worth pinning by its exact words: this defect was invisible to
# every behavioural test in this file, because the behaviour was already right.
_words = _said(ask.main, ["capped ask?", "--nudge-cap", "0", "--no-wait"])
check("ask --no-wait on a capped ask never claims a nudge is coming",
      "keeps nudging" in _words or "nudges it on its own" in _words, False)
check("...and tells the session what WILL happen", "NEVER be nudged" in _words, True)
_words = _said(ask.main, ["uncapped ask?", "--no-wait"])
check("ask --no-wait on an uncapped ask still reports its real budget",
      "nudge(s) left" in _words, True)
_words = _said(outreach.main, [str(GUEST), "capped poke?", "--nudge-cap", "0"])
check("outreach never overpromises a chase at a guest either - c19's own "
      "command, and the one that reaches a real person",
      "nudges it on its own" in _words, False)
check("...and says the question is banked, not chased",
      "NEVER be nudged" in _words, True)
_words = _said(outreach.main, [str(GUEST), "uncapped poke?"])
check("an uncapped outreach still says it will be nudged",
      "nudge(s) left" in _words, True)

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
