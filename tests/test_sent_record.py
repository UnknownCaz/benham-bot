"""
test_sent_record.py - what_i_did sees every message that left (issue 10).

2026-10-19: HERMES_AGENT @'d Benham in a thread, Caz approved the reply, the
reply landed - and what_i_did showed nothing, because only capabilities.run
writes the "action" lines it read, and the approved reply to a mention (like
the closing message, an owner-mention reply, a nudge) went out through
bot.reply_in -> channel.send, which never passed run(). Benham answers "did you
do X?" from that record, so a send it cannot see is a send it will deny.

The fix records at the wire (core/wire.py -> core/outwardlog.py) and teaches
selfrecord to read that record. Pinned here:

  * a reply sent the way the friend path sends it (bot.reply_in on a real
    discord Messageable, the HTTP layer stubbed) shows up through the REAL
    what_i_did capability, with its words and where it went;
  * a send made INSIDE a capability is not shown twice - its own action line
    already says it;
  * a refused send shows as BLOCKED with its rule;
  * the record counts toward `covers`, so "covered" stays honest.

    python test_sent_record.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import sys
import tempfile

import discord

from benham import paths
from benham.core import outwardlog, policy, selfrecord, wire

paths.STATE_DIR = tempfile.mkdtemp(prefix="benham-sent-record-")
paths.LOG_DIR = tempfile.mkdtemp(prefix="benham-sent-record-logs-")

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


SENT = []


async def _stub_send(self, content=None, **kw):
    SENT.append(content)
    return type("Msg", (), {"id": len(SENT)})()


discord.abc.Messageable.send = _stub_send
wire._ORIG.clear()

from benham import bot  # noqa: E402 - imported after the stub so its install() wraps it
from benham.core import capabilities  # noqa: E402


class Thread(discord.abc.Messageable):
    """Where Hermes's thread reply went: a guild channel, not a DM."""
    id = 600000000000000401
    name = "Greet a user by ID"
    guild = None

    async def _get_channel(self):
        return self


async def what_i_did(**p):
    res, _ = await capabilities.run(None, lambda *_: None, "what_i_did", p,
                                    call_ctx=policy.CallContext.local())
    return res


async def main():
    section("an approved reply to a bot's mention is on the record")
    SENT.clear()
    await bot.reply_in(Thread(), "<@777000777000777001> hi Hermes, Benham here.")
    check("the reply went out", SENT, ["<@777000777000777001> hi Hermes, Benham here."])
    r = await what_i_did(limit=10)
    sent = [e for e in r["entries"] if e["kind"] == "sent"]
    check("what_i_did shows one sent entry", len(sent), 1)
    check("...naming where it went and what it said",
          bool(sent) and "Greet a user by ID" in sent[0]["detail"]
          and "hi Hermes, Benham here." in sent[0]["detail"], True)
    check("...so the log covers the moment it happened", r["covers"] is not None, True)

    section("a closing message is a second entry, not folded into the first")
    await bot.reply_in(Thread(), "<@777000777000777001> that's my one reply - later!")
    r = await what_i_did(limit=10)
    check("two sent entries now", len([e for e in r["entries"] if e["kind"] == "sent"]), 2)
    check("newest first", "later!" in r["entries"][0]["detail"], True)

    section("a send inside a capability is not shown twice")
    token = outwardlog.current_action.set("send_message")
    try:
        await Thread().send("sent by a capability")
    finally:
        outwardlog.current_action.reset(token)
    r = await what_i_did(limit=10)
    check("it is in the outward record", outwardlog.entries()[-1]["via"], "send_message")
    check("...but what_i_did leaves it to the capability's own action line",
          any("sent by a capability" in e["detail"] for e in r["entries"]), False)

    section("a refused send is on the record as BLOCKED")
    try:
        await Thread().send("Zebulonbot says hi")
    except policy.OutwardBlocked:
        pass
    r = await what_i_did(limit=10)
    check("BLOCKED entry, with the rule", (r["entries"][0]["kind"],
          "privacy_survivor" in r["entries"][0]["detail"]), ("BLOCKED", True))
    check("...and nothing reached discord", SENT[-1], "sent by a capability")

    section("the friend reply path itself logs a line for a human reader")
    lines = []
    real_log = bot.log
    bot.log = lines.append
    try:
        real_turn = bot.agent.friend_turn

        async def fake_turn(log, content, **kw):
            return "hello friend"
        bot.agent.friend_turn = fake_turn

        async def fake_read(channel, **kw):
            return selfrecord_stub
        real_read = bot.channelread.read
        bot.channelread.read = fake_read
        try:
            ch = Thread()
            ch.guild = type("G", (), {"name": "RadioTower", "id": 1})()
            ch.typing = lambda: _NoTyping()
            await bot.answer_friends(ch, {5: "friend"}, closing=True)
        finally:
            bot.channelread.read = real_read
            bot.agent.friend_turn = real_turn
    finally:
        bot.log = real_log
    check("a 'friend reply sent ... (closing message)' line",
          any(l.startswith("friend reply sent in #") and "(closing message)" in l
              for l in lines), True)


class _NoTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Read:
    people = {}
    lines = []
    images = []


selfrecord_stub = _Read()

_orig_turn_text = bot.channelread.turn_text
_orig_image_parts = bot.channelread.image_parts
_orig_apply = bot.channelread.apply_pings
bot.channelread.turn_text = lambda *a, **k: "room"
bot.channelread.image_parts = lambda *a, **k: []
bot.channelread.apply_pings = lambda reply, people: (reply, {})

asyncio.run(main())

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
