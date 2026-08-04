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
    "speak":        ("benham.cli.speak",        "join a voice channel and say something (edge-tts)"),
    "listen":       ("benham.cli.listen",       "join a voice channel and start transcribing"),
    "stoplisten":   ("benham.cli.stoplisten",   "stop listening and leave the voice channel"),
    "catchup":      ("benham.cli.catchup",      "invisible, read-only catch-up on one channel"),
    "read_history": ("benham.cli.read_history", "invisible, read-only reader across guilds"),
    "status":       ("benham.cli.status",       "quick, read-only health check"),
    "usage":        ("benham.cli.usage",        "token and cost accounting from the logs"),
    "watch_pc":     ("benham.cli.watch_pc",     "watch, live, what Benham is doing on the PC"),
    "webhook":      ("benham.cli.webhook",      "post via a saved webhook / manage webhooks"),
    "guest":        ("benham.guest.guest",      "guest chat admin: status | forget <user_id> | forget-all"),
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
