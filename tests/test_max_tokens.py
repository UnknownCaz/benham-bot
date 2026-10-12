"""
test_max_tokens.py - a turn that hits max_tokens is never dropped in silence.

THE AFTERNOON THIS EXISTS FOR. 2026-10-11 13:02-13:03Z (09:02 local). Caz DM'd
Benham a whole server build and asked for the plan first. Round 1 read the
server (guild_info, list_roles, list_channels, find_user) and said "Let me pull
the current state of that server before I draft the plan." Round 2 came back
`out=2048` - exactly the agent's max_tokens - and NO text: the whole budget had
gone into tool calls that were cut off mid-input. The loop read stop_reason
"max_tokens" as "finished", kept the round-1 sentence as the whole reply, and
said nothing else. He got "let me pull the current state" and then silence.

Two halves, both pinned here:
  - a cut-off round is RETRIED once at a larger cap (the API's own advice for
    a truncated tool call: never run it, re-ask with more room), so the common
    case simply works;
  - if the retry is cut off too, the reply keeps what was written and says so
    out loud, and a tool call that was cut mid-input is never run.

Fully offline - the Anthropic client is a scripted fake, no API calls, no cost.

    python test_max_tokens.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import sys

from benham.core import agent
from benham.core import confirm
from benham.core import policy

TYLER = 273967061619965952
TESTING_CHAN = 753732380921167902

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


# --------------------------------------------------------------------------- stubs

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


class _Messages:
    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.sent = []

    def create(self, **kw):
        self.sent.append(kw)
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return r


class _FakeAnthropic:
    def __init__(self, script):
        self.messages = _Messages(script)


class _StubClient:
    user = _Block(id=752313060970201218)

    def get_channel(self, cid):
        return None

    def get_guild(self, gid):
        return None


def _text(t):
    return _Block(type="text", text=t)


def _cut_tool(name="create_role", tid="toolu_cut"):
    """A tool_use block as the API returns it when max_tokens lands mid-input:
    present, but its input is whatever had been written - here, half of it."""
    return _Block(type="tool_use", id=tid, name=name, input={"name": "Transm"})


def _cut(*content):
    return _Resp(list(content), "max_tokens", out=agent.MAX_TOKENS)


async def _turn(script, said="Build my server. Show me the plan before you build."):
    agent._client = _FakeAnthropic(script)
    agent._last_call.clear()
    key = "test:max_tokens"
    agent.forget(key)
    confirm.cancel()
    logged = []
    reply, _parked = await agent.respond(
        _StubClient(), logged.append, said,
        actor_id=TYLER, actor_name="caz6666", channel_id=TESTING_CHAN,
        guild_id=None, where="a DM", conversation_key=key,
        call_ctx=policy.CallContext.owner_dm(TYLER, TESTING_CHAN))
    sent = agent._client.messages.sent
    agent.forget(key)
    return reply, logged, sent


async def main():
    if not agent.ENABLED:
        print("agent disabled - set any non-empty ANTHROPIC_API_KEY; no calls are made")
        return 1

    # ------------------------------------------------------------------ 1
    section("1. the 13:03Z shape: cut off inside a tool call, retry ALSO cut off")
    reply, logged, sent = await _turn([_cut(_cut_tool()), _cut(_cut_tool())])
    check("the turn says SOMETHING (it used to return nothing at all)",
          bool(reply), True)
    check("the reply says it was cut off",
          "cut off" in (reply or "").lower(), True)
    check("the reply says the half-written action was NOT run",
          "did not run" in (reply or "").lower(), True)
    check("the truncated create_role was never run - nothing parked",
          confirm.current(), None)
    check("one retry was made (two calls, not one, not a loop)", len(sent), 2)
    check("the retry asked for more room than the first call",
          sent[1]["max_tokens"] > sent[0]["max_tokens"], True)
    check("the first call used the configured cap",
          sent[0]["max_tokens"], agent.MAX_TOKENS)
    check("the cut-off is in the log, not only in the reply",
          any("max_tokens" in line for line in logged), True)

    # ------------------------------------------------------------------ 2
    section("2. the retry has room: the plan arrives, and no cut-off line")
    plan = "Plan:\n1. Transmitter\n2. Operator\n3. Listener\nGo ahead?"
    reply, logged, sent = await _turn([_cut(_cut_tool()),
                                       _Resp([_text(plan)], "end_turn")])
    check("the full reply is what was sent", reply, plan)
    check("no cut-off line when nothing was cut", "cut off" in reply.lower(), False)
    check("two calls: the cut one and its retry", len(sent), 2)
    check("the retry was logged", any("retry" in line.lower() for line in logged), True)

    # ------------------------------------------------------------------ 3
    section("3. cut off in the middle of TEXT, twice: keep it, and say so")
    half = "Plan:\n1. Transmitter (agents)\n2. Operator (trusted fr"
    reply, logged, sent = await _turn([_cut(_text(half)), _cut(_text(half))])
    check("what WAS written is kept", (reply or "").startswith(half), True)
    check("and marked as cut off", "cut off" in (reply or "").lower(), True)
    check("no action claim when no tool call was cut",
          "did not run" in (reply or "").lower(), False)

    # ------------------------------------------------------------------ 4
    section("4. a cut-off round mid-turn: earlier rounds' words survive too")
    reply, logged, sent = await _turn([
        _Resp([_text("Let me pull the current state of that server first."),
               _Block(type="tool_use", id="toolu_r1", name="no_such_capability",
                      input={})], "tool_use"),
        _cut(_cut_tool()), _cut(_cut_tool())])
    check("round 1's sentence is still in the reply",
          "Let me pull the current state" in (reply or ""), True)
    check("followed by the cut-off notice", "cut off" in (reply or "").lower(), True)
    check("three calls: round 1, round 2, round 2's retry", len(sent), 3)

    # ------------------------------------------------------------------ 5
    section("5. an ordinary turn is untouched: one call, no retry, no notice")
    reply, logged, sent = await _turn([_Resp([_text("hey")], "end_turn")])
    check("reply unchanged", reply, "hey")
    check("exactly one call", len(sent), 1)

    # ------------------------------------------------------------------ 6
    section("6. rehearse runs the same rule")
    agent._client = _FakeAnthropic([_cut(_cut_tool()), _cut(_cut_tool())])
    r_reply, _usage, _looked, would = await agent.rehearse(
        _StubClient(), lambda *_: None, "plan the server", "a DM",
        "test:max_tokens:rehearse",
        policy.CallContext.owner_dm(TYLER, TESTING_CHAN), actor_id=TYLER)
    check("a rehearsal that is cut off says so", "cut off" in (r_reply or "").lower(),
          True)
    check("and the cut tool call is not reported as something it would do",
          would, [])

    confirm.cancel()
    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
