"""
test_people.py - known people as live state (INTENT decision 61, Stage 9 A).

The claim under test is goal 9 of docs/plans/PLAN-known-people.md: "lists live in
the bot's own state and take effect without a restart". So the central rows here
write state/people.json IN THE MIDDLE of a process that has already imported
everything, and assert the very next check sees it - is_guest, the guest DM gate
(guest.is_known_guest, policy.may_chat_as_guest, guest.check), and the outreach
reachable set. Then the same for a removal. A store that was read at import would
pass every row about its contents and fail exactly these.

    python test_people.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import json
import os
import sys

from benham import paths
from benham.core import capabilities
from benham.core import identity
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


# Invented, like _testconfig's two. NEWBIE is on no config list; the fixture's
# GUEST_ID is on guest.ids, so it is the "both sources" case.
NEWBIE = 555000555000555009
OWNER = sorted(identity.OWNER_IDS)[0]
STRANGER = 999000999000999000


def write_raw(obj, face=None):
    """Write people.json BEHIND the store's back, the way another process (or a
    hand edit) would - the store must not need to have written it to see it."""
    p = people.path(face)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def wipe(face=None):
    try:
        os.remove(people.path(face))
    except OSError:
        pass


# --------------------------------------------------------------------------
section("The store - where it lives, what it reads")

check("people.json lives in the process face's state dir",
      people.path(), os.path.join(paths.process_state_dir(), "people.json"))
check("a named face gets its own file under faces/<name>/",
      people.path("codex"), os.path.join(paths.STATE_DIR, "faces", "codex", "people.json"))
wipe()
check("no file: nobody known, no error", people.load(), {})
check("an invalid face name answers empty, never raises", people.load("../../x"), {})

write_raw({str(NEWBIE): {"name": "newbie", "display_name": "New Bie",
                         "since": "2026-11-03T16:30:00-05:00", "by": "caz", "note": "test"},
           "not-an-id": {"name": "x"},
           "123": "not a dict",
           "-5": {"name": "negative"}})
loaded = people.load()
check("damaged rows are dropped, the good one survives", sorted(loaded), [NEWBIE])
check("ids are ints", people.ids(), frozenset({NEWBIE}))
check("names() is {name: id}", people.names(), {"newbie": NEWBIE})
check("get() by str or int", people.get(str(NEWBIE))["display_name"], "New Bie")
check("get() of a stranger is None", people.get(STRANGER), None)
check("get() of garbage is None", people.get("nope"), None)
wipe()

rec = people.add(NEWBIE, {"name": None, "display_name": None, "since": "s", "by": "cli",
                          "note": None, "extra": "dropped"})
check("add with no name names them by id", rec["name"], str(NEWBIE))
check("add keeps only the five fields", sorted(rec), ["by", "display_name", "name", "note", "since"])
with open(people.path(), encoding="utf-8") as f:
    on_disk = json.load(f)
check("on disk, keyed by the id as a string", list(on_disk), [str(NEWBIE)])
check("remove returns the record", people.remove(NEWBIE)["by"], "cli")
check("remove of someone absent is None", people.remove(NEWBIE), None)
check("and the file is left empty, not deleted", people.load(), {})
wipe()


# --------------------------------------------------------------------------
section("Live read - a write mid-process is seen by the very next check")

from benham.guest import guest  # noqa: E402 - after the fixture, like test_guest

check("before: NEWBIE is not a guest", identity.is_guest(NEWBIE), False)
check("before: not a known guest", guest.is_known_guest(NEWBIE), False)
check("before: may_chat_as_guest refuses on the allowlist",
      policy.may_chat_as_guest(CallContext.guest_dm(NEWBIE)).rule, "guest_allowlist")

write_raw({str(NEWBIE): {"name": "newbie"}})
check("after a raw write: is_guest", identity.is_guest(NEWBIE), True)
check("after a raw write: is_known_guest (the bot.py DM route)",
      guest.is_known_guest(NEWBIE), True)
check("after a raw write: may_chat_as_guest allows",
      policy.may_chat_as_guest(CallContext.guest_dm(NEWBIE)).allowed, True)
d = guest.check(NEWBIE)
check("after a raw write: guest.check lets the message through", d.allowed, True)
if d.allowed:
    guest.refund(NEWBIE)

wipe()
check("after the file goes: is_guest is False again, no restart",
      identity.is_guest(NEWBIE), False)
check("after the file goes: guest.check refuses as a stranger",
      guest.check(NEWBIE).rule, "guest_allowlist")

section("The owner exclusion holds through the new source")
write_raw({str(OWNER): {"name": "owner-by-mistake"}})
check("an owner written into people.json is still not a guest",
      identity.is_guest(OWNER), False)
wipe()

section("Config stays a source - the union, not a replacement")
check("the fixture's config guest is still a guest with no people.json",
      identity.is_guest(_testconfig.GUEST_ID), True)
write_raw({str(_testconfig.GUEST_ID): {"name": "doom"}})
check("...and with a people.json row of their own too", identity.is_guest(_testconfig.GUEST_ID), True)
wipe()

section("Per face - one face's people are not another's")
write_raw({str(NEWBIE): {"name": "newbie"}}, face="codex")
check("a codex row does not make a benham guest", identity.is_guest(NEWBIE), False)
check("people.ids('codex') sees it", people.ids("codex"), frozenset({NEWBIE}))
wipe("codex")


# --------------------------------------------------------------------------
# The verbs, through the one chokepoint (capabilities.run) as the CLI reaches
# them - LOCAL_CLI - and as Caz does, in a DM.

def go(name, params=None, client=None, force=False, owner_dm=False):
    """capabilities.run -> (result, preview). Raises ActionError."""
    cctx = (CallContext.owner_dm(OWNER, channel_id=1) if owner_dm
            else CallContext.local())
    return asyncio.run(capabilities.run(
        client, lambda *_: None, name, params or {},
        actor_id=OWNER if owner_dm else None, force=force, call_ctx=cctx))


def refusal(name, params=None, client=None, force=False, owner_dm=False):
    try:
        go(name, params, client=client, force=force, owner_dm=owner_dm)
        return None
    except capabilities.ActionError as e:
        return str(e)


section("list_people - config people, state people, and where each comes from")
wipe()
r, pending = go("list_people")
check("a read: no preview, no confirm", pending, None)
config_ids = sorted(identity.people_map(identity.guest_config().get("ids")).values())
check("with no state: exactly the config guests", [x["user_id"] for x in r["people"]], config_ids)
check("...each sourced 'config' from guest.ids",
      {(x["source"], tuple(x["lists"])) for x in r["people"]}, {("config", ("guest.ids",))})
check("guest chat reported on/off", r["guest_chat"], "on")
check("count matches", r["count"], len(config_ids))

people.add(NEWBIE, {"name": "newbie", "display_name": "New Bie", "since": "2026-11-03T16:30:00-05:00",
                    "by": "caz", "note": "game hunt"})
people.add(_testconfig.GUEST_ID, {"name": "doom", "by": "cli"})
people.add(OWNER, {"name": "owner-by-mistake", "by": "cli"})
r, _ = go("list_people")
rows = {x["user_id"]: x for x in r["people"]}
check("a state person shows with source 'state' - written mid-process, seen now",
      rows.get(NEWBIE, {}).get("source"), "state")
check("...with since, by, note and display name",
      {k: rows.get(NEWBIE, {}).get(k) for k in ("since", "by", "note", "display_name")},
      {"since": "2026-11-03T16:30:00-05:00", "by": "caz", "note": "game hunt",
       "display_name": "New Bie"})
check("...and lists ['people.json']", rows.get(NEWBIE, {}).get("lists"), ["people.json"])
check("someone in both is 'config+state'",
      rows.get(_testconfig.GUEST_ID, {}).get("source"), "config+state")
check("an owner row in the store is never listed", OWNER in rows, False)
check("sorted by name", [x["name"].lower() for x in r["people"]],
      sorted(x["name"].lower() for x in r["people"]))
wipe()

saved = dict(identity.CONTROL)
identity.CONTROL["outreach"] = {"people": {"draco": _testconfig.GUEST_ID_2, "caz": OWNER}}
r, _ = go("list_people")
rows = {x["user_id"]: x for x in r["people"]}
check("outreach.people is a config source too, named",
      (rows.get(_testconfig.GUEST_ID_2, {}).get("lists"), rows.get(_testconfig.GUEST_ID_2, {}).get("name")),
      (["guest.ids", "outreach.people"], "draco"))
check("an owner on outreach.people is left out", OWNER in rows, False)
identity.CONTROL.clear()
identity.CONTROL.update(saved)

check("list_people is owner-only - a guest cannot reach it",
      policy.authorize(capabilities.REGISTRY["list_people"],
                       CallContext.guest_dm(_testconfig.GUEST_ID)).allowed, False)


# --------------------------------------------------------------------------
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
