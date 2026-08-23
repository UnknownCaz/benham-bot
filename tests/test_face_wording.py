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

The rule, in four halves (3 and 4 joined 2026-08-23, closing the two surfaces
the first pass deliberately left out of scope):

  1. An error/summary string a capability emits must never hardcode a face
     name - edit_role's "this bot" is the voice (neutral, true from any
     process). Checked by AST, not grep: only string literals inside
     ActionError(...) calls and summary/detail/message values are scanned,
     so comments - which are out of this pin's scope - cannot
     false-positive it.
  2. The banner's PC line splits on the face: the primary prints byte-for-byte
     what it always printed (the primary-face law), a non-primary face says
     the lane is walled here, in the OFF-here voice of its neighbours.
  3. Registry descriptions (summaries and param descs) are MODEL-facing - the
     agent reads them as its tool definitions - so they take the same neutral
     "this bot" voice. Neutral rather than face-derived on purpose: the
     registry stays one static artifact (summaries are deliberately not
     fingerprinted, and the doc generators never read them, so per-face
     text would buy nothing and cost the registry its one shape). Checked
     LIVE against REGISTRY, not by AST, so a concatenated or f-string
     summary cannot hide. "Benhams-inbox" is exempt - a folder's name is a
     path fact, not a face self-description.
  4. Audit-log reasons are the one surface where "this bot" would be USELESS
     - Discord's audit log answers "who did this" - so they carry the acting
     face's NAME: capabilities.VIA, derived from paths.PROCESS_FACE exactly
     like issues.FILED_BY. The primary-face law holds because PROCESS_FACE
     is fixed per process: a benham process writes byte-for-byte the
     "via Benham" it always wrote (asserted here), and a real
     BENHAM_FACE=codex subprocess writes "via Codex" (asserted below, the
     test_process_face pattern). No reason= may hardcode a name again -
     pinned by AST over every reason keyword and "reason" dict value.

    python tests/test_face_wording.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import ast
import json
import os
import subprocess
import sys

from benham import bot
from benham.core import capabilities, codesession, identity, issues

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


# --- 3. registry descriptions: the model is never told the wrong name -------
# Live, not AST: REGISTRY is what agent.py actually compiles into tool
# definitions, so scanning it catches any construction of the string. The one
# exemption is the "Benhams-inbox" folder name in pc_task's summary - a real
# directory on disk, the same path from every face, and pc_task is
# machine-walled to the primary anyway.

_desc_offenders = []
for _name, _act in sorted(capabilities.REGISTRY.items()):
    for _label, _text in [(f"{_name}.summary", _act.summary)] + [
            (f"{_name}.params[{_pn}].desc", (_pv or {}).get("desc", ""))
            for _pn, _pv in _act.params.items()]:
        if "Benham" in str(_text).replace("Benhams-inbox", ""):
            _desc_offenders.append(_label)

check("no registry summary or param desc names the primary face",
      _desc_offenders, [])


# --- 4. audit reasons: the acting face's name, never a hardcoded one --------
# Two AST pins against regression plus the primary-face law live. The AST
# halves: no string constant anywhere in capabilities.py may contain
# "via Benham" (they all route through VIA now), and no reason= keyword or
# "reason" dict value may carry ANY literal naming a face - the next
# hardcoding does not get to dodge the first check by dropping the "via".

_via_literals = [(n.lineno, n.value) for n in ast.walk(_tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and "via Benham" in n.value]
check("capabilities.py holds no 'via Benham' literal", _via_literals, [])

_reason_offenders = []
for _node in ast.walk(_tree):
    if isinstance(_node, ast.Call):
        for _kw in _node.keywords:
            if _kw.arg == "reason":
                _reason_offenders += [(_kw.value.lineno, s)
                                      for s in _strings_in(_kw.value)
                                      if "Benham" in s]
    if isinstance(_node, ast.Dict):
        for _k, _v in zip(_node.keys, _node.values):
            if isinstance(_k, ast.Constant) and _k.value == "reason":
                _reason_offenders += [(_v.lineno, s) for s in _strings_in(_v)
                                      if "Benham" in s]
check("no reason= hardcodes a face name", _reason_offenders, [])

# The primary-face law, byte-for-byte: this test process runs with no
# BENHAM_FACE, so it IS a benham process, and the audit log must read exactly
# as it has since the day these capabilities were written.
check("a benham process still writes exactly 'via Benham'",
      capabilities.VIA, "via Benham")

# issues.py is the same class one store over: build_body's trailer signed
# "via Benham" while its own header already attributed through FILED_BY.
_ISSUES = os.path.join(os.path.dirname(_CAPS), "issues.py")
with open(_ISSUES, encoding="utf-8") as f:
    _issues_via = [(n.lineno, n.value)
                   for n in ast.walk(ast.parse(f.read()))
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and "via Benham" in n.value]
check("issues.py holds no 'via Benham' literal", _issues_via, [])

_body = issues.build_body("bug", "some report text")
check("an issue filed from a benham process is signed by Benham",
      ("Filed by Benham." in _body, "- filed via Benham " in _body),
      (True, True))


# --- 5. a real codex process writes the codex name ---------------------------
# The half the primary-face pins above cannot see: VIA and FILED_BY are set at
# import from PROCESS_FACE, so the codex value only exists in a codex process.
# Same shape as test_process_face's probe - scratch config and state via
# _testconfig, nothing live reachable.

_probe = r"""
import json, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import _testconfig  # scratch config/state first - nothing live is writable
from benham.core import capabilities, issues
print(json.dumps({"via": capabilities.VIA, "filed_by": issues.FILED_BY}))
"""

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_tests_dir)
_env = dict(os.environ, BENHAM_FACE="codex", PYTHONIOENCODING="utf-8")
_proc = subprocess.run([sys.executable, "-c", _probe, _tests_dir, _root],
                       capture_output=True, text=True, env=_env, timeout=120)
check("a BENHAM_FACE=codex interpreter comes up clean", _proc.returncode, 0)
if _proc.returncode != 0:
    print(_proc.stdout)
    print(_proc.stderr)
else:
    _got = json.loads(_proc.stdout)
    check("a codex process audit-logs as Codex", _got["via"], "via Codex")
    check("...and files issues as Codex", _got["filed_by"], "Codex")


print(f"\n{'FAIL' if _fails else 'OK'}: {len(_fails)} failures")
sys.exit(1 if _fails else 0)
