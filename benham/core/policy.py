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

from benham.core import identity


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
    # DORMANT since 2026-08-16: voice was archived, so nothing constructs this any
    # more. Kept rather than deleted because test_policy's matrix asserts exactly
    # which capabilities each origin may reach, and an origin no capability can be
    # called from is the strongest possible statement of that - deleting it would
    # weaken the matrix to save one enum member. See archive/voice/.
    OWNER_VOICE = "owner_voice"  # spoken in a voice channel
    LOCAL_CLI = "local_cli"      # outbox/do.py - the caller already has the machine
    SYSTEM = "system"            # startup, watchdog, scheduled work; no human behind it
    GUEST_DM = "guest_dm"        # DM from a whitelisted non-owner; conversation only

    ALL = frozenset({OWNER_DM, OWNER_GUILD, OWNER_VOICE, LOCAL_CLI, SYSTEM,
                     GUEST_DM})

    # Origins that carry a human actor whose identity can be checked.
    #
    # GUEST_DM is in here because a guest IS a human actor - the set means "carries
    # a person whose identity can be checked", not "is allowed".
    #
    # CORRECTED 2026-08-17: this comment used to say rule_owner refuses guests
    # because of this membership. It does not, and has not since the guest
    # refactor - rule_owner explicitly steps aside for Origin.GUEST (see its
    # docstring: "guest origins are rule_guest's lane"). The two denials that
    # actually fire are rule_guest (nothing is granted, and it is fail-closed) and
    # rule_origin_allowed (GUEST_DM is not in DEFAULT_ORIGINS). test_policy pins
    # all three facts, including the step-aside, so this cannot drift again.
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
# This is one of the two independent denials guests get; rule_guest is the other
# (it is fail-closed, and nothing is granted). Either alone would be sufficient,
# which is the reason for having both. NOT rule_owner - that steps aside for guest
# origins; see the correction on Origin.HUMAN above.
DEFAULT_ORIGINS = frozenset({
    Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE, Origin.LOCAL_CLI,
})


class CallContext:
    """Where one call came from, carried from the entry point to the decision.

    `tainted` travels with the context rather than being looked up, because it is a
    property of the turn in progress, not of the world - the same action is fine at
    the start of a conversation and gated after Benham has read a channel.
    """

    __slots__ = ("origin", "actor_id", "guild_id", "channel_id", "tainted", "face")

    def __init__(self, origin, actor_id=None, guild_id=None, channel_id=None,
                 tainted=False, face=None):
        self.origin = origin
        self.actor_id = actor_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.tainted = tainted
        # Which bot identity this call acts AS (PLAN-second-face commit 3).
        # Resolved at construction rather than carried as None, so every rule
        # and every repr sees a real name. None means the primary face because
        # every pre-faces call site in the tree is a Benham call site - and the
        # copies below must CARRY it: a copy that quietly reverted to the
        # primary face would be the with_taint bug with a different field.
        self.face = face or identity.PRIMARY_FACE

    # Constructors named for the call site, so reading bot.py tells you the origin
    # without having to remember which string means what.

    @classmethod
    def owner_dm(cls, actor_id, channel_id=None, tainted=False, face=None):
        return cls(Origin.OWNER_DM, actor_id=actor_id, channel_id=channel_id,
                   tainted=tainted, face=face)

    @classmethod
    def owner_guild(cls, actor_id, guild_id, channel_id=None, tainted=False,
                    face=None):
        return cls(Origin.OWNER_GUILD, actor_id=actor_id, guild_id=guild_id,
                   channel_id=channel_id, tainted=tainted, face=face)

    @classmethod
    def owner_voice(cls, actor_id, guild_id, channel_id=None, face=None):
        return cls(Origin.OWNER_VOICE, actor_id=actor_id, guild_id=guild_id,
                   channel_id=channel_id, face=face)

    @classmethod
    def guest_dm(cls, actor_id, channel_id=None, face=None):
        """A DM from a whitelisted non-owner.

        Always tainted. A guest's own message is text a person other than the owner
        wrote, which is the exact thing `tainted` means - so the flag is set at
        construction rather than left to a caller to remember. Guests reach no
        capability, so today this changes nothing; it is here so that if a later
        change ever does hand them one, it arrives already carrying the truth about
        where its input came from instead of claiming to be clean.
        """
        return cls(Origin.GUEST_DM, actor_id=actor_id, channel_id=channel_id,
                   tainted=True, face=face)

    @classmethod
    def local(cls, actor_id=None, face=None):
        """A request from the filesystem (outbox/do.py).

        Trusted at roughly the level of a DM, on the reasoning that writing into
        outbox/ already requires having the machine - so it is not a weaker channel
        than Discord, and treating it as one would cost capability for no security.
        """
        return cls(Origin.LOCAL_CLI, actor_id=actor_id, face=face)

    @classmethod
    def system(cls, guild_id=None, face=None):
        return cls(Origin.SYSTEM, guild_id=guild_id, face=face)

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
                           self.tainted, face=self.face)

    def with_taint(self, tainted=True):
        """A copy with the taint flag set. MONOTONIC - it can only ever add.

        The old body assigned the argument straight through, so `with_taint(False)`
        on a tainted context handed back a clean one. agent.py's tool loop did
        exactly that on every call, from a local `tainted` that started at False
        whatever the caller had already established, so a turn that arrived tainted
        was laundered clean by the first tool call it made.

        The docstring already claimed this guarantee - "a nested call cannot quietly
        clear a taint its caller had set". It was a sentence, not a mechanism, and
        the test section quoting it only checked that the ORIGINAL object came back
        unchanged, which is a different property and one the broken version had too.
        The sentence is now true because the arithmetic makes it true: a cleared
        taint is unrepresentable, so no future edit can reintroduce the clearing.
        """
        return CallContext(self.origin, self.actor_id, self.guild_id,
                           self.channel_id, bool(tainted) or self.tainted,
                           face=self.face)

    def __repr__(self):
        return (f"CallContext(origin={self.origin!r}, actor={self.actor_id}, "
                f"guild={self.guild_id}, tainted={self.tainted}, "
                f"face={self.face!r})")


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


def rule_guest(action, ctx):
    """The guest lane's own gate: may THIS guest reach THIS capability?

    Guest-refactor Stage 2. Runs before rule_owner, and owns guest origins
    entirely - rule_owner steps aside for them (see its early return). Fail
    closed: the default answer for a guest is no, and every condition below must
    hold before this rule merely PASSES, at which point the remaining rules still
    run. In particular rule_origin_allowed still requires the capability to have
    named GUEST_DM in its origins - the second, independent denial, preserved
    from the pre-refactor design where either alone would have sufficed.

    Passing means returning None, never an early ALLOW: first-non-None-wins is
    the contract, and a rule that answered ALLOW from inside its own lane would
    silence every rule after it.

    The refusal reasons are deliberately distinct (guest_disabled /
    guest_allowlist / guest_capability / guest_config) because the fix for each
    is different, and the audit line naming the wrong one costs real time at
    exactly the moment someone is asking "why did this refuse".
    """
    if ctx.origin not in Origin.GUEST:
        return None
    if not identity.guest_enabled(ctx.face):
        return _deny("guest_disabled",
                     "guest access is switched off in control.json.")
    if not identity.is_guest(ctx.actor_id, ctx.face):
        return _deny("guest_allowlist",
                     f"user {ctx.actor_id} is not on the guest allowlist.")
    if not getattr(action, "guest", False):
        return _deny("guest_capability",
                     f"`{action.name}` is not a guest capability. Guests reach "
                     "only what is explicitly marked for them, and this is not.")
    if action.name not in identity.guest_capabilities(ctx.face):
        return _deny("guest_config",
                     f"`{action.name}` is guest-capable in code but not granted "
                     "in control.json (guest.capabilities). Adding it there and "
                     "restarting is the deliberate step that turns it on.")
    return None


def rule_owner(action, ctx):
    """A request carrying a human actor must carry Tyler.

    Stage 2. The check already existed at the entry points - on_message refuses a
    non-owner before anything else happens (as did handle_auto_reply, until voice
    was archived) - and those stay exactly where they are. This is not a
    replacement for them; it is the
    same rule stated once more at the point where a capability is actually about to
    run, so that a future entry point that forgets the early check still cannot get
    past here.

    Guest origins are rule_guest's lane, not this one's (guest-refactor Stage 2).
    The step-aside is safe precisely because rule_guest is fail-closed and runs
    FIRST - a guest origin cannot arrive here without rule_guest having already
    passed it, and RULES' ordering is asserted by the invariant suite.

    Only human origins are checked. LOCAL_CLI has no Discord actor to verify (its
    authority comes from already having the machine) and SYSTEM has no actor at all,
    so demanding an owner id from either would deny every automated call and every
    CLI invocation that did not bother to pass one. Both are constrained instead by
    rule_origin_allowed, which is the appropriate control for them.
    """
    if ctx.origin in Origin.GUEST:
        return None
    if ctx.origin not in Origin.HUMAN:
        return None
    if identity.is_owner(ctx.actor_id, ctx.face):
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
        Origin.GUEST_DM: "a guest DM",
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
    if ctx.guild_id is not None and int(ctx.guild_id) in identity.agent_guilds(ctx.face):
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


def rule_guest_never_confirms(action, ctx):
    """On the guest lane, anything that would CONFIRM is a flat DENY instead.

    Guest-refactor Stage 2, first in TARGET_RULES and ahead of both confirm
    rules, which is load-bearing twice over: CONFIRM means "park a preview and
    ask Tyler", and a guest must get neither half - not the preview (it leaks
    what would happen), and not the ability to generate approval traffic aimed
    at Tyler's phone.

    The registration invariant in capabilities.action() makes this rule
    unreachable in practice - a guest capability cannot be declared
    needs_confirm or outward at all. It exists anyway, for the same reason
    rule_owner re-states the entry-point check: a security check worth having is
    worth having twice, and this one specifically survives a future edit that
    relaxes the registration invariant without remembering why it was strict.
    """
    if ctx.origin not in Origin.GUEST:
        return None
    if action.needs_confirm:
        return _deny("guest_never_confirms",
                     f"`{action.name}` needs a confirmation, and guest requests "
                     "are never parked for one - this is a hard no, not a "
                     "waiting yes.")
    if action.outward:
        return _deny("guest_never_confirms",
                     f"`{action.name}` is outward, and a guest turn is always "
                     "tainted - what would be a confirmation for the owner is a "
                     "refusal here.")
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
    if identity.destructive_allowed(ctx.guild_id, ctx.face):
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
    if identity.posting_allowed(ctx.guild_id, ctx.channel_id, ctx.face):
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
# rule_guest sits before rule_owner because it OWNS guest origins: it is
# fail-closed for them, and rule_owner steps aside for them on that exact
# guarantee. Swapping the two would make rule_owner's step-aside an open door.
RULES = (
    rule_context_present,
    rule_guest,
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
    rule_guest_never_confirms,
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
    if ctx.origin in Origin.HUMAN and not identity.is_owner(ctx.actor_id, ctx.face):
        return _deny("engage_owner",
                     f"user {ctx.actor_id} is not my owner")
    if ctx.origin == Origin.OWNER_DM:
        return _ALLOW
    if ctx.origin == Origin.OWNER_GUILD:
        if ctx.guild_id is not None and int(ctx.guild_id) in identity.agent_guilds(ctx.face):
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
    if not identity.guest_enabled(ctx.face):
        return _deny("guest_disabled", "guest chat is switched off in control.json")
    if not identity.is_guest(ctx.actor_id, ctx.face):
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


# ==========================================================================
# The unprompted lane - may Claude start a conversation Tyler did not ask for?
# ==========================================================================
#
# Added 2026-08-20, for the mechanism that lets Claude INITIATE contact instead
# of only ever answering. A scheduled job wakes, reads real state (Corkboard
# boards, memory, the open loops in initiative.py), and decides whether there is
# ONE genuine question worth Tyler's attention. Almost always there is not.
#
# WHY THE RULES LIVE HERE AND NOT IN THE JOB'S PROMPT. The job is a model, and
# the failure mode of a model told "only speak when it matters" is that, run
# daily for a year, it eventually finds something. That is not hypothetical: a
# job which MUST produce a question will manufacture one, Tyler mutes it inside
# a week, and the channel is dead. Everything below is the half of the design
# that does not depend on the model being in a good mood.
#
# THE SHAPE OF THE GRANT. `deliver_unprompted` is the only outward action in
# this lane and it takes a conversation id and nothing else - the recipient and
# the words both come off a stored record, exactly as advance_conversation does.
# So these rules can be a single-argument walk over that record rather than the
# (action, ctx) walk above: what needs deciding is not where the call came from
# (that is already settled by the action's declared origins) but whether this
# particular question, at this particular moment, may be sent unbidden.
#
# WHAT IS DELIBERATELY ABSENT: any rule permitting an unprompted request for
# access or capability. See rule_unprompted_no_escalation - it is a design
# decision, not a gap, and it is written as a DENY so that a future session
# cannot helpfully add the feature without first deleting a rule and a test.

import re
from datetime import datetime, timedelta, timezone

# --- the numbers. This block IS the interruption budget; edit it here. ------

# No two unprompted questions closer together than this, whatever the job
# thinks. The scheduled task runs daily so it has a fresh chance to notice
# something real; this is what stops "a daily chance" becoming "a daily
# message". Tyler's brief: "once a day at most; every other day is fine."
UNPROMPTED_MIN_GAP = timedelta(hours=48)

# One unanswered question at a time, and it stops counting after this long.
# Waiting forever would be safe and useless - the lane would die the first time
# he was busy for a week. Lapsing is SILENT: nothing is sent when a question
# times out, because "you never answered me" is the exact guilt framing this
# lane is forbidden to carry.
UNPROMPTED_LAPSE_AFTER = timedelta(days=3)

# Two lapses in a row and the lane goes dormant until he engages with one, or
# until a human runs `initiate reset`. Ignoring two questions days apart is an
# answer; asking past it is what gets a channel muted.
UNPROMPTED_MAX_LAPSES = 2

# It ASKS, it does not report. Length and question-marks are a crude proxy for
# that, and they are here anyway, because the drift they catch is not subtle: a
# status digest is long and has no question in it, and every version of this
# idea slides toward a digest if nothing physically stops it.
UNPROMPTED_MAX_CHARS = 500
UNPROMPTED_MAX_QUESTION_MARKS = 2

# Requests for access or capability. NEVER from this lane - an ask like "can I
# use the webcam" belongs in live conversation, where Tyler can decline in one
# word and the matter is closed. Arriving unbidden on his phone, the same words
# are an open item he has to carry, which is a cost this lane has no right to
# impose. These patterns guard against Claude's own drift over months of runs,
# not against an adversary; a false positive costs one rephrase, and the run
# doing the rephrasing is a Claude that can.
ESCALATION_PATTERNS = (
    r"\b(can|could|may|might|would)\s+i\s+(please\s+)?"
    r"(use|access|open|read|look|see|watch|listen|view|peek|check|run|turn|"
    r"enable|start|borrow|grab|take|get)\b",
    r"\b(let|allow)\s+me\s+(to\s+)?\w+",
    r"\bpermission\s+to\b",
    r"\bgive\s+me\s+(access|permission|the\s+ability)\b",
    r"\b(mind|okay|ok)\s+if\s+i\b",
    r"\bwould\s+you\s+(mind|be\s+(ok|okay|alright)\s+with)\b",
    r"\bcould\s+you\s+(grant|allow|enable|approve|whitelist|give)\b",
    r"\bam\s+i\s+allowed\b",
)

# Guilt framing and wellness-check tone. Tyler's constraint 6, verbatim: "No
# guilt framing, no wellness-check tone, no 'just thinking of you.' A real
# question or nothing." What gets a channel muted is not rudeness, it is being
# made to feel managed - and both of those phrasings make the contact about the
# contact rather than about anything real.
GUILT_PATTERNS = (
    r"\bjust\s+(thinking|checking|wondering)\s+(of|about|in\s+on)\s+you\b",
    r"\bhow\s+are\s+you\s+(holding\s+up|doing\s+really|feeling)\b",
    r"\bhope\s+you('?re|\s+are)\s+(ok|okay|alright|well|doing)\b",
    r"\bchecking\s+in\s+on\s+you\b",
    r"\byou\s+(still\s+)?(haven'?t|never)\s+(answered|replied|got\s+back|told\s+me)\b",
    r"\bi'?ve\s+been\s+waiting\b",
    r"\bdid\s+you\s+(see|forget\s+about)\s+my\s+(last\s+)?(question|message)\b",
    r"\bno\s+pressure,?\s+but\b",
)


def _utcnow():
    return datetime.now(timezone.utc)


# --- the rules. Each takes (conv, now) and returns a Decision or None. ------
# Shape first, then content, then rate. That order is for the DRY RUN more than
# for the live path: a job that has written a bad question learns what is wrong
# with the question before it learns it is also too soon, and can fix both.

def rule_unprompted_direction(conv, now):
    """This path delivers unprompted questions and nothing else.

    Without it, `deliver_unprompted` would be a second - unnudged, unqueued -
    way to push any conversation in the store at anybody, which is precisely the
    "hand a timer the ability to message anyone anything" that the whole
    advance_conversation design exists to avoid.
    """
    from benham.core import conversations
    if conv is None:
        return _deny("unprompted_direction",
                     "there is no such conversation to deliver.")
    if conv.get("direction") != conversations.UNPROMPTED:
        return _deny("unprompted_direction",
                     f"{conv.get('id')} is a {conv.get('direction')!r} conversation. "
                     "This path only delivers questions Claude chose to ask on its "
                     "own; a normal ask goes through the queue and its nudges.")
    return None


def rule_unprompted_owner_only(conv, now):
    """Unbidden contact reaches Tyler, and reaches nobody else, ever.

    A collaborator did not sign up to be thought about by a timer. Reaching one
    stays a thing a human starts, through `outreach`, which has its own refusals
    (see test_outreach). Stated as its own rule rather than left implicit in how
    the CLI happens to fill the field.
    """
    if int(conv.get("counterparty", 0)) not in identity.OWNER_IDS:
        return _deny("unprompted_owner_only",
                     f"unprompted contact may only go to the owner, and "
                     f"{conv.get('counterparty')} is not one. Reaching a collaborator "
                     "unbidden is not something this lane does.")
    return None


def rule_unprompted_is_a_question(conv, now):
    """It ASKS, it does not report. Tyler's constraint 2.

    Arithmetic, not judgement, and knowingly crude - a determined digest can end
    in "?" and squeak under the cap. It is here because the drift it catches is
    the ordinary kind: months of runs, each a sentence longer than the last,
    until the thing arriving on his phone is a summary with a question stapled
    to the end. One question, in Benham's voice, under the cap.
    """
    text = (conv.get("question") or "").strip()
    if not text:
        return _deny("unprompted_is_a_question", "there is no question to send.")
    if len(text) > UNPROMPTED_MAX_CHARS:
        return _deny("unprompted_is_a_question",
                     f"that is {len(text)} characters and the cap is "
                     f"{UNPROMPTED_MAX_CHARS}. Past that it stops being a question and "
                     "starts being a report, which is not what this lane is for.")
    marks = text.count("?")
    if marks == 0:
        return _deny("unprompted_is_a_question",
                     "there is no question mark in it. This lane sends one genuine "
                     "question - if the thing worth saying is not a question, the "
                     "right output is silence.")
    if marks > UNPROMPTED_MAX_QUESTION_MARKS:
        return _deny("unprompted_is_a_question",
                     f"{marks} questions in one message is a survey, not a question. "
                     "Pick the one that is actually worth his attention.")
    return None


def rule_unprompted_no_escalation(conv, now):
    """NEVER ask for access or capability from here. A decision, not an oversight.

    Claude's own call, and the reasoning is kept attached to the rule so a later
    session reads it before deleting it: an ask for the webcam, the mic, OBS, his
    files - anything that widens what Claude can reach - belongs in a live
    conversation, where a "no" costs him one word and ends there. The same ask
    landing unbidden on his phone is an open item he has to carry. A channel
    Claude may open at will, which can also be used to request more reach, is a
    channel that grows itself. This one does not.
    """
    text = (conv.get("question") or "").lower()
    for pat in ESCALATION_PATTERNS:
        if re.search(pat, text):
            return _deny("unprompted_no_escalation",
                         "that reads as a request for access or capability, and this "
                         "lane never makes one - that ask belongs in live conversation, "
                         "where Tyler can say no in one word. If the match is a false "
                         "positive, rephrase it as a question about him or about the "
                         f"work. (matched: {pat})")
    return None


def rule_unprompted_no_guilt(conv, now):
    """No guilt framing, no wellness-check tone. Tyler's constraint 6."""
    text = (conv.get("question") or "").lower()
    for pat in GUILT_PATTERNS:
        if re.search(pat, text):
            return _deny("unprompted_no_guilt",
                         "that carries guilt framing or a wellness-check tone, which "
                         "this lane is not allowed to have. A real question about a "
                         f"real thing, or nothing at all. (matched: {pat})")
    return None


def rule_unprompted_lane_quiet(conv, now):
    """Two lapses in a row and the lane stops until he engages.

    Silence from Tyler is information. One unanswered question is a busy week;
    two in a row, days apart, is the channel not landing - and the right response
    to that is to stop, not to try harder. Recoverable: answering any unprompted
    question clears it, and `initiate reset` clears it by hand. The log says so
    loudly when this fires, so it is never a silent death.
    """
    from benham.core import initiative
    lapses = initiative.consecutive_lapses()
    if lapses >= UNPROMPTED_MAX_LAPSES:
        return _deny("unprompted_lane_quiet",
                     f"{lapses} unprompted questions in a row went unanswered, so this "
                     "lane is dormant. That is the designed response to not landing, "
                     "not a fault. It clears when Tyler answers one, or when a human "
                     "runs `python benham.py initiate reset`.")
    return None


def rule_unprompted_one_at_a_time(conv, now):
    """One unanswered unprompted question in the world at a time. Never stack.

    PARAMETER-FREE BY CONSTRUCTION, and that is what makes it safe: the check is
    "is there any DELIVERED unprompted question still waiting", and the one being
    considered right now has by definition not been delivered yet. There is no id
    to compare against, so there is no comparison to get wrong.
    """
    from benham.core import initiative
    outstanding = initiative.outstanding(now=now)
    if outstanding:
        first = outstanding[0]
        return _deny("unprompted_one_at_a_time",
                     f"{first['id']} is still unanswered (asked "
                     f"{str(first.get('delivered_at'))[:16]}Z). One unanswered question "
                     "at a time - stacking a second on top of it is how this stops "
                     "being a conversation and starts being a feed.")
    return None


def rule_unprompted_cadence(conv, now):
    """A hard floor between deliveries, independent of what the job believes.

    The scheduled job runs daily on purpose - a daily read is a daily chance to
    notice something genuinely worth asking. This is what keeps a daily CHANCE
    from becoming a daily MESSAGE in the worst case, where every run in a row
    talks itself into having found something.
    """
    from benham.core import initiative
    last = initiative.last_delivered_at()
    if last is None:
        return None
    now = now or _utcnow()
    waited = now - last
    if waited < UNPROMPTED_MIN_GAP:
        left = UNPROMPTED_MIN_GAP - waited
        return _deny("unprompted_cadence",
                     f"the last unprompted question went out "
                     f"{int(waited.total_seconds() // 3600)}h ago and the floor is "
                     f"{int(UNPROMPTED_MIN_GAP.total_seconds() // 3600)}h. Wait another "
                     f"{int(left.total_seconds() // 3600) + 1}h. Whatever this is, it keeps.")
    return None


# Order is load-bearing; see the note above rule_unprompted_direction.
UNPROMPTED_RULES = (
    rule_unprompted_direction,
    rule_unprompted_owner_only,
    rule_unprompted_is_a_question,
    rule_unprompted_no_escalation,
    rule_unprompted_no_guilt,
    rule_unprompted_lane_quiet,
    rule_unprompted_one_at_a_time,
    rule_unprompted_cadence,
)


def authorize_unprompted(conv, now=None):
    """May this unprompted question be sent, right now? The lane's chokepoint.

    `deliver_unprompted` is the only thing that puts an unprompted question in
    front of Tyler, and it calls this first and refuses on anything but ALLOW.
    The CLI calls it too, before opening a conversation at all, so a bad question
    is caught while it is still cheap and the job is told why - but the CLI's
    call is a courtesy and this one is the gate. If they ever disagree the gate
    wins, because it runs at the moment the message would actually reach him.

    `now` is injectable for the same reason it is everywhere in conversations.py:
    a cadence rule tested by waiting 48 real hours is a rule nobody runs.
    """
    now = now or _utcnow()
    for rule in UNPROMPTED_RULES:
        verdict = rule(conv, now)
        if verdict is not None:
            return verdict
    return _ALLOW
