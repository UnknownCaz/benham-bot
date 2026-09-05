"""
test_server.py - the bot's Phase B surface: gated, honest, single-writer.

The PC's benham.py reaches the bot on cazzy-mac through benham/core/server.py
(INTENT decision 38). This file pins what the surface promises, against a
real listener on a loopback port with a fake client and a real event loop:

  1. the gate - a missing or wrong X-Benham-Token is 401 with NO body; a Host
     header off the allowlist is 403; both before any route runs,
  2. /health tells the truth about the face and the gateway,
  3. /outbox enqueues into THIS face's outbox and refuses another face's
     request instead of misdelivering it; /outbox/<id> finds the archived
     result wherever the poller filed it, and says "not yet" until then,
  4. /store runs ONLY rpc.TABLE names (the allowlist is the table), executes
     them ON the bot's loop (single writer), carries a raised exception back
     as the same class - and the client-side proxies re-raise it,
  5. /file confines an upload to state/uploads/ whatever the filename says,
  6. remote.py's shapes: RemoteError one-liners name the host, and a tree
     whose state was MOVED refuses to run local with no remote.json.

    python tests/test_server.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request

from benham import paths
from benham.core import outbox, remote, rpc, server

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n--- {title}")


class FakeClient:
    ready = True

    def is_ready(self):
        return self.ready

    def is_closed(self):
        return False


def _raised(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return type(e).__name__
    return None


def _raised_msg(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return str(e)
    return ""


def _testconfig_owner():
    from benham.core import identity
    return identity.OWNER_IDS


# --- a real listener over the scratch state -------------------------------

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, name="test-loop", daemon=True).start()
token_file = os.path.join(tempfile.mkdtemp(prefix="benham-srv-"), "benham-bot.token")
logged = []
client = FakeClient()
srv = server.start("127.0.0.1", 0, client, "benham", logged.append,
                   token_path=token_file, loop=loop, host_name="test-mac", retries=1)
PORT = srv.server_address[1]
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = open(token_file, encoding="utf-8").read().strip()


def req(method, route, body=None, token=TOKEN, host=None, raw=None, ctype=None):
    """(status, parsed-or-raw body)."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {}
    if token is not None:
        headers[server.HEADER] = token
    if host is not None:
        headers["Host"] = host
    if ctype:
        headers["Content-Type"] = ctype
    elif data is not None:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + route, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            out = resp.read()
            return resp.status, (json.loads(out) if out else b"")
    except urllib.error.HTTPError as e:
        out = e.read()
        try:
            return e.code, json.loads(out)
        except ValueError:
            return e.code, out


section("1. the gate: token first, no body on a refusal; Host allowlist")
check("token file was minted, mode-600 shaped, non-empty", len(TOKEN) > 20, True)
check("no token -> 401", req("GET", "/health", token=None)[0], 401)
check("...with NO body", req("GET", "/health", token=None)[1], b"")
check("wrong token -> 401", req("GET", "/health", token="nope")[0], 401)
check("a bad Host header -> 403 (DNS rebinding)",
      req("GET", "/health", host="evil.example:80")[0], 403)
check("the bind address:port IS on the allowlist",
      req("GET", "/health", host=f"127.0.0.1:{PORT}")[0], 200)
check("the start line names the bind and says token-gated",
      any("token-gated" in l and f"127.0.0.1:{PORT}" in l for l in logged), True)

section("2. /health is the health JSON, and it reads the client")
code, body = req("GET", "/health")
check("200 with the face", (code, body["face"]), (200, "benham"))
check("gateway_connected reads the client", body["gateway_connected"], True)
client.ready = False
check("...and flips with it", req("GET", "/health")[1]["gateway_connected"], False)
client.ready = True

section("3. /outbox: this face only, results found where the poller filed them")
code, body = req("POST", "/outbox", {"face": "benham", "fields": {"channel_id": 1, "content": "hi"}})
check("enqueue answers 200 with an id and a path", (code, sorted(body)), (200, ["id", "path"]))
rid = body["id"]
box = outbox.outbox_dir("benham")
check("the request file landed in THIS face's outbox",
      os.path.exists(os.path.join(box, rid + ".json")), True)
with open(os.path.join(box, rid + ".json"), encoding="utf-8") as f:
    filed = json.load(f)
check("the envelope is stamped by the bot's own enqueue (face, queued_at, source)",
      (filed["face"], "queued_at" in filed, filed["source"]), ("benham", True, "remote-cli"))
code, body = req("POST", "/outbox", {"face": "codex", "fields": {"content": "x"}})
check("another face's request is REFUSED, not misdelivered", code, 400)
check("...and the refusal says so", "refusing to act as another identity" in body["error"], True)
check("no result yet reads where=None", req("GET", f"/outbox/{rid}")[1], {"where": None, "result": None})
# file it the way bot.py's _finish does
os.makedirs(os.path.join(box, "sent"), exist_ok=True)
os.replace(os.path.join(box, rid + ".json"), os.path.join(box, "sent", rid + "_20260905_000000.json"))
with open(os.path.join(box, "sent", rid + "_20260905_000000_result.json"), "w", encoding="utf-8") as f:
    json.dump({"status": "sent", "message_id": 7}, f)
check("the archived result is found, with where it went",
      req("GET", f"/outbox/{rid}")[1], {"where": "sent", "result": {"status": "sent", "message_id": 7}})
check("a traversal in the id is refused", req("GET", "/outbox/../x")[0] in (400, 404), True)

section("4. /store: the table is the allowlist; on the loop; exceptions carried")
check("an unlisted name is 404", req("POST", "/store/os.system", {"args": ["x"]})[0], 404)
check("a listed name runs", req("POST", "/store/rooms.listing", {"args": ["cli"]})[1], {"result": []})
# Prove the call ran ON the loop thread: rpc.TABLE is the allowlist, so the
# probe is registered into it for the check's duration and removed after.
seen = {}


def _probe():
    seen["thread"] = threading.current_thread().name
    try:
        seen["loop_running"] = asyncio.get_running_loop() is loop
    except RuntimeError:
        seen["loop_running"] = False
    return "ok"


rpc.TABLE["test.probe"] = "benham.core.rpc:_test_probe"
rpc._test_probe = _probe
try:
    check("a store call is executed ON the bot's loop (single writer)",
          (req("POST", "/store/test.probe", {})[1], seen.get("loop_running")), ({"result": "ok"}, True))
finally:
    del rpc.TABLE["test.probe"]
    del rpc._test_probe
code, body = req("POST", "/store/conversations.get", {"args": ["c99"]})
check("a store function's None comes back as null", (code, body), (200, {"result": None}))
code, body = req("POST", "/store/initiative.drop_thread", {"args": ["t9", "x"]})
check("a raised exception is carried, not a 500",
      (code, body.get("raised", {}).get("type")), (200, "KeyError"))
check("a malformed body is 400", req("POST", "/store/rooms.listing", raw=b"{nope", ctype="application/json")[0], 400)

section("5. /file confines the upload")
boundary = "----t"
part = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"../../escape.txt\"\r\nContent-Type: text/plain\r\n\r\nhello\r\n--{boundary}--\r\n").encode()
code, body = req("POST", "/file", raw=part, ctype=f"multipart/form-data; boundary={boundary}")
check("upload answers 200 with a path", (code, "path" in body), (200, True))
uploads = os.path.realpath(os.path.join(paths.process_state_dir(), server.UPLOADS))
check("...inside state/uploads/, whatever the filename said",
      os.path.commonpath([os.path.realpath(body["path"]), uploads]), uploads)
with open(body["path"], "rb") as f:
    check("...with the bytes intact", f.read(), b"hello")
check("a non-multipart /file is 400", req("POST", "/file", {"x": 1})[0], 400)

section("6. remote.py: the client shapes")
os.environ["BENHAM_REMOTE_URL"] = BASE
os.environ["BENHAM_REMOTE_TOKEN_FILE"] = token_file
os.environ["BENHAM_REMOTE_HOST"] = "test-mac"
remote._config_cache = None
check("active() with the env override", remote.active(), True)
check("the proxy forwards a table name", remote.stores.rooms.listing("cli"), [])
check("...and re-raises the server's exception class",
      _raised(lambda: remote.stores.initiative.drop_thread("t9", "x")), "KeyError")
check("a non-table attribute is the local module's own", remote.stores.rooms.SCRATCH, "scratch")
check("identity() reads the SERVING face's owners",
      remote.identity()["owner_ids"], sorted(_testconfig_owner()))
path = remote.enqueue("benham", {"content": "hi", "channel_id": 1, "source": "test"})
check("enqueue over the wire returns the bot-side path", path.startswith(box), True)
check("wait_result polls to a timeout without a result", remote.wait_result(path, timeout=1), (None, None))
os.environ["BENHAM_REMOTE_TOKEN_FILE"] = token_file + ".missing"
remote._config_cache = None
err = _raised_msg(lambda: remote.stores.rooms.listing("cli"))
check("a missing token file is one line naming the host",
      "no token at" in err and "test-mac" in err and "\n" not in err, True)
os.environ["BENHAM_REMOTE_TOKEN_FILE"] = token_file
os.environ["BENHAM_REMOTE_URL"] = "http://127.0.0.1:1"
remote._config_cache = None
err = _raised_msg(lambda: remote.stores.rooms.listing("cli"))
check("an unreachable bot is one line naming the host and the address",
      err.startswith("cannot reach the bot on test-mac") and "\n" not in err, True)
for k in ("BENHAM_REMOTE_URL", "BENHAM_REMOTE_TOKEN_FILE", "BENHAM_REMOTE_HOST"):
    os.environ.pop(k, None)
remote._config_cache = None
check("without the env and without remote.json: local", remote.active(), False)
with open(os.path.join(paths.STATE_DIR, "MOVED-2026-09-05.md"), "w", encoding="utf-8") as f:
    f.write("moved\n")
remote._config_cache = None
err = _raised_msg(remote.active)
check("a MOVED marker with no remote.json REFUSES local (never write frozen history)",
      "MOVED" in err and "remote.json" in err, True)
os.remove(os.path.join(paths.STATE_DIR, "MOVED-2026-09-05.md"))
remote._config_cache = None

srv.shutdown()
loop.call_soon_threadsafe(loop.stop)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    _sys.exit(1)
print("ALL PASS")
