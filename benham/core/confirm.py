"""
confirm.py - the pending-confirmation store for destructive actions.

Every tier-3 action is a two-step: a first call that runs the dry-run and parks a
pending confirmation here, and a second explicit step that fires it. There is no
inline shortcut, by design - "I already asked for it" is exactly the situation
where a wrong channel id sails through, because the request and the approval carry
the same mistake.

Three properties do the real work:

  Bound to an id. On tier 3 the token is REQUIRED (2026-08-24, below); on tiers
  0-2 it is optional and a bare "yes" fires whatever is currently parked - see
  read_reply() and bot.py's `target = confirm.get(token) if token else pending`.

  CORRECTED 2026-08-17: this paragraph used to claim a confirmation fires ONLY
  when the reply names its token. It does not, and never did. The claim was
  copied out of here into two manual pages, where it was published as a safety
  property of the delete/purge/kick/ban path. What actually keeps a stray "yeah"
  from firing something is the pair of properties below - one live confirmation
  at a time, and a short TTL - plus read_reply()'s whitelist, which matches only
  a bare affirmative phrase and returns None for anything it does not recognise.
  If the token SHOULD be mandatory for tier 3, that is a behaviour change and
  belongs in a commit that says so, not in a comment that assumes it.

  THIS IS THAT COMMIT (2026-08-24, Tyler: "one copy-paste action is worth an
  irreversible action"). On tier 3 the token is now mandatory on the typed-reply
  path, behind `confirm.require_token_tier3` in control.json (default on, so the
  revert is a config edit rather than a revert commit).

  Why the token and not the naming rule it replaces: the 2026-08-17 rule's
  guarantee is word overlap with ordinary English, and the words it accepts are
  channel names, usernames and role names - which ARE ordinary English. Park a
  purge on #general and "yeah, general seems right" is an affirmative sharing a
  word: it fired. That set also grows by itself, because every new capability
  adds preview keys and resolved names to it, and nothing announces when it has
  degraded. A token is exact and does not rot.

  The friction objection does not survive the code: the Approve/Deny buttons
  close over the token directly and never reach read_reply, so the ordinary path
  is still one tap. This binds only the TYPED reply - which is the moment you are
  answering from a notification rather than looking at the preview.

  Uniform across all seven tier-3 actions rather than a delete-only carve-out.
  "No undo" does not pick the delete/purge/kick/ban trio anyway: a kick is an
  invite away and `unban_member` exists, so ban and kick are the two that DO have
  a repair path. A two-level rule would buy back only friction the button already
  removed, at the cost of a curated set that has to be kept right forever.

  One at a time. Parking a new confirmation supersedes the old one rather than
  queueing it. Two live confirmations means an ambiguous "yes", and an ambiguous
  yes on a delete is the failure this module exists to prevent.

  Expiry is cancellation. A confirmation past its TTL is dead and its action never
  runs. Nothing here ever treats silence, a timeout, or a missing record as assent.

State is deliberately in-memory: a restart drops every pending confirmation, which
is the safe direction to fail. A confirmation that survived a crash would be an
approval granted to a process that no longer remembers why it asked.
"""

import re
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


# Words too common to prove a reply means THIS action rather than the sentence
# around it. "yes, do it" names nothing; "yes, purge that channel" does.
_WEAK_WORDS = {
    "the", "that", "this", "it", "its", "do", "go", "and", "for", "you", "your",
    "yes", "yeah", "yep", "ok", "okay", "please", "now", "all", "one", "with",
    "from", "was", "are", "was", "totally", "right", "sure", "sounds", "good",
}


def _action_words(pending):
    """Distinctive words that count as naming this action.

    Drawn from the action name and from whatever the caller actually passed, so
    "yes, purge that channel" and "yes, ban steve" both land without anyone
    maintaining a phrase list per capability.
    """
    words = set(re.split(r"[^a-z0-9]+", pending.action.lower()))
    for value in (pending.params or {}).values():
        if isinstance(value, str):
            words.update(re.split(r"[^a-z0-9]+", value.lower()))
    # The preview holds the resolved names - the channel, the members, the role -
    # which is usually what a human types rather than the parameter name.
    preview = pending.preview
    if isinstance(preview, dict):
        for key in ("channel", "name", "target", "user", "member", "role", "guild"):
            v = preview.get(key)
            if isinstance(v, str):
                words.update(re.split(r"[^a-z0-9]+", v.lower()))
    return {w for w in words if len(w) >= 3} - _WEAK_WORDS


def needs_reference(pending):
    """True when a bare affirmative must not be enough.

    Tier 3 only - no undo. Tyler's rule, 2026-08-17: "a bare yes should not work
    it should work with a yes, xyz to confirm thats what the sender is talking
    about, so 'yes, purge that channel.' would work but, 'yes, your totally
    right' wouldnt."

    Imported lazily: capabilities pulls in policy and the whole registry, and
    this module is deliberately cheap enough to import from anywhere.
    """
    if pending is None:
        return False
    try:
        from benham.core import capabilities
    except ImportError:
        return False
    act = capabilities.REGISTRY.get(pending.action)
    return bool(act and act.tier == 3)


def require_token():
    """Is the 6-hex token mandatory on tier 3? Default yes.

    Read live rather than captured at import, so flipping it in control.json
    takes effect on the next restart without a code change - which is the whole
    point of it being config. Global rather than per-face on purpose: `_pending`
    is one module-level slot shared by every face, so a per-face answer to "does
    this confirmation need its token" would be answering about the wrong object.
    """
    return config_problem() is not None or _configured_require_token() is not False


_MISSING = object()   # "key absent" - distinct from a key present holding null


def _configured_require_token():
    """The raw configured value, or _MISSING when the key is absent.

    A sentinel rather than None because a JSON `null` is PRESENT and malformed,
    while an absent key is simply the default - and collapsing the two would
    report no problem for the one value most likely to be a mistake.
    """
    cfg = identity.CONTROL.get("confirm", {}) or {}
    if "require_token_tier3" not in cfg:
        return _MISSING
    return cfg["require_token_tier3"]


def config_problem():
    """A one-line complaint about a malformed flag, or None when it is sane.

    Exists because `bool(value)` was the obvious way to read this and it FAILS
    OPEN: null, 0, "" and [] all coerce to False and silently disable the gate,
    while the sloppy-looking string "false" coerces to True and happens to fail
    safe. A fail-open reading sitting inside the one layer whose whole doctrine
    is fail-closed is the bug, not the typo that would trip it - so only a real
    JSON boolean turns this off, and anything else is a config error that keeps
    the gate ON and says so at boot.
    """
    value = _configured_require_token()
    if value is _MISSING or value is True or value is False:
        return None
    return (f"confirm.require_token_tier3 is {value!r}, which is not true/false - "
            f"the tier-3 token stays REQUIRED. Fix it or delete the key.")


def needs_token(pending):
    """True when only a token can fire this one - tier 3, with the flag on."""
    return needs_reference(pending) and require_token()


def read_reply(text, pending=None):
    """Classify a reply to a confirmation prompt.

    Returns (verdict, token) where verdict is "yes" | "no" | "needs_token" |
    "needs_reference" | None. A token is extracted when the message names one, so
    an explicit "yes a1b2c3" targets that action even if something else was parked
    in between.

    Anything that is not clearly affirmative or negative returns None - and a None
    must be treated as "no action", never as assent. Ambiguity is not consent.

    "needs_token" and "needs_reference" are the two tier-3 refusals: the reply IS
    affirmative, but it does not carry the token (or, with the flag off, does not
    say what it is affirming), so it does not fire. They are distinct verdicts
    rather than a None so the caller can say WHY nothing happened - a destructive
    action silently not running is its own kind of confusing, and the next thing a
    human does is say "yes" again, louder.

    The refusal is deliberately NOT widened past the set that used to fire. A
    sentence merely opening with "yes" that names nothing ("yes, your totally
    right") falls through to the agent exactly as it always did, because over a
    confirm window that can run an hour it is far likelier to be conversation
    than consent. What changed is only that the affirmatives which USED to fire
    without a token now get told to send one.
    """
    raw = _normalize(text)
    if not raw:
        return None, None

    # Per-word punctuation stripping, because the prompt renders the token in
    # backticks and a literal copy-paste brings them along. Without this,
    # "yes `a1b2c3`" found no token, matched no phrase, and returned SILENCE -
    # the correct answer, typed correctly, doing nothing at all.
    words = [_strip_wrapping(w) for w in raw.replace(",", " ").split()]
    words = [w for w in words if w]

    token = None
    for w in words:
        if _is_token_shaped(w) and get(w):
            token = w
            break
    # Did he TRY to give a token and miss? Used only to decide whether a refusal
    # is spoken or silent - never to fire anything.
    tried_token = any(_token_attempt(w, pending) for w in words) and token is None

    # Compare against the phrase with any token removed, so "yes a1b2c3" still
    # reads as a plain affirmative.
    phrase = " ".join(w for w in words if w != token).strip()
    bare_yes = phrase in _AFFIRMATIVE
    # "yes, purge that channel" is longer than any whitelist entry, so it is
    # judged by its opening word plus what it references.
    leads_yes = bool(words) and words[0] in _AFFIRMATIVE

    # Cancelling never gets harder than confirming, on any tier. If "no" had to
    # carry a token too, the safe direction would be the inconvenient one.
    if phrase in _NEGATIVE:
        return "no", token

    if not needs_reference(pending):
        # Tiers 0-2, and every caller that passes no pending at all: the strict
        # whitelist, exactly as it has always been.
        return ("yes" if bare_yes else None), token

    # --- tier 3 ---
    if token:
        # The token identifies the action more precisely than any word could, and
        # unlike a name it cannot drift into ordinary English. It is the reference
        # the mandatory rule asks for, so it satisfies both rules.
        return ("yes" if (bare_yes or leads_yes) else None), token

    if require_token():
        # Exactly the set that used to fire, plus the bare yes that already got
        # refused - so nothing that previously reached the agent starts nagging.
        # `tried_token` joins them because a mistyped or lapsed token is the one
        # failure this change newly creates, and it is the worst one to answer
        # with silence: he did the thing he was asked to do and nothing happened.
        if bare_yes or (leads_yes and (tried_token or _references(raw, pending))):
            return "needs_token", None
        return None, None

    # Flag off: the 2026-08-17 naming rule, unchanged, as the revert path.
    if bare_yes:
        return "needs_reference", None
    if leads_yes and _references(raw, pending):
        return "yes", None
    return None, None


def _strip_wrapping(word):
    """Drop markdown and punctuation clinging to a word's edges.

    The token is shown inside backticks, so a copy-paste of the exact string the
    prompt asked for arrives as "`a1b2c3`". Bold and quotes get the same
    treatment. Only the EDGES are stripped, so "don't" survives intact.
    """
    return word.strip("`*_~\"'()[]{}<>.,:;!?").strip()


def _is_token_shaped(word):
    """Exactly what park() mints: six lowercase hex characters."""
    return len(word) == 6 and all(c in "0123456789abcdef" for c in word)


def _edit_distance(a, b, cap=3):
    """Levenshtein, bounded - these are six-character strings."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _token_attempt(word, pending):
    """Does this word look like a botched attempt at the token?

    Two ways to qualify: it is token-SHAPED (six hex, so it is either a lapsed
    token or a same-length typo), or it is a near miss of the token actually
    parked. The second case is the one that matters and the first version of this
    change did not have it: a dropped character, an extra character or a non-hex
    slip all failed the shape test, so the ONE typo that got a spoken refusal was
    the least likely kind. Silence is the wrong answer to someone who did what he
    was asked and fat-fingered it.
    """
    if _is_token_shaped(word):
        return True
    if pending is None or not (3 <= len(word) <= 9):
        return False
    return _edit_distance(word, pending.token) <= 2


def _references(raw, pending):
    """Does this reply name the action it is confirming?"""
    said = set(re.split(r"[^a-z0-9]+", raw))
    return bool(said & _action_words(pending))


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
    # The prompt has to state the rule that will actually be applied. Telling him
    # "reply yes" and then refusing a bare yes is how a safety feature becomes a
    # thing people fight with - and the next move after an unexplained no-op is to
    # say yes again, louder.
    if needs_token(p):
        how = (f"Tap **Approve**, or reply `yes {p.token}` - the token is required "
               f"here. A bare \"yes\" will not fire this one: no undo on this tier. "
               f"**no** cancels.")
    elif needs_reference(p):
        verb = p.action.split("_")[0]
        how = (f"Tap a button, or reply with a yes that **names what it is** - "
               f"\"yes, {verb} it\" or `yes {p.token}`. A bare \"yes\" will not fire "
               f"this one: no undo on this tier. **no** cancels.")
    else:
        how = (f"Tap a button, or reply **yes** to run it / **no** to cancel. "
               f"Token `{p.token}`.")
    lines += ["", f"{how} Expires in {p.seconds_left // 60}m."]
    return "\n".join(lines)
