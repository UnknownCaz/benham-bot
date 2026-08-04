"""
fetch.py - ask the running bot.py to pull recent messages from a channel.

Usage:
    python benham.py fetch <channel_id> [limit]     (default limit 20)

Drops a {"action":"history", ...} request into ./outbox. bot.py fetches the
last N messages and writes them into outbox/sent/<name>_result.json.
Tail inbox.jsonl for live messages instead if the bot has been running.
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage

DEFAULT_LIMIT = 20


def main(argv):
    console_utf8()
    if len(argv) < 2:
        return usage("Usage: python benham.py fetch <channel_id> [limit]")
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids

    limit = DEFAULT_LIMIT
    if len(argv) > 2:
        # Previously a bare int(), which gave a raw traceback on a typo.
        limits, err = parse_ids(argv[2:3], ["limit"])
        if err:
            return usage(err)
        (limit,) = limits

    final = enqueue(action="history", channel_id=channel_id, limit=limit)
    print(f"Queued history request -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
