"""
confirm.py - the pending-confirmation store for destructive actions.

Every tier-3 action is a two-step: a first call that runs the dry-run and parks a
pending confirmation here, and a second explicit step that fires it. There is no
inline shortcut, by design - "I already asked for it" is exactly the situation
where a wrong channel id sails through, because the request and the approval carry
the same mistake.

Three properties do the real work:

  Bound to an id. A confirmation fires only when the reply names its token, so a
  stray "yeah" three messages later in an unrelated thread cannot trigger anything.

  One at a time. Parking a new confirmation supersedes the old one rather than
  queueing it. Two live confirmations means an ambiguous "yes", and an ambiguous
  yes on a delete is the failure this module exists to prevent.

  Expiry is cancellation. A confirmation past its TTL is dead and its action never
  runs. Nothing here ever treats silence, a timeout, or a missing record as assent.

State is deliberately in-memory: a restart drops every pending confirmation, which
is the safe direction to fail. A confirmation that survived a crash would be an
approval granted to a process that no longer remembers why it asked.
"""

import time
import uuid

from benham.core import identity

# Narrow affirmative set. Matched against the whole message, lowercased and
# stripped of punctuation - NOT a substring search. "yes" as a substring would fire
# on "yesterday", and more to the point a loose match turns any agreeable-sounding
# sentence into authorization to delete something.
_AFFIRMATIVE = {
    "y", "yes", "yep", "yeah", "yup", "ok", "okay", "do it", "go", "go ahead",
    "confirm", "confirmed", "yes do it", "send it", "approved", "affirmative",
}

_NEGATIVE = {
    "n", "no", "nope", "cancel", "stop", "abort", "nevermind", "never mind",
    "dont", "don't", "no dont", "no don't",
}


class Pending:
    """One parked action awaiting an explicit yes."""

    def __init__(self, token, action, params, preview, requested_by, origin, ttl,
                 call_ctx=None):
        self.token = token
        self.action = action
        self.params = params
        self.preview = preview          # the dry-run result: real counts, names, ids
        self.requested_by = requested_by
        self.origin = origin            # "dm" | "channel" | "outbox" | "self"
        # The policy context the action was parked under, replayed verbatim when it
        # fires. Without this a confirmation would launder an action into whatever
        # origin happened to be firing it - a request that policy refused from a
        # guild mention could be parked, then confirmed, and run as if it had come
        # from somewhere it was allowed.
        self.call_ctx = call_ctx
        self.expires_at = time.monotonic() + ttl

    @property
    def expired(self):
        return time.monotonic() >= self.expires_at

    @property
    def seconds_left(self):
        return max(0, int(self.expires_at - time.monotonic()))


_pending = {}   # token -> Pending  (at most one live entry; see supersede below)


def _ttl_for(origin):
    cfg = identity.CONTROL.get("confirm", {}) or {}
    if origin == "dm":
        return int(cfg.get("conversation_ttl_seconds", 600))
    return int(cfg.get("ttl_seconds", 3600))


def park(action, params, preview, requested_by, origin, call_ctx=None):
    """Store a dry-run result awaiting confirmation. Returns the Pending.

    Supersedes any existing pending action - see the module docstring on why this
    is a replace and not a queue.
    """
    _pending.clear()
    token = uuid.uuid4().hex[:6]
    p = Pending(token, action, params, preview, requested_by, origin,
                _ttl_for(origin), call_ctx=call_ctx)
    _pending[token] = p
    return p


def get(token):
    """Fetch a live pending action by token, or None if unknown or expired.

    Expired entries are dropped on read rather than by a sweeper task: the only
    moment staleness matters is when someone tries to use one.
    """
    p = _pending.get(str(token or "").strip().lower())
    if p is None:
        return None
    if p.expired:
        _pending.pop(p.token, None)
        return None
    return p


def current():
    """The single live pending action, or None."""
    for token in list(_pending):
        p = get(token)
        if p is not None:
            return p
    return None


def consume(token):
    """Take a pending action, removing it so it can never fire twice."""
    p = get(token)
    if p is not None:
        _pending.pop(p.token, None)
    return p


def cancel(token=None):
    """Drop a pending action (a specific one, or all). Returns what was dropped."""
    if token is None:
        dropped = list(_pending.values())
        _pending.clear()
        return dropped
    p = _pending.pop(str(token or "").strip().lower(), None)
    return [p] if p else []


def _normalize(text):
    """Lowercase, strip surrounding punctuation and whitespace."""
    return (text or "").strip().strip(".!?,;:").strip().lower()


def read_reply(text):
    """Classify a reply to a confirmation prompt.

    Returns (verdict, token) where verdict is "yes" | "no" | None. A token is
    extracted when the message names one, so an explicit "yes a1b2c3" targets that
    action even if something else was parked in between.

    Anything that is not clearly affirmative or negative returns None - and a None
    must be treated as "no action", never as assent. Ambiguity is not consent.
    """
    raw = _normalize(text)
    if not raw:
        return None, None

    token = None
    words = raw.replace(",", " ").split()
    for w in words:
        if len(w) == 6 and all(c in "0123456789abcdef" for c in w) and get(w):
            token = w
            break

    # Compare against the phrase with any token removed, so "yes a1b2c3" still
    # reads as a plain affirmative.
    phrase = " ".join(w for w in words if w != token).strip()

    if phrase in _AFFIRMATIVE:
        return "yes", token
    if phrase in _NEGATIVE:
        return "no", token
    return None, token


def describe(p):
    """Render a pending action as the confirmation prompt Tyler sees."""
    lines = [
        f"**Confirm required** - `{p.action}`",
        "",
        p.preview.get("summary", "(no preview available)"),
    ]
    detail = p.preview.get("detail")
    if detail:
        lines += ["", detail]
    # Why it is being asked, in policy's own words. Confirmations now arise for two
    # different reasons - the action is destructive, or the turn is tainted - and
    # they call for different judgement from Tyler. A prompt that does not say
    # which is asking him to approve something without telling him what kind of
    # decision he is making.
    reason = p.preview.get("reason")
    if reason:
        lines += ["", f"_{reason}_"]
    lines += [
        "",
        f"Tap a button, or reply **yes** to run it / **no** to cancel. "
        f"Token `{p.token}`, expires in {p.seconds_left // 60}m.",
    ]
    return "\n".join(lines)
