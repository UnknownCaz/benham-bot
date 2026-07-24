"""
fetch.py — ask the running bot.py to pull recent messages from a channel.

Usage:
    python fetch.py <channel_id> [limit]     (default limit 20)

Drops a {"action":"history", ...} request into ./outbox. bot.py fetches the
last N messages and writes them into outbox/sent/<name>_result.json.
Tail inbox.jsonl for live messages instead if the bot has been running.
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
        print("Usage: python fetch.py <channel_id> [limit]", file=sys.stderr)
        return 2
    try:
        channel_id = int(argv[1])
    except ValueError:
        print(f"channel_id must be an integer, got {argv[1]!r}", file=sys.stderr)
        return 2
    limit = int(argv[2]) if len(argv) > 2 else 20

    os.makedirs(OUTBOX, exist_ok=True)
    req = {
        "action": "history",
        "channel_id": channel_id,
        "limit": limit,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)
    print(f"Queued history request -> {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
