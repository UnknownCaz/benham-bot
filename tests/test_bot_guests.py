"""
test_bot_guests.py - a bot guest in one channel (INTENT decision 53).

Caz, 2026-10-17: "i want to test and establish this as a line she can take to
you so you two can talk about the move itself between yourselves." Hermes
(another bot) may @ Benham in #kestra and get a chat-only reply without Tyler
being asked first - in that channel, for that account, and nowhere else.

Driven through the real bot.on_message with a scripted model, like
test_friends.py. What it pins, in order of how much it matters:

  The scope. Only the listed account, only in the listed channel, only in a
  guild on agent_guilds. Anyone else there, or Hermes anywhere else, takes the
  ordinary decision-50 friend path (Tyler is asked in DMs) - unchanged.

  The default. No `bot_guests` in control.json = nobody is a bot guest.

  The reply. Talking only (tool_choice none), and it OPENS with the inline
  mention Hermes's own gate needs - added once, never doubled.

  The cap. At most botguests.CAP replies per rolling hour per (channel,
  account); past it, silence and no model call. An hour later it reopens.

    python test_bot_guests.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone

from benham import bot
from benham.core import agent
from benham.core import botguests
from benham.core import confirm
from benham.core import identity
from benham.core import invites
from benham.core import policy

TYLER = 273967061619965952
TESTING = 736988645562646619       # on agent_guilds in the fixture
OFF_LIST = 111000111000111000      # invented; on no list
BOT_ID = 752313060970201218
HERMES_ID = 777000777000777001     # invented - the real id lives only in control.json
KESTRA = 600000000000000301        # invented channel ids
GENERAL = 600000000000000302
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
    def __init__(self, uid, name, bot=False):
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = bot
        self.dm_channel = None

    def __str__(self):
        return self.name


BENHAM = _User(BOT_ID, "Benham", bot=True)
CAZ = _User(TYLER, "caz6666")
HERMES = _User(HERMES_ID, "HERMES_AGENT", bot=True)
ALEX = _User(999000111, "alex")


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


class _Channel:
    def __init__(self, cid, name, guild):
        self.id = cid
        self.name = name
        self.guild = guild
        self.said = []
        self.sent = []

    def history(self, limit=100, before=None):
        msgs = list(reversed(self.said))[:limit]

        async def gen():
            for m in msgs:
                yield m
        return gen()

    def typing(self):
        return _Typing()

    async def send(self, content=None, **kw):
        self.sent.append(content)
        return _Sent(self, content)

    def __str__(self):
        return self.name


class _Mention:
    def __init__(self, author, text, channel):
        self.author = author
        self.content = f"<@{BOT_ID}> {text}".strip()
        self.clean_content = f"@Benham {text}".strip()
        self.created_at = NOW
        self.attachments, self.embeds, self.stickers = [], [], []
        self.message_snapshots, self.reactions = [], []
        self.reference = None
        self.poll = None
        self.pinned = False
        self.id = id(self)
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

    def get_guild(self, gid):
        return _Guild(int(gid))


class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Resp:
    def __init__(self, text):
        self.content = [_Blk(type="text", text=text)]
        self.stop_reason = "end_turn"
        self.usage = _Blk(input_tokens=0, output_tokens=0)


class _Model:
    def __init__(self, text="hey Hermes - Benham here."):
        self.text = text
        self.seen = []
        self.messages = self

    def create(self, **kw):
        self.seen.append(kw)
        return _Resp(self.text)


testing = _Guild(TESTING)
offlist = _Guild(OFF_LIST, "Elsewhere")
kestra = _Channel(KESTRA, "kestra", testing)
general = _Channel(GENERAL, "general", testing)
off_kestra = _Channel(KESTRA, "kestra", offlist)   # same channel id, guild off the list
CAZ.dm_channel = _Channel(555, "dm", None)

bot.client = _StubClient()
bot.client.channels = {KESTRA: kestra, GENERAL: general}
bot.record_message = lambda m: {"is_self": False, "channel": str(m.channel),
                                "author": str(m.author), "content": m.content}
bot.agent.ENABLED = True

GRANT = {"hermes": {"id": HERMES_ID, "channels": [KESTRA]}}


def grant(value=GRANT):
    if value is None:
        identity.CONTROL.pop("bot_guests", None)
    else:
        identity.CONTROL["bot_guests"] = value


def fresh(text="hey Hermes - Benham here."):
    invites.reset()
    botguests.reset()
    bot._views.clear()
    confirm.cancel()
    for ch in (kestra, general, off_kestra, CAZ.dm_channel):
        ch.said.clear()
        ch.sent.clear()
    model = _Model(text)
    agent._client = model
    return model


def say(author, text, channel):
    msg = _Mention(author, text, channel)
    asyncio.run(bot.on_message(msg))
    return msg


# --------------------------------------------------------------------------
section("Config: bot_guests parses two shapes and grants nothing by accident")

check("named form", identity.bot_guest_map(GRANT), {HERMES_ID: frozenset({KESTRA})})
check("bare id form", identity.bot_guest_map({str(HERMES_ID): [KESTRA]}),
      {HERMES_ID: frozenset({KESTRA})})
check("absent -> nothing", identity.bot_guest_map(None), {})
check("no channels -> nothing", identity.bot_guest_map({"hermes": {"id": HERMES_ID,
                                                                  "channels": []}}), {})
check("a bare channel id (not a list) -> nothing",
      identity.bot_guest_map({str(HERMES_ID): KESTRA}), {})
check("garbage id -> nothing", identity.bot_guest_map({"x": {"id": "abc",
                                                             "channels": [KESTRA]}}), {})
grant(None)
check("default config: Hermes is a bot guest nowhere",
      identity.bot_guest_channels(HERMES_ID), frozenset())
grant({"me": {"id": TYLER, "channels": [KESTRA]}})
check("an owner listed as a bot guest is never one",
      identity.bot_guest_channels(TYLER), frozenset())


# --------------------------------------------------------------------------
section("Policy: one account, one channel, an agent guild - everything else DENY")

grant()
may = policy.may_answer_bot_guest
check("Hermes in #kestra (agent guild)", may(HERMES_ID, TESTING, KESTRA).allowed, True)
check("Hermes in another channel of the same guild",
      may(HERMES_ID, TESTING, GENERAL).rule, "bot_guest_scope")
check("someone else in #kestra", may(ALEX.id, TESTING, KESTRA).rule, "bot_guest_scope")
check("a DM (no guild)", may(HERMES_ID, None, KESTRA).rule, "bot_guest_context")
check("the channel id in a guild off agent_guilds",
      may(HERMES_ID, OFF_LIST, KESTRA).rule, "bot_guest_guild")
check("the owner never takes this lane", may(TYLER, TESTING, KESTRA).rule, "bot_guest_owner")


# --------------------------------------------------------------------------
section("Live path: Hermes in #kestra gets a chat-only reply that opens with its ping")

model = fresh("hey Hermes - Benham here. Ready when you are.")
msg = say(HERMES, "hi Benham, Caz says hello", kestra)
check("the model was asked once", len(model.seen), 1)
call = model.seen[0] if model.seen else {}
check("...talking only: tool_choice none", call.get("tool_choice"), {"type": "none"})
check("...Benham's bot-guest note opens the turn",
      call["messages"][-1]["content"][0]["text"].startswith(
          "[Caz set this channel up so I can talk directly with HERMES_AGENT")
      if call else False, True)
check("...told they are another AI's bot",
      "another AI's bot" in call["system"][1]["text"] if call else False, True)
check("one message in #kestra", len(kestra.sent), 1)
check("...opening with Hermes's inline mention",
      (kestra.sent[0] or "").startswith(f"<@{HERMES_ID}> hey Hermes") if kestra.sent else False,
      True)
check("Caz was NOT asked (no DM)", CAZ.dm_channel.sent, [])
check("no invite opened as a side effect", invites.invite_for(KESTRA, HERMES_ID), None)
check("the eyes reaction, like a friend turn", msg.reactions_added, ["\N{EYES}"])

model = fresh(f"<@{HERMES_ID}> already pinged you myself")
say(HERMES, "again", kestra)
check("a reply that already opens with the ping is not doubled",
      kestra.sent[0] if kestra.sent else None, f"<@{HERMES_ID}> already pinged you myself")


# --------------------------------------------------------------------------
section("Scope on the live path: nothing else changes")

model = fresh()
say(HERMES, "hello from general", general)
check("Hermes in #general: no model turn", model.seen, [])
check("...the ordinary friend ask goes to Caz's DMs", len(CAZ.dm_channel.sent), 1)
check("...and nothing is said in #general", general.sent, [])

model = fresh()
say(ALEX, "can I join?", kestra)
check("a friend in #kestra: no model turn", model.seen, [])
check("...the ordinary friend ask goes to Caz", len(CAZ.dm_channel.sent), 1)
check("...and #kestra hears nothing", kestra.sent, [])

model = fresh()
say(HERMES, "hi", off_kestra)
check("same channel id in a guild off agent_guilds: silence", model.seen, [])
check("...and no ask either", CAZ.dm_channel.sent, [])

grant(None)
model = fresh()
say(HERMES, "hi", kestra)
check("bot_guests removed: Hermes in #kestra gets the friend path", model.seen, [])
check("...Caz is asked, as before decision 53", len(CAZ.dm_channel.sent), 1)
grant()


# --------------------------------------------------------------------------
section("The cap: CAP replies per rolling hour, then silence")

model = fresh()
for i in range(botguests.CAP + 3):
    say(HERMES, f"msg {i}", kestra)
check(f"exactly {botguests.CAP} model calls", len(model.seen), botguests.CAP)
check(f"exactly {botguests.CAP} replies", len(kestra.sent), botguests.CAP)
check("capped messages never fall through to a Caz ask", CAZ.dm_channel.sent, [])

t0 = time.time()
botguests.reset()
for i in range(botguests.CAP):
    botguests.take(KESTRA, HERMES_ID, now=t0 + i)
check("full at the cap", botguests.take(KESTRA, HERMES_ID, now=t0 + 60)[0], False)
check("still full 59 minutes later",
      botguests.take(KESTRA, HERMES_ID, now=t0 + botguests.WINDOW - 60)[0], False)
check("reopens once the oldest reply is an hour old",
      botguests.take(KESTRA, HERMES_ID, now=t0 + botguests.WINDOW + 0.5), (True, 10))
check("the budget is per channel",
      botguests.take(GENERAL, HERMES_ID, now=t0 + 60), (True, 1))


# --------------------------------------------------------------------------
section("A thread counts as its parent channel (issue 9, 2026-10-19)")
# Hermes answers inside a thread under the message that pinged it. To Discord
# a thread is its own channel, so the channel-id list never matched it and Caz
# got the friend ask for his own bot. Only discord.Thread carries parent_id.


class _Thread(_Channel):
    def __init__(self, cid, name, guild, parent_id):
        super().__init__(cid, name, guild)
        self.parent_id = parent_id


KESTRA_THREAD = 600000000000000401          # invented
GENERAL_THREAD = 600000000000000402
k_thread = _Thread(KESTRA_THREAD, "Greet a user by ID", testing, KESTRA)
g_thread = _Thread(GENERAL_THREAD, "elsewhere", testing, GENERAL)
off_thread = _Thread(600000000000000403, "off", offlist, KESTRA)
bot.client.channels.update({KESTRA_THREAD: k_thread, GENERAL_THREAD: g_thread})

grant()
check("policy: a thread under #kestra is allowed",
      may(HERMES_ID, TESTING, KESTRA_THREAD, parent_id=KESTRA).allowed, True)
check("policy: a thread under #general is not",
      may(HERMES_ID, TESTING, GENERAL_THREAD, parent_id=GENERAL).rule, "bot_guest_scope")
check("policy: without the parent id the thread id alone does not match",
      may(HERMES_ID, TESTING, KESTRA_THREAD).rule, "bot_guest_scope")
check("policy: someone else in a #kestra thread is not",
      may(ALEX.id, TESTING, KESTRA_THREAD, parent_id=KESTRA).rule, "bot_guest_scope")
check("policy: a #kestra-id thread in a guild off agent_guilds is not",
      may(HERMES_ID, OFF_LIST, 600000000000000403, parent_id=KESTRA).rule, "bot_guest_guild")
check("policy: a garbage parent id is ignored, not trusted",
      may(HERMES_ID, TESTING, KESTRA_THREAD, parent_id="abc").rule, "bot_guest_scope")

model = fresh("hi Hermes, Benham here.")
for ch in (k_thread, g_thread, off_thread):
    ch.said.clear()
    ch.sent.clear()
msg = say(HERMES, "Hey Benham, Caz asked me to say hi", k_thread)
check("live: Hermes in a #kestra thread gets a reply, in the thread",
      (len(model.seen), len(k_thread.sent)), (1, 1))
check("...opening with Hermes's ping",
      (k_thread.sent[0] or "").startswith(f"<@{HERMES_ID}>") if k_thread.sent else False, True)
check("...and Caz is NOT asked in DMs", CAZ.dm_channel.sent, [])
check("...nothing lands in #kestra itself", kestra.sent, [])

model = fresh()
say(HERMES, "hi from a general thread", g_thread)
check("live: a thread under #general still takes the friend path", model.seen, [])
check("...Caz is asked, as before", len(CAZ.dm_channel.sent), 1)
check("...and the thread hears nothing", g_thread.sent, [])

model = fresh()
for i in range(botguests.CAP):
    say(HERMES, f"parent {i}", kestra)
k_thread.sent.clear()
say(HERMES, "one more, in a thread", k_thread)
check("the cap is shared: a thread spends its parent's hour, so a new thread "
      "never buys a fresh budget", (len(model.seen), k_thread.sent),
      (botguests.CAP, []))

# --------------------------------------------------------------------------
section("Who a two-bot message is for (INTENT decision 57, issue 18)")
# Caz, 2026-10-20, set in stone: A - the FIRST mention is the addressee, a bot
# mentioned later is its subject and stays silent; B - each reply @'s the other
# bot; C - a reply with no @ closes the exchange. 10-19 in #kestra, his
# "<@Hermes> Say hi to <@Benham> pls." got an answer from both bots.


class _Raw(_Mention):
    """A message whose text and mention list are given exactly."""

    def __init__(self, author, content, channel, mentions):
        super().__init__(author, "", channel)
        self.content = content
        self.clean_content = content
        self.mentions = list(mentions)


def say_raw(author, content, channel, mentions):
    msg = _Raw(author, content, channel, mentions)
    asyncio.run(bot.on_message(msg))
    return msg


owner_turns = []
_real_respond = bot.agent.respond


async def _fake_respond(*a, **kw):
    owner_turns.append(kw)
    return "hello both", None

bot.agent.respond = _fake_respond
grant()
try:
    p = policy
    me = [BOT_ID]
    check("policy: Hermes first, Benham second - DENY, Benham is the subject",
          p.may_answer_addressee(f"<@{HERMES_ID}> say hi to <@{BOT_ID}> pls.", me).rule,
          "bot_subject")
    check("policy: Benham first - ALLOW",
          p.may_answer_addressee(f"<@{BOT_ID}> say hi to <@{HERMES_ID}>", me).allowed, True)
    check("policy: a PERSON first is ordinary group chat - ALLOW",
          p.may_answer_addressee(f"<@{ALEX.id}> <@{BOT_ID}> look at this", me).allowed, True)
    check("policy: an unlisted bot account first is not a bot guest - ALLOW",
          p.may_answer_addressee(f"<@{OFF_LIST}> <@{BOT_ID}>", me).allowed, True)
    check("policy: Benham's managed role counts as Benham",
          p.may_answer_addressee(f"<@{HERMES_ID}> and <@&4242>", me + [4242]).rule,
          "bot_subject")
    check("policy: a reply-ping only (no inline @) is a close",
          p.bot_guest_turn_open("thanks, that's all", me).rule, "bot_close")
    check("policy: an inline @ anywhere keeps the turn open",
          p.bot_guest_turn_open(f"ok - over to you <@{BOT_ID}>", me).allowed, True)
    check("policy: turn pass goes to the listed bot named after Benham",
          p.turn_pass_to(f"<@{BOT_ID}> say hi to <@{HERMES_ID}>", me, KESTRA), HERMES_ID)
    check("policy: ...also in a thread under the listed channel",
          p.turn_pass_to(f"<@{BOT_ID}> hi <@{HERMES_ID}>", me, KESTRA_THREAD, KESTRA), HERMES_ID)
    check("policy: ...not where the bot has no line",
          p.turn_pass_to(f"<@{BOT_ID}> hi <@{HERMES_ID}>", me, GENERAL), None)
    check("policy: ...and not to a person",
          p.turn_pass_to(f"<@{BOT_ID}> hi <@{ALEX.id}>", me, KESTRA), None)

    # A, live: Caz's exact message shape. Benham is the subject - nothing at all.
    model = fresh()
    owner_turns.clear()
    say_raw(CAZ, f"<@{HERMES_ID}> Say hi to <@{BOT_ID}> pls.", kestra, [HERMES, BENHAM])
    check("live A: Caz names Hermes first - no owner turn", owner_turns, [])
    check("...no reply in #kestra", kestra.sent, [])
    check("...and no DM to Caz", CAZ.dm_channel.sent, [])

    model = fresh()
    say_raw(ALEX, f"<@{HERMES_ID}> <@{BOT_ID}> both of you?", kestra, [HERMES, BENHAM])
    check("live A: a friend names Hermes first - no model turn, no ask",
          (model.seen, kestra.sent, CAZ.dm_channel.sent), ([], [], []))

    # B, live: Caz names Benham first - Benham answers and hands Hermes the turn.
    model = fresh()
    owner_turns.clear()
    say_raw(CAZ, f"<@{BOT_ID}> say hi to <@{HERMES_ID}>", kestra, [BENHAM, HERMES])
    check("live B: Caz names Benham first - one owner turn", len(owner_turns), 1)
    check("...the reply opens with Hermes's ping (the turn pass)",
          (kestra.sent[0] or "").startswith(f"<@{HERMES_ID}> hello both")
          if kestra.sent else False, True)
    model = fresh()
    owner_turns.clear()
    say_raw(CAZ, f"<@{BOT_ID}> say hi to <@{HERMES_ID}>", general, [BENHAM, HERMES])
    check("...where Hermes has no line, the reply is not handed to it",
          general.sent, ["hello both"])

    # The thread exchange: Hermes passes, Benham passes back, Hermes closes.
    model = fresh("hi Hermes, Benham here.")
    k_thread.sent.clear()
    say_raw(HERMES, f"Hey <@{BOT_ID}>, Caz asked me to say hi", k_thread, [BENHAM])
    check("live: Hermes @'s Benham in the thread - Benham answers there",
          (len(model.seen), len(k_thread.sent)), (1, 1))
    check("...passing the turn back with Hermes's ping",
          (k_thread.sent[0] or "").startswith(f"<@{HERMES_ID}>") if k_thread.sent else False,
          True)

    # C, live: Hermes's close - a Discord reply to Benham with no inline @.
    model = fresh()
    k_thread.sent.clear()
    say_raw(HERMES, "Nice to meet you, that's all from me.", k_thread, [BENHAM])
    check("live C: a reply-ping with no inline @ - Benham says nothing",
          (model.seen, k_thread.sent), ([], []))
    check("...and Caz is not asked", CAZ.dm_channel.sent, [])
    check("...and it spent none of the hour", botguests.take(KESTRA, HERMES_ID)[1], 1)

    # The cap still holds under the new rules.
    model = fresh()
    for i in range(botguests.CAP + 2):
        say_raw(HERMES, f"<@{BOT_ID}> turn {i}", k_thread, [BENHAM])
    check(f"the cap still holds: {botguests.CAP} replies, then silence",
          (len(model.seen), len(k_thread.sent)), (botguests.CAP, botguests.CAP))
    check("...and no DM ask when it runs out", CAZ.dm_channel.sent, [])

    # Never a DM ask about a listed bot in its own channel, whatever the gate says.
    model = fresh()
    _gates = identity.agent_guilds
    identity.agent_guilds = lambda face=None: frozenset()
    try:
        say_raw(HERMES, f"<@{BOT_ID}> hi", kestra, [BENHAM])
    finally:
        identity.agent_guilds = _gates
    check("gate refuses a listed bot in its own channel: silence, no DM ask",
          (model.seen, kestra.sent, CAZ.dm_channel.sent), ([], [], []))
finally:
    bot.agent.respond = _real_respond

grant(None)

print()
if _fails:
    print(f"FAILED: {len(_fails)} check(s)")
    sys.exit(1)
print("all bot-guest checks passed")
