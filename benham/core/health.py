"""
benham/core/health.py - a tiny loopback liveness port, OFF unless asked for.

Exists for exactly one consumer: the Banker console's host-aware member probe.
A face that migrates to a body with no banker-agent (cazzy-mac, migration
Phase 4) is process-invisible - the console's only eye on that body is its own
TCP connect toward a port the ledger declares (services.json, host + port,
startup_check rule 4) - so the process binds one port and the connect becomes
the probe, exactly the cazzy-mac-ssh shape.

OFF is the default and the primary face never turns it on: BENHAM_HEALTH_PORT
absent or empty means start() is never called and nothing binds - every PC
launch byte-identical. The gate lives in main(), not here.

The bind is 127.0.0.1 ONLY, deliberately: the console stands on the same body
and probes loopback, so a tailnet bind would be surface with no consumer.

start() is called BEFORE client.run() on purpose: a taken port raises and
crash-loops under launchd without ever paying a Discord IDENTIFY - and a bot
that ran on while its declared port stayed dark would read DOWN on the console
forever, which is the invisible-service disease this port exists to kill
(the 36-hour Codex outage). Refusing to boot is the safe direction, same
doctrine as identity's unparseable-control refusal.

The TCP connect is the whole probe; the JSON body is a courtesy for humans:
face, pid, uptime, and whether the gateway is currently connected.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_started = time.time()


class _Server(ThreadingHTTPServer):
    # The console's own macOS lesson (migration step B): without SO_REUSEADDR a
    # launchd respawn's bind fights TIME_WAIT for a minute or two. On Windows
    # the same flag lets a second process bind over a LIVE listener, so it
    # stays off there - this module never runs on the PC today, but a guard
    # this cheap should not depend on that staying true.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def start(port, client, face, log):
    """Bind 127.0.0.1:<port> and answer liveness JSON from a daemon thread.

    Raises OSError when the bind fails - callers run this before client.run()
    so the failure is loud and never costs a gateway login (see module doc).
    Returns the server so tests can shut it down; the bot never does.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({
                "face": face,
                "pid": os.getpid(),
                "uptime_s": int(time.time() - _started),
                # Plain flags, safe to read off-loop: is_ready() is False
                # while (re)connecting, is_closed() flips at shutdown. The
                # port proves the PROCESS; this field says whether Discord
                # is currently on the other end of it.
                "gateway_connected": bool(client.is_ready() and not client.is_closed()),
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass  # the console probes every cycle; that is not news

    server = _Server(("127.0.0.1", int(port)), Handler)
    threading.Thread(target=server.serve_forever, name="health-port",
                     daemon=True).start()
    log(f"Health port: 127.0.0.1:{server.server_address[1]} (liveness only, loopback)")
    return server
