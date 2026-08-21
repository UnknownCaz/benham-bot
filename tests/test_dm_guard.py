"""test_dm_guard.py - a raw dm may not put a question to a collaborator.

The bug this closes, in full: on 2026-08-20 six questions went to Draco as a raw
`dm`. A raw dm opens no conversation, so nothing tracked that an answer was
owed. He answered at 00:12Z. Four hours later a session with no record of that
answer sent him the same six questions again, and he repeated himself verbatim.
Twenty-odd sends to him across three days, not one on the rail: Doom had six
tracked conversations and Draco had zero. `benham.py outreach` opens one. It was
simply not the command that ran.

The heuristic is "contains a question mark", NOT "ends with one", and that
distinction is the whole test: the real message closed with "No rush on any of
it." and carried its questions in a numbered list above that.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham.cli import dm          # noqa: E402
from benham.core import identity   # noqa: E402

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


DRACO = 777000777000777001
DOOM = 777000777000777000
TYLER = 273967061619965952
STRANGER = 999000999000999000


def blocked(user_id, content):
    return dm._refuse_untracked_question(user_id, content) is not None


# The real message, reconstructed to its load-bearing shape: questions in the
# body, a non-question final line. Anything that only looks at the end misses it.
REAL = (
    "yo - Benham here, asking on Tyler's behalf.\n\n"
    "Six questions - answer what you care about.\n\n"
    "1. Where do you actually write?\n"
    "2. Do you ever actually open the repo?\n"
    "3. What would you WANT to do with it?\n\n"
    "No rush on any of it."
)

section("the heuristic - a question mark anywhere, not at the end")
check("the real message is caught", dm.asks_something(REAL), True)
check("...and 'ends with ?' would NOT have caught it - the reason for the rule",
      REAL.rstrip().endswith("?"), False)
check("a plain question is caught", dm.asks_something("you around?"), True)
check("a statement is not", dm.asks_something("roles granted, you're set"),
      False)
check("empty is not", dm.asks_something(""), False)
check("None does not crash", dm.asks_something(None), False)

section("who it applies to")
check("a guest being asked something is refused", blocked(DRACO, REAL), True)
check("...Doom too - this is not about one person",
      blocked(DOOM, "can you test the zoom and tell me if it works?"), True)
check("a guest being TOLD something goes through",
      blocked(DRACO, "granted you the roles, you should be all set now."),
      False)
check("Tyler is exempt - he has the ask queue, not the outreach rail",
      blocked(TYLER, "did the deploy finish?"), False)
check("a stranger is exempt - we do not put questions to them at all",
      blocked(STRANGER, "are you around?"), False)
check("identity.is_guest is what draws the line, not a list in this file",
      identity.is_guest(DRACO) and not identity.is_guest(TYLER), True)

section("the refusal earns its keep")
_msg = dm._refuse_untracked_question(DRACO, REAL)
check("it names the command that should have been used",
      "benham.py outreach" in _msg, True)
check("it says WHY, so the next session does not just retry harder",
      "opens no" in _msg and "conversation" in _msg, True)
check("it cites the incident, which is what stops this being folklore",
      "Draco" in _msg and "2026-08-20" in _msg, True)
check("it names its own escape hatch", "--untracked" in _msg, True)

section("the escape hatch is real - a guard nobody can pass is routed around")
check("a quoted question mark is a known false positive, and it IS blocked",
      blocked(DOOM, 'the sentence is "Buffalo buffalo?" - a grammar joke'),
      True)
# --untracked is parsed in main(), so the guard is simply not consulted. What is
# asserted here is that the refusal advertises the way out; main()'s flag
# handling is one line and reading it is cheaper than a subprocess test.
check("...and the refusal tells you how to send it anyway",
      "pass --untracked and it goes as-is" in _msg, True)

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
