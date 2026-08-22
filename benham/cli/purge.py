"""
purge.py - bulk-delete messages older than N days.

Usage:
    python benham.py purge <channel_id> [--days N] [--scope channel|guild] [--no-wait]

    --days     default 7. Messages older than this are deleted.
    --scope    "channel" (default) purges just that channel;
               "guild" sweeps every text channel in the channel's guild.
    --no-wait  enqueue and exit without waiting for the result.

Enqueues a {"action":"purge", ...} request into ./outbox. bot.py reports per-channel
counts and any per-channel errors (a channel it lacks Manage Messages in is recorded
rather than aborting the sweep) in outbox/sent/<name>_result.json. By default this
waits for that result and prints those counts and errors - they used to be written
faithfully and shown to nobody - and exits non-zero when the whole request was
refused. The wait is generous (5 min) because Discord deletes messages older than
14 days one at a time; a timeout means still running, not failed.

Deletion is PERMANENT. There is no undo, so the scope and day count are printed back
before the request is queued. Discord itself refuses to bulk-delete messages older
than 14 days, so very old messages are removed one at a time and a large sweep takes
a while.

This CLI exists because bot.py has supported the purge action for a while but nothing
could reach it - every other action had a script and this one didn't.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome, usage)

DEFAULT_DAYS = 7
SCOPES = ("channel", "guild")

# A guild sweep with years-old messages deletes them one at a time; 60s would
# report a healthy purge as missing. Same reasoning as do.py's pc_task carve-out.
WAIT_TIMEOUT = 300


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a != "--no-wait"]
    if len(argv) < 2:
        return usage("Usage: python benham.py purge <channel_id> [--days N] "
                     "[--scope channel|guild] [--no-wait]")

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

    final = enqueue(face=paths.DEFAULT_FACE,
        action="purge",
        channel_id=channel_id,
        older_than_days=days,
        scope=scope,
    )
    print(f"Queued purge request -> {final}")
    if no_wait:
        return EXIT_OK
    code, result = report_outcome(final, timeout=WAIT_TIMEOUT)
    if code == EXIT_OK and result:
        print(f"  deleted {result.get('deleted_total', '?')} message(s)")
        for ch, n in (result.get("deleted_by_channel") or {}).items():
            print(f"    {ch}: {n}")
        for ch, err in (result.get("errors") or {}).items():
            print(f"    {ch}: SKIPPED - {err}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
