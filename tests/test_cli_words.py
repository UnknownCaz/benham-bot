"""
test_cli_words.py - every benham.py verb prints the words it printed before Phase B.

THE WORDS ARE THE CONTRACT (INTENT decision 38). RAVEN.md, discord-proxy,
discord-outreach, initiate.bat, watch-exports.ps1 and the CLAUDE.md ask-queue
line all run `benham.py` unattended and read what it prints - and Raven's
permission allowlist is literal. Phase B rewrote the BODY of every verb (the
PC's CLI became a client of the bot on cazzy-mac); this file is what says
the surface did not move.

tests/fixtures/cli-words/ was captured against the UNTOUCHED pre-Phase-B tree
(commit 2f4b0c1) by tests/cliwords.py, which also runs the comparison here:
a scratch clone, seeded stores, a fake bot answering the outbox, one real
subprocess per case, normalised output. Two modes, both asserted:

  LOCAL   no config/remote.json - the clone is the bot's tree, exactly the
          pre-Phase-B shape. Proves the verbs still work with no bot host.
  REMOTE  a second clone plays the Mac: server.py stands up on a loopback
          port over it (token file, fake client, a real asyncio loop for the
          store calls) and the client clone carries BENHAM_REMOTE_URL to it.
          Proves the PC path: words identical THROUGH the wire.

A fixture that differs is a verb whose words changed. That is sometimes the
right thing - a traceback is not a contract - and then the fixture is re-pinned
DELIBERATELY: `python tests/cliwords.py --capture --only <name>` and the commit
says why. It is never done to make this file green.

    python tests/test_cli_words.py            # both modes, every case (~4 min)
    python tests/test_cli_words.py --local    # or --remote, one mode
    python tests/test_cli_words.py --only ask # substring filter on case names
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import sys

import cliwords

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        _fails.append(label)
    return ok


def _diff(name, got, want):
    for field in ("exit", "stdout", "stderr"):
        if got[field] != want[field]:
            print(f"      {field} differs")
            if field != "exit":
                g, w = got[field].splitlines(), want[field].splitlines()
                for i in range(max(len(g), len(w))):
                    gl = g[i] if i < len(g) else "<none>"
                    wl = w[i] if i < len(w) else "<none>"
                    if gl != wl:
                        print(f"        want: {wl[:150]}")
                        print(f"        got : {gl[:150]}")
                        break
            else:
                print(f"        want {want['exit']}, got {got['exit']}")


def main(argv):
    only = argv[argv.index("--only") + 1] if "--only" in argv else ""
    modes = []
    if "--remote" not in argv:
        modes.append(("local", False))
    if "--local" not in argv:
        modes.append(("remote", True))
    names = [n for n in cliwords.CASES if only in n]
    missing = [n for n in names if not os.path.exists(cliwords.fixture_path(n))]
    check("every case has a fixture (capture new ones deliberately)", missing, [])
    names = [n for n in names if n not in missing]

    for mode, remote in modes:
        print(f"\n== {mode}: {len(names)} case(s) ==")

        def compare(name, got, mode=mode):
            want = cliwords.load_fixture(name, mode)
            same = (got["exit"] == want["exit"] and got["stdout"] == want["stdout"]
                    and got["stderr"] == want["stderr"])
            if not check(f"[{mode}] {name}", same, True):
                _diff(name, got, want)

        cliwords.run_all(names, remote=remote, progress=compare)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
