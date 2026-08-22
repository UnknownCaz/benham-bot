"""
repair_memory.py - clean the f06b79b echo damage out of state/agent_memory.json.

Between 2026-08-04 (f06b79b) and 2026-08-16 (aacf21e), respond() stored Benham's
own reply in the user slot. Fixing the code stops new damage; it does not undo
what is already on disk, and Benham reasons from that file every turn - so until
this runs, it still believes Tyler said those things.

Removes the whole corrupted PAIR, not just the fake user turn. Dropping the user
turn alone would leave the history non-alternating, which the API rejects; and
keeping a pair whose user half is fabricated is the actual harm, since that half
is what gets read back as something Tyler said.

Test-only conversations (test:*, repro, tokcmp:*) are dropped entirely - they are
regenerable harness residue, not history.

RUN WITH THE BOT STOPPED. The live process holds this file in a module-level
cache (agent._memory) and its next _remember() would write that cache back over
any repair made underneath it.

    python scripts/repair_memory.py --dry-run     # show what would change
    python scripts/repair_memory.py               # write, after a .bak
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham import paths  # noqa: E402
from benham.core import agent  # noqa: E402

# --face names whose memory to repair; agent_memory.json is per-face since
# PLAN-second-face commit 6, and a repair tool that quietly covered only the
# primary would leave every other face damaged while looking finished - the
# exact failure this script exists because of. The default is the primary,
# which is also every pre-faces layout.
def _memory_file(argv):
    face = paths.DEFAULT_FACE
    if "--face" in argv:
        try:
            face = argv[argv.index("--face") + 1]
        except IndexError:
            raise SystemExit("--face needs a face name after it")
    return os.path.join(paths.state_for(face), "agent_memory.json")


MEMORY_FILE = _memory_file(sys.argv)
DISPOSABLE = ("test:", "repro", "tokcmp:")


def main():
    dry = "--dry-run" in sys.argv
    if not os.path.exists(MEMORY_FILE):
        print(f"nothing to do - {MEMORY_FILE} does not exist")
        return 0

    with open(MEMORY_FILE, encoding="utf-8") as fh:
        mem = json.load(fh)

    out = {}
    dropped_keys, dropped_pairs, kept_pairs = [], 0, 0

    for key, turns in mem.items():
        if key.startswith(DISPOSABLE):
            dropped_keys.append(f"{key} ({len(turns)} turns)")
            continue
        clean = []
        for i in range(0, len(turns) - 1, 2):
            u, a = turns[i], turns[i + 1]
            # agent.is_echo_pair, not a local copy: the first pass of this script
            # tested equality only and left the multi-round variant (where the
            # user turn is the TAIL of the assembled reply) sitting in the live
            # thread, looking repaired.
            if agent.is_echo_pair(u, a):
                dropped_pairs += 1
                continue
            clean += [u, a]
            kept_pairs += 1
        if len(turns) % 2:
            # An odd tail cannot be half of a pair; keeping it would end the
            # history on a user turn and 400 the next call.
            print(f"  note: {key} had an odd trailing turn - dropped")
        out[key] = clean

    print(f"conversations kept:  {len(out)}")
    print(f"harness keys purged: {len(dropped_keys)}")
    for k in dropped_keys:
        print(f"    - {k}")
    print(f"echo pairs removed:  {dropped_pairs}")
    print(f"real pairs kept:     {kept_pairs}")

    if dry:
        print("\n--dry-run: nothing written")
        return 0

    backup = MEMORY_FILE + ".pre-repair.bak"
    shutil.copy2(MEMORY_FILE, backup)
    with open(MEMORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"\nwritten. backup at {backup}")
    print("restart the bot so it loads the repaired file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
