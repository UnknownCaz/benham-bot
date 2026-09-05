"""
remote.py - the client half of Phase B: benham.py talking to a bot elsewhere.

The bot runs on cazzy-mac (INTENT decision 38). Every `benham.py` verb keeps
its words and exit codes; what changed is that its body reaches the bot over
the tailnet instead of over the filesystem. This module is the only place
that knows how.

CONFIGURATION IS A FILE, NOT A FLAG. Every caller of benham.py - RAVEN.md's
allowlisted literals, initiate.bat, watch-exports.ps1, the CLAUDE.md
ask-queue line - runs the same command it always did, so the client cannot
depend on an argument or an environment variable being passed. It reads
config/remote.json (untracked, like control.json):

    {"url": "http://100.76.11.56:8903",
     "token_file": "~/.config/benham-bot.token",
     "host": "cazzy-mac"}

Absent means LOCAL: the CLI works the tree it sits in, exactly as before
Phase B. That is what the test suite runs, and what a shell on the Mac itself
runs. BENHAM_REMOTE_URL / BENHAM_REMOTE_TOKEN_FILE override the file for the
words test, which stands a server up on a loopback port.

THE GUARD AGAINST WRITING THE WRONG MACHINE. If the state directory carries a
MOVED-*.md marker (the cutover writes one beside the frozen PC stores) and
there is no remote.json, the client REFUSES rather than silently writing
frozen history - a PC session's `ask` landing on the wrong machine is the
exact failure D1 exists to prevent.

FAILURE SHAPE. Tailnet down, token wrong, bot down: RemoteError with one line
naming the host. benham.py catches it at the top and exits 1 - never a
traceback, because Raven and the initiates lane read these unattended.

The store proxies at the bottom are what the CLI modules import: an
attribute in rpc.TABLE forwards to the server, anything else (constants,
pure helpers) is the local module's own. The same table is the server's
allowlist, so client and server cannot disagree about what is reachable.
"""

import glob
import importlib
import json
import os
import time
import urllib.error
import urllib.request

from benham import paths
from benham.core import rpc

HEADER = "X-Benham-Token"
CONFIG_NAME = "remote.json"
DEFAULT_TOKEN_FILE = os.path.join("~", ".config", "benham-bot.token")

_config_cache = None


class RemoteError(Exception):
    """One line, for the person reading it; benham.py prints it and exits 1."""


def config():
    """The remote config, or None for local. Cached per process."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache or None
    url = os.environ.get("BENHAM_REMOTE_URL", "").strip()
    if url:
        cfg = {"url": url,
               "token_file": os.environ.get("BENHAM_REMOTE_TOKEN_FILE", "").strip()
               or DEFAULT_TOKEN_FILE,
               "host": os.environ.get("BENHAM_REMOTE_HOST", "").strip() or "the bot host"}
    else:
        path = os.path.join(paths.CONFIG_DIR, CONFIG_NAME)
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            cfg = {}
        except ValueError as e:
            raise RemoteError(f"{path} is not valid JSON ({e}) - fix it or move it aside")
        if cfg and not cfg.get("url"):
            raise RemoteError(f"{path} has no \"url\" - it needs the bot's tailnet address")
        if not cfg:
            moved = glob.glob(os.path.join(paths.STATE_DIR, "MOVED-*.md"))
            if moved:
                raise RemoteError(
                    f"this tree's state was MOVED ({os.path.basename(moved[0])}) but "
                    f"config/{CONFIG_NAME} is missing - refusing to write frozen "
                    "history; point the client at the bot's host")
    _config_cache = cfg or {}
    return cfg or None


def active():
    return config() is not None


def host():
    cfg = config() or {}
    return cfg.get("host") or "the bot host"


def _token():
    cfg = config()
    path = os.path.expanduser(cfg.get("token_file") or DEFAULT_TOKEN_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            tok = f.read().strip()
    except OSError:
        raise RemoteError(f"no token at {path} - copy ~/.config/benham-bot.token from {host()}")
    if not tok:
        raise RemoteError(f"{path} is empty - copy ~/.config/benham-bot.token from {host()}")
    return tok


def call(method, route, body=None, timeout=30, files=None):
    """One request. Returns the parsed JSON body. RemoteError on any failure,
    worded for a human and naming the host."""
    cfg = config()
    if cfg is None:
        raise RemoteError("no remote is configured")
    url = cfg["url"].rstrip("/") + route
    headers = {HEADER: _token(), "User-Agent": "benham-cli/phase-b"}
    data = None
    if files is not None:
        data, ctype = _multipart(body or {}, files)
        headers["Content-Type"] = ctype
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.code == 401:
            raise RemoteError(f"{host()} refused the token - the one in your token file "
                              "does not match the bot's (copy ~/.config/benham-bot.token again)")
        if e.code == 403:
            raise RemoteError(f"{host()} refused the request (403): Host header not on the bot's allowlist")
        try:
            err = json.loads(raw.decode("utf-8"))
        except ValueError:
            err = {}
        if isinstance(err, dict) and err.get("error"):
            raise RemoteError(f"{host()}: {err['error']}")
        raise RemoteError(f"{host()} answered HTTP {e.code} on {route}")
    except urllib.error.URLError as e:
        raise RemoteError(f"cannot reach the bot on {host()} ({cfg['url']}): "
                          f"{getattr(e, 'reason', e)} - is the tailnet up and the bot running?")
    except (TimeoutError, OSError) as e:
        raise RemoteError(f"cannot reach the bot on {host()} ({cfg['url']}): {e}")
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise RemoteError(f"{host()} returned something that is not JSON on {route}")


def _multipart(fields, files):
    """A multipart/form-data body: (bytes, content-type). Stdlib only."""
    boundary = "----benham-" + os.urandom(8).hex()
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v}\r\n").encode("utf-8")
    for k, path in files:
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            content = fh.read()
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8")
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------
# Store calls
# --------------------------------------------------------------------------

_RAISE = {"KeyError": KeyError, "ValueError": ValueError, "OSError": OSError,
          "FileNotFoundError": FileNotFoundError, "RuntimeError": RuntimeError,
          "TypeError": TypeError}


def store(name, *args, **kwargs):
    """Run one rpc.TABLE operation on the bot. A store function that raised
    there raises the same exception class here, with the same message, so
    the CLI's own except-branches keep their words."""
    out = call("POST", "/store/" + name, {"args": list(args), "kwargs": kwargs},
               timeout=kwargs.pop("_timeout", None) or (120 if name in rpc.SLOW else 30))
    if isinstance(out, dict) and "raised" in out:
        exc = _RAISE.get(out["raised"].get("type"), RuntimeError)
        raise exc(*out["raised"].get("args", [out["raised"].get("message", "")]))
    return out.get("result") if isinstance(out, dict) else out


class _Proxy:
    """`stores.conversations.get(...)` - remote when a remote is configured,
    the local module's own function otherwise. Names not in rpc.TABLE are
    always the local module's attribute: constants, pure helpers."""

    def __init__(self, prefix, module):
        self._prefix = prefix
        self._module = module
        self._local = None

    def _mod(self):
        if self._local is None:
            self._local = importlib.import_module(self._module)
        return self._local

    def __getattr__(self, attr):
        name = f"{self._prefix}.{attr}"
        if name in rpc.TABLE:
            if active():
                return lambda *a, **kw: store(name, *a, **kw)
            return rpc.resolve(name)
        return getattr(self._mod(), attr)


class _Stores:
    conversations = _Proxy("conversations", "benham.core.conversations")
    initiative = _Proxy("initiative", "benham.core.initiative")
    ideas = _Proxy("ideas", "benham.core.ideas")
    issues = _Proxy("issues", "benham.core.issues")
    loopclose = _Proxy("loopclose", "benham.core.loopclose")
    rooms = _Proxy("rooms", "benham.core.rooms")
    guest = _Proxy("guest", "benham.guest.guest")
    policy = _Proxy("policy", "benham.core.policy")
    rpc = _Proxy("rpc", "benham.core.rpc")


stores = _Stores()

_identity_cache = None


def identity():
    """The serving face's owner/guest/outreach facts (rpc.identity_snapshot).

    Cached per process when REMOTE - one round trip per CLI run - and read
    fresh every call when local: identity's module state is what the tests
    swap between checks, and a snapshot taken once would lie to them."""
    global _identity_cache
    if not active():
        return stores.rpc.identity_snapshot()
    if _identity_cache is None:
        _identity_cache = stores.rpc.identity_snapshot()
    return _identity_cache


# --------------------------------------------------------------------------
# Outbox over the wire (outbox.py calls these when active())
# --------------------------------------------------------------------------

def enqueue(face, fields):
    """POST /outbox. Returns the request's path ON THE BOT'S HOST - the same
    string the local enqueue returns, so every 'Queued -> <path>' line keeps
    its shape and the caller polls it by name."""
    out = call("POST", "/outbox", {"face": face, "fields": fields})
    return out["path"]


def wait_result(req_path, timeout=60):
    """Poll GET /outbox/<id> at the local poller's cadence."""
    rid = os.path.splitext(os.path.basename(req_path))[0]
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = call("GET", f"/outbox/{rid}", timeout=15)
        if out and out.get("where"):
            return out["result"], out["where"]
        time.sleep(0.5)
    return None, None


def upload(path):
    """Ship one local file to the bot's host so a `path=` parameter keeps
    working from the PC. Returns the path on the bot's host."""
    if not os.path.isfile(path):
        raise RemoteError(f"no such file: {path}")
    out = call("POST", "/file", {}, files=[("file", path)], timeout=300)
    return out["path"]
