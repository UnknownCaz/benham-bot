"""
test_registry.py - the registry is a security surface, so pin its whole shape.

Every other test reaches into REGISTRY for the two or three capabilities it
cares about. Nothing looks at the set as a whole, which leaves two ways to
change security posture with a green suite:

  A CAPABILITY QUIETLY VANISHES. Registration happens as an import side effect
  of the @action decorator. Split capabilities.py into modules and forget to
  import one, and its capabilities are simply absent - no error, no failure,
  the bot just cannot do those things any more. Nothing in the suite would
  notice unless it happened to name one of the missing entries.

  A FLAG QUIETLY FLIPS. taints, blocked_when_tainted, guest and origins are the
  four fields the injection defences are built out of. A one-word edit to any
  of them is invisible in review and invisible in test output.

So this file stores a FINGERPRINT of the security-relevant fields of every
capability, and fails on any difference. It is deliberately annoying: adding a
capability requires updating the fingerprint, which is the point - the diff in
that update IS the security review, stated in one place a human can read.

  python test_registry.py --update    # rewrite the fingerprint after a review

The handler, summary and params are NOT fingerprinted. Those change constantly
and carry no security meaning; including them would make the file churn until
someone started updating it without reading it.

    python test_registry.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import json
import sys

from benham.core import capabilities

FINGERPRINT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                            "registry_fingerprint.json")

# The fields that decide what an action may do and who may reach it. If you add
# a field to Action that gates behaviour, add it here too.
FIELDS = ("tier", "outward", "posts", "destructive", "needs_confirm",
          "always_confirm", "needs_guild", "guest", "taints",
          "blocked_when_tainted")

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        _fails.append(label)


def snapshot():
    out = {}
    for name, act in sorted(capabilities.REGISTRY.items()):
        row = {f: getattr(act, f) for f in FIELDS}
        row["origins"] = sorted(getattr(act, "origins", ()) or ())
        out[name] = row
    return out


def main():
    live = snapshot()

    if "--update" in sys.argv:
        with open(FINGERPRINT, "w", encoding="utf-8") as fh:
            json.dump(live, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"wrote {len(live)} capabilities to registry_fingerprint.json")
        print("READ THE DIFF before committing - that diff is the security review.")
        return 0

    if not _os.path.exists(FINGERPRINT):
        print("  FAIL  no fingerprint on disk. Run: python test_registry.py --update")
        return 1

    with open(FINGERPRINT, encoding="utf-8") as fh:
        want = json.load(fh)

    print("== the registry is exactly what was last reviewed ==")

    added = sorted(set(live) - set(want))
    removed = sorted(set(want) - set(live))
    check("no capability appeared unreviewed", added, [])
    check("no capability disappeared", removed, [])

    changed = []
    for name in sorted(set(live) & set(want)):
        for field, value in sorted(live[name].items()):
            if want[name].get(field) != value:
                changed.append(f"{name}.{field}: "
                               f"{want[name].get(field)!r} -> {value!r}")
    check("no gate changed", changed, [])

    print()
    print("== the invariants the fingerprint cannot express ==")

    # A guest-reachable action that can act outward is the escalation this
    # codebase is shaped to prevent. capabilities.py enforces it at import; if
    # that guard is ever loosened, this states the consequence out loud.
    bad = sorted(n for n, a in capabilities.REGISTRY.items()
                 if a.guest and (a.outward or a.posts or a.destructive
                                 or a.needs_confirm or a.blocked_when_tainted))
    check("no guest capability is outward/posting/destructive", bad, [])

    # Tainted input must not be able to reach the PC. This is the one wall that
    # keeps a stranger's message out of a shell.
    check("pc_task is blocked when tainted",
          capabilities.REGISTRY["pc_task"].blocked_when_tainted, True)

    # Reading anything a human wrote has to mark the turn, or the wall above
    # has nothing to act on.
    for name in ("read_channel", "search_messages", "what_i_did"):
        check(f"{name} taints", capabilities.REGISTRY[name].taints, True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        print("If the change is intended and reviewed:")
        print("  python tests/test_registry.py --update")
        return 1
    print(f"ALL PASS ({len(live)} capabilities)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
