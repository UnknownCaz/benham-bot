"""
policy.py - may this call proceed?

identity.py answers "who is this person". This answers "may this particular call,
arriving from this particular direction, run". They were the same thing until the
capability surface grew enough that the answer stopped depending only on who asked.

The reason this file exists is a bug rather than a theory. identity.agent_allowed()
was written, tested, and passed - and was never called by anything in production,
because bot.on_message re-implemented a subset of it inline and left out the guild
check. The test asserted "the owner cannot drive the agent from Chillbar" and went
green while the live code happily did exactly that. A gate that is written but not
wired looks identical, from the test suite, to a gate that works.

So the design goal is narrow and specific: make it impossible for a rule to exist
without being enforced. A capability declares what it requires; one function decides;
every entry point must say where it came from. There is nowhere to put a check that
does not run, because there are no other places checks live.

Two properties do the work:

  Fail closed. A call with no context, or an origin this file does not recognise, is
  denied. Threading a context through every call site is mechanical and therefore
  easy to get wrong once - so the failure mode for getting it wrong is a loud denial,
  not a silent permission.

  SYSTEM is opt-in. Automated callers (startup, the watchdog) get an origin that
  reaches almost nothing. A capability must name SYSTEM explicitly to be reachable
  without a human behind it.

Stage 1 of 5: this file currently owns only the origin rules. The owner check, the
destructive guild allowlist, the posting allowlist, always-confirm and taint-outward
still live where they were, and move here one at a time - each with the system fully
working in between, so a regression is always attributable to a single step.
"""

import identity


class Origin:
    """Where a request came from. Not who sent it - the direction it arrived from.

    The distinction matters because the same person carries different assurance
    depending on the channel. A DM is a private two-party conversation. A guild
    mention happens in a room other people can write in, where what Benham reads
    around the request is attacker-influenced. Those are not equivalent, and
    pc_task is the case that makes the difference concrete.
    """

    OWNER_DM = "owner_dm"        # private DM with the bot
    OWNER_GUILD = "owner_guild"  # @mention in a guild channel
    OWNER_VOICE = "owner_voice"  # spoken in a voice channel
    LOCAL_CLI = "local_cli"      # outbox/do.py - the caller already has the machine
    SYSTEM = "system"            # startup, watchdog, scheduled work; no human behind it
    GUEST_DM = "guest_dm"        # DM from a whitelisted non-owner; conversation only

    ALL = frozenset({OWNER_DM, OWNER_GUILD, OWNER_VOICE, LOCAL_CLI, SYSTEM,
                     GUEST_DM})

    # Origins that carry a human actor whose identity can be checked.
    #
    # GUEST_DM is deliberately in here even though a guest is never an owner, and
    # that is the point: rule_owner checks exactly this set, so listing a guest
    # origin as human makes every capability refuse it without a line being written
    # about guests. The alternative - leaving guests out so they skip rule_owner -
    # would be a set membership standing between a stranger and 47 actions.
    HUMAN = frozenset({OWNER_DM, OWNER_GUILD, OWNER_VOICE, GUEST_DM})

    # Origins belonging to someone who is not the owner. Nothing in this file grants
    # them anything; it exists so callers can ask "is this a guest route" without
    # re-listing the members and drifting from this file later.
    GUEST = frozenset({GUEST_DM})


# What a capability may be reached from when it does not say otherwise. Every OWNER
# route plus the local CLI. Two absences are deliberate and load-bearing:
#
#   SYSTEM, so an automated caller cannot reach a capability never meant for one.
#   GUEST_DM, so every capability in the registry is unreachable by a guest without
#   anyone having to remember to exclude them. A new capability added next year is
#   guest-proof on the day it is written, because the default it inherits says so.
#
# This is the second of the two independent denials guests get; rule_owner is the
# first. Either alone would be sufficient, which is the reason for having both.
DEFAULT_ORIGINS = frozenset({
    Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE, Origin.LOCAL_CLI,
})


class CallContext:
    """Where one call came from, carried from the entry point to the decision.

    `tainted` travels with the context rather than being looked up, because it is a
    property of the turn in progress, not of the world - the same action is fine at
    the start of a conversation and gated after Benham has read a channel.
    """

    __slots__ = ("origin", "actor_id", "guild_id", "channel_id", "tainted")

    def __init__(self, origin, actor_id=None, guild_id=None, channel_id=None,
                 tainted=False):
        self.origin = origin
        self.actor_id = actor_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.tainted = tainted

    # Constructors named for the call site, so reading bot.py tells you the origin
    # without having to remember which string means what.

    @classmethod
    def owner_dm(cls, actor_id, channel_id=None, tainted=False):
        return cls(Origin.OWNER_DM, actor_id=actor_id, channel_id=channel_id,
                   tainted=tainted)

    @classmethod
    def owner_guild(cls, actor_id, guild_id, channel_id=None, tainted=False):
        return cls(Origin.OWNER_GUILD, actor_id=actor_id, guild_id=guild_id,
                   channel_id=channel_id, tainted=tainted)

    @classmethod
    def owner_voice(cls, actor_id, guild_id, channel_id=None):
        return cls(Origin.OWNER_VOICE, actor_id=actor_id, guild_id=guild_id,
                   channel_id=channel_id)

    @classmethod
    def guest_dm(cls, actor_id, channel_id=None):
        """A DM from a whitelisted non-owner.

        Always tainted. A guest's own message is text a person other than the owner
        wrote, which is the exact thing `tainted` means - so the flag is set at
        construction rather than left to a caller to remember. Guests reach no
        capability, so today this changes nothing; it is here so that if a later
        change ever does hand them one, it arrives already carrying the truth about
        where its input came from instead of claiming to be clean.
        """
        return cls(Origin.GUEST_DM, actor_id=actor_id, channel_id=channel_id,
                   tainted=True)

    @classmethod
    def local(cls, actor_id=None):
        """A request from the filesystem (outbox/do.py).

        Trusted at roughly the level of a DM, on the reasoning that writing into
        outbox/ already requires having the machine - so it is not a weaker channel
        than Discord, and treating it as one would cost capability for no security.
        """
        return cls(Origin.LOCAL_CLI, actor_id=actor_id)

    @classmethod
    def system(cls, guild_id=None):
        return cls(Origin.SYSTEM, guild_id=guild_id)

    def for_target(self, guild_id, channel_id=None):
        """A copy naming the guild/channel this call actually targets.

        The caller's own guild and the call's target are not the same thing and it
        matters here: Tyler DMs Benham (ctx.guild_id is None) and asks it to purge a
        channel in Testing. The rule that decides that is about Testing, not about
        the DM. Resolving the target needs an async channel lookup and validated
        parameters, which is why the target rules run in a second phase rather than
        alongside the caller rules.
        """
        return CallContext(self.origin, self.actor_id, guild_id,
                           channel_id if channel_id is not None else self.channel_id,
                           self.tainted)

    def with_taint(self, tainted=True):
        """A copy with the taint flag set. Contexts are treated as immutable so a
        nested call cannot quietly clear a taint its caller had set."""
        return CallContext(self.origin, self.actor_id, self.guild_id,
                           self.channel_id, tainted)

    def __repr__(self):
        return (f"CallContext(origin={self.origin!r}, actor={self.actor_id}, "
                f"guild={self.guild_id}, tainted={self.tainted})")


class Decision:
    """ALLOW, DENY or CONFIRM, with a reason a human can act on.

    A refusal is a message Tyler reads on his phone, so `reason` is written for him
    and not for a log: it says what was refused and what would change it.
    """

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"

    __slots__ = ("verdict", "reason", "rule")

    def __init__(self, verdict, reason="", rule=""):
        self.verdict = verdict
        self.reason = reason
        self.rule = rule        # which rule decided, for the audit line

    @property
    def allowed(self):
        return self.verdict == Decision.ALLOW

    @property
    def denied(self):
        return self.verdict == Decision.DENY

    @property
    def needs_confirm(self):
        return self.verdict == Decision.CONFIRM

    def __repr__(self):
        return f"Decision({self.verdict}{', ' + self.rule if self.rule else ''})"


_ALLOW = Decision(Decision.ALLOW)


def _deny(rule, reason):
    return Decision(Decision.DENY, reason, rule)


# --------------------------------------------------------------------------
# Rules. Each takes (action, ctx) and returns a Decision or None to pass.
# Order is fixed and meaningful; authorize() walks them and the first
# non-passing answer wins.
# --------------------------------------------------------------------------

def rule_context_present(action, ctx):
    """No context means no decision can be made, which means no.

    This is the rule that makes threading mistakes safe. Adding a call site and
    forgetting to pass a context produces an immediate, obvious refusal rather than
    an unguarded call that nobody notices for months.
    """
    if ctx is None:
        return _deny("context_present",
                     f"`{action.name}` was called without a call context, so there is "
                     "no way to tell where the request came from. Refusing.")
    if ctx.origin not in Origin.ALL:
        return _deny("context_present",
                     f"`{action.name}` arrived with an unrecognised origin "
                     f"{ctx.origin!r}. Refusing.")
    return None


def rule_owner(action, ctx):
    """A request carrying a human actor must carry Tyler.

    Stage 2. The check already existed at the entry points - on_message and
    handle_auto_reply both refuse a non-owner before anything else happens - and
    those stay exactly where they are. This is not a replacement for them; it is the
    same rule stated once more at the point where a capability is actually about to
    run, so that a future entry point that forgets the early check still cannot get
    past here.

    Only human origins are checked. LOCAL_CLI has no Discord actor to verify (its
    authority comes from already having the machine) and SYSTEM has no actor at all,
    so demanding an owner id from either would deny every automated call and every
    CLI invocation that did not bother to pass one. Both are constrained instead by
    rule_origin_allowed, which is the appropriate control for them.
    """
    if ctx.origin not in Origin.HUMAN:
        return None
    if identity.is_owner(ctx.actor_id):
        return None
    return _deny("owner",
                 f"`{action.name}` was requested by user {ctx.actor_id}, who is not "
                 "my owner. I only take direction from one person.")


def rule_origin_allowed(action, ctx):
    """The capability must permit this direction of arrival.

    This is where pc_task's restriction lives: a declaration on the capability
    rather than a check somebody has to remember to write in the right file.
    """
    allowed = action.origins if action.origins is not None else DEFAULT_ORIGINS
    if ctx.origin in allowed:
        return None
    friendly = {
        Origin.OWNER_DM: "a direct DM",
        Origin.OWNER_GUILD: "a mention in a server channel",
        Origin.OWNER_VOICE: "a voice channel",
        Origin.LOCAL_CLI: "the local CLI",
        Origin.SYSTEM: "an automated trigger",
    }
    ways = ", ".join(sorted(friendly.get(o, o) for o in allowed))
    return _deny("origin_allowed",
                 f"`{action.name}` cannot be reached from {friendly.get(ctx.origin, ctx.origin)}. "
                 f"It is only available from: {ways}.")


def rule_agent_guild(action, ctx):
    """A guild mention only drives the agent from a guild on the agent list.

    This is the bug that prompted the whole file. identity.agent_allowed() encoded
    it, nothing called it, and bot.on_message engaged the agent on any mention from
    the owner in any guild Benham happened to be in.
    """
    if ctx.origin != Origin.OWNER_GUILD:
        return None
    if ctx.guild_id is not None and int(ctx.guild_id) in identity.AGENT_GUILDS:
        return None
    return _deny("agent_guild",
                 f"`{action.name}` was requested by mention in guild {ctx.guild_id}, "
                 "which is not on the agent_guilds list in control.json. DM me instead.")


def rule_blocked_when_tainted(action, ctx):
    """Some capabilities are off the table entirely once the turn is tainted.

    Distinct from the outward-action taint rule, which downgrades to CONFIRM. This
    one is an outright no, for capabilities where "ask Tyler first" is not a
    sufficient answer - pc_task being the case: a turn carrying text strangers wrote
    should not be able to start a session on his actual machine at all, even with a
    prompt in front of it, because the prompt is judged against a request that was
    itself shaped by the poisoned content.
    """
    if action.blocked_when_tainted and ctx.tainted:
        return _deny("blocked_when_tainted",
                     f"`{action.name}` is blocked because I have already read content "
                     "other people wrote in this conversation. Ask me again in a fresh "
                     "message and I can do it.")
    return None


def rule_destructive_guild(action, ctx):
    """Tier-3 actions run only in guilds on the destructive allowlist.

    Stage 3. Moved out of capabilities.run unchanged in behaviour: the same
    identity.destructive_allowed() predicate, the same hand-edited config, the same
    refusal that no confirmation can unlock. What changes is that it is now stated
    next to the other rules instead of buried in the middle of the execution path,
    where it was reachable only by reading run() top to bottom.

    A target rule, not a caller rule - it depends on which guild the action points
    at, which is not known until the parameters are validated and the channel is
    resolved. ctx here is the target context from CallContext.for_target.

    Still evaluated before any dry-run, so a destructive action aimed at a guild it
    may not touch never reports that guild's contents. Naming what is inside a
    channel you are not allowed to touch is itself a small leak.
    """
    if not action.destructive:
        return None
    if identity.destructive_allowed(ctx.guild_id):
        return None
    where = "a DM" if ctx.guild_id is None else f"guild {ctx.guild_id}"
    return _deny("destructive_guild",
                 f"`{action.name}` is destructive and {where} is not on the "
                 "destructive_guilds allowlist in control.json. No confirmation can "
                 "override this - the allowlist is edited by hand, on purpose.")


def rule_posting_scope(action, ctx):
    """Content may only enter channels on the posting allowlist.

    Stage 4. A target rule, and the bluntest one here: arithmetic rather than
    judgement. It does not consult who asked, why, whether the turn is tainted, or
    whether a confirmation was given. A refusal is not something a yes unlocks -
    which is why it must also hold under force=True, where every confirmation path
    ends up.

    Its job is the case no amount of care inside the model covers: Benham is
    invited to a server, somebody there writes text engineered to look like an
    instruction, and a later read pulls it into context. Every other defence
    against that ends in a judgement call somewhere. This one ends in set
    membership.
    """
    if not action.posts:
        return None
    if identity.posting_allowed(ctx.guild_id, ctx.channel_id):
        return None
    return _deny("posting_scope",
                 f"`{action.name}` would post into channel {ctx.channel_id} "
                 f"(guild {ctx.guild_id}), which is not on the posting allowlist in "
                 "control.json. This is a hard scope limit - it is not something a "
                 "confirmation unlocks.")


def _confirm(rule, reason):
    return Decision(Decision.CONFIRM, reason, rule)


def rule_always_confirm(action, ctx):
    """Destructive actions, and role changes, need an explicit yes every time.

    Stage 5. Returns CONFIRM rather than DENY: the caller is expected to produce a
    preview and park it, and a later call with force=True - meaning the
    confirmation already happened - skips this. `force` deliberately does not exist
    in this file. Policy says what a call needs; whether that need has already been
    met is the caller's bookkeeping, and mixing the two is how "already asked for
    it" turns into "no longer checked".
    """
    if action.needs_confirm:
        return _confirm("always_confirm",
                        f"`{action.name}` needs an explicit confirmation.")
    return None


def rule_outward_tainted(action, ctx):
    """An outward action stops being free once the turn has read stranger-written text.

    Stage 5, and the rule worth re-deriving rather than relocating - the version of
    this that lived in agent.py did not work. It built its preview by calling
    run(force=False) on the assumption that meant "dry run", but run() only
    dry-runs actions that need confirmation and this rule fires precisely on the
    ones that do not. So the message was really sent, the model was told "NOT
    EXECUTED", and confirming sent it twice.

    Stating it here fixes the shape as well as the bug: policy returns CONFIRM, and
    the single place that honours CONFIRM knows that a taint-induced one must never
    invoke the handler to describe itself, because only tier-3 handlers implement
    dry_run at all.

    The value of the rule is that it does not require the model to be un-foolable.
    Reading downgrades Benham's own authority, so the most a crafted message
    achieves is an action Tyler is shown and must approve.
    """
    if action.outward and ctx.tainted:
        return _confirm("outward_tainted",
                        f"`{action.name}` is outward and I have already read content "
                        "other people wrote in this conversation, so it needs your "
                        "approval.")
    return None


# Caller rules: everything decidable from who is asking and how they reached us.
# Order matters - context validity, then whether this route may reach this
# capability at all, then conditions that depend on the state of the turn.
RULES = (
    rule_context_present,
    rule_owner,
    rule_origin_allowed,
    rule_agent_guild,
    rule_blocked_when_tainted,
)

# Target rules: everything that depends on what the call points AT. Evaluated in a
# second phase because resolving the target needs validated parameters and an async
# channel lookup, neither of which should have to happen before an origin refusal.
# Deny rules first, then confirm rules. The order is load-bearing: first non-None
# wins, so an action that is refused outright never comes back asking to be
# confirmed - which would invite answering yes to something that was never on offer.
TARGET_RULES = (
    rule_destructive_guild,
    rule_posting_scope,
    rule_always_confirm,
    rule_outward_tainted,
)


def may_engage_agent(ctx):
    """Whether the text agent may run at all for a request from this direction.

    Separate from authorize() because it is a question about the conversation, not
    about a capability - and it has to be answerable before any capability is named,
    since the point is to not spend an API call at all. Refusing every individual
    tool afterwards would still have paid for the turn.

    This is the rule identity.agent_allowed() was written for and which nothing
    called. It lives here now, and bot.on_message asks it, so there is exactly one
    copy and it is on the live path.
    """
    if ctx is None or ctx.origin not in Origin.ALL:
        return _deny("engage_context", "No call context; refusing to engage.")
    # Guests never drive the tool-carrying agent. The owner check below would already
    # refuse them - GUEST_DM is in HUMAN and a guest is not an owner - but stating it
    # separately means the refusal names the actual reason, and means a future edit
    # that loosens the owner check cannot silently hand guests the tool loop.
    # Guest conversation is a different function entirely: may_chat_as_guest.
    if ctx.origin in Origin.GUEST:
        return _deny("engage_guest",
                     f"user {ctx.actor_id} is a guest; the tool agent is owner-only")
    # Same reasoning as rule_owner: on_message already refused a non-owner before
    # reaching here, and this says it again anyway. Spending an API call is itself
    # the thing being protected, so the check that decides it should not depend on
    # a caller having done its own.
    if ctx.origin in Origin.HUMAN and not identity.is_owner(ctx.actor_id):
        return _deny("engage_owner",
                     f"user {ctx.actor_id} is not my owner")
    if ctx.origin == Origin.OWNER_DM:
        return _ALLOW
    if ctx.origin == Origin.OWNER_GUILD:
        if ctx.guild_id is not None and int(ctx.guild_id) in identity.AGENT_GUILDS:
            return _ALLOW
        return _deny("engage_guild",
                     f"guild {ctx.guild_id} is not on agent_guilds in control.json")
    return _deny("engage_origin",
                 f"the text agent is not reachable from {ctx.origin}")


def may_chat_as_guest(ctx):
    """Whether a plain conversation may happen with a non-owner on this route.

    The counterpart to may_engage_agent, and deliberately a different function
    rather than a flag on it. What it authorises is not a weaker version of the
    agent - it is a different thing: text in, text out, no capability list handed to
    the model at all. Sharing one entry point between "may use tools" and "may not"
    is how the second quietly becomes the first.

    Structural checks only. Whether this guest has burned their daily quota is
    guest.py's business, because it is state rather than authority, and a decision
    that changes with a counter does not belong in the file that says what the rules
    are. Who counts as a guest lives in identity.py next to is_owner, for the same
    reason owner_ids does: it is a question about people, not about calls.
    """
    if ctx is None or ctx.origin not in Origin.ALL:
        return _deny("guest_context", "No call context; refusing to chat.")
    if ctx.origin not in Origin.GUEST:
        return _deny("guest_origin",
                     f"guest chat is not reachable from {ctx.origin}")
    if not identity.guest_enabled():
        return _deny("guest_disabled", "guest chat is switched off in control.json")
    if not identity.is_guest(ctx.actor_id):
        return _deny("guest_allowlist",
                     f"user {ctx.actor_id} is not on the guest allowlist")
    return _ALLOW


def authorize(action, ctx):
    """Decide whether one call may proceed. The single chokepoint.

    Returns a Decision. Stage 1 answers only ALLOW/DENY on origin grounds; the
    remaining rules (owner, destructive guild allowlist, posting scope, confirms)
    still live in capabilities.run and move here in later stages.
    """
    for rule in RULES:
        verdict = rule(action, ctx)
        if verdict is not None:
            return verdict
    return _ALLOW


def authorize_target(action, ctx):
    """Second phase: the rules that depend on what the call points at.

    Run after parameter validation and target resolution. Split from authorize()
    rather than merged because the caller rules must be answerable without touching
    the network - an origin refusal should not require resolving a channel, and
    should not report anything about one.
    """
    if ctx is None:
        return _deny("target_context",
                     f"`{action.name}` reached target checks without a context.")
    for rule in TARGET_RULES:
        verdict = rule(action, ctx)
        if verdict is not None:
            return verdict
    return _ALLOW
