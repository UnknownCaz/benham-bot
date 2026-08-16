"""
listen.py - have the bot join a voice channel and start transcribing speech.

Usage:
    python benham.py listen <voice_channel_id>

Drops a {"action":"listen", ...} request into ./outbox. The running bot.py joins
the voice channel, captures audio, transcribes utterances with faster-whisper, and
appends them to voice_transcript.jsonl. Utterances containing "claude" are flagged.
Use stoplisten.py to make it leave.
"""

import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage


def main(argv):
    console_utf8()
    if len(argv) < 2:
        return usage("Usage: python benham.py listen <voice_channel_id>")
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids

    final = enqueue(action="listen", channel_id=channel_id)
    print(f"Queued listen request -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
