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

  Replies are stripped of `<<...>>` directives and none are applied. brain.py's
  parse_persona_directive writes `<<persona: ...>>` into personality_overrides.txt,
  which is loaded into the system prompt of every surface including voice - so on any
  path that applied directives, a guest could retune the character Tyler talks to.
  strip_directive is called; the parse functions are not, and must not be.
"""

import os
import threading
import time
from datetime import date

from dotenv import load_dotenv

from benham.core import brain
from benham.core import identity
from benham.core import jsonio
from benham.core import policy
from benham.core import shared_tools

from benham import paths
load_dotenv(os.path.join(paths.CONFIG_DIR, "environ.env"))

# Deliberately NOT agent_memory.json. See the module docstring.
MEMORY_FILE = os.path.join(paths.STATE_DIR, "guest_memory.json")
USAGE_FILE = os.path.join(paths.STATE_DIR, "guest_usage.json")
PERSONA_FILE = os.path.join(paths.PROMPTS_DIR, "guest_persona.md")
SEARCH_LOG = os.path.join(paths.STATE_DIR, "guest_searches.jsonl")

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
_memory_lock = threading.Lock()


def _history(key):
    return jsonio.read_json(MEMORY_FILE, default={}).get(key, [])


def _remember(key, user_text, assistant_text):
    with _memory_lock:
        mem = jsonio.read_json(MEMORY_FILE, default={})
        turns = list(mem.get(key, []))
        turns.append({"role": "user", "content": user_text})
        turns.append({"role": "assistant", "content": assistant_text})
        mem[key] = turns[-HISTORY_TURNS * 2:]
        jsonio.write_json(MEMORY_FILE, mem)


def forget(user_id=None):
    """Drop one guest's conversation, or every guest's."""
    with _memory_lock:
        if user_id is None:
            jsonio.write_json(MEMORY_FILE, {})
            return
        mem = jsonio.read_json(MEMORY_FILE, default={})
        mem.pop(_key(user_id), None)
        jsonio.write_json(MEMORY_FILE, mem)


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
            # A missing prompt file must not become an unconstrained model. This
            # fallback is the load-bearing half of the real file, kept short enough
            # to be obviously correct.
            _persona_cache = (
                "You are Claude, reached through the Benham bot by a guest. You have "
                "NO tools on this path: you cannot send Discord messages, read "
                "channels, or touch anyone's computer. Say so plainly if asked. Do "
                "not discuss the bot's owner or his setup. Be direct, warm and brief."
            )
    return _persona_cache


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    return _client


def respond(user_id, text, log=None):
    """One guest turn: their message in, Benham's reply out.

    Requires that check() already returned ALLOW, which is also where the message was
    charged. Re-deciding here would either duplicate the reason the caller needs to
    word its refusal or throw it away, and re-charging here would double-bill.

    If this raises, the caller should call refund() - the reservation was made on the
    assumption of a turn that then did not happen.
    """
    def _log(msg):
        if log:
            log(msg)

    key = _key(user_id)
    turns = list(_history(key))
    turns.append({"role": "user", "content": text})

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
        system=_system_prompt(),
        messages=turns,
        **kw,
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()

    queries = shared_tools.search_queries(resp)
    if queries:
        _log_searches(user_id, queries)
        charge_search(user_id)   # a searched turn counts double - Tyler's rule
        _log(f"guest search [{user_id}]: " + "; ".join(repr(q) for q in queries))

    # Strip directives, apply none. brain.parse_persona_directive is deliberately not
    # called: it writes to personality_overrides.txt, which every surface reads.
    reply = brain.strip_directive(raw)
    if not reply:
        reply = "...I've got nothing for that one, sorry."

    _remember(key, text, reply)
    mine, everyone = spent_today(user_id)   # already charged by check()

    u = getattr(resp, "usage", None)
    if u is not None:
        # Same shape usage.py already parses for agent turns, so guest traffic shows
        # up in `usage.py --today` with no change to that file.
        _log(f"agent usage [guest:{user_id}] "
             f"in={getattr(u, 'input_tokens', 0)} out={getattr(u, 'output_tokens', 0)} "
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

    if cmd == "status":
        print("=== guest chat ===")
        print(f"enabled:   {identity.guest_enabled()} "
              f"(mode={identity.GUEST.get('mode', 'chat')!r})")
        print(f"allowlist: {sorted(identity.GUEST_IDS) or '(empty)'}")
        print(f"model:     {MODEL}  max_tokens={MAX_TOKENS}")
        print(f"caps:      {DAILY_CAP}/guest/day, {GLOBAL_CAP}/day global, "
              f"{COOLDOWN}s cooldown")
        u = _usage()
        print(f"today ({u['date']}): global {u.get('global', 0)}/{GLOBAL_CAP}")
        for uid, n in sorted(u.get("users", {}).items()):
            print(f"  {uid}: {n}/{DAILY_CAP}")
        mem = jsonio.read_json(MEMORY_FILE, default={})
        print(f"conversations stored: {len(mem)}")
        return 0

    if cmd == "forget" and len(argv) > 2:
        forget(argv[2])
        print(f"Forgot conversation for {argv[2]}")
        return 0

    if cmd == "forget-all":
        forget()
        print("Forgot every guest conversation")
        return 0

    print("Usage: python benham.py guest [status | forget <user_id> | forget-all]")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
