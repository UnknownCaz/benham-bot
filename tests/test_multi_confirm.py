"""
test_multi_confirm.py - several proposals from one turn each stay pending on
their own, and nothing is overwritten in silence.

THE EVENING THIS EXISTS FOR. 2026-10-11 22:35Z (18:35 local). From one DM,
Benham proposed three `create_role` actions at once - Transmitter, Operator,
Listener - each under the `always_confirm` rule. confirm.park() cleared the
store on every call, so only the LAST one (Listener) was still parked when Caz
tapped Approve. Transmitter and Operator were never created and nothing told
him; the hub built them afterwards by hand.

What is pinned here:
  1. the store keeps every parked item, each under its own token;
  2. the agent loop hands ALL of a turn's proposals back, not the last;
  3. bot.on_message sends one prompt with its own buttons per proposal, and
     each is approved or refused on its own;
  4. the 2026-08-26 rules survive several live items: a live token belongs to
     its own confirmation only; a bare yes/no while two things are live answers
     NEITHER, out loud, naming both; a near-miss token gets a spoken refusal;
     a tier-3 item still needs its token (INTENT 37).

Fully offline - the Anthropic client and Discord are fakes, nothing is sent.

    python test_multi_confirm.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import sys
from datetime import datetime, timezone

from benham import bot
from benham.core import agent
from benham.core import capabilities
from benham.core import confirm
from benham.core import policy

TYLER = 273967061619965952
TESTING_CHAN = 753732380921167902
BENHAM_ID = 752313060970201218

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def _role(name):
    return confirm.park("create_role", {"name": name},
                        {"summary": f"Create role {name}", "name": name},
                        TYLER, "dm")


# --------------------------------------------------------------- Discord stubs

sent = []


class _User:
    def __init__(self, uid, name):
        self.id = uid
        self.name = name

    def __str__(self):
        return self.name


class _Sent:
    def __init__(self, content):
        self.id = len(sent)
        self.content = content
        self.jump_url = ""

    async def edit(self, **kw):
        return None


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

    async def send(self, content=None, **kw):
        sent.append(content or "")
        return _Sent(content)

    def __str__(self):
        return self.name


class _Message:
    def __init__(self, content):
        self.author = _User(TYLER, "caz6666")
        self.content = content
        self.guild = None
        self.channel = _Channel(TESTING_CHAN)
        self.mentions = []
        self.created_at = datetime.now(timezone.utc)
        self.id = 42
        self.attachments = []
        self.reference = None
        self.embeds = []
        self.stickers = []
        self.message_snapshots = []

    async def add_reaction(self, emoji):
        return None


class _StubClient:
    user = _User(BENHAM_ID, "Benham#2721")

    def get_channel(self, cid):
        return None

    def get_guild(self, gid):
        return None


# ---------------------------------------------------------- Anthropic stubs

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Block(input_tokens=0, output_tokens=0)


class _Messages:
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def create(self, **kw):
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return r


class _FakeAnthropic:
    def __init__(self, script):
        self.messages = _Messages(script)


# ------------------------------------------------------------------- tests

def store_checks():
    section("1. the store keeps every proposal, each under its own token")
    confirm.cancel()
    a, b, c = _role("Transmitter"), _role("Operator"), _role("Listener")
    check("three parked, three live", len(confirm.live()), 3)
    check("Transmitter is still parked (it used to be wiped)",
          confirm.get(a.token) is not None, True)
    check("Operator is still parked", confirm.get(b.token) is not None, True)
    check("Listener is parked", confirm.get(c.token) is not None, True)
    check("three different tokens", len({a.token, b.token, c.token}), 3)
    check("live() is oldest first",
          [p.params["name"] for p in confirm.live()],
          ["Transmitter", "Operator", "Listener"])
    check("consuming one leaves the others", (confirm.consume(b.token).params["name"],
                                              len(confirm.live())), ("Operator", 2))
    check("cancelling one by token leaves the other",
          ([p.params["name"] for p in confirm.cancel(a.token)], len(confirm.live())),
          (["Transmitter"], 1))

    section("1b. proposing the SAME thing again replaces its twin, and says which")
    confirm.cancel()
    first = _role("Listener")
    again = _role("Listener")
    check("one live, not two identical ones that could both be approved",
          len(confirm.live()), 1)
    check("the fresh one is the live one", confirm.get(again.token) is again, True)
    check("and it names the one it replaced", again.replaces, first.token)
    other = _role("Operator")
    check("a different proposal replaces nothing", other.replaces, None)
    check("so both are live", len(confirm.live()), 2)
    confirm.cancel()


async def agent_checks():
    section("2. one agent round proposing three roles hands back all three")
    confirm.cancel()
    calls = [_Block(type="tool_use", id=f"toolu_{n}", name="create_role",
                    input={"name": n}) for n in ("Transmitter", "Operator", "Listener")]
    agent._client = _FakeAnthropic([
        _Resp([_Block(type="text", text="Three roles coming up.")] + calls, "tool_use"),
        _Resp([_Block(type="text", text="Here are the three previews.")], "end_turn"),
    ])
    agent._last_call.clear()
    key = "test:multi_confirm"
    agent.forget(key)

    real_run = capabilities.run

    async def _always_preview(client, log, name, params, actor_id=None, force=False,
                              call_ctx=None):
        return None, {"summary": f"Create role {params.get('name')}",
                      "name": params.get("name")}

    capabilities.run = _always_preview
    try:
        reply, parked = await agent.respond(
            _StubClient(), lambda *_: None, "make the three roles",
            actor_id=TYLER, actor_name="caz6666", channel_id=TESTING_CHAN,
            guild_id=None, where="a DM", conversation_key=key,
            call_ctx=policy.CallContext.owner_dm(TYLER, TESTING_CHAN))
    finally:
        capabilities.run = real_run
        agent.forget(key)

    names = [p.params["name"] for p in (parked or [])] if isinstance(parked, list) else parked
    check("all three proposals come back, in order", names,
          ["Transmitter", "Operator", "Listener"])
    check("all three are live in the store", len(confirm.live()), 3)

    static, volatile = agent._system_blocks("a DM", "caz6666")
    check("the next turn's prompt names every parked item",
          all(n in volatile for n in ("Transmitter", "Operator", "Listener")), True)
    check("and no longer tells the model a new call REPLACES what is parked",
          "replaces this one" in volatile, False)
    confirm.cancel()

    section("2b. nothing parked: respond hands back an empty list")
    agent._client = _FakeAnthropic([_Resp([_Block(type="text", text="hey")], "end_turn")])
    agent._last_call.clear()
    agent.forget(key)
    reply, parked = await agent.respond(
        _StubClient(), lambda *_: None, "hi",
        actor_id=TYLER, actor_name="caz6666", channel_id=TESTING_CHAN,
        guild_id=None, where="a DM", conversation_key=key,
        call_ctx=policy.CallContext.owner_dm(TYLER, TESTING_CHAN))
    agent.forget(key)
    check("no proposals -> []", parked, [])


async def bot_checks():
    fired = []
    replies = {"next": None}

    async def _fake_respond(*args, **kwargs):
        return replies["next"]

    async def _spy_fire(pending, channel):
        fired.append(pending.params.get("name") or pending.action)

    real_fire, real_respond = bot.fire_confirmed, bot.agent.respond
    bot.client = _StubClient()
    bot.record_message = lambda m: {"is_self": False, "channel": "dm",
                                    "author": str(m.author), "content": m.content}
    bot.log = lambda *a, **k: None
    bot.agent.ENABLED = True
    bot.agent.respond = _fake_respond
    bot.fire_confirmed = _spy_fire

    async def say(text):
        sent.clear()
        await bot.on_message(_Message(text))
        return "\n".join(sent)

    try:
        section("3. bot: three proposals -> three prompts, each with its own buttons")
        confirm.cancel()
        bot._views.clear()
        a, b, c = _role("Transmitter"), _role("Operator"), _role("Listener")
        replies["next"] = ("Three roles to approve.", [a, b, c])
        out = await say("make the three roles")
        check("one prompt per proposal (plus the reply)",
              sum("Confirm required" in s for s in sent), 3)
        check("every token was shown to him",
              all(p.token in out for p in (a, b, c)), True)
        check("three button sets registered",
              sorted(k[1] for k in bot._views if k[0] == "confirm"),
              sorted([a.token, b.token, c.token]))

        section("3b. Approve on the first fires the first ONLY")
        fired.clear()
        await bot._views[("confirm", a.token)].on_decide(True)
        check("Transmitter fired", fired, ["Transmitter"])
        check("Operator and Listener still waiting",
              sorted(p.params["name"] for p in confirm.live()), ["Listener", "Operator"])

        section("3c. a bare 'yes' with two live answers NEITHER, out loud, naming both")
        fired.clear()
        out = await say("yes")
        check("nothing fired", fired, [])
        check("both still live", len(confirm.live()), 2)
        check("he is told, naming both tokens", (b.token in out, c.token in out),
              (True, True))

        section("3d. 'yes <token>' fires that one and only that one")
        out = await say(f"yes {c.token}")
        check("Listener fired", fired, ["Listener"])
        check("Operator untouched", confirm.get(b.token) is not None, True)

        section("3e. one live again: a bare yes works exactly as before (tier 2)")
        fired.clear()
        await say("yes")
        check("Operator fired", fired, ["Operator"])
        check("nothing left", confirm.live(), [])

        section("4. 'no' with several live: bare answers neither, a token cancels one")
        fired.clear()
        bot._views.clear()
        a, b = _role("Transmitter"), _role("Operator")
        out = await say("no")
        check("a bare no cancelled nothing", len(confirm.live()), 2)
        check("and said so, naming both", (a.token in out, b.token in out), (True, True))
        out = await say(f"no {a.token}")
        check("'no <token>' cancelled that one", confirm.get(a.token), None)
        check("and left the other", confirm.get(b.token) is not None, True)
        check("nothing fired", fired, [])
        confirm.cancel()

        section("5. tier 3 among several (INTENT 37 + the 08-26 rules)")
        fired.clear()
        role = _role("Operator")
        purge = confirm.park("purge_messages", {"channel_id": 1},
                             {"channel": "general", "count": 42, "summary": "delete 42"},
                             TYLER, "dm")
        out = await say("yes")
        check("bare yes: nothing fired", fired, [])
        check("bare yes: spoken, naming both", (role.token in out, purge.token in out),
              (True, True))
        near = purge.token[:-1] + ("0" if purge.token[-1] != "0" else "1")
        out = await say(f"yes {near}x")
        check("near-miss token: nothing fired", fired, [])
        check("near-miss token: a spoken refusal, not silence", bool(out.strip()), True)
        out = await say(f"yes {role.token} {purge.token}")
        check("two tokens in one reply: nothing fired", fired, [])
        check("two tokens in one reply: spoken", bool(out.strip()), True)
        out = await say(f"yes {purge.token}")
        check("the purge's own token fires the purge only", fired, ["purge_messages"])
        check("the role is still waiting", confirm.get(role.token) is not None, True)
        confirm.cancel()
    finally:
        bot.fire_confirmed = real_fire
        bot.agent.respond = real_respond
        bot._views.clear()
        confirm.cancel()


async def main():
    if not agent.ENABLED:
        print("agent disabled - set any non-empty ANTHROPIC_API_KEY; no calls are made")
        return 1
    store_checks()
    await agent_checks()
    await bot_checks()
    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
