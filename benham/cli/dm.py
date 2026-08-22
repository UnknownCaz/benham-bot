"""
dm.py - enqueue a Discord DM for the running bot.py to deliver.

Usage:
    python benham.py dm <user_id> "message text"
    python benham.py dm --tyler "message text"      # shorthand for the owner

Writes an atomic request file into ./outbox; bot.py picks it up within ~2s,
resolves (or opens) the user's DM channel, sends it, and moves the request to
outbox/sent or outbox/failed with a result file.

Discord only lets a bot DM a user who shares a guild with it and has not disabled
DMs from server members. If that is not the case the request lands in
outbox/failed with a Forbidden error - and by default this command waits for
that result and exits non-zero with the error printed, so the refusal reaches
the caller instead of only the archive. (Before 2026-08-21 it printed "Queued"
and exited 0 either way; a spawned session once DM'd a nonexistent user, got a
clean exit, and the 404 reached nobody.) --no-wait restores fire-and-forget;
refusals then show up only in `python benham.py status`.

ASKING A COLLABORATOR SOMETHING IS NOT THIS COMMAND'S JOB. A raw dm opens no
conversation, so nothing tracks that a question is owed and nothing binds their
answer to it. `benham.py outreach` opens one; this does not. On 2026-08-20 six
questions went to Draco this way, he answered them at 00:12Z, and a session with
no record of that answer asked him the same six again four hours later - he
repeated himself verbatim, and was left thinking nobody was listening. Twenty-odd
sends to him across three days, not one of them tracked: Doom had six
conversations on the rail and Draco had zero. The rail worked. It was skipped.

So a question to a guest is refused here and pointed at outreach. --untracked
overrides it in the cases the heuristic gets wrong, because a guard nobody can
get past is a guard people route around in worse ways.
"""

import sys

from benham import paths
from benham.core import identity
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome, usage)

# The bot's owner. Kept here rather than in a config file because dm.py is the
# only thing that needs it; move it out if a second caller ever appears.
TYLER_ID = 273967061619965952


def asks_something(content):
    """Does this message put a question to the person receiving it?

    A question mark ANYWHERE, not one at the end. The message that caused this
    guard closed with "No rush on any of it." and carried its six questions in a
    numbered list above that - an end-of-string test would have waved through the
    exact send it exists to catch.
    """
    return "?" in (content or "")


def _refuse_untracked_question(user_id, content):
    """The refusal text, or None if this send is fine as a raw dm."""
    if not asks_something(content):
        return None
    if not identity.is_guest(user_id):
        return None          # Tyler has the ask queue; strangers are not asked things
    return (
        f"refusing: this DM asks {user_id} a question, and a raw dm opens no\n"
        "conversation - nothing would track that an answer is owed, and their\n"
        "reply would bind to nothing. That is exactly how Draco got asked the\n"
        "same six questions twice on 2026-08-20.\n"
        "\n"
        'Use:  python benham.py outreach <who> "your question"\n'
        "\n"
        "If this really is not a question being put to them (a quote, a URL, an\n"
        "aside that happens to contain '?'), pass --untracked and it goes as-is."
    )


def main(argv):
    console_utf8()
    untracked = "--untracked" in argv
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a not in ("--untracked", "--no-wait")]
    if len(argv) < 3:
        return usage('Usage: python benham.py dm <user_id|--tyler> "message text" '
                     '[--untracked] [--no-wait]')

    if argv[1] == "--tyler":
        user_id = TYLER_ID
    else:
        ids, err = parse_ids(argv[1:2], ["user_id"])
        if err:
            return usage(f"{err} (or pass --tyler)")
        (user_id,) = ids

    content = " ".join(argv[2:])
    if not content.strip():
        return usage("refusing to send an empty message")

    if not untracked:
        refusal = _refuse_untracked_question(user_id, content)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2

    final = enqueue(face=paths.DEFAULT_FACE, action="dm", user_id=user_id, content=content)
    print(f"Queued DM -> {final}")
    if no_wait:
        return EXIT_OK
    code, _ = report_outcome(final)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
