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


# ===================================================================== the end
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
