"""
test_jobs.py - a job is live state, opens with a plain DM, and carries its goal
into the guest brain (INTENT decisions 61 and 62, Stage 9 B).

THE AFTERNOON THIS EXISTS FOR. 2026-11-03 14:23-14:28 local: Caz had Benham open a
DM with a friend to help find a half-remembered game. The opener went through
`outreach`, so when the friend answered, the guest brain was told "their message
was just recorded as their answer... Acknowledge it in a few words" - and it
relayed instead of helping. Caz: "This is awful cut this off, and mark this as a
major failure."

So, in order of how much it matters:

  * an open job's note REPLACES the outreach bound/waiting note for that person's
    messages, and the note carries the goal, not "recorded";
  * a person with NO open job is handled exactly as before (decision 51);
  * open_job never touches the outreach store - the opener is a plain DM, logged
    into the job by its message id;
  * a job `with: caz` sends nothing at all;
  * the store is read on every use: a job written mid-process is seen at once.

Fully offline: no Discord, no API.

    python test_jobs.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import json
import os
import sys

from benham import paths
from benham.core import jobs

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)
    return ok


def section(name):
    print(f"\n{name}")


FRIEND = _testconfig.GUEST_ID          # invented, on the fixture's guest list
FRIEND_2 = _testconfig.GUEST_ID_2


def write_raw(obj, face=None):
    """Write jobs.json BEHIND the store's back, as another process would."""
    p = jobs.path(face)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def wipe(face=None):
    try:
        os.remove(jobs.path(face))
    except OSError:
        pass


# --------------------------------------------------------------------------
section("The store - where it lives, what it reads")

check("jobs.json lives in the process face's state dir",
      jobs.path(), os.path.join(paths.process_state_dir(), "jobs.json"))
check("a named face gets its own file under faces/<name>/",
      jobs.path("codex"), os.path.join(paths.STATE_DIR, "faces", "codex", "jobs.json"))
wipe()
check("no file: no jobs, no error", jobs.load(), {})
check("an invalid face name answers empty, never raises", jobs.load("../../x"), {})
check("open_for with no file is None", jobs.open_for(FRIEND), None)

write_raw({"j2": {"goal": "find a game", "with": str(FRIEND), "state": "open"},
           "j1": {"goal": "old one", "with": str(FRIEND), "state": "closed"},
           "jx": {"goal": "bad id"},
           "j9": "not a dict",
           "j4": {"with": "caz"},                       # no goal: dropped
           "j3": {"goal": "for caz", "with": "caz", "state": "open"}})
loaded = jobs.load()
check("damaged rows are dropped, good ones survive, oldest first",
      list(loaded), ["j1", "j2", "j3"])
check("get by 'j2' or '2'", (jobs.get("j2")["goal"], jobs.get("2")["goal"]),
      ("find a game", "find a game"))
check("get of garbage is None", jobs.get("nope"), None)
check("open_for finds the OPEN job, not the closed one", jobs.open_for(FRIEND)["id"], "j2")
check("...by str id too", jobs.open_for(str(FRIEND))["id"], "j2")
check("open_for of someone else is None", jobs.open_for(FRIEND_2), None)
check("a caz job is never a guest's", jobs.open_for("caz"), None)
wipe()

section("create / update / touch")
a = jobs.create({"goal": "g1", "with": str(FRIEND), "tools": ["web_search"],
                 "opened": jobs.stamp(), "opened_by": "cli"})
b = jobs.create({"goal": "g2", "with": "caz", "tools": []})
check("ids count up from j1", (a["id"], b["id"]), ("j1", "j2"))
check("a new job is open, with an empty log and no turns",
      (a["state"], a["log"], a["turns"]), ("open", [], 0))
check("stamp is the Detroit clock with its offset",
      a["opened"][:2] == "20" and a["opened"][-6] in "+-" and a["opened"][-3] == ":", True)
u = jobs.update("j1", log={"event": "opener_sent", "message_id": 42}, opener="hi")
check("update merges fields", u["opener"], "hi")
check("...and appends a stamped log entry",
      (u["log"][0]["event"], u["log"][0]["message_id"], u["log"][0]["at"][:2]),
      ("opener_sent", 42, "20"))
check("update of a missing job is None", jobs.update("j77", state="closed"), None)
t1 = jobs.touch("j1")
t2 = jobs.touch("j1")
check("touch counts turns", (t1["turns"], t2["turns"]), (1, 2))
check("...and stamps last_activity", bool(t2.get("last_activity")), True)
check("touch of garbage is None", jobs.touch("zz"), None)
jobs.update("j1", state="closed")
check("closing takes it out of open_for at once", jobs.open_for(FRIEND), None)
c = jobs.create({"goal": "g3", "with": str(FRIEND)})
check("a closed job's id is never reused", c["id"], "j3")
jobs.update("j3", state="nudged")
check("nudged (Stage C) still counts as open", jobs.open_for(FRIEND)["id"], "j3")
with open(jobs.path(), encoding="utf-8") as f:
    on_disk = json.load(f)
check("on disk, keyed by id, in order", list(on_disk), ["j1", "j2", "j3"])
wipe()

section("Per face - one face's jobs are not another's")
write_raw({"j1": {"goal": "x", "with": str(FRIEND), "state": "open"}}, face="codex")
check("a codex job is not a benham job", jobs.open_for(FRIEND), None)
check("open_for(face='codex') sees it", jobs.open_for(FRIEND, face="codex")["id"], "j1")
wipe("codex")


# --------------------------------------------------------------------------
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
