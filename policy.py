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

    ALL = frozenset({OWNER_DM, OWNER_GUILD, OWNER_VOICE, LOCAL_CLI, SYSTEM})

    # Origins that carry a human actor whose identity can be checked.
    HUMAN = frozenset({OWNER_DM, OWNER_GUILD, OWNER_VOICE})


# What a capability may be reached from when it does not say otherwise. Every human
# route plus the local CLI; SYSTEM is deliberately absent so an automated caller
# cannot reach a capability that was never meant for one.
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


# Order matters: context validity, then whether this route may reach this capability
# at all, then the conditions that depend on the state of the turn.
RULES = (
    rule_context_present,
    rule_origin_allowed,
    rule_agent_guild,
    rule_blocked_when_tainted,
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
    if ctx.origin == Origin.OWNER_DM:
        return _ALLOW
    if ctx.origin == Origin.OWNER_GUILD:
        if ctx.guild_id is not None and int(ctx.guild_id) in identity.AGENT_GUILDS:
            return _ALLOW
        return _deny("engage_guild",
                     f"guild {ctx.guild_id} is not on agent_guilds in control.json")
    return _deny("engage_origin",
                 f"the text agent is not reachable from {ctx.origin}")


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
