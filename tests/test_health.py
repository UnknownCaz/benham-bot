"""
test_health.py - the loopback liveness port: honest, loud, and gated OFF.

Migration Phase 4 moves the codex face onto a body with no banker-agent
(cazzy-mac), where the console's only eye is a TCP connect toward a declared
port. This file pins the properties the move leans on:

  1. the port answers with the face it was started for - a codex port
     claiming benham would be the banner lying (INTENT SS3.3's rule),
  2. gateway state is reported, not invented - a client that is not ready
     reads connected: false, and flips true when it is,
  3. a taken port raises instead of serving nothing - a bot running while
     its declared port is dark reads DOWN forever, the invisible-service
     disease the port exists to kill,
  4. the gate: bot.py starts it only under BENHAM_HEALTH_PORT, so every PC
     launch stays byte-identical (source-pinned, same instrument as
     test_face_wording's scans).

    python test_health.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import json
import os
import sys
import urllib.request

from benham.core import health

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


class FakeClient:
    """Only what the handler reads: two plain flags."""

    def __init__(self):
        self.ready = False
        self.closed = False

    def is_ready(self):
        return self.ready

    def is_closed(self):
        return self.closed


def _get(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


_logged = []
client = FakeClient()
server = health.start(0, client, "codex", _logged.append)  # port 0 = ephemeral
port = server.server_address[1]

# --- 1. the port tells the truth about who it is ----------------------------

body = _get(port)
check("face is the one start() was given", body["face"], "codex")
check("pid is this process", body["pid"], os.getpid())
check("uptime is a non-negative int", isinstance(body["uptime_s"], int) and body["uptime_s"] >= 0, True)
check("the start line names loopback and the port",
      any(f"127.0.0.1:{port}" in line for line in _logged), True)

# --- 2. gateway state is read off the client, not asserted ------------------

check("not-ready client reads disconnected", body["gateway_connected"], False)
client.ready = True
check("ready client reads connected", _get(port)["gateway_connected"], True)
client.closed = True
check("closed client reads disconnected even while 'ready'",
      _get(port)["gateway_connected"], False)

# --- 3. a taken port refuses loudly -----------------------------------------

try:
    health.start(port, client, "codex", _logged.append)
except OSError:
    check("second bind on the same port raises OSError", True, True)
else:
    check("second bind on the same port raises OSError", False, True)

server.shutdown()

# --- 4. the gate in bot.py: env-var name and call site both present ---------
# Source-pinned rather than executed (main() runs client.run). If either
# string leaves main(), the mac deployment silently loses its visibility and
# nothing on the PC would ever notice - the exact failure mode this port
# exists for, so the pin is loud on purpose.

_bot_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "benham", "bot.py"), encoding="utf-8").read()
check("bot.py gates on BENHAM_HEALTH_PORT", "BENHAM_HEALTH_PORT" in _bot_src, True)
check("bot.py calls health.start", "health.start(" in _bot_src, True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
