"""
test_faces.py - identity.py's two config shapes, and the fail-closed rules between them.

Commit 2 of PLAN-second-face.md. The defect class this file guards: a config
shape change that quietly widens a gate. The legacy no-`faces` shape must parse
byte-identically to what every gate test already asserts (those files keep
covering that side); this file covers the NEW shape and the seams between the
two - the four fail-closed rules from the spike:

  1. a declared face with no owner_ids gets [], never the global list
  2. absent guild lists are empty; absent post_guilds DENIES for a declared
     face (the deliberate divergence from legacy allow-everything)
  3. a face naming an unset token env refuses to boot that face, in words,
     without raising
  4. `faces` + top-level owner_ids refuses to boot the process

Reloads identity against temp configs, so it must restore the fixture and
reload once more before exiting - the last reload is itself a check.

    python test_faces.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import importlib
import json
import os
import shutil
import sys
import tempfile

from benham import paths
from benham.core import identity

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


_FIXTURE_CONFIG_DIR = paths.CONFIG_DIR
_SCRATCH = tempfile.mkdtemp(prefix="benham-faces-test-")


def reload_with(cfg):
    """Reload identity against a config dict written to a scratch control.json."""
    with open(os.path.join(_SCRATCH, "control.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    paths.CONFIG_DIR = _SCRATCH
    return importlib.reload(identity)


def reload_refuses(label, cfg):
    """Assert a reload against cfg raises ControlFileError (checked by name -
    the class object changes identity across reloads)."""
    try:
        reload_with(cfg)
    except Exception as e:
        check(label, type(e).__name__, "ControlFileError")
    else:
        check(label, "booted", "ControlFileError")


TYLER = 273967061619965952
OTHER = 111222333444555666
G1, G2 = 1000000000000000001, 1000000000000000002
CH = 2000000000000000001


# --- the legacy shape: one implicit face, identical to the flat globals -----
# The fixture _testconfig provides is the legacy shape, so the module as first
# imported IS the legacy parse. Its face view must agree with its globals.

check("legacy: no faces declared", identity.FACES_DECLARED, False)
check("legacy: one face, the primary", identity.face_names(), ["benham"])
g = identity.face_gates("benham")
check("legacy: face owner_ids IS the global set", g["owner_ids"], identity.OWNER_IDS)
check("legacy: face agent_guilds IS the global set", g["agent_guilds"], identity.AGENT_GUILDS)
check("legacy: face destructive_guilds IS the global set",
      g["destructive_guilds"], identity.DESTRUCTIVE_GUILDS)
check("legacy: guest ids flow from the face view",
      set(identity.people_map(g["guest"].get("ids")).values()), identity.GUEST_IDS)
check("legacy: token env defaults to BOT_KEY", g["token_env"], "BOT_KEY")
check("legacy: the primary face is unconfined (capabilities None)",
      identity.face_capabilities(), None)

# --- legacy: explicit empty post_guilds still means the cap was never set ---
# Today `"post_guilds": []` is falsy and allows everything. The declared-face
# deny below must NOT leak back into this shape - an unmigrated control.json
# that happens to say [] would otherwise go silent on every guild at once.

m = reload_with({"owner_ids": [TYLER], "post_guilds": []})
check("legacy: post_guilds [] allows guild posting (cap unset)",
      m.posting_allowed(G1, CH), True)
check("legacy: post_guilds [] normalizes to None in the gate view",
      m.face_gates("benham")["post_guilds"], None)

# --- the faces shape --------------------------------------------------------

m = reload_with({
    "faces": {
        "benham": {
            "owner_ids": [TYLER],
            "agent_guilds": [G1],
            "post_guilds": [G1],
            "destructive_guilds": [G1],
            "guest": {"enabled": True, "ids": {"friend": OTHER}},
        },
        "codex": {
            "token_env": "CODEX_KEY",
            "post_guilds": [G2],
        },
    },
    "shared": {"issues": {"repo": "example/issues", "issuers": {"friend": OTHER}}},
})

check("faces: declared", m.FACES_DECLARED, True)
check("faces: primary face first in the listing", m.face_names(), ["benham", "codex"])
check("faces: globals track the primary face's block", m.OWNER_IDS, {TYLER})
check("faces: agent guilds from the primary block", m.AGENT_GUILDS, {G1})
check("faces: guest ids from the primary block", m.GUEST_IDS, {OTHER})
check("faces: is_owner still answers for the primary", m.is_owner(TYLER), True)
check("faces: ISSUES resolves from the shared block",
      m.ISSUES.get("repo"), "example/issues")

# rule 1: no owner_ids on a declared face means NOBODY, never the global list
check("rule 1: codex with no owner_ids obeys nobody",
      m.face_gates("codex")["owner_ids"], set())

# rule 2: absent guild lists are empty for a declared face
check("rule 2: codex absent agent_guilds is empty",
      m.face_gates("codex")["agent_guilds"], set())
check("rule 2: codex absent destructive_guilds is empty",
      m.face_gates("codex")["destructive_guilds"], set())

# capabilities carry the OTHER asymmetric default (commit 4): the primary is
# unconfined unless its block says otherwise - migrating the config shape must
# not narrow the live bot - while a new face reaches nothing until granted.
check("capabilities: benham-under-faces with no list stays unconfined",
      m.face_capabilities("benham"), None)
check("capabilities: codex with no list is EMPTY, not unconfined",
      m.face_capabilities("codex"), frozenset())
check("capabilities: an undeclared face is empty too",
      m.face_capabilities("ghost"), frozenset())

# rule 3: token problems are reported in words, never raised. These run HERE,
# while the two-face config is still the loaded one - reload() mutates the
# module in place, so `m` is not a snapshot and order is load-bearing.
check("rule 3: unset token env named in the answer",
      m.face_boot_problem("codex", environ={}),
      "face 'codex' names token env 'CODEX_KEY', which is not set")
check("rule 3: a set token env answers None",
      m.face_boot_problem("codex", environ={"CODEX_KEY": "x"}), None)
check("rule 3: an unknown face is a sentence, not a KeyError",
      "unknown face" in (m.face_boot_problem("ghost", environ={}) or ""), True)

# rule 2, the divergence: absent post_guilds DENIES for a declared face
m2 = reload_with({"faces": {"benham": {"owner_ids": [TYLER]}}})
check("rule 2: declared face, absent post_guilds -> guild posting denied",
      m2.posting_allowed(G1, CH), False)
check("rule 2: ...but a DM is still allowed (taint governs those)",
      m2.posting_allowed(None, CH), True)
check("rule 2: the gate view says set(), not None",
      m2.face_gates("benham")["post_guilds"], set())

# posting scope under the faces shape behaves per the primary block
m3 = reload_with({"faces": {"benham": {"owner_ids": [TYLER], "post_guilds": [G1]}}})
check("faces: posting allowed inside the declared scope", m3.posting_allowed(G1, CH), True)
check("faces: posting denied outside it", m3.posting_allowed(G2, CH), False)

# a faces block that omits the primary: restrictive-empty, never inherited
m4 = reload_with({"faces": {"codex": {"token_env": "CODEX_KEY"}}})
check("primary omitted: obeys nobody", m4.OWNER_IDS, set())
check("primary omitted: guild posting denied", m4.posting_allowed(G1, CH), False)
check("primary omitted: boot problem says so in words",
      "unknown face" in (m4.face_boot_problem("benham", environ={}) or ""), True)

# rule 4 and the malformed shapes: refuse to boot, never guess
reload_refuses("rule 4: faces + top-level owner_ids refuses to boot",
               {"faces": {"benham": {}}, "owner_ids": [TYLER]})
reload_refuses("malformed: faces as a list refuses to boot",
               {"faces": [{"name": "benham"}]})
reload_refuses("malformed: empty faces object refuses to boot", {"faces": {}})
reload_refuses("malformed: a face name with a path in it refuses to boot",
               {"faces": {"a/b": {}}})
reload_refuses("malformed: an uppercase face name refuses to boot",
               {"faces": {"Codex": {}}})
reload_refuses("malformed: a face block that is not an object refuses to boot",
               {"faces": {"benham": "yes"}})

# --- restore the fixture and prove the module comes back whole --------------

paths.CONFIG_DIR = _FIXTURE_CONFIG_DIR
final = importlib.reload(identity)
check("fixture restored: legacy shape again", final.FACES_DECLARED, False)
check("fixture restored: owners parse again", len(final.OWNER_IDS) > 0, True)
shutil.rmtree(_SCRATCH, ignore_errors=True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
