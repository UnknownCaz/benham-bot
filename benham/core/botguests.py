"""
botguests.py - the reply budget for a bot guest (INTENT decision 53).

2026-10-17, Caz: Hermes (HERMES_AGENT, another bot) talks to Benham in one
channel, #kestra in RadioTower. Two bots that each answer a mention can answer
each other forever, so Benham's side counts: at most CAP replies to one account
in one channel per rolling WINDOW, then silence (logged) until the oldest reply
ages out. Hermes runs its own guard (20 per 5 minutes, then a 10-minute
cooldown); with both, a runaway cannot happen.

Who MAY be answered is policy.may_answer_bot_guest - authority. How many times
is this file - state - for the reason may_chat_as_guest gives: a decision that
changes with a counter does not belong in the file that says what the rules are.

In memory, like invites.py. A restart resets the count, which is the one way
this store fails open; it is bounded by restarts being rare and by Hermes's own
guard, and it keeps the suite from writing state anywhere.

Pure bookkeeping: nothing here talks to Discord or the model.
"""

import time

CAP = 10            # replies per WINDOW, per (channel, account)
WINDOW = 3600       # seconds - a rolling hour

_sent = {}          # (channel_id, user_id) -> [monotonic-ish timestamps]


def _live(key, now):
    stamps = [t for t in _sent.get(key, ()) if now - t < WINDOW]
    _sent[key] = stamps
    return stamps


def take(channel_id, user_id, now=None):
    """Spend one reply if the budget has one. Returns (allowed, used_this_window).

    Spent BEFORE the reply is made, so a reply that then fails still counts -
    the error direction is silence, never an extra reply."""
    now = time.time() if now is None else now
    key = (int(channel_id), int(user_id))
    stamps = _live(key, now)
    if len(stamps) >= CAP:
        return False, len(stamps)
    stamps.append(now)
    return True, len(stamps)


def lead_with_mention(text, user_id):
    """The reply must OPEN with the inline mention: Hermes's own gate only
    answers a bot that mentions it inline. Added once, never doubled."""
    lead = f"<@{int(user_id)}>"
    body = (text or "").lstrip()
    if body.startswith(lead) or body.startswith(f"<@!{int(user_id)}>"):
        return body
    return f"{lead} {body}".rstrip()


def reset():
    """Tests only."""
    _sent.clear()
