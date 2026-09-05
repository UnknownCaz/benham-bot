"""issues.py CLI - the intake tracker from the shell.

    python benham.py issues                  # what is in the tracker, by state
    python benham.py issues loop --dry-run   # who WOULD be told what, sends nothing
    python benham.py issues loop             # close the loops: DM each reporter
    python benham.py issues retry            # push any unsent filings to GitHub

`loop` is the close-the-loop lane (decision #12's fourth side). It reads the
tracker, finds guest filings Tyler has closed or declined, DMs the reporter
once, and shuts the OWED conversation the filing opened. Truth lives in GitHub:
flipping a label or closing an issue IS the decision, so there is no separate
"remember to tell them" step. --dry-run is the same read with no side effects.

`retry` drains the unsent queue - filings that were accepted from a guest while
GitHub was unreachable. Nothing is lost while it waits; this just delivers it.
"""

import sys

from benham.core import outbox, remote

# Phase B: the tracker and the loop-close lane run where the bot runs.
issues = remote.stores.issues
loopclose = remote.stores.loopclose


def _list():
    entries = issues.entries()
    if not entries:
        print("(no filings yet)")
        return 0
    waiting = issues.unsent()
    for e in entries:
        if e.get("unsent"):
            state = "UNSENT"
        elif e.get("told"):
            state = "told:" + str(e.get("told"))
        else:
            state = "open"
        who = e.get("author") or "?"
        print(f"[{str(e.get('ts',''))[:16].replace('T',' ')}] "
              f"{state:<14} {e.get('category','?'):<8} {who}")
        print(f"    {e.get('title','')}")
        if e.get("url"):
            print(f"    {e['url']}")
    print(f"\n{len(entries)} filing(s), {len(waiting)} waiting to reach GitHub")
    return 0


def _loop(dry):
    items = loopclose.run(dry_run=dry)
    if not items:
        print("no loops to close - nothing in the tracker has news the "
              "reporter has not been told")
        return 0
    for it in items:
        e = it["entry"]
        head = "WOULD SEND" if dry else "SENT"
        print(f"{head}  #{it['number']}  {it['outcome'].upper()}  "
              f"-> {e.get('author')} ({e.get('author_id')})")
        for line in it["message"].splitlines():
            print(f"    {line}")
        print()
    if dry:
        print(f"{len(items)} loop(s) would close. Run without --dry-run to send.")
    else:
        print(f"{len(items)} loop(s) closed - the bot delivers the DMs.")
    return 0


def _retry():
    sent, failed, urls = issues.retry_unsent()
    for u in urls:
        print("filed:", u)
    print(f"{sent} sent, {failed} still waiting")
    return 0 if not failed else 1


def _main(argv):
    outbox.console_utf8()
    cmd = argv[0] if argv else ""
    if cmd in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if cmd == "loop":
        return _loop("--dry-run" in argv or "-n" in argv)
    if cmd == "retry":
        return _retry()
    if cmd in ("", "list"):
        return _list()
    print(f"unknown subcommand {cmd!r} - try --help", file=sys.stderr)
    return 2


def main():
    return _main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
