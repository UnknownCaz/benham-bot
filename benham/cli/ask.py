"""
ask.py - a running session asks Tyler something and waits for the answer.

Stage 3, item 10b. The thing Tyler said the PC path was for in the first place:

    "the PC was meant to exist as a way to respond to claude when sessions were
     running and communication was needed/wanted"

What existed instead was the opposite - Tyler DMs `pc..`, a NEW session spawns,
does a task and reports. A session already running (one he started at the keyboard
and walked away from, a scheduled job, this one) could shout into the outbox and
had no way to hear anything back.

    python benham.py ask "which database should I use, sqlite or json?"
    python benham.py ask "ready to deploy?" --purpose "deploy gate" --timeout 1800
    python benham.py ask "..." --no-wait          # fire it and check later

THERE IS NO NEW TRANSPORT, and that is the point. Items 7, 8 and 10a already built
everything this needs: a conversation is persisted state with a counterparty, the
bot delivers and nudges it on a timer, and an answer binds when Tyler replies (or
when the model judges and says so). So "wait for an answer" is just watching a
file that another process writes - the same shape as the outbox, and immune to the
loopback-TCP failure this machine has actually had.

REGISTRATION is the `origin` field: the working directory and pid of whoever
asked. Deliberately not a handle or a socket. A session that dies mid-wait leaves
a conversation Tyler can still answer, and the answer is still recorded - it just
gets read by whoever asks next instead of routed into a process that no longer
exists. Losing the asker must not lose the question.
"""

import argparse
import os
import sys
import time

from benham import paths
from benham.core import conversations, identity, outbox


def _owner():
    ids = sorted(identity.OWNER_IDS)
    if not ids:
        print("No owner is configured in control.json - there is nobody to ask.",
              file=sys.stderr)
        raise SystemExit(2)
    return ids[0]


def _print_queue(q, stream=None):
    """What is already waiting on him, in the order he will see it."""
    stream = stream or sys.stdout
    if not q:
        print("  (nothing waiting on him)", file=stream)
        return
    for i, c in enumerate(q, 1):
        mark = {"blocking": "!", "whenever": "."}.get(c.get("priority"), " ")
        age = c.get("opened_at", "")[11:16]
        print(f"  {mark} {i}. [{c.get('priority','normal'):8}] {c['question'][:88]}",
              file=stream)
        detail = c.get("placement_reason")
        if detail:
            print(f"        why: {detail[:88]}", file=stream)
        print(f"        {c['id']}, asked {age}Z, {c.get('nudges',0)} nudge(s)",
              file=stream)


def main(argv):
    ap = argparse.ArgumentParser(
        prog="benham.py ask",
        description="Ask Tyler something from a running session and wait for the answer.")
    # Optional so that `ask --queue` works on its own. It was required, while
    # --queue's own help said "print what is waiting and exit" - so the documented
    # way to read the queue crashed on a missing positional, and the workaround
    # (a dummy question nobody ever asks) had propagated as far as Tyler's global
    # CLAUDE.md: `ask --queue "x"`. Reading the line before joining it is the one
    # step that makes self-assessed priority mean anything; it should not need a
    # placeholder argument.
    ap.add_argument("question", nargs="?", default=None,
                    help="What to ask him, in plain language. Omit it with --queue.")
    ap.add_argument("--purpose", default=None,
                    help="One line on what the answer is FOR (shown in reports, "
                         "defaults to the question)")
    ap.add_argument("--project", default=None, help="Which project this is about")
    ap.add_argument("--timeout", type=int, default=900,
                    help="Seconds to wait. Default 900 (15 min - one nudge cycle).")
    ap.add_argument("--no-wait", action="store_true",
                    help="Send it and exit; read the answer later with `conv show`.")
    ap.add_argument("--priority", default=conversations.NORMAL,
                    choices=list(conversations.PRIORITIES),
                    help="Where you belong in the line. blocking = this session "
                         "cannot continue; normal = it gates something but there "
                         "is other work; whenever = no rush. YOU choose - read the "
                         "queue first (--queue) and be honest, because he sees "
                         "every claim.")
    ap.add_argument("--why", default=None,
                    help="One line on why you placed yourself there. Shown to him "
                         "next to your question.")
    ap.add_argument("--queue", action="store_true",
                    help="Print what is already waiting on him and exit. Read this "
                         "BEFORE asking.")
    a = ap.parse_args(argv)

    who = _owner()

    # THE COORDINATION STEP (Tyler's design, 2026-08-17). Sessions do not know
    # about each other and never will, so the queue is the only place they can
    # meet. Reading it before choosing a priority is what stops self-assessment
    # from inflating: ranking yourself in a vacuum has one safe answer, ranking
    # yourself against three visible claims does not.
    q = conversations.queue_for(who)
    # Answers nobody has picked up. Shown FIRST, because an answer already given
    # is more useful than a question about to be asked - and because the commonest
    # way to waste his time is to re-ask something he has already answered to a
    # session that has since exited.
    waiting = conversations.uncollected(who)
    if waiting:
        print(f"{len(waiting)} answer(s) came back that nobody has collected:",
              file=sys.stderr)
        for c in waiting:
            print(f"  {c['id']}: {c['question'][:80]}", file=sys.stderr)
            print(f"     -> {str(c.get('answer',''))[:140]}", file=sys.stderr)
        print(f"  (act on it, then: python benham.py conv close <id> \"what happened\" --face {paths.PROCESS_FACE})",
              file=sys.stderr)
        print("", file=sys.stderr)
    if a.queue:
        _print_queue(q)
        return 0
    if not a.question:
        ap.error("a question is required unless you are just reading the queue "
                 "(--queue)")
    if q:
        print(f"Already waiting on him ({len(q)}) - place yourself accordingly:",
              file=sys.stderr)
        _print_queue(q, stream=sys.stderr)
        print("", file=sys.stderr)

    conv = conversations.open_conversation(
        who,
        purpose=a.purpose or a.question,
        question=a.question,
        project=a.project,
        priority=a.priority,
        placement_reason=a.why,
        # The registration. cwd and pid, so a banked question can be traced back to
        # whatever was running at the time - months later, when nothing about the
        # session survives except this string.
        origin=f"session cwd={os.getcwd()} pid={os.getpid()}")

    # Delivered through the outbox so the RUNNING bot sends it - this process has no
    # Discord connection and should not grow one. advance_conversation's first beat
    # is the ask itself, so the same action that nudges and banks also delivers.
    outbox.enqueue(face=paths.PROCESS_FACE, action="advance_conversation", id=conv["id"])
    slot = conversations.slot_of(conv["id"])
    total = len(conversations.queue_for(who))
    where = f" - slot {slot} of {total}" if total > 1 else ""
    print(f"asked {who} ({conv['id']}, {a.priority}{where}): {a.question}")

    if a.no_wait:
        print(f"not waiting - read it later with: python benham.py conv show {conv['id']} --face {paths.PROCESS_FACE}")
        return 0

    deadline = time.time() + max(10, int(a.timeout))
    poll = 3
    while time.time() < deadline:
        time.sleep(poll)
        cur = conversations.get(conv["id"])
        if cur is None:
            print(f"{conv['id']} vanished from the store", file=sys.stderr)
            return 4
        state = cur.get("state")
        if state == conversations.ANSWERED:
            how = "replied to the question" if _bound_by(cur) == "reply" else \
                  "answered, bound by Benham's judgement"
            print(f"\n[{cur['id']}] {how}:\n{cur['answer']}")
            return 0
        if state in conversations.TERMINAL_STATES:
            print(f"\n[{cur['id']}] {state}: {cur.get('outcome') or 'no answer'}",
                  file=sys.stderr)
            return 5

    # A timeout is NOT a failure of the conversation. The bot keeps nudging on its
    # own schedule and Tyler can still answer; only this process stopped watching.
    print(f"\nNo answer within {a.timeout}s. {conv['id']} is still open - the bot "
          f"keeps nudging, and the answer will be waiting in the store.",
          file=sys.stderr)
    return 6


def _bound_by(conv):
    for e in reversed(conv.get("log", [])):
        if e.get("event") == "answered":
            return (e.get("detail") or "").replace("via ", "")
    return "unknown"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
