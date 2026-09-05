"""
test_notify.py - how loudly Benham may interrupt Tyler.

Stage 3 item 11. The rundown found forty-nine Claude-initiated DMs with no type
and no priority, all buzzing his phone equally - including a friend filing a bug
at 23:51. Two tiers now: blocked/broke wake the phone, finished/answered land
quietly and are there when he looks.

The checks that matter are not "does the map contain four rows". They are:

  CALLERS STATE THE FACT, NOT THE VOLUME. Nothing outside notify.py decides
  whether something is loud. If a call site could pass tier="buzz" directly, the
  policy would drift back out to forty-nine places, which is how it got lost the
  first time.

  AN UNKNOWN KIND IS REFUSED. Both defaults fail silently - quiet means a new
  class of breakage stops waking him, buzz means fatigue until he ignores the
  channel - so the only safe behaviour is to make someone classify it.

  QUIET IS NOT DROPPED. It maps to Discord's silent=True, which suppresses the
  push and delivers the message. A tier that swallowed news would be worse than
  the problem it replaced.

    python test_notify.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio
import sys

from benham.core import capabilities, identity, notify, policy

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def main():
    section("The tiers Tyler chose")
    check("blocked wakes him", notify.tier_for("blocked"), notify.BUZZ)
    check("broke wakes him", notify.tier_for("broke"), notify.BUZZ)
    check("a finished task waits", notify.tier_for("finished"), notify.QUIET)
    check("a collaborator replying waits", notify.tier_for("answered"), notify.QUIET)

    section("An unknown kind is refused, not defaulted")
    try:
        notify.tier_for("vibes")
        refused = False
    except ValueError as e:
        refused = True
        check("...and the refusal says what to do about it",
              "notify.KINDS" in str(e), True)
    check("an unclassified kind raises", refused, True)

    section("Quiet means no push, not no message")
    check("quiet maps to Discord's silent flag", notify.is_silent("answered"), True)
    check("buzz does not", notify.is_silent("broke"), False)

    section("The capability, through the real chokepoint")
    sent = []

    class _DM:
        async def send(self, content=None, silent=False, **kw):
            sent.append((content, silent))
            return type("M", (), {"id": 1})()

    class _User:
        def __init__(self, uid):
            self.id = uid
            self.dm_channel = _DM()

    class _Client:
        def get_user(self, uid):
            return _User(int(uid))

    async def notify_owner(kind, content):
        res, _ = await capabilities.run(
            _Client(), lambda *a: None, "notify_owner",
            {"kind": kind, "content": content},
            force=True, call_ctx=policy.CallContext.system())
        return res

    r = asyncio.run(notify_owner("broke", "the minecraft server died"))
    check("a break buzzes", r["silent"], False)
    check("...and the message still went", sent[-1][0], "the minecraft server died")

    r = asyncio.run(notify_owner("answered", "Doom filed an idea"))
    check("a collaborator reply is silent", r["silent"], True)
    check("...but IS delivered - quiet is not dropped", sent[-1][0], "Doom filed an idea")

    section("A caller cannot choose the volume")
    # There is no tier parameter. Saying "kind" is the only way to speak, so the
    # policy cannot drift back out to the call sites.
    act = capabilities.REGISTRY["notify_owner"]
    check("the capability takes no tier/silent parameter",
          {"tier", "silent", "loud"} & set(act.params or {}), set())
    try:
        asyncio.run(notify_owner("vibes", "something happened"))
        unclassified_sent = True
    except Exception:
        unclassified_sent = False
    check("an unclassified kind cannot be sent at all", unclassified_sent, False)

    section("It is reachable by a watchdog, which is the point")
    # A server dying is the archetypal `broke` and has nobody to ask.
    check("SYSTEM can notify", policy.Origin.SYSTEM in act.origins, True)
    check("...but a guest cannot", policy.Origin.GUEST_DM in act.origins, False)

    # Item 13 triage_conversation (a read-only Claude Code session) left with
    # the PC lane in Phase B (INTENT 39); its section stood here.

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
