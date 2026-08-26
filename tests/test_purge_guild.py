"""
test_purge_guild.py - the guild-wide sweep is gated, bounded and honest.

It exists because this is the largest blast radius in the bot: every other
destructive action names a target you chose, and this one resolves its OWN
target set. So the preview is not a courtesy, it IS the safety mechanism - the
only thing between a token and a server is the sentence "N messages across M
channels in <guild>".

History worth keeping: it used to exist as `purge --scope guild` on the legacy
outbox verb, with no allowlist, no dry-run and no token. It was retired on
2026-08-26 rather than repointed, because the per-channel twin has no guild
scope and silently narrowing the flag would have been worse than removing it.
Tyler's call was to build it back properly, which is this.

What is asserted, in order:
  1. It carries its sibling's exact security posture - tier 3, confirmation,
     guild-resolved - so the allowlist and the token apply.
  2. The dry-run TOUCHES NOTHING. A preview that deleted would be the whole
     design inverted, and the stub records rather than raises, so that failure
     shows up here rather than passing because the call happened to error.
  3. The preview states the true blast radius: total, per channel, guild BY NAME.
  4. Channels the bot cannot purge are named in the PREVIEW, before the token -
     including the negative case, because an absent line reads as "nothing was
     skipped" and as "nobody checked" identically.
  5. It is BOUNDED per channel, so it cannot be the unbounded verb it replaced
     wearing a token.
  6. Partial failure CONTINUES and reports per channel, never one aggregate.

    python test_purge_guild.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import discord

from benham.core import capabilities

_fails = []
purged = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


class _Perms:
    def __init__(self, manage=True, read=True):
        self.manage_messages = manage
        self.read_message_history = read
        self.view_channel = read


class _M:
    def __init__(self, i, author=1, content="hello"):
        self.id = i
        self.author = type("A", (), {"id": author})()
        self.content = content
        self.created_at = datetime.now(timezone.utc) - timedelta(days=30 + i)


class _Chan:
    def __init__(self, name, msgs, manage=True, read=True, boom=None):
        self.name = name
        self.msgs = msgs
        self._perms = _Perms(manage, read)
        self.boom = boom

    def permissions_for(self, _me):
        return self._perms

    def history(self, limit=100, before=None):
        msgs = self.msgs[:limit]

        async def gen():
            for m in msgs:
                yield m
        return gen()

    async def purge(self, limit=100, before=None, check=None, bulk=True):
        if self.boom:
            raise self.boom
        # Records and SUCCEEDS. A stub that raised would let a dry-run-that-
        # deletes pass this file for the wrong reason.
        hit = [m for m in self.msgs[:limit] if check is None or check(m)]
        purged.append((self.name, len(hit)))
        return hit


class _Guild:
    name = "Testing Server"
    id = 736988645562646619
    me = object()

    def __init__(self, chans):
        self.text_channels = chans


class _Client:
    def __init__(self, guild):
        self._g = guild

    def get_guild(self, gid):
        return self._g


async def run(guild, params, dry):
    ctx = capabilities.Ctx(_Client(guild), lambda *a: None, dry_run=dry)
    return await capabilities.REGISTRY["purge_guild"].handler(ctx, params)


def main():
    print("== posture: the sibling's gates, inherited whole ==")
    act = capabilities.REGISTRY["purge_guild"]
    sib = capabilities.REGISTRY["purge_messages"]
    for field in ("tier", "needs_confirm", "needs_guild", "outward", "posts",
                  "taints", "blocked_when_tainted", "guest"):
        check(f"{field} matches purge_messages",
              getattr(act, field, None), getattr(sib, field, None))
    check("and that tier is 3", act.tier, 3)

    print()
    print("== the dry run touches nothing, and states the blast radius ==")
    g = _Guild([_Chan("asd", [_M(1), _M(2), _M(3)]),
                _Chan("beta", [_M(4)]),
                _Chan("locked", [_M(5), _M(6)], manage=False),
                _Chan("hidden", [_M(7)], read=False)])
    purged.clear()
    prev = asyncio.run(run(g, {"guild_id": g.id, "limit": 100}, dry=True))
    check("NOTHING was purged by the preview", purged, [])
    check("the count is the real total", prev["count"], 4)
    check("...across the channels it can actually touch", prev["channels"], 2)
    check("the guild is named", prev["guild"], "Testing Server")
    check("the summary says SERVER-WIDE", "SERVER-WIDE" in prev["summary"], True)
    check("the summary carries the count", "4 messages" in prev["summary"], True)
    check("per-channel breakdown is in the detail",
          "asd: 3" in prev["detail"] and "beta: 1" in prev["detail"], True)

    print()
    print("== what it CANNOT touch is named before the token ==")
    check("the locked channel is listed as skipped",
          "locked" in prev["detail"], True)
    check("...with the reason", "no Manage Messages" in prev["detail"], True)
    check("the unreadable channel too",
          "hidden" in prev["detail"] and "cannot read history" in prev["detail"],
          True)
    check("neither is counted as in-scope",
          "locked: 2" in prev["detail"] or "hidden: 1" in prev["detail"], False)

    # The negative case is written out rather than omitted: an absent line reads
    # as "nothing skipped" and as "nobody checked" identically.
    clean = _Guild([_Chan("asd", [_M(1)])])
    prev2 = asyncio.run(run(clean, {"guild_id": clean.id}, dry=True))
    check("with nothing skipped it SAYS so",
          "No channels skipped" in prev2["detail"], True)
    empty = _Guild([_Chan("asd", [])])
    prev3 = asyncio.run(run(empty, {"guild_id": empty.id}, dry=True))
    check("a zero-match preview still accounts for itself",
          prev3["count"] == 0 and "No channels skipped" in prev3["detail"], True)

    print()
    print("== bounded per channel, like its sibling ==")
    purged.clear()
    big = _Guild([_Chan("asd", [_M(i) for i in range(50)])])
    prevb = asyncio.run(run(big, {"guild_id": big.id, "limit": 5}, dry=True))
    check("the preview honours limit per channel", prevb["count"], 5)
    asyncio.run(run(big, {"guild_id": big.id, "limit": 5}, dry=False))
    check("and so does the real run", purged, [("asd", 5)])

    print()
    print("== partial failure continues, and reports per channel ==")
    purged.clear()
    mixed = _Guild([
        _Chan("ok1", [_M(1), _M(2)]),
        _Chan("boom", [_M(3)], boom=discord.Forbidden(
            type("R", (), {"status": 403, "reason": "Forbidden"})(), "nope")),
        _Chan("ok2", [_M(4)]),
        _Chan("locked", [_M(5)], manage=False),
    ])
    res = asyncio.run(run(mixed, {"guild_id": mixed.id}, dry=False))
    check("channels after the failure were still swept",
          sorted(n for n, _ in purged), ["ok1", "ok2"])
    check("the total is real", res["deleted"], 3)
    check("per channel, not one aggregate",
          res["deleted_by_channel"], {"#ok1": 2, "#ok2": 1})
    check("the refusing channel is reported, not swallowed",
          "#boom" in res["skipped"], True)
    check("...and so is the one it never had permission for",
          res["skipped"].get("#locked"), "no Manage Messages")

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
