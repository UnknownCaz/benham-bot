"""
test_conversation_tick.py - the nudge timer, driven the way the gateway drives it.

Written for c34 (2026-09-12). The courier found an open conversation reading
`nudges: 0` eleven hours after `conv show` said its first nudge was due, saw a
fresh gateway login inside the same pid between two of its wakes, and asked
whether nudge timers survive a reconnect. It filed that as a hypothesis, and it
was the right question. Both halves of the answer came back "not here":

  THE TIMER. tick_conversations is a discord.ext.tasks loop that on_ready starts
  behind an is_running() guard. A reconnect that re-identifies fires on_ready
  again - the Mac's log carries the whole boot banner a second time at 11:05:00Z
  - and the guard leaves the running loop alone. The same process had nudged c33
  on schedule five days earlier. But nothing in the suite had ever run that path,
  so "the guard handles it" was a reading of the code, not a fact. This file runs
  the REAL on_ready, twice, against the REAL loop object, and watches due nudges
  fire on both sides of it - and after a stopped loop is started again.

  THE DEADLINE. c34 was an UNPROMPTED question, which never chases by design
  (INTENT stage 6, decision 29). Its due_at was a stamp mark_delivered wrote for
  every direction. conversations.chases() owns that rule now, and a section here
  pins the words `conv show` prints instead of a time.

What the question DID find was one step to the side. A beat that came due while
Discord was unreachable - the Mac's gateway was down 08:40-11:05Z on DNS that same
day - raised a network error out of capabilities.run, and the tick's catch-all
banked the question on the spot: nudge budget unspent, nothing sent, and a grace
window that closes long before the connection comes back. That catch-all was
written for a person whose DMs are closed. Discord being unreachable is not that,
so the beat now stays due and the next tick retries it; the outage sections pin
the retry AND the refusal that still banks.

Everything runs against _testconfig's scratch state and a stand-in for the
gateway client: no Discord connection, no real store.

    python test_conversation_tick.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - fixture config + scratch state; must precede benham imports

import asyncio
import contextlib
import io
import sys
from datetime import timedelta

import aiohttp
import discord

from benham import bot
from benham.cli import conv as conv_cli
from benham.core import conversations as C
from benham.core import identity
from benham.core.capabilities import ActionError

# Invented and belonging to nobody: a real person's id in an assertion is a
# machine-specific test, and the tracked tree carries none.
COLLAB = 777000777000777000
OWNER = sorted(identity.OWNER_IDS)[0]

# The loop is 60 seconds in production. Nothing under test depends on the number,
# and a test that waits a real minute per beat is a test nobody runs.
FAST = 0.05

_fails = []
SENT = []       # (uid, content) for every send that landed. Never cleared: the c34
                # checks at the end read the whole run.
ATTEMPTS = []   # uid for every send tried while Discord was unreachable
LOGS = []       # everything bot.log() was handed, so a check can read the log the
                # way a person reading benham.log would
# While set, every DM send raises it - Discord unreachable, from the bot's side.
UNREACHABLE = {"exc": None}


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


class _Msg:
    """What channel.send returns: a message with an id. The nudge path reads .id
    off it to make the nudge repliable, so a stub returning None would hide that."""
    _seq = [9000]

    def __init__(self, content):
        _Msg._seq[0] += 1
        self.id = _Msg._seq[0]
        self.content = content


class _DM:
    """One channel per user, kept across beats."""
    _by_uid = {}

    def __init__(self, uid):
        self.uid = uid

    @classmethod
    def of(cls, uid):
        return cls._by_uid.setdefault(int(uid), cls(int(uid)))

    async def send(self, content=None, **kw):
        if UNREACHABLE["exc"] is not None:
            ATTEMPTS.append(self.uid)
            raise UNREACHABLE["exc"]
        SENT.append((self.uid, content))
        return _Msg(content)


class _User:
    def __init__(self, uid):
        self.id = int(uid)
        self.dm_channel = _DM.of(uid)

    async def create_dm(self):
        return self.dm_channel


class _Me:
    id = 424242424242424242

    def __str__(self):
        return "Benham#0000"


class _Gateway:
    """The slice of discord.Client that on_ready and the loops it starts touch.

    Kept to exactly that slice on purpose: anything else they reach for raises
    AttributeError here rather than being quietly satisfied. The ask queue's
    delivery bugs lived for a day behind a stub that said yes to everything.
    """
    user = _Me()
    guilds = []

    async def wait_until_ready(self):
        # poll_outbox's before_loop awaits this. The real one returns once READY
        # has arrived, and on_ready running at all means it has.
        return None

    def get_user(self, uid):
        return _User(uid)

    async def fetch_user(self, uid):
        return _User(uid)

    async def change_presence(self, **kw):
        pass


class _Response:
    """What discord.HTTPException reads off a response: .status and .reason."""

    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def _http(cls, status, reason, code=0, text=""):
    """A discord HTTP error, built the way discord.py builds one from a response."""
    return cls(_Response(status, reason), {"code": code, "message": text})


def make_due(cid):
    """Wind a deadline back so the next beat is genuinely due - the same move
    test_conversations makes, because a beat runs through capabilities.run and
    takes no clock."""
    C._mutate(cid, lambda cv: cv.__setitem__(
        "due_at", C._iso(C._now() - timedelta(seconds=1))))


def ask(question):
    """An ask already on its counterparty's screen, with its deadline passed."""
    cid = C.open_conversation(COLLAB, "tick test", question)["id"]
    C.mark_delivered(cid)
    make_due(cid)
    return cid


def nudges(cid):
    return int(C.get(cid).get("nudges", 0))


def nudge_messages(question):
    return [c for _, c in SENT
            if c and c.startswith("still after this one") and question in c]


async def settle(pred, timeout=5.0):
    """Poll until pred() holds or the timeout passes; returns whether it held.

    Polling rather than sleeping a fixed number of beats: the loop runs on the
    event loop's clock, and a fixed sleep either wastes seconds or flakes."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return bool(pred())


async def scenario():
    tick = bot.tick_conversations

    section("First READY: on_ready starts the tick, and a due nudge fires")
    a1 = ask("did the fix land?")
    # c34 exactly as it sits on disk: unprompted, delivered, and carrying a due_at
    # stamped before chases() existed - long past.
    un = C.open_conversation(OWNER, "curious", "how did the test go?",
                             direction=C.UNPROMPTED, priority=C.WHENEVER)["id"]
    C.mark_delivered(un)
    C._mutate(un, lambda cv: cv.__setitem__(
        "due_at", C._iso(C._now() - timedelta(hours=11))))

    await bot.on_ready()
    first = tick.get_task()
    check("the tick is running after the first READY", tick.is_running(), True)
    check("a due nudge fires from the live loop",
          await settle(lambda: nudges(a1) == 1), True)

    section("READY again, as a gateway reconnect sends it: no restart, no second loop")
    try:
        await bot.on_ready()
        survived = True
    except RuntimeError:  # Loop.start() on a loop that is already running
        survived = False
    check("on_ready survives a second READY", survived, True)
    check("...and the loop running is the SAME task as before",
          tick.get_task() is first and tick.is_running(), True)
    make_due(a1)
    check("a nudge that comes due after the reconnect fires",
          await settle(lambda: nudges(a1) == 2), True)
    await asyncio.sleep(FAST * 6)
    check("...exactly once per beat - two nudges, two messages",
          len(nudge_messages("did the fix land?")), 2)

    section("A tick that stopped is started again by the next READY")
    tick.cancel()
    check("the loop can be stopped", await settle(lambda: not tick.is_running()), True)
    a2 = ask("and the second fix?")
    await asyncio.sleep(FAST * 6)
    check("a stopped loop fires nothing - so this file is watching the real loop",
          nudges(a2), 0)
    await bot.on_ready()
    check("the next READY starts a fresh one",
          tick.is_running() and tick.get_task() is not first, True)
    check("...and the beat that came due meanwhile fires",
          await settle(lambda: nudges(a2) == 1), True)

    section("Discord unreachable when a beat comes due: the beat waits, it does not bank")
    a3 = ask("did the third fix land?")
    # The Mac's own failure from 2026-09-12 08:40Z, errno and all.
    UNREACHABLE["exc"] = aiohttp.ClientOSError(
        8, "nodename nor servname provided, or not known")
    check("the tick keeps trying while Discord is unreachable",
          await settle(lambda: ATTEMPTS.count(COLLAB) >= 3), True)
    check("...without banking the question", C.get(a3)["state"], C.OPEN)
    check("...or spending its nudge budget", nudges(a3), 0)
    check("the log says Discord was unreachable and the beat is still due",
          any(line.startswith(f"conversation {a3}: Discord unreachable")
              and line.endswith("still due, the next tick retries it")
              for line in LOGS), True)
    UNREACHABLE["exc"] = None
    await bot.on_ready()  # the READY that ends the outage
    check("once Discord answers, the beat that came due during the outage fires",
          await settle(lambda: nudges(a3) == 1), True)

    section("A 5xx is Discord's outage too; a closed DM is the person's door")
    a4 = ask("did the fourth fix land?")
    before = len(ATTEMPTS)
    UNREACHABLE["exc"] = _http(discord.DiscordServerError, 503, "Service Unavailable")
    check("a 5xx is retried, though capabilities.run hands it back as an ActionError",
          await settle(lambda: len(ATTEMPTS) >= before + 3), True)
    check("...and the question is not banked", C.get(a4)["state"], C.OPEN)
    UNREACHABLE["exc"] = _http(discord.Forbidden, 403, "Forbidden", code=50007,
                               text="Cannot send messages to this user")
    check("a closed DM still banks, exactly as it always did",
          await settle(lambda: C.get(a4)["state"] == C.BANKED), True)
    check("...with the refusal on its record",
          "could not deliver" in (C.get(a4)["log"][-1].get("detail") or ""), True)
    UNREACHABLE["exc"] = None

    section("c34's shape: the unprompted question was never chased through any of it")
    check("nothing was ever sent to the owner",
          [c for uid, c in SENT if uid == OWNER], [])
    check("its record still reads zero nudges", nudges(un), 0)
    check("...and it is still open - the tick did not bank it either",
          C.get(un)["state"], C.OPEN)
    check("the tick never logged a beat for it",
          any(line.startswith((f"conversation {un}:", f"conversation {un} "))
              for line in LOGS), False)
    return a1, un


def words(a1, un):
    section("`conv show` prints no due time for a question that never chases")

    def show(cid):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = conv_cli.main(["show", cid])
        return rc, out.getvalue()

    rc, text = show(un)
    check("conv show runs on it", rc, 0)
    check("it says the question never chases",
          "  nudges  : never - unprompted: Claude asked on its own, so it never chases"
          in text, True)
    check("...and prints no due time at all - not even the stale one on its record",
          "due:" in text, False)
    rc, text = show(a1)
    check("an ask still prints its count and its deadline, word for word",
          f"  nudges  : 2   due: {C.get(a1)['due_at']}\n" in text, True)


def classifier():
    section("What counts as Discord being unreachable")

    def rewrapped(inner):
        """What capabilities.run hands back for any discord.HTTPException."""
        try:
            try:
                raise inner
            except discord.HTTPException as e:
                raise ActionError(f"Discord rejected `advance_conversation`: {e}")
        except ActionError as outer:
            return outer

    unreachable = bot._discord_unreachable
    check("DNS failing - the Mac's 09-12 error",
          unreachable(aiohttp.ClientOSError(
              8, "nodename nor servname provided, or not known")), True)
    check("a connection reset",
          unreachable(ConnectionResetError(54, "Connection reset by peer")), True)
    check("a timeout", unreachable(asyncio.TimeoutError()), True)
    check("a 5xx, even rewrapped as an ActionError",
          unreachable(rewrapped(_http(discord.DiscordServerError, 502, "Bad Gateway"))),
          True)
    check("NOT a closed DM, rewrapped the same way",
          unreachable(rewrapped(_http(discord.Forbidden, 403, "Forbidden", code=50007))),
          False)
    check("NOT an unknown user", unreachable(ActionError("no Discord user with id 1")),
          False)
    check("NOT a full disk - once a nudge has gone out, a retry would resend it "
          "every minute", unreachable(OSError(28, "No space left on device")), False)
    check("NOT a store it may not write",
          unreachable(PermissionError(13, "Permission denied")), False)


def main():
    real_client, real_log = bot.client, bot.log
    bot.client = _Gateway()
    bot.log = LOGS.append
    bot.tick_conversations.change_interval(seconds=FAST)
    C.forget()

    async def run():
        try:
            return await scenario()
        finally:
            # on_ready started both loops. asyncio.run would cancel them at
            # shutdown regardless; stopping them here keeps the teardown in view.
            UNREACHABLE["exc"] = None
            bot.tick_conversations.cancel()
            bot.poll_outbox.cancel()
            await asyncio.sleep(FAST * 2)

    try:
        a1, un = asyncio.run(run())
        words(a1, un)
        classifier()
    finally:
        bot.tick_conversations.change_interval(seconds=60)
        bot.client, bot.log = real_client, real_log
        C.forget()

    if _fails:
        print("\nlast log lines, for the failure above:")
        for line in LOGS[-15:]:
            print(f"    {line}")
    print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
