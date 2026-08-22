"""
do.py - run any registered capability from the shell.

    python benham.py do list
    python benham.py do list --tier destructive
    python benham.py do help send_embed
    python benham.py do send_message channel_id=809357286036078612 content="hey"
    python benham.py do purge_messages channel_id=... limit=50
    python benham.py do purge_messages channel_id=... limit=50 confirm_token=a1b2c3

One CLI for the whole registry, rather than a file per action. The old pattern -
send.py, dm.py, delete.py, each with its own argument parsing - meant a new
capability was three edits and usually got only two, which is how `purge` went so
long without any CLI at all.

Destructive actions are two calls. The first returns a dry-run with real counts and
a token; the second passes `confirm_token=` to fire exactly what was previewed. The
token is bound to the parameters captured at preview time, so editing the arguments
between the two calls invalidates it rather than silently running something else.
"""

import json
import os
import sys

from benham import paths
from benham.core import capabilities  # noqa: F401 — imported for its registration side effects
from benham.core import identity
from benham.core import outbox


def _print_list(argv):
    tier_filter = None
    if "--tier" in argv:
        want = argv[argv.index("--tier") + 1].lower()
        tier_filter = {v: k for k, v in identity.TIER_NAMES.items()}.get(want)
        if tier_filter is None:
            return outbox.usage(f"unknown tier {want!r}; use "
                                f"{' | '.join(identity.TIER_NAMES.values())}")
    current = None
    for a in capabilities.catalog(min_tier=tier_filter, max_tier=tier_filter):
        if a["tier_name"] != current:
            current = a["tier_name"]
            marker = "  (no undo — dry-run + confirm required)" if a["tier"] == 3 else ""
            print(f"\n== {current.upper()}{marker} ==")
        req = [k for k, s in a["params"].items() if s.get("required")]
        print(f"  {a['name']:<20} {a['summary']}")
        if req:
            print(f"  {'':<20} required: {', '.join(req)}")
    print()
    return outbox.EXIT_OK


def _print_help(name):
    act = capabilities.REGISTRY.get(name)
    if act is None:
        return outbox.usage(f"unknown action {name!r} — try `python benham.py do list`")
    print(f"\n{act.name}  [{identity.TIER_NAMES[act.tier]}]")
    print(f"  {act.summary}\n")
    if act.destructive:
        print("  DESTRUCTIVE: first call previews and returns a confirm_token;")
        print("  pass confirm_token=<token> on a second call to actually run it.")
        print(f"  Only permitted in guilds: {sorted(identity.DESTRUCTIVE_GUILDS) or 'NONE'}\n")
    if not act.params:
        print("  (no parameters)\n")
        return outbox.EXIT_OK
    for pname, spec in act.params.items():
        flag = "required" if spec.get("required") else "optional"
        print(f"  {pname:<22} {spec.get('type', 'str'):<6} {flag:<9} {spec.get('desc', '')}")
    print()
    return outbox.EXIT_OK


def _parse_kv(pairs):
    """Parse key=value arguments. JSON values are decoded so lists/objects work."""
    out = {}
    for raw in pairs:
        if "=" not in raw:
            return None, f"expected key=value, got {raw!r}"
        k, v = raw.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except ValueError:
            out[k.strip()] = v          # a bare string is the common case
    return out, None


def main(argv):
    outbox.console_utf8()
    if not argv or argv[0] in ("-h", "--help", "help") and len(argv) == 1:
        return outbox.usage(__doc__.strip())
    if argv[0] == "list":
        return _print_list(argv)
    if argv[0] == "help":
        return _print_help(argv[1]) if len(argv) > 1 else outbox.usage("help <action>")

    name = argv[0]
    if name not in capabilities.REGISTRY:
        return outbox.usage(f"unknown action {name!r} — try `python benham.py do list`")

    params, err = _parse_kv(argv[1:])
    if err:
        return outbox.usage(err)

    # Validate locally so a typo fails here instead of 2 seconds later in the bot.
    try:
        capabilities.validate(capabilities.REGISTRY[name],
                              {k: v for k, v in params.items() if k != "confirm_token"})
    except capabilities.ActionError as e:
        return outbox.usage(str(e))

    path = outbox.enqueue(face=paths.PROCESS_FACE, action=name, **params)
    print(f"queued {name} -> {os.path.basename(path)}")

    # A PC task drives a whole Claude Code session and can pause indefinitely on a
    # permission DM, so the 60s that suits a Discord call would report a healthy
    # bot as dead. The request itself is unaffected either way - it stays queued
    # and the bot still runs it - but the message printed here would be a lie.
    timeout = 1800 if name == "pc_task" else 60
    result, where = outbox.wait_result(path, timeout=timeout)
    if result is None:
        print(f"no result within {timeout}s. The request is still queued and will "
              f"run when the bot gets to it; check outbox/sent/. "
              f"(is bot.py running? check with status.py)", file=sys.stderr)
        return outbox.EXIT_FAIL

    status = result.get("status")
    if status == "confirmation_required":
        preview = result.get("preview", {})
        print("\n--- DRY RUN, nothing has happened ---")
        print(preview.get("summary", "(no summary)"))
        if preview.get("detail"):
            print("\n" + preview["detail"])
        print(f"\nTo run it for real, repeat the command with:"
              f"\n  confirm_token={result['confirm_token']}"
              f"\n(expires in {result.get('expires_in_seconds', '?')}s)\n")
        return outbox.EXIT_OK

    print(json.dumps(result.get("result", result), indent=2, default=str))
    return outbox.EXIT_OK if where == "sent" else outbox.EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
