"""
invites.py - when Benham may answer someone other than Tyler in a server channel.

Caz, 2026-10-05, after friends tried to join a conversation he was having with
Benham in Testing #asd and got nothing back: "I was talking to Benham and my
friends wanted to jump in. I @ him in the same channel saying he should
respond. OR he dms me to ask if he should wiht the prompt so they dont see the
behind the scenes confirmation. Then after those steps he can @ the people and
resond to them for the alloted amount of turns default is one reply with one
closing message in response." INTENT decision 50.

Two records per channel:

  an ASK     - a friend @mentioned Benham with no invite open, and Tyler has
               been DMed with buttons. One per channel: more friends @ing while
               it waits fold into it rather than buzzing his phone again.
  an INVITE  - Tyler said yes. Names who may be answered and how many replies
               are left: REPLIES after a tap (the reply and the closing
               message), 1 after Benham pinged them in a reply to Tyler's own
               mention (the reply already happened; the closing one is left).

Both live in memory, not on disk, because both die with what they hang on: an
ASK is only answerable through buttons that do not survive a restart, and an
INVITE that outlived one would let a friend reach the model on a permission
nobody can see any more. So it fails closed - after a restart friends are
asked about again, never answered unasked.

Pure bookkeeping: nothing here talks to Discord or the model, so the tests
drive it with ids and a clock.
"""

import time

REPLIES = 2                  # Caz's default: one reply, then one closing message
ASK_TTL = 3600               # the buttons live an hour, like a confirmation
INVITE_TTL = 1800            # an exchange nobody continues for 30 min is over
QUIET_AFTER_IGNORE = 1800    # "Ignore" means stop asking for a while, not "ask again in a minute"

_asks = {}       # channel_id -> {"people": {id: name}, "messages": [ids], "at": t}
_invites = {}    # channel_id -> {"people": {id: name}, "left": n, "until": t, "by": str}
_quiet = {}      # channel_id -> until

_clock = time.time


def invite_for(channel_id, user_id):
    """The open invite that covers this person in this channel, or None."""
    inv = _invites.get(channel_id)
    if inv is None:
        return None
    if inv["left"] <= 0 or _clock() > inv["until"]:
        _invites.pop(channel_id, None)
        return None
    return inv if user_id in inv["people"] else None


def open_invite(channel_id, people, replies, by):
    """Let Benham answer `people` ({id: name}) here, `replies` more times.

    Opening on a channel that already has one merges the people and keeps the
    larger budget - a second yes never shortens the first."""
    old = _invites.get(channel_id)
    merged = dict(old["people"]) if old else {}
    merged.update(people)
    left = max(int(replies), old["left"] if old else 0)
    _invites[channel_id] = {"people": merged, "left": left,
                            "until": _clock() + INVITE_TTL, "by": by}
    return _invites[channel_id]


def use(channel_id):
    """Spend one reply. Returns how many are left; the invite closes at zero."""
    inv = _invites.get(channel_id)
    if inv is None:
        return 0
    inv["left"] -= 1
    inv["until"] = _clock() + INVITE_TTL
    if inv["left"] <= 0:
        _invites.pop(channel_id, None)
        return 0
    return inv["left"]


def close(channel_id):
    _invites.pop(channel_id, None)


def pending_ask(channel_id):
    ask = _asks.get(channel_id)
    if ask is not None and _clock() - ask["at"] > ASK_TTL:
        _asks.pop(channel_id, None)
        return None
    return ask


def add_ask(channel_id, user_id, name, message_id):
    """Record a friend's @. Returns (ask, is_new): only a NEW ask DMs Tyler."""
    ask = pending_ask(channel_id)
    if ask is not None:
        ask["people"][user_id] = name
        ask["messages"].append(message_id)
        return ask, False
    ask = {"people": {user_id: name}, "messages": [message_id], "at": _clock()}
    _asks[channel_id] = ask
    return ask, True


def drop_ask(channel_id, ignored=False):
    """Resolve the waiting ask. `ignored` starts the quiet period."""
    if ignored:
        _quiet[channel_id] = _clock() + QUIET_AFTER_IGNORE
    return _asks.pop(channel_id, None)


def quiet(channel_id):
    until = _quiet.get(channel_id)
    if until is None:
        return False
    if _clock() > until:
        _quiet.pop(channel_id, None)
        return False
    return True


def reset():
    """Tests only."""
    _asks.clear()
    _invites.clear()
    _quiet.clear()
