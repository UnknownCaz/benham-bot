"""
test_do_token_types.py - a confirm token is a STRING from the shell to the bot.

THE DEFECT (outbox, 2026-10-10 22:40 UTC). `benham.py do create_role ...
confirm_token=<a 6-hex token>` was refused with `confirm_token 7.53e+38 is unknown`.
do.py's key=value parser ran json.loads() on every value so lists could be
typed inline - and six hex characters shaped digits-e-digits are a valid JSON
number. The token left the PC as a float, travelled to the Mac as a
number, and confirm.get() looked up "7.53e+38" (a token like 753e36).
A correct token, refused.
Any token shaped digits-e-digits does this (`1e5`, `0e1234`, `753e36`).

The same parse bent other text the same way: content="1e5" became "100000.0",
content="true" became "True", and content="null" vanished as a missing value.

What is pinned here:
  1. the parser keeps confirm_token, and every str-typed parameter, as the
     exact text typed - no JSON decoding;
  2. list-typed parameters still take inline JSON (the reason json.loads was
     there), and int/bool parameters still decode to ints/bools - but only
     when the decode IS that type;
  3. through do.main(), the enqueued request carries the token as a str;
  4. the bot, given a token that is NOT a str (an old client), refuses it by
     saying so instead of "unknown", and fires nothing; given the same token
     as text, it redeems it.

Fully offline - the outbox, the remote and the Discord client are fakes.

    python test_do_token_types.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import sys

from benham import bot
from benham.cli import do
from benham.core import capabilities, confirm

import test_purge_guild_outbox as harness  # the real-poll_outbox fake guild

_fails = []

# Shaped like real tokens: six hex chars that JSON reads as numbers.
NUMBERISH = ["753e38", "1e5000", "0e1234", "100e10"]


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def parser():
    print("== the parser keeps a token as the text typed ==")
    for tok in NUMBERISH + ["1e5", "123456", "a1b2c3"]:
        out, err = do._parse_kv([f"confirm_token={tok}"])
        check(f"confirm_token={tok} stays the string {tok!r}",
              (err, out.get("confirm_token")), (None, tok))
    act = capabilities.REGISTRY["create_role"]
    out, err = do._parse_kv(["guild_id=736988645562646619", "name=1e5",
                             "confirm_token=753e38"], act)
    check("...also when the action is known", out.get("confirm_token"), "753e38")
    check("a str parameter keeps its text (name=1e5)", out.get("name"), "1e5")

    print("\n== str parameters are text, whatever they look like ==")
    send = capabilities.REGISTRY["send_message"]
    for text in ("1e5", "true", "null", "1.50", "[1, 2]", "007"):
        out, _ = do._parse_kv(["channel_id=5552", f"content={text}"], send)
        clean = capabilities.validate(send, out)
        check(f"content={text} reaches the action as {text!r}",
              clean.get("content"), text)

    print("\n== the reasons json.loads was there still work ==")
    lists = [k for k, a in capabilities.REGISTRY.items()
             if any(s.get("type") == "list" for s in a.params.values())]
    check("some action takes a list parameter", bool(lists), True)
    act = capabilities.REGISTRY[lists[0]]
    lkey = next(k for k, s in act.params.items() if s.get("type") == "list")
    out, _ = do._parse_kv([f'{lkey}=["a b", "c"]'], act)
    check(f"{lists[0]} {lkey}=[...] is decoded as a list", out.get(lkey), ["a b", "c"])
    out, _ = do._parse_kv([f"{lkey}=C:/x.png"], act)
    check("...and a bare value stays one string for _coerce to wrap",
          out.get(lkey), "C:/x.png")
    out, _ = do._parse_kv(["channel_id=809357286036078612", "content=hi"], send)
    clean = capabilities.validate(send, out)
    check("an int id is still an int in the request",
          out.get("channel_id"), 809357286036078612)
    check("...and after validation", clean.get("channel_id"), 809357286036078612)
    out, _ = do._parse_kv(["channel_id=1e5", "content=hi"], send)
    check("an int param typed as a float is NOT silently decoded",
          out.get("channel_id"), "1e5")
    out, _ = do._parse_kv(["guild_id=736988645562646619"], send)
    check("guild_id (accepted by every action) is an int",
          out.get("guild_id"), 736988645562646619)


def through_main():
    print("\n== do.main() enqueues the token as a str ==")
    seen = {}
    real = (do.outbox.enqueue, do.outbox.wait_result, do.remote.active)

    def fake_enqueue(face=None, **fields):
        seen.update(fields)
        return "fake.json"

    do.outbox.enqueue = fake_enqueue
    do.outbox.wait_result = lambda path, timeout=60: ({"status": "ok", "result": {}}, "sent")
    do.remote.active = lambda: False
    try:
        do.main(["create_role", "guild_id=736988645562646619", "name=Operator",
                 "confirm_token=753e38"])
    finally:
        do.outbox.enqueue, do.outbox.wait_result, do.remote.active = real
    check("the request carries confirm_token '753e38'",
          seen.get("confirm_token"), "753e38")
    check("...as a str", type(seen.get("confirm_token")).__name__, "str")


def bot_side():
    print("\n== the bot: a non-str token is refused by name, a str one redeems ==")
    real = (bot.client, bot.OUTBOX, bot.SENT, bot.FAILED, bot.log)
    bot.client = harness._Client()
    bot.log = [].append          # its own "FAILED <file>" lines would read as red
    try:
        confirm.cancel()
        res = harness._drive(harness.REQ_0826) or {}
        check("step one parks a preview", res.get("status"), "confirmation_required")
        # Re-key the parked item under a numeric-looking token, as uuid could have.
        p = confirm.consume(res.get("confirm_token"))
        p.token = "753e38"
        confirm._pending["753e38"] = p

        harness.touched.update(purged=0, sent=0)
        res = harness._drive(dict(harness.REQ_0826, confirm_token=7.53e+38)) or {}
        check("a float token is refused", res.get("status"), "failed")
        check("...saying a token is text, not 'unknown'",
              "must be text" in str(res.get("error")), True)
        check("...and nothing was purged", harness.touched["purged"], 0)
        check("...and the real item is still pending", confirm.get("753e38") is p, True)

        res = harness._drive(dict(harness.REQ_0826, confirm_token="753e38")) or {}
        check("the same token as text redeems", res.get("status"), "ok")
        check("...and fires the previewed action", harness.touched["purged"], 1)
    finally:
        bot.client, bot.OUTBOX, bot.SENT, bot.FAILED, bot.log = real
        confirm.cancel()


def main():
    parser()
    through_main()
    bot_side()
    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
