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
# A Discord that add_person can look people up in: guilds with whole member
# caches (the members intent is on since 2026-11-03), and a user fetch that
# knows only the members.

class _Member:
    def __init__(self, uid, name, global_name=None, nick=None, bot=False):
        self.id, self.name, self.global_name, self.nick, self.bot = uid, name, global_name, nick, bot

    @property
    def display_name(self):
        return self.nick or self.global_name or self.name

    def __str__(self):
        return self.name


class _Guild:
    def __init__(self, gid, name, members):
        self.id, self.name, self.members = gid, name, list(members)

    def get_member(self, uid):
        return next((m for m in self.members if m.id == int(uid)), None)


class _Intents:
    members = True


class _NotFoundResp:
    status, reason = 404, "Not Found"


class _Client:
    intents = _Intents()

    def __init__(self, guilds):
        self.guilds = list(guilds)

    def get_guild(self, gid):
        return next((g for g in self.guilds if g.id == int(gid)), None)

    def get_user(self, uid):
        for g in self.guilds:
            m = g.get_member(uid)
            if m is not None:
                return m
        return None

    async def fetch_user(self, uid):
        u = self.get_user(uid)
        if u is None:
            import discord
            raise discord.NotFound(_NotFoundResp(), "Unknown User")
        return u


BIGGIE_LIKE = 555000555000555011     # invented stand-ins, never a real person
ALEX_A, ALEX_B = 555000555000555012, 555000555000555013
BOT = 555000555000555014
biggie = _Member(BIGGIE_LIKE, "biglike", global_name="Big Like")
alexa = _Member(ALEX_A, "alex", global_name="Alex A")
alexb = _Member(ALEX_B, "alexander", global_name="Alex B")
abot = _Member(BOT, "somebot", bot=True)
owner_m = _Member(OWNER, "theowner", global_name="Owner")
doom_m = _Member(_testconfig.GUEST_ID, "doomlike", global_name="Doom Like")
CLIENT = _Client([_Guild(736988645562646619, "Testing Server",
                         [biggie, alexa, alexb, abot, owner_m, doom_m]),
                  _Guild(1491485711076167711, "Chillbar", [biggie, owner_m])])


section("add_person - the card first, nothing written until confirmed")
wipe()
r, preview = go("add_person", {"query": str(BIGGIE_LIKE), "note": "needs a game found"},
                client=CLIENT)
check("unconfirmed: no result, a preview", (r, preview is not None), (None, True))
check("the card names display name, @username and id",
      preview["summary"], f"Add **Big Like (@biglike, id {BIGGIE_LIKE})** to the people Benham knows")
check("...and the servers they share with Benham",
      "Shares with Benham: Chillbar, Testing Server" in preview["detail"], True)
check("...and what being known means, in the plan's words",
      "Being known means: can DM Benham and be messaged by him; chat + web search only."
      in preview["detail"], True)
check("...and that it is live and lasting",
      "Takes effect at once, no restart; lasts until remove_person." in preview["detail"], True)
check("...and the note", "Note: needs a game found" in preview["detail"], True)
check("the preview wrote nothing", people.load(), {})
check("and they are not a guest yet", identity.is_guest(BIGGIE_LIKE), False)

r, pending = go("add_person", {"query": str(BIGGIE_LIKE), "note": "needs a game found"},
                client=CLIENT, force=True)
check("confirmed: a result, no preview", (r["status"], pending), ("person_added", None))
rec = people.get(BIGGIE_LIKE)
check("people.json carries them", (rec or {}).get("name"), "biglike")
check("...display name, note, by (cli from the shell)",
      {k: (rec or {}).get(k) for k in ("display_name", "note", "by")},
      {"display_name": "Big Like", "note": "needs a game found", "by": "cli"})
check("...since, on the Detroit clock with its offset",
      bool(rec) and rec["since"][:2] == "20" and rec["since"][-6] in "+-"
      and rec["since"][-3] == ":", True)
check("the result says they can DM now", r["can_dm_now"], True)
check("and the very next check knows them - no restart", identity.is_guest(BIGGIE_LIKE), True)
r, _ = go("list_people")
check("list_people shows them with source 'state'",
      [x["source"] for x in r["people"] if x["user_id"] == BIGGIE_LIKE], ["state"])
check("adding them twice is refused, naming when",
      (refusal("add_person", {"query": str(BIGGIE_LIKE)}, client=CLIENT) or "").startswith(
          f"Big Like (@biglike, id {BIGGIE_LIKE}) is already known - since 20"), True)
wipe()

section("add_person - from Caz's DM, by name")
r, preview = go("add_person", {"query": "biglike"}, client=CLIENT, owner_dm=True)
check("a DM ask gets the card too (always_confirm)", (r, bool(preview)), (None, True))
r, _ = go("add_person", {"query": "big like"}, client=CLIENT, owner_dm=True, force=True)
check("by display name, confirmed, by = caz", (r["user_id"], people.get(BIGGIE_LIKE)["by"]),
      (BIGGIE_LIKE, "caz"))
wipe()
r, preview = go("add_person", {"query": "alex"}, client=CLIENT)
check("several matches, one of them exact: the exact one",
      preview["summary"], f"Add **Alex A (@alex, id {ALEX_A})** to the people Benham knows")
err = refusal("add_person", {"query": "ale"}, client=CLIENT) or ""
check("several matches, none exact: refused, with both ids to pick from",
      (str(ALEX_A) in err, str(ALEX_B) in err, "Say which with their id" in err),
      (True, True, True))
check("no match: refused, pointing at the id",
      "Copy User ID" in (refusal("add_person", {"query": "zzzz"}, client=CLIENT) or ""), True)
check("a refusal wrote nothing", people.load(), {})

section("add_person - who can never be added")
check("an owner, by id",
      "is an owner" in (refusal("add_person", {"query": str(OWNER)}, client=CLIENT) or ""), True)
check("an owner, by name",
      "is an owner" in (refusal("add_person", {"query": "theowner"}, client=CLIENT) or ""), True)
check("a bot",
      "is a bot account" in (refusal("add_person", {"query": str(BOT)}, client=CLIENT) or ""), True)
check("none of it wrote anything", people.load(), {})

section("add_person - an id Discord doesn't know (the fake-id rehearsal)")
FAKE = _testconfig.GUEST_ID_2 + 100
r, preview = go("add_person", {"query": str(FAKE)}, client=CLIENT)
check("the card says so plainly",
      "Discord has no user with this id - check it." in preview["detail"], True)
check("...and names it by id", preview["summary"],
      f"Add **{FAKE} (id {FAKE})** to the people Benham knows")
r, _ = go("add_person", {"query": str(FAKE)}, client=CLIENT, force=True)
check("confirmed anyway: stored, named by its id",
      (people.get(FAKE) or {}).get("name"), str(FAKE))
check("...and the result flags it", r["unknown_to_discord"], True)
wipe()

section("add_person - notes on the card")
r, preview = go("add_person", {"query": str(_testconfig.GUEST_ID)}, client=CLIENT)
check("someone already in control.json: allowed, and the card says where",
      "Already in control.json (guest.ids); this adds them to the live list too."
      in preview["detail"], True)
saved_guest = identity.GUEST
identity.GUEST = {**saved_guest, "enabled": False}
r, preview = go("add_person", {"query": str(BIGGIE_LIKE)}, client=CLIENT)
check("guest chat off: the card warns they can't DM yet",
      "Guest chat is OFF on this face" in preview["detail"], True)
identity.GUEST = saved_guest
wipe()

check("add_person is owner-only - a guest cannot reach it",
      policy.authorize(capabilities.REGISTRY["add_person"],
                       CallContext.guest_dm(_testconfig.GUEST_ID)).allowed, False)


# --------------------------------------------------------------------------
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
