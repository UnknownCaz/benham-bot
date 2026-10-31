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


# ===================================================================== the end
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
