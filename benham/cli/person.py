"""
person.py - who Benham knows, from the shell (INTENT decision 61, Stage 9 A).

    python benham.py person list
    python benham.py person add <name | @mention | user id> [--note "why"]
    python benham.py person add ... --confirm-token TOK
    python benham.py person remove <name | @mention | user id> [--confirm-token TOK]

A thin face over three registry verbs - list_people, add_person, remove_person -
through the outbox, exactly as `do` reaches them; nothing here decides anything.
So the bot that answers is the one whose state/people.json is the live list (on
cazzy-mac since Phase B), and the PC never needs ssh to make someone known.

add and remove are tier 2, always confirmed: the first call prints the card the
bot built (who, their id, the servers they share, what being known means) and
a token, and changes nothing; the second, with --confirm-token, does it. A
preview exits NON-ZERO, the delete.py rule: a script that read 0 would believe
someone was added who was not. Being known takes effect on the person's next
message - no restart, no config edit.
"""

import sys

from benham import paths
from benham.cli.delete import run_two_step
from benham.core.outbox import (EXIT_FAIL, EXIT_OK, console_utf8, enqueue,
                                usage, wait_result)

USAGE = ("Usage: python benham.py person list\n"
         "       python benham.py person add <name|@mention|id> [--note \"why\"] "
         "[--confirm-token TOK] [--no-wait]\n"
         "       python benham.py person remove <name|@mention|id> "
         "[--confirm-token TOK] [--no-wait]")


def _take(argv, flag):
    """Pull `flag value` out of argv. Returns (value or None, rest, error)."""
    if flag not in argv:
        return None, argv, None
    i = argv.index(flag)
    if i + 1 >= len(argv):
        return None, argv, f"{flag} needs a value"
    return argv[i + 1], argv[:i] + argv[i + 2:], None


def _list(timeout=60):
    final = enqueue(face=paths.PROCESS_FACE, action="list_people")
    result, where = wait_result(final, timeout=timeout)
    if result is None:
        print(f"no result within {timeout}s. The request is still queued and will "
              f"run when the bot gets to it. (is the bot running? "
              f"`python benham.py status`)", file=sys.stderr)
        return EXIT_FAIL
    if where == "failed":
        print(f"REFUSED: {result.get('error', '(no error recorded)')}", file=sys.stderr)
        return EXIT_FAIL
    r = result.get("result") or {}
    rows = r.get("people") or []
    print(f"known people ({len(rows)}) - guest chat {str(r.get('guest_chat', '?')).upper()}")
    if not rows:
        print("  (nobody yet - `person add <name>` makes someone known)")
    for x in rows:
        shown = x.get("display_name") or x.get("name")
        at = x.get("name")
        who = f"{shown} (@{at})" if at and at != shown and at != str(x["user_id"]) else str(shown)
        where_from = x.get("source", "?")
        lists = [l for l in x.get("lists") or [] if l != "people.json"]
        if lists:
            where_from += f" [{', '.join(lists)}]"
        line = f"  {who:<32} {x['user_id']:<20} {where_from}"
        if x.get("since"):
            line += f"  since {x['since']} by {x.get('by') or '?'}"
        if x.get("note"):
            line += f" - {x['note']}"
        print(line)
    return EXIT_OK


def main(argv):
    console_utf8()
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        return usage(USAGE)
    cmd, rest = args[0], args[1:]

    if cmd == "list":
        if rest:
            return usage(USAGE)
        return _list()

    if cmd not in ("add", "remove"):
        return usage(f"unknown person command {cmd!r}\n{USAGE}")

    no_wait = "--no-wait" in rest
    rest = [a for a in rest if a != "--no-wait"]
    token, rest, err = _take(rest, "--confirm-token")
    if err:
        return usage(err)
    note, rest, err = _take(rest, "--note")
    if err:
        return usage(err)
    if note is not None and cmd == "remove":
        return usage("--note is for add only")
    if len(rest) != 1 or not rest[0].strip():
        return usage(f"person {cmd} takes exactly one name, @mention or id "
                     f"(quote a name with spaces)\n{USAGE}")
    query = rest[0].strip()

    params = {"query": query}
    if note:
        params["note"] = note
    action = "add_person" if cmd == "add" else "remove_person"
    note_flag = f" --note \"{note}\"" if note else ""
    return run_two_step(
        action,
        describe=(f"make {query!r} a known person" if cmd == "add"
                  else f"stop knowing {query!r}"),
        rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} person {cmd} "
                         f"\"{query}\"{note_flag} --confirm-token {t}"),
        token=token, no_wait=no_wait,
        nothing=("NOBODY WAS ADDED YET." if cmd == "add" else "NOBODY WAS REMOVED YET."),
        **params)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
