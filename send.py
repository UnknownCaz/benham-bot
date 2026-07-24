"""
send.py — enqueue a Discord message for the running bot.py to deliver.

Usage:
    python send.py <channel_id> "message text"

Writes an atomic request file into ./outbox; bot.py picks it up within ~2s,
sends it, and moves it to outbox/sent or outbox/failed with a result file.
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
        print('Usage: python send.py <channel_id> "message text"', file=sys.stderr)
        return 2
    channel_id = argv[1]
    content = " ".join(argv[2:])
    try:
        channel_id = int(channel_id)
    except ValueError:
        print(f"channel_id must be an integer, got {channel_id!r}", file=sys.stderr)
        return 2

    os.makedirs(OUTBOX, exist_ok=True)
    req = {
        "channel_id": channel_id,
        "content": content,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)  # atomic
    print(f"Queued -> {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
