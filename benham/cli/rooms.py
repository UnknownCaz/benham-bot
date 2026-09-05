"""rooms - the listing: names + unread counts, nothing else.

This is the line a session runs at startup to know whether anything is waiting
anywhere (c12: "names + unread counts, should be good as that gives the session
context"). It reads two small json files and touches no message content, which
is why it is safe to run reflexively and why it sits on the read-only
allowlist. Reading a room's actual contents is `benham.py room read <name>` -
a separate, deliberate step, because content is other writers' text.

    python benham.py rooms
    python benham.py rooms --as my-session-name
"""

import argparse

from benham.core import remote

store = remote.stores.rooms   # Phase B: the bot's rooms, wherever it runs


def main(argv):
    ap = argparse.ArgumentParser(
        prog="benham.py rooms",
        description="List rooms: names + unread counts for this reader.")
    ap.add_argument("--as", dest="reader", default="cli",
                    help="Reader identity for unread counts (default: cli). "
                         "Sessions sharing a reader share a cursor.")
    args = ap.parse_args(argv)

    lst = store.listing(args.reader)
    if not lst:
        print("no rooms yet - `python benham.py room create <name> \"purpose\"` "
              "makes one (explicitly, always).")
        return 0
    width = max(len(r["name"]) for r in lst)
    for r in lst:
        marks = []
        if r["unread"]:
            marks.append(f"{r['unread']} unread")
        if r["has_worker"]:
            marks.append("has worker")
        print(f"  {r['name']:<{width}}  {', '.join(marks) or 'quiet'}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
