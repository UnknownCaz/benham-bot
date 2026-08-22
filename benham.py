"""
benham.py - the one entry point for every owner command.

    python benham.py send <channel_id> "hello"
    python benham.py status
    python benham.py guest status
    python benham.py --help          # the full catalog

Until the source reorg (PLAN-src-reorg.md) each command was its own root
script - python send.py, python status.py, fifteen more - and knowing the
bot meant remembering fifteen filenames. Now the filenames live in
benham/cli/ and this file is the only thing at the root you can run besides
the bot itself (python -u -m benham.bot).

Mechanism: rewrite argv and run the command's module as __main__ via runpy.
That is deliberate - two of the commands (catchup, read_history) parse argv at
module level, and runpy executes every entry shape identically to the
standalone script it used to be, so nothing needed rewriting to move here.
SystemExit propagates untouched; the 0 / 1 / 2 exit-code convention
(ok / runtime failure / usage error) is unchanged from outbox.py.

Adding a command = add the module to benham/cli/ and one line to COMMANDS.
"""

import os
import runpy
import sys

# Subcommand -> (module, one-line purpose). This table IS the --help text;
# daily drivers first, then voice, then the read-only and admin tools.
COMMANDS = {
    "send":         ("benham.cli.send",         "enqueue a message for the running bot to deliver"),
    "dm":           ("benham.cli.dm",           "enqueue a DM for the running bot to deliver"),
    "draft":        ("benham.cli.draft",        "draft a reply for review BEFORE it goes to a friend"),
    "do":           ("benham.cli.do",           "run any registered capability from the shell"),
    "fetch":        ("benham.cli.fetch",        "pull recent messages from a channel via the bot"),
    "delete":       ("benham.cli.delete",       "ask the bot to delete ONE specific message by id"),
    "purge":        ("benham.cli.purge",        "bulk-delete messages older than N days"),
    "catchup":      ("benham.cli.catchup",      "invisible, read-only catch-up on one channel"),
    "read_history": ("benham.cli.read_history", "invisible, read-only reader across guilds"),
    "ask":          ("benham.cli.ask",          "ask Tyler something and wait for his answer"),
    "initiate":     ("benham.cli.initiate",     "Claude reaching out to Tyler FIRST - the daily job's surface"),
    "outreach":     ("benham.cli.outreach",     "ask a whitelisted COLLABORATOR something (never Tyler)"),
    "conv":         ("benham.cli.conv",         "read, close or bank conversations"),
    "rooms":        ("benham.cli.rooms",        "list rooms: names + unread counts, nothing else"),
    "room":         ("benham.cli.room",         "read / post into / create one room"),
    "status":       ("benham.cli.status",       "quick, read-only health check"),
    "usage":        ("benham.cli.usage",        "token and cost accounting from the logs"),
    "watch_pc":     ("benham.cli.watch_pc",     "watch, live, what Benham is doing on the PC"),
    "webhook":      ("benham.cli.webhook",      "post via a saved webhook / manage webhooks"),
    "guest":        ("benham.guest.guest",      "guest chat admin: status | forget <user_id> | forget-all"),
    "ideas":        ("benham.cli.ideas",        "guest ideas inbox: new since sweep | --all | --sweep"),
    "issues":       ("benham.cli.issues",       "intake tracker: list | loop (close the loop) | retry"),
}


def _help(stream=sys.stdout):
    print("usage: python benham.py <command> [args...]\n", file=stream)
    width = max(len(c) for c in COMMANDS)
    for cmd, (_, blurb) in COMMANDS.items():
        print(f"  {cmd:<{width}}  {blurb}", file=stream)
    print("\n  <command> --help usually prints that command's own usage.", file=stream)
    print("  The bot process itself is separate:  python -u -m benham.bot", file=stream)


def main(argv):
    # Same defensive UTF-8 reconfigure every CLI used to carry for itself:
    # these commands echo Discord content, which routinely contains emoji,
    # and a redirected Windows stream defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if not argv or argv[0] in ("-h", "--help", "help"):
        _help()
        return 0

    # --face <name>: which bot identity this command acts as, REQUIRED on
    # every call (Tyler's decision, PLAN-second-face commit 10 - the safer,
    # more annoying option, bought because a session that means one face must
    # never silently get another). Accepted before or after the subcommand;
    # stripped here so no cli module needs its own copy of the parsing. The
    # BENHAM_FACE environment variable also satisfies it - that is how the
    # supervisor launches face processes - with the flag winning when both are
    # present, because explicit beats ambient.
    #
    # The mechanism is one line: the validated name goes into BENHAM_FACE
    # BEFORE any benham module is imported, and paths.PROCESS_FACE - which
    # every per-face store and every enqueue already resolves through - reads
    # it at import. One parse point, one variable, no threading.
    argv = list(argv)
    face = None
    if "--face" in argv:
        i = argv.index("--face")
        if i + 1 >= len(argv):
            print("--face needs a face name after it (e.g. --face benham)",
                  file=sys.stderr)
            return 2
        face = argv[i + 1]
        del argv[i:i + 2]
    if face is None:
        face = os.environ.get("BENHAM_FACE", "").strip() or None
    if face is None:
        print("every benham.py command names which face it acts as: add "
              "`--face benham` for the usual bot (or set BENHAM_FACE). With "
              "two faces, which identity speaks is said, never guessed.",
              file=sys.stderr)
        return 2
    os.environ["BENHAM_FACE"] = face
    try:
        import benham.paths  # noqa: F401 - validates BENHAM_FACE at import
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if not argv:
        _help()
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd!r}\n", file=sys.stderr)
        _help(stream=sys.stderr)
        return 2
    module, _ = COMMANDS[cmd]
    sys.argv = [f"benham.py {cmd}"] + argv[1:]
    runpy.run_module(module, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
