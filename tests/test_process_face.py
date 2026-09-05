"""
test_process_face.py - an unqualified call site answers as the PROCESS face.

The defect class this pins (found at commit 12, the first moment a second
process could exist): commits 3-4 built face-aware policy, and every live
CallContext mint site in bot.py minted without a face. The default resolved
None to the PRIMARY face, so in a codex process every capability call would
have authorized as benham - the unconfined face. rule_face_capability and the
machine wall would never have fired in the one process they exist for, and
the moment benham's scopes dropped a guild, codex would have been refused in
its own guild. Same class one layer down: identity's face=None defaults
(is_guest, guest_enabled, guest_config) consulted the primary's rosters, so
codex would have answered DMs from benham's guest list.

The fix is the doctrine benham.py states for the CLI: BENHAM_FACE is the
mechanism, everything resolves through paths.PROCESS_FACE. CallContext.face
and identity's face=None now resolve there too. Two halves asserted:

  1. In THIS process (no BENHAM_FACE): PROCESS_FACE is the primary, so every
     default answers exactly as it always did - the byte-identical pin.
  2. In a real BENHAM_FACE=codex subprocess against a faces-shape config:
     an unqualified CallContext carries codex, the machine wall fires on the
     bare mint (the exact shape of bot.py's live sites), the grant table
     confines, and the rosters split.

    python tests/test_process_face.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import json
import os
import subprocess
import sys

from benham import paths
from benham.core import identity, policy

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


# --- this process (no BENHAM_FACE): the primary answers, byte-identical -----

check("no BENHAM_FACE means the primary face", paths.PROCESS_FACE, "benham")
check("a bare CallContext carries the primary face",
      policy.CallContext.local(1).face, identity.PRIMARY_FACE)
check("every constructor agrees",
      {policy.CallContext.owner_dm(1).face, policy.CallContext.system().face,
       policy.CallContext.guest_dm(2).face}, {identity.PRIMARY_FACE})
check("guest_config() is the primary block", identity.guest_config(),
      dict(identity.GUEST))
check("_is_primary(None) is True here", identity._is_primary(None), True)


# --- a real codex process: the same unqualified calls answer as codex -------
# The subprocess writes its own faces-shape control.json (invented ids in the
# test_faces style) and imports identity fresh against it, so nothing here
# touches the fixture the rest of the suite reads - and nothing live.

_probe = r"""
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import _testconfig  # scratch state first - nothing live is writable from here
from benham import paths

TYLER = 273967061619965952
BGUEST, CGUEST = 111222333444555666, 111222333444555777
G_BEN, G_COD = 1000000000000000001, 1000000000000000002

scratch = tempfile.mkdtemp(prefix="benham-procface-")
with open(os.path.join(scratch, "control.json"), "w", encoding="utf-8") as f:
    json.dump({
        "faces": {
            "benham": {
                "owner_ids": [TYLER],
                "agent_guilds": [G_BEN], "destructive_guilds": [G_BEN],
                "post_guilds": [G_BEN],
                "guest": {"enabled": True, "ids": [BGUEST]},
            },
            "codex": {
                "token_env": "CODEX_KEY_TEST",
                "owner_ids": [TYLER],
                "agent_guilds": [G_COD], "destructive_guilds": [G_COD],
                "post_guilds": [G_COD],
                "guest": {"enabled": True, "ids": [CGUEST]},
                "capabilities": ["send_message", "read_channel"],
                "initiative": {"min_gap_hours": 99},
            },
        },
        "shared": {},
    }, f)
paths.CONFIG_DIR = scratch

from benham.core import identity, policy, capabilities

# The mint shapes below are EXACTLY bot.py's live ones: no face= anywhere.
loc = policy.CallContext.local(TYLER)
dm = policy.CallContext.owner_dm(TYLER, 42)
_testconfig.walled_pc_task()   # the lane is gone (Phase B); the wall is not
wall = policy.authorize(capabilities.REGISTRY["pc_task"], loc)
granted = policy.authorize(capabilities.REGISTRY["send_message"], dm)
ungranted = policy.authorize(capabilities.REGISTRY["create_channel"], dm)
print(json.dumps({
    "process_face": paths.PROCESS_FACE,
    "bare_local_face": loc.face,
    "bare_dm_face": dm.face,
    "taint_copy_keeps_face": dm.with_taint(True).face,
    "wall_denied": wall.denied, "wall_rule": wall.rule,
    "granted_allowed": not granted.denied,
    "ungranted_denied": ungranted.denied, "ungranted_rule": ungranted.rule,
    "codex_guest_is_guest": identity.is_guest(CGUEST),
    "benham_guest_is_stranger_here": identity.is_guest(BGUEST),
    "owner_still_owner": identity.is_owner(TYLER),
    "guest_ids": sorted(identity.people_map(
        identity.guest_config().get("ids")).values()),
    "initiative_gap": identity.initiative_config().get("min_gap_hours"),
    "posts_own_guild": identity.posting_allowed(G_COD, 7),
    "posts_benhams_guild": identity.posting_allowed(G_BEN, 7),
}))
"""

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_tests_dir)
env = dict(os.environ, BENHAM_FACE="codex", PYTHONIOENCODING="utf-8")
proc = subprocess.run([sys.executable, "-c", _probe, _tests_dir, _root],
                      capture_output=True, text=True, env=env, timeout=120)
check("a BENHAM_FACE=codex interpreter comes up clean", proc.returncode, 0)
if proc.returncode != 0:
    print(proc.stdout)
    print(proc.stderr)
else:
    got = json.loads(proc.stdout)
    check("codex process knows its face", got["process_face"], "codex")
    check("a BARE CallContext.local carries codex - the live mint shape",
          got["bare_local_face"], "codex")
    check("a BARE CallContext.owner_dm carries codex too",
          got["bare_dm_face"], "codex")
    check("with_taint copies keep the face", got["taint_copy_keeps_face"], "codex")
    check("the machine wall fires on the bare mint", got["wall_denied"], True)
    check("...as face_machine, config irrelevant", got["wall_rule"], "face_machine")
    check("a granted capability passes from the bare mint",
          got["granted_allowed"], True)
    check("an ungranted one is refused", got["ungranted_denied"], True)
    check("...by the grant table", got["ungranted_rule"], "face_capability")
    check("codex's guest is a guest on the unqualified call",
          got["codex_guest_is_guest"], True)
    check("benham's guest is a STRANGER to the codex process",
          got["benham_guest_is_stranger_here"], False)
    check("the shared owner still owns both", got["owner_still_owner"], True)
    check("guest_config() serves codex's roster", got["guest_ids"],
          [111222333444555777])
    check("initiative budget is codex's own", got["initiative_gap"], 99)
    check("codex may post in its own guild", got["posts_own_guild"], True)
    check("...and not in benham's", got["posts_benhams_guild"], False)

print(f"\n{'FAIL' if _fails else 'OK'}: {len(_fails)} failures")
sys.exit(1 if _fails else 0)
