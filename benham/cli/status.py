"""status.py - quick, READ-ONLY health check for benham-bot.

Answers "is Benham up and what is it doing" without touching Discord:
  - is the bot process up, and is its gateway connected?
  - which guilds/channels does it see (from channels.json, written each boot)?
  - recent refusals from outbox/failed (last 24h)
  - last login / command-sync lines from the newest log file

Phase B (INTENT decision 38): the bot runs on cazzy-mac, so every one of those
facts is read from the bot's own host - GET /health for the process and the
gateway, a status snapshot for the rest - instead of from this machine's
process table and this tree's files. Exit 0 means the bot is up AND its
gateway is connected (RAVEN.md's "exit 0 = running"); anything less is 1.
Unreachable prints one line naming the host, never a traceback.

The refusals section is the net under every fire-and-forget enqueue: a caller
that passed --no-wait, or an enqueue made from inside the bot itself (loopclose
DMs), records its failure in outbox/failed and tells nobody. This is the one
place all of those surface without reading the bot log.

Prints a short report and exits. Never prints tokens. Run:  python benham.py status
"""

from datetime import datetime

from benham.core import remote


def _iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def main():
    print("=== benham-bot status ===")

    try:
        snap = remote.stores.rpc.status_snapshot()
    except remote.RemoteError as e:
        # The words a caller reads unattended: the same NOT running line the
        # process-table check used to print, with the reason after it.
        print(f"process:      NOT running ({e})")
        return 1

    up = bool(snap.get("pid")) and snap.get("gateway_connected") is True
    host = snap.get("host") or remote.host() if remote.active() else "this machine"
    if snap.get("pid") and up:
        print(f"process:      RUNNING (pid {snap['pid']} on {host}, gateway connected)")
    elif snap.get("pid"):
        print(f"process:      RUNNING (pid {snap['pid']} on {host}) but gateway NOT connected")
    else:
        print("process:      NOT running")

    ch = snap.get("channels")
    ch_mt = _iso(snap.get("channels_written"))
    if ch:
        when = f"{ch_mt:%Y-%m-%d %H:%M}Z" if ch_mt else "?"
        print(f"guilds:       {len(ch)} (channels.json written {when})")
        for g in ch:
            tc = len(g.get("text_channels", []))
            vc = len(g.get("voice_channels", []))
            print(f"  - {g.get('guild')} ({g.get('guild_id')}): {tc} text, {vc} voice")
    else:
        print("guilds:       channels.json not found (bot hasn't booted here yet)")

    failures = snap.get("failures") or []
    if failures:
        print(f"refused (outbox/failed, last 24h): {len(failures)}")
        for f in failures:
            when = _iso(f.get("when"))
            stamp = f"{when:%Y-%m-%d %H:%M}Z" if when else "?"
            print(f"  {stamp}  {f.get('action', 'send'):<16} {f.get('error', '')}")
    else:
        print("refused:      none in the last 24h (outbox/failed)")

    logname, tail = snap.get("log_name"), snap.get("log_tail") or []
    if logname:
        print(f"last log ({logname}):")
        for line in tail:
            print(f"  {line}")
    else:
        print("last log:     none found")

    return 0 if up else 1


if __name__ == "__main__":
    raise SystemExit(main())
