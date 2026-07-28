"""
test_jsonio.py - the shared JSON/JSONL helpers everything else's state sits on.

Small module, load-bearing promises. The outbox poller's "a request appears
complete or not at all" rests on write_json's atomic replace; the guest quota
and both memory files rest on read_json's narrow exception policy (OSError and
ValueError only - anything else is a real bug and must surface, not be swallowed
as if the file were merely missing); and every transcript reader rests on
iter_jsonl treating a torn final line as normal, because something is usually
still appending.

    python test_jsonio.py
"""

import json
import os
import sys
import tempfile

import jsonio

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


_tmp = tempfile.mkdtemp(prefix="benham-jsonio-test-")


def path(name):
    return os.path.join(_tmp, name)


# --------------------------------------------------------------------------
section("read_json — missing and malformed both mean the default")

check("a missing file is the implicit default", jsonio.read_json(path("no.json")),
      {})
check("an explicit default is honoured",
      jsonio.read_json(path("no.json"), default=[1, 2]), [1, 2])

with open(path("bad.json"), "w", encoding="utf-8") as f:
    f.write("{not json")
check("a malformed file is the default too",
      jsonio.read_json(path("bad.json"), default={"fallback": True}),
      {"fallback": True})

with open(path("ok.json"), "w", encoding="utf-8") as f:
    json.dump({"a": 1}, f)
check("a valid file reads through", jsonio.read_json(path("ok.json")), {"a": 1})


# --------------------------------------------------------------------------
section("write_json — atomic, so a reader never sees half a file")

jsonio.write_json(path("w.json"), {"n": 7})
check("a write round-trips", jsonio.read_json(path("w.json")), {"n": 7})
check("no .tmp staging file is left behind",
      os.path.exists(path("w.json") + ".tmp"), False)
jsonio.write_json(path("w.json"), {"n": 8})
check("an overwrite replaces cleanly", jsonio.read_json(path("w.json")),
      {"n": 8})
check("non-ascii survives (ensure_ascii=False)",
      (jsonio.write_json(path("u.json"), {"s": "émoji 🎲"})
       or jsonio.read_json(path("u.json"))), {"s": "émoji 🎲"})


# --------------------------------------------------------------------------
section("rotate_if_large — one previous generation, no cleanup job")

with open(path("log.jsonl"), "w", encoding="utf-8") as f:
    f.write("x" * 100)
check("a small file does not rotate",
      jsonio.rotate_if_large(path("log.jsonl"), max_bytes=1000), False)
check("...and stays put", os.path.exists(path("log.jsonl")), True)

check("an oversized file rotates",
      jsonio.rotate_if_large(path("log.jsonl"), max_bytes=50), True)
check("...moving aside to .1", os.path.exists(path("log.jsonl") + ".1"), True)
check("...leaving no original", os.path.exists(path("log.jsonl")), False)
check("a missing file reports no rotation",
      jsonio.rotate_if_large(path("gone.jsonl")), False)


# --------------------------------------------------------------------------
section("append_jsonl / iter_jsonl — a torn last line is normal, not corruption")

for i in range(3):
    jsonio.append_jsonl(path("t.jsonl"), {"i": i})
check("appends read back in order",
      [r["i"] for r in jsonio.iter_jsonl(path("t.jsonl"))], [0, 1, 2])

# Simulate a writer caught mid-append: a truncated final line.
with open(path("t.jsonl"), "a", encoding="utf-8") as f:
    f.write('{"i": 3, "partial')
check("the torn line is skipped by default",
      [r["i"] for r in jsonio.iter_jsonl(path("t.jsonl"))], [0, 1, 2])
try:
    list(jsonio.iter_jsonl(path("t.jsonl"), skip_bad=False))
    raised = False
except ValueError:
    raised = True
check("skip_bad=False surfaces it instead", raised, True)

check("a missing file iterates as empty",
      list(jsonio.iter_jsonl(path("gone.jsonl"))), [])

# Appending rotates first when oversized, so one call never grows a file past
# roughly the cap plus one record.
big = path("cap.jsonl")
with open(big, "w", encoding="utf-8") as f:
    f.write("x" * 200 + "\n")
jsonio.append_jsonl(big, {"fresh": True}, max_bytes=100)
check("append rotates an oversized file first",
      os.path.exists(big + ".1"), True)
check("...and the fresh record starts the new file",
      [r for r in jsonio.iter_jsonl(big)], [{"fresh": True}])

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
