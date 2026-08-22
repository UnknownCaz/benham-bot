"""
fetch.py - ask the running bot.py to pull recent messages from a channel.

Usage:
    python benham.py fetch <channel_id> [limit] [--no-wait]     (default limit 20)

Drops a {"action":"history", ...} request into ./outbox. bot.py fetches the
last N messages and writes them into outbox/sent/<name>_result.json. By default
this waits for that result and prints the messages (they ARE the point of the
command - before 2026-08-21 the caller had to go dig the result file out by
hand), and exits non-zero when the fetch was refused. --no-wait just enqueues.
Tail inbox.jsonl for live messages instead if the bot has been running.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome, usage)

DEFAULT_LIMIT = 20


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a != "--no-wait"]
    if len(argv) < 2:
        return usage("Usage: python benham.py fetch <channel_id> [limit] [--no-wait]")
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

    final = enqueue(face=paths.PROCESS_FACE, action="history", channel_id=channel_id, limit=limit)
    print(f"Queued history request -> {final}")
    if no_wait:
        return EXIT_OK
    code, result = report_outcome(final)
    if code == EXIT_OK and result:
        for m in result.get("messages", []):
            ts = str(m.get("ts", ""))[:19].replace("T", " ")
            print(f"[{ts}] {m.get('author')}: {m.get('content')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
