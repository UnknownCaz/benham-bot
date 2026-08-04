"""
draft.py - draft a reply for review BEFORE it goes to a friend (human-in-the-loop).

Instead of sending straight to a friend's channel, this posts a clearly-labeled DRAFT into
the Testing Server (#asd) so Tyler can eyeball it, then prints the exact send.py command that
delivers it for real once he approves. Nothing goes to the target channel until Tyler runs
that command himself.

Usage:
    python draft.py <target_channel_id> "reply text"

Flow:
    1. python draft.py 1491485711801520261 "yo xavier, sounds good"
       -> a DRAFT lands in Testing #asd for review; the real send command is printed.
    2. If it looks good, run the printed command:
       python send.py 1491485711801520261 "yo xavier, sounds good"

Like send.py, this just enqueues a request into ./outbox for the running bot.py to deliver
(the draft goes to Testing #asd). It does not talk to Discord directly.
"""

import json
import os
import sys

from benham.core.outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage

from benham import paths

CHANNELS_FILE = os.path.join(paths.STATE_DIR, "channels.json")

# Testing Server #asd - the review channel drafts are posted to.
REVIEW_CHANNEL_ID = 809357286036078612


def resolve_channel(channel_id):
    """Return (guild_name, channel_name) for a channel id from channels.json, or (None, None)."""
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, None
    for guild in data:
        for ch in guild.get("text_channels", []):
            if ch.get("id") == channel_id:
                return guild.get("guild"), ch.get("name")
    return None, None


def main(argv):
    console_utf8()
    if len(argv) < 3:
        return usage('Usage: python draft.py <target_channel_id> "reply text"')
    ids, err = parse_ids(argv[1:2], ["target_channel_id"])
    if err:
        return usage(err)
    (target_id,) = ids
    content = " ".join(argv[2:])

    guild_name, chan_name = resolve_channel(target_id)
    if chan_name:
        where = f"#{chan_name}" + (f" in {guild_name}" if guild_name else "")
    else:
        where = f"channel {target_id} (not found in channels.json)"

    # Draft body posted to Testing #asd. Tyler-facing text: plain hyphens, no markdown noise.
    draft_body = f"DRAFT for {where}:\n\n{content}"

    final = enqueue(
        channel_id=REVIEW_CHANNEL_ID,
        content=draft_body,
        draft_for_channel_id=target_id,
    )

    print(f"Draft queued to Testing #asd -> {final}")
    print(f"  target: {where}")
    print("  review it in Discord, then to send for real run:")
    # Quote the content so it survives the shell as one argument.
    print(f'    python send.py {target_id} "{content}"')
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
