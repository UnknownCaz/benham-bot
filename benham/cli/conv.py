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

Closing here does NOT tell the counterparty - conversations.close records the
decision, and telling them is a separate, deliberate act (`mark_told`, or a real
message). Collapsing the two would let a failed delivery look like a shut loop.
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
    extra = ""
    if c.get("state") == C.ANSWERED:
        extra = f"  -> {' '.join(str(c.get('answer','')).split())[:60]}"
    elif c.get("state") in C.TERMINAL_STATES and c.get("outcome"):
        extra = f"  -> {c['outcome'][:60]}"
    return f"  {mark:2} {c['id']:<5} [{c.get('state'):8}] {who}  {q}{extra}"


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
        if c.get("answer"):
            print(f"  ANSWER  : {c['answer']}")
        if c.get("outcome"):
            print(f"  outcome : {c['outcome']}")
        print(f"  nudges  : {c.get('nudges', 0)}   due: {c.get('due_at')}")
        print("  log:")
        for e in c.get("log", []):
            print(f"    {e['ts'][:19]}  {e['event']}"
                  + (f" - {e['detail']}" if e.get("detail") else ""))
        return 0

    try:
        if a.cmd == "close":
            c = C.close(a.id, a.outcome)
            print(f"{c['id']} closed: {c['outcome']}")
            print("(this records the decision - it does NOT tell them. Send that "
                  "yourself, or the loop is only shut from your side.)")
        elif a.cmd == "bank":
            c = C.bank(a.id, a.reason)
            print(f"{c['id']} banked: {a.reason}  (the question is kept)")
    except (KeyError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
