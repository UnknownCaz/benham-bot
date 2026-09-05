"""
test_identity_frame.py - Benham is Benham on every surface, and something checks it.

INTENT.md decision #4 settles this: "always Benham. Never break frame; never
pretend to be human." Nothing enforced it anywhere, and on 2026-08-17 23:39:29 a
single prompt line spent the decision: Doom asked what Benham could do after the
refactor and was told "I'm the same Claude you'd use elsewhere". That was not
model drift. guest_persona.md line 14 said "You are Claude, reaching this person
through the Benham bot. Same Claude, different body." The model was doing as it
was told.

Why this earns a slot in the suite:

  A PROMPT IS NOT A COMMENT. It steers every reply on the surface it governs, and
  it fails silently - no exception, no red test, no symptom except a person being
  told something untrue. INTENT section 3.1 recorded this same defect in
  persona.md ("Everyone else is scenery you're polite to") and it survived three
  more weeks, because reading is the only thing that had ever caught it.

  THE FALLBACKS ARE THE COPY NOBODY RE-READS. agent.py and guest.py each carry a
  built-in persona for when the prompt file is missing or empty. Both had drifted
  the same way as the files they stand in for, and neither can drift loudly: that
  code runs only when a file is gone, which is the moment you are least likely to
  be reading prompt text.

  IDENTITY AND HONESTY PULL AGAINST EACH OTHER, so both halves are checked here.
  "Always say Benham" taken alone is one bad edit from a bot that will not admit
  what it is. The negative checks below have a positive twin every time: never
  pretend to be human, never deny being an AI.

WHAT THIS CANNOT DO, stated because this repo keeps relearning what an unchecked
claim costs. The positive checks are the strong half - every surface must assert
Benham, and a wholesale rewrite that drops it fails here. The negative list is
the phrasings this repo has ACTUALLY shipped plus their obvious variants: it
catches a regression to a known wording, and it will not catch a paraphrase
nobody has written yet. It is a tripwire on a walked path, not a proof.

    python test_identity_frame.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import sys
import tempfile

from benham import paths
from benham.core import agent
from benham.guest import guest

os.environ.setdefault("BOT_KEY", "test-token-not-used")

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# Every phrasing that hands out an identity other than Benham. The first four are
# not hypothetical - they were live in this tree, and the third is what Doom was
# read back to him. The rest are what the same edit produces in another voice.
BREAKS = [
    "you are claude",        # guest_persona.md:14, and guest.py's OSError fallback
    "you're claude",
    "same claude",           # "Same Claude, different body" -> what Doom was told
    "different body",
    "claude's presence",     # agent.py _DEFAULT_PERSONA, before this branch
    "i'm claude",
    "i am claude",
    "claude, reaching",
    "claude, reached",
]


def frame(label, text):
    """One prompt surface: it names Benham, and it names nobody else."""
    low = text.lower()
    check(f"{label}: says 'You are Benham'", "you are benham" in low, True)
    check(f"{label}: hands out no other identity",
          sorted(p for p in BREAKS if p in low), [])


def guest_prompt(persona_file):
    """The REAL guest system prompt, with PERSONA_FILE pointed where we want it.

    Asserting on the file's bytes would prove only that the file says Benham. What
    reaches the model is whatever _system_prompt() returns, and the gap between
    those two is where this repo's last several bugs lived.
    """
    real, cached = guest.PERSONA_FILE, guest._persona_cache
    try:
        guest.PERSONA_FILE = persona_file
        guest._persona_cache = None
        return guest._system_prompt()
    finally:
        guest.PERSONA_FILE, guest._persona_cache = real, cached


def owner_persona(persona_file):
    """Same idea, for the owner agent."""
    real = agent.PERSONA_FILE
    try:
        agent.PERSONA_FILE = persona_file
        return agent._persona()
    finally:
        agent.PERSONA_FILE = real


def main():
    missing = os.path.join(paths.PROMPTS_DIR, "no-such-persona-file.md")

    section("The guest surface - the one that got this wrong in front of a real person")
    live_guest = guest_prompt(guest.PERSONA_FILE)
    frame("guest_persona.md", live_guest)
    low = live_guest.lower()
    check("it still forbids pretending to be human",
          "pretend to be human" in low or "imply you\nare human" in low, True)
    check("it still forbids denying being an AI", "deny being an ai" in low, True)
    check("it still says there are no tools on this path",
          "no tools on this path" in low, True)

    section("The owner surface")
    live_owner = owner_persona(agent.PERSONA_FILE)
    frame("persona.md", live_owner)
    low = live_owner.lower()
    check("collaborators are no longer 'scenery'", "scenery" in low, False)
    # The real gate is identity.is_owner() plus agent.py's hard rules, both covered
    # by test_owner_gate. This is the persona echoing it, and it is worth echoing:
    # the same edit that widened "collaborators matter" could drop it by accident.
    check("direction still belongs to Tyler alone",
          "take direction from" in low, True)
    check("it still forbids pretending to be human",
          "pretend to be human" in low, True)

    # "The PC surface" (codesession._APPEND_PROMPT) stood here; the lane was
    # deleted in Phase B (INTENT 39).

    section("The fallbacks - the copy nobody re-reads")
    fallback_guest = guest_prompt(missing)
    frame("guest.py, prompt file MISSING", fallback_guest)
    check("...and it still says NO tools",
          "no tools on this path" in fallback_guest.lower(), True)
    check("...and it still refuses to pass as human",
          "pretend to be human" in fallback_guest.lower(), True)

    frame("agent.py, prompt file MISSING", owner_persona(missing))
    with tempfile.TemporaryDirectory() as d:
        empty = os.path.join(d, "persona.md")
        open(empty, "w").close()
        # _persona() falls back on an EMPTY file too, not only a missing one - a
        # separate branch, and the likelier accident of the two.
        frame("agent.py, prompt file EMPTY", owner_persona(empty))

    section("The handout - what a guest reads before ever sending a message")
    with open(os.path.join(paths.PROMPTS_DIR, "guest_guide.md"), encoding="utf-8") as f:
        guide = f.read().lower()
    check("guest_guide.md: names Benham in the opening", "i'm benham" in guide, True)
    check("guest_guide.md: hands out no other identity",
          sorted(p for p in BREAKS if p in guide), [])
    check("guest_guide.md: still says plainly that it is an AI",
          "i'm an ai" in guide, True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        print("INTENT.md decision #4: always Benham, one character, every surface.")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
