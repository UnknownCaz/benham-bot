"""
test_memory.py - what Benham remembers of a conversation is what actually happened.

Written after a twelve-day corruption. `f06b79b` bound the model's own output to
`text` inside respond()'s loop - the same name as the PARAMETER holding what the
owner said - and `_remember()` reads that parameter 120 lines below to store the
user turn. So every turn where the model produced text was written to disk as
though Tyler had said Benham's reply.

It is not a cosmetic bug. History is what the next turn is reasoned from, so
Benham read its own replies back as Tyler's messages and answered them. On
2026-08-15 it told him it had never run a pc_task while he was looking at the
eight approval prompts it had just sent him - and argued the point twice, because
from where it sat the record really did say that.

Nothing in the suite caught it. test_injection.py drives the same code path and
was corrupting `test:taintorder` on every run while passing, because it asserted
on which tools ran and never looked at what got stored. So these checks assert on
the stored bytes, and the last one is shape-only: it fails on ANY user turn equal
to the assistant turn beside it, whatever future edit causes it.

Fully offline - the Anthropic client is a scripted fake, no API calls, no cost.

    python test_memory.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# These checks assert on what reached DISK, which is the whole point of the file -
# so it matters a great deal which disk. Without this the store resolved to the
# live state/ of whatever checkout ran the suite, and in the main repo that is the
# directory the running bot has open. The keys were cleaned up on the way out; the
# read-modify-write against the bot's own memory file was not.
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import sys

from benham.core import agent
from benham.core import capabilities
from benham.core import confirm
from benham.core import policy

TYLER = 273967061619965952
TESTING = 736988645562646619
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


class _StubChannel:
    def __init__(self, cid):
        self.id = cid
        self.name = "asd"
        self.guild = _Block(id=TESTING, name="Testing Server")

    async def history(self, limit=50, **kw):
        for _ in ():
            yield _


class _StubClient:
    user = _Block(id=752313060970201218)

    def get_channel(self, cid):
        return _StubChannel(int(cid))

    def get_guild(self, gid):
        return _Block(id=int(gid), name="Testing Server")


# --------------------------------------------------------------------------- harness

def _stored(key):
    """The turns as they exist on disk, not as the module cached them."""
    # No cache to invalidate any more - turnmemory reads the file every time,
    # which is the change that removed the "stop the bot before repairing it"
    # hazard. Kept as its own helper because the NAME is the point: these checks
    # assert on what reached disk, not on what a getter remembers.
    return agent._history(key)


async def _one_turn(key, said, script):
    agent._client = _FakeAnthropic(script)
    agent._last_call.clear()
    confirm.cancel()
    agent.forget(key)
    reply, _ = await agent.respond(
        _StubClient(), lambda *_: None, said,
        actor_id=TYLER, actor_name="caz6666", channel_id=TESTING_CHAN,
        guild_id=None, where="a DM", conversation_key=key,
        call_ctx=policy.CallContext.owner_dm(TYLER, TESTING_CHAN))
    return reply


async def main():
    if not agent.ENABLED:
        print("agent disabled (no ANTHROPIC_API_KEY) - these checks need the module "
              "live, but make no API calls. Set any non-empty key to run them.")
        return 0

    real_pc = capabilities.REGISTRY["pc_task"].handler

    async def fake_pc(ctx, p):
        return {"status": "completed", "result": "a.txt, b.txt"}

    capabilities.REGISTRY["pc_task"].handler = fake_pc
    try:
        section("A plain turn stores what the owner said, not what Benham replied")
        # The exact shape that was corrupting: one round, model returns text, done.
        await _one_turn("test:memory_plain", "what files are in your folder?",
                        [_Resp([_Block(type="text", text="a.txt and b.txt")], "end_turn")])
        turns = _stored("test:memory_plain")
        check("two turns stored", len(turns), 2)
        check("user turn is the OWNER's message",
              turns[0]["content"], "what files are in your folder?")
        check("assistant turn is Benham's reply",
              turns[1]["content"], "a.txt and b.txt")

        section("Still true after a tool round - the loop must not clobber the parameter")
        # The regression lived in the loop body, so a turn that goes AROUND the loop
        # is the case that actually exercises it.
        await _one_turn("test:memory_tools", "list your folder for me", [
            _Resp([_Block(type="tool_use", id="p1", name="pc_task",
                          input={"task": "list the working directory"})], "tool_use"),
            _Resp([_Block(type="text", text="a.txt and b.txt")], "end_turn"),
        ])
        turns = _stored("test:memory_tools")
        check("user turn survives a tool round",
              turns[0]["content"], "list your folder for me")
        check("assistant turn is the reply", turns[1]["content"], "a.txt and b.txt")

        section("A multi-round turn stores the owner's message, not the LAST text block")
        # The variant that got past the first repair. reply is "\n\n".join(parts),
        # so with two text-producing rounds the clobbered `text` held only the tail
        # - user != assistant, and an equality check sails right past it.
        await _one_turn("test:memory_multi", "check my downloads folder", [
            _Resp([_Block(type="text", text="Sure - checking now."),
                   _Block(type="tool_use", id="p1", name="pc_task",
                          input={"task": "list downloads"})], "tool_use"),
            _Resp([_Block(type="text", text="Two files in there.")], "end_turn"),
        ])
        turns = _stored("test:memory_multi")
        check("user turn survives MULTIPLE text rounds",
              turns[0]["content"], "check my downloads folder")
        check("assistant turn joins every part",
              turns[1]["content"], "Sure - checking now.\n\nTwo files in there.")
        check("and the pair does not read as an echo",
              agent.is_echo_pair(turns[0], turns[1]), False)

        section("No stored conversation echoes Benham's reply back as the owner")
        # Shape-only, and deliberately over the WHOLE file including the live DM
        # thread: this is the assertion that would have caught f06b79b without
        # anyone knowing what f06b79b was going to be. It asks agent.is_echo_pair
        # rather than testing equality here, so widening the definition of damage
        # can never again leave the repair script and the guard disagreeing.
        mem = agent._store._read()
        echoed = [f"{key}[{i // 2}]"
                  for key, turns in mem.items()
                  for i in range(0, len(turns) - 1, 2)
                  if agent.is_echo_pair(turns[i], turns[i + 1])]
        check("no conversation echoes Benham's reply back as the owner's message",
              echoed, [])
    finally:
        capabilities.REGISTRY["pc_task"].handler = real_pc
        agent._client = None
        for k in ("test:memory_plain", "test:memory_tools", "test:memory_multi"):
            agent.forget(k)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
