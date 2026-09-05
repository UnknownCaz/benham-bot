"""
guest.py - Benham holding a conversation with someone who is not Tyler.

The feature request was doomassassin1's: a whitelist of people who can reach Claude
through Benham without waiting for Tyler to relay. What makes that safe to build is
not a longer list of rules, it is a shorter list of powers.

THE PROPERTY THIS FILE EXISTS TO HAVE. The API call below passes no CLIENT tools.
The single entry in `tools` is Anthropic's server-side web search, which executes on
Anthropic's infrastructure: no code runs here, nothing is fetched from this machine
or network, and there is no tool-result loop in this file for a model to steer. So
the question "could a guest reach capability X" still has the same answer for all of
them, for the ones added next year, and for pc_task, without anything here knowing
what a capability is. A gate that has to enumerate what it forbids is a gate that
can be out of date; this one cannot be. Adding any CLIENT tool here would break the
property - read this paragraph again before doing it.

Web search rules (Tyler's): a turn that searched counts DOUBLE against the daily
cap (the search is a second API round trip, so it is priced like one), and every
query is logged to guest_searches.jsonl for hand moderation.

That is also why this is a separate module rather than a flag on agent.py. agent.py's
whole job is handing the model a tool list and running the loop; "the same thing but
the list is empty" is one wrong conditional away from not being empty. Two files that
do different things cannot be collapsed by accident.

WHAT IS DEFENDED AND WHERE. Authority questions - is guest chat on, is this person on
the list - live in policy.may_chat_as_guest and identity.is_guest, next to the owner
rules they parallel. This file owns only what is genuinely its own: how many messages
someone has spent today, what they said last time, and the call itself. Rules in the
rules file; state here.

Two smaller things worth knowing:

  Memory is a separate FILE, not a prefixed key in agent_memory.json. A prefix means
  Tyler's history and a guest's history are one typo apart. A different path is not.

  Replies are stripped of `<<...>>` directives and none are applied. The reason was
  that brain.py's parse_persona_directive wrote `<<persona: ...>>` into
  personality_overrides.txt, so any path that APPLIED directives let a guest retune
  the character Tyler talks to. Voice was archived 2026-08-16 and took the only
  applier with it, so nothing can act on a directive today - but strip stays, and
  stays load-bearing: the persona still describes the syntax, so the model can still
  emit one, and an unstripped `<<...>>` in a friend's DM is a leaked internal.
  directives.strip_directive is called; nothing parses. Do not add an applier here.
"""

import os
import threading
import time
from datetime import date

from dotenv import load_dotenv

from benham.core import directives
from benham.core import identity
from benham.core import issues
from benham.core import jsonio
from benham.core import policy
from benham.core import shared_tools
from benham.core import turnmemory

from benham import paths
load_dotenv(os.path.join(paths.CONFIG_DIR, "environ.env"))

# Deliberately NOT agent_memory.json. See the module docstring.
MEMORY_FILE = os.path.join(paths.process_state_dir(), "guest_memory.json")
USAGE_FILE = os.path.join(paths.process_state_dir(), "guest_usage.json")
PERSONA_FILE = os.path.join(paths.prompts_for(paths.PROCESS_FACE), "guest_persona.md")
SEARCH_LOG = os.path.join(paths.process_state_dir(), "guest_searches.jsonl")

_CFG = identity.guest_config()
MODEL = _CFG.get("model") or "claude-haiku-4-5"
MAX_TOKENS = int(_CFG.get("max_tokens", 500))
HISTORY_TURNS = int(_CFG.get("history_turns", 10))
COOLDOWN = float(_CFG.get("cooldown_seconds", 3))
DAILY_CAP = int(_CFG.get("daily_message_cap", 100))
GLOBAL_CAP = int(_CFG.get("global_daily_cap", 400))
WEB_SEARCH = bool(_CFG.get("web_search", True))
SEARCHES_PER_TURN = int(_CFG.get("searches_per_turn", 2))
TOOL_ROUND_COST = int(_CFG.get("tool_round_cost", 1))

_client = None
_persona_cache = None
_last_call = {}          # user_id -> monotonic timestamp of last accepted message

# --------------------------------------------------------------------------
# Outreach quiet: a temporary mute of the guest brain for one user, so that
# while Claude is holding a real conversation with someone through Benham
# (discord-outreach), the autonomous brain does not talk over it - two Claudes
# answering in one DM confuses the human and bills two API calls per turn.
#
# PERSISTED with a deadline, not a config flag: control.json is read once at
# import (see identity.guest_enabled) and quiet is per-conversation state, not
# an identity question. The TTL is the safety property, and it is the DEADLINE
# that carries it, not the process: a session that crashes mid-outreach cannot
# leave someone permanently unable to talk to Benham, because the stored
# deadline expires on its own no matter what restarts underneath it.
#
# This used to be in-memory, on the stated theory that "a bot restart clears it
# for the same reason, and that is fine". 2026-08-20 proved it is not: the
# supervisor restarted the bot twice INSIDE one outreach conversation (routine
# restarts, picking up an allowlist change), the mute died with the process
# both times, and the brain talked over a live outreach thread with the person
# it had been quieted for. Restarts are ordinary events here, not rare manual
# ones - so the deadline lives in state/guest_quiet.json and survives them.
# --------------------------------------------------------------------------

QUIET_DEFAULT_MINUTES = 60
QUIET_MAX_MINUTES = 240
QUIET_FILE = os.path.join(paths.process_state_dir(), "guest_quiet.json")

_quiet = None            # user_id -> time.time() deadline; None until first touch
_quiet_lock = threading.Lock()


def _quiet_map():
    """The live deadline map, loaded from QUIET_FILE on first touch.

    Lazy rather than at import so a test can repoint QUIET_FILE before anything
    is read. Caller must hold _quiet_lock. JSON keys are strings, so ids are
    normalised back to int on load - the same int() every accessor applies -
    and a damaged entry is dropped rather than wedging every quiet call."""
    global _quiet
    if _quiet is None:
        raw = jsonio.read_json(QUIET_FILE, default={})
        _quiet = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    _quiet[int(k)] = float(v)
                except (TypeError, ValueError):
                    continue
    return _quiet


def _save_quiet(q):
    """Persist the map atomically. Caller must hold _quiet_lock."""
    jsonio.write_json(QUIET_FILE, {str(k): v for k, v in q.items()})


def quiet(user_id, minutes=QUIET_DEFAULT_MINUTES):
    """Silence the guest brain for this user; returns the epoch deadline."""
    minutes = max(1, min(int(minutes), QUIET_MAX_MINUTES))
    until = time.time() + minutes * 60
    with _quiet_lock:
        q = _quiet_map()
        q[int(user_id)] = until
        _save_quiet(q)
    return until


def wake(user_id):
    """Lift a quiet early. Returns True if one was actually in effect."""
    with _quiet_lock:
        q = _quiet_map()
        was = q.pop(int(user_id), None) is not None
        if was:
            _save_quiet(q)
        return was


def quiet_until(user_id):
    """The active quiet deadline for this user, or None. Prunes on read, so an
    expired entry can never linger and shadow a later is-quiet question."""
    now = time.time()
    with _quiet_lock:
        q = _quiet_map()
        until = q.get(int(user_id))
        if until is None:
            return None
        if until <= now:
            del q[int(user_id)]
            _save_quiet(q)
            return None
        return until


def _key(user_id):
    """Conversation key. Namespaced as well as separately filed - belt and braces,
    and it makes a stray key visibly wrong when reading the file by eye."""
    return f"guest:{int(user_id)}"


# --------------------------------------------------------------------------
# Quota. Tyler is paying for every one of these messages.
# --------------------------------------------------------------------------

def _today():
    return date.today().isoformat()


def _usage():
    """Today's counters, resetting on a date change.

    The reset is on read rather than on a timer: there is no scheduler here, and a
    bot that happens to be running at midnight should not be the thing that decides
    whether a cap rolls over.
    """
    u = jsonio.read_json(USAGE_FILE, default={})
    if u.get("date") != _today():
        return {"date": _today(), "users": {}, "global": 0}
    u.setdefault("users", {})
    u.setdefault("global", 0)
    return u


# Held across the read-decide-write of a reservation. Guest turns run in worker
# threads (bot.py hands respond() to asyncio.to_thread), so two DMs really can be in
# here at once.
_quota_lock = threading.Lock()


def _reserve(user_id):
    """Claim one message against both caps, atomically. Returns a Decision.

    Checking the cap and spending against it have to be one operation. When they
    were two, both halves were individually correct and the pair was not: two
    messages arriving together each read a count below the cap, and each then
    incremented it, so the cap was passed by however many turns were in flight.
    That is only a cost bug rather than an access one - no capability sits behind
    this - but the caps exist because Tyler is billed per message, so a cap that
    leaks under exactly the condition that costs the most is not much of a cap.

    Reserving here rather than after the API call means a turn that then fails has
    still spent its message. That is the direction to err: a failed call may well
    have been billed anyway, and refund() exists for the case where it certainly
    was not.
    """
    with _quota_lock:
        u = _usage()
        uid = str(int(user_id))
        mine = int(u["users"].get(uid, 0))
        everyone = int(u.get("global", 0))
        if mine >= DAILY_CAP:
            return policy.Decision(
                policy.Decision.DENY,
                f"That's your {DAILY_CAP} messages for today - back tomorrow.",
                "guest_quota")
        if everyone >= GLOBAL_CAP:
            return policy.Decision(
                policy.Decision.DENY,
                "I've hit my message budget for today across everyone. Try tomorrow.",
                "guest_global_quota")
        u["users"][uid] = mine + 1
        u["global"] = everyone + 1
        jsonio.write_json(USAGE_FILE, u)
        return policy.Decision(policy.Decision.ALLOW)


def refund(user_id):
    """Hand back a reserved message when the turn never happened.

    For the case where the API call raised before it could have been billed. Floors
    at zero rather than trusting the counter, so a double refund cannot mint quota.
    """
    with _quota_lock:
        u = _usage()
        uid = str(int(user_id))
        u["users"][uid] = max(0, int(u["users"].get(uid, 0)) - 1)
        u["global"] = max(0, int(u.get("global", 0)) - 1)
        jsonio.write_json(USAGE_FILE, u)


def charge_search(user_id):
    """Spend one extra message for a turn that used web search.

    Tyler's pricing rule: a searched turn counts double, because the search is a
    second round trip billed like one. Charged AFTER the response (only then is it
    known a search happened), so unlike _reserve this never refuses - a guest at
    the cap already got their answer, and the honest ledger entry matters more
    than a cap technically exceeded by one. The cap check next turn settles it.
    """
    with _quota_lock:
        u = _usage()
        uid = str(int(user_id))
        u["users"][uid] = int(u["users"].get(uid, 0)) + 1
        u["global"] = int(u.get("global", 0)) + 1
        jsonio.write_json(USAGE_FILE, u)








def charge_rounds(user_id, extra_rounds):
    """Spend TOOL_ROUND_COST messages per tool round beyond a turn's first.

    Guest-refactor Stage 3, guest_agent.py's pricing rule, and the same shape as
    charge_search for the same reason: charged AFTER the turn (only then is the
    round count known), never refuses (the guest already got their answer; the
    honest ledger matters more than a cap exceeded by one), settled by the cap
    check on their next turn.
    """
    n = int(extra_rounds) * TOOL_ROUND_COST
    if n <= 0:
        return
    with _quota_lock:
        u = _usage()
        uid = str(int(user_id))
        u["users"][uid] = int(u["users"].get(uid, 0)) + n
        u["global"] = int(u.get("global", 0)) + n
        jsonio.write_json(USAGE_FILE, u)


def _log_searches(user_id, queries):
    """Append each query to guest_searches.jsonl - the hand-moderation trail.

    The writer moved to shared_tools.log_searches (Stage 0) so the owner agent's
    search log comes out the same shape; this wrapper pins THIS surface's file and
    role so no caller in this module can misfile a guest query.
    """
    shared_tools.log_searches(SEARCH_LOG, user_id, queries, role="guest")


def spent_today(user_id):
    """(this guest's count, everyone's count) for today. Read-only."""
    u = _usage()
    return int(u["users"].get(str(int(user_id)), 0)), int(u.get("global", 0))


# --------------------------------------------------------------------------
# May this person talk to us right now?
# --------------------------------------------------------------------------

def check(user_id, channel_id=None):
    """Authority, then rate, then budget. Returns a policy.Decision.

    CONSUMES A MESSAGE when it returns ALLOW. This is a gate, not a question: the
    caller may ask once per inbound message and must then either send the turn or
    call refund(). Making it advisory is what the race came from - anything that
    answers "is there room" without also taking the room is true only until the next
    thread reads it.

    Authority comes from policy.may_chat_as_guest so that "who may talk to Benham"
    has one answer in one file, whichever surface is asking. Only once that says yes
    does this look at rate or budget, so a stranger cannot learn the state of Tyler's
    quota by watching which refusal they get.

    Cooldown is evaluated before the reservation, so a guest typing too fast is told
    to wait rather than being charged for the message that was refused.

    The `.rule` on the returned Decision is load-bearing for the caller: a refusal
    for not being a guest must look like the ordinary non-owner refusal, while a
    refusal for spending the day's messages should say so - the first must not reveal
    that a guest list exists.
    """
    ctx = policy.CallContext.guest_dm(user_id, channel_id)
    decision = policy.may_chat_as_guest(ctx)
    if not decision.allowed:
        return decision

    last = _last_call.get(int(user_id), 0)
    if time.monotonic() - last < COOLDOWN:
        return policy.Decision(
            policy.Decision.DENY, "One sec - still catching up.", "guest_cooldown")

    reserved = _reserve(user_id)
    if not reserved.allowed:
        return reserved

    _last_call[int(user_id)] = time.monotonic()
    return policy.Decision(policy.Decision.ALLOW)


def is_known_guest(user_id):
    """On the allowlist and switched on, regardless of quota. Consumes nothing.

    The question a caller needs before check(): "is this person meant to be talking
    to me at all", which decides between an over-quota reply and a flat non-owner
    refusal. Kept deliberately free of any counter, because it runs on every inbound
    non-owner message including from strangers, and check() is the one that spends.

    There is deliberately no `may_chat()` convenience wrapper. It existed, returned
    check().allowed, and became a hazard the moment check() started reserving - a
    predicate-shaped name that quietly bills Tyler is exactly the kind of thing that
    gets called twice in a future edit.
    """
    return identity.guest_enabled() and identity.is_guest(user_id)


# --------------------------------------------------------------------------
# Conversation
# --------------------------------------------------------------------------

# Guards every write to MEMORY_FILE, for the same reason _quota_lock guards the
# usage file - and it is not only about losing an update. jsonio.write_json stages
# through a single `path + ".tmp"` and then os.replace()s it, so two threads writing
# the same file at once collide on that one temp path: on Windows the loser gets
# PermissionError [WinError 32] rather than a merge conflict. Guest turns run in
# worker threads (asyncio.to_thread), so this is reachable whenever two guests are
# mid-conversation - the failure would be a crashed turn, not a wrong count.
# Shared with agent.py via turnmemory - one implementation of the six lines that
# store a turn pair. This copy was never broken, but only because it happened to
# name a variable `raw` instead of `text`; agent.py's identical copy stored
# Benham's own replies as Tyler's messages for twelve days. Luck is not a
# maintenance strategy.
#
# The FILE stays separate, which is the part that matters here: guest_memory.json
# is a different path, not a prefixed key, so Tyler's history and a guest's cannot
# end up in one thread through a typo. turnmemory takes the path as an argument
# precisely so sharing the logic cannot erode that.
_store = turnmemory.TurnMemory(lambda: MEMORY_FILE, HISTORY_TURNS)


def _history(key):
    return _store.history(key)


def _remember(key, user_text, assistant_text):
    _store.remember(key, user_text, assistant_text)


def forget(user_id=None):
    """Drop one guest's conversation, or every guest's."""
    _store.forget(None if user_id is None else _key(user_id))


def _system_prompt():
    """guest_persona.md, and nothing else.

    Notably NOT guardrails.md or persona.md. Both are written for the owner: they name
    him, and persona.md tells the model it has real tools and operates a machine. A
    model told it has tools it does not have will promise actions that never happen,
    which on this path is every action.
    """
    global _persona_cache
    if _persona_cache is None:
        try:
            with open(PERSONA_FILE, "r", encoding="utf-8") as f:
                _persona_cache = f.read()
        except OSError:
            # A missing prompt file must not become an unconstrained model - and it
            # must not become a DIFFERENT one either. This fallback carries both
            # load-bearing halves of the real file, no tools and Benham, kept short
            # enough to be obviously correct.
            _persona_cache = (
                "You are Benham, an AI, talking to a guest in a DM. You are Benham on "
                "every surface - never introduce yourself as Claude or as the assistant "
                "they use somewhere else - and you never pretend to be human or deny "
                "being an AI. You have NO tools on this path: you cannot send Discord "
                "messages, read channels, or touch anyone's computer. Say so plainly if "
                "asked. Do not discuss the bot's owner or his setup. Be direct, warm "
                "and brief."
            )
    return _persona_cache


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    return _client


def respond(user_id, text, log=None, content=None):
    """One guest turn: their message in, Benham's reply out.

    Requires that check() already returned ALLOW, which is also where the message was
    charged. Re-deciding here would either duplicate the reason the caller needs to
    word its refusal or throw it away, and re-charging here would double-bill.

    If this raises, the caller should call refund() - the reservation was made on the
    assumption of a turn that then did not happen.

    `content` is the user turn as API content BLOCKS, for a message that carried
    more than text - a picture to look at, a quoted message, a link preview.
    bot.py builds it (msgparts does the encoding); None means an ordinary text
    turn and nothing here changes.

    THIS DOES NOT ADD A TOOL, and the distinction is the whole of this file's
    security story rather than a technicality. The blocks are finished before the
    call - assembled from the message that was just sent to us, by code the model
    never talks to - so there is still no tool definition here, no name for a
    model to emit, and no tool-result loop to steer. A guest gains the ability to
    be LOOKED AT; they do not gain the ability to make Benham fetch anything. The
    question the docstring above asks - "could a guest reach capability X" - has
    exactly the same answer it had before, for the same reason.

    `text` stays required and stays the thing that is REMEMBERED. History holds a
    description of the picture, never the picture: HISTORY_TURNS is 5 here, so a
    remembered image would be re-sent and re-billed on the next five turns of the
    conversation. This way it costs once, on the turn it arrives.

    COST, because Tyler pays for these. An image is charged by visual tokens,
    ceil(w/28) x ceil(h/28), and this surface runs a standard-resolution model
    (claude-haiku-4-5), which downscales anything past 1568px on the long edge
    and so caps at 1568 visual tokens per image whatever gets sent. At Haiku
    input rates that is about $0.0016 - roughly one extra ordinary turn's worth
    of input. Even the absurd case, every one of a guest's 100 daily messages
    carrying a full-size picture, is about $0.16 for that guest and $0.63 across
    the 400-message global cap.

    So images are NOT charged extra against the cap, unlike a web search. That is
    deliberate and it is Tyler's to overrule: a search costs double because it is
    a second API round trip, where an image is only a bigger first one, and
    charging double for the exact gesture this was built to support would tax
    Doom for reaching for a screenshot. The ceilings that DO hold are msgparts'
    four-images-per-message and 4MB-each, which bound the worst turn.
    """
    def _log(msg):
        if log:
            log(msg)

    key = _key(user_id)
    turns = list(_history(key))
    # Blocks to the API, text to history. See the docstring: they differ only when
    # a picture came in, and keeping them apart is what makes it cost once.
    turns.append({"role": "user", "content": content or text})

    # The cooldown clock is started by check(), not here: it has to advance for a
    # message that was accepted even if this call then fails, or a guest whose turns
    # error out is free to retry with no rate limit at all.
    kw = {}
    if WEB_SEARCH:
        # Anthropic's SERVER-SIDE web search - the only tool a guest gets, and the
        # only kind that keeps this file's security property: it runs on Anthropic's
        # servers, touches nothing on this machine or network, and there is no
        # client tool-result loop here for fetched content to steer. Do NOT add
        # client tools; see the module docstring. shared_tools is server-side-only
        # by charter, so building the entry there does not weaken this paragraph.
        kw["tools"] = [shared_tools.web_search_tool(SEARCHES_PER_TURN)]
    resp = _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # The persona is a static file read once into _persona_cache: same bytes on
        # every turn, no clock, no per-guest interpolation. That makes it the one
        # thing here worth a cache breakpoint - it is ~3.3k tokens re-billed at full
        # price on every guest message otherwise, which at this history size is over
        # half the input of a turn. agent.py:461 has done this since it was written;
        # this path simply never got it. Cached reads bill at 0.1x, and the guest
        # turns that matter arrive a minute apart, well inside the 5-minute TTL.
        # If anything per-guest or time-varying is ever added to the system prompt,
        # it must go in a SECOND block after this one or the cache dies silently -
        # check usage.cache_read_input_tokens before believing otherwise.
        system=[{"type": "text", "text": _system_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=turns,
        **kw,
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()

    # The issue-offer tag (INTENT item 23), parsed from the RAW reply before the
    # strip below removes it. This does NOT add a tool and does not weaken the
    # module docstring's property: nothing executes on model output - parsing
    # only PARKS a proposal, the filing runs on the guest's own next-message
    # affirmative in bot.py, and what gets filed is `text` as captured HERE by
    # code, never the model's retelling. issues.py's docstring carries the full
    # argument. Non-issuers fall through silently (returns None, tag stripped).
    offer_line = None
    try:
        offer_line = issues.offer_from_reply(user_id, raw, text)
        if offer_line is None:
            # The model declined to tag this turn. Ask CODE whether the guest
            # just reported a defect anyway - twice in two days the model has
            # missed one that a human read as obvious, and a third prompt patch
            # is the same bet at a higher stake. Runs second, and only on a
            # turn the tag did not claim, so a guest is never asked twice.
            offer_line = issues.offer_from_message(user_id, text)
            if offer_line:
                _log(f"guest issue offer [detector] parked for {user_id}: "
                     f"{text[:120]!r}")
    except Exception as e:  # noqa: BLE001 - an offer must never eat a reply
        _log(f"guest issue offer failed to park ({e}) - reply unaffected")

    queries = shared_tools.search_queries(resp)
    if queries:
        _log_searches(user_id, queries)
        charge_search(user_id)   # a searched turn counts double - Tyler's rule
        _log(f"guest search [{user_id}]: " + "; ".join(repr(q) for q in queries))

    # Strip directives, apply none. Nothing here parses one - see the module
    # docstring for why that stays true now that voice took the applier with it.
    reply = directives.strip_directive(raw)
    if not reply:
        reply = "...I've got nothing for that one, sorry."
    if offer_line:
        # Appended by CODE, so the ask the guest reads is always the same
        # standard sentence - the persona tells the model the tag IS the ask
        # and not to word its own, which keeps a forgotten or doubled ask
        # unrepresentable.
        reply = f"{reply}\n\n{offer_line}"

    _remember(key, text, reply)
    mine, everyone = spent_today(user_id)   # already charged by check()

    u = getattr(resp, "usage", None)
    if u is not None:
        # Same shape usage.py already parses for agent turns, so guest traffic shows
        # up in `usage.py --today` with no change to that file.
        # cache_read/cache_write ride BEFORE model= on purpose: usage.py's RE_AGENT
        # captures everything up to " model=" as the body and hands it to kv(), which
        # parses arbitrary key=value tokens - so these are free to add here and would
        # be invisible (or worse, break the model match) after it. They are logged
        # because a prompt cache fails SILENTLY: a stale-looking bill is the only
        # symptom otherwise. cache_read of 0 across a live back-and-forth means the
        # system prefix stopped being byte-identical - look there first.
        _log(f"agent usage [guest:{user_id}] "
             f"in={getattr(u, 'input_tokens', 0)} out={getattr(u, 'output_tokens', 0)} "
             f"cache_read={getattr(u, 'cache_read_input_tokens', 0)} "
             f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} "
             f"model={MODEL}")
    _log(f"guest chat: {user_id} used {mine}/{DAILY_CAP} today "
         f"(global {everyone}/{GLOBAL_CAP})")
    return reply


# --------------------------------------------------------------------------
# CLI: python benham.py guest [status | forget <user_id> | forget-all]
# --------------------------------------------------------------------------

def _main(argv):
    try:
        import sys as _s
        _s.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    cmd = argv[1] if len(argv) > 1 else "status"
    # Phase B: the guest stores and switches live where the bot runs. These
    # forward there when config/remote.json names a host, else run here.
    from benham.core import remote
    stores = remote.stores

    if cmd == "status":
        g = stores.rpc.guest_status()
        print("=== guest chat ===")
        print(f"enabled:   {g['enabled']} (mode={g['mode']!r})")
        print(f"allowlist: {g['allowlist'] or '(empty)'}")
        print(f"model:     {g['model']}  max_tokens={g['max_tokens']}")
        print(f"caps:      {g['daily_cap']}/guest/day, {g['global_cap']}/day global, "
              f"{g['cooldown']}s cooldown")
        u = g["usage"]
        print(f"today ({u['date']}): global {u.get('global', 0)}/{g['global_cap']}")
        for uid, n in sorted(u.get("users", {}).items()):
            print(f"  {uid}: {n}/{g['daily_cap']}")
        print(f"conversations stored: {g['conversations_stored']}")
        return 0

    if cmd == "forget" and len(argv) > 2:
        stores.guest.forget(argv[2])
        print(f"Forgot conversation for {argv[2]}")
        return 0

    if cmd == "forget-all":
        stores.guest.forget()
        print("Forgot every guest conversation")
        return 0

    if cmd == "off":
        # INTENT decision 43: the panic button, owner-gated through the
        # registry like everything else. Two steps (preview, then the token),
        # the shape delete.py has - the bot restarts on the second call and
        # guests are refused from the next boot. Turning them back ON stays a
        # control.json edit at the keyboard, on purpose.
        from benham.cli.delete import run_two_step
        rest = list(argv[2:])
        no_wait = "--no-wait" in rest
        rest = [a for a in rest if a != "--no-wait"]
        token = None
        if "--confirm-token" in rest:
            i = rest.index("--confirm-token")
            if i + 1 >= len(rest):
                print("--confirm-token needs a token", file=_s.stderr)
                return 2
            token = rest[i + 1]
        return run_two_step(
            "guest_off",
            describe="set guest.enabled=false in control.json and restart the bot",
            rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} guest off "
                             f"--confirm-token {t}"),
            token=token, no_wait=no_wait,
            nothing="NOTHING CHANGED YET - guest chat is still on.")

    print("Usage: python benham.py guest [status | forget <user_id> | forget-all | off]")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
