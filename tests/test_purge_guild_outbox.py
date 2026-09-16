"""
test_purge_guild_outbox.py - `purge --guild` survives the trip through the outbox.

THE DEFECT (logs/supervise.log, 2026-08-26 02:57:09Z): request file
20260826_025708_f62d927e.json - written by `benham.py purge --guild` - failed with
`KeyError: 'channel_id'`. A composition bug, not a logic one: the AUTHOR (the CLI,
a fresh import) wrote `action: purge_guild, guild_id: ...`, and the CONSUMER (a bot
process booted at 02:41Z, before purge_guild was committed at 03:02Z) did not have
that action in its registry. poll_outbox's rule was "not a registry action => a
legacy send", and the legacy send path demands channel_id. So a verb the consumer
did not know was silently re-read as a chat message with its key missing.

Two halves, both asserted through the REAL poll_outbox:
  1. Today's consumer reads the exact 08-26 request shape: it previews, parks,
     hands back a token, and deletes NOTHING. The tier-3 token path is mandatory -
     a request carrying no token or a bogus one purges nothing.
  2. A consumer that does not know an action (the stale-process case) REFUSES it
     by name and says why, instead of falling into the send path. Nothing is sent.

No real guild is touched: the client is a stub whose purge() RECORDS and would
otherwise succeed, so a preview that deleted would show up here.

    python test_purge_guild_outbox.py
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
from datetime import datetime, timedelta, timezone

from benham import bot
from benham.core import capabilities, confirm

_fails = []
touched = {"purged": 0, "sent": 0}

# The fixture's one tier-3 guild; the id is the Testing Server's, from _testconfig.
GUILD = 736988645562646619

# Byte-for-byte the shape of the 08-26 request, minus the timestamp.
REQ_0826 = {"action": "purge_guild", "guild_id": GUILD, "older_than_days": 3650,
            "limit": 20, "source": "benham.py purge --guild"}


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


class _Perms:
    manage_messages = read_message_history = view_channel = True


class _M:
    def __init__(self, i):
        self.id = i
        self.author = type("A", (), {"id": 1})()
        self.content = "old"
        self.created_at = datetime.now(timezone.utc) - timedelta(days=4000 + i)


class _Chan:
    id = 5552
    name = "asd"

    def __init__(self):
        self.msgs = [_M(1), _M(2)]

    def permissions_for(self, _me):
        return _Perms()

    def history(self, limit=100, before=None):
        msgs = self.msgs[:limit]

        async def gen():
            for m in msgs:
                yield m
        return gen()

    async def purge(self, **kw):
        touched["purged"] += 1          # would really have purged
        return list(self.msgs)

    async def send(self, content):
        touched["sent"] += 1            # would really have posted
        return type("S", (), {"id": 1})()


class _Guild:
    name = "Testing Server"
    id = GUILD
    me = object()

    def __init__(self):
        self.text_channels = [_Chan()]


class _Client:
    def __init__(self):
        self._g = _Guild()

    def get_guild(self, gid):
        return self._g if int(gid) == GUILD else None

    def get_channel(self, cid):
        return self._g.text_channels[0]

    async def fetch_channel(self, cid):
        return self._g.text_channels[0]


def _drive(req):
    """Queue one request, run one real poll_outbox pass, return its result record."""
    tmp = tempfile.mkdtemp(prefix="benham-outbox-")
    try:
        for d in ("sent", "failed"):
            _os.makedirs(_os.path.join(tmp, d), exist_ok=True)
        bot.OUTBOX, bot.SENT, bot.FAILED = (tmp, _os.path.join(tmp, "sent"),
                                            _os.path.join(tmp, "failed"))
        req = dict(req)
        req.setdefault("face", bot.FACE)
        with open(_os.path.join(tmp, "20260826_025708_f62d927e.json"), "w",
                  encoding="utf-8") as f:
            json.dump(req, f)
        asyncio.run(bot.poll_outbox())
        hits = (glob.glob(_os.path.join(tmp, "failed", "*_result.json"))
                + glob.glob(_os.path.join(tmp, "sent", "*_result.json")))
        if not hits:
            return None
        with open(hits[0], encoding="utf-8") as f:
            return json.load(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    real = (bot.client, bot.OUTBOX, bot.SENT, bot.FAILED, bot.log)
    bot.client = _Client()
    # The bot's own log says "FAILED <file>" for every refusal this file provokes
    # on purpose, and run_tests.py reads any "FAIL" in the output as a red test.
    logged = []
    bot.log = logged.append
    try:
        print("== the 08-26 request shape, through today's consumer ==")
        touched.update(purged=0, sent=0)
        res = _drive(REQ_0826) or {}
        check("it is answered", bool(res), True)
        check("no KeyError", "KeyError" in str(res.get("error")), False)
        check("step one is a preview awaiting a token",
              res.get("status"), "confirmation_required")
        check("...which hands back a token", bool(res.get("confirm_token")), True)
        check("...and states the server-wide blast radius",
              "SERVER-WIDE" in str((res.get("preview") or {}).get("summary")), True)
        check("the preview purged NOTHING", touched["purged"], 0)
        check("...and posted nothing", touched["sent"], 0)
        confirm.cancel()

        print()
        print("== the token path stays mandatory ==")
        touched.update(purged=0, sent=0)
        res = _drive(dict(REQ_0826, confirm_token="zzzzzz")) or {}
        check("a bogus token is refused", res.get("status"), "failed")
        check("...by saying the token is unknown", "unknown or expired"
              in str(res.get("error")), True)
        check("...and nothing was purged", touched["purged"], 0)

        print()
        print("== a consumer that does not know the verb refuses it by name ==")
        # The 08-26 process, reconstructed: the action is absent from ITS registry.
        saved = capabilities.REGISTRY.pop("purge_guild")
        try:
            touched.update(purged=0, sent=0)
            res = _drive(REQ_0826) or {}
        finally:
            capabilities.REGISTRY["purge_guild"] = saved
        check("it fails", res.get("status"), "failed")
        check("...NOT as a KeyError from the send path",
              "KeyError" in str(res.get("error")), False)
        check("...naming the action it does not know",
              "purge_guild" in str(res.get("error")), True)
        check("...and saying a restart is the likely fix",
              "restart" in str(res.get("error")), True)
        check("nothing was purged", touched["purged"], 0)
        check("nothing was sent as a chat message instead", touched["sent"], 0)

        print()
        print("== the legacy verbs still route where they always did ==")
        touched.update(purged=0, sent=0)
        res = _drive({"action": "send", "channel_id": 5552, "content": "hi"}) or {}
        check("a plain send still sends", res.get("status"), "sent")
        res = _drive({"channel_id": 5552, "content": "hi"}) or {}
        check("...and so does one with no action at all (the oldest shape)",
              res.get("status"), "sent")
    finally:
        bot.client, bot.OUTBOX, bot.SENT, bot.FAILED, bot.log = real

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
