"""restart.py - ask the running bot to exit, and let its supervisor bring it back.

    python benham.py restart              # request it, then wait for the bot to be back
    python benham.py restart --no-wait    # request it and exit

Phase B, INTENT decision 44: the bot lives under launchd on cazzy-mac, where
"kill the process" is not a hand this machine has. So the bot carries an
owner-gated `restart` capability - it exits 0 a few seconds after answering,
and launchd's KeepAlive restarts it. This verb is that capability from the
shell, through the outbox like every other action, so policy sees the call.

Restart is the small hand. When the bot's own API is wedged, the path is ssh:
    sudo launchctl kickstart -k system/com.caz.benham-bot
"""

import sys
import time

from benham import paths
from benham.core import remote
from benham.core.outbox import (EXIT_FAIL, EXIT_OK, console_utf8, enqueue,
                                report_outcome)

WAIT_BACK = 60      # seconds to wait for the bot to come back before giving up


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    final = enqueue(face=paths.PROCESS_FACE, action="restart")
    print(f"Queued restart -> {final}")
    code, result = report_outcome(final)
    if code != EXIT_OK or not result:
        return code
    res = result.get("result") or {}
    old_pid = res.get("pid")
    print(f"  bot pid {old_pid} exits in ~{res.get('in_seconds', '?')}s; "
          f"its supervisor brings it back")
    if no_wait or not remote.active():
        return EXIT_OK

    # Wait for a DIFFERENT pid with the gateway connected: the same pid still
    # answering means it has not gone yet; a new pid not yet connected means
    # it is on its way.
    deadline = time.time() + WAIT_BACK
    time.sleep(float(res.get("in_seconds") or 2) + 1)
    while time.time() < deadline:
        try:
            h = remote.call("GET", "/health", timeout=5)
        except remote.RemoteError:
            h = None
        if h and h.get("pid") and h["pid"] != old_pid and h.get("gateway_connected"):
            print(f"  back: pid {h['pid']}, gateway connected")
            return EXIT_OK
        time.sleep(2)
    print(f"  not back within {WAIT_BACK}s - check `python benham.py status`; "
          f"if it stays down, the ssh path is launchctl kickstart", file=sys.stderr)
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
