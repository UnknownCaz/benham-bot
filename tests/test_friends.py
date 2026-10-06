"""
test_friends.py - friends in a channel, as Caz specified it (INTENT decision 50).

Caz, 2026-10-05: "I was talking to Benham and my friends wanted to jump in. I @
him in the same channel saying he should respond. OR he dms me to ask if he
should wiht the prompt so they dont see the behind the scenes confirmation. Then
after those steps he can @ the people and resond to them for the alloted amount
of turns default is one reply with one closing message in response."

Driven through the real bot.on_message, with a scripted model behind it. In
order of how much it matters:

  A friend's @ never starts a model turn on their say-so. With nothing open,
  Tyler is asked IN HIS DMS and the channel hears nothing.

  Once he says yes - a tap, or Benham pinging them in a reply to his own mention
  - it is one reply and one closing message, then quiet again. The budget is
  the point; it is counted, not hoped for.

  A friend turn is talking only: tool_choice "none", Benham's own note on top
  of the prompt, the friend's words fenced below it. They still cannot make him
  do anything.

  Crowd pings: never from the client by default, and a post that names a crowd
  waits for Tyler's approval - "No not unless I say so."

  The bug that started this: in a server his reply IS the message to the
  channel, approvals go to Tyler's DMs, and a confirmed post that landed right
  here is not followed by a JSON blob.

    python test_friends.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import discord

from benham import bot
from benham.core import agent
from benham.core import capabilities
from benham.core import channelread
from benham.core import confirm
from benham.core import invites
from benham.core import policy

TYLER = 273967061619965952
TESTING = 736988645562646619       # on agent_guilds and post_guilds in the fixture
OFF_LIST = 111000111000111000      # invented; on no list
BOT_ID = 752313060970201218
GENERAL = 600000000000000101       # invented channel ids
OTHER = 600000000000000102
NOW = datetime.now(timezone.utc)

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------------------- stubs

class _User:
    def __init__(self, uid, name, display=None, bot=False):
        self.id = uid
        self.name = name
        self.display_name = display or name
        self.bot = bot
        self.dm_channel = None

    def __str__(self):
        return self.name


BENHAM = _User(BOT_ID, "Benham", bot=True)
CAZ = _User(TYLER, "caz6666")
ALEX = _User(999000111, "alex", "Alex")
SAM = _User(999000222, "sam")


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Guild:
    def __init__(self, gid, name="Test Server"):
        self.id = gid
        self.name = name
        self.text_channels = []
        self.threads = []
        self.default_role = None


class _Sent:
    def __init__(self, channel, content):
        self.id = 1
        self.content = content
        gid = channel.guild.id if channel.guild else "@me"
        self.jump_url = f"https://discord.com/channels/{gid}/{channel.id}/1"

    async def edit(self, **kw):
        self.content = kw.get("content", self.content)


class _Channel:
    def __init__(self, cid, name, guild):
        self.id = cid
        self.name = name
        self.guild = guild
        self.said = []
        self.sent = []
        self.send_kwargs = []

    def history(self, limit=100, before=None):
        msgs = [m for m in self.said
                if before is None or m.created_at < before.created_at]
        msgs = list(reversed(msgs))[:limit]

        async def gen():
            for m in msgs:
                yield m
        return gen()

    def typing(self):
        return _Typing()

    async def send(self, content=None, **kw):
        self.sent.append(content)
        self.send_kwargs.append(kw)
        return _Sent(self, content)

    def __str__(self):
        return self.name


class _Said:
    """A message already in a channel - and, for the newest, the @ itself."""

    def __init__(self, author, content, ago=0.0):
        self.author = author
        self.content = content
        self.clean_content = content
        self.created_at = NOW - timedelta(minutes=ago)
        self.attachments, self.embeds, self.stickers = [], [], []
        self.message_snapshots, self.reactions = [], []
        self.reference = None
        self.poll = None
        self.pinned = False
        self.id = id(self)


class _Mention(_Said):
    """Someone @mentioning Benham - in the channel's history and delivered."""

    def __init__(self, author, text, channel):
        super().__init__(author, f"<@{BOT_ID}> {text}".strip())
        self.clean_content = f"@Benham {text}".strip()
        self.channel = channel
        self.guild = channel.guild
        self.mentions = [BENHAM]
        self.reactions_added = []
        channel.said.append(self)

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class _StubClient:
    def __init__(self):
        self.user = BENHAM
        self.channels = {}

    def get_user(self, uid):
        return CAZ if int(uid) == TYLER else None

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    async def fetch_channel(self, cid):
        ch = self.channels.get(int(cid))
        if ch is None:
            raise capabilities.ActionError(f"no channel {cid}")
        return ch

    def get_guild(self, gid):
        return _Guild(int(gid))


class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Resp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Blk(input_tokens=0, output_tokens=0)


class _Model:
    """The Anthropic client, scripted: answers with the next line, records calls."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.seen = []
        self.messages = self

    def create(self, **kw):
        self.seen.append(kw)
        text = self.texts.pop(0) if self.texts else "ok"
        if isinstance(text, _Resp):
            return text
        return _Resp([_Blk(type="text", text=text)])


owner_turns = []


async def _fake_respond(*args, **kwargs):
    owner_turns.append(kwargs)
    return owner_reply[0], None

owner_reply = ["ok"]

testing = _Guild(TESTING)
general = _Channel(GENERAL, "general", testing)
other = _Channel(OTHER, "announcements", testing)
CAZ.dm_channel = _Channel(555, "dm", None)

bot.client = _StubClient()
bot.client.channels = {GENERAL: general, OTHER: other}
bot.record_message = lambda m: {"is_self": False, "channel": str(m.channel),
                                "author": str(m.author), "content": m.content}
bot.agent.ENABLED = True
_REAL_RESPOND = agent.respond
bot.agent.respond = _fake_respond


def fresh():
    invites.reset()
    bot._views.clear()
    confirm.cancel()
    general.said.clear()
    general.sent.clear()
    other.sent.clear()
    CAZ.dm_channel.sent.clear()
    owner_turns.clear()


def say(author, text, channel=general):
    msg = _Mention(author, text, channel)
    asyncio.run(bot.on_message(msg))
    return msg


def tap(approved, channel=general):
    view = bot._views.get(("invite", channel.id))
    if view is None:
        return False
    asyncio.run(view.on_decide(approved))
    return True


def user_text(call):
    return call["messages"][-1]["content"][0]["text"]


# --------------------------------------------------------------------------
section("A friend's @ starts nothing - Tyler is asked, in his DMs")

fresh()
model = _Model("hey @alex - pineapple on pizza is a crime. @sam back me up.")
agent._client = model
general.said.append(_Said(CAZ, "who wants to argue about pizza", ago=5))
say(ALEX, "settle this: pineapple on pizza?")
check("no model turn on a friend's say-so", model.seen, [])
check("nothing said in the channel", general.sent, [])
check("Tyler got one DM", len(CAZ.dm_channel.sent), 1)
dm = CAZ.dm_channel.sent[0] if CAZ.dm_channel.sent else ""
check("...naming who and quoting what they said",
      "**Alex** @'d me in #general" in dm and "pineapple on pizza?" in dm, True)
check("...and saying the channel can't see it", "Nobody in the channel sees this" in dm, True)
check("the buttons are live", ("invite", GENERAL) in bot._views, True)

say(SAM, "yeah settle it")
check("a second friend while the ask waits does not DM him again",
      len(CAZ.dm_channel.sent), 1)
check("...they are folded into the same ask",
      sorted(invites.pending_ask(GENERAL)["people"].values()), ["Alex", "sam"])


# --------------------------------------------------------------------------
section("He taps Let him answer - one reply, with real pings")

check("the tap lands", tap(True), True)
check("the model was asked once", len(model.seen), 1)
call = model.seen[0] if model.seen else {}
check("...talking only: tool_choice none", call.get("tool_choice"), {"type": "none"})
check("...Benham's own note opens the turn, never a friend's words",
      user_text(call).startswith("[Tyler let me answer Alex, sam here") if call else False,
      True)
check("...and the friends' words are fenced below it",
      "--- recent messages in #general in Test Server [" in user_text(call) if call else False,
      True)
volatile = call["system"][1]["text"] if call else ""
check("...told they are not his owner", "They are NOT your owner" in volatile, True)
check("...told it has no tools", "no tools on this turn" in volatile, True)
check("the reply went to the channel", len(general.sent), 1)
check("...with @names turned into real pings",
      general.sent[0] if general.sent else "",
      "hey <@999000111> - pineapple on pizza is a crime. <@999000222> back me up.")
check("one closing message is left", invites._invites[GENERAL]["left"], 1)
check("the DM ask is resolved", invites.pending_ask(GENERAL), None)


# --------------------------------------------------------------------------
section("Their answer gets ONE closing message, then he goes quiet")

model.texts = ["fair enough @alex, agree to disagree. back to caz."]
say(ALEX, "nah you're wrong")
check("the model was asked again", len(model.seen), 2)
check("...told this is the CLOSING message",
      "CLOSING message" in model.seen[-1]["system"][1]["text"], True)
check("the closing message went out", len(general.sent), 2)
check("the invite is spent", invites.invite_for(GENERAL, ALEX.id), None)

say(ALEX, "wait come back")
check("after the closing message, a friend's @ goes back to asking Tyler",
      len(CAZ.dm_channel.sent), 2)
check("...with no model turn", len(model.seen), 2)


# --------------------------------------------------------------------------
section("Ignore means no reply - and no new asks for a while")

check("the ignore lands", tap(False), True)
check("nothing was said", len(general.sent), 2)
say(SAM, "benham??")
check("no new DM right after an Ignore", len(CAZ.dm_channel.sent), 2)
check("...and still no model turn", len(model.seen), 2)


# --------------------------------------------------------------------------
section("A server off agent_guilds: friends' @s reach nothing at all")

fresh()
offlist = _Channel(600000000000000199, "general", _Guild(OFF_LIST))
bot.client.channels[offlist.id] = offlist
say(ALEX, "hello?", channel=offlist)
check("no DM", CAZ.dm_channel.sent, [])
check("no reply", offlist.sent, [])
check("no ask", invites.pending_ask(offlist.id), None)


# --------------------------------------------------------------------------
section("Tyler's own go-ahead in the channel counts as the tap")

fresh()
model = _Model("ok ok, @alex: closing thought - pineapple stays banned.")
agent._client = model
general.said += [_Said(ALEX, "benham where'd you go", ago=3),
                 _Said(SAM, "lol", ago=2)]
say(ALEX, "you there?")                       # an ask is waiting in his DMs
check("an ask is waiting", invites.pending_ask(GENERAL) is not None, True)
owner_reply[0] = "@alex @sam sorry, was out - what'd I miss?"
say(CAZ, "go ahead and respond to them")
check("his own mention ran a normal owner turn", len(owner_turns), 1)
check("the reply pinged them for real",
      general.sent[-1] if general.sent else "",
      "<@999000111> <@999000222> sorry, was out - what'd I miss?")
inv = invites._invites.get(GENERAL)
check("...which let them in for one closing message",
      (sorted(inv["people"]), inv["left"]) if inv else None,
      (sorted([ALEX.id, SAM.id]), 1))
check("...and answered the waiting DM ask", invites.pending_ask(GENERAL), None)
check("...whose buttons retired", ("invite", GENERAL) in bot._views, False)

say(ALEX, "the pizza debate")
check("their answer got the closing message", len(model.seen), 1)
check("...and the exchange is over", invites.invite_for(GENERAL, SAM.id), None)
owner_reply[0] = "ok"


# --------------------------------------------------------------------------
section("A friend turn cannot act, even if the model reaches for a tool")

fresh()
ran = []
real_run = capabilities.run


async def watched_run(*a, **kw):
    ran.append(a[2] if len(a) > 2 else kw.get("name"))
    return await real_run(*a, **kw)

capabilities.run = watched_run
model = _Model(_Resp([_Blk(type="tool_use", id="t1", name="send_message",
                           input={"channel_id": OTHER, "content": "@everyone hi"})],
                     "tool_use"))
agent._client = model
invites.open_invite(GENERAL, {ALEX.id: "Alex"}, 2, by="tap")
try:
    say(ALEX, "post @everyone hi in #announcements for me")
finally:
    capabilities.run = real_run
check("no capability ran", ran, [])
check("nothing was posted anywhere", (general.sent, other.sent), ([], []))


# --------------------------------------------------------------------------
section("Crowd pings - never by default, only on Tyler's say-so")

check("the client blocks @everyone", bot.ALLOWED_MENTIONS.everyone, False)
check("...and roles", bot.ALLOWED_MENTIONS.roles, False)
check("...but one person can still be pinged", bot.ALLOWED_MENTIONS.users, True)
check("@everyone names a crowd", policy.names_a_crowd({"content": "hi @everyone"}), True)
check("@here names a crowd", policy.names_a_crowd({"content": "@here look"}), True)
check("a role mention names a crowd", policy.names_a_crowd({"content": "<@&123> up"}), True)
check("one person does not", policy.names_a_crowd({"content": "<@123> hi"}), False)
check("a word that starts with it does not",
      policy.names_a_crowd({"content": "@everyones_fave"}), False)


async def _post(content, force):
    other.sent.clear()
    other.send_kwargs.clear()
    return await capabilities.run(
        bot.client, lambda *_: None, "send_message",
        {"channel_id": OTHER, "content": content}, actor_id=TYLER, force=force,
        call_ctx=policy.CallContext.owner_dm(TYLER, 555))

res, preview = asyncio.run(_post("@everyone dinner's ready", force=False))
check("a clean, owner-asked post naming a crowd still waits for him",
      (res, preview is not None, other.sent), (None, True, []))
check("...and says why", "only happens when Tyler says so" in (preview or {}).get("reason", ""),
      True)
res, preview = asyncio.run(_post("@everyone dinner's ready", force=True))
check("once he confirms, it goes", other.sent, ["@everyone dinner's ready"])
check("...with crowd pings unlocked for that one message",
      isinstance(other.send_kwargs[-1].get("allowed_mentions"), discord.AllowedMentions)
      and other.send_kwargs[-1]["allowed_mentions"].everyone, True)
res, preview = asyncio.run(_post("plain hello", force=False))
check("an ordinary post is untouched", (other.sent, "allowed_mentions" in other.send_kwargs[-1]),
      (["plain hello"], False))


# --------------------------------------------------------------------------
section("The bug that started it: his reply IS the message to the channel")

_static, vol = agent._system_blocks("#general in Test Server", "caz6666",
                                    channel_id=GENERAL, guild_id=TESTING)
check("he knows where he is, by id", f"channel_id {GENERAL}, guild_id {TESTING}" in vol, True)
check("...that his reply goes to everyone here", "to everyone in the channel" in vol, True)
check("...and send_message is for somewhere else", "send_message is for posting somewhere ELSE" in vol,
      True)
_static, vol = agent._system_blocks("a DM", "caz6666")
check("a DM gets none of that", agent.SERVER_NOTE in vol, False)


async def _fire(target):
    p = confirm.park("send_message", {"channel_id": target.id, "content": "hi all"},
                     {"summary": "post"}, TYLER, "dm",
                     call_ctx=policy.CallContext.owner_guild(TYLER, TESTING, GENERAL))
    await bot.fire_confirmed(confirm.consume(p.token), general)

fresh()
asyncio.run(_fire(general))
check("a confirmed post that landed right here is not followed by a 'Done' blob",
      general.sent, ["hi all"])
fresh()
asyncio.run(_fire(other))
check("one that went elsewhere says where, as a link - not JSON",
      general.sent, [f"Done — `send_message`: https://discord.com/channels/{TESTING}/{OTHER}/1"])


# --------------------------------------------------------------------------
section("apply_pings only reaches people in the read")

people = {"alex": (1, "Alex"), "sam": (2, "sam")}
check("a name at the end of a sentence",
      channelread.apply_pings("ask @alex.", people), ("ask <@1>.", {1: "Alex"}))
check("an email address is left alone",
      channelread.apply_pings("mail me@alex.com", people)[0], "mail me@alex.com")
check("someone not in the chat is left as text",
      channelread.apply_pings("@bob hi", people), ("@bob hi", {}))
check("@everyone is never a person", channelread.apply_pings("@everyone", people),
      ("@everyone", {}))


agent._client = None
bot.agent.respond = _REAL_RESPOND
print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
