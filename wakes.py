"""
wakes.py — print NEW wake-word utterances from voice_transcript.jsonl since last call.

Used by the background auto-respond loop: each iteration runs this to get only the
utterances aimed at the bot ("claude"/"benham") that haven't been handled yet, then the
brain composes a persona reply and speaks it. A marker file (.wake_seen) stores how many
wake lines have already been surfaced so nothing is answered twice.

Usage:
    python wakes.py            # print new wake utterances (one JSON per line), advance marker
    python wakes.py --peek     # print them WITHOUT advancing the marker
"""

import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPT = os.path.join(BASE_DIR, "voice_transcript.jsonl")
MARKER = os.path.join(BASE_DIR, ".wake_seen")


def main(argv):
    peek = "--peek" in argv[1:]
    if not os.path.exists(TRANSCRIPT):
        return 0
    wake = []
    with open(TRANSCRIPT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("contains_wake"):
                wake.append(rec)

    seen = 0
    if os.path.exists(MARKER):
        try:
            seen = int(open(MARKER).read().strip() or "0")
        except ValueError:
            seen = 0

    new = wake[seen:]
    for rec in new:
        print(json.dumps({"speaker": rec.get("speaker"), "text": rec.get("text"),
                          "channel_id": rec.get("channel_id"), "ts": rec.get("ts")},
                         ensure_ascii=False))

    if not peek:
        with open(MARKER, "w", encoding="utf-8") as f:
            f.write(str(len(wake)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
