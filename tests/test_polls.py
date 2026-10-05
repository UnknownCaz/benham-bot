"""
test_polls.py - the native poll, offline: posting one, reading one, ending one.

send_poll hands Discord an object rather than a string, so the two ways it can
go wrong are both quiet: a limit Discord enforces (10 answers, 55 characters
each, 300 for the question, 32 days) comes back as a bare 400 that names
nothing, and a poll that DID post cannot be edited - a wrong timer or a missing
answer is a delete and a second notification. So the limits are checked before
anything is sent, and this file pins both halves: what reaches the channel, and
that a refusal reaches nothing.

Reading is the other quiet one. A poll message has empty content, so until
msg_dict learned about polls a read reported one as a blank line - the votes
were on the message and nothing surfaced them.

    python test_polls.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package and bot.py live.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import discord

from benham.core import capabilities
from benham.core import policy

TESTING, ASD = 736988645562646619, 809357286036078612
OUTSIDE_GUILD, OUTSIDE_CHANNEL = 4040404040404040404, 999
BOT_ID, SOMEONE_ELSE = 1004, 1005

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------------- stubs

class _StubGuild:
    def __init__(self, gid):
        self.id = gid


class _StubSent:
    id = 4242
    jump_url = "https://discord.test/jump/4242"


class _StubChannel:
    def __init__(self, cid, gid):
        self.id = cid
        self.guild = _StubGuild(gid)
        self.sent = []
        self.messages = {}

    async def send(self, *args, **kw):
        self.sent.append((args, kw))
        return _StubSent()

    async def fetch_message(self, mid):
        return self.messages[int(mid)]

    def __str__(self):
        return "asd"


class _StubUser:
    def __init__(self, uid, name):
        self.id = uid
        self.name = name

    def __str__(self):
        return self.name


class _StubAnswer:
    def __init__(self, text, votes):
        self.text = text
        self.vote_count = votes


class _StubPoll:
    """A poll as it comes back on a fetched message - votes and all.

    A real discord.Poll built here would be stateless: no votes, no expiry, and
    an end() that raises. What a read has to survive is the fetched shape.
    """

    def __init__(self, votes, finalized=False):
        self.question = "Which name?"
        self.answers = [_StubAnswer(t, n) for t, n in votes]
        self.total_votes = sum(n for _, n in votes)
        self.multiple = False
        self.expires_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        self._finalized = finalized
        self.ended = 0

    def is_finalised(self):
        return self._finalized

    async def end(self):
        self.ended += 1
        self._finalized = True
        return self


class _StubMessage:
    def __init__(self, mid, channel, author, poll=None, content=""):
        self.id = mid
        self.channel = channel
        self.author = author
        self.content = content
        self.poll = poll
        self.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.attachments, self.embeds, self.reactions = [], [], []
        self.pinned = False
        self.reference = None


class _StubClient:
    def __init__(self):
        self.user = _StubUser(BOT_ID, "benham-bot")
        self.channels = {ASD: _StubChannel(ASD, TESTING),
                         OUTSIDE_CHANNEL: _StubChannel(OUTSIDE_CHANNEL, OUTSIDE_GUILD)}

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    def add_message(self, mid, author_id, poll=None, content=""):
        ch = self.channels[ASD]
        ch.messages[mid] = _StubMessage(mid, ch, _StubUser(author_id, "someone"),
                                        poll=poll, content=content)
        return ch.messages[mid]


def run(client, _action="send_poll", **params):
    result, _pending = asyncio.run(capabilities.run(
        client, lambda *_: None, _action, params,
        call_ctx=policy.CallContext.local()))
    return result


def refusal(client, _action="send_poll", **params):
    try:
        run(client, _action, **params)
    except capabilities.ActionError as e:
        return str(e)
    return None


NAMES = ["Alpha", "Bravo", "Charlie"]

# ------------------------------------------------------------ what gets sent
section("A poll reaches the channel as a poll")
c = _StubClient()
r = run(c, channel_id=ASD, question="  Which name?  ", answers=NAMES)
args, kw = c.channels[ASD].sent[0]
poll = kw["poll"]
check("exactly one message", len(c.channels[ASD].sent), 1)
check("nothing rides beside the poll", (args, sorted(kw)), ((), ["poll"]))
check("the question, trimmed", poll.question, "Which name?")
check("every answer, in order", [a.text for a in poll.answers], NAMES)
check("24 hours when no timer is given", poll.duration, timedelta(hours=24))
check("one vote each by default", poll.multiple, False)
check("the result says what was posted",
      (r["status"], r["message_id"], r["answers"], r["hours"], r["jump_url"]),
      ("sent", 4242, NAMES, 24, "https://discord.test/jump/4242"))

section("Timer, multi-pick and emoji")
c = _StubClient()
run(c, channel_id=ASD, question="Q", hours=1, multiple=True,
    answers=[{"text": "Alpha", "emoji": "\N{FIRE}"}, "Bravo"])
poll = c.channels[ASD].sent[0][1]["poll"]
check("the timer is the one asked for", poll.duration, timedelta(hours=1))
check("multi-pick when asked", poll.multiple, True)
check("{text, emoji} and a bare string mix", [a.text for a in poll.answers],
      ["Alpha", "Bravo"])
check("the emoji lands on its answer", str(poll.answers[0].emoji), "\N{FIRE}")
check("a bare string carries no emoji", poll.answers[1].emoji, None)
c = _StubClient()
run(c, channel_id=ASD, question="Q", answers=NAMES, hours=768)
check("32 days is the ceiling and is allowed",
      c.channels[ASD].sent[0][1]["poll"].duration, timedelta(days=32))

# -------------------------------------------------------------- the refusals
section("A poll Discord would reject is refused before anything is sent")
c = _StubClient()
for label, params, names in (
    ("one answer", dict(answers=["only"]), "2-10 answers, got 1"),
    ("eleven answers", dict(answers=[f"a{i}" for i in range(11)]), "2-10 answers, got 11"),
    ("a bare string is ONE answer, not its letters", dict(answers="Alpha"),
     "2-10 answers, got 1"),
    ("a 56-character answer", dict(answers=["ok", "x" * 56]), "1-55 characters, got 56"),
    ("a blank answer", dict(answers=["ok", "   "]), "1-55 characters, got 0"),
    ("an answer object with no text", dict(answers=["ok", {"emoji": "\N{FIRE}"}]),
     "1-55 characters, got 0"),
    ("a blank question", dict(question="   "), "1-300 characters, got 0"),
    ("a 301-character question", dict(question="q" * 301), "1-300 characters, got 301"),
    ("zero hours", dict(hours=0), "hours must be 1-768, got 0"),
    ("33 days", dict(hours=769), "hours must be 1-768, got 769"),
):
    full = {"channel_id": ASD, "question": "Q", "answers": NAMES, **params}
    check(f"{label} is refused, and the refusal names it",
          names in (refusal(c, **full) or ""), True)
check("none of those sent anything", c.channels[ASD].sent, [])
check("a missing question is refused by name",
      "needs `question`" in (refusal(c, channel_id=ASD, answers=NAMES) or ""), True)
check("missing answers are refused by name",
      "needs `answers`" in (refusal(c, channel_id=ASD, question="Q") or ""), True)

# ------------------------------------------------------- the gates it inherits
section("A poll is content, so it wears send_message's gates")
act, msg = capabilities.REGISTRY["send_poll"], capabilities.REGISTRY["send_message"]
check("same tier, outward and posting flags as send_message",
      (act.tier, act.outward, act.posts, act.guest, act.origins),
      (msg.tier, msg.outward, msg.posts, msg.guest, msg.origins))
c = _StubClient()
check("refused outside the posting scope",
      "post" in (refusal(c, channel_id=OUTSIDE_CHANNEL, question="Q", answers=NAMES) or "").lower(),
      True)
check("...and nothing was sent there", c.channels[OUTSIDE_CHANNEL].sent, [])
tainted = policy.CallContext.owner_dm(273967061619965952, 111, tainted=True)
_res, pending = asyncio.run(capabilities.run(
    c, lambda *_: None, "send_poll",
    {"channel_id": ASD, "question": "Which name?", "answers": NAMES}, call_ctx=tainted))
check("a tainted turn parks it for a yes instead of posting",
      (_res, c.channels[ASD].sent), (None, []))
check("the parked preview shows the question", "Which name?" in pending["detail"], True)

# ------------------------------------------------------------ reading a poll
section("A read shows a poll's votes instead of a blank line")
c = _StubClient()
c.add_message(1, BOT_ID, poll=_StubPoll([("Alpha", 3), ("Bravo", 1), ("Charlie", 0)]))
got = run(c, "get_message", channel_id=ASD, message_id=1)["poll"]
check("the question", got["question"], "Which name?")
check("every answer with its votes", got["answers"],
      [{"text": "Alpha", "votes": 3}, {"text": "Bravo", "votes": 1},
       {"text": "Charlie", "votes": 0}])
check("the total", got["total_votes"], 4)
check("who leads", got["leading"], ["Alpha"])
check("a running poll says its numbers are not final", got["finalized"], False)
check("when it closes", got["expires_at"], "2026-01-02T03:04:00+00:00")
c.add_message(2, BOT_ID, poll=_StubPoll([("Alpha", 2), ("Bravo", 2), ("Charlie", 1)],
                                        finalized=True))
got = run(c, "get_message", channel_id=ASD, message_id=2)["poll"]
check("a tie names both, never just the first", got["leading"], ["Alpha", "Bravo"])
check("a closed poll says its numbers are final", got["finalized"], True)
c.add_message(3, BOT_ID, poll=_StubPoll([("Alpha", 0), ("Bravo", 0)]))
check("nobody leads a poll nobody voted in",
      run(c, "get_message", channel_id=ASD, message_id=3)["poll"]["leading"], [])
c.add_message(4, SOMEONE_ELSE, content="hello")
check("an ordinary message keeps its shape - no poll key",
      "poll" in run(c, "get_message", channel_id=ASD, message_id=4), False)
real = discord.Poll(question="Q", duration=timedelta(hours=1)).add_answer(text="Alpha")
check("the library's own poll object reads too (unsent: no votes, no expiry)",
      capabilities.poll_dict(real),
      {"question": "Q", "answers": [{"text": "Alpha", "votes": 0}], "total_votes": 0,
       "leading": [], "multiple": False, "expires_at": None, "finalized": False})

# ------------------------------------------------------------- ending a poll
section("end_poll closes this bot's own poll, and only that")
c = _StubClient()
mine = c.add_message(1, BOT_ID, poll=_StubPoll([("Alpha", 1), ("Bravo", 0)]))
r = run(c, "end_poll", channel_id=ASD, message_id=1)
check("it ends the poll once", mine.poll.ended, 1)
check("the result carries the final count",
      (r["status"], r["poll"]["finalized"], r["poll"]["leading"]),
      ("ended", True, ["Alpha"]))
theirs = c.add_message(2, SOMEONE_ELSE, poll=_StubPoll([("Alpha", 1), ("Bravo", 0)]))
check("someone else's poll is refused",
      "its own polls" in (refusal(c, "end_poll", channel_id=ASD, message_id=2) or ""), True)
check("...and left running", theirs.poll.ended, 0)
c.add_message(3, BOT_ID, content="not a poll")
check("a message that is not a poll is refused by name",
      "is not a poll" in (refusal(c, "end_poll", channel_id=ASD, message_id=3) or ""), True)
act, edit = capabilities.REGISTRY["end_poll"], capabilities.REGISTRY["edit_message"]
check("same tier and outward flag as edit_message - it changes what people see",
      (act.tier, act.outward, act.posts, act.guest, act.origins),
      (edit.tier, edit.outward, edit.posts, edit.guest, edit.origins))

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
