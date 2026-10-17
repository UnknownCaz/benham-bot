"""
test_ask_transcript.py - the open question is something Benham SAID, memory has
an age limit, and the bind mark means one thing.

Gumshoe case 2026-10-17-benham-bot (C:\\Users\\Tyler\\Claude\\Gumshoe\\cases\\),
fixes 1 and 2, on Caz's yes 2026-10-17.

THE TURN THIS EXISTS FOR. 2026-10-15 09:05:06Z the ask queue put c46 in his DM.
At 09:05 he typed "Leave it. I'll get it eventualy" - a plain, correct answer.
The model had the question only as a system-prompt section; its own transcript,
which code-sent asks and nudges never enter, ended on a 10-12 argument about
something else, kept because DM memory had a count limit and no age limit. It
read "Leave it" as part of that, called nothing, and the bot nudged twice and
banked a question he had answered. Meanwhile his message wore the same check
mark a bound answer gets, so nothing on his screen said it had not taken.

  1. The question goes into the turn list as Benham's own message, placed when
     it was last shown, saying how long ago; and memory older than
     agent.memory_max_age_hours (default 24) - or with no stamp at all - is not
     sent.
  2. A real bind - reply, slot, or an answer_conversation that landed - gets
     bot.ANSWER_MARK and a "Took that as your answer to" line. A message that
     bound nothing gets the ordinary handled mark and no line.

Fully offline: the Anthropic client is a scripted fake, Discord is stubbed.

    python test_ask_transcript.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture + temp state; must precede benham imports

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from benham import bot
from benham.core import agent
from benham.core import confirm
from benham.core import conversations as C
from benham.core import jsonio
from benham.core import policy

TYLER = 273967061619965952
DM_CHAN = 4242
BOT_ID = 752313060970201218

# The Windows console is cp1252, and these checks print emoji (cp1252 landmine).
try:
    sys.stdout.reconfigure(errors="backslashreplace")
except AttributeError:
    pass

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

class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Blk(input_tokens=0, output_tokens=0)


class _Messages:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def create(self, **kw):
        self.sent.append(dict(kw, messages=[dict(m) for m in kw.get("messages", [])]))
        if len(self.sent) > len(self.script):
            raise AssertionError(
                f"model called {len(self.sent)} times, script has {len(self.script)}")
        return self.script[len(self.sent) - 1]


class _Fake:
    def __init__(self, script):
        self.messages = _Messages(script)


def _says(text):
    return _Resp([_Blk(type="text", text=text)], "end_turn")


def _calls_answer(cid, answer):
    return _Resp([_Blk(type="tool_use", id="tu_1", name="answer_conversation",
                       input={"id": cid, "answer": answer})], "tool_use")


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
        self.id = DM_CHAN
        self.sent = []

    def typing(self):
        return _Typing()

    async def send(self, content=None, **kw):
        self.sent.append(content)


class _Replied:
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
    def __init__(self, content, reference=None):
        self.author = _User(TYLER, "caz6666")
        self.content = content
        self.guild = None
        self.channel = _Channel()
        self.mentions = []
        self.attachments, self.embeds = [], []
        self.stickers, self.message_snapshots = [], []
        self.reference = reference
        self.id = 999000222
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class _StubClient:
    user = _User(BOT_ID, "Benham#2721")

    def get_channel(self, cid):
        return None

    def get_guild(self, gid):
        return None


_real_respond = agent.respond
AGENT = []


def _install():
    bot.client = _StubClient()
    bot.record_message = lambda m: {"is_self": False, "channel": "dm",
                                    "author": str(m.author), "content": m.content}
    bot.log = lambda *_a: None
    bot.strip_mention = lambda m: m.content

    async def _reply_in(channel, text, **kw):
        channel.sent.append(text)
    bot.reply_in = _reply_in
    agent.ENABLED = True
    confirm.cancel()


async def _plain_agent(client, log, text, **kw):
    AGENT.append(kw)
    return "ok", None


_next_msg = [1552030327386543000]


def ask(*questions):
    """Open and deliver questions to Tyler as advance_conversation does."""
    convs = [C.open_conversation(TYLER, purpose=q, question=q, direction=C.ASKING)
             for q in questions]
    _next_msg[0] += 1
    mid = _next_msg[0]
    C.set_batch_message(TYLER, mid, shown=[c["id"] for c in convs])
    for c in convs:
        C.record_ask_message(c["id"], mid)
        C.mark_delivered(c["id"])
    return mid, [C.get(c["id"]) for c in convs]


def reset():
    C.forget()
    C.set_batch_message(TYLER, None)
    AGENT.clear()
    agent._last_call.clear()
    agent.forget(f"dm:{TYLER}")
    confirm.cancel()


def deliver(content, reference=None):
    msg = _Msg(content, reference)
    asyncio.run(bot.on_message(msg))
    return msg


def _write_memory(key, pairs):
    """pairs: [(user, assistant, ts-or-None)] straight onto disk."""
    turns = []
    for u, a, ts in pairs:
        extra = {"ts": ts} if ts is not None else {}
        turns.append({"role": "user", "content": u, **extra})
        turns.append({"role": "assistant", "content": a, **extra})
    mem = agent._store._read()
    mem[key] = turns
    jsonio.write_json(agent.MEMORY_FILE, mem)


def _text_of(turn):
    c = turn["content"]
    return c if isinstance(c, str) else "\n".join(b.get("text", "") for b in c)


def _alternates(msgs):
    roles = [m["role"] for m in msgs]
    return roles[:1] == ["user"] and all(a != b for a, b in zip(roles, roles[1:]))


async def _agent_turn(said, script, **kw):
    """One owner-DM agent turn, the real respond(), a scripted model."""
    key = f"dm:{TYLER}"
    fake = _Fake(script)
    agent._client = fake
    agent._last_call.clear()
    reply, _parked = await _real_respond(
        _StubClient(), lambda *_a: None, said,
        actor_id=TYLER, actor_name="caz6666", channel_id=DM_CHAN, guild_id=None,
        where="a DM", conversation_key=key,
        call_ctx=policy.CallContext.owner_dm(TYLER, DM_CHAN), **kw)
    return reply, fake


C46 = ("Moltbook shows the kestra account's owner under your legal name - rename "
       "the X display name, or leave it?")
SAID = "Leave it. I'll get it eventualy"


def main():
    _install()
    key = f"dm:{TYLER}"
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ fix 1b
    section("Fix 1b - DM memory has an age limit")
    reset()
    old = (now - timedelta(days=2, hours=12)).isoformat()
    fresh = (now - timedelta(hours=1)).isoformat()
    _write_memory(key, [("unstamped, from before the fix", "an old reply", None),
                        ("the 10-12 welcome-message argument", "nothing's sent", old),
                        ("an hour ago", "a recent reply", fresh)])
    h = agent._history(key)
    check("only the fresh exchange survives (stale and unstamped are dropped)",
          [t["content"] for t in h], ["an hour ago", "a recent reply"])
    check("what is sent carries role and content only - no ts key for the API",
          all(set(t) == {"role", "content"} for t in h), True)
    real_age = agent.MEMORY_MAX_AGE_HOURS
    try:
        agent.MEMORY_MAX_AGE_HOURS = 72
        check("the limit is the config value: 72 h keeps the 2.5-day exchange",
              [t["content"] for t in agent._history(key)][:2],
              ["the 10-12 welcome-message argument", "nothing's sent"])
        agent.MEMORY_MAX_AGE_HOURS = 0
        check("0 means no limit at all", len(agent._history(key)), 6)
    finally:
        agent.MEMORY_MAX_AGE_HOURS = real_age
    check("the default is a day", agent.MEMORY_MAX_AGE_HOURS, 24.0)
    agent._remember(key, "new", "stored")
    raw = agent._store._read()[key]
    check("a new exchange is stored with a stamp on both halves",
          all("ts" in t for t in raw[-2:]), True)
    check("nothing was deleted from disk by the age filter", len(raw), 8)

    # ------------------------------------------------------------------ fix 1a
    section("Fix 1a - c46 replayed: the question is the last thing Benham said")
    reset()
    _write_memory(key, [("the 10-12 welcome-message argument", "I give up too", old)])
    _mid, (c46,) = ask(C46)
    reply, fake = asyncio.run(_agent_turn(SAID, [_says("ok")], conversation=C.get(c46["id"])))
    msgs = fake.messages.sent[0]["messages"]
    check("the model input opens on a user turn and alternates", _alternates(msgs), True)
    check("his message is the last turn", msgs[-1], {"role": "user", "content": SAID})
    q_turn = msgs[-2]
    check("the turn before it is Benham's (assistant)", q_turn["role"], "assistant")
    check("...and carries the question's own words", C46 in _text_of(q_turn), True)
    check("...and its id", f"`{c46['id']}`" in _text_of(q_turn), True)
    check("...and how long ago it was sent",
          "under a minute ago" in _text_of(q_turn), True)
    check("the 2.5-day-old argument is not in front of the model",
          any("welcome-message" in _text_of(m) for m in msgs), False)
    check("with nothing earlier, a harness note opens the list - not words in his mouth",
          msgs[0]["role"] == "user" and "harness note" in _text_of(msgs[0]), True)
    check("the system-prompt section is still there too",
          f"`{c46['id']}`" in fake.messages.sent[0]["system"][1]["text"], True)
    stored = agent._store._read()[key]
    check("the question is NOT written into memory (rebuilt from the record each turn)",
          any(C46 in str(t.get("content")) for t in stored), False)

    section("Fix 1a - with a fresh chat before the ask, the question follows it")
    reset()
    _write_memory(key, [("morning", "morning!", (now - timedelta(minutes=30)).isoformat())])
    _mid, (c47,) = ask(C46)
    _r, fake = asyncio.run(_agent_turn(SAID, [_says("ok")], conversation=C.get(c47["id"])))
    msgs = fake.messages.sent[0]["messages"]
    check("roles still alternate (the question joins Benham's last turn)",
          _alternates(msgs), True)
    check("three turns: his morning, Benham's reply + the question, his answer",
          [m["role"] for m in msgs], ["user", "assistant", "user"])
    check("Benham's earlier reply is kept, the question comes after it",
          _text_of(msgs[1]).index("morning!") < _text_of(msgs[1]).index(C46), True)

    section("Fix 1a - a chat AFTER the ask stays after it (placed in time, not forced last)")
    reset()
    _mid, (c48,) = ask(C46)
    later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    _write_memory(key, [("its all good", "back at you", later)])
    _r, fake = asyncio.run(_agent_turn(SAID, [_says("ok")], conversation=C.get(c48["id"])))
    msgs = fake.messages.sent[0]["messages"]
    check("order: note, the question, then the later exchange, then his message",
          (C46 in _text_of(msgs[1]), _text_of(msgs[2]), _text_of(msgs[3])),
          (True, "its all good", "back at you"))
    check("roles alternate", _alternates(msgs), True)

    section("Fix 1a - several waiting: the numbered list, every id")
    reset()
    _mid, (a, b) = ask("which db?", "drop the cache?")
    q = C.queue_for(TYLER)
    _r, fake = asyncio.run(_agent_turn("sqlite, and yes", [_says("ok")],
                                       conversation=q[0], queue=q))
    text = _text_of(fake.messages.sent[0]["messages"][-2])
    check("both questions with their numbers and ids",
          (f"1. `{a['id']}` - which db?" in text, f"2. `{b['id']}` - drop the cache?" in text),
          (True, True))

    section("Fix 1a - no question waiting, nothing added")
    reset()
    _write_memory(key, [("hi", "hey", (now - timedelta(minutes=5)).isoformat())])
    _r, fake = asyncio.run(_agent_turn("what's up", [_says("ok")]))
    check("the turns are just memory plus his message",
          fake.messages.sent[0]["messages"],
          [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"},
           {"role": "user", "content": "what's up"}])

    # ------------------------------------------------------------------ fix 2
    section("Fix 2 - a message that binds nothing keeps the ordinary mark")
    reset()
    bot.agent.respond = _plain_agent
    msg = deliver("what's the weather")
    check("eyes then the handled check, no bind mark", msg.reactions_added, ["👀", "✅"])
    check("no 'took that' line", any("Took that" in str(s) for s in msg.channel.sent), False)

    section("Fix 2 - c46 again: an open question and the model binds nothing")
    reset()
    _mid, (c49,) = ask(C46)
    msg = deliver(SAID)
    check("still no bind mark - nothing was recorded", bot.ANSWER_MARK in msg.reactions_added,
          False)
    check("still no 'took that' line", any("Took that" in str(s) for s in msg.channel.sent),
          False)
    check("and the question is still open", C.get(c49["id"])["state"], C.OPEN)

    section("Fix 2 - a Discord reply binds: the bind mark and the line")
    reset()
    mid, (c50,) = ask(C46)
    msg = deliver(SAID, reference=_Ref(mid))
    check("bound", C.get(c50["id"])["state"], C.ANSWERED)
    check("the bind mark, once, plus the ordinary marks",
          msg.reactions_added, [bot.ANSWER_MARK, "👀", "✅"])
    said = "\n".join(str(s) for s in msg.channel.sent)
    check("'Took that as your answer to' names the question",
          f"Took that as your answer to `{c50['id']}`: Moltbook shows" in said, True)

    section("Fix 2 - slots: one line per question bound")
    reset()
    _mid, (s1, s2) = ask("which db?", "drop the cache?")
    msg = deliver("1: sqlite\n2: yes")
    said = "\n".join(str(s) for s in msg.channel.sent)
    check("both bound", (C.get(s1["id"])["state"], C.get(s2["id"])["state"]),
          (C.ANSWERED, C.ANSWERED))
    check("a line for each", (f"`{s1['id']}`: which db?" in said,
                              f"`{s2['id']}`: drop the cache?" in said), (True, True))
    check("the bind mark appears once", msg.reactions_added.count(bot.ANSWER_MARK), 1)

    section("Fix 2 - a typed answer the model really records (answer_conversation)")
    reset()
    bot.agent.respond = _real_respond
    _mid, (c51,) = ask(C46)
    agent._client = _Fake([_calls_answer(c51["id"], SAID),
                           _says("Leaving it - took that as your answer.")])
    msg = deliver(SAID)
    check("bound as a judgement", C.get(c51["id"])["state"], C.ANSWERED)
    check("the bind mark lands on his message",
          msg.reactions_added, ["👀", bot.ANSWER_MARK, "✅"])
    said = "\n".join(str(s) for s in msg.channel.sent)
    check("and the line, from the record", f"Took that as your answer to `{c51['id']}`" in said,
          True)

    section("Fix 2 - the model only SAYS it took it and the check finds nothing")
    reset()
    _mid, (c52,) = ask(C46)
    agent._client = _Fake([_says(f"That's the answer to {c52['id']} - taking it as such."),
                           _says("NOT AN ANSWER")])
    msg = deliver(SAID)
    check("no bind mark for words alone", bot.ANSWER_MARK in msg.reactions_added, False)
    check("no 'took that' line from code",
          any("Took that as your answer" in str(s) for s in msg.channel.sent), False)
    agent._client = None
    bot.agent.respond = _real_respond

    print()
    if _fails:
        print(f"{len(_fails)} check(s) did not pass:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
