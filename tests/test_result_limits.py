"""
test_result_limits.py - a whole-file read reaches the model whole.

2026-10-12: Doom handed over a 17,763-char text file. read_attachments returned
it (it keeps up to capabilities.MAX_TEXT_CHARS = 20,000), then the agent loop
cut every tool result at a flat 6,000 chars, and Benham told Caz "the tool cut
the text off". The limit is now per tool name: read_attachments gets 24,000,
everything else keeps 6,000, so a 200-message channel dump is still bounded.

    python test_result_limits.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import sys

from benham.core import agent
from benham.core import capabilities

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def main():
    big = "x" * 17000 + "THE-END-MARKER" + "y" * 986   # 18,000 chars

    print("\nPer-tool result limits")
    check("read_attachments may carry a whole MAX_TEXT_CHARS file",
          agent._result_limit("read_attachments") >= capabilities.MAX_TEXT_CHARS, True)
    check("everything else keeps 6,000", agent._result_limit("read_channel"), 6000)

    att = agent._labelled("read_attachments", big)
    check("an 18k read_attachments result survives whole", big in att, True)
    check("...with no truncation note", "[truncated" in att, False)

    chan = agent._labelled("read_channel", big)
    check("an 18k read_channel result is still cut", "THE-END-MARKER" in chan, False)
    check("...at 6,000, and says so", "[truncated, 18000 chars total]" in chan, True)
    check("...keeping exactly the first 6,000 chars", ("x" * 6000 + "\n...") in chan, True)

    # The untainted path (_truncate called directly with the tool's limit).
    check("untainted path: other tools cut at 6,000",
          len(agent._truncate(big, agent._result_limit("server_status")).split("\n")[0]),
          6000)
    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
