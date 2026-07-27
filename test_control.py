"""
test_control.py - checks on the gates, not on Discord.

Everything here runs offline with a stub client. The point is the safety layer:
who Benham obeys, where destructive actions may run, and what counts as a yes.
Those three are the parts where a silent regression is expensive, and they are
also the parts that are awkward to exercise by hand against a live bot - you
cannot casually test "does it refuse to purge Chillbar" in Chillbar.

    python test_control.py
"""

import asyncio
import os
import sys

import discord

import capabilities
import confirm
import identity

TESTING = 736988645562646619
CHILLBAR = 1491485711076167711
TYLER = 273967061619965952
STRANGER = 999000999000999000

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------- owner gate
section("Owner gate — Benham obeys exactly one person")
check("Tyler is owner", identity.is_owner(TYLER), True)
check("stranger is not", identity.is_owner(STRANGER), False)
check("string id still works", identity.is_owner(str(TYLER)), True)
check("None is not an owner", identity.is_owner(None), False)
check("garbage is not an owner", identity.is_owner("nope"), False)

section("Agent engagement")
check("owner DM engages", identity.agent_allowed(None, TYLER, True), True)
check("stranger DM does not", identity.agent_allowed(None, STRANGER, True), False)
check("owner in Testing engages", identity.agent_allowed(TESTING, TYLER, False), True)
check("owner in Chillbar does not", identity.agent_allowed(CHILLBAR, TYLER, False), False)
check("stranger in Testing does not", identity.agent_allowed(TESTING, STRANGER, False), False)

# ------------------------------------------------------- destructive allowlist
section("Destructive allowlist — the structural wall")
check("Testing allowed", identity.destructive_allowed(TESTING), True)
check("Chillbar refused", identity.destructive_allowed(CHILLBAR), False)
check("DM (no guild) refused", identity.destructive_allowed(None), False)
check("unknown guild refused", identity.destructive_allowed(1), False)


class _StubChannel:
    """Minimum surface capabilities.run touches before the allowlist check."""
    def __init__(self, cid, guild):
        self.id = cid
        self.guild = guild


class _StubGuild:
    def __init__(self, gid):
        self.id = gid
        self.name = f"guild-{gid}"


class _StubResponse:
    status = 404
    reason = "Not Found"


class _StubClient:
    """Maps a channel id to a guild so _infer_guild can resolve it.

    fetch_channel raises the real discord.NotFound, because Ctx.channel catches
    that specific type - a stub that raised something else would let a bug in that
    handler pass unnoticed.
    """
    def __init__(self, mapping):
        self.mapping = mapping

    def get_channel(self, cid):
        gid = self.mapping.get(int(cid))
        return _StubChannel(int(cid), _StubGuild(gid)) if gid else None

    async def fetch_channel(self, cid):
        gid = self.mapping.get(int(cid))
        if gid is None:
            raise discord.NotFound(_StubResponse(), "Unknown Channel")
        return _StubChannel(int(cid), _StubGuild(gid))

    def get_guild(self, gid):
        return _StubGuild(int(gid)) if int(gid) in self.mapping.values() else None


CHILLBAR_CHAN = 5551
TESTING_CHAN = 5552
stub = _StubClient({CHILLBAR_CHAN: CHILLBAR, TESTING_CHAN: TESTING})


async def _run_expect_error(name, params):
    """Return the ActionError message, or None if it did not raise."""
    try:
        await capabilities.run(stub, lambda *_: None, name, params, force=False)
        return None
    except capabilities.ActionError as e:
        return str(e)


async def _async_checks():
    section("capabilities.run — destructive routing")

    err = await _run_expect_error("purge_messages", {"channel_id": CHILLBAR_CHAN, "limit": 10})
    check("purge in Chillbar refused", bool(err and "allowlist" in err), True)
    check("refusal names the allowlist, not the contents",
          bool(err and "messages would be deleted" not in err), True)

    err = await _run_expect_error("delete_channel", {"channel_id": CHILLBAR_CHAN})
    check("delete_channel in Chillbar refused", bool(err and "allowlist" in err), True)

    err = await _run_expect_error("ban_member", {"guild_id": CHILLBAR, "user_id": 1})
    check("ban in Chillbar refused", bool(err and "allowlist" in err), True)

    # A guild-less target must never fall through to "allowed".
    err = await _run_expect_error("delete_message", {"channel_id": 424242, "message_id": 1})
    check("unresolvable channel refused", err is not None, True)

    section("capabilities.run — unknown action and bad params")
    err = await _run_expect_error("nuke_everything", {})
    check("unknown action refused", bool(err and "unknown action" in err), True)

    err = await _run_expect_error("send_message", {"channel_id": TESTING_CHAN})
    check("missing required param caught", bool(err and "content" in err), True)

    err = await _run_expect_error("send_message",
                                  {"channel_id": TESTING_CHAN, "content": "x", "oops": 1})
    check("unknown param caught", bool(err and "oops" in err), True)

    err = await _run_expect_error("send_message", {"channel_id": "not-a-number", "content": "x"})
    check("bad int caught", bool(err and "channel_id" in err), True)


asyncio.run(_async_checks())

# ------------------------------------------------------------------ confirms
section("Confirmations — what counts as a yes")
p = confirm.park("purge_messages", {"channel_id": 1}, {"summary": "delete 5"}, TYLER, "dm")
check("parked action retrievable", confirm.get(p.token).action, "purge_messages")

check("'yes' is affirmative", confirm.read_reply("yes")[0], "yes")
check("'do it' is affirmative", confirm.read_reply("do it")[0], "yes")
check("'yes.' punctuation ok", confirm.read_reply("yes.")[0], "yes")
check("'YES' case-insensitive", confirm.read_reply("YES")[0], "yes")
check("'no' is negative", confirm.read_reply("no")[0], "no")
check("'cancel' is negative", confirm.read_reply("cancel")[0], "no")

# The whole point of matching the full phrase rather than a substring.
check("'yesterday' is NOT a yes", confirm.read_reply("yesterday")[0], None)
check("'yes but wait' is NOT a yes", confirm.read_reply("yes but wait")[0], None)
check("'i think yes maybe' is NOT a yes", confirm.read_reply("i think yes maybe")[0], None)
check("prose is NOT a yes", confirm.read_reply("that sounds ok to me")[0], None)
check("empty is NOT a yes", confirm.read_reply("")[0], None)
check("'ok' alone IS a yes", confirm.read_reply("ok")[0], "yes")

verdict, token = confirm.read_reply(f"yes {p.token}")
check("token-targeted yes parses", (verdict, token), ("yes", p.token))

section("Confirmations — lifecycle")
p2 = confirm.park("delete_channel", {"channel_id": 2}, {"summary": "x"}, TYLER, "dm")
check("parking supersedes the previous", confirm.get(p.token), None)
check("only one live at a time", confirm.current().token, p2.token)
check("consume returns it", confirm.consume(p2.token).action, "delete_channel")
check("consumed cannot fire twice", confirm.get(p2.token), None)
check("nothing pending after consume", confirm.current(), None)

p3 = confirm.park("kick_member", {"user_id": 3}, {"summary": "x"}, TYLER, "dm")
p3.expires_at = 0  # force expiry
check("expired reads as gone", confirm.get(p3.token), None)
check("expired is not pending", confirm.current(), None)

confirm.cancel()

section("Agent history — must always alternate user/assistant")
import agent  # noqa: E402 — imported here so a missing API key can't break earlier checks

_orig_mem_file = agent.MEMORY_FILE
agent.MEMORY_FILE = os.path.join(os.path.dirname(_orig_mem_file), "_test_agent_memory.json")
agent._memory = {}
try:
    KEY = "test:conversation"
    for i in range(30):
        agent._remember(KEY, f"question {i}", f"answer {i}")
    h = agent._history(KEY)
    roles = [t["role"] for t in h]
    check("history is bounded", len(h) <= agent.HISTORY_TURNS * 2, True)
    check("roles strictly alternate",
          all(a != b for a, b in zip(roles, roles[1:])), True)
    check("history starts on a user turn", roles[0], "user")
    check("history ends on an assistant turn", roles[-1], "assistant")
    check("an empty reply is not stored",
          (agent._remember(KEY, "q", ""), len(agent._history(KEY)))[1], len(h))
    check("a dropped user turn is not stored",
          (agent._remember(KEY, "", "a"), len(agent._history(KEY)))[1], len(h))
finally:
    try:
        os.remove(agent.MEMORY_FILE)
    except OSError:
        pass
    agent.MEMORY_FILE = _orig_mem_file
    agent._memory = None

section("Registry shape")
check("every action has a summary",
      all(a.summary for a in capabilities.REGISTRY.values()), True)
check("every destructive action is tier 3",
      all(a.tier == identity.DESTRUCTIVE for a in capabilities.REGISTRY.values() if a.destructive),
      True)
check("no action name collides with a legacy outbox name",
      set(capabilities.REGISTRY) & {"send", "dm", "speak", "listen", "stop_listen",
                                    "edit", "delete", "history", "purge"},
      set())

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
