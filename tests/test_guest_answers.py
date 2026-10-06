"""
test_guest_answers.py - a guest's answer reaches the question Benham asked them.

The board's "bug that matters" (2026-09-27): since Phase B nothing bound a
guest's DM reply to an outreach question. Tyler's answers bind in on_message, a
guest never reaches that block, and the PC session that used to tail
inbox.jsonl went away with the move to the Mac. c40 (Draco, 2026-09-22) is the
case: the brain sat out under a quiet as told, they answered "yes", and the
answer was found by hand.

Tyler's rule, now applied to guests (INTENT 3.3, item 10): a Discord reply or a
slot number binds in code; a typed message is judged by a model and announced
(a check mark on their message, a quiet line to Tyler). Driven through the real
bot.on_message, with the judge's model scripted. In order of how much it
matters:

  c40 replayed: quieted brain, a typed "yes" - it binds now, judged, and Tyler
  hears about it.

  A Discord reply binds with no model asked at all.

  Not an answer stays unbound, and the brain is told not to claim otherwise.

  The judge can only pick this guest's own delivered questions - never someone
  else's, never one the bot has not sent, never an id it made up - and a judge
  that fails or mumbles leaves the answer unbound rather than guessed.

  Tyler's own path is the same block it was before it moved (bind_certain).

    python test_guest_answers.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture + temp state; must precede benham imports

import asyncio
import os
import sys
import tempfile

from benham import bot
from benham.core import agent
from benham.core import confirm
from benham.core import conversations
from benham.core import jsonio
from benham.guest import answers
from benham.guest import guest

TYLER = 273967061619965952
DRACO = _testconfig.GUEST_ID            # invented ids, on the fixture's guest list
DOOM = _testconfig.GUEST_ID_2
BOT_ID = 752313060970201218

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
    def __init__(self, uid, name):
        self.id = uid
        self.name = name
        self.display_name = name

    def __str__(self):
        return self.name


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Channel:
    def __init__(self):
        self.id = 4242
        self.sent = []

    def typing(self):
        return _Typing()

    async def send(self, content=None, **kw):
        self.sent.append(content)


class _Replied:
    """Benham's own message - the ask - as the one being replied to."""

    def __init__(self, content):
        self.author = _User(BOT_ID, "Benham")
        self.content = content
        self.attachments, self.embeds = [], []
        self.stickers, self.message_snapshots = [], []


class _Ref:
    def __init__(self, message_id, content="(the question)"):
        self.message_id = message_id
        self.resolved = _Replied(content)


class _Msg:
    def __init__(self, uid, content, reference=None):
        self.author = _User(uid, {DRACO: "draco", DOOM: "doom",
                                  TYLER: "caz6666"}.get(uid, "someone"))
        self.content = content
        self.guild = None          # a DM
        self.channel = _Channel()
        self.mentions = []
        self.attachments, self.embeds = [], []
        self.stickers, self.message_snapshots = [], []
        self.reference = reference
        self.id = 999000111
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, text):
        self.content = [_Blk(type="text", text=text)]
        self.usage = _Blk(input_tokens=10, output_tokens=5)


class _JudgeModel:
    """The Anthropic client the judge calls, scripted. Records every call."""

    def __init__(self, reply='{"answers": []}', boom=None):
        self.reply = reply
        self.boom = boom
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.boom is not None:
            raise self.boom
        return _Resp(self.reply(kw) if callable(self.reply) else self.reply)


LOG = []
OWNER = []        # (text, kind) per owner DM
BRAIN = []        # (user_id, text, note) per guest brain turn
AGENT = []        # kwargs per owner agent turn


def _install():
    bot.record_message = lambda m: {"is_self": False, "channel": "dm",
                                    "author": str(m.author), "content": m.content}
    bot.log = LOG.append
    bot.strip_mention = lambda m: m.content

    async def _reply_in(channel, text, **kw):
        channel.sent.append(text)
    bot.reply_in = _reply_in

    async def _owner(text, kind=None):
        OWNER.append((text, kind))
    bot.ask_owner_dm = _owner

    def _respond(user_id, text, log=None, content=None, note=None):
        BRAIN.append((user_id, text, note))
        return "guest reply"
    guest.respond = _respond

    async def _agent(client, log, text, **kw):
        AGENT.append(kw)
        return "ok", None
    bot.agent.respond = _agent
    agent.ENABLED = True
    confirm.current = lambda: None

    tmp = tempfile.mkdtemp(prefix="benham-answers-test-")
    guest.MEMORY_FILE = os.path.join(tmp, "guest_memory.json")
    guest.USAGE_FILE = os.path.join(tmp, "guest_usage.json")
    guest.QUIET_FILE = os.path.join(tmp, "guest_quiet.json")
    guest._quiet = None
    guest.COOLDOWN = 0
    jsonio.write_json(guest.USAGE_FILE, {})


_install()

Q_REPO = "Want to make the repo private and add UnknownCaz as a collaborator?"
Q_TEST = "Does the zoom look right on stream now?"
_next_msg = [1552030327386542000]


def reset():
    conversations.forget()
    for who in (DRACO, DOOM, TYLER):
        conversations.set_batch_message(who, None)
        guest.wake(who)
    guest._last_call.clear()
    LOG.clear()
    OWNER.clear()
    BRAIN.clear()
    AGENT.clear()


def ask(who, *questions):
    """Open questions for `who` and deliver them as advance_conversation does:
    one batch message showing all of them, bound to each, each marked delivered.
    Returns (ask message id, [conversations])."""
    convs = [conversations.open_conversation(who, purpose=q, question=q,
                                             direction=conversations.ASKING)
             for q in questions]
    _next_msg[0] += 1
    mid = _next_msg[0]
    conversations.set_batch_message(who, mid, shown=[c["id"] for c in convs])
    for c in convs:
        conversations.record_ask_message(c["id"], mid)
        conversations.mark_delivered(c["id"])
    return mid, convs


def deliver(uid, content, reference=None, model=None):
    guest._client = model or _JudgeModel()
    msg = _Msg(uid, content, reference)
    asyncio.run(bot.on_message(msg))
    return msg


def state(c):
    return conversations.get(c["id"])["state"]


def how(c):
    """The 'answered via X' detail from the conversation's own log."""
    for e in conversations.get(c["id"]).get("log", []):
        if e.get("event") == "answered":
            return e.get("detail")
    return None


# --------------------------------------------------------------------------
section("c40 replayed: the brain is quiet, they type 'yes' - it binds now")
reset()
_mid, (c40,) = ask(DRACO, Q_REPO)
guest.quiet(DRACO, 240)
model = _JudgeModel(lambda kw: '{"answers": ["%s"]}' % c40["id"])
msg = deliver(DRACO, "yes", model=model)
check("the question is answered", state(c40), conversations.ANSWERED)
check("...with their words", conversations.get(c40["id"])["answer"], "yes")
check("...recorded as the model's read, not a certainty", how(c40), "via judged")
check("the judge was asked exactly once", len(model.calls), 1)
check("they see a check mark - it landed", msg.reactions_added, ["✅"])
check("the quiet still holds: the brain did not speak", BRAIN, [])
check("...and nothing was posted to them", msg.channel.sent, [])
check("Tyler hears about it, once", len(OWNER), 1)
check("...quietly (the 'answered' kind)", OWNER[0][1], "answered")
check("...naming the question and their words",
      (c40["id"] in OWNER[0][0], '"yes"' in OWNER[0][0]), (True, True))
check("...and saying it was Benham's read, not a reply",
      "Benham's read" in OWNER[0][0], True)

section("What the judge was shown")
kw = model.calls[0]
prompt = kw["messages"][0]["content"]
check("the cheap judge model, not the chat model", kw["model"], answers.MODEL)
check("...with a timeout, so a slow judge cannot hold the DM", kw.get("timeout"),
      answers.TIMEOUT)
check("the question is in front of it", Q_REPO in prompt, True)
check("their words are fenced as data", "--- their message [" in prompt, True)
check("...and are in there", "\nyes\n" in prompt, True)
check("it is asked for ids, as JSON", '{"answers": []}' in prompt, True)

# --------------------------------------------------------------------------
section("A Discord reply to the question binds in code - no model asked")
reset()
mid, (c1,) = ask(DRACO, Q_TEST)
model = _JudgeModel('{"answers": []}')
msg = deliver(DRACO, "yeah it looks fine", reference=_Ref(mid, Q_TEST), model=model)
check("answered", state(c1), conversations.ANSWERED)
check("...as a reply, the certain route", how(c1), "via reply")
check("the judge was never called", len(model.calls), 0)
check("check mark", msg.reactions_added, ["✅"])
check("Tyler is told", len(OWNER), 1)
check("...without the 'Benham's read' caveat - it was certain",
      "Benham's read" in OWNER[0][0], False)
check("the brain (not quiet) still answers them", len(BRAIN), 1)
check("...told their message was just recorded",
      "just recorded" in (BRAIN[0][2] or ""), True)
check("...and what the question was", Q_TEST in (BRAIN[0][2] or ""), True)

section("A reply to some OTHER message of Benham's is not an answer by itself")
reset()
mid, (c2,) = ask(DRACO, Q_TEST)
model = _JudgeModel('{"answers": []}')
msg = deliver(DRACO, "lol", reference=_Ref(mid + 999, "some chat"), model=model)
check("not bound by the reply route", state(c2), conversations.OPEN)
check("...it went to the judge instead, which said no", len(model.calls), 1)

# --------------------------------------------------------------------------
section("A typed answer with the brain awake: judged, bound, and acknowledged")
reset()
_mid, (c3,) = ask(DRACO, Q_TEST)
model = _JudgeModel(lambda kw: '{"answers": ["%s"]}' % c3["id"])
msg = deliver(DRACO, "yep, did it - looks great", model=model)
check("answered, judged", (state(c3), how(c3)),
      (conversations.ANSWERED, "via judged"))
check("check mark", msg.reactions_added, ["✅"])
check("the brain answers them", len(BRAIN), 1)
check("...knowing it was recorded, so it can say thanks rather than 'yes to what?'",
      "just recorded" in (BRAIN[0][2] or ""), True)

section("Not an answer: unbound, and the brain is told not to pretend")
reset()
_mid, (c4,) = ask(DRACO, Q_TEST)
model = _JudgeModel('{"answers": []}')
msg = deliver(DRACO, "hey what's up, random question about minecraft", model=model)
check("still open", state(c4), conversations.OPEN)
check("no check mark", msg.reactions_added, [])
check("Tyler is not pinged", OWNER, [])
check("the brain answers them", len(BRAIN), 1)
note = BRAIN[0][2] or ""
check("...knowing the question is still waiting", Q_TEST in note, True)
check("...and that it must not claim it recorded anything",
      "never say you recorded" in note, True)

# --------------------------------------------------------------------------
section("The judge can only pick this guest's own delivered questions")
reset()
_mid, (mine,) = ask(DRACO, Q_TEST)
_mid2, (theirs,) = ask(DOOM, Q_REPO)
model = _JudgeModel(lambda kw: '{"answers": ["%s", "c999"]}' % theirs["id"])
deliver(DRACO, "yes", model=model)
check("someone else's question is untouched", state(theirs), conversations.OPEN)
check("...a made-up id binds nothing", state(mine), conversations.OPEN)
check("...and Tyler is not told of an answer that did not happen", OWNER, [])
check("only this guest's question was offered to the judge",
      (theirs["question"] in model.calls[0]["messages"][0]["content"],
       mine["question"] in model.calls[0]["messages"][0]["content"]),
      (False, True))

reset()
undelivered = conversations.open_conversation(
    DRACO, purpose="p", question=Q_TEST, direction=conversations.ASKING)
model = _JudgeModel(lambda kw: '{"answers": ["%s"]}' % undelivered["id"])
deliver(DRACO, "yes", model=model)
check("a question the bot has not sent yet cannot be answered by chance",
      state(undelivered), conversations.OPEN)
check("...the judge is not even asked", len(model.calls), 0)

section("Nothing waiting on them, nothing spent")
reset()
model = _JudgeModel('{"answers": []}')
deliver(DRACO, "yes", model=model)
check("no judge call", len(model.calls), 0)
check("the brain gets no note", BRAIN[0][2], None)

section("A judge that fails or mumbles leaves the answer unbound, never guessed")
reset()
_mid, (c5,) = ask(DRACO, Q_TEST)
deliver(DRACO, "yes", model=_JudgeModel(boom=RuntimeError("overloaded")))
check("an API error binds nothing", state(c5), conversations.OPEN)
check("...and the guest is still answered", len(BRAIN), 1)
check("...and the failure is in the log",
      any("judge failed" in str(line) for line in LOG), True)
for raw in ("sure!", '{"answers": "c1"}', "{not json}", ""):
    deliver(DRACO, "yes", model=_JudgeModel(raw))
check("garbage from the judge binds nothing", state(c5), conversations.OPEN)

section("answers.parse on its own")
qs = [{"id": "c7"}, {"id": "c8"}]
check("ids it was given", answers.parse('{"answers": ["c8", "c7"]}', qs), ["c8", "c7"])
check("prose around the JSON is fine",
      answers.parse('Sure: {"answers": ["c7"]} done', qs), ["c7"])
check("duplicates once", answers.parse('{"answers": ["c7", "c7"]}', qs), ["c7"])
check("unknown ids dropped", answers.parse('{"answers": ["c9"]}', qs), [])
check("empty list", answers.parse('{"answers": []}', qs), [])
check("no JSON", answers.parse("yes it answers c7", qs), [])

# --------------------------------------------------------------------------
section("Two questions on screen: slots bind for a guest too")
reset()
mid, (s1, s2) = ask(DOOM, Q_TEST, Q_REPO)
model = _JudgeModel('{"answers": []}')
msg = deliver(DOOM, "1: looks good\n2: nah keep it public", model=model)
check("both answered by number",
      (state(s1), state(s2)), (conversations.ANSWERED, conversations.ANSWERED))
check("...each with its own part",
      (conversations.get(s1["id"])["answer"], conversations.get(s2["id"])["answer"]),
      ("looks good", "nah keep it public"))
check("...as slots, no judge", (how(s1), len(model.calls)), ("via slot", 0))
check("one quiet line per question to Tyler, in one DM",
      (len(OWNER), OWNER[0][0].count("📬")), (1, 2))

reset()
mid, (s1, s2) = ask(DOOM, Q_TEST, Q_REPO)
model = _JudgeModel(lambda kw: '{"answers": ["%s"]}' % s2["id"])
deliver(DOOM, "yes", reference=_Ref(mid), model=model)
check("a reply to a message showing two is NOT certain - the judge decides",
      (len(model.calls), state(s1), how(s2)), (1, conversations.OPEN, "via judged"))

# --------------------------------------------------------------------------
section("Tyler's own path is the same block it was (bind_certain)")
reset()
mid, (t1,) = ask(TYLER, "restart the server?")
model = _JudgeModel('{"answers": []}')
msg = deliver(TYLER, "yes restart it", reference=_Ref(mid), model=model)
check("his reply binds", (state(t1), how(t1)), (conversations.ANSWERED, "via reply"))
check("...the agent is told it already bound", AGENT[-1].get("already_bound"), True)
check("...and the guest judge never runs on his path", len(model.calls), 0)
check("...nor the 'tell Tyler' line - he is the one answering", OWNER, [])

reset()
mid, (t1, t2) = ask(TYLER, "which db?", "drop the cache?")
deliver(TYLER, "1: sqlite\n2: yes")
check("his slots bind",
      (how(t1), how(t2)), ("via slot", "via slot"))

reset()
mid, (t1,) = ask(TYLER, "restart the server?")
deliver(TYLER, "yes restart it")
check("a plain DM from him is still the model's call, not code's",
      (state(t1), AGENT[-1].get("already_bound")), (conversations.OPEN, False))


print()
if _fails:
    print(f"{len(_fails)} check(s) did not pass:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("all green")
