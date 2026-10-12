"""
test_loop_offload.py - a model call never blocks the Discord event loop.

THE MORNING THIS EXISTS FOR. 2026-10-12 09:28:34Z, #control. Caz and Benham were
going back and forth over a silent retry, and discord.py logged that the
gateway heartbeat had been blocked for more than 10 seconds: agent.respond
called the Anthropic API synchronously, inside the event loop, so while one
round was thinking nothing else in the bot could run - not the heartbeat, not
another message, not a button tap.

Every API call reached from an `async def` in agent.py now waits on a worker
thread (asyncio.to_thread). Pinned here by measurement, not by reading the
source: a fake client whose create() sleeps the way a slow round does, and a
ticker on the same loop that must keep ticking the whole time. On the old
code the ticker stalls for the full sleep of every call.

Also pinned: _create's own behaviour is unchanged by the move - the max_tokens
retry still asks twice, with the two caps in order, and logs both usage lines.

Fully offline - no API calls, no cost.

    python test_loop_offload.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import sys
import time

from benham.core import agent
from benham.core import confirm
from benham.core import policy

TYLER = 273967061619965952
TESTING_CHAN = 753732380921167902
SLOW = 0.6       # seconds one fake API call takes
TICK = 0.02      # how often the ticker wants to run
STALL = 0.3      # a gap this long means the loop was held

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def _fmt(gap):
    return "none - the ticker never ran" if gap is None else f"{gap:.3f}s"


def section(title):
    print(f"\n{title}")


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Resp:
    def __init__(self, content, stop_reason, out=0):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Block(input_tokens=0, output_tokens=out)


class _SlowMessages:
    """create() blocks its thread for SLOW seconds, like a real round does."""

    def __init__(self, script):
        self.script = script
        self.sent = []

    def create(self, **kw):
        time.sleep(SLOW)
        self.sent.append(kw)
        return self.script[min(len(self.sent) - 1, len(self.script) - 1)]


class _SlowAnthropic:
    def __init__(self, script):
        self.messages = _SlowMessages(script)


class _StubClient:
    user = _Block(id=752313060970201218)

    def get_channel(self, cid):
        return None

    def get_guild(self, gid):
        return None


async def _ticking(work):
    """Run `work` while a ticker on the same loop records its longest gap."""
    gaps, stop = [], asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(TICK)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(ticker())
    try:
        result = await work
    finally:
        stop.set()
        await t
    return result, max(gaps) if gaps else None, len(gaps)


async def main():
    if not agent.ENABLED:
        print("agent disabled - set any non-empty ANTHROPIC_API_KEY; no calls are made")
        return 1

    section("1. respond(): the loop keeps beating through a slow round")
    agent._client = _SlowAnthropic([_Resp([_Block(type="text", text="hi")], "end_turn")])
    agent._last_call.clear()
    agent.forget("test:offload")
    confirm.cancel()
    try:
        (reply, _parked), worst, ticks = await _ticking(agent.respond(
            _StubClient(), lambda *_: None, "hey",
            actor_id=TYLER, actor_name="caz6666", channel_id=TESTING_CHAN,
            guild_id=None, where="a DM", conversation_key="test:offload",
            call_ctx=policy.CallContext.owner_dm(TYLER, TESTING_CHAN)))
    finally:
        agent._client = None
        agent.forget("test:offload")
    check("the turn still answers", reply, "hi")
    check(f"the loop was never held for {STALL}s or more (worst gap "
          f"{_fmt(worst)} over {ticks} ticks)", worst is not None and worst < STALL, True)
    check("the ticker actually ran during the call", ticks >= int(SLOW / TICK / 2), True)

    section("2. the max_tokens retry is unchanged by the move")
    cut = _Resp([_Block(type="text", text="half")], "max_tokens", out=agent.MAX_TOKENS)
    done = _Resp([_Block(type="text", text="whole")], "end_turn")
    api = _SlowAnthropic([cut, done])
    logged = []
    (resp, discarded), worst, _ = await _ticking(
        agent._acreate(api, logged.append, "round 1", system=[], messages=[]))
    check("asked twice", len(api.messages.sent), 2)
    check("first at MAX_TOKENS, then at MAX_TOKENS_RETRY",
          [k["max_tokens"] for k in api.messages.sent],
          [agent.MAX_TOKENS, agent.MAX_TOKENS_RETRY])
    check("same model both times", {k["model"] for k in api.messages.sent}, {agent.MODEL})
    check("the cut response is handed back as discarded", discarded, [cut])
    check("the retry's response is the one returned", resp, done)
    check("the retry is logged by name",
          any("hit max_tokens" in str(x) for x in logged), True)
    check(f"two slow calls in a row never held the loop (worst {_fmt(worst)})",
          worst is not None and worst < STALL, True)

    section("3. friend_turn(): the friends' reply is off the loop too")
    agent._client = _SlowAnthropic([_Resp([_Block(type="text", text="yo")], "end_turn")])
    try:
        said, worst, _ = await _ticking(agent.friend_turn(
            lambda *_: None, [{"type": "text", "text": "hi benham"}], "#asd",
            TESTING_CHAN, 1, "doom", closing=False))
        sent = agent._client.messages.sent
    finally:
        agent._client = None
    check("the friend reply comes back", said, "yo")
    check("still tool_choice none (no forced tool, no thinking param added)",
          (sent[0].get("tool_choice"), "thinking" in sent[0]), ({"type": "none"}, False))
    check(f"the loop was never held (worst {_fmt(worst)})", worst is not None and worst < STALL, True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
