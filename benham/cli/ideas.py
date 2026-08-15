"""ideas.py CLI - read (and sweep) the guest ideas inbox.

    python benham.py ideas            # ideas filed since the last sweep
    python benham.py ideas --all      # the whole inbox
    python benham.py ideas --sweep    # show the new ones AND mark them seen

Read-only except for the sweep cursor; the inbox itself is only ever appended
to by the bot. The sweep is for Claude sessions: show what's new, triage the
good ones onto project Corkboard boards as reported speech, mark them seen.
"""

import sys

from benham.core import ideas


def _print(entries):
    if not entries:
        print("(nothing here)")
        return
    for e in entries:
        ts = str(e.get("ts", ""))[:16].replace("T", " ")
        print(f"[{ts}] {e.get('author', '?')} ({e.get('author_id', '?')}):")
        print(f"    {e.get('text', '')}")


def _main(argv):
    flag = argv[0] if argv else ""
    if flag in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if flag == "--all":
        entries, total = ideas.new_since_sweep()
        _print(ideas._entries())
        print(f"\n{total} total, {len(entries)} unswept")
        return 0
    new, total = ideas.new_since_sweep()
    _print(new)
    if flag == "--sweep":
        ideas.mark_swept(total)
        print(f"\nswept - {len(new)} idea(s) marked seen ({total} total on file)")
    elif new:
        print(f"\n{len(new)} new since last sweep ({total} total) - "
              "--sweep to mark seen once triaged")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
