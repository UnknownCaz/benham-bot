"""The agent prompt names the servers where destructive tools may run, and tells
the model the list is the gate's to apply, never its own.

2026-10-31 08:40 local: told only the ids, the model could not tell that "the
friends server" was the second allowlisted guild, decided it was off the list,
refused a delete the gate would have allowed, and explained it with "a hard block
in the code" that no tool had reported. Two things changed: the prompt line now
carries each server's name beside its id (from the channels.json the bot writes
at login), and the rule says to look (`list_guilds`) or make the call and let the
gate answer - never to refuse on its own reading, never to describe a block it
did not get back from a tool.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import json
import os
import sys
import tempfile

from benham import paths
from benham.core import agent
from benham.core import identity

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def main():
    section("1. _guild_names reads the channels.json the bot writes at login")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "channels.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"guild": "Testing Server", "guild_id": 736988645562646619,
                        "text_channels": [], "voice_channels": []},
                       {"guild": "Fuckass Friends", "guild_id": "1491485711076167711"},
                       {"guild": "no id here"},
                       {"guild_id": 5, "guild": None},
                       "not a dict"], f)
        names = agent._guild_names(path)
        check("ids map to the names Discord shows",
              names.get(736988645562646619), "Testing Server")
        check("a string id is read as a number", names.get(1491485711076167711), "Fuckass Friends")
        check("an entry with no id is skipped, not fatal", len(names), 3)
        check("a missing file means no names, not an error",
              agent._guild_names(os.path.join(d, "nope.json")), {})

    section("2. the prompt's hard rule names the servers beside their ids")
    allowed = sorted(identity.face_gates(paths.PROCESS_FACE)["destructive_guilds"])
    check("the test control plane allows tier 3 somewhere (else this test proves nothing)",
          bool(allowed), True)
    first = allowed[0] if allowed else 0
    real = agent._guild_names
    try:
        agent._guild_names = lambda path=None: {first: "Testing Server"}
        static, _vol = agent._system_blocks("a DM", "caz6666")
        check("the allowlisted server shows as name (id)",
              f"Testing Server ({first})" in static, True)
        check("the line says servers, not bare guild ids",
              "work only in these servers:" in static, True)
        check("a guild with no name still prints its id bare",
              all(str(g) in static for g in allowed), True)

        agent._guild_names = lambda path=None: {}
        static_bare, _vol = agent._system_blocks("a DM", "caz6666")
        check("with no names on disk the ids print bare, as before",
              f"servers: {first}" in static_bare or f", {first}" in static_bare, True)
    finally:
        agent._guild_names = real

    section("3. the list is the gate's to apply, not the model's")
    static, _vol = agent._system_blocks("a DM", "caz6666")
    for piece in ("That list is the\n  gate's to apply, not yours",
                  "call `list_guilds` and look",
                  "let the gate answer",
                  "Never refuse on your own reading of this list",
                  "never describe a block you\n  did not get back from a tool"):
        check(f"the rule says: {piece.replace(chr(10), ' ').replace('  ', ' ')!r}",
              piece in static, True)
    check("the 10-31 refusal is cited as the reason", "2026-10-31" in static, True)

    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} FAILED'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
