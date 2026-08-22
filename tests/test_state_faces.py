"""
test_state_faces.py - the per-face/shared state classification, held to.

Commit 6 of PLAN-second-face.md. The spike classified all ~26 state entries:
nine are per-face (the face is part of the data's identity - whose DM thread,
whose quota, whose gateway's channel map), the rest are project truth and
deliberately shared. The plan's own risk note says commit 6 is where a wrong
call "shows up as a silent bug of the kind INTENT §7 is a list of" - so this
file asserts BOTH halves, in a real subprocess running as a real second face,
because the process face is decided by BENHAM_FACE at import time and only a
fresh interpreter honestly exercises that.

    python test_state_faces.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - redirects CONFIG/STATE dirs first

import json
import os
import subprocess
import sys

from benham import paths
from benham.core import agent, conversations, initiative, issues, rooms
from benham.guest import guest

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


# --- this process (no BENHAM_FACE): every store is exactly where it was -----

S = paths.STATE_DIR
check("no BENHAM_FACE means the primary face", paths.PROCESS_FACE, "benham")
check("agent memory unchanged", agent.MEMORY_FILE, os.path.join(S, "agent_memory.json"))
check("agent search log unchanged", agent.SEARCH_LOG, os.path.join(S, "agent_searches.jsonl"))
check("guest memory unchanged", guest.MEMORY_FILE, os.path.join(S, "guest_memory.json"))
check("guest usage unchanged", guest.USAGE_FILE, os.path.join(S, "guest_usage.json"))
check("guest quiet unchanged", guest.QUIET_FILE, os.path.join(S, "guest_quiet.json"))
check("guest search log unchanged", guest.SEARCH_LOG, os.path.join(S, "guest_searches.jsonl"))
check("initiative store unchanged", initiative.STORE,
      os.path.join(S, "initiative.json"))
check("initiative log unchanged", initiative.LOG_MD,
      os.path.join(S, "initiative-log.md"))
check("conversations shared, at the shared root", conversations.STORE,
      os.path.join(S, "conversations.json"))
check("rooms shared, at the shared root", rooms.INDEX_FILE,
      os.path.join(S, "rooms.json"))
check("persona unchanged (commit 9)", agent.PERSONA_FILE,
      os.path.join(paths.PROMPTS_DIR, "persona.md"))
check("guest persona unchanged", guest.PERSONA_FILE,
      os.path.join(paths.PROMPTS_DIR, "guest_persona.md"))
check("issues file as the primary face", issues.FILED_BY, "Benham")

# Codex's persona ships with the plumbing, and its two named prohibitions are
# asserted the way the guest persona's false sentences are: in a test, so a
# reword cannot drift them back in. (INTENT: in-world it is "the Codex" -
# the manuscript never says either word below.)
_codex_persona = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "prompts", "faces", "codex", "persona.md")
check("the codex persona file exists in the repo", os.path.exists(_codex_persona), True)
if os.path.exists(_codex_persona):
    with open(_codex_persona, encoding="utf-8") as _f:
        _ptext = _f.read()
    check("...and is warm-but-businesslike per Tyler's voice answer",
          "warm but businesslike" in _ptext, True)
    check("...never breaks frame (INTENT #4 stated inside it)",
          "break frame" in _ptext, True)

# --- a real codex process: per-face stores move, shared stores DO NOT -------
# The subprocess bootstraps _testconfig exactly as this file did, so it writes
# nothing into live state; it then prints every path for the parent to judge.

_probe = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import _testconfig
from benham import paths
from benham.core import agent, conversations, initiative, issues, rooms
from benham.guest import guest
print(json.dumps({
    "process_face": paths.PROCESS_FACE,
    "persona": agent.PERSONA_FILE,
    "guest_persona": guest.PERSONA_FILE,
    "filed_by": issues.FILED_BY,
    "prompts_dir": paths.PROMPTS_DIR,
    "initiative": initiative.STORE,
    "initiative_log": initiative.LOG_MD,
    "agent_memory": agent.MEMORY_FILE,
    "agent_searches": agent.SEARCH_LOG,
    "guest_memory": guest.MEMORY_FILE,
    "guest_usage": guest.USAGE_FILE,
    "guest_quiet": guest.QUIET_FILE,
    "guest_searches": guest.SEARCH_LOG,
    "conversations": conversations.STORE,
    "rooms_index": rooms.INDEX_FILE,
    "state_dir": paths.STATE_DIR,
}))
"""

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_tests_dir)
env = dict(os.environ, BENHAM_FACE="codex", PYTHONIOENCODING="utf-8")
proc = subprocess.run([sys.executable, "-c", _probe, _tests_dir, _root],
                      capture_output=True, text=True, env=env, timeout=120)
check("a BENHAM_FACE=codex interpreter comes up clean", proc.returncode, 0)
if proc.returncode == 0:
    got = json.loads(proc.stdout)
    F = os.path.join(got["state_dir"], "faces", "codex")
    check("codex process knows its face", got["process_face"], "codex")
    for key, fname in [("agent_memory", "agent_memory.json"),
                       ("agent_searches", "agent_searches.jsonl"),
                       ("guest_memory", "guest_memory.json"),
                       ("guest_usage", "guest_usage.json"),
                       ("guest_quiet", "guest_quiet.json"),
                       ("guest_searches", "guest_searches.jsonl"),
                       ("initiative", "initiative.json"),
                       ("initiative_log", "initiative-log.md")]:
        check(f"codex {key} lives under faces/codex/", got[key],
              os.path.join(F, fname))
    check("codex persona lives under prompts/faces/codex/ (commit 9)",
          got["persona"],
          os.path.join(got["prompts_dir"], "faces", "codex", "persona.md"))
    check("codex guest persona too", got["guest_persona"],
          os.path.join(got["prompts_dir"], "faces", "codex", "guest_persona.md"))
    check("codex files issues as Codex, not as Benham", got["filed_by"], "Codex")
    check("conversations STAY at the shared root for codex - one question owed "
          "is one question owed, whoever carried it",
          got["conversations"], os.path.join(got["state_dir"], "conversations.json"))
    check("rooms STAY at the shared root for codex - sharing is the point of rooms",
          got["rooms_index"], os.path.join(got["state_dir"], "rooms.json"))
else:
    print(f"      stderr: {proc.stderr[-600:]}")

# --- a typo'd face refuses the interpreter, never runs as the primary -------

env_bad = dict(os.environ, BENHAM_FACE="Codex!", PYTHONIOENCODING="utf-8")
proc_bad = subprocess.run([sys.executable, "-c", _probe, _tests_dir, _root],
                          capture_output=True, text=True, env=env_bad, timeout=120)
check("an invalid BENHAM_FACE refuses to come up", proc_bad.returncode != 0, True)
check("...and the error names the face", "Codex!" in proc_bad.stderr, True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
