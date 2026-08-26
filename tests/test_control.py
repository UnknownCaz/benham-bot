"""
test_control.py - checks on the gates, not on Discord.

Everything here runs offline with a stub client. The point is the safety layer:
who Benham obeys, where destructive actions may run, and what counts as a yes.
Those three are the parts where a silent regression is expensive, and they are
also the parts that are awkward to exercise by hand against a live bot - you
cannot casually test "does it refuse to purge Chillbar" in Chillbar.

    python test_control.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package and bot.py live.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import os
import shutil
import sys
import tempfile

import discord

from benham.core import capabilities
from benham.core import confirm
from benham.core import identity
from benham.core import policy

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

section("Agent engagement — via policy, which bot.on_message actually calls")
# These used to assert against identity.agent_allowed(), a helper nothing in
# production called. They passed while the live code did the opposite. They now go
# through policy.may_engage_agent, which is the function on_message asks.
check("owner DM engages",
      policy.may_engage_agent(policy.CallContext.owner_dm(TYLER)).allowed, True)
check("owner in Testing engages",
      policy.may_engage_agent(policy.CallContext.owner_guild(TYLER, TESTING)).allowed, True)
check("owner in Chillbar does NOT",
      policy.may_engage_agent(policy.CallContext.owner_guild(TYLER, CHILLBAR)).allowed, False)
check("no context does not engage",
      policy.may_engage_agent(None).allowed, False)
check("the local CLI does not engage the chat agent",
      policy.may_engage_agent(policy.CallContext.local()).allowed, False)
# Non-owners are stopped by is_owner in on_message before this is reached; the
# owner-gate suite covers that end to end.
check("stranger is still not an owner", identity.is_owner(STRANGER), False)

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
        await capabilities.run(stub, lambda *_: None, name, params, force=False,
                               call_ctx=policy.CallContext.local())
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

section("Confirmations — tier 3 requires the token")
# Tyler, 2026-08-24: "one copy-paste action is worth an irreversible action."
# This supersedes his 2026-08-17 naming rule ("yes, purge that channel" fires,
# "yes, your totally right" does not), which is kept below as the revert path.
# The naming rule's guarantee was word overlap with ordinary English, over a word
# set built from channel/user/role names - so a purge parked on #general could be
# fired by "yeah, general seems right", and the set grows with every capability
# added. A token is exact and does not rot.
#
# Note every check above passes NO pending, which is the old behaviour and stays
# exactly as it was. The rule only engages when the caller hands over the action,
# so nothing that used to fire has quietly stopped firing except tier 3.
confirm.cancel()
d3 = confirm.park("purge_messages", {"channel_id": 1},
                  {"channel": "general", "count": 42}, TYLER, "dm")

check("the token fires it", confirm.read_reply(f"yes {d3.token}", d3)[0], "yes")
check("the token comes back for binding",
      confirm.read_reply(f"yes {d3.token}", d3)[1], d3.token)
check("token plus a name still fires",
      confirm.read_reply(f"yes, purge that channel {d3.token}", d3)[0], "yes")

check("bare 'yes' does NOT fire tier 3",
      confirm.read_reply("yes", d3)[0], "needs_token")
check("the OLD rule's example no longer fires",
      confirm.read_reply("yes, purge that channel.", d3)[0], "needs_token")
check("naming the target is no longer enough",
      confirm.read_reply("yes general", d3)[0], "needs_token")
check("'yes, do it' names nothing",
      confirm.read_reply("yes, do it", d3)[0], "needs_token")
# A mistyped or lapsed token is the one new failure this change creates, and the
# worst possible answer to it is silence - he did exactly what he was asked to.
check("a wrong/expired token is refused OUT LOUD",
      confirm.read_reply("yes deadbe", d3)[0], "needs_token")
check("a reference without a yes is still not a yes",
      confirm.read_reply("purge it", d3)[0], None)

# The refusal is deliberately NOT widened past the set that used to fire. Over a
# confirm window that can run an hour, a sentence merely opening with "yes" and
# naming nothing is far likelier to be conversation than consent - so it keeps
# falling through to the agent exactly as it did before this change.
check("his example that should NOT, still falls through",
      confirm.read_reply("yes, your totally right", d3)[0], None)
check("ordinary agreement does not get nagged",
      confirm.read_reply("yes that sounds about right to me", d3)[0], None)

# Cancelling must never get harder than confirming. If "no" needed to carry the
# token too, the safe direction would be the inconvenient one.
check("bare 'no' still cancels tier 3", confirm.read_reply("no", d3)[0], "no")

# The refusal has to be a distinct verdict rather than a None: the caller says
# WHY nothing happened, because an unexplained no-op on a destructive action just
# gets answered with a louder yes.
check("refusal is distinguishable from ambiguity",
      confirm.read_reply("yes", d3)[0] != confirm.read_reply("hmm", d3)[0], True)

# The prompt must state the rule it will actually enforce. Telling him "reply
# yes" and then refusing a bare yes is how a safety feature becomes a thing
# people fight with.
check("tier-3 prompt warns a bare yes will not work",
      "bare \"yes\" will not fire" in confirm.describe(d3), True)
check("tier-3 prompt hands him the exact string to send",
      f"yes {d3.token}" in confirm.describe(d3), True)

# ---- the revert path: flag off falls back to the 2026-08-17 naming rule ----
# It is config rather than a revert commit, so it has to actually work; an
# escape hatch nothing exercises is an escape hatch that has rusted shut.
_orig_confirm_cfg = identity.CONTROL.get("confirm")
identity.CONTROL["confirm"] = dict(_orig_confirm_cfg or {},
                                   require_token_tier3=False)
check("flag off: the naming rule fires again",
      confirm.read_reply("yes, purge that channel.", d3)[0], "yes")
check("flag off: bare yes is the old refusal",
      confirm.read_reply("yes", d3)[0], "needs_reference")
check("flag off: the token still fires",
      confirm.read_reply(f"yes {d3.token}", d3)[0], "yes")
check("flag off: prompt goes back to the naming wording",
      "names what it is" in confirm.describe(d3), True)
if _orig_confirm_cfg is None:
    identity.CONTROL.pop("confirm", None)
else:
    identity.CONTROL["confirm"] = _orig_confirm_cfg
check("flag restored: the token is mandatory again",
      confirm.read_reply("yes, purge that channel.", d3)[0], "needs_token")

# Absent from config must mean ON. The default is the safety property; a
# control.json written before this flag existed must not silently opt out.
_no_cfg = dict(identity.CONTROL)
_no_cfg.pop("confirm", None)
_saved = identity.CONTROL
identity.CONTROL = _no_cfg
check("missing config defaults to mandatory", confirm.require_token(), True)
identity.CONTROL = _saved

confirm.cancel()
d2 = confirm.park("add_role", {"user_id": 1, "role": "Streamer"},
                  {"name": "Streamer"}, TYLER, "dm")
check("tier 2 still accepts a bare yes", confirm.read_reply("yes", d2)[0], "yes")
check("tier-2 prompt does not warn", "will not fire" in confirm.describe(d2), False)
confirm.cancel()

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
from benham.core import agent  # noqa: E402 — imported here so a missing API key can't break earlier checks

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

section("The control file itself — absent and malformed are opposite cases")
# Both halves matter and neither is ceremony. The loud half stops a typo from
# silently WIDENING two gates: treating an unparseable control.json as an absent
# one drops post_guilds (posting_allowed then returns True everywhere) and drops
# agent.enabled (agent.py defaults it to True). The quiet half is what stops a
# future edit from "fixing" that by catching more and making a fresh clone fail to
# boot. Written together so neither can drift without the other noticing.
_orig_control_file = identity.CONTROL_FILE
_cf_dir = tempfile.mkdtemp(prefix="benham-controlfile-")
try:
    identity.CONTROL_FILE = os.path.join(_cf_dir, "control.json")

    check("an ABSENT control.json is not an error", identity.load_control(), dict(identity._DEFAULTS))

    with open(identity.CONTROL_FILE, "w", encoding="utf-8") as _f:
        _f.write('{ "owner_ids": [1], oops }')
    _raised = None
    try:
        identity.load_control()
    except identity.ControlFileError as e:
        _raised = e
    check("a MALFORMED control.json raises", _raised is not None, True)
    check("...and the message names the file", identity.CONTROL_FILE in str(_raised), True)
    check("...and does not masquerade as the absent case",
          "not valid JSON" in str(_raised), True)

    # The trap this whole section exists for: the old code returned the defaults
    # here, and the defaults are not uniformly restrictive. posting_allowed() reads
    # the module-level CONTROL, so swap in exactly what that fallback produces.
    os.remove(identity.CONTROL_FILE)                     # back to the absent state
    _orig_control = identity.CONTROL
    try:
        identity.CONTROL = identity.load_control()
        check("the defaults a malformed file would have fallen back to leave posting UNCAPPED",
              identity.posting_allowed(4040404040404040404, 999), True)
    finally:
        identity.CONTROL = _orig_control
    check("...while destructive_guilds does fail closed, which is why it looked safe",
          identity._DEFAULTS["destructive_guilds"], [])
    check("...and agent.enabled is absent, which agent.py reads as ON",
          "enabled" in identity._DEFAULTS["agent"], False)

    # A valid file still loads, so the raise is about parseability and nothing else.
    with open(identity.CONTROL_FILE, "w", encoding="utf-8") as _f:
        _f.write('{"owner_ids": [7], "_comment": "annotation keys are still skipped"}')
    _reloaded = identity.load_control()
    check("a VALID control.json still loads", _reloaded["owner_ids"], [7])
    check("...with annotation keys skipped", "_comment" in _reloaded, False)
finally:
    identity.CONTROL_FILE = _orig_control_file
    shutil.rmtree(_cf_dir, ignore_errors=True)

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

section("People-lists parse from BOTH shapes - the old one is not a courtesy")
# control.json is a live control plane on a running bot. The readable name->id
# shape is what every people-list should be written as from here on, but a bot
# whose config still holds a bare array must keep booting - a format change that
# refuses the file already on disk takes the bot down at the next restart, which
# is a worse failure than the unreadability it fixes. So the old shape is asserted
# as its own case, deliberately, rather than left to be inferred from the new one
# passing.
_DOOM, _DRACO = 777000777000777000, 777000777000777001

check("the readable shape parses to the right ids",
      identity.people_map({"doom": _DOOM, "draco": _DRACO}),
      {"doom": _DOOM, "draco": _DRACO})
check("a BARE ARRAY still parses - an unmigrated control.json must still boot",
      identity.people_map([_DOOM, _DRACO]),
      {str(_DOOM): _DOOM, str(_DRACO): _DRACO})
check("...and both shapes agree on the id set, which is what every gate reads",
      set(identity.people_map({"doom": _DOOM}).values())
      == set(identity.people_map([_DOOM]).values()),
      True)
check("string ids in the readable shape are coerced, as JSON often carries them",
      identity.people_map({"doom": str(_DOOM)}), {"doom": _DOOM})
check("absent is empty, not an error - the fail-closed direction",
      [identity.people_map(None), identity.people_map([]), identity.people_map({})],
      [{}, {}, {}])
check("whitespace in a hand-typed name is stripped",
      identity.people_map({"  doom  ": _DOOM}), {"doom": _DOOM})

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
