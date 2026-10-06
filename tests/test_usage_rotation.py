"""
test_usage_rotation.py - `benham.py usage` reads past a log rotation.

The board's find (2026-09-17): rpc._log_candidates globbed `benham*.log` and
stopped there, so the generations rotlog shifts out - `benham.log.1`, `.2` -
were invisible. On the Mac that meant `usage --all` saw only the live file, and
`usage --today` lost the morning whenever the 1 MB cap rolled the log mid-day.
rotlog rotates by size, not by day, so that is an ordinary day, not an edge.

    python test_usage_rotation.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - local, isolated; must precede benham imports

import os
import sys
import tempfile
import time

from benham import paths
from benham.core import rpc

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


TODAY = time.strftime("%Y-%m-%d", time.gmtime())


def line(day, n):
    return (f"[{day} 0{n}:00:00Z] agent usage [dm:1] in=10 out=5 "
            f"model=claude-haiku-4-5\n")


root = tempfile.mkdtemp(prefix="usage-rot-")
logs = os.path.join(root, "logs")
os.makedirs(logs)
# Oldest generation first, each written (and so stamped) before the next.
files = [("benham.log.2", ["2026-01-01", TODAY]),
         ("benham.log.1", [TODAY, TODAY]),
         ("benham.log", [TODAY])]
for name, days in files:
    with open(os.path.join(logs, name), "w", encoding="utf-8") as f:
        for i, d in enumerate(days, 1):
            f.write(line(d, i))
    time.sleep(0.05)

_root, _logdir, _rt = paths.ROOT, paths.LOG_DIR, rpc.RUNTIME.get("log_file")
paths.ROOT, paths.LOG_DIR, rpc.RUNTIME["log_file"] = root, logs, None
try:
    print("\nThe candidates include rotated generations")
    names = sorted(os.path.basename(p) for p in rpc._log_candidates())
    check("benham.log, .1 and .2 are all candidates", names,
          ["benham.log", "benham.log.1", "benham.log.2"])

    print("\nusage --all reads every generation")
    rep = rpc.usage_report(all=True)
    check("three sources", sorted(rep["sources"]),
          ["benham.log", "benham.log.1", "benham.log.2"])
    check("every agent call in all of them", rep["scan"]["agent_calls"], 5)

    print("\nusage --today reads the newest log AND its generations, oldest first")
    rep = rpc.usage_report(today=True)
    check("sources in order", rep["sources"],
          ["benham.log.2", "benham.log.1", "benham.log"])
    check("today's calls from all three, not just the live file",
          rep["scan"]["agent_calls"], 4)

    print("\nplain usage still means the newest log only")
    rep = rpc.usage_report()
    check("one source", rep["sources"], ["benham.log"])
    check("its one call", rep["scan"]["agent_calls"], 1)
finally:
    paths.ROOT, paths.LOG_DIR, rpc.RUNTIME["log_file"] = _root, _logdir, _rt

print()
if _fails:
    print(f"{len(_fails)} check(s) did not pass:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("all green")
