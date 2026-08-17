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

from benham.core import conversations, identity, outbox


def _owner():
    ids = sorted(identity.OWNER_IDS)
    if not ids:
        print("No owner is configured in control.json - there is nobody to ask.",
              file=sys.stderr)
        raise SystemExit(2)
    return ids[0]


def main(argv):
    ap = argparse.ArgumentParser(
        prog="benham.py ask",
        description="Ask Tyler something from a running session and wait for the answer.")
    ap.add_argument("question", help="What to ask him, in plain language")
    ap.add_argument("--purpose", default=None,
                    help="One line on what the answer is FOR (shown in reports, "
                         "defaults to the question)")
    ap.add_argument("--project", default=None, help="Which project this is about")
    ap.add_argument("--timeout", type=int, default=900,
                    help="Seconds to wait. Default 900 (15 min - one nudge cycle).")
    ap.add_argument("--no-wait", action="store_true",
                    help="Send it and exit; read the answer later with `conv show`.")
    a = ap.parse_args(argv)

    who = _owner()

    # One live ask per person, so a reply is unambiguously bindable. Report the
    # clash rather than silently queueing behind it: two questions in flight is a
    # mistake at the call site, and the caller can decide whether to wait or drop it.
    live = conversations.live_for(who)
    if live:
        print(f"He is already being asked something else ({live['id']}): "
              f"{live['question'][:160]}", file=sys.stderr)
        print("Wait for that one to close, or answer it first.", file=sys.stderr)
        return 3

    conv = conversations.open_conversation(
        who,
        purpose=a.purpose or a.question,
        question=a.question,
        project=a.project,
        # The registration. cwd and pid, so a banked question can be traced back to
        # whatever was running at the time - months later, when nothing about the
        # session survives except this string.
        origin=f"session cwd={os.getcwd()} pid={os.getpid()}")

    # Delivered through the outbox so the RUNNING bot sends it - this process has no
    # Discord connection and should not grow one. advance_conversation's first beat
    # is the ask itself, so the same action that nudges and banks also delivers.
    outbox.enqueue(action="advance_conversation", id=conv["id"])
    print(f"asked {who} ({conv['id']}): {a.question}")

    if a.no_wait:
        print(f"not waiting - read it later with: python benham.py conv show {conv['id']}")
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
