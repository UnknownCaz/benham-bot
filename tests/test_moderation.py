"""
test_moderation.py - the moderation set, offline (INTENT 60, Stage 8 D).

Nine capabilities and one extension give the Audit Log, Bans, Invites, Prune,
member channel overwrites and AutoMod screens to a prompt: read_audit_log,
list_bans, list_invites, revoke_invite, prune_members, set_channel_permissions'
user_id, list_automod_rules, create_automod_rule, edit_automod_rule,
delete_automod_rule. What is pinned:

  THE READS ARE READS. Tier 0, a guild named, taint (reasons, names, inviters
  and AutoMod keywords are other people's text), times in both halves - Caz's
  clock and UTC - and an empty answer said plainly.

  THE POSTURE IS THE STAGE'S. revoke_invite, create_automod_rule and
  edit_automod_rule are tier 2 and always confirmed (the AutoMod pair outward:
  a rule acts on everyone's messages). prune_members and delete_automod_rule
  are tier 3 behind the three gates. A guest reaches none of them.

  REFUSED BEFORE ANYTHING CHANGES, WHOLE. An unknown action name, a bad prune
  day count, an unknown trigger or preset, content that doesn't fit its
  trigger, a rule with no action, a duplicate name, Discord's rule caps, an
  edit that changes nothing, an unknown invite code, both or neither of
  role_id and user_id: one plain sentence, and Discord is never asked to
  change anything.

  THE TRAP RULE (Stage 7) ON THE MEMBER PATH. Allows are applied first and the
  overwrite is READ BACK from Discord before any deny is sent - asserted on the
  order of the calls the fake HTTP layer recorded.

  THE EXACT INVERSE IS MADE TRUE. A revoke, an edit and a delete write what
  they replace to look_backups first, then change, then read back and report
  what Discord holds.

The guild, its channels, roles, members, invites, audit entries and AutoMod
rules are REAL discord.py 2.7.1 objects built from the JSON Discord sends;
only the HTTP layer underneath is fake, and it records every request in
order. The backup folder is a temp directory.

    python test_moderation.py
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
from datetime import datetime, timedelta, timezone

import discord

from benham.core import capabilities, eventtime, policy

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

OWNER = 273967061619965952
GUILD = 736988645562646619          # the Testing Server
OTHER_GUILD = 8800009
BOT = 8800001
ALICE, BOB, CAROL = 8800101, 8800102, 8800103   # invented people

WELCOME, ASD = 101, 809357286036078612
GAMES, BETA = 200, 201
VOICE = 301
ELSEWHERE = 901

EVERYONE = GUILD
MEMBERS, MODS, BOTS, OWNER_ROLE = 11, 14, 15, 16

ADMIN = 8
KICK, BAN = 1 << 1, 1 << 2
MANAGE_GUILD = 1 << 5
AUDIT = 1 << 7
VIEW, SEND = 1 << 10, 1 << 11
EMBED = 1 << 14
MANAGE_ROLES = 1 << 28
MODERATE = 1 << 40


class _Resp:
    def __init__(self, status=400):
        self.status = status
        self.reason = "stub"


def user(uid, name, bot=False, global_name=None):
    return {"id": str(uid), "username": name, "discriminator": "0", "avatar": None,
            "bot": bot, "global_name": global_name}


def chan(cid, name, kind, pos, parent=None, guild=GUILD):
    t = {"text": 0, "voice": 2, "category": 4}[kind]
    d = {"id": str(cid), "type": t, "name": name, "position": pos,
         "guild_id": str(guild), "parent_id": str(parent) if parent else None,
         "permission_overwrites": [], "nsfw": False}
    if kind == "voice":
        d.update(bitrate=64000, user_limit=0)
    return d


def role(rid, name, pos, perms=0):
    return {"id": str(rid), "name": name, "position": pos, "permissions": str(perms),
            "color": 0, "hoist": False, "managed": False, "mentionable": False,
            "flags": 0}


def member(uid, name, roles=(), global_name=None, bot=False):
    return {"user": user(uid, name, bot=bot, global_name=global_name),
            "roles": [str(r) for r in roles], "joined_at": "2020-01-01T00:00:00+00:00",
            "deaf": False, "mute": False, "flags": 0}


def guild_data(gid=GUILD, name="Testing Server", bot_perms=ADMIN, features=()):
    if gid == GUILD:
        channels = [chan(WELCOME, "welcome", "text", 0), chan(ASD, "asd", "text", 1),
                    chan(GAMES, "Games", "category", 0),
                    chan(BETA, "benham-beta", "text", 0, GAMES),
                    chan(VOICE, "General", "voice", 0)]
        roles = [role(gid, "@everyone", 0, VIEW | SEND), role(MEMBERS, "Members", 1),
                 role(MODS, "Mods", 2), role(BOTS, "Bots", 3, bot_perms),
                 role(OWNER_ROLE, "Owner", 4, ADMIN)]
        bot_role = BOTS
    else:
        channels = [chan(ELSEWHERE, "elsewhere", "text", 0, guild=gid)]
        roles = [role(gid, "@everyone", 0, VIEW | SEND), role(BOTS + 100, "Bots", 1, ADMIN)]
        bot_role = BOTS + 100
    return {"id": str(gid), "name": name, "owner_id": str(OWNER), "member_count": 4,
            "verification_level": 0, "default_message_notifications": 1,
            "explicit_content_filter": 0, "afk_timeout": 300, "afk_channel_id": None,
            "system_channel_id": None, "system_channel_flags": 0,
            "rules_channel_id": None, "public_updates_channel_id": None,
            "vanity_url_code": "testing" if "VANITY_URL" in features else None,
            "features": list(features), "premium_tier": 0,
            "premium_subscription_count": 0, "roles": roles, "channels": channels,
            "members": [member(BOT, "benham", [bot_role], bot=True)]}


# The people the fake Discord knows. Only the bot is in the member cache, so a
# lookup of anyone else goes through fetch_member - the path the live bot takes
# for a member it hasn't seen.
PEOPLE = {ALICE: member(ALICE, "alice", [MEMBERS], global_name="Alice"),
          BOB: member(BOB, "bob", global_name="Bob")}


class _Http:
    """Discord, as far as this file goes: the guild's JSON, its audit log, bans,
    invites and AutoMod rules - and an ordered record of every request."""

    def __init__(self, data):
        self.data = data
        self.calls = []                 # (verb, detail) for EVERY request, in order
        self.audit = []
        self.users = [user(BOT, "benham", bot=True),
                      user(ALICE, "alice", global_name="Alice"),
                      user(BOB, "bob", global_name="Bob"),
                      user(CAROL, "carol", global_name="Carol")]
        self.bans = []
        self.invites = []
        self.vanity = None
        self.rules = []
        self.next_rule = 9000
        self.prune_count = 0
        self.fail = {}                  # verb -> exception, raised once
        self.answer_override = {}       # verb -> fn(payload) changing what Discord holds

    def _call(self, verb, detail=None):
        self.calls.append((verb, detail))
        exc = self.fail.pop(verb, None)
        if exc is not None:
            raise exc

    def writes(self):
        reads = ("get_audit_logs", "get_bans", "invites_from", "get_vanity_code",
                 "get_invite", "get_channel", "get_member", "estimate_pruned_members",
                 "get_auto_moderation_rules", "get_auto_moderation_rule")
        return [c for c in self.calls if c[0] not in reads]

    def verbs(self):
        return [c[0] for c in self.calls]

    # --- audit log
    async def get_audit_logs(self, guild_id, limit=100, before=None, after=None,
                             user_id=None, action_type=None):
        self._call("get_audit_logs", {"limit": limit, "user_id": user_id,
                                      "action_type": action_type})
        rows = [e for e in self.audit
                if (user_id is None or e["user_id"] == str(user_id))
                and (action_type is None or e["action_type"] == action_type)]
        rows.sort(key=lambda e: -int(e["id"]))
        if before:
            rows = [e for e in rows if int(e["id"]) < int(before)]
        return {"audit_log_entries": copy.deepcopy(rows[:limit]),
                "users": copy.deepcopy(self.users), "integrations": [], "webhooks": [],
                "application_commands": [], "auto_moderation_rules": [],
                "guild_scheduled_events": [], "threads": []}

    # --- bans
    async def get_bans(self, guild_id, limit, before=None, after=None):
        self._call("get_bans", {"limit": limit})
        return copy.deepcopy(self.bans[:limit])

    # --- invites
    async def invites_from(self, guild_id):
        self._call("invites_from")
        return copy.deepcopy(self.invites)

    async def get_vanity_code(self, guild_id):
        self._call("get_vanity_code")
        return {"code": self.vanity, "uses": 3}

    async def get_invite(self, code, *, with_counts=True, guild_scheduled_event_id=None):
        self._call("get_invite", code)
        if code == self.vanity:
            return {"code": code, "channel": {"id": str(WELCOME), "name": "welcome",
                                              "type": 0},
                    "guild": {"id": str(GUILD), "name": "Testing Server"}}
        raise discord.NotFound(_Resp(404), "Unknown Invite")

    async def delete_invite(self, code, *, reason=None):
        self._call("delete_invite", {"code": code, "reason": reason})
        hit = next((i for i in self.invites if i["code"] == code), None)
        if hit is None:
            raise discord.NotFound(_Resp(404), "Unknown Invite")
        if "delete_invite" not in self.answer_override:
            self.invites.remove(hit)
        return copy.deepcopy(hit)

    # --- prune
    async def estimate_pruned_members(self, guild_id, days, roles):
        self._call("estimate_pruned_members", {"days": days, "roles": sorted(roles)})
        return {"pruned": self.prune_count}

    async def prune_members(self, guild_id, days, compute_prune_count, roles, *,
                            reason=None):
        self._call("prune_members", {"days": days, "roles": sorted(roles),
                                     "compute_prune_count": compute_prune_count,
                                     "reason": reason})
        return {"pruned": self.prune_count}

    # --- channel overwrites
    def _channel(self, cid):
        return next(c for c in self.data["channels"] if c["id"] == str(cid))

    async def edit_channel_permissions(self, channel_id, target, allow, deny, type, *,
                                       reason=None):
        self._call("edit_channel_permissions",
                   {"target": int(target), "allow": int(allow), "deny": int(deny),
                    "type": type, "reason": reason})
        c = self._channel(channel_id)
        c["permission_overwrites"] = [o for o in c["permission_overwrites"]
                                      if o["id"] != str(target)]
        ow = {"id": str(target), "type": type, "allow": str(allow), "deny": str(deny)}
        if "edit_channel_permissions" in self.answer_override:
            self.answer_override["edit_channel_permissions"](ow)
        c["permission_overwrites"].append(ow)

    async def delete_channel_permissions(self, channel_id, target, *, reason=None):
        self._call("delete_channel_permissions", {"target": int(target),
                                                  "reason": reason})
        c = self._channel(channel_id)
        c["permission_overwrites"] = [o for o in c["permission_overwrites"]
                                      if o["id"] != str(target)]

    async def get_channel(self, channel_id):
        self._call("get_channel", int(channel_id))
        return copy.deepcopy(self._channel(channel_id))

    async def get_member(self, guild_id, member_id):
        self._call("get_member", int(member_id))
        if int(guild_id) != GUILD or int(member_id) not in PEOPLE:
            raise discord.NotFound(_Resp(404), "Unknown Member")
        return copy.deepcopy(PEOPLE[int(member_id)])

    # --- AutoMod
    async def get_auto_moderation_rules(self, guild_id):
        self._call("get_auto_moderation_rules")
        return copy.deepcopy(self.rules)

    async def get_auto_moderation_rule(self, guild_id, rule_id):
        self._call("get_auto_moderation_rule", int(rule_id))
        hit = next((r for r in self.rules if r["id"] == str(rule_id)), None)
        if hit is None:
            raise discord.NotFound(_Resp(404), "Unknown auto moderation rule")
        return copy.deepcopy(hit)

    async def create_auto_moderation_rule(self, guild_id, *, reason, **payload):
        payload = {k: v for k, v in payload.items() if v is not None}
        self._call("create_auto_moderation_rule", {**copy.deepcopy(payload),
                                                   "reason": reason})
        self.next_rule += 1
        rule = {"id": str(self.next_rule), "guild_id": str(guild_id),
                "creator_id": str(BOT), "exempt_roles": [], "exempt_channels": [],
                "enabled": False, "trigger_metadata": {}, **copy.deepcopy(payload)}
        self.rules.append(rule)
        return copy.deepcopy(rule)

    async def edit_auto_moderation_rule(self, guild_id, rule_id, *, reason, **payload):
        payload = {k: v for k, v in payload.items() if v is not None}
        self._call("edit_auto_moderation_rule", {**copy.deepcopy(payload),
                                                 "reason": reason})
        rule = next(r for r in self.rules if r["id"] == str(rule_id))
        rule.update(copy.deepcopy(payload))
        rule["exempt_roles"] = [str(x) for x in rule["exempt_roles"]]
        rule["exempt_channels"] = [str(x) for x in rule["exempt_channels"]]
        if "edit_auto_moderation_rule" in self.answer_override:
            self.answer_override["edit_auto_moderation_rule"](rule)
        return copy.deepcopy(rule)

    async def delete_auto_moderation_rule(self, guild_id, rule_id, *, reason):
        self._call("delete_auto_moderation_rule", {"rule_id": int(rule_id),
                                                   "reason": reason})
        if "delete_auto_moderation_rule" not in self.answer_override:
            self.rules = [r for r in self.rules if r["id"] != str(rule_id)]


_GUILDS = {}


class _State:
    """The slice of discord.py's ConnectionState these objects need."""
    cache_guild_expressions = False
    member_cache_flags = types.SimpleNamespace(joined=False, voice=False)
    self_id = BOT
    user = types.SimpleNamespace(id=BOT)

    def __init__(self, data):
        self.http = _Http(data)

    def store_user(self, data, **_):
        return discord.User(state=self, data=data)

    def create_user(self, data):
        return discord.User(state=self, data=data)

    def get_user(self, uid):
        return None

    def get_emoji(self, eid):
        return None

    def get_sticker(self, sid):
        return None

    def _get_guild(self, gid):
        return _GUILDS.get(gid)


class _Client:
    def __init__(self, guilds):
        self.guilds = {g.id: g for g in guilds}
        self.foreign_invites = {}       # code -> guild id, invites of other servers

    def get_guild(self, gid):
        return self.guilds.get(int(gid))

    def get_channel(self, cid):
        for g in self.guilds.values():
            ch = g.get_channel(int(cid))
            if ch is not None:
                return ch
        return None

    def get_user(self, uid):
        return None

    async def fetch_channel(self, cid):
        ch = self.get_channel(cid)
        if ch is None:
            raise discord.NotFound(_Resp(404), "Unknown Channel")
        return ch

    async def fetch_invite(self, url, **_):
        code = str(url).rsplit("/", 1)[-1]
        if code in self.foreign_invites:
            return types.SimpleNamespace(code=code, guild=types.SimpleNamespace(
                id=self.foreign_invites[code]))
        raise discord.NotFound(_Resp(404), "Unknown Invite")


def build(data):
    g = discord.Guild(data=data, state=_State(data))
    _GUILDS[g.id] = g
    return g


def fresh(**gkw):
    """(client, guild, http) over a new Testing Server and one other server."""
    g = build(guild_data(**gkw))
    other = build(guild_data(gid=OTHER_GUILD, name="Elsewhere"))
    return _Client([g, other]), g, g._state.http


def resync(client, g):
    """The gateway catching up: rebuild the cached guild from what Discord holds."""
    ng = discord.Guild(data=g._state.http.data, state=g._state)
    client.guilds[ng.id] = ng
    _GUILDS[ng.id] = ng
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


TMP = tempfile.mkdtemp(prefix="benham-moderation-")
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


def snow(dt):
    return str(discord.utils.time_snowflake(dt, high=False) + 1)


WHEN = datetime(2026, 10, 30, 23, 30, tzinfo=timezone.utc)


# ============================================================== read_audit_log
section("read_audit_log - a read that taints, both clocks, names not ids")
registered("read_audit_log", 0, False, False, True)


def audit_entry(at, action_type, user_id, target_id=None, changes=(), reason=None,
                options=None):
    e = {"id": snow(at), "action_type": action_type, "user_id": str(user_id),
         "target_id": str(target_id) if target_id is not None else None,
         "changes": [{"key": k, "old_value": o, "new_value": n} for k, o, n in changes]}
    if reason is not None:
        e["reason"] = reason
    if options is not None:
        e["options"] = options
    return e


def seeded():
    cl, g, http = fresh()
    http.audit = [
        audit_entry(WHEN, 1, BOT, GUILD, [("afk_timeout", 300, 900),
                                          ("icon_hash", "aaaa", "bbbb")],
                    reason="via Benham"),
        audit_entry(WHEN + timedelta(minutes=1), 11, ALICE, ASD,
                    [("name", "asd-old", "asd"), ("rate_limit_per_user", 0, 5)]),
        audit_entry(WHEN + timedelta(minutes=2), 20, ALICE, BOB, reason="spam"),
        audit_entry(WHEN + timedelta(minutes=3), 31, BOT, MODS,
                    [("permissions", str(VIEW), str(VIEW | SEND | MANAGE_ROLES))]),
        audit_entry(WHEN + timedelta(minutes=4), 21, ALICE,
                    options={"delete_member_days": "7", "members_removed": "2"}),
        audit_entry(WHEN + timedelta(minutes=5), 11, BOT, BETA,
                    [("name", "a", "b"), ("topic", None, "x" * 200),
                     ("position", 0, 1), ("nsfw", False, True),
                     ("rate_limit_per_user", 0, 10), ("bitrate", 1, 2),
                     ("user_limit", 0, 3), ("rtc_region", None, "us-east")]),
    ]
    return cl, g, http


cl, g, http = seeded()
result, preview = go(cl, "read_audit_log", {"guild_id": GUILD})
check("no preview for a read, nothing written", (preview, http.writes()), (None, []))
check("Discord asked once: 20 entries, no filters, newest first",
      http.calls, [("get_audit_logs", {"limit": 20, "user_id": None,
                                       "action_type": None})])
check("six entries, newest first", (result["count"], [e["what"] for e in result["entries"]]),
      (6, ["channel_update", "member_prune", "role_update", "kick", "channel_update",
           "guild_update"]))
e = result["entries"][-1]
check("an entry: when in both halves, who by name, what, target, reason, changes",
      e, {"when": {"local": "Fri 2026-10-30 19:30 EDT", "utc": "2026-10-30T23:30:00Z"},
          "who": "benham", "what": "guild_update",
          "target": {"kind": "server", "name": "Testing Server"},
          "reason": "via Benham", "changes": ["afk_timeout: 300 -> 900"]})
check("...a picture's change (the icon) is left out, not printed as a blob",
      any("icon" in c for c in e["changes"]), False)
e = result["entries"][-2]
check("a channel edit by another member: their display name, the channel by #name",
      (e["who"], e["target"], e["reason"], e["changes"]),
      ("Alice", {"kind": "channel", "name": "#asd"}, None,
       ['name: "asd-old" -> "asd"', "slowmode_delay: 0 -> 5"]))
e = result["entries"][3]
check("a kick: the member kicked by name (from the log's own user list)",
      (e["what"], e["target"], e["reason"]), ("kick", {"kind": "member", "name": "Bob"},
                                               "spam"))
e = result["entries"][2]
check("a role's permissions: what was gained and lost, by name",
      (e["target"], e["changes"]),
      ({"kind": "role", "name": "Mods"}, ["permissions: +send_messages +manage_roles"]))
e = result["entries"][1]
check("a prune: how many it removed and the day count",
      (e["what"], e["extra"]), ("member_prune", ["members removed 2", "inactive days 7"]))
e = result["entries"][0]
check("many changes: six shown, then how many more", e["changes"][-1], "+2 more")
check("...a long text is cut short", e["changes"][1], 'topic: none -> "' + "x" * 77 + '..."')
check("no person's id anywhere in an entry",
      any(str(x) in json.dumps(result["entries"]) for x in (ALICE, BOB, BOT, OWNER)),
      False)
check("...and the result is plain JSON", json.loads(json.dumps(result)) == result, True)

cl, g, http = seeded()
result, _ = go(cl, "read_audit_log", {"guild_id": GUILD, "action_type": "channel_update",
                                      "limit": 5, "user_id": ALICE})
check("filters go to Discord: the action's number, the actor, the limit",
      http.calls, [("get_audit_logs", {"limit": 5, "user_id": ALICE, "action_type": 11})])
check("...and come back filtered", ([x["what"] for x in result["entries"]],
                                    result["filter"]),
      (["channel_update"], {"action_type": "channel_update", "by_user": True}))
for raw, name in (("member_kick", "kick"), ("Member Ban", "ban"), ("ROLE-UPDATE",
                                                                   "role_update")):
    cl, g, http = seeded()
    go(cl, "read_audit_log", {"guild_id": GUILD, "action_type": raw})
    check(f"action_type {raw!r} reads as {name}",
          http.calls[0][1]["action_type"], discord.AuditLogAction[name].value)
cl, g, http = seeded()
go(cl, "read_audit_log", {"guild_id": GUILD, "limit": 500})
check("a limit over 100 is held to 100", http.calls[0][1]["limit"], 100)

cl, g, http = seeded()
m = refusal(cl, "read_audit_log", {"guild_id": GUILD, "action_type": "explode"})
check("an unknown action name is refused at once, a few real ones named",
      (m, http.calls),
      ("action_type: 'explode' isn't an audit-log action - names look like "
       "channel_update, kick, ban, role_update, guild_update, invite_create or "
       "message_delete.", []))
m = refusal(cl, "read_audit_log", {"guild_id": GUILD, "action_type": "channel"})
check("...a near miss gets suggestions",
      m.startswith("action_type: 'channel' isn't an audit-log action - did you mean "
                   "channel_create, channel_delete, channel_update"), True)
cl, g, http = fresh()
result, _ = go(cl, "read_audit_log", {"guild_id": GUILD})
check("an empty log is said plainly", (result["count"], result["message"]),
      (0, "No audit-log entries in Testing Server match."))
cl, g, http = fresh(bot_perms=SEND)
m = refusal(cl, "read_audit_log", {"guild_id": GUILD})
check("no View Audit Log: one sentence, Discord never asked",
      (m, http.calls), ("this bot lacks View Audit Log in Testing Server, so it can't "
                        "read the audit log - give its role View Audit Log (or "
                        "Administrator) first.", []))


# =================================================================== list_bans
section("list_bans - who is banned, by name, and why")
registered("list_bans", 0, False, False, True)
cl, g, http = fresh()
http.bans = [{"user": user(BOB, "bob", global_name="Bob"), "reason": "spam links"},
             {"user": user(CAROL, "carol"), "reason": None}]
result, preview = go(cl, "list_bans", {"guild_id": GUILD})
check("a read: no preview, nothing written, Discord asked for 50",
      (preview, http.writes(), http.calls), (None, [], [("get_bans", {"limit": 50})]))
check("each ban: display name, username, the id unban_member takes, the reason",
      result["bans"], [{"name": "Bob", "username": "bob", "user_id": BOB,
                        "reason": "spam links"},
                       {"name": "carol", "username": "carol", "user_id": CAROL,
                        "reason": None}])
check("...counted, and the list is complete", (result["count"], result["complete"]),
      (2, True))
cl, g, http = fresh()
http.bans = [{"user": user(8800200 + i, f"u{i}"), "reason": None} for i in range(5)]
result, _ = go(cl, "list_bans", {"guild_id": GUILD, "limit": 3})
check("a limit: that many, and it says there may be more",
      (result["count"], result["complete"]), (3, False))
go(cl, "list_bans", {"guild_id": GUILD, "limit": 5000})
check("a limit over 1000 is held to 1000", http.calls[-1], ("get_bans", {"limit": 1000}))
cl, g, http = fresh()
result, _ = go(cl, "list_bans", {"guild_id": GUILD})
check("no bans is said plainly", (result["count"], result["bans"], result["message"]),
      (0, [], "No bans in Testing Server."))
cl, g, http = fresh(bot_perms=SEND)
check("no Ban Members: one sentence, Discord never asked",
      (refusal(cl, "list_bans", {"guild_id": GUILD}), http.calls),
      ("this bot lacks Ban Members in Testing Server, so it can't read the ban list - "
       "give its role Ban Members (or Administrator) first.", []))


# ================================================================ list_invites
section("list_invites - every active invite, both clocks, the vanity link")
registered("list_invites", 0, False, False, True)


def invite(code, cid, cname, inviter=ALICE, uses=1, max_uses=5, max_age=600,
           temporary=False, created=WHEN):
    d = {"code": code, "channel": {"id": str(cid), "name": cname, "type": 0},
         "guild": {"id": str(GUILD), "name": "Testing Server"},
         "uses": uses, "max_uses": max_uses, "max_age": max_age,
         "temporary": temporary, "created_at": created.isoformat()}
    if inviter is not None:
        d["inviter"] = copy.deepcopy(PEOPLE[inviter]["user"]) if inviter in PEOPLE \
            else user(inviter, "benham", bot=True)
    return d


def invited(**gkw):
    cl, g, http = fresh(**gkw)
    http.invites = [invite("aB3dE9f", ASD, "asd"),
                    invite("forever1", WELCOME, "welcome", inviter=BOT, uses=12,
                           max_uses=0, max_age=0, temporary=True,
                           created=WHEN + timedelta(hours=1))]
    return cl, g, http


cl, g, http = invited()
result, preview = go(cl, "list_invites", {"guild_id": GUILD})
check("a read: no preview, nothing written, one list call (no vanity feature)",
      (preview, http.writes(), http.verbs()), (None, [], ["invites_from"]))
check("newest first, both counted", ([i["code"] for i in result["invites"]],
                                     result["count"]), (["forever1", "aB3dE9f"], 2))
check("an invite: code, link, channel, inviter, uses, expiry and creation on both "
      "clocks, temporary", result["invites"][1],
      {"code": "aB3dE9f", "url": "https://discord.gg/aB3dE9f", "channel": "#asd",
       "channel_id": ASD, "inviter": "Alice", "uses": 1, "max_uses": 5,
       "expires": {"local": "Fri 2026-10-30 19:40 EDT", "utc": "2026-10-30T23:40:00Z"},
       "temporary": False,
       "created": {"local": "Fri 2026-10-30 19:30 EDT", "utc": "2026-10-30T23:30:00Z"},
       "vanity": False})
check("no age limit reads 'never', unlimited uses is 0, temporary membership true",
      {k: result["invites"][0][k] for k in ("expires", "max_uses", "temporary",
                                             "inviter")},
      {"expires": "never", "max_uses": 0, "temporary": True, "inviter": "benham"})
result, _ = go(cl, "list_invites", {"guild_id": GUILD, "channel_id": WELCOME})
check("a channel filter", ([i["code"] for i in result["invites"]], result["channel"]),
      (["forever1"], "#welcome"))
check("a channel from another server is refused",
      refusal(cl, "list_invites", {"guild_id": GUILD, "channel_id": ELSEWHERE}),
      f"channel_id: there's no channel {ELSEWHERE} in Testing Server that this bot can "
      "see.")
cl, g, http = invited(features=("VANITY_URL",))
http.vanity = "testing"
result, _ = go(cl, "list_invites", {"guild_id": GUILD})
check("a server with a vanity link: it comes first, marked",
      (result["invites"][0]["code"], result["invites"][0]["vanity"],
       result["invites"][0]["channel"], result["count"]), ("testing", True, "#welcome", 3))
cl, g, http = invited(features=("VANITY_URL",))
http.vanity = None
result, _ = go(cl, "list_invites", {"guild_id": GUILD})
check("...the feature but no code set: quietly none", result["count"], 2)
cl, g, http = invited(features=("VANITY_URL",))
http.fail["get_vanity_code"] = discord.HTTPException(_Resp(400), "nope")
result, _ = go(cl, "list_invites", {"guild_id": GUILD})
check("...and a vanity read Discord refuses is quiet too", result["count"], 2)
cl, g, http = fresh()
result, _ = go(cl, "list_invites", {"guild_id": GUILD})
check("no invites is said plainly", (result["count"], result["message"]),
      (0, "No active invites in Testing Server."))
cl, g, http = fresh(bot_perms=SEND)
check("no Manage Server: one sentence, Discord never asked",
      (refusal(cl, "list_invites", {"guild_id": GUILD}), http.calls),
      ("this bot lacks Manage Server in Testing Server, so it can't read its invites - "
       "give its role Manage Server (or Administrator) first.", []))


# =============================================================== revoke_invite
section("revoke_invite - tier 2, always confirmed, a copy kept, read back")
registered("revoke_invite", 2, False, True, True)
before = backups()
cl, g, http = invited()
result, preview = go(cl, "revoke_invite", {"guild_id": GUILD, "code": "aB3dE9f"})
check("the first call is a preview: Discord only read, nothing saved",
      (result, http.writes(), http.verbs(), backups() == before),
      (None, [], ["invites_from"], True))
check("...the summary names the code, the channel and the server",
      preview["summary"], "Revoke invite **aB3dE9f** to **#asd** in **Testing Server**")
check("...the facts, and that the same code can't come back",
      preview["detail"].splitlines(),
      ["Channel: #asd", "Made by: Alice", "Uses: 1 of 5",
       "Expires: Fri 2026-10-30 19:40 EDT = 2026-10-30 23:40 UTC",
       "Temporary membership: no",
       "Its link stops working at once. A revoked invite can't be restored with the "
       "same code - create_invite makes a new one.", "Its details are saved first."])
_, preview = go(cl, "revoke_invite", {"guild_id": GUILD,
                                      "code": "https://discord.gg/forever1"})
check("a discord.gg link works as the code; no limits read as no limit and never",
      [l for l in preview["detail"].splitlines() if l.startswith(("Uses", "Expires",
                                                                   "Temporary"))],
      ["Uses: 12 (no limit)", "Expires: never", "Temporary membership: yes"])

cl, g, http = invited()
before = backups()
result, preview = go(cl, "revoke_invite", {"guild_id": GUILD, "code": "aB3dE9f",
                                           "reason": "stage d"}, force=True)
check("confirmed: the invite list read, the delete with the reason, the list read back",
      http.verbs(), ["invites_from", "delete_invite", "invites_from"])
check("...the delete names the code and carries the reason",
      http.calls[1], ("delete_invite", {"code": "aB3dE9f", "reason": "stage d"}))
check("...the result: gone, how many are left, the backup path",
      {k: result[k] for k in ("status", "code", "channel", "gone", "invites_left")},
      {"status": "invite_revoked", "code": "aB3dE9f", "channel": "#asd", "gone": True,
       "invites_left": 1})
new = sorted(set(backups()) - set(before))
check("one backup, named <guild>-invite-<code>-<local stamp>.json",
      (len(new), new[0].startswith(f"{GUILD}-invite-aB3dE9f-"), new[0].endswith(".json"),
       os.path.basename(result["backup"]) == new[0]), (1, True, True, True))
saved = json.load(open(result["backup"], encoding="utf-8"))
check("...holding the invite's facts and the create_invite call that remakes it",
      (saved["invite"]["code"], saved["invite"]["inviter"], saved["remake"]),
      ("aB3dE9f", "Alice", {"channel_id": ASD, "max_age_seconds": 600, "max_uses": 5}))
cl, g, http = invited()
http.answer_override["delete_invite"] = True          # Discord keeps it
result, _ = go(cl, "revoke_invite", {"guild_id": GUILD, "code": "aB3dE9f"}, force=True)
check("Discord still listing it afterwards: the result says so",
      (result["gone"], result["warning"]),
      (False, "Discord took the revoke, but its invite list still shows aB3dE9f - "
              "check list_invites."))

section("revoke_invite - refused at the preview (and at the confirm), nothing deleted")
cl, g, http = invited()
cl.foreign_invites["xYz123"] = OTHER_GUILD
for code, want in (
        ("nosuchcode", "there's no active invite nosuchcode in Testing Server - "
                       "list_invites has the codes (an expired or used-up invite is "
                       "already gone)."),
        ("AB3DE9F", "there's no active invite AB3DE9F in Testing Server - list_invites "
                    "has the codes (an expired or used-up invite is already gone)."),
        ("xYz123", "invite xYz123 belongs to another server, not Testing Server - this "
                   "bot only revokes a server's own invites."),
        ("not a code!", "code: 'not a code!' doesn't look like an invite code - "
                        "list_invites has them.")):
    for force in (False, True):
        m = refusal(cl, "revoke_invite", {"guild_id": GUILD, "code": code}, force=force)
        check(f"{code!r} ({'confirm' if force else 'preview'}): {want[:40]}...", m, want)
check("...and Discord was never asked to delete anything",
      [c for c in http.writes()], [])
cl, g, http = invited(features=("VANITY_URL",))
check("the server's vanity code is not an invite to revoke",
      refusal(cl, "revoke_invite", {"guild_id": GUILD, "code": "testing"}),
      "testing is Testing Server's vanity link, which isn't revoked like an invite - "
      "it's changed in Server Settings.")
cl, g, http = invited()
_real_dir = capabilities.LOOK_BACKUP_DIR
_blocker = os.path.join(TMP, "blocker")
open(_blocker, "w").close()
capabilities.LOOK_BACKUP_DIR = os.path.join(_blocker, "look_backups")
m = refusal(cl, "revoke_invite", {"guild_id": GUILD, "code": "aB3dE9f"}, force=True)
capabilities.LOOK_BACKUP_DIR = _real_dir
check("no backup, no revoke: one sentence, Discord never asked to delete",
      (m.startswith("I couldn't save the invite aB3dE9f's details first ("),
       m.endswith("Nothing was changed."), "delete_invite" in http.verbs()),
      (True, True, False))
cl, g, http = invited()
http.fail["delete_invite"] = discord.HTTPException(_Resp(429), "You are being rate limited.")
check("a rate limit says so, Nothing was changed",
      refusal(cl, "revoke_invite", {"guild_id": GUILD, "code": "aB3dE9f"}, force=True),
      "Discord is rate-limiting invite revokes right now. Nothing was changed; try again "
      "in a while.")


# =============================================================== prune_members
section("prune_members - tier 3: the dry-run asks how many, the confirm prunes the same")
a = capabilities.REGISTRY["prune_members"]
check("prune_members: tier 3 (allowlist, dry-run, token), needs a guild, never a guest",
      (a.tier, a.destructive, a.needs_confirm, a.needs_guild, a.guest),
      (3, True, True, True, False))
cl, g, http = fresh()
http.prune_count = 4
result, preview = go(cl, "prune_members", {"guild_id": GUILD})
check("the first call is a dry-run: Discord asked for an estimate only, 7 days, no roles",
      (result, http.calls), (None, [("estimate_pruned_members", {"days": 7, "roles": []})]))
check("...the summary and the three sentences",
      (preview["summary"], preview["detail"].splitlines(), preview["count"]),
      ("Prune **Testing Server**: **4 member(s)** inactive 7 day(s) would be kicked",
       ["4 member(s) would be kicked: no roles, no activity for 7 day(s).",
        "Discord doesn't say WHICH members - only how many.",
        "They can rejoin with an invite, but a prune can't be undone."], 4))
cl, g, http = fresh()
http.prune_count = 0
_, preview = go(cl, "prune_members", {"guild_id": GUILD, "days": 30,
                                      "include_role_ids": [str(MEMBERS), MODS, MEMBERS]})
check("days and roles go to the estimate (each role once)",
      http.calls, [("estimate_pruned_members", {"days": 30,
                                                "roles": sorted([str(MEMBERS), str(MODS)])})])
check("...and the preview names the roles counted too",
      preview["detail"].splitlines()[0],
      "0 member(s) would be kicked: no roles (plus members whose only roles are "
      "'Members', 'Mods'), no activity for 30 day(s).")
cl, g, http = fresh()
http.prune_count = 2
result, _ = go(cl, "prune_members", {"guild_id": GUILD, "days": 30,
                                     "include_role_ids": [MEMBERS], "reason": "cleanup"},
               force=True)
check("confirmed: one prune with the same days and roles, the count asked for, the reason",
      http.calls, [("prune_members", {"days": 30, "roles": [str(MEMBERS)],
                                      "compute_prune_count": True, "reason": "cleanup"})])
check("...and the result is Discord's count",
      result, {"status": "pruned", "guild": "Testing Server", "days": 30,
               "included_roles": ["Members"], "pruned": 2})
cl, g, http = fresh()
go(cl, "prune_members", {"guild_id": GUILD}, force=True)
check("no reason given: the audit log still names the face", http.calls[0][1]["reason"],
      capabilities.VIA)

section("prune_members - refused before Discord is asked anything")
cl, g, http = fresh()
for days in (0, 31, -3):
    check(f"days={days}", refusal(cl, "prune_members", {"guild_id": GUILD, "days": days}),
          f"days has to be 1 to 30 - Discord counts a prune's inactivity in whole days "
          f"up to 30, and {days} isn't in that range.")
check("an unknown role",
      refusal(cl, "prune_members", {"guild_id": GUILD, "include_role_ids": [999]}),
      "include_role_ids: there's no role 999 in Testing Server - list_roles has the ids.")
check("@everyone",
      refusal(cl, "prune_members", {"guild_id": GUILD, "include_role_ids": [EVERYONE]}),
      "include_role_ids: @everyone isn't needed - members with no roles are always "
      "counted.")
check("...not a role id at all",
      refusal(cl, "prune_members", {"guild_id": GUILD, "include_role_ids": ["mods"]}),
      "include_role_ids takes role ids - 'mods' isn't one (list_roles has them).")
check("...and none of those reached Discord", http.calls, [])
cl, g, http = fresh(bot_perms=KICK)
check("Kick Members without Manage Server is not enough",
      refusal(cl, "prune_members", {"guild_id": GUILD}),
      "this bot needs both Kick Members and Manage Server in Testing Server to prune it "
      "- give its role both (or Administrator) first.")
cl, g, http = fresh()
m = refusal(cl, "prune_members", {"guild_id": OTHER_GUILD})
check("a server off the destructive allowlist: refused before the dry-run",
      (m is not None and "destructive_guilds allowlist" in m, http.calls), (True, []))


# ===================================================== set_channel_permissions
section("set_channel_permissions - user_id joins role_id; exactly one of the two")
a = capabilities.REGISTRY["set_channel_permissions"]
check("its flags did not move: tier 2, outward, taints, not always-confirmed",
      (a.tier, a.outward, a.taints, a.always_confirm, a.needs_guild),
      (2, True, True, False, False))
check("role_id is no longer required, its words unchanged; user_id is new",
      (a.params["role_id"], a.params["user_id"]["type"], a.params["channel_id"]),
      ({"type": "int", "desc": "Role to set the overwrite for (list_roles has the ids)"},
       "int", {"type": "int", "required": True}))
cl, g, http = fresh()
for params, want in (
        ({"role_id": MEMBERS, "user_id": ALICE, "allow": ["view_channel"]},
         "give role_id or user_id, not both - one overwrite is set per call, for a role "
         "or for one member."),
        ({"allow": ["view_channel"]},
         "say whose overwrite this is: role_id for a role, or user_id for one member.")):
    check(f"{sorted(k for k in params if k.endswith('_id'))}: one sentence, nothing sent",
          (refusal(cl, "set_channel_permissions", {"channel_id": BETA, **params}),
           http.calls), (want, []))
check("not a member of the server: refused, nothing sent",
      (refusal(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": CAROL,
                                               "allow": ["view_channel"]}),
       http.writes()),
      (f"user {CAROL} is not a member of Testing Server", []))

cl, g, http = fresh()
result, preview = go(cl, "set_channel_permissions", {
    "channel_id": BETA, "role_id": MEMBERS, "allow": ["view_channel"],
    "deny": ["send_tts_messages"]})
check("the role path is as it was: one call with both halves, no read-back",
      [v for v, _ in http.calls], ["edit_channel_permissions"])
check("...for the role, allow and deny together",
      (http.calls[0][1]["target"], http.calls[0][1]["type"], http.calls[0][1]["allow"],
       http.calls[0][1]["deny"], result["role"]),
      (MEMBERS, 0, VIEW, 1 << 12, "Members"))

section("set_channel_permissions - a member: allows applied and READ BACK before any deny")
cl, g, http = fresh()
result, preview = go(cl, "set_channel_permissions", {
    "channel_id": BETA, "user_id": ALICE, "allow": ["view_channel", "embed_links"],
    "deny": ["send_tts_messages"], "reason": "stage d"})
check("no preview from a clean owner turn (the flags didn't move)", preview, None)
check("THE ORDER: member looked up, allow sent, read back, deny sent, read back",
      [v for v, _ in http.calls],
      ["get_member", "edit_channel_permissions", "get_channel",
       "edit_channel_permissions", "get_channel"])
first, second = http.calls[1][1], http.calls[3][1]
check("the first write carries the allows and NO deny, for the member (type 1)",
      (first["target"], first["type"], first["allow"], first["deny"], first["reason"]),
      (ALICE, 1, VIEW | EMBED, 0, "stage d"))
check("the second keeps the allows Discord read back and adds the deny",
      (second["allow"], second["deny"]), (VIEW | EMBED, 1 << 12))
check("the result: each step with what Discord read",
      result["steps"],
      ["1. allowed view_channel, embed_links - Discord reads: allows embed_links, "
       "view_channel",
       "2. denied send_tts_messages - Discord reads: allows embed_links, view_channel; "
       "denies send_tts_messages"])
check("...the member by name, before and after, and the order said",
      (result["member"], result["before"], result["after"], result["message"]),
      ("Alice", {"allow": [], "deny": []},
       {"allow": ["embed_links", "read_messages"], "deny": ["send_tts_messages"]},
       "**Alice** in **#benham-beta** (Testing Server): allowed `view_channel`, "
       "`embed_links` and denied `send_tts_messages` - the allows were applied and read "
       "back before the deny."))
check("...and no warning", "warning" in result, False)

cl, g, http = fresh()
go(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                   "deny": ["send_tts_messages"]})
check("a deny alone: one write, then the read-back",
      [v for v, _ in http.calls], ["get_member", "edit_channel_permissions", "get_channel"])
cl, g, http = fresh()
http._channel(BETA)["permission_overwrites"] = [
    {"id": str(ALICE), "type": 1, "allow": "0", "deny": str(EMBED)}]
g = resync(cl, g)
result, _ = go(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                               "allow": ["embed_links"]})
check("merged onto an existing overwrite: a denied permission flips to allowed",
      (result["before"], result["after"]),
      ({"allow": [], "deny": ["embed_links"]}, {"allow": ["embed_links"], "deny": []}))

cl, g, http = fresh()


def _drops_view(ow):
    ow["allow"] = str(int(ow["allow"]) & ~VIEW)       # Discord doesn't keep it


http.answer_override["edit_channel_permissions"] = _drops_view
m = refusal(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                            "allow": ["view_channel"],
                                            "deny": ["send_tts_messages"]})
check("THE TRAP: an allow that doesn't read back stops it - the deny is never sent",
      (m, [v for v, _ in http.calls]),
      ("Discord took the allow for Alice in #benham-beta, but the overwrite it reads back "
       "doesn't allow view_channel, so the deny was NOT applied - check channel_info.",
       ["get_member", "edit_channel_permissions", "get_channel"]))
cl, g, http = fresh()
_n = []


def _second_refused(ow):
    _n.append(1)
    if len(_n) == 2:
        raise discord.Forbidden(_Resp(403), "Missing Permissions")


http.answer_override["edit_channel_permissions"] = _second_refused
m = refusal(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                            "allow": ["view_channel"],
                                            "deny": ["send_tts_messages"]})
check("Discord refusing the deny after the allow landed: says exactly what landed",
      m, "the allows for Alice in #benham-beta landed and read back (allows "
         "view_channel), but Discord refused the deny: Missing Permissions. The deny was "
         "not applied.")

cl, g, http = fresh()
http._channel(BETA)["permission_overwrites"] = [
    {"id": str(ALICE), "type": 1, "allow": str(VIEW), "deny": str(1 << 12)}]
g = resync(cl, g)
result, _ = go(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                               "reset": True})
check("reset: the overwrite deleted, then read back clean",
      ([v for v, _ in http.calls], result["after"], result["status"]),
      (["get_member", "delete_channel_permissions", "get_channel"],
       {"allow": [], "deny": []}, "reset"))
check("...and said in words", result["message"],
      "Cleared **Alice**'s overwrite in **#benham-beta** (Testing Server) - they now have "
      "the permissions their roles give them.")

section("set_channel_permissions - a member: refused before anything is sent")
cl, g, http = fresh(bot_perms=MANAGE_ROLES | VIEW | SEND)
check("an allow this bot doesn't hold itself",
      (refusal(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                               "allow": ["embed_links"]}),
       http.writes()),
      ("this bot doesn't hold embed_links in #benham-beta itself, and Discord won't let "
       "it grant a member a permission it lacks. Nothing was changed.", []))
check("this bot denying itself view_channel without Administrator",
      (refusal(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": BOT,
                                               "deny": ["view_channel"]}),
       http.writes()),
      ("denying this bot its own view_channel in #benham-beta would lock it out of that "
       "channel's overwrites, so it won't. Nothing was changed.", []))
cl, g, http = fresh()
go(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": BOT,
                                   "allow": ["view_channel"], "deny": ["send_tts_messages"]})
check("...with Administrator it may (the live test's shape): the bot is cached, no fetch",
      [v for v, _ in http.calls],
      ["edit_channel_permissions", "get_channel", "edit_channel_permissions",
       "get_channel"])
cl, g, http = fresh()
for params, want in (
        ({"allow": ["view_channel"], "deny": ["read_messages"]},
         "cannot allow and deny the same permission: view_channel/read_messages (the same "
         "permission)"),
        ({}, "nothing to change - pass `allow`, `deny`, or reset=true")):
    check(f"{params or 'nothing named'}: refused like the role path",
          refusal(cl, "set_channel_permissions", {"channel_id": BETA, "user_id": ALICE,
                                                  **params}), want)
check("...nothing written", http.writes(), [])


# ========================================================== list_automod_rules
section("list_automod_rules - every rule: what it matches, does, spares; the slots")
registered("list_automod_rules", 0, False, False, True)


def am_rule(rid, name, trigger_type, metadata, actions, creator=BOT, enabled=True,
            exempt_roles=(), exempt_channels=(), event_type=1):
    return {"id": str(rid), "guild_id": str(GUILD), "name": name,
            "creator_id": str(creator), "event_type": event_type,
            "trigger_type": trigger_type, "trigger_metadata": metadata,
            "actions": actions, "enabled": enabled,
            "exempt_roles": [str(r) for r in exempt_roles],
            "exempt_channels": [str(c) for c in exempt_channels]}


def ruled(**gkw):
    cl, g, http = fresh(**gkw)
    http.rules = [
        am_rule(7001, "No slurs", 1,
                {"keyword_filter": ["badword", "worse word"], "regex_patterns": ["b+a+d"],
                 "allow_list": ["badminton"]},
                [{"type": 1, "metadata": {"custom_message": "Not here, please."}},
                 {"type": 2, "metadata": {"channel_id": str(WELCOME)}},
                 {"type": 3, "metadata": {"duration_seconds": 600}}],
                exempt_roles=[MODS], exempt_channels=[BETA]),
        am_rule(7002, "Discord lists", 4, {"presets": [1, 3], "allow_list": []},
                [{"type": 1, "metadata": {}}], creator=ALICE),
        am_rule(7003, "Ping storms", 5, {"mention_total_limit": 8,
                                         "mention_raid_protection_enabled": True},
                [{"type": 1, "metadata": {}}], enabled=False),
        am_rule(7004, "Names", 6, {"keyword_filter": ["spammer"], "regex_patterns": [],
                                   "allow_list": []},
                [{"type": 4, "metadata": {}}], event_type=2),
    ]
    return cl, g, http


cl, g, http = ruled()
result, preview = go(cl, "list_automod_rules", {"guild_id": GUILD})
check("a read: no preview, nothing written, one fetch",
      (preview, http.writes(), http.verbs()), (None, [], ["get_auto_moderation_rules"]))
check("four rules, and the slots each trigger uses of Discord's caps",
      (result["count"], result["slots"]),
      (4, {"keyword": {"used": 1, "limit": 6},
           "keyword_preset": {"used": 1, "limit": 1}, "spam": {"used": 0, "limit": 1},
           "mention_spam": {"used": 1, "limit": 1},
           "member_profile": {"used": 1, "limit": 1}}))
check("a keyword rule: its lists, its three actions, who it spares, who made it",
      result["rules"][0],
      {"id": 7001, "name": "No slurs", "enabled": True, "trigger": "keyword",
       "event": "message_send", "keywords": ["badword", "worse word"],
       "regex_patterns": ["b+a+d"], "allow_list": ["badminton"],
       "actions": [{"type": "block_message", "custom_message": "Not here, please."},
                   {"type": "send_alert_message", "channel": "#welcome",
                    "channel_id": WELCOME},
                   {"type": "timeout", "seconds": 600, "duration": "10m"}],
       "exempt_roles": [{"id": MODS, "name": "Mods"}],
       "exempt_channels": [{"id": BETA, "name": "#benham-beta"}], "creator": "benham"})
check("a preset rule: Discord's lists by name; a maker not in the cache reads null",
      {k: result["rules"][1][k] for k in ("trigger", "presets", "allow_list", "creator")},
      {"trigger": "keyword_preset", "presets": ["profanity", "slurs"], "allow_list": [],
       "creator": None})
check("a mention rule: the limit and raid protection; a rule that's off says so",
      {k: result["rules"][2][k] for k in ("trigger", "mention_limit",
                                           "mention_raid_protection", "enabled")},
      {"trigger": "mention_spam", "mention_limit": 8, "mention_raid_protection": True,
       "enabled": False})
check("a profile rule: on member updates, blocking the member's interactions",
      {k: result["rules"][3][k] for k in ("trigger", "event", "keywords", "actions")},
      {"trigger": "member_profile", "event": "member_update", "keywords": ["spammer"],
       "actions": [{"type": "block_member_interactions"}]})
check("...and plain JSON", json.loads(json.dumps(result)) == result, True)
cl, g, http = fresh()
result, _ = go(cl, "list_automod_rules", {"guild_id": GUILD})
check("no rules is said plainly", (result["count"], result["message"]),
      (0, "No AutoMod rules in Testing Server."))
cl, g, http = fresh()
http.fail["get_auto_moderation_rules"] = discord.NotFound(_Resp(404), "none")
check("...and discord.py's NotFound for a server with none is an empty list",
      go(cl, "list_automod_rules", {"guild_id": GUILD})[0]["count"], 0)
cl, g, http = fresh(bot_perms=SEND)
check("no Manage Server: one sentence, Discord never asked",
      (refusal(cl, "list_automod_rules", {"guild_id": GUILD}), http.calls),
      ("this bot lacks Manage Server in Testing Server, so it can't read its AutoMod "
       "rules - give its role Manage Server (or Administrator) first.", []))


# ========================================================= create_automod_rule
section("create_automod_rule - tier 2, outward, always confirmed; the whole rule shown")
registered("create_automod_rule", 2, True, True, True)
LIVE_SHAPE = {"guild_id": GUILD, "name": "stage-d-test", "trigger": "keyword",
              "keywords": ["stage-d-blocked-xyz"], "block_message": True,
              "custom_message": "stage D test"}
cl, g, http = fresh()
result, preview = go(cl, "create_automod_rule", LIVE_SHAPE)
check("the first call is a preview: Discord only read the rules",
      (result, http.verbs()), (None, ["get_auto_moderation_rules"]))
check("...the summary names the rule and the server",
      preview["summary"], "Make the AutoMod rule **stage-d-test** in **Testing Server**")
check("...and the whole rule as it will be made, the slot count, the way back",
      preview["detail"].splitlines(),
      ['Name: "stage-d-test"', "Trigger: keyword - messages with these words or patterns",
       'Keywords: (1) "stage-d-blocked-xyz"', "Regex patterns: none", "Allow list: none",
       'Actions: block the message, telling the sender "stage D test"',
       "Exempt roles: none", "Exempt channels: none", "Enabled: yes",
       "It acts on everyone's messages as soon as it's made, except the exempt roles "
       "and channels.",
       "That makes 1 of 6 keyword rule(s) Discord allows; delete_automod_rule removes it "
       "again."])

cl, g, http = fresh()
result, _ = go(cl, "create_automod_rule", {**LIVE_SHAPE, "reason": "stage d"}, force=True)
check("confirmed: rules read, the rule created, then fetched back",
      http.verbs(), ["get_auto_moderation_rules", "create_auto_moderation_rule",
                     "get_auto_moderation_rule"])
sent = http.calls[1][1]
check("...what went to Discord: name, message_send, keyword, the list, the action, on",
      {k: sent[k] for k in ("name", "event_type", "trigger_type", "trigger_metadata",
                            "actions", "enabled", "reason")},
      {"name": "stage-d-test", "event_type": 1, "trigger_type": 1,
       "trigger_metadata": {"keyword_filter": ["stage-d-blocked-xyz"],
                            "regex_patterns": [], "allow_list": []},
       "actions": [{"type": 1, "metadata": {"custom_message": "stage D test"}}],
       "enabled": True, "reason": "stage d"})
check("...no exempt lists sent when there are none",
      ("exempt_roles" in sent, "exempt_channels" in sent), (False, False))
check("...the result is the rule Discord reads back",
      (result["status"], result["rule"]["name"], result["rule"]["keywords"],
       result["rule"]["actions"], result["rule"]["creator"], "warning" in result),
      ("automod_rule_created", "stage-d-test", ["stage-d-blocked-xyz"],
       [{"type": "block_message", "custom_message": "stage D test"}], "benham", False))

cl, g, http = fresh()
go(cl, "create_automod_rule", {
    "guild_id": GUILD, "name": "Everything", "trigger": "keyword",
    "keywords": ["a", "b", "a"], "regex_patterns": ["x+"], "allow_list": ["ab"],
    "custom_message": "no", "alert_channel_id": str(WELCOME), "timeout_seconds": 3600,
    "exempt_role_ids": [MODS, str(MEMBERS)], "exempt_channel_ids": [BETA],
    "enabled": False}, force=True)
sent = http.calls[1][1]
check("every field: the lists deduped, three actions in order, exempts, off",
      {k: sent[k] for k in ("trigger_metadata", "actions", "exempt_roles",
                            "exempt_channels", "enabled")},
      {"trigger_metadata": {"keyword_filter": ["a", "b"], "regex_patterns": ["x+"],
                            "allow_list": ["ab"]},
       "actions": [{"type": 1, "metadata": {"custom_message": "no"}},
                   {"type": 2, "metadata": {"channel_id": str(WELCOME)}},
                   {"type": 3, "metadata": {"duration_seconds": 3600}}],
       "exempt_roles": [str(MEMBERS), str(MODS)], "exempt_channels": [str(BETA)],
       "enabled": False})
for params, words in (
        ({"trigger": "keyword_preset", "presets": ["Slurs", "profanity"],
          "allow_list": ["darn"]},
         {"trigger_type": 4, "trigger_metadata": {"presets": [1, 3],
                                                  "allow_list": ["darn"]}}),
        ({"trigger": "spam"}, {"trigger_type": 3}),
        ({"trigger": "mention_spam", "mention_limit": 8, "timeout_seconds": 60},
         {"trigger_type": 5, "trigger_metadata": {"mention_total_limit": 8,
                                                  "mention_raid_protection_enabled":
                                                      False}}),
        ({"trigger": "member_profile", "keywords": ["spammer"]},
         {"event_type": 2, "trigger_type": 6,
          "actions": [{"type": 4, "metadata": {}}]})):
    cl, g, http = fresh()
    go(cl, "create_automod_rule", {"guild_id": GUILD, "name": "r", **params}, force=True)
    sent = http.calls[1][1]
    check(f"a {params['trigger']} rule goes to Discord as {sorted(words)}",
          {k: sent.get(k) for k in words}, words)
cl, g, http = fresh()
_, preview = go(cl, "create_automod_rule", {"guild_id": GUILD, "name": "Names",
                                            "trigger": "member_profile",
                                            "keywords": ["spammer"]})
check("a member_profile preview: profiles, and the block reads as interactions",
      [l for l in preview["detail"].splitlines() if l.startswith(("Trigger", "Actions",
                                                                  "It acts"))],
      ["Trigger: member_profile - member names and profiles with these words or patterns",
       "Actions: block the member from chatting, talking and other interactions until "
       "their profile passes",
       "It acts on everyone's profiles as soon as it's made, except the exempt roles and "
       "channels."])

section("create_automod_rule - refused at the preview (and at the confirm), nothing sent")
cl, g, http = ruled()


def am_refused(params, client=None):
    out = []
    for force in (False, True):
        c, gg, h = (client or ruled())
        m = refusal(c, "create_automod_rule", {"guild_id": GUILD, "name": "new rule",
                                               **params}, force=force)
        out.append((m, "create_auto_moderation_rule" in h.verbs()))
    (m1, s1), (m2, s2) = out
    return m1 if (m1 == m2 and not s1 and not s2) else ("MISMATCH", out)


for params, want in (
        ({"trigger": "regex"}, "trigger is one of keyword, keyword_preset, spam, "
                               "mention_spam, member_profile, and 'regex' isn't one of "
                               "those."),
        ({"trigger": "keyword_preset", "presets": ["rude"]},
         "presets: 'rude' isn't one of Discord's word lists - they are profanity, "
         "sexual_content and slurs."),
        ({"trigger": "spam", "keywords": ["x"]},
         "keywords doesn't fit a spam rule - a spam rule uses Discord's own spam "
         "detection and takes no list."),
        ({"trigger": "mention_spam", "mention_limit": 5, "presets": ["slurs"]},
         "presets doesn't fit a mention_spam rule - presets fits keyword_preset rules."),
        ({"trigger": "keyword", "keywords": ["x"], "mention_limit": 5},
         "mention_limit doesn't fit a keyword rule - mention_limit fits mention_spam "
         "rules."),
        ({"trigger": "keyword", "keywords": ["x"], "block_message": False},
         "a rule with no action does nothing - turn block_message on, or give "
         "alert_channel_id or timeout_seconds."),
        ({"trigger": "keyword"}, "a keyword rule needs at least one keyword or regex "
                                 "pattern to match."),
        ({"trigger": "keyword_preset"}, "a keyword_preset rule needs at least one of "
                                        "Discord's word lists: profanity, sexual_content "
                                        "or slurs."),
        ({"trigger": "mention_spam", "mention_limit": 51},
         "a mention_spam rule needs mention_limit, 1 to 50 mentions per message."),
        ({"trigger": "keyword", "keywords": ["x"], "timeout_seconds": 2419201},
         "timeout_seconds has to be 1 to 2419200 (28 days), or 0 for no timeout."),
        ({"trigger": "member_profile", "keywords": ["x"], "timeout_seconds": 60},
         "a timeout only goes on keyword and mention_spam rules - Discord doesn't allow "
         "one on a member_profile rule."),
        ({"trigger": "member_profile", "keywords": ["x"], "custom_message": "hi"},
         "custom_message is shown to someone whose message was blocked, and a "
         "member_profile rule blocks members, not messages."),
        ({"trigger": "keyword", "keywords": ["x"], "block_message": False,
          "alert_channel_id": str(WELCOME), "custom_message": "hi"},
         "custom_message needs block_message on - it's what the sender sees when their "
         "message is blocked."),
        ({"trigger": "keyword", "keywords": ["x"], "custom_message": "y" * 151},
         "custom_message is 151 characters, and Discord allows 150."),
        ({"trigger": "keyword", "keywords": ["x" * 61]},
         'keywords: "' + "x" * 30 + '..." is 61 characters, and Discord allows 60 per '
         "entry."),
        ({"trigger": "keyword", "regex_patterns": [f"r{i}" for i in range(11)]},
         "regex_patterns has 11 entries, and Discord allows 10 on one rule."),
        ({"trigger": "keyword", "keywords": ["x"], "allow_list": [f"w{i}" for i in
                                                                 range(101)]},
         "allow_list has 101 entries, and Discord allows 100 on one rule."),
        ({"trigger": "keyword", "keywords": [{"keyword": "x"}]},
         'keywords is a list of plain words or phrases, like ["word", "two words"] - '
         "{'keyword': 'x'} isn't one."),
        ({"trigger": "keyword", "keywords": ["x"], "alert_channel_id": str(VOICE)},
         "alert_channel_id: alerts post in a text channel, and #General is a voice "
         "channel."),
        ({"trigger": "keyword", "keywords": ["x"], "alert_channel_id": str(ELSEWHERE)},
         f"alert_channel_id: channel {ELSEWHERE} belongs to another server, not Testing "
         "Server."),
        ({"trigger": "keyword", "keywords": ["x"], "exempt_role_ids": [999]},
         "exempt_role_ids: there's no role 999 in Testing Server - list_roles has the "
         "ids."),
        ({"trigger": "keyword", "keywords": ["x"], "exempt_channel_ids": [999]},
         "exempt_channel_ids: there's no channel 999 in Testing Server that this bot can "
         "see."),
        ({"trigger": "keyword", "keywords": ["Zebulon"]},
         "keywords carries a personal term the privacy screen keeps on this machine, and "
         "rewriting it would change what the rule matches - so it isn't sent. Nothing "
         "was changed."),
        ({"trigger": "keyword", "keywords": ["x"], "name": "no SLURS"},
         'Testing Server already has an AutoMod rule named "no SLURS" - pick another '
         "name."),
        ({"trigger": "keyword_preset", "presets": ["slurs"]},
         "Testing Server already has 1 of 1 keyword_preset rule(s) Discord allows - edit "
         "or delete one first."),
        ({"trigger": "mention_spam", "mention_limit": 5},
         "Testing Server already has 1 of 1 mention_spam rule(s) Discord allows - edit "
         "or delete one first.")):
    m = am_refused(params)
    check(f"{want[:60]}...", m, want)
    check("...one sentence", one_sentence(m) if isinstance(m, str) else False, True)


def six_keyword_rules():
    cl, g, http = fresh()
    http.rules = [am_rule(7100 + i, f"k{i}", 1, {"keyword_filter": ["x"],
                                                 "regex_patterns": [], "allow_list": []},
                          [{"type": 1, "metadata": {}}]) for i in range(6)]
    return cl, g, http


check("the seventh keyword rule: N of M said",
      am_refused({"trigger": "keyword", "keywords": ["y"]}, six_keyword_rules()),
      "Testing Server already has 6 of 6 keyword rule(s) Discord allows - edit or delete "
      "one first.")
cl, g, http = fresh(bot_perms=MANAGE_GUILD)
check("a timeout without Timeout Members", refusal(cl, "create_automod_rule", {
    "guild_id": GUILD, "name": "t", "trigger": "keyword", "keywords": ["x"],
    "timeout_seconds": 60}),
    "a timeout needs this bot to hold Timeout Members in Testing Server, and it doesn't.")
cl, g, http = fresh(bot_perms=SEND)
check("no Manage Server: one sentence, Discord never asked",
      (refusal(cl, "create_automod_rule", LIVE_SHAPE), http.calls),
      ("this bot lacks Manage Server in Testing Server, so it can't make AutoMod rules - "
       "give its role Manage Server (or Administrator) first.", []))
cl, g, http = fresh()
http.fail["create_auto_moderation_rule"] = discord.HTTPException(_Resp(400),
                                                                 "Invalid Form Body")
check("Discord's own refusal is one sentence, Nothing was changed",
      refusal(cl, "create_automod_rule", LIVE_SHAPE, force=True),
      "Discord turned the AutoMod rule create down: Invalid Form Body. Nothing was "
      "changed.")
cl, g, http = fresh()
http.fail["get_auto_moderation_rule"] = discord.HTTPException(_Resp(500), "boom")
result, _ = go(cl, "create_automod_rule", LIVE_SHAPE, force=True)
check("a read-back that fails after the create: Discord's answer shown, and said",
      (result["rule"]["name"], result["warning"].startswith("Discord took it, but I "
                                                            "couldn't read the rule back")),
      ("stage-d-test", True))


# =========================================================== edit_automod_rule
section("edit_automod_rule - tier 2, outward, always confirmed; old -> new, backed up")
registered("edit_automod_rule", 2, True, True, True)
STAGE_RULE = am_rule(7200, "stage-d-test", 1, {"keyword_filter": ["stage-d-blocked-xyz"],
                                               "regex_patterns": [], "allow_list": []},
                     [{"type": 1, "metadata": {"custom_message": "stage D test"}}])


def staged():
    cl, g, http = ruled()
    http.rules.append(copy.deepcopy(STAGE_RULE))
    return cl, g, http


EDIT = {"guild_id": GUILD, "rule_id": "stage-d-test",
        "keywords": ["stage-d-blocked-xyz", "stage-d-second"], "enabled": False}
cl, g, http = staged()
before = backups()
result, preview = go(cl, "edit_automod_rule", EDIT)
check("the first call is a preview: Discord only read, nothing saved",
      (result, http.verbs(), backups() == before),
      (None, ["get_auto_moderation_rules"], True))
check("...the summary names the rule and what changes",
      preview["summary"],
      "Change the AutoMod rule **stage-d-test** in **Testing Server**: Enabled, Keywords")
check("...each changed field old -> new, a list said to be replaced, nothing else",
      preview["detail"].splitlines(),
      ["Enabled: on -> off",
       'Keywords: (1) "stage-d-blocked-xyz" -> (2) "stage-d-blocked-xyz", '
       '"stage-d-second" (the list is replaced)',
       "Nothing else changes.", "Turned off, it stops acting at once.",
       "The rule as it is now is saved first, so this can be put back."])
cl, g, http = staged()
_, preview = go(cl, "edit_automod_rule", {"guild_id": GUILD, "rule_id": "7200",
                                          "custom_message": "none",
                                          "alert_channel_id": str(WELCOME),
                                          "timeout_seconds": 300,
                                          "exempt_role_ids": [MODS]})
check("by id; actions and exempts in words",
      preview["detail"].splitlines()[:4],
      ['Note to the sender: "stage D test" -> none', "Alert channel: none -> #welcome",
       "Timeout: none -> 5m", "Exempt roles: none -> Mods (the list is replaced)"])

cl, g, http = staged()
before = backups()
result, _ = go(cl, "edit_automod_rule", {**EDIT, "reason": "stage d"}, force=True)
check("confirmed: rules read, the edit, the rule fetched back",
      http.verbs(), ["get_auto_moderation_rules", "edit_auto_moderation_rule",
                     "get_auto_moderation_rule"])
check("...only the changed parts went to Discord, the whole new keyword list among them",
      http.calls[1][1], {"trigger_metadata": {"keyword_filter": ["stage-d-blocked-xyz",
                                                                 "stage-d-second"],
                                              "regex_patterns": [], "allow_list": []},
                         "enabled": False, "reason": "stage d"})
check("...the result is the rule Discord reads, and what changed",
      (result["status"], result["changed"], result["rule"]["enabled"],
       result["rule"]["keywords"], "warning" in result),
      ("automod_rule_edited", ["Enabled", "Keywords"], False,
       ["stage-d-blocked-xyz", "stage-d-second"], False))
new = sorted(set(backups()) - set(before))
check("one backup, named <guild>-automod-<rule id>-<local stamp>.json",
      (len(new), new[0].startswith(f"{GUILD}-automod-7200-"),
       os.path.basename(result["backup"]) == new[0]), (1, True, True))
saved = json.load(open(result["backup"], encoding="utf-8"))
check("...the full rule as Discord had it, and the call that puts it back",
      (saved["rule"]["trigger_metadata"]["keyword_filter"], saved["rule"]["enabled"],
       saved["restore"]),
      (["stage-d-blocked-xyz"], True,
       {"rule_id": "7200", "enabled": True, "keywords": ["stage-d-blocked-xyz"]}))
cl, g, http = staged()
go(cl, "edit_automod_rule", EDIT, force=True)
_, back = go(cl, "edit_automod_rule", {"guild_id": GUILD, **saved["restore"]})
check("...and after the edit, the restore previews every field back",
      back["detail"].splitlines()[:2],
      ["Enabled: off -> on",
       'Keywords: (2) "stage-d-blocked-xyz", "stage-d-second" -> (1) '
       '"stage-d-blocked-xyz" (the list is replaced)'])
cl, g, http = staged()
go(cl, "edit_automod_rule", {"guild_id": GUILD, "rule_id": "stage-d-test",
                             "alert_channel_id": str(WELCOME), "timeout_seconds": 60},
   force=True)
check("an action change sends the whole new action list",
      http.calls[1][1]["actions"],
      [{"type": 1, "metadata": {"custom_message": "stage D test"}},
       {"type": 2, "metadata": {"channel_id": str(WELCOME)}},
       {"type": 3, "metadata": {"duration_seconds": 60}}])
cl, g, http = staged()


def _stays_on(rule):
    rule["enabled"] = True                      # Discord ignores the switch


http.answer_override["edit_auto_moderation_rule"] = _stays_on
result, _ = go(cl, "edit_automod_rule", EDIT, force=True)
check("Discord's rule disagreeing with the edit: the result says so",
      result["warning"], "Discord took the edit, but the rule doesn't read back as asked: "
                         "Enabled - check list_automod_rules.")

section("edit_automod_rule - refused at the preview (and at the confirm), nothing sent")


def edit_refused(params, client=None):
    out = []
    for force in (False, True):
        c, gg, h = (client or staged())
        m = refusal(c, "edit_automod_rule", {"guild_id": GUILD, **params}, force=force)
        out.append((m, "edit_auto_moderation_rule" in h.verbs()))
    (m1, s1), (m2, s2) = out
    return m1 if (m1 == m2 and not s1 and not s2) else ("MISMATCH", out)


for params, want in (
        ({"rule_id": "stage-d-test"},
         "nothing to change - pass name, keywords, regex_patterns, allow_list, presets, "
         "mention_limit, block_message, custom_message, alert_channel_id, "
         "timeout_seconds, exempt_role_ids, exempt_channel_ids or enabled."),
        ({"rule_id": "stage-d-test", "keywords": ["stage-d-blocked-xyz"], "enabled": True},
         "nothing would change - the AutoMod rule 'stage-d-test' already reads that way."),
        ({"rule_id": "nope", "enabled": False},
         "there's no AutoMod rule 'nope' in Testing Server - list_automod_rules has the "
         "ids and names."),
        ({"rule_id": "Ping storms", "keywords": ["x"]},
         "keywords doesn't fit a mention_spam rule - keywords fits keyword and "
         "member_profile rules."),
        ({"rule_id": "7002", "timeout_seconds": 60},
         "a timeout only goes on keyword and mention_spam rules - Discord doesn't allow "
         "one on a keyword_preset rule."),
        ({"rule_id": "stage-d-test", "keywords": [], "regex_patterns": []},
         "a keyword rule needs at least one keyword or regex pattern to match."),
        ({"rule_id": "stage-d-test", "block_message": False},
         "custom_message needs block_message on - it's what the sender sees when their "
         "message is blocked."),
        ({"rule_id": "stage-d-test", "block_message": False, "custom_message": ""},
         "a rule with no action does nothing - turn block_message on, or give "
         "alert_channel_id or timeout_seconds."),
        ({"rule_id": "stage-d-test", "name": "Names"},
         'Testing Server already has an AutoMod rule named "Names" - pick another name.'),
        ({"rule_id": "stage-d-test", "allow_list": ["Zebulon Quartermaine"]},
         "allow_list carries a personal term the privacy screen keeps on this machine, "
         "and rewriting it would change what the rule matches - so it isn't sent. "
         "Nothing was changed."),
        ({"rule_id": "Discord lists", "presets": ["everything"]},
         "presets: 'everything' isn't one of Discord's word lists - they are profanity, "
         "sexual_content and slurs.")):
    m = edit_refused(params)
    check(f"{want[:60]}...", m, want)
cl, g, http = staged()
http.rules.append(am_rule(7300, "stage-d-test", 1, {"keyword_filter": ["y"],
                                                    "regex_patterns": [],
                                                    "allow_list": []},
                          [{"type": 1, "metadata": {}}]))
check("a name two rules share: asks for the id",
      edit_refused({"rule_id": "stage-d-test", "enabled": False}, (cl, g, http)),
      "2 AutoMod rules in Testing Server are named 'stage-d-test' - give the rule's id "
      "instead (list_automod_rules has them).")
cl, g, http = staged()
result, _ = go(cl, "edit_automod_rule", {"guild_id": GUILD, "rule_id": "7200",
                                         "name": "STAGE-D-TEST"}, force=True)
check("renaming a rule to its own name in other letters is not a clash",
      result["rule"]["name"], "STAGE-D-TEST")
cl, g, http = staged()
_real_dir = capabilities.LOOK_BACKUP_DIR
capabilities.LOOK_BACKUP_DIR = os.path.join(_blocker, "look_backups")
m = refusal(cl, "edit_automod_rule", EDIT, force=True)
capabilities.LOOK_BACKUP_DIR = _real_dir
check("no backup, no edit: one sentence, Discord never asked to change it",
      (m.startswith("I couldn't save the AutoMod rule 'stage-d-test' first ("),
       m.endswith("Nothing was changed."), "edit_auto_moderation_rule" in http.verbs()),
      (True, True, False))


# ===================================================================== the end
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
