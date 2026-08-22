"""test_outbox_visibility.py - a refused request must reach the caller that queued it.

The defect class: bot.py records every refusal faithfully - the request moves to
outbox/failed/ with a sibling _result.json naming the error - and the
fire-and-forget CLIs (send, dm, delete, fetch, draft, purge, room post/create)
printed "Queued" and exited 0 without ever looking there. A session could enqueue
a call, see a clean exit, and reasonably believe it succeeded when Discord had
refused it. The 2026-08-18 gate test met this live: a spawned session's test DM
to a fake user id 404'd into failed/ and reached nobody; a second instance on
2026-08-21 was only caught because that session hand-rolled result-file polling.

What is asserted here, in order:
  1. outbox.wait_result finds the result wherever bot._finish filed it.
  2. outbox.report_outcome converts the verdict into the exit-code convention
     (0 ok / 1 failure) and PRINTS the recorded error on a refusal.
  3. End to end through the real CLIs: send and dm exit non-zero when the bot
     refuses, exit zero when it delivers, and --no-wait still fire-and-forgets.
  4. No enqueuing CLI is blind unless it is deliberately exempt - the guard that
     stops the next CLI being added with the old shape.
  5. status.recent_failures surfaces fresh failed/ entries and ages out old ones
     - the net under --no-wait callers and the bot's own internal enqueues.
"""

import glob
import io
import json
import os
import shutil
import sys
import threading
import time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _testconfig                 # noqa: F401,E402 - must precede every benham import

from benham.core import outbox     # noqa: E402
from benham.cli import send        # noqa: E402
from benham.cli import dm          # noqa: E402
from benham.cli import status      # noqa: E402

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def bot_finish(req_path, dest_dirname, result):
    """File a request the way bot.py's _finish does: move + sibling _result.json."""
    base = os.path.splitext(os.path.basename(req_path))[0]
    dest_dir = os.path.join(outbox.OUTBOX, dest_dirname)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{base}_20260101_000000.json")
    shutil.move(req_path, dest)
    with open(os.path.splitext(dest)[0] + "_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return dest


def stub_bot(dest_dirname, result, timeout=10):
    """Answer the NEXT request to appear in the outbox, from a background thread.

    Snapshots what already exists first: earlier checks deliberately leave
    unanswered requests behind (that is what a timeout is), and answering one of
    those would bind this stub's verdict to the wrong question.
    """
    before = set(glob.glob(os.path.join(outbox.OUTBOX, "*.json")))

    def run():
        end = time.time() + timeout
        while time.time() < end:
            new = [p for p in glob.glob(os.path.join(outbox.OUTBOX, "*.json"))
                   if p not in before]
            if new:
                bot_finish(new[0], dest_dirname, result)
                return
            time.sleep(0.05)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


section("wait_result finds the verdict wherever _finish filed it")
_req = outbox.enqueue(action="dm", user_id=1, content="hi")
bot_finish(_req, "sent", {"status": "sent", "message_id": 42})
_res, _where = outbox.wait_result(_req, timeout=5)
check("a delivered request reads back from sent/", _where, "sent")
check("...with the result intact", _res and _res.get("message_id"), 42)

_req = outbox.enqueue(action="dm", user_id=2, content="hi")
bot_finish(_req, "failed", {"status": "failed", "error": "NotFound: Unknown User"})
_res, _where = outbox.wait_result(_req, timeout=5)
check("a refused request reads back from failed/", _where, "failed")
check("...with the recorded error", _res and _res.get("error"),
      "NotFound: Unknown User")

_req = outbox.enqueue(action="dm", user_id=3, content="hi")
_res, _where = outbox.wait_result(_req, timeout=1)
check("no result in time reads as (None, None), not a crash", (_res, _where),
      (None, None))

section("report_outcome speaks the exit-code convention and prints the error")
_req = outbox.enqueue(action="dm", user_id=4, content="hi")
bot_finish(_req, "sent", {"status": "sent", "message_id": 7})
_code, _ = outbox.report_outcome(_req, timeout=5)
check("delivered -> exit code 0", _code, outbox.EXIT_OK)

_req = outbox.enqueue(action="dm", user_id=5, content="hi")
bot_finish(_req, "failed", {"status": "failed", "error": "Forbidden: Cannot send"})
_err = io.StringIO()
with redirect_stderr(_err):
    _code, _res = outbox.report_outcome(_req, timeout=5)
check("refused -> exit code 1", _code, outbox.EXIT_FAIL)
check("...and the caller was SHOWN the error - the whole point",
      "Forbidden: Cannot send" in _err.getvalue(), True)
check("...and the result is handed back for callers that print more",
      _res and _res.get("status"), "failed")

_req = outbox.enqueue(action="dm", user_id=6, content="hi")
_err = io.StringIO()
with redirect_stderr(_err):
    _code, _ = outbox.report_outcome(_req, timeout=1)
check("timeout -> exit code 1 (queued is not delivered)", _code, outbox.EXIT_FAIL)
check("...and the message says still-queued, not failed",
      "still queued" in _err.getvalue(), True)

section("end to end: the real CLIs are no longer blind")
stub_bot("failed", {"status": "failed", "error": "NotFound: Unknown Channel"})
_err = io.StringIO()
with redirect_stderr(_err):
    _code = send.main(["send", "809357286036078612", "hello"])
check("send exits non-zero on a refusal", _code, outbox.EXIT_FAIL)
check("...and prints the recorded error",
      "NotFound: Unknown Channel" in _err.getvalue(), True)

stub_bot("sent", {"status": "sent", "message_id": 99})
_code = send.main(["send", "809357286036078612", "hello again"])
check("send exits zero when the bot delivered", _code, outbox.EXIT_OK)

stub_bot("failed", {"status": "failed", "error": "Forbidden: user blocks DMs"})
_err = io.StringIO()
with redirect_stderr(_err):
    _code = dm.main(["dm", "--tyler", "the deploy finished."])
check("dm exits non-zero on a refusal - the 2026-08-18 fake-id DM would now "
      "have been caught", _code, outbox.EXIT_FAIL)
check("...and prints the recorded error",
      "Forbidden: user blocks DMs" in _err.getvalue(), True)

_t0 = time.time()
_code = send.main(["send", "809357286036078612", "bulk mode", "--no-wait"])
check("--no-wait still fire-and-forgets (exit 0, no result ever written)",
      _code, outbox.EXIT_OK)
check("...and returns immediately rather than polling out the timeout",
      time.time() - _t0 < 5, True)

section("no enqueuing CLI is blind unless deliberately exempt")
# The exempt files enqueue signals whose outcome is tracked elsewhere by design:
# ask/outreach/initiate advance a conversation the 60s tick re-delivers if lost,
# and conv --tell documents queued-not-sent with `conv show` as its verification.
# do.py waits through wait_result directly (it prints the full result itself).
_CLI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "benham", "cli")
_EXEMPT = {"ask.py", "conv.py", "initiate.py", "outreach.py", "do.py"}
for _path in sorted(glob.glob(os.path.join(_CLI_DIR, "*.py"))):
    _name = os.path.basename(_path)
    with open(_path, encoding="utf-8") as _f:
        _src = _f.read()
    if "enqueue(" not in _src or _name in _EXEMPT:
        continue
    check(f"{_name} waits and reports by default",
          "report_outcome" in _src, True)
with open(os.path.join(_CLI_DIR, "do.py"), encoding="utf-8") as _f:
    check("do.py still waits (through the shared helper)",
          "wait_result" in _f.read(), True)

section("status.recent_failures - the net under --no-wait and bot-internal enqueues")
_failed_dir = os.path.join(outbox.OUTBOX, "failed")
os.makedirs(_failed_dir, exist_ok=True)
# Age out everything the earlier checks filed, so this section owns its fixture.
for _fn in os.listdir(_failed_dir):
    _old = os.path.join(_failed_dir, _fn)
    os.utime(_old, (time.time() - 48 * 3600, time.time() - 48 * 3600))

_fresh = os.path.join(_failed_dir, "20260821_120000_aaaaaaaa_20260821_120001_result.json")
with open(_fresh, "w", encoding="utf-8") as _f:
    json.dump({"status": "failed", "error": "x" * 200,
               "request": {"action": "set_channel_permissions"}}, _f)

_rows = status.recent_failures(within_hours=24)
check("one fresh failure is listed", len(_rows), 1)
check("...the 48h-old ones aged out",
      all("aaaaaaaa" in os.path.basename(_fresh) for _ in _rows), True)
_when, _action, _error = _rows[0]
check("the action is read from the archived request", _action,
      "set_channel_permissions")
check("a 200-char error is truncated to one report line", len(_error) <= 120, True)

_bare = os.path.join(_failed_dir, "20260821_130000_bbbbbbbb_20260821_130001_result.json")
with open(_bare, "w", encoding="utf-8") as _f:
    json.dump({"status": "failed", "error": "boom", "request": {"channel_id": 1}}, _f)
_rows = status.recent_failures(within_hours=24)
check("a bare send request (no action key) reports as 'send'",
      _rows[0][1] if _rows else None, "send")
check("newest first", len(_rows), 2)

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
