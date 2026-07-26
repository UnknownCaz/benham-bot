"""
dm.py - enqueue a Discord DM for the running bot.py to deliver.

Usage:
    python dm.py <user_id> "message text"
    python dm.py --tyler "message text"      # shorthand for the owner

Writes an atomic request file into ./outbox; bot.py picks it up within ~2s,
resolves (or opens) the user's DM channel, sends it, and moves the request to
outbox/sent or outbox/failed with a result file.

Discord only lets a bot DM a user who shares a guild with it and has not disabled
DMs from server members. If that is not the case the request lands in
outbox/failed with a Forbidden error rather than failing silently.
"""

import os
import sys
import json
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE_DIR, "outbox")

# The bot's owner. Kept here rather than in a config file because dm.py is the
# only thing that needs it; move it out if a second caller ever appears.
TYLER_ID = 273967061619965952


def main(argv):
    if len(argv) < 3:
        print('Usage: python dm.py <user_id|--tyler> "message text"', file=sys.stderr)
        return 2

    target = argv[1]
    if target == "--tyler":
        user_id = TYLER_ID
    else:
        try:
            user_id = int(target)
        except ValueError:
            print(f"user_id must be an integer or --tyler, got {target!r}", file=sys.stderr)
            return 2

    content = " ".join(argv[2:])
    if not content.strip():
        print("refusing to send an empty message", file=sys.stderr)
        return 2

    req = {
        "action": "dm",
        "user_id": user_id,
        "content": content,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(OUTBOX, exist_ok=True)
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)  # atomic: the poller never sees a half-written request

    print(f"Queued DM -> {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
