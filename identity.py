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

import os

import jsonio

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_FILE = os.path.join(BASE_DIR, "control.json")

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
}


def load_control():
    """Read control.json, filling in restrictive defaults for anything absent."""
    cfg = jsonio.read_json(CONTROL_FILE, default={})
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


def agent_allowed(guild_id, user_id, is_dm):
    """Whether the autonomous agent may engage with this message.

    An owner DM always qualifies: that channel is private to the two of us and is
    the primary way Tyler reaches Claude when away from the PC. In a guild, both
    conditions must hold - the guild is on the agent list AND the speaker is an
    owner. Benham can read every channel it can see, but it only *answers to* one
    person, so a mention from anyone else is recorded and ignored.
    """
    if not is_owner(user_id):
        return False
    if is_dm:
        return True
    try:
        return int(guild_id) in AGENT_GUILDS
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
