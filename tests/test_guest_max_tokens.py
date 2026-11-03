"""
test_guest_max_tokens.py - a guest reply that hits max_tokens is retried once,
and never reaches the guest cut off in silence (INTENT 61/62, Stage 9 B).

THE AFTERNOON THIS EXISTS FOR. 2026-11-03 14:2x local: a friend asked Benham to
help find a half-remembered game. The guest lane searched the web every turn with
max_tokens 500, and each reply was cut off BEFORE the search results were written
- the log shows out=487/758/595 with one-sentence replies. He got three apologies
and no names. The owner agent fixed this same shape on 2026-10-11
(agent._create, test_max_tokens.py); the guest path never got it.

Pinned here, replaying that shape with a scripted client:
  - the first response stops at max_tokens with a web_search block and NO text;
    it is asked again ONCE at guest.MAX_TOKENS_RETRY, and the complete second
    answer is what the guest reads, with no cut-off line;
  - the turn's searches are logged once each and charged ONCE (a retry is this
    lane's doing, not a second searched turn of theirs), and both calls' usage
    lines are in the log;
  - cut off twice: what was written is kept and a visible one-line notice says
    so; cut with nothing written: the notice alone;
  - an ordinary turn is one call, untouched;
  - the call still carries no client tool - only server-side web search.

Fully offline - no API calls, no cost.

    python test_guest_max_tokens.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import os
import sys
import tempfile

from benham.core import jsonio
from benham.guest import guest

GUEST = _testconfig.GUEST_ID

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


class _Resp:
    def __init__(self, content, stop_reason, out=0):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Block(input_tokens=100, output_tokens=out,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)


class _Messages:
    def __init__(self, script):
        self.script, self.calls, self.sent = script, 0, []

    def create(self, **kw):
        self.sent.append(kw)
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return r


class _Fake:
    def __init__(self, script):
        self.messages = _Messages(script)


def _search(q):
    return _Block(type="server_tool_use", id=f"srv_{abs(hash(q)) % 10000}",
                  name="web_search", input={"query": q})


def _result():
    return _Block(type="web_search_tool_result", tool_use_id="srv", content=[])


def _text(t):
    return _Block(type="text", text=t)


_tmp = tempfile.mkdtemp(prefix="benham-guest-maxtok-")
guest.MEMORY_FILE = os.path.join(_tmp, "guest_memory.json")
guest.USAGE_FILE = os.path.join(_tmp, "guest_usage.json")
guest.SEARCH_LOG = os.path.join(_tmp, "guest_searches.jsonl")
guest.WEB_SEARCH = True
guest.MAX_TOKENS = 500            # the cap of 14:2x, to replay it exactly
guest.MAX_TOKENS_RETRY = 8000


def turn(script, said="it was a 2d platformer, ps2 era, a robot with a scarf"):
    guest._client = _Fake(script)
    guest.forget(GUEST)
    jsonio.write_json(guest.USAGE_FILE, {})
    try:
        os.remove(guest.SEARCH_LOG)
    except OSError:
        pass
    logged = []
    reply = guest.respond(GUEST, said, logged.append)
    spent, _ = guest.spent_today(GUEST)
    searches = []
    if os.path.exists(guest.SEARCH_LOG):
        with open(guest.SEARCH_LOG, encoding="utf-8") as f:
            searches = [line for line in f if line.strip()]
    return reply, logged, guest._client.messages.sent, spent, searches


def cut(*content):
    return _Resp(list(content), "max_tokens", out=guest.MAX_TOKENS)


ANSWER = ("Sounds like it could be one of these:\n1. Robo Scarf Adventures (2004)\n"
          "2. Tin Knight (2003)\nAny of those ring a bell?")

# ------------------------------------------------------------------ 1
section("1. the 14:2x shape: searched, cut before any text - the retry has room")
reply, logged, sent, spent, searches = turn([
    cut(_search("2d platformer ps2 robot scarf"), _result()),
    _Resp([_search("2d platformer ps2 robot scarf"), _result(), _text(ANSWER)],
          "end_turn", out=900)])
check("the guest reads the full answer, names and all", reply, ANSWER)
check("no cut-off line when the retry had room", "Cut off" in reply, False)
check("two calls: the cut one and its retry", len(sent), 2)
check("the first at the configured cap", sent[0]["max_tokens"], 500)
check("the retry at guest.max_tokens_retry", sent[1]["max_tokens"], 8000)
check("the retry is the same request (same messages, same system, same tools)",
      (sent[1]["messages"] == sent[0]["messages"], sent[1]["system"] == sent[0]["system"],
       sent[1]["tools"] == sent[0]["tools"]), (True, True, True))
check("the retry is in the log",
      any("hit max_tokens=500 - retrying once at 8000" in line for line in logged), True)
check("both calls' usage lines are in the log (the bill is two calls)",
      sum(1 for line in logged if line.startswith("agent usage [guest:")), 2)
check("...the second marked retry",
      any(line.startswith(f"agent usage [guest:{GUEST} retry]") for line in logged), True)
check("the search is logged ONCE, though both calls made it", len(searches), 1)
check("...and the turn charged ONCE for searching (check() is not run here, so "
      "the search charge is all that shows)", spent, 1)

# ------------------------------------------------------------------ 2
section("2. cut off twice, with words: keep them, and say so in one line")
half = "Okay, a robot with a scarf on PS2 - my first guesses are Robo Sc"
reply, logged, sent, spent, searches = turn([
    cut(_search("robot scarf ps2"), _result(), _text(half)),
    cut(_search("robot scarf ps2"), _result(), _text(half))])
check("what was written is kept", reply.startswith(half), True)
check("followed by a visible one-line cut notice",
      reply.split("\n\n")[-1], guest.CUT_NOTICE)
check("exactly one retry, not a loop", len(sent), 2)
check("the log says it was still cut",
      any("still cut off at max_tokens" in line for line in logged), True)
check("searched once, charged once", (len(searches), spent), (1, 1))

# ------------------------------------------------------------------ 3
section("3. cut off twice with NO words: the notice alone, never 'nothing for that one'")
reply, logged, sent, spent, searches = turn([
    cut(_search("q one"), _result()), cut(_search("q two"), _result())])
check("the reply is the notice", reply, guest.CUT_NOTICE)
check("two different queries across the calls: both logged, charged once",
      (len(searches), spent), (2, 1))

# ------------------------------------------------------------------ 4
section("4. an ordinary turn is untouched: one call, no retry, no notice, no charge")
reply, logged, sent, spent, searches = turn([_Resp([_text("hey!")], "end_turn", out=3)])
check("reply unchanged", reply, "hey!")
check("exactly one call", len(sent), 1)
check("one usage line", sum(1 for line in logged if line.startswith("agent usage")), 1)
check("no search, no charge", (searches, spent), ([], 0))

# ------------------------------------------------------------------ 5
section("5. retry off when the retry cap is not larger: one call, the notice")
guest.MAX_TOKENS_RETRY = 500
reply, logged, sent, spent, searches = turn([cut(_text("Robo"))])
check("one call only", len(sent), 1)
check("kept and marked", reply, f"Robo\n\n{guest.CUT_NOTICE}")
guest.MAX_TOKENS_RETRY = 8000

# ------------------------------------------------------------------ 6
section("6. the lane's property holds: the only tool is server-side web search")
reply, logged, sent, spent, searches = turn([cut(_search("x"), _result()),
                                             _Resp([_text("ok")], "end_turn")])
for i, kw in enumerate(sent):
    tools = kw.get("tools") or []
    check(f"call {i + 1}: one tool, and it is web_search (server-side)",
          [t.get("type", "").startswith("web_search") for t in tools], [True])
check("what is remembered is the reply the guest read",
      guest._history(guest._key(GUEST))[-1]["content"], "ok")
check("default config: max_tokens_retry is 8000 unless config says otherwise",
      int(guest._CFG.get("max_tokens_retry", 8000)), 8000)

guest.forget(GUEST)
print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} FAILED'}")
sys.exit(1 if _fails else 0)
