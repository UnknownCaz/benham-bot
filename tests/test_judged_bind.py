"""
test_judged_bind.py - "taking it as the answer" must be backed by the tool call.

THE TURN THIS EXISTS FOR. 2026-10-05. An unprompted question was delivered at
07:17:12Z and buzzed his phone. At 07:19:49Z he typed back - a plain DM, not a
Discord reply. The conversation WAS in the prompt and the model DID judge it:
its reply opened "That's the answer to <id> - taking it as such". And it called
nothing. `agent usage [round 1] in=3489 out=109`, then no round 2 and no
`action answer_conversation` line - the same token-accounting proof as the
phantom previews in test_claims.py. The record stayed open, the lane read the
question as ignored, and it would have lapsed after three days and counted
toward dormancy: an answer given in two and a half minutes, filed as silence.

Not the first. On 2026-09-15 the same shape was said about an ASKING question -
"Taking that as your answer ... Answered and logged." - with no call behind it.
That exchange was still in the turn history on 10-05, which stores text only:
the announcement is remembered, the tool call that should accompany it is not,
so history itself teaches that the sentence IS the action.

INTENT.md section 3.3, through the judged-binding route. Stage 3 item 12 settled
that a plain message is "judged by the model, which must say which way it read
the message". The model kept the SAY half and dropped the DO half.

WHAT THE FIX IS, AND WHAT IT IS NOT. The check is: the reply names a question
that was waiting on him, and no answer_conversation call was made this turn.
That triggers ONE more round in which the model is told nothing was recorded
and must either make the call or say it did not mean it. The prose match only
ever buys a second question to the model - it never binds anything by itself,
so a false positive costs one API call and a true one closes the loop with the
judgement still the model's (bound_by="judged"). If the model still calls
nothing, the reply is corrected in the same message, like the other two
claim checks. Whether code may bind a plain DM WITHOUT the model is a state
machine rule and is not decided here.

Fully offline - the Anthropic client is a scripted fake, no API calls, no cost.

    python test_judged_bind.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import os
import shutil
import sys
import tempfile

from benham.core import agent
from benham.core import confirm
from benham.core import conversations as C
from benham.core import identity
from benham.core import initiative
from benham.core import policy

TYLER = 273967061619965952
DM_CHAN = 753732380921167902

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


# --------------------------------------------------------------------------- stubs

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Block(input_tokens=0, output_tokens=0)


class _Messages:
    """Scripted, and STRICT: running off the end of the script is an error.

    test_claims' fake repeats its last response forever, which is right there
    (one round is the whole point) and wrong here - this file asserts on HOW
    MANY rounds happen, and a fake that quietly serves a third would make an
    unbounded verify loop look like a passing one.
    """

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def create(self, **kw):
        # Snapshot the turn list: respond() keeps appending to the same object.
        self.sent.append(dict(kw, messages=list(kw.get("messages", []))))
        if len(self.sent) > len(self.script):
            raise AssertionError(
                f"model called {len(self.sent)} times, script has {len(self.script)}")
        return self.script[len(self.sent) - 1]


class _FakeAnthropic:
    def __init__(self, script):
        self.messages = _Messages(script)


class _StubClient:
    user = _Block(id=752313060970201218)

    def get_channel(self, cid):
        return None

    def get_guild(self, gid):
        return None


def _says(text):
    return _Resp([_Block(type="text", text=text)], "end_turn")


def _calls_answer(cid, answer, text=""):
    blocks = ([_Block(type="text", text=text)] if text else []) + [
        _Block(type="tool_use", id="tu_1", name="answer_conversation",
               input={"id": cid, "answer": answer})]
    return _Resp(blocks, "tool_use")


# --------------------------------------------------------------------------- harness

_n = 0


async def _turn(script, said, logged, **kw):
    """One owner-DM turn, wired the way bot.py wires it. Returns (reply, fake)."""
    global _n
    _n += 1
    key = f"test:judged_bind_{_n}"
    fake = _FakeAnthropic(script)
    agent._client = fake
    agent._last_call.clear()
    agent.forget(key)
    try:
        reply, _pending = await agent.respond(
            _StubClient(), logged.append, said,
            actor_id=TYLER, actor_name="caz6666", channel_id=DM_CHAN,
            guild_id=None, where="a DM", conversation_key=key,
            call_ctx=policy.CallContext.owner_dm(TYLER, DM_CHAN), **kw)
    finally:
        agent.forget(key)
    return reply, fake


def _as_bot_passes():
    """Exactly bot.py's expression for `conversation` on an unbound owner DM."""
    return C.live_for(TYLER) or initiative.live_unprompted_for(TYLER)


def _lane_question(text):
    q = initiative.open_question(text)
    return C.mark_delivered(q["id"])


QUESTION = ("did this one actually buzz your phone, or did you only find it "
            "when you looked?")
SAID = "yer it buzzed!"


def _incident_reply(cid):
    # 07:20:03Z, verbatim but for the id.
    return (f"That's the answer to {cid} - taking it as such: yes, it buzzed his "
            f"phone, he didn't just find it by looking.\n\nGood to know the "
            f"notify_owner alerts are actually landing properly then. Good.")


async def main():
    if not agent.ENABLED:
        print("agent disabled (no ANTHROPIC_API_KEY) - the end-to-end checks need "
              "the module live, but make no API calls. Set any non-empty key.")
        return 1

    tmp = tempfile.mkdtemp(prefix="benham-judged-bind-")
    real = (C.STORE, C.BATCHES, initiative.STORE, initiative.LOG_MD,
            identity.OWNER_IDS)
    try:
        C.STORE = os.path.join(tmp, "conversations.json")
        C.BATCHES = os.path.join(tmp, "ask_batches.json")
        initiative.STORE = os.path.join(tmp, "initiative.json")
        initiative.LOG_MD = os.path.join(tmp, "initiative-log.md")
        identity.OWNER_IDS = {TYLER}
        confirm.cancel()

        section("The question WAS in front of the model - the plumbing is not the bug")
        q = _lane_question(QUESTION)
        cid = q["id"]
        check("bot.py's lookup finds the delivered unprompted question",
              (_as_bot_passes() or {}).get("id"), cid)
        _reply, fake = await _turn([_says("ok")], "unrelated: what time is it?", [],
                                   conversation=_as_bot_passes())
        system = "\n\n".join(b["text"] for b in fake.messages.sent[0]["system"])
        check("its id reaches the API in the system prompt", f"`{cid}`" in system, True)
        check("...with the question's own words", QUESTION in system, True)
        check("...and the instruction to call the tool",
              "call `answer_conversation`" in system, True)
        check("the prompt now says the sentence alone records nothing",
              "Saying you took it records NOTHING" in system, True)
        check("an unrelated reply that never names it costs exactly one round",
              len(fake.messages.sent), 1)
        check("...and leaves the question open", C.get(cid)["state"], C.OPEN)

        section("07:19:49Z - it SAID it took the answer and called nothing")
        logged = []
        reply, fake = await _turn(
            [_says(_incident_reply(cid)),
             _calls_answer(cid, SAID)],
            SAID, logged, conversation=_as_bot_passes())
        conv = C.get(cid)
        # This is the check that fails when the fix is backed out: the reply is
        # relayed, one round runs, and the record still says nobody answered.
        check("the question is ANSWERED on the record", conv["state"], C.ANSWERED)
        check("...with his words, not the model's summary", conv.get("answer"), SAID)
        check("...bound as a judgement, which is what it was",
              any("via judged" in str(e.get("detail")) for e in conv["log"]), True)
        check("it took exactly one extra round", len(fake.messages.sent), 2)
        nudge = fake.messages.sent[1]["messages"][-1]
        check("the extra round is a harness note, and says so",
              nudge["role"] == "user" and "automatic check" in str(nudge["content"])
              and "NOTHING is recorded" in str(nudge["content"]), True)
        check("the reply he sees is the one it already wrote", reply,
              _incident_reply(cid))
        check("...with no correction, because it is true now",
              "Correction" in reply, False)
        check("and the miss is logged",
              any("no answer_conversation call" in m for m in logged), True)
        check("the lane no longer reads him as silent",
              initiative.consecutive_lapses(), 0)
        check("...and nothing is left outstanding to block the next question",
              initiative.outstanding(), [])

        section("If it STILL calls nothing, he is told - in the same message")
        q2 = _lane_question("did the new overlay read ok on stream?")
        cid2 = q2["id"]
        logged = []
        reply, fake = await _turn(
            [_says(f"Taking that as your answer to {cid2}. Logged."),
             _says("Recorded it.")],
            "yeah it looked fine", logged, conversation=_as_bot_passes())
        check("nothing was recorded, and the record says so",
              C.get(cid2)["state"], C.OPEN)
        check("the reply carries the automatic correction",
              "Correction (automatic check)" in reply, True)
        check("...naming the question", cid2 in reply.split("Correction")[1], True)
        check("...and saying nothing was recorded",
              "nothing was recorded" in reply.split("Correction")[1], True)
        check("the original sentence is kept, not silently deleted",
              f"Taking that as your answer to {cid2}" in reply, True)
        check("the second round's words are NOT relayed", "Recorded it." in reply, False)
        check("it does not loop - two rounds, then stop", len(fake.messages.sent), 2)

        section("Naming a question is not always claiming it - the model may say no")
        # "is that about cN or something else?" names the id honestly. The verify
        # round asks; the model declines in the agreed words; nothing is bound and
        # nothing is appended. A false positive costs one round and no noise.
        reply, fake = await _turn(
            [_says(f"Not sure if that's about {cid2} or the PC thing - which?"),
             _says("NOT AN ANSWER")],
            "eh maybe", [], conversation=_as_bot_passes())
        check("a declined check binds nothing", C.get(cid2)["state"], C.OPEN)
        check("...and adds no correction", "Correction" in reply, False)
        check("...and the reply is exactly what it first wrote", reply,
              f"Not sure if that's about {cid2} or the PC thing - which?")

        section("The honest path is untouched")
        # Round 1 makes the call, round 2 announces. No verify round on top.
        reply, fake = await _turn(
            [_calls_answer(cid2, "yeah it looked fine"),
             _says(f"Took that as your answer to {cid2}.")],
            "yeah it looked fine", [], conversation=_as_bot_passes())
        check("a real call binds", C.get(cid2)["state"], C.ANSWERED)
        check("...in the usual two rounds, with no third", len(fake.messages.sent), 2)
        check("...and no correction", "Correction" in reply, False)

        section("A reply that already bound in code is left alone")
        q3 = _lane_question("still want the weekly digest?")
        cid3 = q3["id"]
        C.answer(cid3, "yes", bound_by="reply")
        reply, fake = await _turn(
            [_says(f"Got it - {cid3} is answered: yes.")],
            "yes", [], conversation=C.get(cid3), already_bound=True)
        check("already_bound: naming the id is true, so one round only",
              len(fake.messages.sent), 1)
        check("...and no correction", "Correction" in reply, False)

        section("The ASKING queue gets the same check - 2026-09-15 was not the lane")
        a = C.open_conversation(TYLER, purpose="p", question="drop the retired row?",
                                origin="test")
        a = C.mark_delivered(a["id"])
        reply, fake = await _turn(
            [_says(f"Taking that as your answer to {a['id']}: yes, delete it.\n\n"
                   f"Answered and logged."),
             _calls_answer(a["id"], "go ahead and delete the opus one then, yeah.")],
            "go ahead and delete the opus one then, yeah.", [],
            conversation=_as_bot_passes(), queue=C.queue_for(TYLER))
        check("an announced-but-uncalled answer to a queued ask is recorded too",
              C.get(a["id"])["state"], C.ANSWERED)

        section("An id that was never in front of it is not a candidate")
        reply, fake = await _turn([_says("c9999 was ages ago, no idea.")],
                                  "what was c9999?", [], conversation=None)
        check("no open question, no verify round", len(fake.messages.sent), 1)
    finally:
        (C.STORE, C.BATCHES, initiative.STORE, initiative.LOG_MD,
         identity.OWNER_IDS) = real
        confirm.cancel()
        agent._client = None
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
