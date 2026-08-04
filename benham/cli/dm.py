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
outbox/failed with a Forbidden error rather than failing silently.
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage

# The bot's owner. Kept here rather than in a config file because dm.py is the
# only thing that needs it; move it out if a second caller ever appears.
TYLER_ID = 273967061619965952


def main(argv):
    console_utf8()
    if len(argv) < 3:
        return usage('Usage: python benham.py dm <user_id|--tyler> "message text"')

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

    final = enqueue(action="dm", user_id=user_id, content=content)
    print(f"Queued DM -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
