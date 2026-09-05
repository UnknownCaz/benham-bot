"""
server.py - the bot's HTTP surface for a CLI on another machine (Phase B).

OFF unless asked for, like health.py: bot.py starts this only under
BENHAM_API_BIND, so every launch without it - the whole pre-Phase-B PC world,
the test suite - binds nothing and is byte-identical.

THE SHAPE IS BANKER-AGENT'S, DELIBERATELY. Interface bind (the tailnet
address, NEVER 0.0.0.0 - Doom's shared devices reach nothing today and keep
reaching nothing), a shared token in ~/.config/benham-bot.token minted on
first boot and carried by every request as X-Benham-Token, and a Host-header
allowlist against DNS rebinding. A missing or wrong token is 401 with no
body. Loopback is not a security boundary (the banker-panel lesson), so the
token gate applies on loopback too.

Same port number as the health listener, two sockets: the console probes
127.0.0.1:8903 (health.py, liveness only) and the PC reaches
100.76.11.56:8903 (this). One ledger reservation, the agent's two-listener
shape.

Routes - one verb, one handler:

  GET  /health           the health JSON, so `benham.py status` reads it
  POST /outbox           enqueue a request into THIS face's outbox; the
                         bot's own poller runs it. {"face", "fields"} ->
                         {"id", "path"}. A request naming another face is
                         refused here rather than misdelivered.
  GET  /outbox/<id>      the archived result: {"where": sent|failed|null,
                         "result"}. The client polls at the poller's cadence.
  POST /store/<name>     one rpc.TABLE operation, executed IN THE BOT
                         PROCESS - on its event loop unless the name is
                         rpc.SLOW - so the bot is the single writer of every
                         store. {"args", "kwargs"} -> {"result"} or
                         {"raised": {type, args, message}}; the client
                         re-raises the same class so CLI words hold.
  POST /file             multipart upload into state/uploads/, so a `path=`
                         from the PC keeps working. -> {"path"}

Store calls run through asyncio.run_coroutine_threadsafe onto the bot's
loop: a `conv close` from the PC serialises with tick_conversations instead
of racing it across two processes, which is strictly better than what two
processes on one filesystem had before.
"""

import asyncio
import email.parser
import email.policy
import json
import os
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from benham import paths
from benham.core import health, outbox, pathsafe, rpc

HEADER = "X-Benham-Token"
DEFAULT_TOKEN_FILE = os.path.join("~", ".config", "benham-bot.token")
UPLOADS = "uploads"
UPLOAD_MAX = 30 * 1024 * 1024       # Discord's own ceiling is 25 MB
UPLOAD_KEEP_S = 24 * 3600


def load_token(path=None):
    """The shared token. Created on first boot, then stable - the PC gets a
    copy of the file once. agent.py's exact shape."""
    path = os.path.expanduser(path or DEFAULT_TOKEN_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(tok)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


class _Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"   # health.py's reasoning, verbatim
    daemon_threads = True

    def __init__(self, addr, handler, *, token, face, client, log, loop, hosts):
        super().__init__(addr, handler)
        self.token = token
        self.face = face
        self.client = client
        self.log = log
        self.loop = loop
        self.hosts = hosts


def _json_default(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


class Handler(BaseHTTPRequestHandler):
    server_version = "benham-api/phase-b"

    # ---- plumbing -------------------------------------------------------

    def log_message(self, *_args):
        pass  # the bot's own log() below says what matters

    def _send(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj, default=_json_default).encode("utf-8")
        self.send_response(code)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _gate(self):
        """Host allowlist, then the token. False means already answered."""
        host = (self.headers.get("Host") or "").lower()
        if host not in self.server.hosts:
            self._send(403, {"error": "bad Host header"})
            return False
        if not self.server.token or self.headers.get(HEADER) != self.server.token:
            self._send(401)          # no body, on purpose
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("request body is not JSON")

    def _loop_call(self, fn, *args, **kwargs):
        """Run fn ON the bot's loop when it is running, inline otherwise."""
        loop = self.server.loop
        if loop is None:
            loop = getattr(self.server.client, "loop", None)
        if loop is not None and getattr(loop, "is_running", lambda: False)():
            async def _run():
                return fn(*args, **kwargs)
            fut = asyncio.run_coroutine_threadsafe(_run(), loop)
            return fut.result(timeout=60)
        return fn(*args, **kwargs)

    # ---- routes ---------------------------------------------------------

    def do_GET(self):
        if not self._gate():
            return
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, health.snapshot(self.server.client, self.server.face))
        if url.path.startswith("/outbox/"):
            return self._outbox_result(url.path[len("/outbox/"):])
        return self._send(404, {"error": f"no route {url.path}"})

    def do_POST(self):
        if not self._gate():
            return
        url = urlparse(self.path)
        try:
            if url.path == "/outbox":
                return self._outbox_enqueue()
            if url.path.startswith("/store/"):
                return self._store(url.path[len("/store/"):])
            if url.path == "/file":
                return self._file()
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - one bad request must not kill the thread
            self.server.log(f"api: {self.path} failed: {type(e).__name__}: {e}\n"
                            + traceback.format_exc())
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": f"no route {url.path}"})

    def _outbox_enqueue(self):
        body = self._json_body()
        face = body.get("face")
        if face != self.server.face:
            raise ValueError(f"this bot runs face {self.server.face!r}; the request "
                             f"names {face!r} - refusing to act as another identity")
        fields = body.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")
        source = fields.pop("source", None) or "remote-cli"
        path = outbox.enqueue_local(face=self.server.face, _source=source, **fields)
        rid = os.path.splitext(os.path.basename(path))[0]
        self.server.log(f"api: outbox {fields.get('action', 'send')} <- "
                        f"{self.client_address[0]} ({rid})")
        return self._send(200, {"id": rid, "path": path})

    def _outbox_result(self, rid):
        if not rid or "/" in rid or "\\" in rid or ".." in rid:
            return self._send(400, {"error": "bad request id"})
        box = outbox.outbox_dir(self.server.face)
        for where in outbox.RESULT_DIRS:
            folder = os.path.join(box, where)
            if not os.path.isdir(folder):
                continue
            for fn in os.listdir(folder):
                if fn.startswith(rid) and fn.endswith("_result.json"):
                    with open(os.path.join(folder, fn), encoding="utf-8") as f:
                        return self._send(200, {"where": where, "result": json.load(f)})
        return self._send(200, {"where": None, "result": None})

    def _store(self, name):
        if name not in rpc.TABLE:
            return self._send(404, {"error": f"no store operation {name!r}"})
        body = self._json_body()
        args = body.get("args") or []
        kwargs = body.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("args must be a list and kwargs an object")
        fn = rpc.resolve(name)
        self.server.log(f"api: store {name} <- {self.client_address[0]}")
        try:
            if name in rpc.SLOW:
                result = fn(*args, **kwargs)
            else:
                result = self._loop_call(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - carried to the client, re-raised there
            return self._send(200, {"raised": {"type": type(e).__name__,
                                               "args": [str(a) for a in e.args],
                                               "message": str(e)}})
        return self._send(200, {"result": result})

    def _file(self):
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ctype:
            raise ValueError("POST /file needs multipart/form-data")
        n = int(self.headers.get("Content-Length") or 0)
        if n > UPLOAD_MAX:
            raise ValueError(f"upload too large ({n} bytes; max {UPLOAD_MAX})")
        raw = b"Content-Type: " + ctype.encode("utf-8") + b"\r\n\r\n" + self._body()
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
        folder = os.path.join(paths.process_state_dir(), UPLOADS)
        os.makedirs(folder, exist_ok=True)
        _sweep_uploads(folder)
        for part in msg.iter_parts():
            filename = part.get_filename()
            if not filename:
                continue
            safe = pathsafe.safe_filename(filename, "upload.bin")
            dest = pathsafe.confined_path(folder, f"{int(time.time())}-{safe}")
            with open(dest, "wb") as f:
                f.write(part.get_payload(decode=True) or b"")
            self.server.log(f"api: file {safe} ({os.path.getsize(dest)} bytes) <- "
                            f"{self.client_address[0]}")
            return self._send(200, {"path": dest})
        raise ValueError("no file part in the upload")


def _sweep_uploads(folder):
    """Uploads are transit, not storage: anything older than a day goes."""
    cutoff = time.time() - UPLOAD_KEEP_S
    try:
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass


def start(bind, port, client, face, log, token_path=None, loop=None, host_name=None,
          retries=12, retry_wait=5.0, extra_hosts=None):
    """Bind <bind>:<port> and serve from a daemon thread. Returns the server.

    Retries the bind: under launchd the Tailscale interface can come up after
    us, and an address that does not exist yet is EADDRNOTAVAIL, not a taken
    port. A taken port on the last try raises, loudly, before client.run() -
    health.py's doctrine.

    MEASURED 2026-09-05 on cazzy-mac (Tailscale.app): a Python listener bound
    to the Tailscale interface address accepts and then reads ENOTCONN on
    every connection - stdlib http.server and a raw socket alike - so the
    interface bind the brief asked for is not possible there. The shape that
    keeps every property (tailnet-only, never 0.0.0.0, the same address:port
    from the PC) is the console's own: bind LOOPBACK and expose it with
    `tailscale serve --tcp 8903 tcp://127.0.0.1:8903`. The client still sends
    Host: 100.76.11.56:8903, so that value must be on the allowlist -
    `extra_hosts` (BENHAM_API_HOSTS in the plist, comma-separated) carries it.
    """
    token = load_token(token_path or os.environ.get("BENHAM_API_TOKEN_FILE") or None)
    last = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            server = _Server((bind, int(port)), Handler, token=token, face=face,
                             client=client, log=log, loop=loop, hosts=set())
            break
        except OSError as e:
            last = e
            if attempt >= retries:
                raise
            log(f"api: bind {bind}:{port} failed ({e}); retry {attempt}/{retries} in {retry_wait:.0f}s")
            time.sleep(retry_wait)
    else:  # pragma: no cover - the loop either breaks or raises
        raise last
    # The allowlist is built from the port actually BOUND (port 0 = ephemeral,
    # which the tests use), not the one asked for.
    bound = server.server_address[1]
    server.hosts = {f"{bind}:{bound}", bind, f"127.0.0.1:{bound}", "127.0.0.1",
                    f"localhost:{bound}", "localhost"}
    for h in (extra_hosts or os.environ.get("BENHAM_API_HOSTS", "")).split(",") if isinstance(
            extra_hosts or os.environ.get("BENHAM_API_HOSTS", ""), str) else (extra_hosts or []):
        if h.strip():
            server.hosts.add(h.strip().lower())
    rpc.set_runtime(client, face, host=host_name,
                    log_file=os.environ.get("BENHAM_LOG_FILE", "").strip() or None)
    threading.Thread(target=server.serve_forever, name="api-port", daemon=True).start()
    log(f"API: {bind}:{server.server_address[1]} (token-gated, {len(rpc.TABLE)} store ops, "
        f"hosts {sorted(server.hosts)})")
    return server
