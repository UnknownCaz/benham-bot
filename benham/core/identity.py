"""
identity.py - who Benham answers to, and how dangerous each thing it can do is.

Benham is Claude's face in Discord: it acts on Tyler's word and nobody else's.
That rule has to live in exactly one place, because the bot is reachable from
several directions at once - a DM, a channel mention, a slash command, a file
dropped in outbox/ - and a gate that only covers three of the four is not a gate.
Every entry point calls is_owner() here.

The tier system is the other half. Discord actions are not uniformly risky, and
treating them as if they were produces a bot that either nags about pinning a
message or quietly deletes a channel. Four tiers, by what happens when the action
was a mistake:

  READ (0)        Nothing happened. No gate.
  SPEAK (1)       A human saw it. Deletable, but the notification already fired -
                  socially irreversible. This is the tier most likely to actually
                  go wrong (wrong channel id), which is why draft-first exists.
  MANAGE (2)      State changed and has an exact inverse. Unpin what you pinned.
  DESTRUCTIVE (3) No undo. Deleted messages are gone; a deleted role cannot tell
                  you who used to have it. Gated three ways: guild allowlist,
                  mandatory dry-run, and an explicit second step to fire.
"""

import json
import os

from benham import paths
CONTROL_FILE = os.path.join(paths.CONFIG_DIR, "control.json")

# Tiers, ordered. Compared with >= so a threshold check reads naturally.
READ = 0
SPEAK = 1
MANAGE = 2
DESTRUCTIVE = 3

TIER_NAMES = {READ: "read", SPEAK: "speak", MANAGE: "manage", DESTRUCTIVE: "destructive"}

# Fallbacks used when control.json is missing entirely. Deliberately the most
# restrictive reading: Tyler is the only owner, and nothing destructive is allowed
# anywhere. A missing config should cost capability, never safety.
_DEFAULTS = {
    "owner_ids": [273967061619965952],  # caz6666
    "destructive_guilds": [],
    "agent_guilds": [],
    "agent": {},
    "confirm": {},
    "presence": {},
    "guest": {},  # absent => disabled, nobody whitelisted. See guest_enabled().
}


class ControlFileError(Exception):
    """control.json exists but could not be parsed. Never raised for an absent file."""


def load_control():
    """Read control.json, filling in restrictive defaults for anything absent.

    ABSENT and MALFORMED are opposite cases and get opposite treatment, which is
    the whole reason this does not just call jsonio.read_json - that helper catches
    (OSError, ValueError) together, so a syntax error read as an empty file.

    Absent is a legitimate state: a fresh clone, or Tyler moving the file aside on
    purpose. It falls back to _DEFAULTS above.

    Malformed is a typo he just made in an editor, and treating it as absent is not
    the safe reading it looks like. The defaults fail CLOSED on two axes and OPEN on
    two others:

        destructive_guilds  -> []      no tier 3 anywhere.        Closed. Fine.
        agent_guilds        -> []      no mentions engage.        Closed. Fine.
        post_guilds         -> absent  posting_allowed() returns True for EVERY
                                       guild. The scope cap that docstring calls
                                       "arithmetic" rather than a judgement call -
                                       the one defence against Benham being invited
                                       somewhere and reading planted text - is gone.
        agent.enabled       -> absent  agent.py reads .get("enabled", True), so the
                                       autonomous agent switches ON, and bills.

    So a misplaced comma silently widens two gates and starts spending money, while
    looking like a bot that came up fine. Refusing to boot is the correct response
    to a control plane nobody can parse: the failure is loud, immediate, and happens
    before a single Discord connection, which is the cheapest possible moment.
    """
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    except ValueError as e:  # json.JSONDecodeError subclasses this
        raise ControlFileError(
            f"{CONTROL_FILE} is not valid JSON: {e}\n"
            "Benham will not start on a guess. Treating an unparseable control "
            "plane as an absent one removes the posting scope cap entirely and "
            "turns the agent on - it is not the safe reading it looks like.\n"
            "Fix the syntax, or move the file aside to run on restrictive defaults "
            "deliberately."
        ) from e
    out = dict(_DEFAULTS)
    for k, v in cfg.items():
        if k.startswith("_"):  # "_comment"-style annotation keys in the example file
            continue
        out[k] = v
    return out


CONTROL = load_control()

OWNER_IDS = set(int(u) for u in CONTROL.get("owner_ids", []) or [])
DESTRUCTIVE_GUILDS = set(int(g) for g in CONTROL.get("destructive_guilds", []) or [])
AGENT_GUILDS = set(int(g) for g in CONTROL.get("agent_guilds", []) or [])


def is_owner(user_id):
    """True only for a user Benham takes direction from.

    Note what this deliberately does NOT do: there is no guild-admin escape hatch,
    no "operator" role, no way for a server owner to inherit control. Benham is one
    person's proxy. Someone with admin in a guild Benham happens to be in is still
    a stranger to it.
    """
    try:
        return int(user_id) in OWNER_IDS
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Guests. A second, much smaller category of person: someone Benham will hold a
# conversation with, and nothing else. They are not a weaker owner - there is no
# tier of capability they sit at. The distinction this file draws is between the
# one person who directs Benham and everyone who does not, and a guest is firmly
# on the second side of it. All a guest id buys is a reply instead of a refusal.
# --------------------------------------------------------------------------

def people_map(value):
    """A config list of people as {name: id}, from either shape it may be written in.

    The readable shape is a name->id map, and it is what every people-list here
    should be written as. A bare array of 19-digit Discord ids is precisely where a
    wrong id hides in plain sight: the boot banner prints it, it looks exactly like
    the four beside it, and nothing in the system can tell you it is the wrong
    person until someone notices Benham talking to a stranger. A name cannot hide
    that way. `outreach.people` has been the readable shape all along - this makes
    the others match it rather than the reverse.

    The bare array still parses, and that is not politeness. control.json is a live
    control plane on a running bot; a format change that refuses the file already on
    disk would take the bot down on the next restart, which is a worse failure than
    the unreadability it fixes. An id with no name gets its own id as its name -
    exactly what outreach._allowed() was already faking locally.
    """
    if isinstance(value, dict):
        return {str(k).strip(): int(v) for k, v in value.items()}
    return {str(i): int(i) for i in (value or [])}


GUEST = CONTROL.get("guest") or {}
GUEST_PEOPLE = people_map(GUEST.get("ids"))
GUEST_IDS = set(GUEST_PEOPLE.values())

# The GitHub intake funnel (core/issues.py). A separate block rather than a key
# under `guest` because the owner files through it too - being an issuer is a
# per-person grant layered ON TOP of being a guest, not a property of guests.
ISSUES = CONTROL.get("issues") or {}


def issues_config():
    """The raw issues block, for the knobs core/issues.py owns (repo, issuers,
    caps). Same import-once semantics as everything else here: editing it takes
    effect on the next restart, and the restart is the kill switch."""
    return dict(ISSUES)

# Modes this build knows how to run. An unrecognised mode disables guest chat
# rather than falling back to one, because the failure being guarded against is a
# config written for a newer build than the code - where guessing which mode was
# meant is exactly the wrong instinct.
#
# "workspace" was the second mode: guest turns ran a tool loop over whatever
# capabilities.guest_grants() allowed. Archived 2026-08-16 (archive/guest-tools/)
# and deliberately NOT left in this set, so a control.json still saying
# mode: "workspace" now switches guests OFF instead of quietly running them
# through a chat path that cannot do what that config asked for.
GUEST_MODES = frozenset({"chat"})


def guest_enabled():
    """Whether the guest surface is switched on and set to a mode this build knows.

    Both halves matter. `enabled` is the switch Tyler flips; the mode check is what
    stops a control.json written for a later build from silently running guests
    through the only path this one happens to have.

    NOT a live kill switch. CONTROL is read once at import, so `enabled: false` - or
    striking someone from `ids` - takes effect on the next restart and not before.
    Cutting a guest off right now means bouncing the bot. That is how every other
    setting in this file behaves (owner_ids and destructive_guilds included), and
    making this one reload on its own would put a second, different answer to "who
    may talk to Benham" in the codebase, which is worse than the delay.
    """
    if not bool(GUEST.get("enabled", False)):
        return False
    return str(GUEST.get("mode", "chat")) in GUEST_MODES


def is_guest(user_id):
    """True for a user on the guest allowlist, and never for an owner.

    The owner exclusion is not tidiness. Tyler reaching the guest path would silently
    downgrade him into a no-tools conversation with a separate memory, and the
    symptom - Benham suddenly unable to do anything and not remembering the thread -
    reads like a broken bot rather than a misfiled id. Refusing the overlap here
    means it cannot happen however the allowlist is edited.
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    if uid in OWNER_IDS:
        return False
    return uid in GUEST_IDS


def guest_config():
    """The raw guest block, for the runtime knobs guest.py owns (caps, model)."""
    return dict(GUEST)






def guest_capabilities():
    """Capability names control.json grants to guests. Config half of the grant.

    Empty set unless guest.capabilities is present and non-empty, which is the
    fail-closed direction: an old control.json means no grants, not default
    grants. Listing a name here does nothing unless the capability ALSO declares
    guest=True in the registry - a typo in this list can only turn things off.
    Same import-once semantics as everything else in this file: editing the list
    takes effect on the next restart, and the restart is the kill switch.
    """
    return frozenset(str(n) for n in (GUEST.get("capabilities") or []))


def destructive_allowed(guild_id):
    """Whether tier-3 actions may run in this guild at all.

    Checked before the dry-run, not after, so a destructive request aimed at a
    non-allowlisted guild never even reports what it would have done - naming the
    contents of a channel it may not touch is itself a small leak.

    A DM has no guild, so guild_id is None there and this returns False: you cannot
    purge your way through a DM with Benham, which is correct - the confirmation
    conversation itself lives there.
    """
    if guild_id is None:
        return False
    try:
        return int(guild_id) in DESTRUCTIVE_GUILDS
    except (TypeError, ValueError):
        return False


def posting_allowed(guild_id, channel_id):
    """Whether Benham may post content into this channel at all.

    A hard scope cap, independent of who asked and of why. The case it exists for:
    Benham gets invited to a server, someone there posts text engineered to look
    like an instruction, and a later read pulls it into context. Every other defence
    against that is a judgement call somewhere; this one is arithmetic.

    `post_channels` wins when set (the tight configuration). Otherwise the channel's
    guild must be on `post_guilds`. An empty/absent config allows everything, which
    keeps existing behaviour for anyone who never sets it - the cap is opt-in, and a
    silently-restrictive default would look like the bot being broken.

    A channel with no guild is a DM. Those are allowed here and governed instead by
    the taint rule, which is the relevant control for "Benham messaged a stranger".
    """
    cfg_channels = set(int(c) for c in (CONTROL.get("post_channels") or []))
    if cfg_channels:
        try:
            return int(channel_id) in cfg_channels
        except (TypeError, ValueError):
            return False

    cfg_guilds = CONTROL.get("post_guilds")
    if not cfg_guilds:
        return True
    if guild_id is None:
        return True  # DM; see docstring
    try:
        return int(guild_id) in set(int(g) for g in cfg_guilds)
    except (TypeError, ValueError):
        return False


def refusal(user_id, what="that"):
    """The line Benham gives a non-owner who tries to direct it.

    Plain, not preachy, and honest about the reason. It names no owner - telling a
    stranger whose bot this is invites them to go work on that person instead.
    """
    return (
        f"I can't do {what} for you - I only take direction from my owner. "
        "Happy to talk, though."
    )
