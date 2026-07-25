"""
delete.py — ask the running bot.py to delete ONE specific message by id.

Usage:
    python delete.py <channel_id> <message_id>

Enqueues a {"action":"delete", ...} request into ./outbox; bot.py fetches that
message and deletes it (~2s). The bot can always delete its OWN messages; deleting
someone else's needs the Manage Messages permission. Deletion is PERMANENT.

Use it to clean up a stray/accidental Benham post or a bad draft. To remove many
old messages at once, use the bulk `purge` action instead (see bot.py).
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
        print("Usage: python delete.py <channel_id> <message_id>", file=sys.stderr)
        return 2
    try:
        channel_id = int(argv[1])
        message_id = int(argv[2])
    except ValueError:
        print("channel_id and message_id must both be integers", file=sys.stderr)
        return 2

    os.makedirs(OUTBOX, exist_ok=True)
    req = {
        "action": "delete",
        "channel_id": channel_id,
        "message_id": message_id,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)  # atomic
    print(f"Delete request queued -> {final}")
    print(f"  channel {channel_id}, message {message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
