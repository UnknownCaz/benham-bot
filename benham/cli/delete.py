"""
delete.py - ask the running bot.py to delete ONE specific message by id.

Usage:
    python benham.py delete <channel_id> <message_id> [--no-wait]

Enqueues a {"action":"delete", ...} request into ./outbox; bot.py fetches that
message and deletes it (~2s). The bot can always delete its OWN messages; deleting
someone else's needs the Manage Messages permission. Deletion is PERMANENT.

By default this waits for the bot's result and exits non-zero when the delete
was refused (missing permission, unknown message) - a permanent action whose
failure the caller cannot see is how a "cleaned up" message stays visible.
--no-wait restores fire-and-forget.

Use it to clean up a stray/accidental Benham post or a bad draft. To remove many
old messages at once, use purge.py.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome, usage)


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a != "--no-wait"]
    if len(argv) < 3:
        return usage("Usage: python benham.py delete <channel_id> <message_id> "
                     "[--no-wait]")
    ids, err = parse_ids(argv[1:3], ["channel_id", "message_id"])
    if err:
        return usage(err)
    channel_id, message_id = ids

    final = enqueue(face=paths.PROCESS_FACE, action="delete", channel_id=channel_id, message_id=message_id)
    print(f"Delete request queued -> {final}")
    print(f"  channel {channel_id}, message {message_id}")
    if no_wait:
        return EXIT_OK
    code, _ = report_outcome(final)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
