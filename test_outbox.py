"""
test_outbox.py - the machine ingress: outbox/*.json -> poll_outbox -> sent/failed.

Every CLI in this directory hands work to the running bot the same way: drop a
JSON file in outbox/ and let the two-second poller execute it. That makes the
poller a real command-ingress surface - the same rank as on_message - and until
this file it had zero coverage. Three properties matter and are pinned here:

  Atomicity. outbox.enqueue writes `.json.tmp` (outside the poller's glob) and
  os.replace()s it into place, so a request appears complete or not at all. The
  consumer half of that contract is that the poller must never pick up a `.tmp`.

  The policy chokepoint. Registry actions dispatched from the outbox run through
  capabilities.run under a LOCAL_CLI context - the outbox does not get its own,
  more trusting, path around policy.

  Token redemption. A destructive request without a token gets a dry-run and a
  parked confirmation; redeeming the token fires the PARKED params under the
  PARKED context, so a queued file cannot swap the action - or its arguments, or
  its origin - under a token that was issued for something else.

Deliberately offline, like every other suite: capabilities.run is watched, not
executed, because "does the poller purge" is not a thing to verify by purging.

    python test_outbox.py
"""

import asyncio
import json
import os
import sys
import tempfile

import outbox

# Redirect the CLI-side outbox before anything writes. Same reason the guest
# tests redirect their state files: a test run must never leave requests where
# the real bot would execute them on its next start.
_tmp = tempfile.mkdtemp(prefix="benham-outbox-test-")
outbox.OUTBOX = os.path.join(_tmp, "outbox")

os.environ.setdefault("BOT_KEY", "test-token-not-used")
import bot  # noqa: E402
import capabilities  # noqa: E402
import confirm  # noqa: E402
import policy  # noqa: E402

TYLER = 273967061619965952

# Point the poller's three directories at the same sandbox.
bot.OUTBOX = outbox.OUTBOX
bot.SENT = os.path.join(outbox.OUTBOX, "sent")
bot.FAILED = os.path.join(outbox.OUTBOX, "failed")
os.makedirs(bot.SENT, exist_ok=True)
os.makedirs(bot.FAILED, exist_ok=True)
bot.log = lambda *a, **k: None

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def poll_once():
    """One pass of the real poller loop body."""
    asyncio.run(bot.poll_outbox.coro())


def harvest(dirpath):
    """Read and clear a sent/failed directory, returning the parsed results."""
    out = []
    for f in sorted(os.listdir(dirpath)):
        p = os.path.join(dirpath, f)
        if f.endswith("_result.json"):
            with open(p, "r", encoding="utf-8") as fh:
                out.append(json.load(fh))
        os.remove(p)
    return out


def queued():
    """Request files the poller would see on its next pass."""
    return sorted(f for f in os.listdir(outbox.OUTBOX) if f.endswith(".json"))


# --------------------------------------------------------------------------
section("enqueue — the write half of the atomicity contract")

path = outbox.enqueue(action="send_message", channel_id=555, content="hi",
                      actor_id=TYLER)
check("the request lands as a .json the poller will see",
      path.endswith(".json") and os.path.exists(path), True)
with open(path, "r", encoding="utf-8") as f:
    req = json.load(f)
check("the fields arrive as written", (req["action"], req["channel_id"]),
      ("send_message", 555))
check("queued_at is stamped so no caller has to remember", "queued_at" in req, True)
check("no .tmp is left behind",
      [f for f in os.listdir(outbox.OUTBOX) if f.endswith(".tmp")], [])
os.remove(path)

section("parse_ids — the shared CLI boundary")
ids, err = outbox.parse_ids(["123", "456"], ["channel_id", "message_id"])
check("good ids parse", (ids, err), ([123, 456], None))
ids, err = outbox.parse_ids(["123", "oops"], ["channel_id", "message_id"])
check("a bad id returns no ids", ids, None)
check("...and the error names WHICH argument was wrong",
      "message_id" in (err or ""), True)


# --------------------------------------------------------------------------
section("The poller never reads a half-written request")

# The read half of the contract enqueue's rename exists for. A request written
# in place could be seen mid-write; the glob is what makes that impossible, so
# pin the glob: a `.json.tmp` (however malformed) must be invisible.
stray = os.path.join(outbox.OUTBOX, "20990101_000000_partial.json.tmp")
with open(stray, "w", encoding="utf-8") as f:
    f.write('{"action": "send_message", "channel_')   # torn mid-write

ran = []
_real_run = capabilities.run


async def watched_run(client, log, name, params, actor_id=None, dry_run=False,
                      force=False, call_ctx=None, **kw):
    ran.append({"name": name, "params": dict(params), "force": force,
                "actor_id": actor_id, "call_ctx": call_ctx})
    act = capabilities.REGISTRY[name]
    if act.needs_confirm and not force:
        return None, {"summary": f"would run {name}"}
    return {"status": "done"}, None


capabilities.run = watched_run
try:
    poll_once()
    check("a torn .tmp dispatches nothing", ran, [])
    check("...and is left alone for its writer to finish",
          os.path.exists(stray), True)
    check("...and produces no result in sent/",
          harvest(bot.SENT), [])
    check("...or failed/", harvest(bot.FAILED), [])

    # ----------------------------------------------------------------------
    section("A registry action dispatches through the chokepoint")

    outbox.enqueue(action="send_message", channel_id=555, content="hello",
                   actor_id=TYLER)
    poll_once()
    check("dispatched through capabilities.run, once",
          [(r["name"], r["force"]) for r in ran], [("send_message", True)])
    check("under a LOCAL_CLI context — no special outbox trust",
          ran[0]["call_ctx"].origin, policy.Origin.LOCAL_CLI)
    check("params exclude the envelope fields",
          sorted(ran[0]["params"]), ["channel_id", "content"])
    results = harvest(bot.SENT)
    check("the request is archived to sent/ with its result",
          [r.get("status") for r in results], ["ok"])
    check("the outbox is drained", queued(), [])

    # ----------------------------------------------------------------------
    section("Destructive two-step, part one: no token means a dry-run and a park")

    ran.clear()
    confirm.cancel()
    outbox.enqueue(action="purge_messages", channel_id=555, older_than_days=30,
                   actor_id=TYLER)
    poll_once()
    check("only a dry-run ran (force=False)",
          [(r["name"], r["force"]) for r in ran], [("purge_messages", False)])
    parked = confirm.current()
    check("the real params were parked", parked is not None
          and parked.params.get("older_than_days"), 30)
    results = harvest(bot.SENT)
    check("the caller is told to come back with the token",
          [r.get("status") for r in results], ["confirmation_required"])
    check("...and the token in the result is the parked one",
          results[0].get("confirm_token"), parked.token)

    # ----------------------------------------------------------------------
    section("Part two: redeeming the token fires what was PREVIEWED")

    ran.clear()
    # The redeeming file lies about the params. What fires must be the parked 30,
    # not the 99999 this request smuggles in next to the token.
    outbox.enqueue(action="purge_messages", channel_id=555, older_than_days=99999,
                   confirm_token=parked.token, actor_id=TYLER)
    poll_once()
    check("the action fired exactly once, forced",
          [(r["name"], r["force"]) for r in ran], [("purge_messages", True)])
    check("with the PARKED params, not the re-submitted ones",
          ran[0]["params"].get("older_than_days"), 30)
    check("under the PARKED context — a token cannot move an action's origin",
          ran[0]["call_ctx"] is parked.call_ctx, True)
    check("the token is single-use", confirm.current(), None)
    results = harvest(bot.SENT)
    check("archived as ok", [r.get("status") for r in results], ["ok"])

    # ----------------------------------------------------------------------
    section("A token redeems only the action it was issued for")

    ran.clear()
    confirm.cancel()
    outbox.enqueue(action="purge_messages", channel_id=555, older_than_days=7,
                   actor_id=TYLER)
    poll_once()
    parked = confirm.current()
    harvest(bot.SENT)
    ran.clear()
    outbox.enqueue(action="delete_channel", channel_id=555,
                   confirm_token=parked.token, actor_id=TYLER)
    poll_once()
    check("nothing fired", ran, [])
    results = harvest(bot.FAILED)
    check("the mismatch is a refusal, filed under failed/",
          [r.get("status") for r in results], ["failed"])
    check("...naming what the token was actually for",
          "was issued for" in (results[0].get("error") or ""), True)
    # Consumed-and-destroyed, not left live: the failure mode to avoid is a
    # rejected redemption leaving a token someone can keep guessing at.
    check("the mismatched token is dead, not still parked", confirm.current(), None)

    section("An unknown token is a refusal, not a fresh dry-run")
    ran.clear()
    outbox.enqueue(action="purge_messages", channel_id=555, older_than_days=7,
                   confirm_token="ffffff", actor_id=TYLER)
    poll_once()
    check("nothing fired", ran, [])
    results = harvest(bot.FAILED)
    check("filed under failed/, saying expiry means cancelled",
          "unknown or expired" in (results[0].get("error") or ""), True)

    # ----------------------------------------------------------------------
    section("A malformed request fails loudly instead of wedging the loop")

    ran.clear()
    bad = os.path.join(outbox.OUTBOX, "20990101_000000_corrupt.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{this is not json")
    poll_once()
    check("nothing dispatched", ran, [])
    check("the file is moved out of the poller's path", os.path.exists(bad), False)
    results = harvest(bot.FAILED)
    check("and a failed result records why",
          [r.get("status") for r in results], ["failed"])

    # ----------------------------------------------------------------------
    section("The legacy verbs still route (send, the one every CLI uses)")

    class _LegacyChannel:
        def __init__(self):
            self.id = 555
            self.name = "asd"
            self.posted = []

        async def send(self, content):
            self.posted.append(content)
            return type("M", (), {"id": 7})()

    class _LegacyClient:
        def __init__(self, chan):
            self.chan = chan

        def get_channel(self, cid):
            return self.chan if int(cid) == 555 else None

        async def fetch_channel(self, cid):
            raise RuntimeError(f"no channel {cid}")

    chan = _LegacyChannel()
    _real_client = bot.client
    bot.client = _LegacyClient(chan)
    try:
        outbox.enqueue(channel_id=555, content="hello from the CLI")  # no action = "send"
        poll_once()
    finally:
        bot.client = _real_client
    check("a legacy send reaches the channel", chan.posted, ["hello from the CLI"])
    results = harvest(bot.SENT)
    check("and archives as sent", [r.get("status") for r in results], ["sent"])
finally:
    capabilities.run = _real_run
    confirm.cancel()

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
