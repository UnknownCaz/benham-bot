"""
purge.py - bulk-delete messages in a channel, filtered by age.

Usage:
    python benham.py purge <channel_id> [--days N] [--limit N]
                          [--confirm-token TOK] [--no-wait]

    --days           only messages older than this (default 7).
    --limit          how many recent messages to consider (default 100).
    --confirm-token  fire the previewed purge.
    --no-wait        enqueue and exit without waiting.

TWO STEPS, since 2026-08-26. The first call performs nothing: it walks the same
set the real purge would and reports the real count, date span and authors, then
hands back a token. You re-run with `--confirm-token` to actually delete. This is
what catches a wrong channel id or an `older_than_days` off by 10x.

WHY THIS CHANGED, and what it cost. Until 2026-08-26 this enqueued a legacy
`purge` action that bot.py handled in its own branch, calling ch.purge() directly
with NO policy.authorize, NO destructive_guilds allowlist, no dry-run and no
confirmation. `--scope guild` swept every text channel in any guild the bot could
see. It now enqueues the registry twin `purge_messages`, which carries all three
gates - Tyler's call, after the mandatory-token work on 08-24 made it visible that
the tier-3 guarantee was a property of the verb NAME rather than of the effect.

TWO DELIBERATE DIFFERENCES from the legacy verb, both narrowing:

  --scope guild is GONE and is refused rather than quietly ignored. The guarded
  twin is per-channel; a whole-guild sweep would need either N separate
  confirmations or a new guild-wide tier-3 capability, and adding one is a
  decision about blast radius rather than a routing detail. Nothing measured is
  lost: across 970 lifetime outbox records the legacy purge was invoked ZERO
  times, at any scope.

  --limit exists and defaults to 100, because the twin considers a bounded slice
  of history. The legacy verb was unbounded (limit=None). Pass --limit explicitly
  for a big sweep; the dry-run tells you what it actually matched before anything
  is deleted.

The twin also supports author and text filters, which this never had - reach them
with `python benham.py do purge_messages channel_id=... author_id=... contains=...`.

Deletion is PERMANENT. Discord refuses to bulk-delete messages older than 14 days,
so very old ones go one at a time and a large sweep takes a while - which is why
the wait is a generous 5 minutes and a timeout means still running, not failed.

EXIT CODES: a preview-only first call exits NON-ZERO, because nothing was deleted
and a script reading 0 would believe otherwise. Only a completed purge exits 0.
"""

import sys

from benham import paths
from benham.cli.delete import run_two_step
from benham.core.outbox import EXIT_OK, console_utf8, parse_ids, usage

CHR_NL = "\n"
DEFAULT_DAYS = 7
DEFAULT_LIMIT = 100

# A purge with years-old messages deletes them one at a time; 60s would report a
# healthy purge as missing. Same reasoning as do.py's pc_task carve-out.
WAIT_TIMEOUT = 300


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a != "--no-wait"]

    token = None
    if "--confirm-token" in argv:
        i = argv.index("--confirm-token")
        if i + 1 >= len(argv):
            return usage("--confirm-token needs a token")
        token = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    if len(argv) < 2:
        return usage("Usage: python benham.py purge <channel_id> [--days N] "
                     "[--limit N] [--confirm-token TOK] [--no-wait]" + CHR_NL +
                     "   or: python benham.py purge --guild <guild_id> "
                     "[--days N] [--limit N] [--confirm-token TOK]")

    guild_id = None
    if "--guild" in argv:
        i = argv.index("--guild")
        if i + 1 >= len(argv):
            return usage("--guild needs a guild id")
        got, err = parse_ids(argv[i + 1:i + 2], ["--guild"])
        if err:
            return usage(err)
        (guild_id,) = got
        argv = argv[:i] + argv[i + 2:]
        argv.insert(1, "0")        # keep the positional slot; unused for a guild sweep

    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids

    days = DEFAULT_DAYS
    limit = DEFAULT_LIMIT
    rest = argv[2:]
    while rest:
        flag = rest.pop(0)
        if flag in ("--days", "--limit"):
            if not rest:
                return usage(f"{flag} needs a number")
            got, err = parse_ids(rest[:1], [flag])
            if err:
                return usage(err)
            (value,) = got
            rest.pop(0)
            if value < 0:
                return usage(f"{flag} must not be negative, got {value}")
            if flag == "--days":
                days = value
            else:
                limit = value
        elif flag == "--scope":
            scope = rest.pop(0) if rest else "?"
            if scope == "channel":
                continue          # the default; harmless to pass
            # Deliberately NOT reinterpreted. The old form took a CHANNEL id and
            # inferred the guild from it; the capability takes a guild id. Quietly
            # treating one as the other is the silent reinterpretation this whole
            # lane has been removing.
            return usage(
                "--scope guild is gone; the guild sweep is now its own tier-3 "
                "capability and takes a GUILD id, not a channel id. Use: "
                "python benham.py purge --guild <guild_id> [--days N] [--limit N]")
        else:
            return usage(f"unknown argument {flag!r}")

    if guild_id is not None:
        print(f"SERVER-WIDE purge: up to {limit} message(s) per channel older "
              f"than {days} day(s), across EVERY text channel in guild "
              f"{guild_id}.")
        print("  This is PERMANENT."
              + ("" if token else " This call previews only - nothing is deleted."))
        return run_two_step(
            "purge_guild",
            describe=f"guild {guild_id}, older than {days}d, limit {limit}/channel",
            rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} purge "
                             f"--guild {guild_id} --days {days} --limit {limit} "
                             f"--confirm-token {t}"),
            token=token,
            no_wait=no_wait,
            timeout=WAIT_TIMEOUT,
            guild_id=guild_id,
            older_than_days=days,
            limit=limit,
        )

    print(f"Purging up to {limit} message(s) older than {days} day(s) "
          f"from channel {channel_id}.")
    # Only true of the first call - saying "previews only" while redeeming a token
    # tells someone about to delete something permanently that nothing will happen.
    print("  This is PERMANENT."
          + ("" if token else " This call previews only - nothing is deleted."))

    return run_two_step(
        "purge_messages",
        describe=f"channel {channel_id}, older than {days}d, limit {limit}",
        rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} purge "
                         f"{channel_id} --days {days} --limit {limit} "
                         f"--confirm-token {t}"),
        token=token,
        no_wait=no_wait,
        timeout=WAIT_TIMEOUT,
        channel_id=channel_id,
        older_than_days=days,
        limit=limit,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
