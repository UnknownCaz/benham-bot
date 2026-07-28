"""
test_find_user.py - the name->id lookup, offline.

find_user is the one read that has two completely different implementations behind
one name: a substring scan of the member cache when the Server Members intent is
on, and a gateway prefix query when it is off. Whichever one Tyler happens to be
running, the other is the untested half - so both are driven here against stubs.

The prefix path is the one worth guarding hardest. It cannot match mid-string, and
a search that quietly returns two of the three matching people looks exactly like a
search that returned all of them.

    python test_find_user.py
"""

import asyncio
import sys

import capabilities
import policy

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------------- stubs

class _StubMember:
    def __init__(self, uid, name, nick=None, global_name=None, bot=False):
        self.id = uid
        self.name = name
        self.nick = nick
        self.global_name = global_name
        self.bot = bot

    @property
    def display_name(self):
        return self.nick or self.global_name or self.name

    def __str__(self):
        return self.name


class _StubGuild:
    """A guild whose two lookup paths can disagree, which is the real case.

    `members` is the cache and `roster` is what the gateway would answer from - with
    the intent off the cache is nearly empty while the roster is complete, so the
    stub keeps them separate rather than deriving one from the other.
    """

    def __init__(self, gid, name, roster, cached=()):
        self.id = gid
        self.name = name
        self.roster = list(roster)
        self.members = list(cached)
        self.queries = []

    async def query_members(self, query=None, limit=5, **kw):
        self.queries.append((query, limit))
        low = (query or "").lower()
        # Discord matches a PREFIX of the username or nickname. Copying that
        # exactly is the point of the stub: a substring stub here would make the
        # intent-off path look like it finds people it cannot find.
        return [m for m in self.roster
                if m.name.lower().startswith(low)
                or (m.nick or "").lower().startswith(low)][:limit]


class _StubIntents:
    def __init__(self, members):
        self.members = members


class _StubClient:
    def __init__(self, guilds, members_intent):
        self.guilds = list(guilds)
        self.intents = _StubIntents(members_intent)

    def get_guild(self, gid):
        return next((g for g in self.guilds if g.id == int(gid)), None)

    def get_user(self, uid):
        for g in self.guilds:
            for m in g.roster:
                if m.id == int(uid):
                    return m
        return None

    async def fetch_user(self, uid):
        return self.get_user(uid)


TESTING, CHILLBAR = 736988645562646619, 1491485711076167711
CAZ = 273967061619965952

# UnknownCaz is the case the two paths split on: "caz" is inside the name but not
# at the front, so the cache finds them and the gateway never can.
caz = _StubMember(CAZ, "caz6666", nick="Tyler")
unknown = _StubMember(1001, "UnknownCaz", global_name="Unknown")
cazgirl = _StubMember(1002, "cazgirl", nick="Caz Jr")
alex = _StubMember(1003, "alexander", nick="alex")
benham = _StubMember(1004, "benham-bot", bot=True)

ROSTER = [caz, unknown, cazgirl, alex, benham]


def _client(members_intent):
    """Cache follows the intent, the way discord.py actually behaves.

    With the intent on it chunks each guild at startup, so a guild's cache is its
    own roster - NOT some shared list. Getting that wrong in the stub hid a bug in
    an earlier draft of this file, where guild_id=Chillbar appeared to return
    members who are only in Testing.
    """
    return _StubClient(
        [_StubGuild(TESTING, "Testing Server", ROSTER, ROSTER if members_intent else []),
         _StubGuild(CHILLBAR, "Chillbar", [caz, alex], [caz, alex] if members_intent else [])],
        members_intent,
    )


def run(client, **params):
    result, _pending = asyncio.run(capabilities.run(
        client, lambda *_: None, "find_user", params,
        call_ctx=policy.CallContext.local()))
    return result


def ids(result):
    return sorted(m["user_id"] for m in result["matches"])


# ------------------------------------------------------- intent ON: the cache
section("Members intent ON - substring scan of the cache")
on = _client(True)
r = run(on, query="caz")
check("finds all three, including the mid-string one", ids(r), sorted([CAZ, 1001, 1002]))
check("reports the cache path", r["method"], "member_cache")
check("no prefix caveat when it can match substrings", "note" in r, False)
check("does not touch the gateway", on.guilds[0].queries, [])
check("exact name outranks the rest", r["matches"][0]["user_id"], CAZ)
check("says which field matched",
      [m["matched_on"] for m in r["matches"] if m["user_id"] == 1002], ["username"])
check("a nickname-only hit is labelled as one",
      run(on, query="tyler")["matches"][0]["matched_on"], "nickname")
check("case does not matter", ids(run(on, query="CAZ6666")), [CAZ])
check("a miss is an empty result, not an error", run(on, query="nobodyhere")["count"], 0)
check("bots are findable too", ids(run(on, query="benham-bot")), [1004])

section("Cross-guild")
r = run(on, query="caz6666")
check("one row for someone in two servers", len(r["matches"]), 1)
check("both servers listed on it",
      sorted(g["name"] for g in r["matches"][0]["guilds"]), ["Chillbar", "Testing Server"])
narrowed = run(on, query="caz", guild_id=CHILLBAR)
check("guild_id narrows which servers are searched", narrowed["searched_guilds"], ["Chillbar"])
check("and narrows the results with it", ids(narrowed), [CAZ])

section("Limit")
r = run(on, query="caz", limit=1)
check("returns only the limit", len(r["matches"]), 1)
check("but still reports the true total", r["count"], 3)
check("and admits it truncated", r["truncated"], True)

# ---------------------------------------------------- intent OFF: the gateway
section("Members intent OFF - gateway prefix query")
off = _client(False)
r = run(off, query="caz")
check("finds the prefix matches", ids(r), sorted([CAZ, 1002]))
check("cannot find the mid-string one", 1001 in ids(r), False)
check("reports the prefix path", r["method"], "gateway_prefix")
check("and warns that it was prefix-only", r["note"].startswith("Server Members intent is off"), True)
check("queried the gateway", off.guilds[0].queries[0][0], "caz")
check("clamped the limit into Discord's 5..100", off.guilds[0].queries[0][1] >= 5, True)
check("still works with no cache at all", run(off, query="alex")["count"], 1)

section("Intent OFF but a few members happen to be cached")
# Whoever Benham saw in voice is in the cache even with the intent off, so the two
# paths run together and must not produce the same person twice.
half = _StubClient([_StubGuild(TESTING, "Testing Server", ROSTER, cached=[caz, unknown])], False)
r = run(half, query="caz")
check("cache adds the mid-string hit the gateway missed", ids(r), sorted([CAZ, 1001, 1002]))
check("the member in both paths appears once",
      sum(1 for m in r["matches"] if m["user_id"] == CAZ), 1)
check("still warns, because coverage is still partial", "note" in r, True)

section("A guild that fails to answer")


class _DeadGuild(_StubGuild):
    async def query_members(self, query=None, limit=5, **kw):
        raise asyncio.TimeoutError()


dead = _StubClient([_DeadGuild(TESTING, "Testing Server", ROSTER),
                    _StubGuild(CHILLBAR, "Chillbar", [caz, alex])], False)
r = run(dead, query="caz")
check("the healthy server still answers", ids(r), [CAZ])
check("and the failure is reported, not swallowed", r["problems"], ["Testing Server: member query timed out"])

# ------------------------------------------------------------------ id inputs
section("Ids and mentions short-circuit the search")
r = run(on, query=str(CAZ))
check("a raw id resolves", r["matches"][0]["user_id"], CAZ)
check("labelled as an id, not a name match", r["method"], "id")
check("an @mention resolves", run(on, query=f"<@{CAZ}>")["matches"][0]["user_id"], CAZ)
check("the <@!nick> form too", run(on, query=f"<@!{CAZ}>")["matches"][0]["user_id"], CAZ)
check("a short number is a name, not an id", capabilities._as_user_id("123"), None)
check("a name that is not digits is a name", capabilities._as_user_id("caz6666"), None)

section("Bad input")


def err(**params):
    try:
        run(on, **params)
        return None
    except capabilities.ActionError as e:
        return str(e)


check("empty query is refused", err(query="   ") is not None, True)
check("missing query is refused", err() is not None, True)
check("an unknown guild is refused", err(query="caz", guild_id=1) is not None, True)

section("Registration")
act = capabilities.REGISTRY["find_user"]
check("is tier 0 read", act.tier, 0)
check("taints - names are attacker-writable text", act.taints, True)
check("changes nothing outward", act.outward, False)
check("needs no confirmation", act.needs_confirm, False)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
