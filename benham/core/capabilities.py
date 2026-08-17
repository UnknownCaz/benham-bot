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

import asyncio
import io
import os
import re
from datetime import datetime, timezone, timedelta

import discord

from benham.core import identity
from benham.core import pathsafe
from benham.core import policy

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
                 origins=None, blocked_when_tainted=False, guest=False):
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
        # Whether a GUEST may ever reach this capability. Code-side half of a
        # three-part grant: this flag, GUEST_DM in origins, AND the name listed in
        # control.json's guest.capabilities - policy.rule_guest checks the config,
        # rule_origin_allowed checks the origins, and all three must agree. The
        # default is the entire point: a capability written next year is
        # guest-proof on the day it is written, exactly as before Stage 2.
        self.guest = bool(guest)

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
           origins=None, blocked_when_tainted=False, guest=False):
    """Register one capability.

    A guest=True declaration is checked HERE, at import, and a violation refuses
    to register - the bot crashes at startup rather than carrying a capability
    whose declaration lies. The rules (see PLAN-guest-permissions.md §2):

      not destructive / posts / always_confirm / outward - the guest lane never
      confirms and never produces content other people see, so a guest capability
      that would need either is a contradiction, not a configuration.

      not blocked_when_tainted - a guest turn is BORN tainted, so such a
      capability could be granted in config and dead in practice, which is
      exactly the kind of lie a registry must not tell.

      GUEST_DM in origins - rule_origin_allowed is the second, independent
      denial, and a guest capability that forgot to name the guest origin would
      pass the flag check and then refuse every real call.
    """
    # Reserved by the outbox envelope. do.py enqueues {action: <name>, **params},
    # a FLAT json object that bot.py reads by key - so a capability parameter
    # sharing one of these names is not merely awkward, it is unrepresentable: the
    # value silently overwrites the envelope, or (for "action") blows up with a
    # TypeError deep inside enqueue that names neither the capability nor the
    # parameter. Found the honest way, by declaring a param called "action".
    RESERVED_PARAMS = {"action", "queued_at", "confirm_token"}

    def deco(fn):
        clash = RESERVED_PARAMS & set(params or {})
        if clash:
            raise ValueError(
                f"capability {name!r} declares parameter(s) {sorted(clash)}, which "
                "the outbox envelope reserves. Rename them - the CLI cannot send a "
                "capability parameter that collides with an envelope key.")
        if guest:
            problems = []
            if tier >= identity.DESTRUCTIVE:
                problems.append("destructive")
            if posts:
                problems.append("posts")
            if always_confirm:
                problems.append("always_confirm")
            if outward:
                problems.append("outward")
            if blocked_when_tainted:
                problems.append("blocked_when_tainted (guest turns are born tainted)")
            if origins is None or policy.Origin.GUEST_DM not in origins:
                problems.append("origins must explicitly include Origin.GUEST_DM")
            if problems:
                raise ValueError(
                    f"capability {name!r} declares guest=True but violates the "
                    f"guest-lane invariant: {'; '.join(problems)}. See "
                    "PLAN-guest-permissions.md §2.")
        REGISTRY[name] = Action(name, tier, summary, params, fn, needs_guild,
                                outward=outward, taints=taints,
                                always_confirm=always_confirm, posts=posts,
                                origins=origins,
                                blocked_when_tainted=blocked_when_tainted,
                                guest=guest)
        return fn
    return deco


def guest_grants():
    """The capabilities a guest may actually reach, {name: Action}.

    The single computation of the three-way agreement: flag, origins, config.
    The guest tool loop (Stage 3) builds its schemas from this, and the invariant
    suite recomputes it independently - two consumers, one definition, no drift.
    Lives here rather than in policy.py only because the registry does (policy
    cannot import capabilities without a cycle); policy.rule_guest applies the
    same three conditions per-call.
    """
    cfg = identity.guest_capabilities()
    return {name: act for name, act in REGISTRY.items()
            if act.guest and act.origins is not None
            and policy.Origin.GUEST_DM in act.origins
            and name in cfg}


# --------------------------------------------------------------------------
# Context + resolution helpers
# --------------------------------------------------------------------------

class Ctx:
    """What a handler is given: the client, a logger, and the dry-run flag."""

    def __init__(self, client, log, dry_run=False, actor_id=None, on_progress=None,
                 source_channel_id=None, source_message_id=None, on_attach=None):
        self.client = client
        self.log = log
        self.dry_run = dry_run
        self.actor_id = actor_id
        # Optional live-progress sink, async fn(kind, detail). Carried on the
        # context rather than passed as a parameter: it is a callable, so it could
        # not be declared in the tool schema, and validate() rightly refuses
        # anything undeclared.
        self.on_progress = on_progress
        # Where THIS turn physically came from: the channel and message that
        # started it, set by run() from trusted caller state - never from
        # parameters. ws_import is the consumer: "the message you sent" has to
        # mean the one the transport delivered, not one the model named.
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        # Optional collector, fn(path). A handler wanting a file to ride the
        # reply calls this; whether anything is listening is the surface's
        # choice (the guest loop listens, the CLI does not). A callable for the
        # same reason on_progress is: it cannot be a schema parameter.
        self.on_attach = on_attach

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
        "attachments": [att_dict(a) for a in m.attachments],
        "embeds": [e for e in (embed_dict(em) for em in m.embeds) if e],
        "reactions": [{"emoji": str(r.emoji), "count": r.count} for r in m.reactions],
        "pinned": m.pinned,
        "reply_to": m.reference.message_id if m.reference else None,
    }


def att_dict(a):
    """One attachment as metadata - not its bytes.

    A bare CDN url used to be all a read reported, which told the reader that a file
    exists and nothing about whether it is a screenshot, a crash log or a 400MB
    video. Deciding whether something is worth downloading needs the name, the size
    and the type; `read_attachments` is what then fetches it.
    """
    return {"attachment_id": a.id, "filename": a.filename, "bytes": a.size,
            "content_type": a.content_type, "url": a.url}


def embed_dict(em):
    """One embed's readable surface, media URLs included - omit-empty.

    A GIF sent through Discord's picker is a bare Tenor/Klipy URL plus an embed
    with no title, description or fields; the picture lives only in the embed's
    media slots. Without those URLs a read reports the message as a naked link
    and the reader can never fetch the actual GIF. proxy_url first: Discord's
    proxy serves the media directly, where url may be the source page.
    """
    d = {}
    if em.title:
        d["title"] = em.title
    if em.description:
        d["description"] = em.description
    for f in em.fields:
        if f.name or f.value:
            d.setdefault("fields", []).append({"name": f.name, "value": f.value})
    for label, proxy in (("image", em.image), ("video", em.video),
                         ("thumbnail", em.thumbnail)):
        u = proxy.proxy_url or proxy.url
        if u:
            d[label] = u
    if em.url:
        d["url"] = em.url
    return d


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


# --- attachment download -------------------------------------------------
#
# Downloading is the one read that leaves something behind on the machine, which is
# why it is fenced rather than trusted. Three properties do the work, and the tier
# depends on all three holding:
#
#   1. It fetches what a NAMED MESSAGE carries. There is deliberately no url
#      parameter - that would be a general "download anything from anywhere" tool
#      aimed by whatever text Benham last read, which is the exact shape of attack
#      the rest of this file is built to refuse.
#   2. Bytes land only under downloads/, with a filename this module rewrites. The
#      uploader chooses that name and nothing else about where it goes.
#   3. Nothing is ever run. Files are written and described; a runnable extension is
#      flagged in the result and otherwise treated as inert bytes.

# Aliased: two functions below use `paths` as a local for outgoing file lists,
# and a bare `from benham import paths` would be shadowed there.
from benham import paths as _paths

DOWNLOAD_DIR = os.path.join(_paths.STATE_DIR, "downloads")
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024      # per file, unless the caller raises it
HARD_ATTACHMENT_BYTES = 100 * 1024 * 1024   # ceiling the caller cannot raise past
MAX_TEXT_CHARS = 20000                      # of a text file, returned inline

# Filename hygiene and path confinement live in pathsafe.py now (Stage 0 of
# PLAN-guest-permissions.md) so the guest workspace can share one implementation
# with this quarantine rather than growing a second. The private aliases below
# keep every internal call site - and test_attachments.py, which reaches in for
# them - exactly as they were.
#
# Runnable extensions here are FLAGGED, never blocked: a blocklist would refuse
# `.jar`-shaped legitimate work (mod files are a normal thing for Tyler to be
# handed) while stopping nothing, since the file is only ever written and never
# launched. That is this call site's policy, not pathsafe's - the guest workspace
# makes the opposite call with the same list.

_RUNNABLE_SUFFIXES = pathsafe.RUNNABLE_SUFFIXES
_safe_filename = pathsafe.safe_filename
_is_textual = pathsafe.is_textual


def _confined_path(root, filename):
    """pathsafe.confined_path, re-raised in this module's vocabulary.

    pathsafe deliberately does not know what ActionError is (capabilities imports
    pathsafe, not the reverse) or what the root MEANS - "outside downloads/" is a
    sentence about this call site.
    """
    try:
        return pathsafe.confined_path(root, filename)
    except pathsafe.ConfinementError:
        raise ActionError(f"refusing to write outside downloads/: {filename!r}")


@action("read_attachments", identity.READ,
        "Download the files attached to one message and return what they are; text "
        "files come back with their contents. Files are saved to the bot's safety "
        "quarantine folder (downloads/<message_id>/ inside the benham-bot repo), NOT "
        "the Windows user Downloads folder - say so when telling Tyler where a file "
        "went, and give the full saved_to path from the result.",
        {"channel_id": {"type": "int", "required": True},
         "message_id": {"type": "int", "required": True},
         "index": {"type": "int", "desc": "Only this attachment, 0-based (default all)"},
         "save": {"type": "bool", "desc": "Write to disk (default true; false = look, "
                                          "report, keep nothing)"},
         "max_bytes": {"type": "int",
                       "desc": f"Per-file ceiling (default {MAX_ATTACHMENT_BYTES // 1048576}MB)"}},
        taints=True)
async def _read_attachments(ctx, p):
    m = await ctx.message(p["channel_id"], p["message_id"])
    atts = list(m.attachments)
    base = 0
    if p.get("index") is not None:
        i = int(p["index"])
        if not 0 <= i < len(atts):
            raise ActionError(f"index {i} is out of range - that message has "
                              f"{len(atts)} attachment(s)")
        atts, base = [atts[i]], i

    cap = min(int(p.get("max_bytes") or MAX_ATTACHMENT_BYTES), HARD_ATTACHMENT_BYTES)
    save = p.get("save") is not False
    target = os.path.join(DOWNLOAD_DIR, str(m.id))
    out = []

    for offset, a in enumerate(atts):
        rec = {"index": base + offset, "filename": a.filename, "bytes": a.size,
               "content_type": a.content_type}
        safe = _safe_filename(a.filename, f"attachment_{base + offset}")
        if safe != a.filename:
            # Say so rather than quietly writing a different name. A rewritten name
            # is usually harmless (a colon in a screenshot title) and occasionally
            # the most interesting fact about the file.
            rec["sanitised_to"] = safe
        if os.path.splitext(safe.lower())[1] in _RUNNABLE_SUFFIXES:
            rec["executable"] = True
        # Size comes from the message metadata, so an oversized file is refused
        # before spending the bandwidth rather than after.
        if a.size > cap:
            rec["skipped"] = (f"{a.size / 1048576:.1f}MB is over the "
                              f"{cap / 1048576:.1f}MB ceiling - raise max_bytes to fetch it")
            out.append(rec)
            continue
        try:
            data = await a.read()
        except (discord.HTTPException, discord.NotFound) as e:
            # One unreachable attachment should not lose the others: Discord's CDN
            # links expire, and a message can outlive its own files.
            rec["skipped"] = f"download failed: {getattr(e, 'text', None) or e}"
            out.append(rec)
            continue
        rec["bytes_downloaded"] = len(data)
        if save:
            os.makedirs(target, exist_ok=True)
            dest = _confined_path(target, safe)
            with open(dest, "wb") as fh:
                fh.write(data)
            rec["saved_to"] = dest
        if _is_textual(a.content_type, safe):
            # replace, not strict: a log that is mostly UTF-8 with one bad byte is
            # still worth reading, and a decode error here would lose the whole file.
            text = data.decode("utf-8", errors="replace")
            rec["text"] = text[:MAX_TEXT_CHARS]
            rec["text_truncated"] = len(text) > MAX_TEXT_CHARS
        out.append(rec)

    res = {"message_id": m.id, "channel": str(m.channel), "author": str(m.author),
           "count": len(out), "attachments": out}
    if save and any("saved_to" in r for r in out):
        res["saved_in"] = target
    if not atts:
        res["note"] = "that message has no attachments"
    return res


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


# --- find_user helpers ---------------------------------------------------
#
# Every other action in this file wants a numeric user_id, which is the one thing
# a human never has to hand. This closes that gap, and it has to do it twice over
# because there are two different ways to look a member up and neither covers the
# other: the member cache can match mid-string but only exists when a privileged
# intent is on, and the gateway query needs no intent but only matches prefixes.

_MENTION_RE = re.compile(r"<@!?(\d{15,25})>")


def _as_user_id(text):
    """Return an id if the query is already one, else None.

    A pasted @mention arrives as `<@123...>` and a copied id arrives bare; both are
    the answer rather than something to search for. The length floor matters: `123`
    is a legal Discord name and a nonsense snowflake, so short digit strings stay on
    the name path instead of turning into a doomed user fetch.
    """
    m = _MENTION_RE.fullmatch(text)
    if m:
        return int(m.group(1))
    return int(text) if text.isdigit() and len(text) >= 15 else None


def _matched_field(m, low):
    """Which of a member's names contains `low`, or None.

    Reported back so a match on a server nickname is distinguishable from a match on
    the account name - "which Alex is this" is usually answered by knowing which of
    the two hit. Checks the raw fields rather than display_name, which is itself just
    whichever of nick/global_name/name happens to be set.
    """
    for label, value in (("username", m.name),
                         ("display_name", getattr(m, "global_name", None)),
                         ("nickname", getattr(m, "nick", None))):
        if value and low in value.lower():
            return label
    return None


def _found(m, matched_on):
    """A lookup result. Deliberately thinner than member_dict().

    Roles, join date and status are left out: this action exists to turn a name into
    an id, and once you have the id `member_info` answers everything else about one
    person. Carrying all of it here would also raise the question of which guild's
    roles a cross-guild match is showing.
    """
    return {"user_id": m.id, "name": str(m), "display_name": m.display_name,
            "bot": m.bot, "matched_on": matched_on}


def _record(hits, m, g, field):
    """Fold one hit into the results, keyed by user so a shared member is one row."""
    rec = hits.get(m.id)
    if rec is None:
        rec = hits[m.id] = _found(m, field)
        rec["guilds"] = []
    if not any(e["guild_id"] == g.id for e in rec["guilds"]):
        rec["guilds"].append({"guild_id": g.id, "name": g.name, "nick": m.nick})


def _rank(rec, low):
    """Exact name first, then prefixes, then mid-string hits."""
    names = [rec["name"].lower(), (rec["display_name"] or "").lower()]
    if low in names:
        return 0
    return 1 if any(n.startswith(low) for n in names) else 2


@action("find_user", identity.READ,
        "Look a user up by name and get their user_id. Matches username, display "
        "name and server nickname; also accepts an @mention or a raw id.",
        {"query": {"type": "str", "required": True,
                   "desc": "A name or part of one, an @mention, or a user id"},
         "guild_id": {"type": "int",
                      "desc": "Search one server (default: every server Benham is in)"},
         "limit": {"type": "int", "desc": "Max matches to return (default 25)"}},
        taints=True)
async def _find_user(ctx, p):
    needle = str(p["query"]).strip()
    if not needle:
        raise ActionError("query is empty - give a name, an @mention or a user id")
    limit = max(1, min(int(p.get("limit") or 25), 100))

    uid = _as_user_id(needle)
    if uid is not None:
        # A guild was named, so fetch the member there: that is an API call, so it
        # answers "is this person actually in that server" authoritatively, which
        # scanning a possibly-empty cache would not.
        if p.get("guild_id"):
            g = ctx.guild(p["guild_id"])
            m = await ctx.member(g.id, uid)
            rec = _found(m, "id")
            rec["guilds"] = [{"guild_id": g.id, "name": g.name, "nick": m.nick}]
        else:
            u = await ctx.user(uid)
            rec = {"user_id": u.id, "name": str(u),
                   "display_name": getattr(u, "global_name", None) or u.name,
                   "bot": u.bot, "matched_on": "id", "guilds": []}
        return {"query": needle, "method": "id", "count": 1, "matches": [rec]}

    guilds = [ctx.guild(p["guild_id"])] if p.get("guild_id") else list(ctx.client.guilds)
    if not guilds:
        raise ActionError("Benham is not in any server")

    low = needle.lower()
    full_cache = ctx.client.intents.members
    hits, problems = {}, []

    for g in guilds:
        for m in g.members:
            field = _matched_field(m, low)
            if field:
                _record(hits, m, g, field)
        # With the members intent on, discord.py chunks the whole member list at
        # startup and keeps it current, so the cache above IS the answer and a
        # round trip per guild would buy nothing. With it off the cache holds only
        # stragglers - whoever Benham saw in voice, or looked up earlier - so the
        # gateway query below is the real search.
        if full_cache:
            continue
        try:
            # Discord only demands the privileged intent for the UNFILTERED member
            # list; a query with a name in it is allowed without it. That is the
            # whole reason this action works on Tyler's current config. The limit
            # is a protocol constraint, not ours: it must land in 5..100.
            found = await g.query_members(query=needle, limit=max(5, min(limit, 100)))
        except asyncio.TimeoutError:
            problems.append(f"{g.name}: member query timed out")
            continue
        except (discord.HTTPException, discord.ClientException) as e:
            problems.append(f"{g.name}: {getattr(e, 'text', None) or e}")
            continue
        for m in found:
            # query_members matched a prefix of some name; _matched_field says which
            # one, and falls back when the hit came from a field it does not read.
            _record(hits, m, g, _matched_field(m, low) or "name_prefix")

    matches = sorted(hits.values(), key=lambda r: (_rank(r, low), r["name"].lower()))
    out = {
        "query": needle,
        "method": "member_cache" if full_cache else "gateway_prefix",
        "searched_guilds": [g.name for g in guilds],
        "count": len(matches),
        "matches": matches[:limit],
        "truncated": len(matches) > limit,
    }
    if not full_cache:
        # Said on every call, not just the empty ones. A prefix search that finds
        # two Alexes looks complete, and the third one it could never have matched
        # is exactly what someone would go on to act on.
        out["note"] = (
            "Server Members intent is off, so this was a prefix search: names that "
            "merely CONTAIN the query were not searched. Set intents.members true in "
            "control.json (and enable it in the Dev Portal) for full substring search."
        )
    if problems:
        out["problems"] = problems
    return out


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


@action("what_i_did", identity.READ,
        "Your OWN action record - what you actually did, read from the log the bot "
        "writes on every capability run. Use this whenever you are asked what you "
        "did, whether something ran, or what a pc_task actually got up to. Answer "
        "from what this returns, never from what you remember. "
        "TWO FIELDS DECIDE WHETHER SILENCE MEANS ANYTHING: `covers` is the real "
        "time span the log spans, and `covered` says whether the window you asked "
        "about falls inside it. If `covered` is false, the record does not reach "
        "back that far - say exactly that, and do NOT say the thing did not "
        "happen. Quote the timestamps you find; they are the evidence.",
        {"limit": {"type": "int", "desc": "How many entries, newest first. Default 20, max 200."},
         "only": {"type": "str", "desc": "Only this capability name, e.g. pc_task."},
         "since_minutes": {"type": "int", "desc": "Only the last N minutes. Sets `covered`."},
         "actor": {"type": "str", "desc": "Only this actor id, or 'code-session'."}},
        # Third-party text rides along: a logged read_channel result contains the
        # messages it read. Without this, reading the log would launder a
        # stranger's words into an untainted turn and back into pc_task's reach.
        taints=True)
async def _what_i_did(ctx, p):
    from benham.core import selfrecord
    out = selfrecord.read(
        limit=int(p.get("limit") or 20),
        action=p.get("only") or None,
        since_minutes=(int(p["since_minutes"]) if p.get("since_minutes") else None),
        actor=p.get("actor") or None)
    if not out["entries"]:
        # Say which of the two silences this is, in the payload itself, rather than
        # leaving the model to infer it from a bare empty list. Inferring it wrong
        # is precisely the 2026-08-15 failure this capability exists to prevent.
        out["note"] = ("No matching entries. " + (
            "The log does NOT cover the window you asked about, so this is not "
            "evidence that nothing happened."
            if out["covered"] is False else
            "The log DOES cover that window, so nothing matching was recorded."))
    return out


@action("answer_conversation", identity.MANAGE,
        "Record that the message you were just sent ANSWERS the open question. Use "
        "it only when there is an open question and this really does answer it - "
        "and when you use it, SAY SO in your reply, in your own words. Tyler chose "
        "this deliberately: a reply to the question binds by itself and needs no "
        "tool, but anything else is your judgement, and a judgement that is not "
        "announced is indistinguishable from swallowing what he said. If you are "
        "unsure, do not call this - ask him whether he meant it as the answer.",
        {"id": {"type": "str", "required": True, "desc": "Conversation id, e.g. c7"},
         "answer": {"type": "str", "required": True,
                    "desc": "What he said, as he said it - do not summarise"}},
        origins={policy.Origin.OWNER_DM, policy.Origin.LOCAL_CLI})
async def _answer_conversation(ctx, p):
    from benham.core import conversations
    conv = conversations.get(str(p["id"]))
    if conv is None:
        raise ActionError(f"no conversation {p['id']!r}")
    if conv.get("state") not in conversations.LIVE_STATES:
        raise ActionError(f"{conv['id']} is {conv['state']} - it is not waiting on an answer")
    if int(conv["counterparty"]) != int(ctx.actor_id or 0):
        # The one open question belongs to whoever was asked. Recording someone
        # else's answer against it would put words in their mouth on the record.
        raise ActionError(f"{conv['id']} is waiting on someone else, not you")
    conversations.answer(conv["id"], str(p["answer"]), bound_by="judged")
    return {"status": "answered", "id": conv["id"], "bound_by": "judged",
            "question": conv["question"],
            "note": "Tell him you took this as the answer - a judged binding that "
                    "is not announced is the failure mode this exists to avoid."}


@action("advance_conversation", identity.SPEAK,
        "Move one waiting conversation to its next beat: ASK the question if it has "
        "never been delivered, send the nudge that is due, or - once its nudge "
        "budget is spent - bank it and tell the owner it went unanswered. Takes only "
        "a conversation id; the recipient and the words both come from the stored "
        "record.",
        {"id": {"type": "str", "required": True, "desc": "Conversation id, e.g. c7"}},
        # SYSTEM, because the whole point of stage 3 is that a loop closes with no
        # session running. This is the ONLY outward action an automated caller can
        # reach, and it is safe to grant precisely because it cannot choose anything:
        # the counterparty and the question come from a conversation a human opened,
        # the nudge count is capped in conversations.py, and the 15-minute clock
        # rate-limits it. Granting dm_user to SYSTEM would have been the lazy version
        # of this and would have handed a timer the ability to message anyone
        # anything.
        origins=policy.DEFAULT_ORIGINS | {policy.Origin.SYSTEM},
        outward=True)
async def _advance_conversation(ctx, p):
    from benham.core import conversations
    conv = conversations.get(str(p["id"]))
    if conv is None:
        raise ActionError(f"no conversation {p['id']!r}")
    if conv.get("state") not in conversations.LIVE_STATES:
        raise ActionError(f"{conv['id']} is {conv['state']} - nobody is being waited on")

    who = int(conv["counterparty"])
    user = await ctx.user(who)
    ch = user.dm_channel or await user.create_dm()

    # BEAT ZERO - the question has never actually been sent. This was missing when
    # the loop shipped, and the symptom was ugly: the first tick greeted a question
    # nobody had been asked with "still after this one", which reads as a reminder
    # about a conversation the other person has no memory of. Delivering is NOT a
    # nudge and must not consume the budget.
    if not (conv.get("ask_message_ids") or []):
        body = f"{conv['question']}"
        if conv.get("project"):
            body += f"\n\n_(about {conv['project']})_"
        sent = await ch.send(body)
        conversations.record_ask_message(conv["id"], sent.id)
        # Restart the nudge clock from DELIVERY, not from opening. open() assumes
        # prompt delivery; if the bot was down when the ask was made, the 15 minutes
        # would otherwise have elapsed before the person ever saw it.
        conversations.restart_clock(conv["id"])
        return {"status": "asked", "id": conv["id"], "counterparty": who,
                "message_id": sent.id}

    if int(conv.get("nudges", 0)) < conversations.MAX_NUDGES:
        # Quote the question rather than paraphrasing it. A nudge that restates the
        # ask in new words reads as a second, different question.
        sent = await ch.send("still after this one when you get a sec:\n\n"
                             f"> {conv['question']}")
        # Record the nudge as a thing a reply can bind to. Replying to the message
        # that just arrived is the most natural way anyone answers, and without this
        # the certain half of binding would fail exactly then.
        conversations.record_ask_message(conv["id"], sent.id)
        conversations.nudge(conv["id"])
        return {"status": "nudged", "id": conv["id"],
                "nudges": int(conv["nudges"]) + 1, "counterparty": who}

    conversations.bank(conv["id"])
    told_owner = False
    owner_id = sorted(identity.OWNER_IDS)[0]
    # No report when the owner IS the person who did not answer. He has already had
    # the question twice; a third message telling him he did not reply to himself is
    # noise, and the banked question stays readable either way.
    if who != owner_id:
        owner = await ctx.user(owner_id)
        och = owner.dm_channel or await owner.create_dm()
        await och.send(
            f"no answer from <@{who}> after {conversations.MAX_NUDGES} nudges - "
            "banked it.\n\n"
            f"> {conv['question']}\n\n"
            f"({conv['id']}, asked for: {conv['purpose']})")
        told_owner = True
    return {"status": "banked", "id": conv["id"], "counterparty": who,
            "owner_told": told_owner}


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
        # discord.py wants a real MessageReference here, not a bare Object.
        # fail_if_not_exists=False: if the referenced message was deleted, send
        # as a normal message rather than erroring the whole action.
        kw["reference"] = discord.MessageReference(
            message_id=int(p["reply_to"]), channel_id=ch.id,
            fail_if_not_exists=False)
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


MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # Discord's per-message payload limit
MAX_UPLOAD_FILES = 10                 # Discord's per-message file count


def _outgoing_paths(p):
    """The local files a send was asked to attach, in order.

    `path` and `paths` both exist because `path` is the older parameter and the
    common case is still one file; accepting only a list would have broken every
    existing caller to spare one pair of brackets.
    """
    if p.get("paths"):
        return [str(x) for x in p["paths"]]
    return [str(p["path"])] if p.get("path") else []


def _load_files(p):
    """Read the outgoing files into discord.File objects. Returns (files, names, bytes).

    Every limit is Discord's, checked locally because the server-side failure is a
    bare 413 that names neither the file nor the size - and with several attachments
    "one of these was too big" is not a useful thing to be told.
    """
    paths = _outgoing_paths(p)
    if len(paths) > MAX_UPLOAD_FILES:
        raise ActionError(f"Discord takes at most {MAX_UPLOAD_FILES} files per message, "
                          f"got {len(paths)}")
    files, names, total = [], [], 0
    for path in paths:
        if not os.path.isfile(path):
            raise ActionError(f"no file at {path}")
        size = os.path.getsize(path)
        if size > MAX_UPLOAD_BYTES:
            raise ActionError(f"{path} is {size / 1048576:.1f}MB; Discord's limit is 25MB here")
        total += size
        if total > MAX_UPLOAD_BYTES:
            raise ActionError(f"those {len(paths)} files total {total / 1048576:.1f}MB; "
                              f"Discord's limit is 25MB per message")
        with open(path, "rb") as fh:
            files.append(discord.File(io.BytesIO(fh.read()),
                                      filename=os.path.basename(path),
                                      spoiler=bool(p.get("spoiler"))))
        names.append(os.path.basename(path))
    return files, names, total


@action("send_file", identity.SPEAK, "Upload one or more local files to a channel.",
        {"channel_id": {"type": "int", "required": True},
         "path": {"type": "str", "desc": "Local path on Tyler's PC"},
         "paths": {"type": "list", "desc": "Several local paths at once (max 10, 25MB total)"},
         "content": {"type": "str", "desc": "Message text alongside the files"},
         "spoiler": {"type": "bool"}},
        outward=True, posts=True)
async def _send_file(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    files, names, total = _load_files(p)
    if not files:
        raise ActionError("send_file needs `path` (one file) or `paths` (several)")
    sent = await ch.send(content=p.get("content") or None, files=files)
    return {"status": "sent", "message_id": sent.id, "channel": str(ch),
            "filenames": names, "bytes": total, "jump_url": sent.jump_url}


@action("dm_user", identity.SPEAK, "Send a direct message to a user, with files if wanted.",
        {"user_id": {"type": "int", "required": True},
         "content": {"type": "str", "desc": "Message text (optional when sending a file)"},
         "path": {"type": "str", "desc": "Local file to attach"},
         "paths": {"type": "list", "desc": "Several local files (max 10, 25MB total)"},
         "spoiler": {"type": "bool"}},
        outward=True)
async def _dm_user(ctx, p):
    u = await ctx.user(p["user_id"])
    ch = u.dm_channel or await u.create_dm()
    files, names, total = _load_files(p)
    content = str(p["content"]) if p.get("content") else None
    # Discord rejects a message with neither text nor a file, with an error about
    # the message being empty rather than about the call being wrong.
    if not content and not files:
        raise ActionError("dm_user needs `content`, a file, or both")
    try:
        sent = await ch.send(content=content, files=files or None)
    except discord.Forbidden:
        raise ActionError(
            f"{u} has DMs closed to server members, or shares no server with Benham"
        )
    out = {"status": "sent", "message_id": sent.id, "to": str(u), "user_id": u.id}
    if names:
        out["filenames"], out["bytes"] = names, total
    return out


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


# --- channel permission overwrites ---------------------------------------
#
# The permission names belong to Discord, there are 59 of them, and several are
# aliases for one bit: `view_channel` and `read_messages` are the same flag, so
# are `manage_roles` and `manage_permissions`. Both facts decide how this is
# built. The names are checked against discord.py's own table rather than a list
# kept here, because a hand-copied list goes stale the next time Discord ships a
# permission - and a misspelled flag set on a PermissionOverwrite raises nothing
# at all, it just quietly does not apply.

_PERM_NAMES = frozenset(discord.Permissions.VALID_FLAGS)


def _perm_name(raw, field):
    """One permission name, validated, with suggestions when it is wrong."""
    n = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    if n in _PERM_NAMES:
        return n
    near = sorted(x for x in _PERM_NAMES if n and (n in x or x in n))[:6]
    raise ActionError(
        f"{field}: {raw!r} is not a Discord permission."
        + (f" Did you mean: {', '.join(near)}?" if near else
           " Names look like: view_channel, send_messages, read_message_history, "
           "connect, speak.")
    )


def _perm_key(name):
    """The single flag `name` refers to, so an alias cannot land on both sides.

    allow=['view_channel'] with deny=['read_messages'] names one bit twice, and
    setattr would simply let the second write win - leaving the caller told that
    their allow succeeded while the channel says the opposite.
    """
    probe = discord.PermissionOverwrite(**{name: True})
    return next(k for k, v in probe if v is True)


def _perm_list(raw, field):
    """Validate one allow/deny list. Returns (names as written, {flag: as written})."""
    names, keys = [], {}
    for item in (raw or []):
        if not str(item).strip():
            continue
        n = _perm_name(item, field)
        key = _perm_key(n)
        if key in keys:      # the same bit twice, possibly under two spellings
            continue
        keys[key] = n
        names.append(n)
    return names, keys


def _overwrite_state(ow):
    """The half of an overwrite that is actually set, ignoring inherited flags."""
    return {"allow": sorted(k for k, v in ow if v is True),
            "deny": sorted(k for k, v in ow if v is False)}


@action("set_channel_permissions", identity.MANAGE,
        "Allow or deny specific permissions for one role in one channel (a channel "
        "overwrite). Merges onto whatever overwrite the role already has, so naming "
        "one permission leaves the others alone; `reset` drops the overwrite entirely "
        "and the channel falls back to the role's server-wide permissions.",
        {"channel_id": {"type": "int", "required": True},
         "role_id": {"type": "int", "required": True,
                     "desc": "Role to set the overwrite for (list_roles has the ids)"},
         "allow": {"type": "list",
                   "desc": "Permission names to allow, e.g. ['view_channel', 'send_messages']"},
         "deny": {"type": "list", "desc": "Permission names to deny, same spelling"},
         "reset": {"type": "bool",
                   "desc": "Remove this role's overwrite here, back to the server "
                           "default. Ignores allow/deny."},
         "reason": {"type": "str"}},
        outward=True, taints=True)
async def _set_channel_permissions(ctx, p):
    ch = await ctx.channel(p["channel_id"])
    g = getattr(ch, "guild", None)
    if g is None or not hasattr(ch, "set_permissions"):
        raise ActionError(
            f"{ch} cannot carry permission overwrites - only server channels and "
            "categories can. A thread inherits its parent channel's, so set them "
            "on the parent instead."
        )
    role = g.get_role(int(p["role_id"]))
    if role is None:
        raise ActionError(f"no role {p['role_id']} in {g.name} - list_roles has the ids")
    # Discord answers a missing Manage Permissions with a bare 403 that names
    # neither the channel nor the permission. Same reasoning as add_role's
    # hierarchy check: saying it here is the difference between a fixable error
    # and a mystery.
    if not ch.permissions_for(g.me).manage_permissions:
        raise ActionError(
            f"Benham lacks Manage Permissions in #{ch.name}, so it cannot change "
            "overwrites there. Note Discord also refuses to let a bot grant a "
            "permission it does not hold itself."
        )

    reason = p.get("reason") or "via Benham"
    before = _overwrite_state(ch.overwrites_for(role))

    if p.get("reset"):
        had = before["allow"] or before["deny"]
        await ch.set_permissions(role, overwrite=None, reason=reason)
        return {"status": "reset", "channel": ch.name, "channel_id": ch.id,
                "role": role.name, "role_id": role.id,
                "before": before, "after": {"allow": [], "deny": []},
                "message": (f"Cleared **{role.name}**'s overwrite in **#{ch.name}** "
                            f"({g.name}) - it now inherits its server-wide permissions."
                            if had else
                            f"**{role.name}** already had no overwrite in "
                            f"**#{ch.name}** ({g.name}) - nothing to clear.")}

    allow, allow_keys = _perm_list(p.get("allow"), "allow")
    deny, deny_keys = _perm_list(p.get("deny"), "deny")
    if not allow and not deny:
        raise ActionError("nothing to change - pass `allow`, `deny`, or reset=true")
    clash = sorted(set(allow_keys) & set(deny_keys))
    if clash:
        pairs = ", ".join(
            allow_keys[k] if allow_keys[k] == deny_keys[k]
            else f"{allow_keys[k]}/{deny_keys[k]} (the same permission)" for k in clash)
        raise ActionError(f"cannot allow and deny the same permission: {pairs}")

    ow = ch.overwrites_for(role)      # merge onto what is there, do not clobber it
    for n in allow:
        setattr(ow, n, True)
    for n in deny:
        setattr(ow, n, False)
    await ch.set_permissions(role, overwrite=ow, reason=reason)

    bits = []
    if allow:
        bits.append("allowed " + ", ".join(f"`{n}`" for n in allow))
    if deny:
        bits.append("denied " + ", ".join(f"`{n}`" for n in deny))
    return {"status": "permissions_set", "channel": ch.name, "channel_id": ch.id,
            "role": role.name, "role_id": role.id,
            "allowed": allow, "denied": deny,
            "before": before, "after": _overwrite_state(ow),
            "message": (f"**{role.name}** in **#{ch.name}** ({g.name}): "
                        + " and ".join(bits) + ".")}


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


@action("guest_quiet", identity.MANAGE,
        "Temporarily silence the guest brain for one user, so Claude can hold an "
        "outreach conversation through the dm pipeline without the autonomous brain "
        "talking over it. The mute expires on its own (default 60 min, max 240); the "
        "user's messages still reach inbox.jsonl, they just get no brain reply. Not "
        "outward in the registry's sense: nothing is posted and nobody's access "
        "changes - the person is being answered, by Claude instead of the brain.",
        {"user_id": {"type": "int", "required": True, "desc": "Guest to quiet"},
         "minutes": {"type": "int", "desc": "How long (default 60, max 240)"}})
async def _guest_quiet(ctx, p):
    # Imported here, not at module top: guest.py sits above this file's import
    # position in bot.py's order, and a top-level import would be the first
    # capabilities -> guest edge - one refactor away from a cycle.
    from benham.guest import guest as guest_mod
    uid = int(p["user_id"])
    if not guest_mod.is_known_guest(uid):
        # Quieting a stranger would "work" (a no-op mute nobody consults), which
        # is exactly how a typo'd id looks like success. Refuse instead.
        raise ActionError(f"user {uid} is not an enabled guest - nothing to quiet")
    minutes = int(p.get("minutes") or guest_mod.QUIET_DEFAULT_MINUTES)
    until = guest_mod.quiet(uid, minutes)
    return {"status": "quieted", "user_id": uid,
            "minutes": max(1, min(minutes, guest_mod.QUIET_MAX_MINUTES)),
            "quiet_until": datetime.fromtimestamp(until, timezone.utc).isoformat()}


@action("guest_wake", identity.MANAGE,
        "Lift a guest_quiet early - the guest brain resumes replying to that user.",
        {"user_id": {"type": "int", "required": True, "desc": "Guest to unmute"}})
async def _guest_wake(ctx, p):
    from benham.guest import guest as guest_mod
    uid = int(p["user_id"])
    was_quiet = guest_mod.wake(uid)
    return {"status": "woken" if was_quiet else "was_not_quiet", "user_id": uid}


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
        "in the Benhams-inbox folder - read/edit files, run commands, use his "
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
    from benham.core import codesession
    if not codesession.ENABLED:
        raise ActionError("PC access is off (pc.enabled in control.json)")

    # Report each tool as it is used, rather than only a summary once the task is
    # over. run_task always accepted this callback and nothing ever passed one, so
    # a session doing things on the machine was invisible until it finished - which
    # is the wrong half of the timeline to be able to see. watch_pc.py gives the
    # detailed live view; this is the coarse one that lands in bot.log next to
    # everything else.
    async def _progress(kind, detail):
        ctx.log(f"  pc_task ... {detail if kind == 'tool' else '(thinking)'}")
        if ctx.on_progress:
            await ctx.on_progress(kind, detail)

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
            # A bare string is ONE item, not a list of characters. Without this,
            # paths="C:/x.png" becomes ['C', ':', '/', ...] and the failure reads
            # "no file at C", which describes nothing that happened.
            return [value] if isinstance(value, str) else list(value)
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
              call_ctx=None, on_progress=None, source_message_id=None,
              on_attach=None):
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
    ctx = Ctx(client, log, dry_run=False, actor_id=actor_id, on_progress=on_progress,
              source_channel_id=getattr(call_ctx, "channel_id", None),
              source_message_id=source_message_id, on_attach=on_attach)

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
    # Name the files. This preview is what Tyler reads before approving a send he
    # cannot take back, and `send_file (channel_id=...)` does not tell him whether
    # what is about to leave the machine is a screenshot or environ.env.
    paths = _outgoing_paths(params)
    if paths:
        bits.append("files=" + ", ".join(os.path.basename(x) for x in paths))
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
