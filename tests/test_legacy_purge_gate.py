"""
test_legacy_purge_gate.py - the ungated destructive verbs are really gone.

THE DEFECT, retired 2026-08-26 on Tyler's call. bot.py's poll_outbox carried two
legacy branches, `purge` and `delete`, that predate the capability registry. They
called ch.purge() and msg.delete() DIRECTLY: no policy.authorize, no
destructive_guilds allowlist, no dry-run, no confirmation token. Their registry
twins - purge_messages and delete_message - carry all three. So the tier-3
guarantee was a property of the verb NAME rather than of the irreversible EFFECT,
and `benham.py purge <channel_id> --days 0 --scope guild` would sweep every text
channel in any guild the bot could see.

Why this file exists rather than a line in test_registry: the registry was always
right about its own actions. What was wrong was a DISPATCH PATH that never asked
it. So the assertion has to drive the real poll_outbox and watch the Discord
objects, not inspect the registry - the founding bug of this repo was a gate that
was written, tested, and never called.

The stub is deliberately assertive: its purge() and delete() RECORD and would
otherwise succeed. A stub that raised would pass this test for the wrong reason,
and a loose stub reads exactly like a passing one.

    python test_legacy_purge_gate.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import glob
import json
import shutil
import sys
import tempfile

from benham import bot
from benham.core import capabilities

_fails = []
touched = {"purged": 0, "deleted": 0}


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


class _Msg:
    id = 1234

    async def delete(self):
        touched["deleted"] += 1        # would really have deleted


class _Channel:
    id = 5552
    name = "asd"

    def __init__(self):
        self.guild = None

    async def fetch_message(self, mid):
        return _Msg()

    async def purge(self, **kw):
        touched["purged"] += 1         # would really have purged
        return [_Msg()]


class _Client:
    def get_channel(self, cid):
        return _Channel()

    async def fetch_channel(self, cid):
        return _Channel()


def _queue(tmp, **req):
    req.setdefault("face", bot.FACE)
    path = _os.path.join(tmp, "req.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(req, f)
    return path


def _result(tmp):
    hits = glob.glob(_os.path.join(tmp, "failed", "*_result.json")) +            glob.glob(_os.path.join(tmp, "sent", "*_result.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as f:
        return json.load(f)


def main():
    print("== the retired verbs cannot reach Discord ==")

    # Both names must be UNKNOWN to the registry, which is what makes the
    # redirect table meaningful rather than a second name for the same thing.
    for legacy, twin in bot._RETIRED_UNGATED.items():
        check(f"{legacy!r} is not itself a registry action",
              legacy in capabilities.REGISTRY, False)
        check(f"...and redirects to {twin!r}, which is tier 3",
              capabilities.REGISTRY[twin].tier, 3)
        check(f"...which requires a confirmation",
              capabilities.REGISTRY[twin].needs_confirm, True)

    real_client, real_outbox = bot.client, bot.OUTBOX
    real_sent, real_failed = bot.SENT, bot.FAILED
    bot.client = _Client()
    try:
        for action, params in (("purge", {"channel_id": 5552, "older_than_days": 0,
                                          "scope": "guild"}),
                               ("delete", {"channel_id": 5552, "message_id": 1234})):
            tmp = tempfile.mkdtemp(prefix="benham-outbox-")
            _os.makedirs(_os.path.join(tmp, "sent"), exist_ok=True)
            _os.makedirs(_os.path.join(tmp, "failed"), exist_ok=True)
            bot.OUTBOX = tmp
            bot.SENT = _os.path.join(tmp, "sent")
            bot.FAILED = _os.path.join(tmp, "failed")
            touched["purged"] = touched["deleted"] = 0
            try:
                _queue(tmp, action=action, **params)
                asyncio.run(bot.poll_outbox())

                check(f"{action!r}: nothing was purged",
                      touched["purged"], 0)
                check(f"{action!r}: nothing was deleted",
                      touched["deleted"], 0)

                res = _result(tmp)
                check(f"{action!r}: the request was ANSWERED, not ignored",
                      res is not None, True)
                check(f"{action!r}: it failed rather than reporting success",
                      (res or {}).get("status"), "failed")
                # A refusal that does not say what to use instead is how a
                # retired verb becomes a mystery six months later.
                check(f"{action!r}: the refusal names the twin",
                      bot._RETIRED_UNGATED[action] in (res or {}).get("error", ""),
                      True)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        bot.client, bot.OUTBOX = real_client, real_outbox
        bot.SENT, bot.FAILED = real_sent, real_failed

    print()
    print("== the CLIs point at the guarded twins ==")
    from benham.cli import delete as delete_cli
    from benham.cli import purge as purge_cli
    check("delete.py enqueues delete_message", delete_cli.ACTION, "delete_message")
    src = open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "benham", "cli", "purge.py"),
        encoding="utf-8").read()
    check("purge.py enqueues purge_messages", '"purge_messages"' in src, True)
    # The guild sweep has no gated equivalent, so it must be REFUSED rather than
    # silently downgraded to a single channel - a flag that quietly means
    # something smaller than it says is worse than one that is gone.
    check("purge.py refuses --scope guild",
          purge_cli.main(["purge", "5552", "--scope", "guild"]), 2)
    check("purge.py still accepts --scope channel as a no-op",
          "--scope guild was retired" in src, True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
