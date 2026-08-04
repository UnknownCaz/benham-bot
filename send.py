"""
send.py - enqueue a Discord message for the running bot.py to deliver.

Usage:
    python send.py <channel_id> "message text"

Writes an atomic request file into ./outbox; bot.py picks it up within ~2s,
sends it, and moves it to outbox/sent or outbox/failed with a result file.
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage


def main(argv):
    console_utf8()
    if len(argv) < 3:
        return usage('Usage: python send.py <channel_id> "message text"')
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids
    content = " ".join(argv[2:])

    # No "action" key on purpose: bot.py defaults to "send".
    final = enqueue(channel_id=channel_id, content=content)
    print(f"Queued -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
