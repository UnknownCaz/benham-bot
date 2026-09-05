"""read_history.py - one-shot, READ-ONLY message reader across guilds.

Lists the guilds the bot is in, then reads the last N messages from every text
channel in guilds OTHER than the Testing Server, prints them, and exits. It
posts NOTHING: every read is a `history` request the running bot serves, and
reading history shows nobody anything.

Before Phase B this logged in as a second, invisible client with the bot's
token; the token no longer lives on the PC (INTENT decision 42). The guild
list now comes from the bot's channels.json (written each boot on its host)
and a channel the bot cannot read comes back as a refusal, printed as SKIPPED.

Usage:
    python -u benham.py read_history [limit]
Default: limit=100 per channel. Testing Server is always excluded. "all",
"0", "full" or "none" reads the whole channel history.
"""

import sys

from benham import paths
from benham.core import remote
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, wait_result)

TESTING_SERVER_ID = 736988645562646619  # excluded - we want the friend's server only


def _limit(arg):
    if arg in ("all", "0", "full", "none"):
        return None
    try:
        return int(arg)
    except ValueError:
        print(f"limit must be an integer or all/0/full/none, got {arg!r}", file=sys.stderr)
        raise SystemExit(2)


def main(argv):
    console_utf8()
    limit = _limit(argv[1].lower() if len(argv) > 1 else "100")

    guilds = remote.stores.rpc.status_snapshot().get("channels") or []
    print(f"Benham is in {len(guilds)} guild(s):")
    for g in guilds:
        tag = "  [TESTING - excluded]" if g.get("guild_id") == TESTING_SERVER_ID else ""
        print(f"  - {g.get('guild')} (id {g.get('guild_id')}){tag}")

    targets = [g for g in guilds if g.get("guild_id") != TESTING_SERVER_ID]
    if not targets:
        print("\nNO non-Testing guild found - the friend's server is NOT added yet. "
              "Nothing to read.")
        return EXIT_OK

    for g in targets:
        print(f"\n########## GUILD: {g.get('guild')} (id {g.get('guild_id')}) ##########")
        for ch in g.get("text_channels", []):
            print(f"\n--- #{ch.get('name')} (last {limit}, oldest->newest) ---")
            req = {"action": "history", "channel_id": int(ch["id"])}
            if limit is not None:
                req["limit"] = limit
            final = enqueue(face=paths.PROCESS_FACE, **req)
            result, where = wait_result(final, timeout=120)
            if result is None:
                print("  ERROR: no result from the bot in time")
                continue
            if where == "failed":
                print(f"  SKIPPED ({result.get('error', 'refused')})")
                continue
            msgs = result.get("messages") or []
            if not msgs:
                print("  (no messages)")
            for m in msgs:
                ts = str(m.get("ts", ""))[:16].replace("T", " ")
                print(f"  [{ts}] {m.get('author')}: {m.get('content') or ''}")
    print("\n=== DONE (read-only; nothing was posted) ===")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
