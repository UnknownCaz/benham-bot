"""
test_settings.py - server settings and ordering, offline (INTENT 60, Stage 8 C).

Three capabilities and one read give the server's Overview, Moderation, Channels
and Roles ordering screens to a prompt: guild_info's `settings` block,
edit_server_settings, move_channel, move_role. What is pinned:

  THE READ IS THE SAME TABLE AS THE EDIT. guild_info's settings block names every
  field edit_server_settings can change (AFK channel and timeout, system channel
  and each of its toggles, default notifications, verification level, explicit-
  content filter, rules and public-updates channels) plus the boost and Community
  facts that gate them. The toggle table is pinned against discord.py's own
  SystemChannelFlags, so a toggle discord.py learns later fails here.

  THE POSTURE IS edit_server_look's. All three are tier 2, outward, always
  confirmed, taint (the preview repeats channel and role names). A guest
  reaches none of them.

  REFUSED AT THE PREVIEW, WHOLE. An unknown value, an AFK timeout Discord does
  not offer, a channel of the wrong kind or from another server, a rules channel
  on a server that isn't Community, a request that changes nothing, two places
  named for one move, and every case of the role hierarchy rule: one plain
  sentence, and Discord is never asked to change anything.

  THE EXACT INVERSE IS MADE TRUE. Every confirmed call first writes what it is
  about to replace to look_backups (a settings change: the old values, as the
  parameters that would put them back), then changes, then READS BACK and
  reports what Discord holds - never what was sent. No backup, no change.

The guild, its channels, roles and the bot's member are REAL discord.py 2.7.1
objects built from the JSON Discord sends, and the edits are discord.py's own
Guild.edit / Guild.edit_role_positions / Guild.fetch_channels - only the HTTP
layer underneath is fake, and it records every request. So a keyword argument
discord.py would reject fails here, and "Discord was never asked" is a fact
about a list. The backup folder is a temp directory.

    python test_settings.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import atexit
import copy
import json
import os
import shutil
import sys
import tempfile
import types

import discord

from benham.core import capabilities, policy

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!a}\n        want {want!a}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# ======================================================================= stubs
#
# Discord as a dict of JSON (the guild, its channels, its roles) and a record of
# every request. Everything above that is discord.py's own.

OWNER = 273967061619965952
GUILD = 736988645562646619          # the Testing Server
OTHER_GUILD = 8800009
BOT = 8800001

# Channels. Top level: #welcome, #asd. Category Games: #benham-beta, #lfg, #clips
# and the voice channel Game Night. Category Voice: General, AFK (voice), and the
# stage Town Hall. Category Archive: #old.
WELCOME, ASD = 101, 809357286036078612
GAMES, VOICECAT, ARCHIVE = 200, 300, 400
BETA, LFG, CLIPS, GAMENIGHT = 201, 202, 203, 204
GENERAL, AFK, TOWNHALL = 301, 302, 303
OLD = 401
ELSEWHERE = 901                     # a text channel in another server

# Roles, lowest first: @everyone 0, Members 1, Regulars 2, Hermes 3 (another
# bot's managed role), Mods 4, Bots 5 (this bot's top role), Owner 6.
EVERYONE = GUILD
MEMBERS, REGULARS, HERMES, MODS, BOTS, OWNER_ROLE = 11, 12, 13, 14, 15, 16

ADMIN = 8                           # the administrator bit
MANAGE_CHANNELS, MANAGE_GUILD, MANAGE_ROLES = 1 << 4, 1 << 5, 1 << 28
VIEW, SEND = 1 << 10, 1 << 11


class _Resp:
    def __init__(self, status=400):
        self.status = status
        self.reason = "stub"


def chan(cid, name, kind, pos, parent=None, guild=GUILD):
    t = {"text": 0, "voice": 2, "category": 4, "stage": 13}[kind]
    d = {"id": str(cid), "type": t, "name": name, "position": pos,
         "guild_id": str(guild), "parent_id": str(parent) if parent else None,
         "permission_overwrites": [], "nsfw": False}
    if kind in ("voice", "stage"):
        d.update(bitrate=64000, user_limit=0)
    return d


def role(rid, name, pos, perms=0, managed=False):
    d = {"id": str(rid), "name": name, "position": pos, "permissions": str(perms),
         "color": 0, "hoist": False, "managed": managed, "mentionable": False,
         "flags": 0}
    if managed:
        d["tags"] = {"bot_id": "8800777"}
    return d


def guild_data(gid=GUILD, name="Testing Server", bot_perms=ADMIN, community=False,
               **over):
    if gid == GUILD:
        channels = [
            chan(WELCOME, "welcome", "text", 0), chan(ASD, "asd", "text", 1),
            chan(GAMES, "Games", "category", 0), chan(VOICECAT, "Voice", "category", 1),
            chan(ARCHIVE, "Archive", "category", 2),
            chan(BETA, "benham-beta", "text", 0, GAMES), chan(LFG, "lfg", "text", 1, GAMES),
            chan(CLIPS, "clips", "text", 2, GAMES),
            chan(GAMENIGHT, "Game Night", "voice", 0, GAMES),
            chan(GENERAL, "General", "voice", 0, VOICECAT),
            chan(AFK, "AFK", "voice", 1, VOICECAT),
            chan(TOWNHALL, "Town Hall", "stage", 2, VOICECAT),
            chan(OLD, "old", "text", 0, ARCHIVE),
        ]
        roles = [role(gid, "@everyone", 0, VIEW | SEND), role(MEMBERS, "Members", 1),
                 role(REGULARS, "Regulars", 2), role(HERMES, "Hermes", 3, managed=True),
                 role(MODS, "Mods", 4), role(BOTS, "Bots", 5, bot_perms),
                 role(OWNER_ROLE, "Owner", 6, ADMIN)]
        bot_role = BOTS
    else:
        channels = [chan(ELSEWHERE, "elsewhere", "text", 0, guild=gid)]
        roles = [role(gid, "@everyone", 0, VIEW | SEND), role(BOTS + 100, "Bots", 1, ADMIN)]
        bot_role = BOTS + 100
    d = {"id": str(gid), "name": name, "owner_id": str(OWNER), "member_count": 9,
         "verification_level": 0, "default_message_notifications": 1,
         "explicit_content_filter": 0, "afk_timeout": 300,
         "afk_channel_id": str(AFK) if gid == GUILD else None,
         "system_channel_id": str(WELCOME) if gid == GUILD else None,
         # 4 is setup tips' suppress bit: that one toggle is OFF.
         "system_channel_flags": 4,
         "rules_channel_id": None, "public_updates_channel_id": None,
         "features": [], "premium_tier": 0, "premium_subscription_count": 0,
         "roles": roles, "channels": channels,
         "members": [{"user": {"id": str(BOT), "username": "benham", "discriminator": "0",
                               "avatar": None, "bot": True},
                      "roles": [str(bot_role)],
                      "joined_at": "2020-01-01T00:00:00+00:00", "deaf": False,
                      "mute": False, "flags": 0}]}
    if community:
        d.update(features=["COMMUNITY", "NEWS"], rules_channel_id=str(ASD),
                 public_updates_channel_id=str(WELCOME), verification_level=1,
                 explicit_content_filter=2)
    d.update(over)
    return d


class _Http:
    """Discord, as far as this file goes: the guild's JSON, and a record."""

    def __init__(self, data):
        self.data = data
        self.writes = []                # (verb, payload, reason)
        self.reads = []
        self.raise_on_write = None
        self.answer_override = None     # makes Discord's answer disagree with the ask

    def _check(self):
        if self.raise_on_write is not None:
            raise self.raise_on_write

    async def edit_guild(self, guild_id, *, reason=None, **fields):
        self.writes.append(("edit_guild", dict(fields), reason))
        self._check()
        self.data.update(fields)
        # Discord answers with the guild object - no channels or members.
        out = {k: copy.deepcopy(v) for k, v in self.data.items()
               if k not in ("channels", "members")}
        if self.answer_override is not None:
            out.update(self.answer_override(fields))
        return out

    async def bulk_channel_update(self, guild_id, payload, *, reason=None):
        self.writes.append(("bulk_channel_update", copy.deepcopy(payload), reason))
        self._check()
        by_id = {c["id"]: c for c in self.data["channels"]}
        for item in payload:
            c = by_id[str(item["id"])]
            c["position"] = item["position"]
            if "parent_id" in item:
                c["parent_id"] = str(item["parent_id"]) if item["parent_id"] else None
        if self.answer_override is not None:
            self.answer_override(by_id)
        return None

    async def get_all_guild_channels(self, guild_id):
        self.reads.append("channels")
        return copy.deepcopy(self.data["channels"])

    async def move_role_position(self, guild_id, positions, *, reason=None):
        self.writes.append(("move_role_position", copy.deepcopy(positions), reason))
        self._check()
        by_id = {r["id"]: r for r in self.data["roles"]}
        for item in positions:
            by_id[str(item["id"])]["position"] = item["position"]
        if self.answer_override is not None:
            self.answer_override(by_id)
        return copy.deepcopy(self.data["roles"])


class _State:
    """The slice of discord.py's ConnectionState a Guild built from JSON needs."""
    cache_guild_expressions = False
    member_cache_flags = types.SimpleNamespace(joined=False, voice=False)
    self_id = BOT
    user = types.SimpleNamespace(id=BOT)

    def __init__(self, data):
        self.http = _Http(data)

    def store_user(self, data, **_):
        return discord.User(state=self, data=data)

    def get_user(self, uid):
        return None

    def _get_guild(self, gid):
        return None


class _Client:
    def __init__(self, guilds):
        self.guilds = {g.id: g for g in guilds}

    def get_guild(self, gid):
        return self.guilds.get(int(gid))

    def get_channel(self, cid):
        for g in self.guilds.values():
            ch = g.get_channel(int(cid))
            if ch is not None:
                return ch
        return None

    async def fetch_channel(self, cid):
        ch = self.get_channel(cid)
        if ch is None:
            raise discord.NotFound(_Resp(404), "Unknown Channel")
        return ch


def build(data):
    return discord.Guild(data=data, state=_State(data))


def fresh(**gkw):
    """(client, guild, http) over a new Testing Server and one other server."""
    g = build(guild_data(**gkw))
    other = build(guild_data(gid=OTHER_GUILD, name="Elsewhere"))
    return _Client([g, other]), g, g._state.http


def resync(client, g):
    """The gateway catching up: rebuild the cached guild from what Discord holds
    now (real Discord sends GUILD_UPDATE / CHANNEL_UPDATE after a change)."""
    ng = discord.Guild(data=g._state.http.data, state=g._state)
    client.guilds[ng.id] = ng
    return ng


def go(client, name, params, force=False):
    """capabilities.run as the owner in a DM -> (result, preview). Raises ActionError."""
    return asyncio.run(capabilities.run(
        client, lambda *_: None, name, params, actor_id=OWNER, force=force,
        call_ctx=policy.CallContext.owner_dm(OWNER, channel_id=ASD)))


def refusal(client, name, params, force=False):
    try:
        go(client, name, params, force=force)
        return None
    except capabilities.ActionError as e:
        return str(e)


def backups():
    d = capabilities.LOOK_BACKUP_DIR
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def one_sentence(msg):
    return bool(msg) and "\n" not in msg and "Traceback" not in msg


TMP = tempfile.mkdtemp(prefix="benham-settings-")
atexit.register(shutil.rmtree, TMP, True)
capabilities.LOOK_BACKUP_DIR = os.path.join(TMP, "look_backups")


def registered(name, tier, outward, always_confirm, taints):
    a = capabilities.REGISTRY[name]
    check(f"{name}: tier {tier}, needs a guild", (a.tier, a.needs_guild), (tier, True))
    check(f"{name}: outward={outward}, always_confirm={always_confirm}, taints={taints}",
          (a.outward, a.always_confirm, a.taints), (outward, always_confirm, taints))
    check(f"{name}: never a guest capability, and a guest is refused",
          (a.guest, policy.authorize(a, policy.CallContext.guest_dm(111, 5)).allowed),
          (False, False))


# ================================================================== guild_info
section("guild_info - still a read that taints, now with a settings block")
a = capabilities.REGISTRY["guild_info"]
check("guild_info: tier 0, taints, no confirm, not outward",
      (a.tier, a.taints, a.always_confirm, a.outward), (0, True, False, False))
cl, g, http = fresh()
result, preview = go(cl, "guild_info", {"guild_id": GUILD})
check("no preview for a read, nothing written", (preview, http.writes), (None, []))
check("the old fields are all still there",
      all(k in result for k in ("guild_id", "name", "members", "owner_id", "created",
                                "text_channels", "voice_channels", "roles", "emojis",
                                "benham_top_role", "benham_can", "destructive_allowed")),
      True)
check("...with their values", (result["name"], result["benham_top_role"],
                               result["destructive_allowed"]),
      ("Testing Server", "Bots", True))
s = result["settings"]
check("the AFK channel by name and id, and the timeout in seconds",
      (s["afk_channel"], s["afk_timeout"]), ({"name": "AFK", "id": AFK}, 300))
check("the system channel by name and id",
      s["system_channel"], {"name": "welcome", "id": WELCOME})
check("every system-channel toggle as true/false, Discord's suppress bits inverted",
      s["system_flags"],
      {"join_notifications": True, "boost_notifications": True, "setup_tips": False,
       "join_sticker_replies": True, "role_subscription_notifications": True,
       "role_subscription_sticker_replies": True, "emoji_added_notifications": True})
check("notifications, verification and the content filter in Discord's words",
      (s["default_notifications"], s["verification_level"], s["explicit_content_filter"]),
      ("only_mentions", "none", "disabled"))
check("no rules or public-updates channel on a server that isn't Community",
      (s["rules_channel"], s["public_updates_channel"], s["community"]), (None, None, False))
check("the boost tier and count, and the features list",
      (s["premium_tier"], s["boost_count"], s["features"]), (0, 0, []))
check("the block is exactly these fields",
      sorted(s), sorted(["afk_channel", "afk_timeout", "system_channel", "system_flags",
                         "default_notifications", "verification_level",
                         "explicit_content_filter", "rules_channel",
                         "public_updates_channel", "premium_tier", "boost_count",
                         "community", "features"]))
check("...and it is plain JSON", json.loads(json.dumps(s)) == s, True)

cl, g, http = fresh(community=True, premium_tier=2, premium_subscription_count=7,
                    afk_channel_id=None, system_channel_id=None,
                    default_message_notifications=0, system_channel_flags=0b111111111)
s = go(cl, "guild_info", {"guild_id": GUILD})[0]["settings"]
check("a Community server: rules and public-updates channels by name, community true",
      (s["rules_channel"], s["public_updates_channel"], s["community"]),
      ({"name": "asd", "id": ASD}, {"name": "welcome", "id": WELCOME}, True))
check("...its features sorted, its levels, boosts",
      (s["features"], s["verification_level"], s["explicit_content_filter"],
       s["premium_tier"], s["boost_count"], s["default_notifications"]),
      (["COMMUNITY", "NEWS"], "low", "all_members", 2, 7, "all_messages"))
check("no AFK or system channel reads as null", (s["afk_channel"], s["system_channel"]),
      (None, None))
check("every suppress bit set reads as every toggle off",
      set(s["system_flags"].values()), {False})
cl, g, http = fresh(afk_channel_id="555")
check("an AFK id this bot can't see as a channel keeps its id, name null",
      go(cl, "guild_info", {"guild_id": GUILD})[0]["settings"]["afk_channel"],
      {"name": None, "id": 555})

section("guild_info - the toggle table is discord.py's, all of it")
check("one name per SystemChannelFlags flag, none missing, none invented",
      sorted(attr for _, attr in capabilities._SYSTEM_FLAGS),
      sorted(discord.SystemChannelFlags.VALID_FLAGS))
check("...and no two toggles share a name",
      len({n for n, _ in capabilities._SYSTEM_FLAGS}), len(capabilities._SYSTEM_FLAGS))


# ================================================================ flags params
section("a flags parameter - a map of names to true/false, three spellings")
from benham.cli import do as _do      # noqa: E402
from benham.core import agent as _agent  # noqa: E402
FSPEC = {"type": "flags"}


def coerce(value):
    try:
        return capabilities._coerce("system_flags", FSPEC, value)
    except capabilities.ActionError as e:
        return f"refused: {e}"


check("a JSON object from a tool call", coerce({"setup_tips": False, "join_notifications": True}),
      {"setup_tips": False, "join_notifications": True})
check("the same object as text", coerce('{"setup_tips": false}'), {"setup_tips": False})
check("name=value pairs, commas, spaces or semicolons between",
      coerce("setup_tips=false, join_notifications=true;emoji_added_notifications=off"),
      {"setup_tips": False, "join_notifications": True, "emoji_added_notifications": False})
check("name:value works too, and yes/no/on/off/1/0",
      coerce("a:yes b:no c=on d=0"), {"a": True, "b": False, "c": True, "d": False})
check("an empty map is an empty map (the handler decides what that means)", coerce("{}"), {})
for bad in ("setup_tips", "{not json", "[1, 2]", ["setup_tips"], 7):
    m = coerce(bad)
    check(f"{bad!r} is refused with the shape spelled out",
          m.startswith("refused: system_flags must be a map of names to true/false"), True)
m = coerce({"setup_tips": "maybe"})
check("a value that isn't true or false names the flag",
      m, "refused: system_flags: setup_tips has to be true or false, got 'maybe'")
check("...a number other than 0/1 too", coerce({"x": 2}).startswith("refused: system_flags: x"),
      True)
_act = types.SimpleNamespace(params={"system_flags": FSPEC})
check("the CLI decodes a JSON object for a flags parameter",
      _do._parse_kv(['system_flags={"setup_tips": false}'], _act),
      ({"system_flags": {"setup_tips": False}}, None))
check("...and leaves name=value text for the coercion",
      _do._parse_kv(["system_flags=setup_tips=false,join_notifications=true"], _act),
      ({"system_flags": "setup_tips=false,join_notifications=true"}, None))
check("...a JSON list is not a flags value, so it stays text (and is refused later)",
      _do._parse_kv(["system_flags=[1]"], _act), ({"system_flags": "[1]"}, None))
check("the model is told it's an object", _agent._JSON_TYPE["flags"], "object")


# ======================================================== edit_server_settings
section("edit_server_settings - registered like edit_server_look")
registered("edit_server_settings", 2, True, True, True)
_tool = next(t for t in _agent.build_tools() if t["name"] == "edit_server_settings")
check("the model sees system_flags as an object of booleans",
      _tool["input_schema"]["properties"]["system_flags"],
      {"type": "object", "description": capabilities.REGISTRY["edit_server_settings"]
       .params["system_flags"]["desc"], "additionalProperties": {"type": "boolean"}})


def es(params, force=False, **gkw):
    """(result, preview, http, client, guild) for one edit_server_settings call."""
    cl, g, http = fresh(**gkw)
    result, preview = go(cl, "edit_server_settings", {"guild_id": GUILD, **params},
                         force=force)
    return result, preview, http, cl, g


def es_refused(params, **gkw):
    """The refusal, at the preview AND at the confirm, with Discord never asked."""
    out = []
    for force in (False, True):
        cl, g, http = fresh(**gkw)
        before = backups()
        m = refusal(cl, "edit_server_settings", {"guild_id": GUILD, **params}, force=force)
        out.append((m, http.writes == [], backups() == before))
    (m1, w1, b1), (m2, w2, b2) = out
    return m1 if (m1 == m2 and w1 and w2 and b1 and b2) else ("MISMATCH", out)


section("edit_server_settings - previewed first, nothing touched")
before = backups()
result, preview, http, cl, g = es({"afk_timeout": "15m"})
check("the first call is a preview; Discord is asked nothing, nothing is saved",
      (result, http.writes, backups() == before), (None, [], True))
check("...the summary names the server and the field",
      preview["summary"], "Change **Testing Server**'s settings: AFK timeout")
check("...the field old -> new, then 'Nothing else changes' and the backup promise",
      preview["detail"].splitlines(),
      ["AFK timeout: 5m (300 s) -> 15m (900 s)", "Nothing else changes.",
       "The current values are saved first, so this can be put back."])
_, preview, http, _, _ = es({"afk_channel_id": str(GENERAL), "afk_timeout": 300,
                             "system_channel_id": "none",
                             "system_flags": {"setup_tips": True, "join_notifications": True},
                             "default_notifications": "all messages",
                             "verification_level": "low",
                             "explicit_content_filter": "no_role"})
check("several fields at once: every change on its own line, old -> new",
      preview["detail"].splitlines()[:6],
      ["AFK channel: #AFK -> #General", "System channel: #welcome -> none",
       "System toggle setup_tips: off -> on", "Default notifications: only_mentions -> "
       "all_messages", "Verification level: none -> low",
       "Explicit-content filter: disabled -> no_role"])
check("...the ones already that way are named and left alone",
      "Already that way, left alone: afk_timeout (5m (300 s)), join_notifications (on)."
      in preview["detail"], True)
check("...the side effects a person should know, in words",
      all(w in preview["detail"] for w in (
          "no system channel, so the toggles have nowhere to post",
          "can't send messages until they do",
          "notified of every message")), True)
check("...and still nothing asked of Discord", http.writes, [])

section("edit_server_settings - refused at the preview, whole (and at the confirm)")
for raw in ("10m", "7", "2h", "soon", "-60", "0"):
    m = es_refused({"afk_timeout": raw})
    check(f"afk_timeout {raw!r}: Discord's five values named",
          isinstance(m, str) and m.startswith("afk_timeout can only be 1m, 5m, 15m, 30m "
                                              "or 1h") and one_sentence(m), True)
for raw, secs in (("1m", 60), ("300", 300), ("15 min", 900), ("30m", 1800),
                  ("1h", 3600), ("3600s", 3600), ("1 hour", 3600)):
    check(f"afk_timeout {raw!r} reads as {secs} s", capabilities._afk_timeout(raw), secs)
for field, raw, words in (
        ("default_notifications", "loud", "all_messages, only_mentions"),
        ("verification_level", "extreme", "none, low, medium, high, highest"),
        ("explicit_content_filter", "everyone", "disabled, no_role, all_members")):
    m = es_refused({field: raw})
    check(f"{field} {raw!r}: unknown, the known values listed",
          m, f"{field} is one of {words}, and {raw!r} isn't one of those.")
for field, cid, words in (
        ("afk_channel_id", ASD, "#asd is a text channel, and the AFK channel has to be a "
                                "voice channel."),
        ("afk_channel_id", TOWNHALL, "#Town Hall is a stage channel, and the AFK channel "
                                     "has to be a voice channel."),
        ("afk_channel_id", GAMES, "#Games is a category channel, and the AFK channel has "
                                  "to be a voice channel."),
        ("system_channel_id", GENERAL, "#General is a voice channel, and the system "
                                       "channel has to be a text channel.")):
    check(f"{field} -> a channel of the wrong kind ({cid})", es_refused({field: str(cid)}),
          f"{field}: {words}")
check("a channel from another server",
      es_refused({"system_channel_id": str(ELSEWHERE)}),
      "system_channel_id: channel 901 belongs to another server, not Testing Server.")
check("a channel that doesn't exist",
      es_refused({"afk_channel_id": "4242"}),
      "afk_channel_id: there's no channel 4242 in Testing Server that this bot can see.")
check("a channel id that isn't one", es_refused({"afk_channel_id": "#AFK"}),
      "afk_channel_id takes a channel id, or none to clear it - '#AFK' is neither.")
for field in ("rules_channel_id", "public_updates_channel_id"):
    m = es_refused({field: str(OLD)})
    check(f"{field} on a server that isn't Community: 'needs a Community server'",
          isinstance(m, str) and "needs a Community server, and Testing Server isn't one"
          in m and one_sentence(m), True)
m = es_refused({"rules_channel_id": "none"}, community=True)
check("a Community server can't clear its rules channel",
      m, "rules_channel_id: a Community server has to keep a rules channel - name another "
         "text channel instead of none.")
m = es_refused({"rules_channel_id": str(GENERAL)}, community=True)
check("...and its rules channel has to be a text channel",
      m, "rules_channel_id: #General is a voice channel, and a rules channel has to be a "
         "text channel.")
check("a request that changes nothing",
      es_refused({"afk_timeout": "5m", "default_notifications": "only_mentions",
                  "system_flags": {"setup_tips": False}}),
      "nothing would change - Testing Server already has afk_timeout (5m (300 s)), "
      "setup_tips (off), default_notifications (only_mentions).")
check("no field at all", es_refused({}).startswith("nothing to change - pass "), True)
check("an empty toggle map is no field either",
      es_refused({"system_flags": {}}).startswith("nothing to change"), True)
m = es_refused({"system_flags": {"pings": False}})
check("a toggle that doesn't exist, the real ones listed",
      m, "system_flags: 'pings' isn't a system-channel toggle - the toggles are "
         "join_notifications, boost_notifications, setup_tips, join_sticker_replies, "
         "role_subscription_notifications, role_subscription_sticker_replies, "
         "emoji_added_notifications.")
m = es_refused({"system_flags": {"boost_notifications": True,
                                 "premium_subscriptions": False}})
check("one toggle named twice (two spellings), on and off",
      m, "system_flags names boost_notifications twice, once on and once off - pick one.")
check("no Manage Server", es_refused({"afk_timeout": "1h"}, bot_perms=VIEW | SEND),
      "this bot lacks Manage Server in Testing Server, so it can't change the server's "
      "settings - give its role Manage Server (or Administrator) first.")

section("edit_server_settings - confirmed: backup, discord.py's own edit, read back")
before = backups()
result, _, http, cl, g = es({"afk_timeout": "15m", "reason": "stage c test"}, force=True)
check("one edit, only the field named, the typed reason",
      http.writes, [("edit_guild", {"afk_timeout": 900}, "stage c test")])
new = [b for b in backups() if b not in before]
check("one backup, <guild>-settings-<local stamp>.json",
      (len(new), new[0].startswith(f"{GUILD}-settings-2"), new[0].endswith(".json")),
      (1, True, True))
with open(os.path.join(capabilities.LOOK_BACKUP_DIR, new[0]), encoding="utf-8") as fh:
    rec = json.load(fh)
check("...it holds the old value as the parameter that puts it back",
      (rec["changed"], rec["restore"], rec["before"]),
      (["afk_timeout"], {"afk_timeout": "300"}, {"AFK timeout": "5m (300 s)"}))
check("...and the whole settings block as it was",
      (rec["settings_before"]["afk_timeout"], rec["guild_id"], rec["guild"],
       len(rec["saved_at"])), (300, GUILD, "Testing Server", 19))
check("the result is what Discord answered, and where the backup is",
      (result["status"], result["changed"], result["now"], result["settings"]["afk_timeout"],
       result["backup"], "warning" in result),
      ("settings_changed", ["afk_timeout"], {"afk_timeout": 900}, 900,
       os.path.join(capabilities.LOOK_BACKUP_DIR, new[0]), False))
check("...an audit reason defaults to the acting face",
      es({"afk_timeout": "1h"}, force=True)[2].writes[0][2], capabilities.VIA)

result, _, http, cl, g = es({"system_flags": "setup_tips=true"}, force=True)
check("a toggle: the current bits with only that one flipped (4 -> 0)",
      http.writes[0][1], {"system_channel_flags": 0})
check("...read back on, the others untouched",
      (result["now"], result["settings"]["system_flags"]["join_notifications"]),
      ({"setup_tips": True}, True))
_, _, http, _, _ = es({"system_flags": {"join_notifications": False}}, force=True)
check("switching one off sets only its suppress bit (4 -> 5)",
      http.writes[0][1], {"system_channel_flags": 5})
_, _, http, _, _ = es({"system_flags": {"premium_subscriptions": False}}, force=True)
check("discord.py's own spelling reaches the same toggle (4 -> 6)",
      http.writes[0][1], {"system_channel_flags": 6})
_, _, http, _, _ = es({"system_flags": {"setup_tips": True}}, force=True,
                      system_channel_flags=4 | 64)
check("a bit discord.py doesn't name survives the edit (68 -> 64)",
      http.writes[0][1], {"system_channel_flags": 64})
result, _, http, cl, g = es({"afk_channel_id": str(GENERAL), "system_channel_id": "0",
                             "default_notifications": "all_messages",
                             "verification_level": "medium",
                             "explicit_content_filter": "all_members"}, force=True)
check("channels by id (or null to clear), the levels as Discord's numbers",
      http.writes[0][1], {"afk_channel_id": GENERAL, "system_channel_id": None,
                          "default_message_notifications": 0, "verification_level": 2,
                          "explicit_content_filter": 2})
check("...read back field by field",
      result["now"], {"afk_channel": GENERAL, "system_channel": None,
                      "default_notifications": "all_messages",
                      "verification_level": "medium", "explicit_content_filter": "all_members"})
check("...the read-back names channels from the cache, though Discord's answer has none",
      result["settings"]["afk_channel"], {"name": "General", "id": GENERAL})
result, _, http, _, _ = es({"rules_channel_id": str(OLD),
                            "public_updates_channel_id": str(ASD)}, force=True,
                           community=True)
check("a Community server's rules and public-updates channels",
      (http.writes[0][1], result["now"]),
      ({"rules_channel_id": OLD, "public_updates_channel_id": ASD},
       {"rules_channel": OLD, "public_updates_channel": ASD}))

section("edit_server_settings - the read-back is Discord's, not the request's")
cl, g, http = fresh()
http.answer_override = lambda fields: {"afk_timeout": 300}
result, _ = go(cl, "edit_server_settings", {"guild_id": GUILD, "afk_timeout": "1h"},
               force=True)
check("Discord answers 300 to a 3600: the result says 300, and warns",
      (result["now"], result["settings"]["afk_timeout"], result["warning"]),
      ({"afk_timeout": 300}, 300, "Discord accepted the change but doesn't read back what "
                                  "was asked for: afk_timeout (it reads 300) - check "
                                  "guild_info."))

section("edit_server_settings - the backup comes first, and no backup means no change")
cl, g, http = fresh()
http.raise_on_write = discord.HTTPException(_Resp(400), {"code": 50035, "message":
                                                         "Invalid Form Body"})
before = backups()
m = refusal(cl, "edit_server_settings", {"guild_id": GUILD, "afk_timeout": "1h"}, force=True)
check("Discord's refusal is one sentence ending 'Nothing was changed.'",
      (one_sentence(m), m.startswith("Discord turned the settings change down: "),
       m.endswith("Nothing was changed.")), (True, True, True))
check("...and the backup had already been written before Discord was asked",
      len(backups()) - len(before), 1)
http.raise_on_write = discord.HTTPException(_Resp(429), "You are being rate limited.")
m = refusal(cl, "edit_server_settings", {"guild_id": GUILD, "afk_timeout": "1h"}, force=True)
check("a rate limit says so", m, "Discord is rate-limiting settings changes right now. "
                                 "Nothing was changed; try again in a while.")
_real_dir = capabilities.LOOK_BACKUP_DIR
_blocker = os.path.join(TMP, "not-a-folder")
with open(_blocker, "w") as fh:
    fh.write("x")
capabilities.LOOK_BACKUP_DIR = os.path.join(_blocker, "look_backups")
cl, g, http = fresh()
m = refusal(cl, "edit_server_settings", {"guild_id": GUILD, "afk_timeout": "1h"}, force=True)
capabilities.LOOK_BACKUP_DIR = _real_dir
check("a backup that can't be written stops the change: one sentence, Discord never asked",
      (m.startswith("I couldn't save the current settings first ("),
       m.endswith("so I didn't change anything. Nothing was changed."), http.writes),
      (True, True, []))

section("edit_server_settings - the backup's restore puts it back")
cl, g, http = fresh()
before = backups()
go(cl, "edit_server_settings", {"guild_id": GUILD, "afk_timeout": "15m",
                                "afk_channel_id": "none",
                                "system_flags": {"setup_tips": True, "join_notifications": False},
                                "verification_level": "high"}, force=True)
with open(os.path.join(capabilities.LOOK_BACKUP_DIR,
                       [b for b in backups() if b not in before][0]), encoding="utf-8") as fh:
    rec = json.load(fh)
check("restore holds every old value, toggles as a map",
      rec["restore"], {"afk_timeout": "300", "afk_channel_id": str(AFK),
                       "system_flags": {"setup_tips": False, "join_notifications": True},
                       "verification_level": "none"})
g = resync(cl, g)
result, _ = go(cl, "edit_server_settings", {"guild_id": GUILD, **rec["restore"]}, force=True)
g = resync(cl, g)
check("...fed back to edit_server_settings, the settings block is what it was",
      capabilities._settings_block(g), rec["settings_before"])


# ===================================================================== the end
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
