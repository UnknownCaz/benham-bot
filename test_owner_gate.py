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

    async def send(self, content=None, embed=None, view=None):
        # Record what a human would read: embed answers land as their description.
        sent.append(content if content is not None
                    else (embed.description if embed is not None else ""))
        return type("M", (), {"id": 1, "jump_url": ""})()

    def __str__(self):
        return self.name


class _Guild:
    def __init__(self, gid, name):
        self.id = gid
        self.name = name


class _Message:
    def __init__(self, author_id, content, guild=None, mentions=(), attachments=()):
        self.author = _User(author_id, f"user{author_id}")
        self.content = content
        self.guild = guild
        self.channel = _Channel(555, "asd" if guild else "dm")
        self.mentions = list(mentions)
        self.created_at = datetime.now(timezone.utc)
        self.id = 42
        # Every real Message has this, empty or not. on_message checks it to decide
        # whether a file arrived, so a stub without it tests a message shape that
        # cannot exist.
        self.attachments = list(attachments)
        # Same rule: every real Message carries a reference (None when it isn't a
        # reply), and the pc.. branch reads it. test_pc_reply.py owns the non-None
        # cases.
        self.reference = None
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class _StubClient:
    def __init__(self):
        self.user = _User(752313060970201218, "Benham#2721")
        # speak_in_voice resolves its channel by id through the client, the same as
        # every other capability, so the stub has to be able to answer that.
        self.channels = {}

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    async def fetch_channel(self, cid):
        ch = self.channels.get(int(cid))
        if ch is None:
            raise capabilities.ActionError(f"no channel {cid}")
        return ch

    def get_guild(self, gid):
        return _Guild(int(gid), f"guild-{gid}")


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

    print("\nOwner mention in a NON-agent guild (the bug this refactor fixed)")
    reset()
    await bot.on_message(_Message(TYLER, "<@752313060970201218> what's up",
                                  guild=chillbar, mentions=[benham]))
    # identity.agent_allowed() encoded this rule, was tested, and was never called;
    # on_message engaged the agent on any owner mention in any guild. Asserted here
    # against on_message itself rather than the policy helper, because the helper
    # passing is precisely what the old test did while the live path did the opposite.
    check("agent was NOT invoked in Chillbar", len(agent_calls), 0)
    check("and nothing was posted into that server", len(sent), 0)

    print("\nAmbient chatter is read but never acted on")
    reset()
    await bot.on_message(_Message(STRANGER, "benham delete the channel", guild=chillbar))
    check("agent was NOT invoked", len(agent_calls), 0)
    check("nothing sent", len(sent), 0)
    reset()
    await bot.on_message(_Message(TYLER, "just talking, not addressing the bot",
                                  guild=testing))
    check("un-addressed owner message ignored too", len(agent_calls), 0)

    print("\nVoice: a stranger says the wake word")
    reset()
    spoke = []
    shortcuts = []
    # Registered with the REGISTRY, not just patched onto bot. Voice speech now
    # reaches the speaker through capabilities.run, so stubbing bot.speak_in_channel
    # alone would leave this measuring a function nothing calls - the same shape of
    # mistake the whole policy refactor exists to prevent.
    async def _record_speech(ch, t):
        spoke.append(t)
    bot.speak_in_channel = _record_speech
    capabilities.set_voice_speaker(_record_speech)
    bot.stop_listening = lambda g: shortcuts.append("stop") or asyncio.sleep(0)
    bot.reset_overrides = lambda: shortcuts.append("persona_reset") or True
    brain_calls = []
    bot.brain.respond = lambda conv: (brain_calls.append(conv) or ("hi", None))
    vc = _Channel(777, "voice")
    vc.guild = testing
    bot.client.channels[777] = vc

    await bot.handle_auto_reply(testing, vc, "stranger", STRANGER, "benham what's up")
    check("stranger got no spoken reply", len(spoke), 0)
    check("stranger did not reach the brain", len(brain_calls), 0)

    # The local shortcuts are free of API cost but not free of consequence, so the
    # gate has to sit above them too - not merely above the API call.
    await bot.handle_auto_reply(testing, vc, "stranger", STRANGER, "benham go to sleep")
    check("stranger cannot disconnect Benham", "stop" in shortcuts, False)
    await bot.handle_auto_reply(testing, vc, "stranger", STRANGER,
                                "benham reset your personality")
    check("stranger cannot reset the personality", "persona_reset" in shortcuts, False)

    print("\nVoice: Tyler says the wake word")
    reset()
    spoke.clear(); brain_calls.clear()
    # A shortcut phrase answers locally and never calls the API - that is the point
    # of the shortcuts, so assert the free path stays free.
    await bot.handle_auto_reply(testing, vc, "caz6666", TYLER, "benham you there")
    check("Tyler gets a local reply", len(spoke) >= 1, True)
    check("...with no API call", len(brain_calls), 0)

    spoke.clear(); brain_calls.clear()
    await bot.handle_auto_reply(testing, vc, "caz6666", TYLER,
                                "benham what do you make of this modpack")
    check("Tyler's real question DID reach the brain", len(brain_calls), 1)

    print("\nVoice: speech itself now passes the chokepoint")
    # OWNER_VOICE was a declared origin with no production caller until now: voice
    # was the one outward action that never reached policy. Its owner gate was
    # sound, but it was sound because handle_auto_reply remembered to check - the
    # arrangement this refactor exists to eliminate.
    spoke.clear()
    await bot.say(vc, "hello from Tyler", TYLER)
    check("Tyler's speech reaches the speaker", len(spoke), 1)
    spoke.clear()
    await bot.say(vc, "hello from a stranger", STRANGER)
    check("a stranger's speech is refused by policy itself", len(spoke), 0)
    check("speak_in_voice is a registered capability",
          "speak_in_voice" in capabilities.REGISTRY, True)
    check("...and is marked outward", capabilities.REGISTRY["speak_in_voice"].outward, True)

    print("\nVoice: the continuous-conversation window is keyed on user id")
    bot._convo_until.clear(); bot._convo_speaker.clear()
    bot._open_convo(TESTING, TYLER)
    check("Tyler's window is open", bot.convo_active(TESTING, TYLER), True)
    check("a stranger cannot ride Tyler's open window",
          bot.convo_active(TESTING, STRANGER), False)

    print("\nThe pc.. prefix - zero API calls is the entire point")
    reset()
    ran, agent_before = [], len(agent_calls)
    real = capabilities.REGISTRY["pc_task"].handler

    async def fake_pc(ctx, p):
        ran.append(p.get("task"))
        return {"status": "completed", "result": "31 .py files"}

    capabilities.REGISTRY["pc_task"].handler = fake_pc
    try:
        await bot.on_message(_Message(TYLER, "pc.. count the py files"))
        check("the task reached the PC session", ran, ["count the py files"])
        check("NO API call was made", len(agent_calls), agent_before)
        check("the session's own answer was posted", sent[-1], "31 .py files")

        reset(); ran.clear()
        await bot.on_message(_Message(TYLER, "PC.. list them"))
        check("prefix is case-insensitive", ran, ["list them"])

        reset(); ran.clear()
        await bot.on_message(_Message(TYLER, "pc.."))
        check("a bare prefix starts no session", ran, [])
        check("...and says what it wants", "needs something after it" in sent[-1], True)

        # In a guild it must NOT hijack the message - pc_task is DM-only, so the
        # prefix there would only produce a refusal posted into a server.
        reset(); ran.clear()
        await bot.on_message(_Message(TYLER, "pc.. do something",
                                      guild=testing, mentions=[benham]))
        check("ignored in a guild (falls through to the agent)", ran, [])
        check("...and the agent handled it instead", len(agent_calls), 1)

        reset(); ran.clear()
        await bot.on_message(_Message(TYLER, "pc is short for personal computer"))
        check("'pc ...' without the dots is NOT the prefix", ran, [])
    finally:
        capabilities.REGISTRY["pc_task"].handler = real


    print("\nDefence in depth — the capability refuses a stranger on its own")
    # The whole point of stage 2. on_message would never build a stranger context,
    # so this reaches past it and asks capabilities.run directly - simulating a
    # future entry point that forgets the early check. Before stage 2 this ran.
    import policy
    stranger_ctx = policy.CallContext.owner_dm(STRANGER, 555)
    refused = []
    for name, params in (("send_message", {"channel_id": 555, "content": "x"}),
                         ("read_channel", {"channel_id": 555}),
                         ("pc_task", {"task": "anything"})):
        try:
            await capabilities.run(bot.client, lambda *_: None, name, params,
                                   actor_id=STRANGER, call_ctx=stranger_ctx)
            refused.append(f"{name}=RAN")
        except capabilities.ActionError as e:
            refused.append(f"{name}=refused" if "not my owner" in str(e)
                           else f"{name}=refused({str(e)[:30]})")
    check("every capability refuses a stranger context",
          all(r.endswith("=refused") for r in refused), True)

    print("\nWhat a stranger could reach IF the gate were bypassed")
    tiers = {}
    for name, act in capabilities.REGISTRY.items():
        tiers.setdefault(identity.TIER_NAMES[act.tier], []).append(name)
    print(f"  tier 3 (needs Tyler's confirm even then): {len(tiers.get('destructive', []))}")
    print(f"  tiers 0-2 (gated by taint/outward, not by tier alone): "
          f"{len(tiers.get('read', [])) + len(tiers.get('speak', [])) + len(tiers.get('manage', []))}")
    print(f"  but all of them now refuse a non-owner context: {refused}")


asyncio.run(main())
print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
