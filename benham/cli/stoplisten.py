"""
stoplisten.py - tell the bot to stop listening and leave the voice channel.

Usage:
    python benham.py stoplisten <voice_channel_id>
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage


def main(argv):
    console_utf8()
    if len(argv) < 2:
        return usage("Usage: python benham.py stoplisten <voice_channel_id>")
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids

    final = enqueue(action="stop_listen", channel_id=channel_id)
    print(f"Queued stop_listen request -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
