"""
run_tests.py - the whole suite, one command, one exit code.

The tests are standalone scripts on purpose (pytest is not installed, and each
file documents the defect class it guards). The cost of that choice was that
"run the tests" meant a shell loop everyone retyped - and on the PC path each
retyped loop is an approval prompt Tyler has to answer from his phone. A July
coverage branch (claude/test-coverage-analysis-gq6320) proposed exactly this
runner before it went stale; this is that one surviving idea, written fresh.

Each test file prints PASS/FAIL lines and exits nonzero on failure. Both are
checked: an exit code catches a crash before the first check, and the FAIL scan
catches a file whose main() forgets to propagate its failures - a loose runner
reads exactly like a passing one, which is how the ask-queue delivery bugs hid.

    python run_tests.py            # everything
    python run_tests.py policy     # only tests/test_*policy*.py
"""

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    want = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    paths = sorted(glob.glob(os.path.join(HERE, "tests", "test_*.py")))
    if want:
        paths = [p for p in paths if want in os.path.basename(p).lower()]
    if not paths:
        print(f"no test files match {want!r}")
        return 2

    failed = []
    for path in paths:
        name = os.path.basename(path)
        proc = subprocess.run([sys.executable, path], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              cwd=HERE)
        out = (proc.stdout or "") + (proc.stderr or "")
        bad = proc.returncode != 0 or "FAIL" in out
        print(f"{'FAIL' if bad else 'pass'}  {name}")
        if bad:
            failed.append(name)
            # Show the file's own account of what went wrong, not the whole run.
            tail = [l for l in out.splitlines() if "FAIL" in l or "Error" in l]
            for line in (tail or out.splitlines())[-12:]:
                print(f"      {line}")

    print(f"\n{len(paths) - len(failed)}/{len(paths)} files green"
          + (f" - FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
