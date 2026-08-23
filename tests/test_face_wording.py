"""
test_face_wording.py - no face-blind surface may name the primary face.

The defect class this pins (both instances observed live during the 2026-08-23
two-face launch): a string that hardcodes "Benham" on a surface every face
emits about ITSELF. set_channel_permissions refused the CODEX process with
"Benham lacks Manage Permissions in #authors", and the codex boot banner
printed "PC access: ON — workdir ..." when the machine wall makes the PC lane
unreachable for every non-primary face regardless of config. Both are
§3.3-class - a surface stating a false thing about itself - wearing the
faces build as the new costume.

The rule, in two halves:

  1. An error/summary string a capability emits must never hardcode a face
     name - edit_role's "this bot" is the voice (neutral, true from any
     process). Checked by AST, not grep: only string literals inside
     ActionError(...) calls and summary/detail/message values are scanned,
     so descriptions, audit reasons ("via Benham") and comments - which are
     out of this pin's scope - cannot false-positive it.
  2. The banner's PC line splits on the face: the primary prints byte-for-byte
     what it always printed (the primary-face law), a non-primary face says
     the lane is walled here, in the OFF-here voice of its neighbours.

    python tests/test_face_wording.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import ast
import os
import sys

from benham import bot
from benham.core import codesession, identity

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


# --- 1. capabilities.py: no face name in error/summary strings --------------

_CAPS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "benham", "core", "capabilities.py")
with open(_CAPS, encoding="utf-8") as f:
    _tree = ast.parse(f.read())


def _strings_in(node):
    """Every literal string fragment inside an expression, f-strings included."""
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


_offenders = []
for _node in ast.walk(_tree):
    if isinstance(_node, ast.Call):
        _fn = _node.func
        if getattr(_fn, "id", getattr(_fn, "attr", "")) == "ActionError":
            _offenders += [(_node.lineno, s) for s in _strings_in(_node)
                           if "Benham" in s]
    if isinstance(_node, ast.Dict):
        for _k, _v in zip(_node.keys, _node.values):
            if isinstance(_k, ast.Constant) and _k.value in ("summary", "detail",
                                                             "message"):
                _offenders += [(_v.lineno, s) for s in _strings_in(_v)
                               if "Benham" in s]

check("no ActionError or summary string hardcodes the primary face's name",
      _offenders, [])


# --- 2. the banner's PC line splits on the face -----------------------------
# The module attributes are the seam codesession itself exposes; each test
# file is its own process, so poking them leaks nowhere.

codesession.ENABLED = True
codesession.WORKDIR = r"C:\somewhere"
codesession.PERMISSION_TIMEOUT = 120

check("primary + enabled: byte-identical to the pre-faces line",
      bot._pc_access_line(identity.PRIMARY_FACE),
      "PC access: ON — workdir C:\\somewhere, writes/commands ask (timeout 120s)")

# The live bug's exact shape: config says ON, the process is not the primary.
check("non-primary + enabled: the wall is stated, config notwithstanding",
      bot._pc_access_line("codex"),
      "PC access: OFF here (machine wall) - the benham process runs it")

codesession.ENABLED = False
check("primary + disabled: byte-identical to the pre-faces line",
      bot._pc_access_line(identity.PRIMARY_FACE), "PC access: OFF")


print(f"\n{'FAIL' if _fails else 'OK'}: {len(_fails)} failures")
sys.exit(1 if _fails else 0)
