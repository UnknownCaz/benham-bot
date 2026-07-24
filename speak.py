"""
speak.py — have the bot join a voice channel and say something (Windows SAPI TTS).

Usage:
    python speak.py <voice_channel_id> "text to speak"

Drops a {"action":"speak", ...} request into ./outbox. The running bot.py joins
the voice channel, speaks the text, then disconnects. Find voice channel IDs in
channels.json under each guild's "voice_channels".
"""

import os
import sys
import json
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE_DIR, "outbox")


def main(argv):
    if len(argv) < 3:
        print('Usage: python speak.py <voice_channel_id> "text to speak"', file=sys.stderr)
        return 2
    try:
        channel_id = int(argv[1])
    except ValueError:
        print(f"channel_id must be an integer, got {argv[1]!r}", file=sys.stderr)
        return 2
    content = " ".join(argv[2:])

    os.makedirs(OUTBOX, exist_ok=True)
    req = {
        "action": "speak",
        "channel_id": channel_id,
        "content": content,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)
    print(f"Queued speak request -> {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
