"""initiate.py - Claude reaching out to Tyler first, instead of only ever replying.

    python benham.py initiate status                  # may I speak, and why not
    python benham.py initiate threads                 # the open loops I am holding
    python benham.py initiate note "..." --why "..."  # hold a new one
    python benham.py initiate drop t3 "..."           # decide one is not worth it
    python benham.py initiate ask "..." --dry-run     # would this be allowed?
    python benham.py initiate ask "..." --thread t3   # send it
    python benham.py initiate silent "..."            # log a run that said nothing
    python benham.py initiate log                     # what it has been deciding

THE POINT. Every Claude session is reactive - nothing runs unless Tyler types -
so "I'll check tomorrow whether that worked" has never been a promise Claude
could keep, and Claude has never been able to be curious about him on its own.
A daily scheduled job drives this CLI: it wakes, reads real state, and almost
always decides there is nothing worth asking. When there is, one question goes
out as a DM from Benham.

SILENCE IS THE PRODUCT. `silent` is the command this thing should run most days,
and it is a real command with a real record rather than a no-op, because the
only way Tyler can tell "quiet because it is working" from "quiet because it
broke in March" is a log that says which. A run that decides nothing and logs
nothing is indistinguishable from a run that never happened.

WHAT THIS CLI DOES NOT DECIDE. Whether a question may go is
policy.authorize_unprompted. `--dry-run` asks the very same function the bot
asks at delivery time, so a dry run that says yes and a live send that says no
can only differ by something that genuinely changed in between.
"""

import argparse
import json
import sys

from benham import paths
from benham.core import conversations, initiative, outbox, policy


def _print_lane(state, stream=sys.stdout):
    p = lambda s: print(s, file=stream)  # noqa: E731 - three uses, one line
    if state["may_ask"]:
        p("lane: CLEAR - a question may go out now.")
    else:
        p("lane: CLOSED - " + str(state["why_not"]))
    if state["outstanding"]:
        for c in state["outstanding"]:
            p(f"  waiting on him: [{c['id']}] {c['question'][:100]}")
            p(f"      sent {str(c['delivered_at'])[:16]}Z")
    last = state["last_delivered_at"]
    p(f"  last question: {str(last)[:16] + 'Z' if last else 'never'}"
      + (f" ({state['hours_since_last']}h ago, floor {state['min_gap_hours']}h)"
         if last else ""))
    if state["dormant"]:
        p(f"  DORMANT: {state['consecutive_lapses']} in a row went unanswered. "
          "This is the designed response to not landing, not a fault. "
          "`initiate reset` clears it, and so does him answering one.")
    p(f"  open threads: {state['open_threads']}   runs logged: {state['runs_logged']}")


def _print_threads(rows, stream=sys.stdout):
    if not rows:
        print("  (no open threads)", file=stream)
        return
    for t in rows:
        gate = f"  not before {t['not_before']}" if t.get("not_before") else ""
        print(f"  [{t['id']}] {t['text']}{gate}", file=stream)
        if t.get("why"):
            print(f"        why: {t['why']}", file=stream)
        if t.get("source"):
            print(f"        from: {t['source']}", file=stream)
        print(f"        noted {str(t.get('noted_at'))[:16]}Z, {t['state']}",
              file=stream)


def cmd_status(a):
    state = initiative.lane_state()
    if a.json:
        print(json.dumps(state, indent=2))
        return 0
    _print_lane(state)
    return 0


def cmd_threads(a):
    rows = initiative.threads(state=(None if a.all else initiative.T_OPEN),
                              askable_on=a.askable_on)
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    _print_threads(rows)
    return 0


def cmd_note(a):
    t = initiative.add_thread(a.text, why=a.why, source=a.source,
                              not_before=a.not_before)
    print(f"holding [{t['id']}] {t['text']}")
    if not a.why:
        # Not an error, but worth saying. A thread with no stated reason is the
        # raw material of a manufactured question six weeks from now, when
        # nothing is left of the context except the sentence.
        print("  (no --why given - a later run has only these words to judge by, "
              "and will drop it if it cannot see why it matters)", file=sys.stderr)
    return 0


def cmd_drop(a):
    t = initiative.drop_thread(a.id, a.reason)
    print(f"dropped [{t['id']}] {t['text']}\n  because: {a.reason}")
    return 0


def cmd_close(a):
    t = initiative.close_thread(a.id, a.outcome)
    print(f"closed [{t['id']}] {t['text']}\n  outcome: {a.outcome}")
    return 0


def cmd_sweep(a):
    notes = initiative.sweep()
    if not notes:
        print("nothing to sweep")
    for n in notes:
        print(n)
    return 0


def cmd_reset(a):
    when = initiative.reset()
    print(f"lane reset at {when} - the lapse count starts again from here.")
    return 0


def cmd_log(a):
    print(initiative.read_markdown(tail_bytes=int(a.bytes)))
    return 0


def cmd_silent(a):
    """Record a run that decided to say nothing. THE COMMON CASE.

    Deliberately requires a reason. "Nothing to ask" as a bare fact is not
    auditable - it reads identically whether the run swept nine boards and three
    threads and concluded correctly, or whether it fell over before it started.
    """
    rec = initiative.record_run(initiative.R_SILENT, a.reason,
                                read=(a.read or []))
    print(f"logged a silent run at {rec['at'][:16]}Z")
    return 0


def cmd_ask(a):
    """Write the one question, run it past the gate, and send it if it passes."""
    # Housekeeping first, always. sweep() is what turns "he never answered that
    # one" from a permanent blockage into a lapse, so a run that skipped it could
    # be refused by a question that expired days ago.
    initiative.sweep()

    # Ask the REAL gate about the real text, before anything is written down. A
    # refused question should leave no trace in the conversation store - the
    # record of it belongs in the run log, as a `blocked` entry, and nowhere else.
    owner = sorted(policy.identity.OWNER_IDS)
    if not owner:
        print("no owner is configured in control.json - there is nobody to ask.",
              file=sys.stderr)
        return 2
    probe = {"id": "(dry-run)", "direction": conversations.UNPROMPTED,
             "counterparty": owner[0], "question": a.question}
    verdict = policy.authorize_unprompted(probe)

    if not verdict.allowed:
        print(f"REFUSED [{verdict.rule}]\n  {verdict.reason}", file=sys.stderr)
        if not a.dry_run:
            initiative.record_run(
                initiative.R_BLOCKED,
                a.why or "the run judged this worth asking; policy disagreed.",
                question=a.question, thread_id=a.thread, gate=verdict.rule,
                read=(a.read or []))
        return 3

    if a.dry_run:
        print("WOULD SEND (the gate allows it):")
        print(f"  {a.question}")
        print("\nNothing was opened and nothing was logged. Drop --dry-run to send.")
        return 0

    conv = initiative.open_question(a.question, purpose=a.why, thread_id=a.thread)
    if a.thread:
        initiative.mark_thread_asked(a.thread, conv["id"])

    # Delivered by the RUNNING bot, through the outbox, exactly as `ask` does.
    # This process has no Discord connection and must not grow one - and routing
    # through the outbox is what puts the send back through policy at the moment
    # it actually happens.
    outbox.enqueue(face=paths.DEFAULT_FACE, action="deliver_unprompted", id=conv["id"])
    initiative.record_run(initiative.R_ASKED,
                          a.why or "the run judged this worth asking.",
                          question=a.question, conv_id=conv["id"],
                          thread_id=a.thread, read=(a.read or []))
    print(f"queued [{conv['id']}] for delivery to {conv['counterparty']}:")
    print(f"  {a.question}")
    print("\nThe bot sends it within ~2s. It will NOT be nudged, and if he never "
          "answers, it lapses quietly.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="benham.py initiate",
        description="Claude reaching out to Tyler first. Silence is the default.")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="may a question go out right now, and why not")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("threads", help="the open loops Claude is holding")
    s.add_argument("--all", action="store_true", help="include closed and dropped")
    s.add_argument("--askable-on", default=None, metavar="YYYY-MM-DD",
                   help="hide threads not yet due to be asked about")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_threads)

    s = sub.add_parser("note", help="hold a new open loop for a later run")
    s.add_argument("text", help="the unresolved thing, in your own words")
    s.add_argument("--why", default=None,
                   help="why it is worth his attention. Write it honestly.")
    s.add_argument("--source", default=None,
                   help="where it came from (a session, a board, a memory)")
    s.add_argument("--not-before", default=None, metavar="YYYY-MM-DD",
                   help="do not ask about this before then")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("drop", help="decide an open loop is not worth asking")
    s.add_argument("id")
    s.add_argument("reason")
    s.set_defaults(fn=cmd_drop)

    s = sub.add_parser("close", help="an open loop resolved")
    s.add_argument("id")
    s.add_argument("outcome")
    s.set_defaults(fn=cmd_close)

    s = sub.add_parser("ask", help="send ONE question, if the gate allows it")
    s.add_argument("question", help="the whole message. One question, Benham's voice.")
    s.add_argument("--thread", default=None, help="the open loop this closes")
    s.add_argument("--why", default=None,
                   help="why this is worth asking today. Goes in the log.")
    s.add_argument("--read", action="append", default=None, metavar="WHAT",
                   help="something this run actually read. Repeatable.")
    s.add_argument("--dry-run", action="store_true",
                   help="run the real gate and print the verdict; send nothing, "
                        "open nothing, log nothing")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("silent", help="log a run that decided to say nothing")
    s.add_argument("reason", help="what you looked at and why none of it earned a message")
    s.add_argument("--read", action="append", default=None, metavar="WHAT",
                   help="something this run actually read. Repeatable.")
    s.set_defaults(fn=cmd_silent)

    s = sub.add_parser("sweep", help="lapse expired questions, reconcile threads")
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("reset", help="clear the dormant state (human only)")
    s.set_defaults(fn=cmd_reset)

    s = sub.add_parser("log", help="the human-readable run log")
    s.add_argument("--bytes", default=8000, help="how much of the tail to print")
    s.set_defaults(fn=cmd_log)
    return ap


def main(argv):
    outbox.console_utf8()
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
