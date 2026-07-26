"""
purge.py - bulk-delete messages older than N days.

Usage:
    python purge.py <channel_id> [--days N] [--scope channel|guild]

    --days   default 7. Messages older than this are deleted.
    --scope  "channel" (default) purges just that channel;
             "guild" sweeps every text channel in the channel's guild.

Enqueues a {"action":"purge", ...} request into ./outbox. bot.py reports per-channel
counts and any per-channel errors (a channel it lacks Manage Messages in is recorded
rather than aborting the sweep) in outbox/sent/<name>_result.json.

Deletion is PERMANENT. There is no undo, so the scope and day count are printed back
before the request is queued. Discord itself refuses to bulk-delete messages older
than 14 days, so very old messages are removed one at a time and a large sweep takes
a while.

This CLI exists because bot.py has supported the purge action for a while but nothing
could reach it - every other action had a script and this one didn't.
"""

import sys

from outbox import EXIT_OK, console_utf8, enqueue, parse_ids, usage

DEFAULT_DAYS = 7
SCOPES = ("channel", "guild")


def main(argv):
    console_utf8()
    if len(argv) < 2:
        return usage("Usage: python purge.py <channel_id> [--days N] [--scope channel|guild]")

    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids

    days = DEFAULT_DAYS
    scope = "channel"
    rest = argv[2:]
    while rest:
        flag = rest.pop(0)
        if flag == "--days":
            if not rest:
                return usage("--days needs a number")
            got, err = parse_ids(rest[:1], ["--days"])
            if err:
                return usage(err)
            (days,) = got
            rest.pop(0)
            if days < 0:
                return usage(f"--days must not be negative, got {days}")
        elif flag == "--scope":
            if not rest:
                return usage(f"--scope needs one of {'|'.join(SCOPES)}")
            scope = rest.pop(0)
            if scope not in SCOPES:
                return usage(f"--scope must be one of {'|'.join(SCOPES)}, got {scope!r}")
        else:
            return usage(f"unknown argument {flag!r}")

    where = "every text channel in the guild" if scope == "guild" else f"channel {channel_id}"
    print(f"Purging messages older than {days} day(s) from {where}.")
    print("  This is PERMANENT - bot.py will report per-channel counts when done.")

    final = enqueue(
        action="purge",
        channel_id=channel_id,
        older_than_days=days,
        scope=scope,
    )
    print(f"Queued purge request -> {final}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
