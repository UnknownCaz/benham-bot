"""
wakes.py — print NEW wake-word utterances from voice_transcript.jsonl since last call.

Used by the background auto-respond loop: each iteration runs this to get only the
utterances aimed at the bot ("claude"/"benham") that haven't been handled yet, then the
brain composes a persona reply and speaks it. A marker file (.wake_seen) stores how many
wake lines have already been surfaced so nothing is answered twice.

Each emitted wake utterance also carries a "context" list: the recent transcript lines that
came just before it (within CONTEXT_SECONDS / CONTEXT_MAX lines), so the brain can follow an
active conversation even when Tyler forgets to say the wake name mid-thought.

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

CONTEXT_SECONDS = 90   # include preceding lines within this many seconds of the wake
CONTEXT_MAX = 8        # ...up to this many lines


def _parse_ts(s):
    from datetime import datetime

    try:
        return datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


def _context_for(all_recs, wake_idx):
    """Recent lines just before the wake line at wake_idx (time- and count-bounded)."""
    ctx = []
    wake_ts = _parse_ts(all_recs[wake_idx].get("ts", ""))
    for rec in reversed(all_recs[:wake_idx]):
        if len(ctx) >= CONTEXT_MAX:
            break
        if wake_ts is not None:
            rts = _parse_ts(rec.get("ts", ""))
            if rts is not None and (wake_ts - rts).total_seconds() > CONTEXT_SECONDS:
                break
        ctx.append({"speaker": rec.get("speaker"), "text": rec.get("text"), "ts": rec.get("ts")})
    ctx.reverse()  # oldest -> newest
    return ctx


def main(argv):
    peek = "--peek" in argv[1:]
    if not os.path.exists(TRANSCRIPT):
        return 0
    all_recs = []
    with open(TRANSCRIPT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                all_recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    wake_idxs = [i for i, r in enumerate(all_recs) if r.get("contains_wake")]

    seen = 0
    if os.path.exists(MARKER):
        try:
            seen = int(open(MARKER).read().strip() or "0")
        except ValueError:
            seen = 0

    for idx in wake_idxs[seen:]:
        rec = all_recs[idx]
        print(json.dumps({
            "speaker": rec.get("speaker"),
            "text": rec.get("text"),
            "channel_id": rec.get("channel_id"),
            "ts": rec.get("ts"),
            "context": _context_for(all_recs, idx),
        }, ensure_ascii=False))

    if not peek:
        with open(MARKER, "w", encoding="utf-8") as f:
            f.write(str(len(wake_idxs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
