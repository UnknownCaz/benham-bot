"""catchup.py - READ-ONLY "catch me up on one channel".

Reads the last N messages of a SINGLE channel by id through the RUNNING bot,
prints them oldest->newest, and exits. Nothing is posted: the request is a
`history` read, the same one `fetch` makes, and the bot's own poller serves it.

Before Phase B this logged in as a second, INVISIBLE client with the bot's
token. The token no longer lives on the PC (INTENT decision 42), and a history
read through the connected bot is just as invisible - reading history shows
nobody anything - so the second login is gone and only the words changed:
there is no "Logged in as" line any more, because nothing logs in.

Use it to get a quick read on a friend-server channel (e.g. Chillbar #general)
so Claude can give Tyler a short catch-up summary. Keep the read LIGHT: recent
activity and vibe, not deep profiles or sensitive personal content.

Usage:
    python -u benham.py catchup <channel_id> [limit]
Default: limit=40.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome)

DEFAULT_LIMIT = 40


def main(argv):
    console_utf8()
    if len(argv) < 2:
        print("Usage: python -u benham.py catchup <channel_id> [limit]", file=sys.stderr)
        return 2
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        print(err, file=sys.stderr)
        return 2
    (channel_id,) = ids
    limit = DEFAULT_LIMIT
    if len(argv) > 2:
        got, err = parse_ids(argv[2:3], ["limit"])
        if err:
            print(err, file=sys.stderr)
            return 2
        (limit,) = got

    final = enqueue(face=paths.PROCESS_FACE, action="history",
                    channel_id=channel_id, limit=limit)
    code, result = report_outcome(final)
    if code != EXIT_OK or not result:
        return code
    chan = result.get("channel") or str(channel_id)
    print(f"--- {chan} (last {limit}, oldest->newest) ---")
    msgs = result.get("messages") or []
    if not msgs:
        print("  (no messages)")
    for m in msgs:
        ts = str(m.get("ts", ""))[:16].replace("T", " ")
        print(f"  [{ts}] {m.get('author')}: {m.get('content') or ''}")
    print("=== DONE (read-only; nothing was posted) ===")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
