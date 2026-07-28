"""
guest.py - Benham holding a conversation with someone who is not Tyler.

The feature request was doomassassin1's: a whitelist of people who can reach Claude
through Benham without waiting for Tyler to relay. What makes that safe to build is
not a longer list of rules, it is a shorter list of powers.

THE PROPERTY THIS FILE EXISTS TO HAVE. The API call below passes no `tools`
parameter. Not an empty list, not a filtered one - the argument is absent. So the
question "could a guest reach capability X" has the same answer for all 47 of them,
for the ones added next year, and for pc_task, without anything here knowing what a
capability is. A gate that has to enumerate what it forbids is a gate that can be
out of date; this one cannot be.

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
import time
from datetime import date

from dotenv import load_dotenv

import brain
import identity
import jsonio
import policy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "environ.env"))

# Deliberately NOT agent_memory.json. See the module docstring.
MEMORY_FILE = os.path.join(BASE_DIR, "guest_memory.json")
USAGE_FILE = os.path.join(BASE_DIR, "guest_usage.json")
PERSONA_FILE = os.path.join(BASE_DIR, "guest_persona.md")

_CFG = identity.guest_config()
MODEL = _CFG.get("model") or "claude-haiku-4-5"
MAX_TOKENS = int(_CFG.get("max_tokens", 500))
HISTORY_TURNS = int(_CFG.get("history_turns", 10))
COOLDOWN = float(_CFG.get("cooldown_seconds", 3))
DAILY_CAP = int(_CFG.get("daily_message_cap", 100))
GLOBAL_CAP = int(_CFG.get("global_daily_cap", 400))

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


def _spend(user_id):
    """Count one message against this guest and against the global ceiling."""
    u = _usage()
    uid = str(int(user_id))
    u["users"][uid] = int(u["users"].get(uid, 0)) + 1
    u["global"] = int(u.get("global", 0)) + 1
    jsonio.write_json(USAGE_FILE, u)
    return u["users"][uid], u["global"]


def spent_today(user_id):
    """(this guest's count, everyone's count) for today. Read-only."""
    u = _usage()
    return int(u["users"].get(str(int(user_id)), 0)), int(u.get("global", 0))


# --------------------------------------------------------------------------
# May this person talk to us right now?
# --------------------------------------------------------------------------

def check(user_id, channel_id=None):
    """Authority first, then budget. Returns a policy.Decision.

    Authority comes from policy.may_chat_as_guest so that "who may talk to Benham" has
    one answer in one file, whichever surface is asking. Only once that says yes does
    this look at counters, because a stranger should not be able to learn the state of
    Tyler's quota by watching which refusal they get.

    The `.rule` on the returned Decision is load-bearing for the caller: a refusal for
    not being a guest must look like the ordinary non-owner refusal, while a refusal
    for spending the day's messages should say so - the first must not reveal that a
    guest list exists.
    """
    ctx = policy.CallContext.guest_dm(user_id, channel_id)
    decision = policy.may_chat_as_guest(ctx)
    if not decision.allowed:
        return decision

    mine, everyone = spent_today(user_id)
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

    last = _last_call.get(int(user_id), 0)
    if time.monotonic() - last < COOLDOWN:
        return policy.Decision(
            policy.Decision.DENY, "One sec - still catching up.", "guest_cooldown")
    return policy.Decision(policy.Decision.ALLOW)


def may_chat(user_id, channel_id=None):
    """Bare boolean for a call site that only needs to branch."""
    return check(user_id, channel_id).allowed


def is_known_guest(user_id):
    """On the allowlist and switched on, regardless of quota.

    Distinct from may_chat on purpose: it answers "is this person meant to be talking
    to me" rather than "may they right now", which is what a caller needs to decide
    between an over-quota reply and a flat non-owner refusal.
    """
    return identity.guest_enabled() and identity.is_guest(user_id)


# --------------------------------------------------------------------------
# Conversation
# --------------------------------------------------------------------------

def _history(key):
    return jsonio.read_json(MEMORY_FILE, default={}).get(key, [])


def _remember(key, user_text, assistant_text):
    mem = jsonio.read_json(MEMORY_FILE, default={})
    turns = list(mem.get(key, []))
    turns.append({"role": "user", "content": user_text})
    turns.append({"role": "assistant", "content": assistant_text})
    mem[key] = turns[-HISTORY_TURNS * 2:]
    jsonio.write_json(MEMORY_FILE, mem)


def forget(user_id=None):
    """Drop one guest's conversation, or every guest's."""
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

    Assumes check() already said yes - the caller has to know WHY a refusal happened
    to word it, so re-deciding here would either duplicate that or throw the reason
    away. The quota is spent here rather than in check() so that a call that never
    happened is never billed.
    """
    def _log(msg):
        if log:
            log(msg)

    key = _key(user_id)
    turns = list(_history(key))
    turns.append({"role": "user", "content": text})

    _last_call[int(user_id)] = time.monotonic()

    resp = _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_system_prompt(),
        messages=turns,
        # NO tools= argument. This absence is the security property; see the module
        # docstring before adding anything here.
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()

    # Strip directives, apply none. brain.parse_persona_directive is deliberately not
    # called: it writes to personality_overrides.txt, which every surface reads.
    reply = brain.strip_directive(raw)
    if not reply:
        reply = "...I've got nothing for that one, sorry."

    _remember(key, text, reply)
    mine, everyone = _spend(user_id)

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
# CLI: python guest.py [status | forget <user_id> | forget-all]
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

    print("Usage: python guest.py [status | forget <user_id> | forget-all]")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
