"""
test_jobs_close.py - borrowed tools and the close of a job (INTENT decision 61,
Stage 9 C; decision 62).

Caz, 2026-11-03: "The purpose should expire the guest shouldnt. So say they need
access to websearch (or something) they use it, and then I get a response from
Benham on what was done and found with the tool, in the form of a friendly heads
up, then I get prompted to have the tool removed, or kept."

So, in this file:

  * a person holds a tool set in people.json; grant_tool / revoke_tool change it
    one tool at a time, each on a card; web_search is the only grantable tool,
    anything else refused in words;

Fully offline: no Discord, no API.

    python test_jobs_close.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import json
import os
import sys

from benham.core import capabilities
from benham.core import identity
from benham.core import jobs
from benham.core import people
from benham.core import policy
from benham.core.policy import CallContext

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


OWNER = sorted(identity.OWNER_IDS)[0]
FRIEND = _testconfig.GUEST_ID          # invented; on the fixture's guest.ids
FRIEND_2 = _testconfig.GUEST_ID_2
KNOWN = 555000555000555021             # invented; made known through people.json


class _Sent:
    def __init__(self, mid):
        self.id = mid


class _DM:
    def __init__(self, owner):
        self.owner = owner

    async def send(self, content=None, files=None, view=None, **kw):
        DMS.append((self.owner.id, content))
        if view is not None:
            VIEWS.append((self.owner.id, content, view))
        return _Sent(880000 + len(DMS))


class _U:
    def __init__(self, uid, name):
        self.id, self.name = uid, name
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
        return self.get_user(uid)


DMS, VIEWS = [], []
CLIENT = _Client([_U(KNOWN, "biglike"), _U(FRIEND, "doomlike"), _U(FRIEND_2, "other"),
                  _U(OWNER, "caz")])


def go(name, params=None, force=False, owner_dm=False, system=False):
    if system:
        cctx = CallContext.system()
    elif owner_dm:
        cctx = CallContext.owner_dm(OWNER, channel_id=1)
    else:
        cctx = CallContext.local()
    return asyncio.run(capabilities.run(
        CLIENT, lambda *_: None, name, params or {},
        actor_id=OWNER if owner_dm else None, force=force, call_ctx=cctx))


def refusal(name, params=None, force=False):
    try:
        go(name, params, force=force)
        return None
    except capabilities.ActionError as e:
        return str(e)


def wipe_all():
    for p in (people.path(), jobs.path()):
        try:
            os.remove(p)
        except OSError:
            pass
    DMS.clear()
    VIEWS.clear()


def setup_people():
    wipe_all()
    people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})


# --------------------------------------------------------------------------
section("people.json carries a person's tools - default chat + web search")
setup_people()
check("a known person with no `tools` key holds the default", people.tools_for(KNOWN),
      ["web_search"])
check("a config-only guest (no row) holds the default too", people.tools_for(FRIEND),
      ["web_search"])
check("add_person's row has no tools key - five fields, as ever",
      sorted(people.get(KNOWN)), ["by", "display_name", "name", "note", "since"])
people.set_tools(KNOWN, [])
check("a row with tools [] holds nothing beyond chat", people.tools_for(KNOWN), [])
check("...and the key is on disk", people.get(KNOWN)["tools"], [])
people.set_tools(KNOWN, ["web_search", "pc_task"])
check("set_tools keeps only what is grantable", people.get(KNOWN)["tools"], ["web_search"])
with open(people.path(), encoding="utf-8") as f:
    raw = json.load(f)
raw[str(KNOWN)]["tools"] = ["pc_task", "web_search", "read_channel"]
with open(people.path(), "w", encoding="utf-8") as f:
    json.dump(raw, f)
check("a hand-edited row can never widen the lane", people.tools_for(KNOWN), ["web_search"])
check("set_tools for someone with no row and no new row writes nothing",
      (people.set_tools(FRIEND, []), people.get(FRIEND)), (None, None))

section("The registry rows - what the fingerprint pins")
for n in ("grant_tool", "revoke_tool"):
    a = capabilities.REGISTRY[n]
    check(f"{n}: tier 2, always confirmed, not outward, taints",
          (a.tier, a.always_confirm, a.outward, a.taints), (identity.MANAGE, True, False, True))
    check(f"{n} is owner-only - a guest cannot reach it",
          policy.authorize(a, CallContext.guest_dm(FRIEND)).allowed, False)
    check(f"{n} is not reachable by a timer (SYSTEM)",
          policy.authorize(a, CallContext.system()).allowed, False)

section("revoke_tool - a card, then web search is gone; grant_tool gives it back")
setup_people()
r, preview = go("revoke_tool", {"who": "Big Like", "tool": "web_search"})
check("unconfirmed: a card naming the person and the tool",
      (r, preview["summary"]),
      (None, f"Take web search away from **Big Like (@biglike, id {KNOWN})**"))
check("...saying what they'd hold", "They'd hold: chat only." in preview["detail"], True)
check("the card changed nothing", people.tools_for(KNOWN), ["web_search"])
r, _ = go("revoke_tool", {"who": str(KNOWN), "tool": "web search"}, force=True)
check("confirmed: revoked ('web search' with a space is web_search)",
      (r["status"], r["tools"], people.tools_for(KNOWN)), ("tool_revoked", [], []))
check("revoking what they don't hold is refused in words",
      refusal("revoke_tool", {"who": str(KNOWN), "tool": "web_search"}),
      f"Big Like (@biglike, id {KNOWN}) doesn't hold web search.")
r, preview = go("grant_tool", {"who": str(KNOWN), "tool": "web_search"})
check("grant card", preview["summary"], f"Give **Big Like (@biglike, id {KNOWN})** web search")
check("the card changed nothing", people.tools_for(KNOWN), [])
r, _ = go("grant_tool", {"who": str(KNOWN), "tool": "web_search"}, force=True, owner_dm=True)
check("confirmed: granted", (r["status"], people.tools_for(KNOWN)), ("tool_granted",
                                                                    ["web_search"]))
check("granting what they hold is refused",
      refusal("grant_tool", {"who": str(KNOWN), "tool": "web_search"}),
      f"Big Like (@biglike, id {KNOWN}) already holds web search.")

section("Only web_search - anything else refused by name, nothing written")
for t in ("pc_task", "read_channel", "dm_user"):
    check(f"grant {t}: refused",
          refusal("grant_tool", {"who": str(KNOWN), "tool": t}),
          f"The guest brain can't run {t}; web_search is the only tool it has.")
check("revoke of a non-tool: refused the same way",
      refusal("revoke_tool", {"who": str(KNOWN), "tool": "shell"}),
      "The guest brain can't run shell; web_search is the only tool it has.")
check("Caz never borrows a tool", "Caz doesn't borrow tools" in (refusal(
    "grant_tool", {"who": "caz", "tool": "web_search"}) or ""), True)
check("a stranger is refused - add_person first", "add_person makes someone known first"
      in (refusal("grant_tool", {"who": "999000999000999000", "tool": "web_search"}) or ""),
      True)

section("A config-only guest gets a row on their first change")
setup_people()
r, preview = go("revoke_tool", {"who": str(FRIEND), "tool": "web_search"})
check("the card says a row will be written",
      "no people.json row yet" in preview["detail"], True)
check("...and wrote none", people.get(FRIEND), None)
r, _ = go("revoke_tool", {"who": str(FRIEND), "tool": "web_search"}, force=True)
row = people.get(FRIEND)
check("confirmed: a row, holding nothing, by caz",
      (r["row_written"], row["tools"], row["by"]), (True, [], "caz"))
check("...named from control.json (the fixture lists ids only), still a guest",
      (row["name"], identity.is_guest(FRIEND)), (str(FRIEND), True))
r, _ = go("grant_tool", {"who": str(FRIEND), "tool": "web_search"}, force=True)
check("a second change finds the row, writes none new",
      (r["row_written"], people.tools_for(FRIEND)), (False, ["web_search"]))

section("list_people shows each person's tools")
setup_people()
people.set_tools(KNOWN, [])
r, _ = go("list_people")
rows = {x["user_id"]: x for x in r["people"]}
check("revoked: []", rows[KNOWN]["tools"], [])
check("default: web_search", rows[FRIEND]["tools"], ["web_search"])
wipe_all()


# --------------------------------------------------------------------------
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
