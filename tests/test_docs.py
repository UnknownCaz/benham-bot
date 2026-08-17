"""
test_docs.py - the written record has to agree with the running code.

Both generators shipped with a --check mode and neither was ever called by
anything, which is the same defect they exist to fix: a check nobody runs is a
claim nobody verifies. This is the thing that runs them.

Why it earns a slot in the suite:

  THE MANUAL WENT STALE WITHOUT A SINGLE FAILING TEST. Three pages said 57
  capabilities against a live 56, and eight of twenty-two pages documented
  voice, guest workspaces and code execution months after they were archived.
  Every test passed the whole time. Documentation is the one artifact where
  being wrong has no symptom.

  A STALE DOC IS WORSE THAN NO DOC, because it is believed. This session watched
  a false security claim in a comment get read, trusted, and copied verbatim
  into a NEW comment by someone who had just been told it was false. Generated
  blocks cannot do that; prose can.

  LOSING THE MARKERS IS THE SILENT FAILURE. A page that drops its
  <!-- GENERATED --> comments simply stops being checked and never shows a diff,
  so gen_manual returns non-zero when a block has no home left. That case is
  worth more than the drift case: drift is loud once you look, an unwatched
  page is quiet forever.

The checks are deliberately shallow - exit codes, not content. Whether the
numbers are RIGHT is the generator's problem; whether anyone asked is this
file's problem.

    python test_docs.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import subprocess
import sys

from benham import paths

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        _fails.append(label)


def run(script, *args):
    """Run a generator in --check mode and hand back (rc, combined output)."""
    p = subprocess.run(
        [sys.executable, _os.path.join(paths.ROOT, "scripts", script), *args],
        capture_output=True, text=True, cwd=paths.ROOT)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    print("== generated docs match the live code ==")

    rc, out = run("gen_manual.py", "--check")
    check("docs/ is current", rc, 0)
    if rc != 0:
        print("        " + out.strip().replace("\n", "\n        "))

    rc, out = run("gen_readme.py", "--check")
    check("README.md is current", rc, 0)
    if rc != 0:
        print("        " + out.strip().replace("\n", "\n        "))

    print()
    print("== --check does not write ==")
    # A --check that repaired the file would report success forever and hide
    # every future drift - the failure mode is invisible, so it gets a test.
    import hashlib
    def digest():
        h = hashlib.sha256()
        for root, _d, files in _os.walk(_os.path.join(paths.ROOT, "docs")):
            for fn in sorted(files):
                if fn.endswith(".html"):
                    h.update(open(_os.path.join(root, fn), "rb").read())
        return h.hexdigest()

    before = digest()
    run("gen_manual.py", "--check")
    check("docs/ is byte-identical after --check", digest(), before)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        print("Run: python scripts/gen_manual.py   (and/or gen_readme.py)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
