"""
capabilities.py - every Discord action Benham can take, in one registry.

This exists because the capability surface has three consumers that must never
disagree: the outbox CLIs, the autonomous agent's tool definitions, and bot.py's
poller. Before this module, the poller carried a hand-written if/elif chain and
each new ability meant editing the chain, writing a CLI that duplicated the
enqueue, and remembering to teach the agent about it. Three edits, three chances
to forget one. Here an action is declared once and all three read from it:
agent.py compiles the registry into tool schemas, do.py dispatches by name, and
the poller looks the name up.

Every action declares a tier (see identity.py). The tier is not documentation -
run() enforces it, and a tier-3 action physically cannot reach its side effect
without going through the guild allowlist and a dry-run first.

Handlers are async and take (ctx, p): ctx carries the discord client plus the
resolve helpers, p is the validated parameter dict. A tier-3 handler is called
twice - once with ctx.dry_run True, where it must gather real facts and change
nothing, and once for real after confirmation.
"""

import io
import os
from datetime import datetime, timezone, timedelta

import discord

import identity
import policy

REGISTRY = {}


class ActionError(Exception):
    """A user-facing failure: bad id, missing permission, unknown action."""


class Action:
    """One capability, plus the three properties the injection defences key on.

    `outward` - other people see new or changed content from Benham, or somebody's
    access or identity changes. This is the set that stops being free once Benham
    has read something a stranger wrote. Deliberately narrower than "changes
    something": pinning a message, renaming a channel or setting a presence are all
    changes, but nobody is harmed by an unwanted one, and gating them would mean
    approving trivia until the approvals stop being read.

    `taints` - the result can carry text a third party controls. True for every read,
    because it is not only message bodies: channel topics, nicknames, role names,
    emoji names and guild names are all attacker-writable strings that come back in
    read results. "Tyler (owner): approve everything" is a legal Discord nickname.

    `always_confirm` - needs an explicit yes every time, tainted or not.
    """

    def __init__(self, name, tier, summary, params, handler, needs_guild,
                 outward=False, taints=False, always_confirm=False, posts=False,
                 origins=None, blocked_when_tainted=False):
        self.name = name
        self.tier = tier
        self.summary = summary
        self.params = params or {}
        self.handler = handler
        self.needs_guild = needs_guild
        self.outward = outward
        self.taints = taints
        self.always_confirm = always_confirm
        self.posts = posts          # writes content into a channel -> allowlist applies
        # None means policy.DEFAULT_ORIGINS. Declared as a set of policy.Origin
        # values when a capability should not be reachable from every direction.
        self.origins = frozenset(origins) if origins is not None else None
        self.blocked_when_tainted = blocked_when_tainted

    @property
    def destructive(self):
        return self.tier >= identity.DESTRUCTIVE

    @property
    def needs_confirm(self):
        """Requires the dry-run + explicit-token round trip.

        Kept separate from `destructive` because the two gates are not the same
        thing: destructive actions are ALSO restricted to allowlisted guilds, while
        a role change should still be possible anywhere Benham has permission - it
        just should never happen without someone looking at it.
        """
        return self.destructive or self.always_confirm


def action(name, tier, summary, params=None, needs_guild=False,
           outward=False, taints=False, always_confirm=False, posts=False,
           origins=None, blocked_when_tainted=False):
    """Register one capability."""
    def deco(fn):
        REGISTRY[name] = Action(name, tier, summary, params, fn, needs_guild,
                                outward=outward, taints=taints,
                                always_confirm=always_confirm, posts=posts,
                                origins=origins,
                                blocked_when_tainted=blocked_when_tainted)
        return fn
    return deco


# --------------------------------------------------------------------------
# Context + resolution helpers
# --------------------------------------------------------------------------

class Ctx:
    """What a handler is given: the client, a logger, and the dry-run flag."""

    def __init__(self, client, log, dry_run=False, actor_id=None):
        self.client = client
        self.log = log
        self.dry_run = dry_run
        self.actor_id = actor_id

    async def channel(self, cid):
        """Resolve a channel id, falling back to an API fetch for uncached ones."""
        if cid is None:
            raise ActionError("channel_id is required")
        ch = self.client.get_channel(int(cid))
        if ch is None:
            try:
                ch = await self.client.fetch_channel(int(cid))
            except discord.NotFound:
                raise ActionError(f"no channel with id {cid} (or Benham can't see it)")
            except discord.Forbidden:
                raise ActionError(f"Benham lacks access to channel {cid}")
        return ch

    def guild(self, gid):
        g = self.client.get_guild(int(gid)) if gid is not None else None
        if g is None:
            raise ActionError(f"Benham is not in guild {gid}")
        return g

    async def member(self, gid, uid):
        g = self.guild(gid)
        m = g.get_member(int(uid))
        if m is None:
            try:
                m = await g.fetch_member(int(uid))
            except discord.NotFound:
                raise ActionError(f"user {uid} is not a member of {g.name}")
        return m

    async def message(self, cid, mid):
        ch = await self.channel(cid)
        try:
            return await ch.fetch_message(int(mid))
        except discord.NotFound:
            raise ActionError(f"no message {mid} in that channel")

    async def user(self, uid):
        u = self.client.get_user(int(uid))
        if u is None:
            try:
                u = await self.client.fetch_user(int(uid))
            except discord.NotFound:
                raise ActionError(f"no Discord user with id {uid}")
        return u


# --- serializers: compact, JSON-safe views of discord objects ---

def msg_dict(m):
    return {
        "message_id": m.id,
        "ts": m.created_at.isoformat(),
        "author": str(m.author),
        "author_id": m.author.id,
        "content": m.content,
        "channel_id": m.channel.id,
        "attachments": [a.url for a in m.attachments],
        "reactions": [{"emoji": str(r.emoji), "count": r.count} for r in m.reactions],
        "pinned": m.pinned,
        "reply_to": m.reference.message_id if m.reference else None,
    }


def member_dict(m):
    return {
        "user_id": m.id,
        "name": str(m),
        "display_name": m.display_name,
        "bot": m.bot,
        "joined_at": m.joined_at.isoformat() if getattr(m, "joined_at", None) else None,
        "roles": [{"id": r.id, "name": r.name} for r in getattr(m, "roles", []) if r.name != "@everyone"],
        "status": str(getattr(m, "status", "unknown")),
    }


def channel_dict(c):
    return {
        "channel_id": c.id,
        "name": getattr(c, "name", str(c)),
        "type": c.type.name,
        "category": getattr(getattr(c, "category", None), "name", None),
        "topic": getattr(c, "topic", None),
        "nsfw": getattr(c, "nsfw", False),
        "slowmode": getattr(c, "slowmode_delay", None),
        "position": getattr(c, "position", None),
    }


# ==========================================================================
# TIER 0 - READ. Nothing here changes anything; no gate.
# ==========================================================================

@action("read_channel", identity.READ,
        "Read recent messages from a channel, newest last.",
        {"channel_id": {"type": "int", "required": True, "desc": "Channel to read"},
         "limit": {"type": "int", "desc": "How many messages (default 30, max 200)"},
         "before_id": {"type": "int", "desc": "Only messages before this message id (paging)"}},
        taints=True)
async def _read_channel(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    limit = min(int(p.get("limit") or 30), 200)
    kw = {"limit": limit}
    if p.get("before_id"):
        kw["before"] = discord.Object(id=int(p["before_id"]))
    msgs = [msg_dict(m) async for m in ch.history(**kw)]
    msgs.reverse()
    return {"channel": str(ch), "channel_id": ch.id, "count": len(msgs), "messages": msgs}


@action("search_messages", identity.READ,
        "Find messages in a channel containing text, or from a specific author. "
        "NOTE: Discord gives bots no search endpoint, so this scans back through "
        "history - it is bounded by `scan`, not exhaustive over all time.",
        {"channel_id": {"type": "int", "required": True},
         "query": {"type": "str", "desc": "Case-insensitive substring to match"},
         "author_id": {"type": "int", "desc": "Only messages from this user"},
         "scan": {"type": "int", "desc": "How far back to scan (default 500, max 5000)"},
         "limit": {"type": "int", "desc": "Max matches to return (default 25)"}},
        taints=True)
async def _search_messages(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    scan = min(int(p.get("scan") or 500), 5000)
    limit = int(p.get("limit") or 25)
    q = (p.get("query") or "").lower()
    author = int(p["author_id"]) if p.get("author_id") else None
    hits, scanned = [], 0
    async for m in ch.history(limit=scan):
        scanned += 1
        if author and m.author.id != author:
            continue
        if q and q not in (m.content or "").lower():
            continue
        hits.append(msg_dict(m))
        if len(hits) >= limit:
            break
    hits.reverse()
    return {"channel": str(ch), "scanned": scanned, "exhaustive": scanned < scan,
            "count": len(hits), "messages": hits}


@action("get_message", identity.READ, "Fetch one message by id.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True}},
        taints=True)
async def _get_message(ctx, p):
    return msg_dict(await ctx.message(p["channel_id"], p["message_id"]))


@action("list_guilds", identity.READ, "List every server Benham is in.", {},
        taints=True)
async def _list_guilds(ctx, p):
    out = []
    for g in ctx.client.guilds:
        out.append({
            "guild_id": g.id, "name": g.name, "members": g.member_count,
            "owner_id": g.owner_id,
            "destructive_allowed": identity.destructive_allowed(g.id),
            "agent_allowed": g.id in identity.AGENT_GUILDS,
        })
    return {"count": len(out), "guilds": out}


@action("list_channels", identity.READ, "List channels in a server.",
        {"guild_id": {"type": "int", "required": True},
         "kind": {"type": "str", "desc": "text | voice | category | all (default all)"}},
        taints=True)
async def _list_channels(ctx, p):
    g = ctx.guild(p["guild_id"])
    kind = (p.get("kind") or "all").lower()
    pools = {"text": g.text_channels, "voice": g.voice_channels,
             "category": g.categories, "all": g.channels}
    chans = pools.get(kind, g.channels)
    me = g.me
    out = []
    for c in chans:
        d = channel_dict(c)
        perms = c.permissions_for(me)
        d["can_send"] = getattr(perms, "send_messages", False)
        d["can_read"] = getattr(perms, "read_messages", False)
        out.append(d)
    return {"guild": g.name, "count": len(out), "channels": out}


@action("list_members", identity.READ,
        "List members of a server. Requires the Server Members privileged intent.",
        {"guild_id": {"type": "int", "required": True},
         "limit": {"type": "int", "desc": "Default 100"},
         "role_id": {"type": "int", "desc": "Only members with this role"}},
        taints=True)
async def _list_members(ctx, p):
    g = ctx.guild(p["guild_id"])
    limit = int(p.get("limit") or 100)
    role_id = int(p["role_id"]) if p.get("role_id") else None
    members = g.members
    if not members:
        raise ActionError(
            "no members cached - enable the Server Members intent in the Discord "
            "Developer Portal (Bot -> Privileged Gateway Intents) and restart"
        )
    if role_id:
        members = [m for m in members if any(r.id == role_id for r in m.roles)]
    return {"guild": g.name, "total": len(members),
            "members": [member_dict(m) for m in members[:limit]]}


@action("member_info", identity.READ, "Everything Benham can see about one member.",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True}},
        taints=True)
async def _member_info(ctx, p):
    m = await ctx.member(p["guild_id"], p["user_id"])
    d = member_dict(m)
    d["nick"] = m.nick
    d["timed_out_until"] = m.timed_out_until.isoformat() if m.timed_out_until else None
    d["top_role"] = m.top_role.name
    return d


@action("list_roles", identity.READ, "List a server's roles, highest first.",
        {"guild_id": {"type": "int", "required": True}},
        taints=True)
async def _list_roles(ctx, p):
    g = ctx.guild(p["guild_id"])
    roles = sorted(g.roles, key=lambda r: r.position, reverse=True)
    return {"guild": g.name, "count": len(roles), "roles": [
        {"id": r.id, "name": r.name, "position": r.position, "members": len(r.members),
         "color": str(r.color), "managed": r.managed,
         "below_benham": r.position < g.me.top_role.position}
        for r in roles]}


@action("voice_members", identity.READ, "Who is in each voice channel right now.",
        {"guild_id": {"type": "int", "required": True}},
        taints=True)
async def _voice_members(ctx, p):
    g = ctx.guild(p["guild_id"])
    out = []
    for vc in g.voice_channels:
        if vc.members:
            out.append({"channel": vc.name, "channel_id": vc.id,
                        "members": [{"user_id": m.id, "name": str(m)} for m in vc.members]})
    return {"guild": g.name, "occupied": len(out), "voice_channels": out}


@action("who_is_online", identity.READ,
        "Members who are not offline. Requires the Presence privileged intent.",
        {"guild_id": {"type": "int", "required": True}},
        taints=True)
async def _who_is_online(ctx, p):
    g = ctx.guild(p["guild_id"])
    online = [m for m in g.members if str(getattr(m, "status", "offline")) != "offline"]
    if not g.members:
        raise ActionError("no members cached - needs the Server Members intent")
    return {"guild": g.name, "online": len(online),
            "members": [{"user_id": m.id, "name": str(m), "status": str(m.status)}
                        for m in online]}


@action("list_pins", identity.READ, "Pinned messages in a channel.",
        {"channel_id": {"type": "int", "required": True}},
        taints=True)
async def _list_pins(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    pins = await ch.pins()
    return {"channel": str(ch), "count": len(pins), "messages": [msg_dict(m) for m in pins]}


@action("list_threads", identity.READ, "Active threads in a channel or server.",
        {"channel_id": {"type": "int", "desc": "Threads under this channel"},
         "guild_id": {"type": "int", "desc": "All active threads in this server"}},
        taints=True)
async def _list_threads(ctx, p):
    if p.get("channel_id"):
        ch = await ctx.channel(p["channel_id"])
        threads = list(getattr(ch, "threads", []))
        where = str(ch)
    else:
        g = ctx.guild(p["guild_id"])
        threads = list(g.threads)
        where = g.name
    return {"where": where, "count": len(threads), "threads": [
        {"thread_id": t.id, "name": t.name, "archived": t.archived,
         "parent_id": t.parent_id, "message_count": t.message_count} for t in threads]}


@action("list_emojis", identity.READ, "Custom emoji in a server.",
        {"guild_id": {"type": "int", "required": True}},
        taints=True)
async def _list_emojis(ctx, p):
    g = ctx.guild(p["guild_id"])
    return {"guild": g.name, "count": len(g.emojis), "emojis": [
        {"id": e.id, "name": e.name, "animated": e.animated, "usage": str(e)}
        for e in g.emojis]}


@action("guild_info", identity.READ, "Overview of one server.",
        {"guild_id": {"type": "int", "required": True}},
        taints=True)
async def _guild_info(ctx, p):
    g = ctx.guild(p["guild_id"])
    perms = g.me.guild_permissions
    return {
        "guild_id": g.id, "name": g.name, "members": g.member_count,
        "owner_id": g.owner_id, "created": g.created_at.isoformat(),
        "text_channels": len(g.text_channels), "voice_channels": len(g.voice_channels),
        "roles": len(g.roles), "emojis": len(g.emojis),
        "benham_top_role": g.me.top_role.name,
        "benham_can": sorted(n for n, v in perms if v),
        "destructive_allowed": identity.destructive_allowed(g.id),
    }


# ==========================================================================
# TIER 1 - SPEAK. A human sees it. Deletable, but the notification already fired.
# ==========================================================================

@action("send_message", identity.SPEAK, "Post a message to a channel.",
        {"channel_id": {"type": "int", "required": True},
         "content": {"type": "str", "required": True},
         "reply_to": {"type": "int", "desc": "Message id to reply to"},
         "silent": {"type": "bool", "desc": "Suppress the push notification"}},
        outward=True, posts=True)
async def _send_message(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    kw = {"silent": bool(p.get("silent"))}
    if p.get("reply_to"):
        kw["reference"] = discord.Object(id=int(p["reply_to"]))
    sent = await ch.send(str(p["content"]), **kw)
    return {"status": "sent", "message_id": sent.id, "channel": str(ch),
            "jump_url": sent.jump_url}


@action("send_embed", identity.SPEAK,
        "Post a rich embed (title/description/fields/colour) to a channel.",
        {"channel_id": {"type": "int", "required": True},
         "title": {"type": "str"}, "description": {"type": "str"},
         "color": {"type": "str", "desc": "Hex like '5865F2'"},
         "url": {"type": "str"}, "footer": {"type": "str"},
         "image_url": {"type": "str"}, "thumbnail_url": {"type": "str"},
         "fields": {"type": "list", "desc": "[{name, value, inline}]"},
         "content": {"type": "str", "desc": "Plain text above the embed"}},
        outward=True, posts=True)
async def _send_embed(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    color = discord.Color.blurple()
    if p.get("color"):
        try:
            color = discord.Color(int(str(p["color"]).lstrip("#"), 16))
        except ValueError:
            raise ActionError(f"color must be hex like '5865F2', got {p['color']!r}")
    em = discord.Embed(title=p.get("title"), description=p.get("description"),
                       color=color, url=p.get("url") or None)
    for f in (p.get("fields") or []):
        em.add_field(name=f.get("name", "​"), value=f.get("value", "​"),
                     inline=bool(f.get("inline", False)))
    if p.get("footer"):
        em.set_footer(text=p["footer"])
    if p.get("image_url"):
        em.set_image(url=p["image_url"])
    if p.get("thumbnail_url"):
        em.set_thumbnail(url=p["thumbnail_url"])
    sent = await ch.send(content=p.get("content") or None, embed=em)
    return {"status": "sent", "message_id": sent.id, "channel": str(ch),
            "jump_url": sent.jump_url}


@action("send_file", identity.SPEAK, "Upload a local file to a channel.",
        {"channel_id": {"type": "int", "required": True},
         "path": {"type": "str", "required": True, "desc": "Local path on Tyler's PC"},
         "content": {"type": "str", "desc": "Message text alongside the file"},
         "spoiler": {"type": "bool"}},
        outward=True, posts=True)
async def _send_file(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    path = str(p["path"])
    if not os.path.isfile(path):
        raise ActionError(f"no file at {path}")
    size = os.path.getsize(path)
    # Discord rejects oversized uploads with an unhelpful 413; check first so the
    # failure names the actual problem.
    if size > 25 * 1024 * 1024:
        raise ActionError(f"{path} is {size/1048576:.1f}MB; Discord's limit is 25MB here")
    with open(path, "rb") as fh:
        f = discord.File(io.BytesIO(fh.read()), filename=os.path.basename(path),
                         spoiler=bool(p.get("spoiler")))
    sent = await ch.send(content=p.get("content") or None, file=f)
    return {"status": "sent", "message_id": sent.id, "channel": str(ch),
            "filename": os.path.basename(path), "bytes": size}


@action("dm_user", identity.SPEAK, "Send a direct message to a user.",
        {"user_id": {"type": "int", "required": True},
         "content": {"type": "str", "required": True}},
        outward=True)
async def _dm_user(ctx, p):
    u = await ctx.user(p["user_id"])
    ch = u.dm_channel or await u.create_dm()
    try:
        sent = await ch.send(str(p["content"]))
    except discord.Forbidden:
        raise ActionError(
            f"{u} has DMs closed to server members, or shares no server with Benham"
        )
    return {"status": "sent", "message_id": sent.id, "to": str(u), "user_id": u.id}


@action("react", identity.SPEAK, "Add a reaction to a message.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True},
         "emoji": {"type": "str", "required": True,
                   "desc": "Unicode emoji, or '<:name:id>' for custom"}},
        outward=True)
async def _react(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    try:
        await m.add_reaction(str(p["emoji"]))
    except discord.HTTPException as e:
        raise ActionError(f"Discord rejected the emoji {p['emoji']!r}: {e.text}")
    return {"status": "reacted", "message_id": m.id, "emoji": str(p["emoji"])}


_voice_speaker = None


def set_voice_speaker(fn):
    """Register bot.py's speak_in_channel.

    A callback rather than an import: speaking needs the TTS pipeline and the live
    voice client, both of which live in bot.py, and bot.py already imports this
    module. Registering the function at startup keeps the dependency one-way, the
    same shape codesession.configure uses for its permission prompt.
    """
    global _voice_speaker
    _voice_speaker = fn


@action("speak_in_voice", identity.SPEAK,
        "Say something aloud in a voice channel Benham is connected to.",
        {"channel_id": {"type": "int", "required": True},
         "content": {"type": "str", "required": True}},
        outward=True)
async def _speak_in_voice(ctx, p):
    if _voice_speaker is None:
        raise ActionError("voice output is not wired up (bot.py did not register a speaker)")
    ch = await ctx.channel(p["channel_id"])
    text = str(p["content"])
    await _voice_speaker(ch, text)
    return {"status": "spoke", "channel": getattr(ch, "name", str(ch)),
            "chars": len(text)}


@action("typing", identity.SPEAK,
        "Show the 'Benham is typing...' indicator for a few seconds.",
        {"channel_id": {"type": "int", "required": True},
         "seconds": {"type": "int", "desc": "Default 5, max 30"}})
async def _typing(ctx, p):
    import asyncio
    ch = await ctx.channel(p["channel_id"])
    secs = min(int(p.get("seconds") or 5), 30)
    async with ch.typing():
        await asyncio.sleep(secs)
    return {"status": "typed", "channel": str(ch), "seconds": secs}


# ==========================================================================
# TIER 2 - MANAGE. Real change, exact inverse available.
# ==========================================================================

@action("edit_message", identity.MANAGE, "Edit one of Benham's own messages.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True},
         "content": {"type": "str", "required": True}},
        outward=True)
async def _edit_message(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    if m.author.id != ctx.client.user.id:
        raise ActionError("Discord only allows a bot to edit its own messages")
    old = m.content
    await m.edit(content=str(p["content"]))
    return {"status": "edited", "message_id": m.id, "previous_content": old}


@action("pin_message", identity.MANAGE, "Pin a message.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True}})
async def _pin_message(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    await m.pin()
    return {"status": "pinned", "message_id": m.id}


@action("unpin_message", identity.MANAGE, "Unpin a message.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True}})
async def _unpin_message(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    await m.unpin()
    return {"status": "unpinned", "message_id": m.id}


@action("unreact", identity.MANAGE, "Remove Benham's own reaction from a message.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True},
         "emoji": {"type": "str", "required": True}})
async def _unreact(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    await m.remove_reaction(str(p["emoji"]), ctx.client.user)
    return {"status": "unreacted", "message_id": m.id, "emoji": str(p["emoji"])}


@action("create_thread", identity.MANAGE, "Start a thread, optionally on a message.",
        {"channel_id": {"type": "int", "required": True},
         "name": {"type": "str", "required": True},
         "message_id": {"type": "int", "desc": "Anchor the thread to this message"},
         "auto_archive_minutes": {"type": "int", "desc": "60 | 1440 | 4320 | 10080"}})
async def _create_thread(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    kw = {"name": str(p["name"])}
    if p.get("auto_archive_minutes"):
        kw["auto_archive_duration"] = int(p["auto_archive_minutes"])
    if p.get("message_id"):
        m = await ctx.message(p["channel_id"], p["message_id"])
        t = await m.create_thread(**kw)
    else:
        t = await ch.create_thread(**kw)
    return {"status": "created", "thread_id": t.id, "name": t.name}


@action("archive_thread", identity.MANAGE, "Archive (or unarchive) a thread.",
        {"thread_id": {"type": "int", "required": True},
         "archived": {"type": "bool", "desc": "Default true"}})
async def _archive_thread(ctx, p):
    t = await ctx.channel(p["thread_id"])
    archived = p.get("archived")
    archived = True if archived is None else bool(archived)
    await t.edit(archived=archived)
    return {"status": "archived" if archived else "unarchived", "thread_id": t.id}


@action("add_role", identity.MANAGE, "Give a member a role.",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True},
         "role_id": {"type": "int", "required": True},
         "reason": {"type": "str"}}, needs_guild=True,
        outward=True, always_confirm=True, taints=True)
async def _add_role(ctx, p):
    m = await ctx.member(p["guild_id"], p["user_id"])
    role = m.guild.get_role(int(p["role_id"]))
    if role is None:
        raise ActionError(f"no role {p['role_id']} in {m.guild.name}")
    # Discord silently refuses role changes above the bot's own top role; saying so
    # is far more useful than the generic 403 that comes back.
    if role.position >= m.guild.me.top_role.position:
        raise ActionError(
            f"'{role.name}' sits above Benham's own role - move Benham's role higher "
            "in Server Settings > Roles first"
        )
    if ctx.dry_run:
        perms = sorted(n for n, v in role.permissions if v)
        notable = [x for x in perms if x in (
            "administrator", "manage_guild", "manage_roles", "manage_channels",
            "ban_members", "kick_members", "manage_messages", "mention_everyone")]
        return {"summary": f"Give **{m}** the role **{role.name}** in **{m.guild.name}**",
                "detail": (f"That role grants: {', '.join(notable) if notable else 'no elevated permissions'}."
                           + (f"\n{len(role.members)} member(s) currently have it." if role.members else ""))}
    await m.add_roles(role, reason=p.get("reason") or "via Benham")
    return {"status": "role_added", "user": str(m), "role": role.name}


@action("remove_role", identity.MANAGE, "Take a role away from a member.",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True},
         "role_id": {"type": "int", "required": True},
         "reason": {"type": "str"}}, needs_guild=True,
        outward=True, always_confirm=True, taints=True)
async def _remove_role(ctx, p):
    m = await ctx.member(p["guild_id"], p["user_id"])
    role = m.guild.get_role(int(p["role_id"]))
    if role is None:
        raise ActionError(f"no role {p['role_id']} in {m.guild.name}")
    if ctx.dry_run:
        has = any(r.id == role.id for r in m.roles)
        return {"summary": f"Take the role **{role.name}** away from **{m}** in **{m.guild.name}**",
                "detail": "They do not currently have it - this would be a no-op."
                          if not has else "This removes whatever access that role granted them."}
    await m.remove_roles(role, reason=p.get("reason") or "via Benham")
    return {"status": "role_removed", "user": str(m), "role": role.name}


@action("create_role", identity.MANAGE, "Create a new role.",
        {"guild_id": {"type": "int", "required": True},
         "name": {"type": "str", "required": True},
         "color": {"type": "str", "desc": "Hex like 'FF5555'"},
         "hoist": {"type": "bool", "desc": "Show separately in the member list"},
         "mentionable": {"type": "bool"}}, needs_guild=True,
        outward=True, always_confirm=True)
async def _create_role(ctx, p):
    g = ctx.guild(p["guild_id"])
    if ctx.dry_run:
        return {"summary": f"Create a new role **{p['name']}** in **{g.name}**",
                "detail": "It starts with no permissions; granting any is a separate step."}
    kw = {"name": str(p["name"]), "hoist": bool(p.get("hoist")),
          "mentionable": bool(p.get("mentionable")), "reason": "via Benham"}
    if p.get("color"):
        kw["color"] = discord.Color(int(str(p["color"]).lstrip("#"), 16))
    r = await g.create_role(**kw)
    return {"status": "created", "role_id": r.id, "name": r.name}


@action("set_nickname", identity.MANAGE, "Change a member's nickname (blank to clear).",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True},
         "nickname": {"type": "str"}}, needs_guild=True,
        outward=True, taints=True)
async def _set_nickname(ctx, p):
    m = await ctx.member(p["guild_id"], p["user_id"])
    old = m.nick
    await m.edit(nick=(p.get("nickname") or None), reason="via Benham")
    return {"status": "renamed", "user": str(m), "from": old, "to": p.get("nickname")}


@action("timeout_member", identity.MANAGE,
        "Time a member out (mute) for N minutes. Reversible with 0.",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True},
         "minutes": {"type": "int", "required": True, "desc": "0 lifts the timeout; max 40320 (28d)"},
         "reason": {"type": "str"}}, needs_guild=True,
        outward=True, taints=True)
async def _timeout_member(ctx, p):
    m = await ctx.member(p["guild_id"], p["user_id"])
    mins = int(p["minutes"])
    until = None if mins <= 0 else datetime.now(timezone.utc) + timedelta(minutes=mins)
    await m.timeout(until, reason=p.get("reason") or "via Benham")
    return {"status": "timeout_lifted" if mins <= 0 else "timed_out",
            "user": str(m), "minutes": mins,
            "until": until.isoformat() if until else None}


@action("create_channel", identity.MANAGE, "Create a text or voice channel.",
        {"guild_id": {"type": "int", "required": True},
         "name": {"type": "str", "required": True},
         "kind": {"type": "str", "desc": "text (default) | voice"},
         "category_id": {"type": "int"}, "topic": {"type": "str"},
         "nsfw": {"type": "bool"}}, needs_guild=True)
async def _create_channel(ctx, p):
    g = ctx.guild(p["guild_id"])
    cat = g.get_channel(int(p["category_id"])) if p.get("category_id") else None
    if (p.get("kind") or "text").lower() == "voice":
        c = await g.create_voice_channel(str(p["name"]), category=cat, reason="via Benham")
    else:
        c = await g.create_text_channel(str(p["name"]), category=cat,
                                        topic=p.get("topic"), nsfw=bool(p.get("nsfw")),
                                        reason="via Benham")
    return {"status": "created", "channel_id": c.id, "name": c.name, "type": c.type.name}


@action("edit_channel", identity.MANAGE,
        "Rename a channel or change its topic, slowmode, or category.",
        {"channel_id": {"type": "int", "required": True},
         "name": {"type": "str"}, "topic": {"type": "str"},
         "slowmode_seconds": {"type": "int", "desc": "0-21600"},
         "category_id": {"type": "int"}, "nsfw": {"type": "bool"}}, taints=True)
async def _edit_channel(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    before = channel_dict(ch)
    kw = {}
    if p.get("name"):
        kw["name"] = str(p["name"])
    if p.get("topic") is not None:
        kw["topic"] = p["topic"]
    if p.get("slowmode_seconds") is not None:
        kw["slowmode_delay"] = int(p["slowmode_seconds"])
    if p.get("nsfw") is not None:
        kw["nsfw"] = bool(p["nsfw"])
    if p.get("category_id"):
        kw["category"] = ch.guild.get_channel(int(p["category_id"]))
    if not kw:
        raise ActionError("nothing to change - pass at least one field")
    await ch.edit(reason="via Benham", **kw)
    return {"status": "edited", "channel_id": ch.id,
            "before": before, "after": channel_dict(await ctx.channel(ch.id))}


@action("create_invite", identity.MANAGE, "Create an invite link to a channel.",
        {"channel_id": {"type": "int", "required": True},
         "max_age_seconds": {"type": "int", "desc": "0 = never expires (default 86400)"},
         "max_uses": {"type": "int", "desc": "0 = unlimited"}},
        outward=True)
async def _create_invite(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    inv = await ch.create_invite(
        max_age=int(p.get("max_age_seconds", 86400) or 0),
        max_uses=int(p.get("max_uses") or 0), reason="via Benham")
    return {"status": "created", "url": inv.url, "code": inv.code,
            "expires_in": inv.max_age, "max_uses": inv.max_uses}


@action("unban_member", identity.MANAGE, "Lift a ban (restorative, so not tier 3).",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True}}, needs_guild=True,
        outward=True, taints=True)
async def _unban_member(ctx, p):
    g = ctx.guild(p["guild_id"])
    u = await ctx.user(p["user_id"])
    try:
        await g.unban(u, reason="via Benham")
    except discord.NotFound:
        raise ActionError(f"{u} is not banned in {g.name}")
    return {"status": "unbanned", "user": str(u), "guild": g.name}


@action("set_presence", identity.MANAGE, "Set Benham's status and activity.",
        {"status": {"type": "str", "desc": "online | idle | dnd | invisible"},
         "activity_type": {"type": "str", "desc": "playing | listening | watching | competing | none"},
         "activity_name": {"type": "str"}},
        # Applied on login by on_ready, which has no human behind it.
        origins=policy.DEFAULT_ORIGINS | {policy.Origin.SYSTEM})
async def _set_presence(ctx, p):
    status = {"online": discord.Status.online, "idle": discord.Status.idle,
              "dnd": discord.Status.dnd, "invisible": discord.Status.invisible
              }.get((p.get("status") or "online").lower(), discord.Status.online)
    kind = (p.get("activity_type") or "none").lower()
    activity = None
    if kind != "none" and p.get("activity_name"):
        atype = {"playing": discord.ActivityType.playing,
                 "listening": discord.ActivityType.listening,
                 "watching": discord.ActivityType.watching,
                 "competing": discord.ActivityType.competing}.get(kind)
        if atype is None:
            raise ActionError(f"unknown activity_type {kind!r}")
        activity = discord.Activity(type=atype, name=str(p["activity_name"]))
    await ctx.client.change_presence(status=status, activity=activity)
    return {"status": "presence_set", "discord_status": str(status),
            "activity": f"{kind} {p.get('activity_name')}" if activity else None}


@action("set_bot_nickname", identity.MANAGE, "Change Benham's own nickname in a server.",
        {"guild_id": {"type": "int", "required": True},
         "nickname": {"type": "str", "desc": "Blank resets to 'Benham'"}}, needs_guild=True, taints=True)
async def _set_bot_nickname(ctx, p):
    g = ctx.guild(p["guild_id"])
    old = g.me.nick
    await g.me.edit(nick=(p.get("nickname") or None))
    return {"status": "renamed", "guild": g.name, "from": old, "to": p.get("nickname")}


@action("create_webhook", identity.MANAGE, "Create a webhook on a channel.",
        {"channel_id": {"type": "int", "required": True},
         "name": {"type": "str", "required": True}},
        outward=True)
async def _create_webhook(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    wh = await ch.create_webhook(name=str(p["name"]), reason="via Benham")
    # The URL is a bearer credential - anyone holding it can post as this webhook.
    # Returned because the caller asked for it, but never logged by run().
    return {"status": "created", "webhook_id": wh.id, "name": wh.name, "url": wh.url,
            "_sensitive": ["url"]}


@action("pc_task", identity.MANAGE,
        "Do something on Tyler's actual PC by running a real Claude Code session "
        "in the Discord-Claude folder - read/edit files, run commands, use his "
        "skills (exaroton, drive-api, double, desktop-automation). Give it the task "
        "in plain language, as you would type it into a terminal session. Reading "
        "is free; every write or command asks Tyler for approval first, so expect "
        "this to take a while and do not retry if he says no.",
        {"task": {"type": "str", "required": True,
                  "desc": "What to do, in plain language, with enough context to act alone"}},
        taints=True,
        # The most consequential capability here, and the only one restricted by
        # where the request arrived from. A DM is a private two-party channel; a
        # guild mention happens in a room strangers can write in, and a voice
        # channel is whoever is sitting in it. LOCAL_CLI is included because writing
        # into outbox/ already requires having the machine, so denying it would cost
        # capability without closing anything.
        origins={policy.Origin.OWNER_DM, policy.Origin.LOCAL_CLI},
        # And not even from a DM once the turn has read what strangers wrote.
        blocked_when_tainted=True)
async def _pc_task(ctx, p):
    # Imported lazily: the SDK pulls in a large dependency tree and spawns the
    # Claude Code CLI, and neither should be a cost paid by a bot that never
    # touches the PC.
    import codesession
    if not codesession.ENABLED:
        raise ActionError("PC access is off (pc.enabled in control.json)")

    # Report each tool as it is used, rather than only a summary once the task is
    # over. run_task always accepted this callback and nothing ever passed one, so
    # a session doing things on the machine was invisible until it finished - which
    # is the wrong half of the timeline to be able to see. watch_pc.py gives the
    # detailed live view; this is the coarse one that lands in bot.log next to
    # everything else.
    async def _progress(tool_name):
        ctx.log(f"  pc_task ... {tool_name}")

    result = await codesession.run_task(str(p["task"]), on_progress=_progress)
    return {"status": "completed", "task": str(p["task"])[:200], "result": result}


# ==========================================================================
# TIER 3 - DESTRUCTIVE. No undo. Guild-allowlisted, dry-run first, explicit fire.
# Each handler must produce a preview under ctx.dry_run WITHOUT touching anything.
# ==========================================================================

@action("delete_message", identity.DESTRUCTIVE, "Delete a single message.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True}}, needs_guild=True)
async def _delete_message(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    if ctx.dry_run:
        body = (m.content or "")[:200] or "(no text - embed or attachment)"
        return {"summary": f"Delete 1 message by **{m.author}** in **#{m.channel}**",
                "detail": f"> {body}", "author": str(m.author), "ts": m.created_at.isoformat()}
    await m.delete()
    return {"status": "deleted", "message_id": m.id, "author": str(m.author)}


@action("purge_messages", identity.DESTRUCTIVE,
        "Bulk-delete messages in a channel, optionally filtered by age or author.",
        {"channel_id": {"type": "int", "required": True},
         "limit": {"type": "int", "desc": "How many to consider (default 100)"},
         "older_than_days": {"type": "int", "desc": "Only messages older than this"},
         "author_id": {"type": "int", "desc": "Only this user's messages"},
         "contains": {"type": "str", "desc": "Only messages containing this text"}},
        needs_guild=True)
async def _purge_messages(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    limit = int(p.get("limit") or 100)
    author = int(p["author_id"]) if p.get("author_id") else None
    contains = (p.get("contains") or "").lower()
    before = None
    if p.get("older_than_days"):
        before = datetime.now(timezone.utc) - timedelta(days=int(p["older_than_days"]))

    def match(m):
        if author and m.author.id != author:
            return False
        if contains and contains not in (m.content or "").lower():
            return False
        return True

    if ctx.dry_run:
        # Walk the same set the real purge would, but only describe it. This is the
        # check that catches a wrong channel id or an off-by-10x days value, so it
        # reports the actual span and a sample rather than just a count.
        hits = []
        async for m in ch.history(limit=limit, before=before):
            if match(m):
                hits.append(m)
        if not hits:
            return {"summary": f"Nothing matches in **#{ch}** - 0 messages would be deleted.",
                    "count": 0}
        oldest, newest = hits[-1], hits[0]
        authors = {}
        for m in hits:
            authors[str(m.author)] = authors.get(str(m.author), 0) + 1
        top = ", ".join(f"{a} ({n})" for a, n in
                        sorted(authors.items(), key=lambda kv: -kv[1])[:5])
        sample = "\n".join(f"> {(m.content or '(no text)')[:100]}" for m in hits[:3])
        return {
            "summary": (f"Delete **{len(hits)} messages** from **#{ch}** "
                        f"in **{ch.guild.name}**"),
            "detail": (f"Span: {oldest.created_at:%Y-%m-%d} to {newest.created_at:%Y-%m-%d}\n"
                       f"Authors: {top}\n\nNewest few:\n{sample}"),
            "count": len(hits),
        }

    deleted = await ch.purge(limit=limit, before=before, check=match, bulk=True)
    return {"status": "purged", "channel": str(ch), "deleted": len(deleted)}


@action("delete_channel", identity.DESTRUCTIVE,
        "Delete a channel and every message in it.",
        {"channel_id": {"type": "int", "required": True}}, needs_guild=True)
async def _delete_channel(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    if ctx.dry_run:
        recent = [m async for m in ch.history(limit=1)] if hasattr(ch, "history") else []
        last = f"last activity {recent[0].created_at:%Y-%m-%d}" if recent else "no messages"
        return {"summary": (f"Delete channel **#{ch.name}** from **{ch.guild.name}** "
                            f"- this destroys its entire message history"),
                "detail": f"Type: {ch.type.name}, {last}. This cannot be undone."}
    name = ch.name
    await ch.delete(reason="via Benham")
    return {"status": "deleted", "channel": name, "channel_id": ch.id}


@action("delete_role", identity.DESTRUCTIVE,
        "Delete a role, stripping it from everyone who has it.",
        {"guild_id": {"type": "int", "required": True},
         "role_id": {"type": "int", "required": True}}, needs_guild=True)
async def _delete_role(ctx, p):
    g = ctx.guild(p["guild_id"])
    role = g.get_role(int(p["role_id"]))
    if role is None:
        raise ActionError(f"no role {p['role_id']} in {g.name}")
    if ctx.dry_run:
        holders = role.members
        names = ", ".join(str(m) for m in holders[:10]) or "nobody"
        # Naming the holders matters: once the role is gone there is no record of
        # who had it, so this preview is the only chance to reconstruct it.
        return {"summary": f"Delete role **{role.name}** from **{g.name}**, "
                           f"removing it from **{len(holders)} member(s)**",
                "detail": f"Currently held by: {names}"
                          f"{' ...' if len(holders) > 10 else ''}\n"
                          "Who held it cannot be recovered afterwards."}
    name, count = role.name, len(role.members)
    await role.delete(reason="via Benham")
    return {"status": "deleted", "role": name, "stripped_from": count}


@action("kick_member", identity.DESTRUCTIVE, "Kick a member (they can rejoin with an invite).",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True},
         "reason": {"type": "str"}}, needs_guild=True)
async def _kick_member(ctx, p):
    m = await ctx.member(p["guild_id"], p["user_id"])
    if ctx.dry_run:
        return {"summary": f"Kick **{m}** from **{m.guild.name}**",
                "detail": f"Joined {m.joined_at:%Y-%m-%d}, roles: "
                          f"{', '.join(r.name for r in m.roles if r.name != '@everyone') or 'none'}. "
                          "They can rejoin with a fresh invite."}
    await m.kick(reason=p.get("reason") or "via Benham")
    return {"status": "kicked", "user": str(m), "user_id": m.id}


@action("ban_member", identity.DESTRUCTIVE, "Ban a user from a server.",
        {"guild_id": {"type": "int", "required": True},
         "user_id": {"type": "int", "required": True},
         "reason": {"type": "str"},
         "delete_message_days": {"type": "int", "desc": "Also wipe their last N days of messages (0-7)"}},
        needs_guild=True)
async def _ban_member(ctx, p):
    g = ctx.guild(p["guild_id"])
    days = int(p.get("delete_message_days") or 0)
    if ctx.dry_run:
        try:
            m = await ctx.member(p["guild_id"], p["user_id"])
            who = (f"{m} (joined {m.joined_at:%Y-%m-%d}, roles: "
                   f"{', '.join(r.name for r in m.roles if r.name != '@everyone') or 'none'})")
        except ActionError:
            u = await ctx.user(p["user_id"])
            who = f"{u} (not currently in the server - pre-emptive ban)"
        extra = (f"\nAlso deletes their last {days} day(s) of messages."
                 if days else "\nTheir messages stay.")
        return {"summary": f"Ban **{who}** from **{g.name}**",
                "detail": f"Reason: {p.get('reason') or '(none given)'}{extra}"}
    u = await ctx.user(p["user_id"])
    await g.ban(u, reason=p.get("reason") or "via Benham",
                delete_message_days=min(max(days, 0), 7))
    return {"status": "banned", "user": str(u), "user_id": u.id, "guild": g.name}


@action("delete_emoji", identity.DESTRUCTIVE, "Delete a custom emoji.",
        {"guild_id": {"type": "int", "required": True},
         "emoji_id": {"type": "int", "required": True}}, needs_guild=True)
async def _delete_emoji(ctx, p):
    g = ctx.guild(p["guild_id"])
    e = discord.utils.get(g.emojis, id=int(p["emoji_id"]))
    if e is None:
        raise ActionError(f"no emoji {p['emoji_id']} in {g.name}")
    if ctx.dry_run:
        return {"summary": f"Delete emoji **:{e.name}:** from **{g.name}**",
                "detail": f"{e.url}\nThe image is not recoverable from Discord afterwards."}
    name = e.name
    await e.delete(reason="via Benham")
    return {"status": "deleted", "emoji": name}


# ==========================================================================
# Dispatch
# ==========================================================================

def _coerce(name, spec, value):
    """Coerce one parameter, with an error that names the parameter."""
    t = spec.get("type", "str")
    try:
        if t == "int":
            return int(value)
        if t == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "y", "on")
            return bool(value)
        if t == "list":
            return list(value)
        return str(value)
    except (TypeError, ValueError):
        raise ActionError(f"{name} must be {t}, got {value!r}")


def validate(act, params):
    """Check required params and coerce types. Returns a clean dict."""
    out = {}
    for key, spec in act.params.items():
        if key in params and params[key] is not None:
            out[key] = _coerce(key, spec, params[key])
        elif spec.get("required"):
            raise ActionError(f"{act.name} needs `{key}`: {spec.get('desc', '')}".strip())
    unknown = set(params) - set(act.params) - {"action", "guild_id"}
    if unknown:
        raise ActionError(f"{act.name} got unknown parameter(s): {', '.join(sorted(unknown))}")
    # guild_id is accepted by every action for the allowlist check even when the
    # handler itself does not declare it.
    if "guild_id" in params and "guild_id" not in out and params["guild_id"] is not None:
        out["guild_id"] = int(params["guild_id"])
    return out


async def _infer_guild(ctx, params):
    """Work out which guild an action targets, for the destructive allowlist.

    Most destructive actions name a channel rather than a guild, and the allowlist
    is per-guild - so the check has to resolve the channel to its guild first. A
    channel that resolves to no guild (a DM) yields None, which destructive_allowed
    treats as "not allowed".
    """
    if params.get("guild_id"):
        return int(params["guild_id"])
    for key in ("channel_id", "thread_id"):
        if params.get(key):
            ch = await ctx.channel(params[key])
            g = getattr(ch, "guild", None)
            return g.id if g else None
    return None


async def run(client, log, name, params, actor_id=None, dry_run=False, force=False,
              call_ctx=None):
    """Execute one action by name. The single chokepoint every caller goes through.

    Returns (result_dict, pending_preview_or_None). When a destructive action is
    called without force, the preview is returned and NOTHING has happened yet -
    the caller is responsible for parking it for confirmation.

    The ordering here is load-bearing: allowlist BEFORE dry-run, so an action aimed
    at a guild it may not touch never even reports that guild's contents.
    """
    act = REGISTRY.get(name)
    if act is None:
        raise ActionError(f"unknown action {name!r}. Known: {', '.join(sorted(REGISTRY))}")

    # policy.authorize is consulted BEFORE the parameters are even validated. A
    # capability this route may not reach should not report which arguments it
    # would have wanted - and more practically, an origin refusal is not something
    # a caller should be able to probe for shape by sending malformed input.
    decision = policy.authorize(act, call_ctx)
    if decision.denied:
        log(f"DENIED {name} by {actor_id or 'code-session'} "
            f"[rule={decision.rule}, origin={getattr(call_ctx, 'origin', None)}]")
        raise ActionError(decision.reason)

    clean = validate(act, params or {})
    ctx = Ctx(client, log, dry_run=False, actor_id=actor_id)

    gid = None
    if act.destructive or act.posts or act.needs_confirm:
        gid = await _infer_guild(ctx, clean)

    # Second authorization phase: the rules that depend on what this call points at
    # rather than on who is asking. Resolving that needs validated parameters and a
    # channel lookup, which is why it cannot happen alongside the caller rules.
    target_decision = policy.authorize_target(
        act, (call_ctx or policy.CallContext.system()).for_target(gid, clean.get("channel_id")))
    if target_decision.denied:
        log(f"DENIED {name} by {actor_id or 'code-session'} "
            f"[rule={target_decision.rule}, guild={gid}]")
        raise ActionError(target_decision.reason)

    # `force` means the confirmation already happened, so it is checked here and
    # deliberately not inside policy - policy states what a call needs, this decides
    # whether that need has been met. Keeping them apart is what stops "he already
    # asked for it" from quietly becoming "so it no longer needs checking".
    if target_decision.needs_confirm and not force:
        if act.needs_confirm:
            # Destructive and role actions implement a real dry_run branch that
            # gathers facts - counts, date spans, who holds the role - without
            # touching anything.
            ctx.dry_run = True
            preview = await act.handler(ctx, clean)
        else:
            # A taint-induced confirmation. The handler must NOT be called: only
            # tier-3 handlers honour dry_run, and send_message ignores it and sends.
            # That exact assumption is what made the first version of this defence
            # execute the action it claimed to be previewing.
            preview = describe_call(name, clean)
        preview = dict(preview)
        preview.setdefault("reason", target_decision.reason)
        # Log the PROPOSAL, not just the execution. Without this the audit trail
        # records only what was destroyed, so a preview that was declined, ignored,
        # or left to expire leaves no trace at all - and "what did it try to do"
        # is exactly the question worth being able to answer after the fact.
        log(f"PROPOSED {name} by {actor_id or 'code-session'} "
            f"(guild {gid}) [rule={target_decision.rule}]: {preview.get('summary', '?')}")
        return None, preview

    try:
        result = await act.handler(ctx, clean)
    except discord.Forbidden as e:
        raise ActionError(
            f"Discord refused `{name}`: Benham's role lacks the permission for it "
            f"in that server ({e.text or 'Forbidden'})"
        )
    except discord.HTTPException as e:
        raise ActionError(f"Discord rejected `{name}`: {e.text or e}")

    # Never log a result field the handler flagged as a credential (webhook URLs).
    loggable = {k: v for k, v in result.items()
                if k not in set(result.get("_sensitive", [])) and k != "_sensitive"}
    log(f"action {name} by {actor_id or 'code-session'}: {loggable}")
    return result, None


def describe_call(name, params):
    """Summarise a call WITHOUT running it. Used to park a confirmation.

    This exists because of a real bug. The taint gate used to build its preview by
    calling run(force=False) and assuming that meant "dry run" - but run() only
    dry-runs when the action needs confirmation, and the taint branch fires
    precisely on actions that do NOT. So the call fell through to the handler, the
    message was actually sent, and the model was then told "NOT EXECUTED" and asked
    to get Tyler's approval to do the thing it had already done. Confirming fired
    it a second time.

    Calling a handler with dry_run=True would not have fixed it either: only tier-3
    and always-confirm handlers check that flag, and send_message ignores it. The
    only safe way to describe an arbitrary action is to not invoke it at all.
    """
    act = REGISTRY.get(name)
    if act is None:
        return {"summary": f"unknown action `{name}`"}
    bits = []
    for key in ("channel_id", "user_id", "guild_id", "message_id", "role_id"):
        if params.get(key):
            bits.append(f"{key}={params[key]}")
    body = params.get("content") or params.get("task") or params.get("nickname") or ""
    detail = f"> {str(body)[:400]}" if body else ""
    return {
        "summary": f"`{name}`" + (f" ({', '.join(bits)})" if bits else ""),
        "detail": detail,
    }


def catalog(min_tier=None, max_tier=None):
    """The registry as plain data - used for tool schemas and the help listing."""
    out = []
    for name, a in sorted(REGISTRY.items(), key=lambda kv: (kv[1].tier, kv[0])):
        if min_tier is not None and a.tier < min_tier:
            continue
        if max_tier is not None and a.tier > max_tier:
            continue
        out.append({"name": name, "tier": a.tier, "tier_name": identity.TIER_NAMES[a.tier],
                    "summary": a.summary, "params": a.params})
    return out
