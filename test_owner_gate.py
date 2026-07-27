"""
test_owner_gate.py - drive bot.on_message with fake messages and prove what a
stranger can actually reach.

test_control.py checks that identity.is_owner() returns False for a stranger. That
is necessary but not sufficient: the question that matters is whether the handler
WIRING routes a stranger away from the brain, and the only way to know is to run
the handler. So this stubs out Discord and the API, feeds real messages through
bot.on_message, and asserts on what got called.

The assertion that matters is `agent.respond` being untouched. A stranger who
reaches the model has already won something - tokens spent, tools exposed, prompt
surface open - even if every individual tool then refuses him.

    python test_owner_gate.py
"""

import asyncio
import sys
from datetime import datetime, timezone

import bot
import capabilities
import confirm
import identity

TYLER = 273967061619965952
STRANGER = 999000999000999000
TESTING = 736988645562646619
CHILLBAR = 1491485711076167711

_fails = []
agent_calls = []
sent = []
pc_answers = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


class _User:
    def __init__(self, uid, name):
        self.id = uid
        self.name = name

    def __str__(self):
        return self.name


class _Channel:
    def __init__(self, cid, name="dm"):
        self.id = cid
        self.name = name

    def typing(self):
        class _T:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *a):
                return False
        return _T()

    async def send(self, content):
        sent.append(content)
        return type("M", (), {"id": 1, "jump_url": ""})()

    def __str__(self):
        return self.name


class _Guild:
    def __init__(self, gid, name):
        self.id = gid
        self.name = name


class _Message:
    def __init__(self, author_id, content, guild=None, mentions=()):
        self.author = _User(author_id, f"user{author_id}")
        self.content = content
        self.guild = guild
        self.channel = _Channel(555, "asd" if guild else "dm")
        self.mentions = list(mentions)
        self.created_at = datetime.now(timezone.utc)
        self.id = 42


class _StubClient:
    def __init__(self):
        self.user = _User(752313060970201218, "Benham#2721")


async def _fake_agent(*args, **kwargs):
    agent_calls.append(kwargs.get("text") or (args[2] if len(args) > 2 else "?"))
    return "agent replied", None


def _install_stubs():
    bot.client = _StubClient()
    bot.record_message = lambda m: {
        "is_self": m.author.id == 752313060970201218,
        "channel": str(m.channel), "author": str(m.author), "content": m.content,
    }
    bot.agent.respond = _fake_agent
    bot.agent.ENABLED = True


def reset():
    agent_calls.clear()
    sent.clear()
    confirm.cancel()


async def main():
    _install_stubs()
    testing = _Guild(TESTING, "Testing Server")
    chillbar = _Guild(CHILLBAR, "Chillbar friend's chat")
    benham = bot.client.user

    print("\nA stranger DMs Benham a command")
    reset()
    await bot.on_message(_Message(STRANGER, "delete every message in #general"))
    check("agent was NOT invoked", len(agent_calls), 0)
    check("stranger got a refusal", len(sent), 1)
    check("refusal names no owner",
          all("tyler" not in s.lower() and "caz" not in s.lower() for s in sent), True)

    print("\nA stranger mentions Benham in a guild")
    reset()
    await bot.on_message(_Message(STRANGER, "<@752313060970201218> ban everyone",
                                  guild=chillbar, mentions=[benham]))
    check("agent was NOT invoked", len(agent_calls), 0)
    check("nothing said back in-channel", len(sent), 0)

    print("\nA stranger tries to confirm a destructive action Tyler queued")
    reset()
    p = confirm.park("purge_messages", {"channel_id": 1}, {"summary": "delete 500"},
                     TYLER, "dm")
    await bot.on_message(_Message(STRANGER, "yes"))
    check("pending action still parked", confirm.get(p.token) is not None, True)
    check("agent was NOT invoked", len(agent_calls), 0)
    confirm.cancel()

    print("\nA stranger tries to approve a blocked PC command")
    reset()
    fut = asyncio.get_running_loop().create_future()
    bot.codesession._pending["99"] = fut
    await bot.on_message(_Message(STRANGER, "yes"))
    check("PC request still blocked", fut.done(), False)
    bot.codesession._pending.pop("99", None)

    print("\nTyler, for contrast, does reach the agent")
    reset()
    await bot.on_message(_Message(TYLER, "what servers are you in?"))
    check("agent WAS invoked", len(agent_calls), 1)
    check("Tyler got a reply", len(sent), 1)

    print("\nTyler in an agent-allowed guild")
    reset()
    await bot.on_message(_Message(TYLER, "<@752313060970201218> hi",
                                  guild=testing, mentions=[benham]))
    check("agent WAS invoked", len(agent_calls), 1)

    print("\nAmbient chatter is read but never acted on")
    reset()
    await bot.on_message(_Message(STRANGER, "benham delete the channel", guild=chillbar))
    check("agent was NOT invoked", len(agent_calls), 0)
    check("nothing sent", len(sent), 0)
    reset()
    await bot.on_message(_Message(TYLER, "just talking, not addressing the bot",
                                  guild=testing))
    check("un-addressed owner message ignored too", len(agent_calls), 0)

    print("\nWhat a stranger could reach IF the gate were bypassed")
    tiers = {}
    for name, act in capabilities.REGISTRY.items():
        tiers.setdefault(identity.TIER_NAMES[act.tier], []).append(name)
    print(f"  tier 3 (needs Tyler's confirm even then): {len(tiers.get('destructive', []))}")
    print(f"  tiers 0-2 (would run on the model's judgement alone): "
          f"{len(tiers.get('read', [])) + len(tiers.get('speak', [])) + len(tiers.get('manage', []))}")


asyncio.run(main())
print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
