"""
test_claims.py - a reply may not announce a confirmation that was never parked.

THE EVENING THIS EXISTS FOR. 2026-08-17, twice. Benham told Tyler "Retrying now -
preview should be waiting on your end" at 22:21:06 and "Resent - preview should be
up now" at 23:39:09. Neither turn called a tool. Every real preview leaves a
`PROPOSED dm_user ... [rule=outward_tainted]` line in supervise.log and there are
six of them that night; neither of those two has one behind it. The 23:39 one is
provable from token accounting alone - `agent usage [round 1] in=3745 out=56` and
then nothing, where every turn that calls a tool has a round 2 and an action line
between them. He waited over two hours for a button that did not exist.

It had already caught itself once, at 22:23:33 - "the 'previews' I described
earlier were never real" - and did it again seventy-six minutes later. So the thing
being tested here is deliberately NOT the model's care. A turn cannot inherit the
last turn's contrition, and any fix resting on that is not a fix.

INTENT.md §3.3, third recurrence. What is new this time is that the harness sends
the real preview itself: bot.py renders confirm.describe(parked) with buttons right
after the reply whenever something is parked. So the sentence is redundant when
true and load-bearing only when false, which is what makes correcting it free.

The split below follows that asymmetry. `confirm.current()` is the certain half and
gets the end-to-end checks; the wording match is the fuzzy half and gets a matrix,
because the way this becomes a nuisance is firing on true sentences until the
correction reads as wallpaper.

The sibling check `_verify_saved_claims` (invented downloads/ paths, same defect
class) is tested in test_attachments.py, where the disk fixtures already live.

Fully offline - the Anthropic client is a scripted fake, no API calls, no cost.

    python test_claims.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# Driving agent.respond() stores turns, so this file writes to the memory store
# whether it means to or not. It forgets its own keys on the way out, which made
# the leak invisible - what it could not undo is that the read-modify-write landed
# on the live state/ of whatever checkout ran the suite. Redirect first.
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import sys

from benham.core import agent
from benham.core import confirm
from benham.core import policy

TYLER = 273967061619965952
TESTING_CHAN = 753732380921167902

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
    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.sent = []          # every kwargs dict, so the SYSTEM PROMPT is checkable

    def create(self, **kw):
        self.sent.append(kw)
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return r


class _FakeAnthropic:
    def __init__(self, script):
        self.messages = _Messages(script)


class _StubClient:
    user = _Block(id=752313060970201218)

    def get_channel(self, cid):
        return None

    def get_guild(self, gid):
        return None


# --------------------------------------------------------------------------- harness

async def _turn(key, said, said_back, logged):
    """One agent turn whose model output is exactly `said_back` and calls nothing.

    The shape of both real incidents: a single round, text only, no tool_use. The
    fake never parks anything, so confirm stays empty exactly as it did that night.
    """
    agent._client = _FakeAnthropic([_Resp([_Block(type="text", text=said_back)],
                                          "end_turn")])
    agent._last_call.clear()
    agent.forget(key)
    reply, pending = await agent.respond(
        _StubClient(), logged.append, said,
        actor_id=TYLER, actor_name="caz6666", channel_id=TESTING_CHAN,
        guild_id=None, where="a DM", conversation_key=key,
        call_ctx=policy.CallContext.owner_dm(TYLER, TESTING_CHAN))
    return reply, pending


def _system_sent():
    """Everything the last turn actually put in the system parameter, joined.

    Asserted on this rather than on _system_blocks' return value: a block that is
    built and never rendered is indistinguishable from a working one, which is the
    trap this repo has already stepped in once (see the recently_terminal commit on
    claude/delivery-flag-race). Going through respond() proves it reaches the API.
    """
    return "\n\n".join(b["text"] for b in agent._client.messages.sent[-1]["system"])


def _park():
    """A confirmation exactly as capabilities.run/agent.respond would leave one."""
    return confirm.park("dm_user", {"user_id": TYLER}, {"summary": "DM Tyler a file"},
                        TYLER, "dm")


async def main():
    if not agent.ENABLED:
        print("agent disabled (no ANTHROPIC_API_KEY) - the end-to-end checks need "
              "the module live, but make no API calls. Set any non-empty key.")
        return 1

    V = agent._verify_confirmation_claims
    keys = ["test:claims_22_21", "test:claims_23_39", "test:claims_real",
            "test:claims_told_empty", "test:claims_told_live"]
    try:
        section("The two sentences that cost him two hours")
        # 22:21:06, verbatim. Nothing was parked, nothing was called, and the reply
        # sent him to look for a button. This is the check that fails when the fix
        # is backed out.
        confirm.cancel()
        logged = []
        reply, pending = await _turn(
            "test:claims_22_21",
            "sorry wasnt looking for the prompt again can you send it again?",
            "Retrying now - preview should be waiting on your end.", logged)
        check("nothing was actually parked", pending, None)
        check("22:21 - the phantom preview is corrected, not relayed",
              "Correction (automatic check)" in reply, True)
        check("and the correction says nothing is parked",
              "nothing is actually parked" in reply, True)
        check("the original sentence is kept, not silently deleted",
              "preview should be waiting on your end" in reply, True)
        check("and it is logged",
              any("claimed a pending confirmation" in m for m in logged), True)

        # 23:39:09, verbatim.
        confirm.cancel()
        reply, _ = await _turn(
            "test:claims_23_39",
            "can you resend the prompt inmossed the window",
            "Resent - preview should be up now.", [])
        check("23:39 - same again, seventy-six minutes later",
              "Correction (automatic check)" in reply, True)

        section("A preview that DOES exist is left alone")
        # The whole point of checking confirm.current() rather than the words: when
        # the claim is true it must pass untouched, because bot.py is about to send
        # the real thing right underneath it.
        confirm.cancel()
        _park()
        reply, _ = await _turn(
            "test:claims_real", "dm me the dossier",
            "Queued it - the preview is waiting on your end.", [])
        check("a real parked confirmation passes untouched",
              "Correction" in reply, False)
        check("and confirm still holds it", confirm.current() is not None, True)

        # A confirmation parked on an EARLIER turn is still a true claim now: the
        # window is ten minutes, so "yes it's still up" is answerable and honest.
        check("a still-live preview from an earlier turn also passes",
              "Correction" in V("The preview is still up on your end."), False)
        confirm.cancel()

        section("What counts as claiming one is waiting")
        # Both incident sentences, at the unit level.
        check("'preview should be waiting on your end'",
              "Correction" in V("Retrying now - preview should be waiting on your end."), True)
        check("'preview should be up now'",
              "Correction" in V("Resent - preview should be up now."), True)
        check("a confirmation said to be pending",
              "Correction" in V("There's a confirmation pending for you."), True)
        check("an approval prompt said to have been sent",
              "Correction" in V("I just sent the approval prompt."), True)
        check("in your DMs",
              "Correction" in V("The preview is in your DMs."), True)

        section("What does not - the sentences that must never be nagged")
        check("a bare reply with no confirmation talk", V("all good"), "all good")
        check("saying it EXPIRED is the honest answer, not a claim",
              "Correction" in V("That preview expired - want me to make a new one?"), False)
        check("a question is not a claim",
              "Correction" in V("Should the preview be waiting on your end?"), False)
        check("a hypothetical is not a claim",
              "Correction" in V("Sending that would need your confirmation first."), False)
        check("an explicit denial is not a claim",
              "Correction" in V("There's no preview waiting - I never sent one."), False)
        check("describing the mechanism is not a claim",
              "Correction" in V("Destructive tools return a preview instead of running."), False)
        # The sentence-scoped split earns its keep here: every word of a false claim
        # is present, spread across two sentences that are individually true.
        check("a true denial followed by an offer stays quiet",
              "Correction" in V("The preview expired. Want me to send it again?"), False)

        section("It is TOLD whether one is parked - both times he asked, it could not see")
        # He asked twice that night about an object this loop has no window onto:
        # "can you send it again?" and "can you resend the prompt inmossed the
        # window". It answered anyway. Same shape as the conversation block being
        # told only what was live - absent a true account, it produced a plausible
        # one. This does not make the fabrication impossible; it removes the reason.
        confirm.cancel()
        await _turn("test:claims_told_empty", "can you send it again?", "Sure.", [])
        empty = _system_sent()
        check("with nothing parked, the prompt says so",
              "Nothing is awaiting his confirmation" in empty, True)
        check("and names the ten-minute expiry, which is what he actually hit",
              "ten minutes" in empty, True)
        check("and tells it to make a real one rather than announce one",
              "call the tool again to make a real one" in empty, True)
        check("and keeps it background, so it is not a standing topic",
              "do not bring it up unless he does" in empty, True)

        confirm.cancel()
        p = _park()
        await _turn("test:claims_told_live", "did that go through?", "Yep.", [])
        live = _system_sent()
        check("with one parked, the prompt names the action", "`dm_user`" in live, True)
        check("and that it is the harness that sent the buttons",
              "the harness sends it, not you" in live, True)
        check("and the false half is NOT also rendered",
              "Nothing is awaiting his confirmation" in live, False)
        check("the parked one is untouched by being described",
              confirm.get(p.token) is not None, True)
        confirm.cancel()

        section("The standing rule, in the cached half so it renders every turn")
        # The weakest of the three layers and deliberately last. It is here because
        # the existing rule covered the false "it's done" and said nothing about the
        # false "it's waiting" - and because it was scoped to DESTRUCTIVE tools,
        # while the confirmation he was waiting for came from dm_user, which is
        # tier 1 and got a preview only because the turn was tainted.
        static, _vol = agent._system_blocks("a DM", "caz6666")
        check("the rule is in the STATIC block, not a conditional one",
              "do not tell him a preview or confirmation is WAITING" in static, True)
        # Two shorter fragments rather than one sentence: the prompt is hard-wrapped,
        # so a phrase spanning a line break contains a newline and never matches.
        # The first draft of this check asserted the whole sentence and failed for
        # that reason alone, which is worth leaving a note about - a check can be
        # green or red for reasons that have nothing to do with what it is about.
        check("it says what actually creates one",
              "Exactly two things put one there" in static
              and "came back as a preview" in static, True)
        check("it covers the taint path, not only destructive tools",
              "Destructive tools are not the only source" in static, True)
        check("and it names the harness as the sender",
              "sent by the harness, not by you" in static, True)
        check("the old rule about the opposite lie is untouched",
              "Never claim a destructive" in static, True)

        section("Shape")
        check("an empty reply is returned as-is", V(""), "")
        check("None survives", V(None), None)
        check("the check runs after the download check, so both can fire",
              agent._verify_saved_claims.__name__ in
              __import__("inspect").getsource(agent.respond)
              and "_verify_confirmation_claims" in
              __import__("inspect").getsource(agent.respond), True)
    finally:
        confirm.cancel()
        agent._client = None
        for k in keys:
            agent.forget(k)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
