"""
test_rotlog.py - the self-rotating writer: rotates at the cap, keeps its
generations in order, never raises, and stays gated OFF.

Under launchd (migration Phase 4) the bot owns its own log rotation - there
is no supervisor script to ride and newsyslog cannot reopen a descriptor it
does not hold. This file pins:

  1. writes land in the file, appended,
  2. crossing CAP rotates: <file> -> <file>.1 -> <file>.2, newest first,
  3. the writer swallows failures instead of raising into the caller's loop
     (log()'s doctrine),
  4. install() replaces BOTH stdout and stderr, and bot.py gates it on
     BENHAM_LOG_FILE (source-pinned).

    python test_rotlog.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import sys
import tempfile

from benham.core import rotlog

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


tmp = tempfile.mkdtemp(prefix="rotlog-test-")
path = os.path.join(tmp, "bot.log")

# --- 1. plain writes append -------------------------------------------------

w = rotlog._RotatingWriter(path)
w.write("first line\n")
w.write("second line\n")
with open(path, encoding="utf-8") as f:
    check("writes land in the file, in order", f.read(), "first line\nsecond line\n")

# --- 2. crossing the cap rotates, generations shift newest-first ------------

_real_cap = rotlog.CAP
try:
    rotlog.CAP = 200
    w.write("A" * 250 + "\n")          # crosses -> rotation 1: file -> .1
    w.write("fresh after first rotation\n")
    w.write("B" * 250 + "\n")          # crosses -> rotation 2: .1 -> .2, file -> .1
    with open(path + ".2", encoding="utf-8") as f:
        gen2 = f.read()
    with open(path + ".1", encoding="utf-8") as f:
        gen1 = f.read()
    check("oldest generation (.2) holds the first overflow", "A" * 250 in gen2, True)
    check("newer generation (.1) holds the second overflow", "B" * 250 in gen1, True)
    check("the live file is fresh after rotation",
          os.path.getsize(path) < 200, True)
    w.write("still writing after two rotations\n")
    with open(path, encoding="utf-8") as f:
        check("writes continue into the reopened file",
              "still writing after two rotations" in f.read(), True)
finally:
    rotlog.CAP = _real_cap

# --- 3. failure is swallowed, never raised ----------------------------------

w._f.close()                            # sabotage: closed underlying file
try:
    w.write("write into a closed file\n")
    check("a broken writer never raises", True, True)
except Exception as e:  # noqa: BLE001
    check(f"a broken writer never raises (got {type(e).__name__})", False, True)

# --- 4. install() swaps both streams; bot.py gates on the env var -----------

_out, _err = sys.stdout, sys.stderr
try:
    w2 = rotlog.install(os.path.join(tmp, "swap.log"))
    swapped = sys.stdout is w2 and sys.stderr is w2
finally:
    sys.stdout, sys.stderr = _out, _err
check("install() points stdout and stderr at the writer", swapped, True)

_bot_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "benham", "bot.py"), encoding="utf-8").read()
check("bot.py gates on BENHAM_LOG_FILE", "BENHAM_LOG_FILE" in _bot_src, True)
check("bot.py calls rotlog.install", "rotlog.install(" in _bot_src, True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
