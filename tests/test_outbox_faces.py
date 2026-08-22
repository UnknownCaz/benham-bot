"""
test_outbox_faces.py - each face has its own outbox, and a request names its face.

Commit 5 of PLAN-second-face.md. The defect class: with one shared outbox
directory, WHICH face sends a message is decided by a listdir race - the worst
of the five shared-state breaks the spike found. Per-face directories dissolve
the race, but only if three properties hold, and each is asserted here:

  * the primary face's outbox is EXACTLY the pre-faces path, so nothing moves;
  * enqueue() refuses a request that names no face - the silent failure it
    prevents (a caller meaning one identity reaching another) is worse than
    the loud one it introduces;
  * a process refuses a request naming another face (outbox.misdelivered,
    pure, wired at the poll site), while a pre-faces file with no face field
    still runs on the primary - nothing queued before the upgrade strands.

    python test_outbox_faces.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - redirects STATE_DIR; this file writes state

import json
import os
import sys

from benham import paths
from benham.core import outbox

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


# --- the paths -------------------------------------------------------------

check("the primary face's outbox IS the pre-faces path",
      outbox.outbox_dir(paths.DEFAULT_FACE),
      os.path.join(paths.STATE_DIR, "outbox"))
check("...and the module constant still points there", outbox.OUTBOX,
      os.path.join(paths.STATE_DIR, "outbox"))
check("a named face's outbox lives under its own state root",
      outbox.outbox_dir("codex"),
      os.path.join(paths.STATE_DIR, "faces", "codex", "outbox"))

# --- enqueue requires a face, and refuses before writing -------------------

_codex_box = outbox.outbox_dir("codex")
_before = set(os.listdir(_codex_box)) if os.path.isdir(_codex_box) else set()
try:
    outbox.enqueue(action="dm", user_id=1, content="hi")
except ValueError as e:
    check("enqueue with no face raises ValueError", True, True)
    check("...and the message says how to mean Benham",
          "paths.DEFAULT_FACE" in str(e), True)
else:
    check("enqueue with no face raises ValueError", False, True)
check("...and nothing was written anywhere",
      set(os.listdir(_codex_box)) if os.path.isdir(_codex_box) else set(), _before)

try:
    outbox.enqueue(face="../escape", action="dm")
except ValueError:
    check("a traversal face name is refused", True, True)
else:
    check("a traversal face name is refused", False, True)

# --- the envelope stamps ---------------------------------------------------

_req_path = outbox.enqueue(face="codex", action="dm", user_id=42, content="hi")
check("a codex request lands in codex's own outbox",
      os.path.dirname(_req_path), _codex_box)
with open(_req_path, encoding="utf-8") as f:
    _req = json.load(f)
check("the request file names its face", _req.get("face"), "codex")
check("queued_at is stamped", bool(_req.get("queued_at")), True)
check("source is stamped - who enqueued this stops being a clustering job",
      bool(_req.get("source")), True)

# --- wait_result looks beside the REQUEST, not beside the module constant --

_sent_dir = os.path.join(_codex_box, "sent")
os.makedirs(_sent_dir, exist_ok=True)
_base = os.path.splitext(os.path.basename(_req_path))[0]
with open(os.path.join(_sent_dir, _base + "_result.json"), "w",
          encoding="utf-8") as f:
    json.dump({"status": "ok"}, f)
os.replace(_req_path, os.path.join(_sent_dir, _base + ".json"))
_result, _where = outbox.wait_result(_req_path, timeout=3)
check("wait_result finds a result in the request's own face's outbox",
      (_result or {}).get("status"), "ok")
check("...and says which verdict folder held it", _where, "sent")

# --- misdelivered: the pure decision the poller wires ----------------------

check("a request with no face field runs on the primary (pre-faces files)",
      outbox.misdelivered({"action": "dm"}, paths.DEFAULT_FACE), None)
check("a benham request runs on benham",
      outbox.misdelivered({"face": "benham"}, "benham"), None)
check("a codex request runs on codex",
      outbox.misdelivered({"face": "codex"}, "codex"), None)
_msg = outbox.misdelivered({"face": "codex"}, "benham")
check("a codex request is refused by a benham process", _msg is not None, True)
check("...and the reason names both faces",
      "codex" in (_msg or "") and "benham" in (_msg or ""), True)
check("a pre-faces file is refused by a NON-primary process - it belongs to "
      "the primary", outbox.misdelivered({"action": "dm"}, "codex") is not None,
      True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
