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
# The verbs, through the one chokepoint (capabilities.run), as the CLI reaches
# them (LOCAL_CLI) and as Caz does, in a DM. Discord is a fake whose DM channel
# records what was sent - the dm_user path, proven offline.

import asyncio  # noqa: E402

from benham.core import capabilities  # noqa: E402
from benham.core import conversations  # noqa: E402
from benham.core import identity  # noqa: E402
from benham.core import people  # noqa: E402
from benham.core import policy  # noqa: E402
from benham.core.policy import CallContext  # noqa: E402

OWNER = sorted(identity.OWNER_IDS)[0]
KNOWN = 555000555000555021          # invented; made known through people.json below
OUTREACH_ONLY = 555000555000555022  # invented; on outreach.people, not a guest
STRANGER = 999000999000999000
BOTLIKE = 752313060970201218        # Benham's own id - never known, so refused


class _Sent:
    def __init__(self, mid):
        self.id = mid


class _DM:
    def __init__(self, owner):
        self.owner = owner

    async def send(self, content=None, files=None, **kw):
        DMS.append((self.owner.id, content))
        if self.owner.refuse:
            import discord
            raise discord.Forbidden(_Forbidden(), "Cannot send messages to this user")
        return _Sent(880000 + len(DMS))


class _Forbidden:
    status, reason = 403, "Forbidden"


class _U:
    def __init__(self, uid, name, refuse=False):
        self.id, self.name, self.refuse = uid, name, refuse
        self.dm_channel = _DM(self)

    def __str__(self):
        return self.name


class _Client:
    def __init__(self, users):
        self.users = {u.id: u for u in users}
        self.guilds = []

    def get_user(self, uid):
        return self.users.get(int(uid))

    async def fetch_user(self, uid):
        u = self.get_user(uid)
        if u is None:
            import discord
            raise discord.NotFound(_NF(), "Unknown User")
        return u


class _NF:
    status, reason = 404, "Not Found"


DMS = []
CLIENT = _Client([_U(KNOWN, "biglike"), _U(FRIEND, "doomlike"), _U(FRIEND_2, "closed",
                                                                    refuse=True)])


def go(name, params=None, force=False, owner_dm=False, client=CLIENT):
    cctx = (CallContext.owner_dm(OWNER, channel_id=1) if owner_dm
            else CallContext.local())
    return asyncio.run(capabilities.run(
        client, lambda *_: None, name, params or {},
        actor_id=OWNER if owner_dm else None, force=force, call_ctx=cctx))


def refusal(name, params=None, force=False, owner_dm=False):
    try:
        go(name, params, force=force, owner_dm=owner_dm)
        return None
    except capabilities.ActionError as e:
        return str(e)


def setup_people():
    try:
        os.remove(people.path())
    except OSError:
        pass
    people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})


section("The registry rows - what the fingerprint pins")
oj, lj, cj = (capabilities.REGISTRY[n] for n in ("open_job", "list_jobs", "close_job"))
check("open_job: tier 2, always confirmed, outward (it sends a DM), taints",
      (oj.tier, oj.always_confirm, oj.outward, oj.taints, oj.needs_guild),
      (identity.MANAGE, True, True, True, False))
check("list_jobs: tier 0, taints, nothing else",
      (lj.tier, lj.always_confirm, lj.outward, lj.taints), (identity.READ, False, False, True))
check("close_job: tier 2, always confirmed, not outward (no DM in Stage B)",
      (cj.tier, cj.always_confirm, cj.outward), (identity.MANAGE, True, False))
for n in ("open_job", "list_jobs", "close_job"):
    check(f"{n} is owner-only - a guest cannot reach it",
          policy.authorize(capabilities.REGISTRY[n],
                           CallContext.guest_dm(_testconfig.GUEST_ID)).allowed, False)


section("open_job with caz - the card, then the job, and NOTHING is sent")
wipe()
setup_people()
conversations.forget()
DMS.clear()
r, preview = go("open_job", {"goal": "stage B rehearsal", "with": "caz"})
check("unconfirmed: a card, no result", (r, preview is not None), (None, True))
check("the card says it is Caz's", preview["summary"], "Open a job for **Caz**: stage B rehearsal")
check("...the goal, in his words", "Goal (Caz's words): stage B rehearsal" in preview["detail"], True)
check("...that no DM goes out", "no DM goes out" in preview["detail"], True)
check("...the tools", "Tools: web search" in preview["detail"], True)
check("...and no opener line", "Opener" in preview["detail"], False)
check("the card wrote nothing", jobs.load(), {})
r, pending = go("open_job", {"goal": "stage B rehearsal", "with": "caz"}, force=True)
check("confirmed: job_opened, j1", (r["status"], r["job_id"], pending), ("job_opened", "j1", None))
check("nothing was sent to anyone", (DMS, r["opener_sent"]), ([], False))
j = jobs.get("j1")
check("stored: with caz, web search, open, by cli",
      (j["with"], j["tools"], j["state"], j["opened_by"]), ("caz", ["web_search"], "open", "cli"))
check("...opened and last_activity stamped, log says opened",
      (bool(j["opened"]), j["last_activity"] == j["opened"], j["log"][0]["event"]),
      (True, True, "opened"))
check("an opener with caz is refused - there is nothing to send",
      "has no opener" in (refusal("open_job", {"goal": "x", "with": "caz",
                                               "opener": "hi"}) or ""), True)
r, _ = go("open_job", {"goal": "second", "with": "caz"}, force=True, owner_dm=True)
check("caz may have several open jobs; from his DM by = caz",
      (r["job_id"], jobs.get(r["job_id"])["opened_by"]), ("j2", "caz"))

section("list_jobs - newest first, with who and state")
r, pending = go("list_jobs")
check("a read: no card", pending, None)
check("both, newest first", [x["id"] for x in r["jobs"]], ["j2", "j1"])
check("open count", r["open"], 2)
check("with_label for caz", r["jobs"][0]["with_label"], "Caz")

section("close_job - card, then closed, nothing sent")
r, preview = go("close_job", {"job_id": "j1", "outcome": "rehearsal done"})
check("the card names the job and who", preview["summary"], "Close job **j1** with **Caz**")
check("...with the outcome and that nothing is sent",
      ("Outcome: rehearsal done" in preview["detail"],
       "Nothing is sent to anyone." in preview["detail"]), (True, True))
check("the card closed nothing", jobs.get("j1")["state"], "open")
r, _ = go("close_job", {"job_id": "1", "outcome": "rehearsal done"}, force=True)
check("confirmed: closed (by '1' as well as 'j1')", (r["status"], jobs.get("j1")["state"]),
      ("job_closed", "closed"))
j = jobs.get("j1")
check("...outcome, closed stamp, closed_by, log",
      (j["outcome"], bool(j["closed"]), j["closed_by"], j["log"][-1]["event"]),
      ("rehearsal done", True, "cli", "closed"))
check("closing twice is refused", "already closed" in (refusal(
    "close_job", {"job_id": "j1", "outcome": "x"}) or ""), True)
check("closing a job that does not exist is refused",
      "list_jobs shows them" in (refusal("close_job", {"job_id": "j99", "outcome": "x"}) or ""),
      True)
check("an outcome is required", "needs `outcome`" in (refusal("close_job", {"job_id": "j2"}) or ""),
      True)
r, _ = go("list_jobs", {"state": "open"})
check("list_jobs state=open: only j2", [x["id"] for x in r["jobs"]], ["j2"])
r, _ = go("list_jobs", {"state": "closed"})
check("list_jobs state=closed: j1 with its outcome",
      [(x["id"], x.get("outcome")) for x in r["jobs"]], [("j1", "rehearsal done")])
check("still nothing sent", DMS, [])

section("open_job with a person - the opener is a plain DM, logged by message id")
wipe()
DMS.clear()
r, preview = go("open_job", {"goal": "find a half-remembered game", "with": "Big Like"})
check("the card names them by display name, @name and id",
      preview["summary"],
      f"Open a job with **Big Like (@biglike, id {KNOWN})**: find a half-remembered game")
want_opener = ("Hey Big Like! Benham here - Caz asked me to give you a hand with this: "
               "\"find a half-remembered game\". Tell me what you've got so far and "
               "I'll start digging.")
check("...shows the EXACT opener that will be sent",
      f"> {want_opener}" in preview["detail"], True)
check("...says it is not outreach", "not through outreach" in preview["detail"], True)
check("the card sent nothing and wrote nothing", (DMS, jobs.load()), ([], {}))
r, _ = go("open_job", {"goal": "find a half-remembered game", "with": "Big Like"},
          force=True, owner_dm=True)
check("confirmed: opened, opener sent", (r["status"], r["opener_sent"]), ("job_opened", True))
check("exactly one DM, to them, the opener the card showed", DMS, [(KNOWN, want_opener)])
j = jobs.get(r["job_id"])
check("the job logs the sent message id", (j["opener_message_id"], j["log"][-1]["event"],
                                            j["log"][-1]["message_id"]),
      (r["message_id"], "opener_sent", r["message_id"]))
check("...and keeps the opener text", j["opener"], want_opener)
check("with is their id as a string; by caz", (j["with"], j["opened_by"]), (str(KNOWN), "caz"))
check("THE OUTREACH STORE WAS NOT TOUCHED - no conversation for them",
      conversations.all_conversations(counterparty=KNOWN), [])
check("...nor for anyone", conversations.all_conversations(), [])
check("open_for finds it - the guest note will see it", jobs.open_for(KNOWN)["id"], j["id"])
check("a second open job with the same person is refused, naming the first",
      f"already has an open job, {j['id']}" in (refusal(
          "open_job", {"goal": "another", "with": str(KNOWN)}, force=True) or ""), True)

section("open_job with a person - a written opener, an @mention, tools")
DMS.clear()
r, preview = go("open_job", {"goal": "help pick a mod", "with": f"<@{FRIEND}>",
                             "opener": "yo - Caz said you're hunting mods?\nwhat game?"})
check("a config guest by @mention; their own opener, every line quoted",
      ("> yo - Caz said you're hunting mods?" in preview["detail"],
       "> what game?" in preview["detail"]), (True, True))
check("tools refused by name, pointing at Stage C",
      refusal("open_job", {"goal": "x", "with": str(FRIEND), "tools": ["web_search", "read_channel"]}),
      "'read_channel' can't be given to a job yet - only web_search. Stage C grants other tools.")
r, preview = go("open_job", {"goal": "x", "with": str(FRIEND), "tools": []})
check("an empty tool list says chat only", "Tools: none beyond chat" in preview["detail"], True)
r, preview = go("open_job", {"goal": "x", "with": str(FRIEND), "tools": "web-search"})
check("'web-search' as a bare string is web_search", "Tools: web search" in preview["detail"], True)

section("open_job - who it can never be with")
check("Caz by id: told to use with=caz",
      "`with: caz`" in (refusal("open_job", {"goal": "x", "with": str(OWNER)}) or ""), True)
check("a stranger's id: add_person first",
      "add_person makes someone known first" in (refusal(
          "open_job", {"goal": "x", "with": str(STRANGER)}) or ""), True)
check("this bot's own id (a bot, never known): refused the same way",
      "isn't a known person" in (refusal("open_job", {"goal": "x", "with": str(BOTLIKE)}) or ""),
      True)
check("an unknown name: refused, pointing at list_people",
      "list_people" in (refusal("open_job", {"goal": "x", "with": "nobody"}) or ""), True)
saved = dict(identity.CONTROL)
identity.CONTROL["outreach"] = {"people": {"reachonly": OUTREACH_ONLY}}
check("someone only on outreach.people can't DM back: refused",
      "can't DM this bot back" in (refusal("open_job", {"goal": "x", "with": "reachonly"}) or ""),
      True)
identity.CONTROL.clear()
identity.CONTROL.update(saved)
saved_guest = identity.GUEST
identity.GUEST = {**saved_guest, "enabled": False}
check("guest chat off: refused - nobody could answer",
      "Guest chat is OFF" in (refusal("open_job", {"goal": "x", "with": str(FRIEND)}) or ""), True)
identity.GUEST = saved_guest
check("a blank goal is refused", "needs a goal" in (refusal(
    "open_job", {"goal": "  ", "with": "caz"}) or ""), True)

section("open_job - a DM Discord refuses closes the job it just wrote")
DMS.clear()
n_before = len(jobs.load())
err = refusal("open_job", {"goal": "x", "with": str(FRIEND_2)}, force=True) or ""
check("refused in words: written, not sent, closed",
      ("didn't go out" in err, "so I closed it" in err), (True, True))
failed = list(jobs.load().values())[-1]
check("the job exists, closed, with why", (len(jobs.load()), failed["state"],
                                           failed["outcome"].startswith("opener not sent"),
                                           failed["log"][-1]["event"]),
      (n_before + 1, "closed", True, "opener_failed"))
check("so it never shapes their chat", jobs.open_for(FRIEND_2), None)
wipe()
try:
    os.remove(people.path())
except OSError:
    pass


# --------------------------------------------------------------------------
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
