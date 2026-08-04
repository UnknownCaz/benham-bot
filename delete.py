"""
delete.py - ask the running bot.py to delete ONE specific message by id.

Usage:
    python delete.py <channel_id> <message_id>

Enqueues a {"action":"delete", ...} request into ./outbox; bot.py fetches that
message and deletes it (~2s). The bot can always delete its OWN messages; deleting
someone else's needs the Manage Messages permission. Deletion is PERMANENT.

Use it to clean up a stray/accidental Benham post or a bad draft. To remove many
old messages at once, use purge.py.
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage


def main(argv):
    console_utf8()
    if len(argv) < 3:
        return usage("Usage: python delete.py <channel_id> <message_id>")
    ids, err = parse_ids(argv[1:3], ["channel_id", "message_id"])
    if err:
        return usage(err)
    channel_id, message_id = ids

    final = enqueue(action="delete", channel_id=channel_id, message_id=message_id)
    print(f"Delete request queued -> {final}")
    print(f"  channel {channel_id}, message {message_id}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
