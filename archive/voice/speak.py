"""
speak.py - have the bot join a voice channel and say something (edge-tts).

Usage:
    python benham.py speak <voice_channel_id> "text to speak"

Drops a {"action":"speak", ...} request into ./outbox. The running bot.py joins
the voice channel, speaks the text, then disconnects. Find voice channel IDs in
channels.json under each guild's "voice_channels".
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage


def main(argv):
    console_utf8()
    if len(argv) < 3:
        return usage('Usage: python benham.py speak <voice_channel_id> "text to speak"')
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids
    content = " ".join(argv[2:])

    final = enqueue(action="speak", channel_id=channel_id, content=content)
    print(f"Queued speak request -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
