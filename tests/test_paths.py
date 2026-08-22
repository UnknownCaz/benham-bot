"""
test_paths.py - the face-scoped roots resolve right, and the default face is a no-op.

Commit 1 of PLAN-second-face.md. The property the rest of that plan leans on:
state_for/prompts_for with the DEFAULT face return exactly STATE_DIR/PROMPTS_DIR
- not a subdirectory, not a normalised variant, the same string - so shipping
this commit changes no path anywhere. A named face gets faces/<name>/ under the
same roots, and a name that could escape those roots is unrepresentable rather
than filtered, because a face name reaches os.path.join.

    python test_paths.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# No _testconfig import: nothing here reads a gate or writes state - it is path
# arithmetic only, and the redirect seam is itself one of the checks below.

import os
import sys

from benham import paths

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


# --- the default face IS today's paths, byte for byte -----------------------

check("state_for() defaults to the default face",
      paths.state_for(), paths.state_for(paths.DEFAULT_FACE))
check("the default face is benham", paths.DEFAULT_FACE, "benham")
check("default face state root IS STATE_DIR - the no-behaviour-change property",
      paths.state_for("benham"), paths.STATE_DIR)
check("default face prompts root IS PROMPTS_DIR",
      paths.prompts_for("benham"), paths.PROMPTS_DIR)

# --- a named face lives under faces/<name>/ ---------------------------------

check("codex state root", paths.state_for("codex"),
      os.path.join(paths.STATE_DIR, "faces", "codex"))
check("codex prompts root", paths.prompts_for("codex"),
      os.path.join(paths.PROMPTS_DIR, "faces", "codex"))
check("kebab names are legal", paths.state_for("face-two"),
      os.path.join(paths.STATE_DIR, "faces", "face-two"))

# --- bad names are unrepresentable, not filtered ----------------------------
# "a/b", "a\\b" and ".." are the path-traversal cases; the rest hold the
# charset rule so a config typo fails loudly instead of minting a ghost face.

for bad in ("", "Codex", "a/b", "a\\b", "..", "a_b", "-codex", "9x",
            "a" * 41, None, 7):
    try:
        paths.state_for(bad)
    except ValueError:
        check(f"state_for rejects {bad!r}", True, True)
    else:
        check(f"state_for rejects {bad!r}", False, True)
    try:
        paths.prompts_for(bad)
    except ValueError:
        check(f"prompts_for rejects {bad!r}", True, True)
    else:
        check(f"prompts_for rejects {bad!r}", False, True)

# --- the redirect seam _testconfig depends on -------------------------------
# Every store resolves its path from paths.STATE_DIR late; these functions
# must do the same, or a redirected suite would write faces into live state -
# the exact bleed 08bc523 closed.

_real_state, _real_prompts = paths.STATE_DIR, paths.PROMPTS_DIR
try:
    paths.STATE_DIR = os.path.join("redirected", "state")
    paths.PROMPTS_DIR = os.path.join("redirected", "prompts")
    check("state_for follows a redirected STATE_DIR at call time",
          paths.state_for("codex"),
          os.path.join("redirected", "state", "faces", "codex"))
    check("...and the default face follows it too",
          paths.state_for(), os.path.join("redirected", "state"))
    check("prompts_for follows a redirected PROMPTS_DIR at call time",
          paths.prompts_for("codex"),
          os.path.join("redirected", "prompts", "faces", "codex"))
finally:
    paths.STATE_DIR, paths.PROMPTS_DIR = _real_state, _real_prompts

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
