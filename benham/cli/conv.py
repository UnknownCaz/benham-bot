"""
conv.py - read and close conversations from the shell.

The companion to `ask`. A conversation outlives the session that opened it, which
is the whole design - and that only pays off if a LATER session can find it, read
the answer, and shut the loop.

    python benham.py conv list                  # everything still live
    python benham.py conv list --all            # including closed and banked
    python benham.py conv show c7               # one, with its full event log
    python benham.py conv close c7 "fixed in a1b2c3"
    python benham.py conv bank c7 "not worth chasing"

`close` takes an outcome and refuses without one, the same as the module it calls.
A silent close is the bug this stage exists to end: work that looks finished from
the inside and reads as silence from the outside.

    python benham.py conv close c7 "fixed" --tell        # and DM them the outcome

Closing and TELLING stay two things even behind one flag. close() records the
decision here and now; --tell queues a message the running bot delivers, and the
"told" event is written by the capability when it actually goes out. So a failed
delivery can never look like a shut loop - which is the exact failure this stage
exists to end.

Direction shows in `list` as an arrow: `->` is something we asked them, `<-` is
something they told us and we owe an answer to.
"""

import argparse
import sys

from benham.core import conversations as C

_STATE_MARK = {
    C.OPEN: "?", C.NUDGED: "??", C.ANSWERED: "!",
    C.CLOSED: "x", C.BANKED: "-",
}


def _line(c):
    mark = _STATE_MARK.get(c.get("state"), " ")
    q = " ".join(str(c.get("question", "")).split())[:80]
    who = c.get("counterparty")
    arrow = "<-" if c.get("direction") == C.OWED else "->"
    extra = ""
    if c.get("state") == C.ANSWERED:
        extra = f"  -> {' '.join(str(c.get('answer','')).split())[:60]}"
    elif c.get("state") in C.TERMINAL_STATES and c.get("outcome"):
        extra = f"  -> {c['outcome'][:60]}"
    return f"  {mark:2} {c['id']:<5} [{c.get('state'):8}] {arrow} {who}  {q}{extra}"


def main(argv):
    ap = argparse.ArgumentParser(prog="benham.py conv")
    sub = ap.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="Conversations, live by default")
    p_list.add_argument("--all", action="store_true", help="Include closed and banked")

    p_show = sub.add_parser("show", help="One conversation with its event log")
    p_show.add_argument("id")

    p_close = sub.add_parser("close", help="Shut the loop, with an outcome")
    p_close.add_argument("id")
    p_close.add_argument("outcome", help="What happened - required, and it is the point")
    p_close.add_argument("--tell", action="store_true",
                         help="Also DM the counterparty the outcome (the beat Doom "
                              "asked for). Needs the bot running.")
    p_close.add_argument("--note", default=None,
                         help="Extra line for them - detail, or sorry about the wait")

    p_bank = sub.add_parser("bank", help="Give up waiting, keep the question")
    p_bank.add_argument("id")
    p_bank.add_argument("reason", nargs="?", default="dropped by hand")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0

    if a.cmd == "list":
        rows = C.all_conversations()
        if not a.all:
            rows = [c for c in rows if c.get("state") in C.LIVE_STATES
                    or c.get("state") == C.ANSWERED]
        if not rows:
            print("nothing open" + ("" if a.all else " (--all for closed ones too)"))
            return 0
        for c in rows:
            print(_line(c))
        return 0

    if a.cmd == "show":
        c = C.get(a.id)
        if c is None:
            print(f"no conversation {a.id!r}", file=sys.stderr)
            return 1
        print(f"{c['id']}  [{c['state']}]  counterparty {c['counterparty']}")
        print(f"  purpose : {c.get('purpose')}")
        print(f"  question: {c.get('question')}")
        if c.get("project"):
            print(f"  project : {c['project']}")
        if c.get("origin"):
            print(f"  asked by: {c['origin']}")
        if c.get("asker_session"):
            # The routable owner - Raven delivers the answer to this session.
            print(f"  asker session: {c['asker_session']}")
        if c.get("answer"):
            print(f"  ANSWER  : {c['answer']}")
        if c.get("outcome"):
            print(f"  outcome : {c['outcome']}")
        cap = f" (cap {c['nudge_cap']})" if c.get("nudge_cap") is not None else ""
        print(f"  nudges  : {c.get('nudges', 0)}{cap}   due: {c.get('due_at')}")
        print("  log:")
        for e in c.get("log", []):
            print(f"    {e['ts'][:19]}  {e['event']}"
                  + (f" - {e['detail']}" if e.get("detail") else ""))
        return 0

    try:
        if a.cmd == "close":
            c = C.close(a.id, a.outcome)
            print(f"{c['id']} closed: {c['outcome']}")
            if a.tell:
                # Through the outbox so the RUNNING bot delivers it; this process
                # has no Discord connection. Queued, not sent - the "told" event is
                # written by the capability when the message actually goes out, so
                # a delivery failure can never look like a shut loop.
                from benham.core import outbox
                fields = {"action": "tell_conversation", "id": c["id"]}
                if a.note:
                    fields["note"] = a.note
                outbox.enqueue(**fields)
                print(f"queued the outcome to {c['counterparty']} - "
                      f"`conv show {c['id']}` will show a counterparty-told event "
                      "once it lands")
            else:
                print("(this records the decision - it does NOT tell them. Use "
                      "--tell, or the loop is only shut from your side.)")
        elif a.cmd == "bank":
            c = C.bank(a.id, a.reason)
            print(f"{c['id']} banked: {a.reason}  (the question is kept)")
    except (KeyError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
