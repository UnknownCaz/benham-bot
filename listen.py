"""
listen.py — have the bot join a voice channel and start transcribing speech.

Usage:
    python listen.py <voice_channel_id>

Drops a {"action":"listen", ...} request into ./outbox. The running bot.py joins
the voice channel, captures audio, transcribes utterances with faster-whisper, and
appends them to voice_transcript.jsonl. Utterances containing "claude" are flagged.
Use stoplisten.py to make it leave.
"""

import os
import sys
import json
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE_DIR, "outbox")


def main(argv):
    if len(argv) < 2:
        print("Usage: python listen.py <voice_channel_id>", file=sys.stderr)
        return 2
    try:
        channel_id = int(argv[1])
    except ValueError:
        print(f"channel_id must be an integer, got {argv[1]!r}", file=sys.stderr)
        return 2

    os.makedirs(OUTBOX, exist_ok=True)
    req = {
        "action": "listen",
        "channel_id": channel_id,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)
    print(f"Queued listen request -> {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
