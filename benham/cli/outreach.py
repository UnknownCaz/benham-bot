"""outreach.py - ask a COLLABORATOR something, without reopening arbitrary messaging.

Tyler denied the unbounded send commands outright on 2026-08-18 - `dm`, `send`,
`do dm_user`, `do send_message`, alongside the Gmail send/reply/forward tools. That
is a coherent posture rather than an oversight, and it should not be undone: every
one of those reaches ANY recipient with ANY words, and a session that misreads a
situation can message a friend with nothing on the record.

What it left behind is a hole. INTENT.md's second stated purpose - "the line to the
people working with them on projects" - went with those commands, and the
collaborator loop (§3.5) lost its only paths at once. The first thing Tyler asked
for after the change ("ask Doom to test the image fix") could not be done at all.

THIS IS THE BOUNDED PATH, and it is bounded in the two ways that matter:

  WHO   the recipient must be a whitelisted collaborator, and can NEVER be an
        owner. Tyler is reached with `ask`, which is a different command with a
        different rule - see discord-outreach rule 1, "never target Tyler".
  WHAT  nothing here composes a message. The words that go out are a QUESTION
        RECORDED ON A CONVERSATION, delivered by advance_conversation - the same
        action the SYSTEM origin is trusted with on the 60-second timer, and
        trusted for exactly this reason: it cannot choose the recipient or the
        words, because both come from a record a human opened.

So this needs none of the denied permissions, and asking for them back would have
been the wrong fix - trading a real boundary for a convenience.

    python benham.py outreach 777000777000777000 "does the image thing work now?"
    python benham.py outreach doom "..." --project storyizier

It also gains what a raw DM never had: the ask is nudged on Tyler's policy with no
session running, the answer BINDS to the question rather than being remembered, and
the loop is on the record instead of in somebody's head.
"""

import argparse
import os
import sys

from benham import paths
from benham.core import caller, conversations, identity, outbox


def _allowed():
    """Who may be contacted, as {name-or-id: user_id}.

    `outreach.people` in control.json wins when set, so the reachable set can be
    narrowed BELOW the guest whitelist without a code change. Those are genuinely
    different questions: four people are whitelisted to chat with Benham, and the
    discord-outreach skill lists exactly one it is appropriate to go and bother.
    Chatting is something they choose to do; being contacted is something we do to
    them.

    Falling back to the guest whitelist rather than to nobody keeps this usable on
    a fresh clone, and that whitelist is already curated by hand.
    """
    cfg = identity.CONTROL.get("outreach") or {}
    people = cfg.get("people") or {}
    if people:
        return {str(k).strip().lower(): int(v) for k, v in people.items()}
    # Real names now, when the guest list carries them. This line used to fake a
    # name map out of bare ids (`{str(i): int(i)}`) because there was nothing else
    # to use - identity.people_map does that same fallback centrally, so the fake
    # is gone and `outreach doom "..."` works off the guest list alone.
    return {k.lower(): v for k, v in identity.GUEST_PEOPLE.items()}


def resolve_target(arg):
    """A name or a user id -> the user id to contact. Raises SystemExit if refused.

    Fail-closed on every branch. An unrecognised name is refused rather than
    guessed at: a fuzzy match here would DM the wrong person, which is precisely
    the class of mistake the deny list exists to prevent.
    """
    allowed = _allowed()
    key = str(arg).strip().lower()
    uid = allowed.get(key)
    if uid is None and key.isdigit():
        uid = int(key)

    if uid is None:
        print(f"Don't know who {arg!r} is. Reachable: "
              + (", ".join(sorted(allowed)) or "(nobody)"), file=sys.stderr)
        print("Add them under outreach.people in control.json, and ask Tyler "
              "first - the skill's rule is allowlist-only with ask-to-add.",
              file=sys.stderr)
        raise SystemExit(2)

    # Owner check FIRST, and on the resolved id rather than on the argument, so it
    # holds even if an owner id were ever put in outreach.people by mistake.
    if identity.is_owner(uid):
        print(f"{uid} is an owner. Outreach never targets Tyler - use "
              "`python benham.py ask \"...\"`, which is built for him and joins "
              "his queue.", file=sys.stderr)
        raise SystemExit(2)

    if uid not in set(allowed.values()):
        print(f"{uid} is not on the outreach list. Reachable: "
              + (", ".join(str(v) for v in sorted(set(allowed.values()))) or "(nobody)"),
              file=sys.stderr)
        raise SystemExit(2)
    return uid


def main(argv):
    ap = argparse.ArgumentParser(
        prog="benham.py outreach",
        description="Ask a whitelisted collaborator something, through a "
                    "conversation. Never reaches Tyler - use `ask` for him.")
    ap.add_argument("who", help="Collaborator name (outreach.people) or user id")
    ap.add_argument("question", nargs="?", help="What to ask them")
    ap.add_argument("--purpose", help="What the answer is for, in your words")
    ap.add_argument("--project", help="Which project this belongs to")
    ap.add_argument("--nudge-cap", type=int, default=None, metavar="N",
                    help=f"Cap THIS ask's nudges below the default "
                         f"({conversations.MAX_NUDGES}); 0 = never nudge, just "
                         "bank at the deadline. For zero-pressure questions - "
                         "the written 'one nudge max' that never reached the "
                         "timer is how Draco got poked twice (c19).")
    ap.add_argument("--list", action="store_true",
                    help="Show what is already open with them, and exit")
    a = ap.parse_args(argv)
    if a.nudge_cap is not None and not 0 <= a.nudge_cap <= conversations.MAX_NUDGES:
        ap.error(f"--nudge-cap must be 0..{conversations.MAX_NUDGES} - it can "
                 "only lower the pressure on a person, never raise it")

    who = resolve_target(a.who)

    # What is already waiting on them. Shown before anything is opened, for the
    # same reason `ask` prints Tyler's queue: piling a third question on someone
    # who has not answered the first two is how a person stops answering at all.
    live = conversations.queue_for(who)
    if live:
        print(f"Already waiting on {who} ({len(live)}):", file=sys.stderr)
        for i, c in enumerate(live, 1):
            print(f"  {i}. {c['question'][:90]}  ({c['id']}, "
                  f"{c.get('nudges', 0)} nudge(s))", file=sys.stderr)
        print("", file=sys.stderr)
    elif a.list:
        print(f"  (nothing waiting on {who})")

    if a.list:
        return 0
    if not a.question:
        ap.error("a question is required unless you are just reading with --list")

    conv = conversations.open_conversation(
        who,
        purpose=a.purpose or a.question,
        question=a.question,
        project=a.project,
        direction=conversations.ASKING,
        origin=f"session cwd={os.getcwd()} pid={os.getpid()}",
        # So Raven can route the answer to the session that asked; None when no
        # session could be resolved (never a guess - cwd is the fallback then).
        asker_session=caller.session_id(),
        nudge_cap=a.nudge_cap)

    # The ONLY outward step, and it composes nothing: the running bot reads this
    # and delivers the question already stored on the record.
    outbox.enqueue(face=paths.PROCESS_FACE, action="advance_conversation", id=conv["id"])
    print(f"asked {who} ({conv['id']}): {a.question}")
    print(f"the bot delivers and nudges it on its own; read it later with: "
          f"python benham.py conv show {conv['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
