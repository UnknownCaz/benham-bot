"""
test_codesession_gate.py - the spawned-session command policy: deny is greedy,
allow is strict, everything else asks.

Tyler's call, 2026-08-18, answering "should these sessions be able to message
Doom?": may, VISIBLY, through bounded shapes. His workspace settings deny the
raw send commands, but a spawned session runs in Benhams-inbox - a different
project root that never loads that file - so the wall lives in
codesession._classify_bash, and this file holds the whole matrix against it:

  DENIED, never asked: dm / send / do dm_user / do send_message, found
  anywhere in the command - a send smuggled into a compound is still a send.

  READ-ONLY, never asked: the reflexive CLI reads (rooms, room read, conv
  list/show, status, ask --queue) - but only when the whole command IS the
  read: any shell metacharacter disqualifies it.

  ASK: everything else, unchanged - outreach and room post stay one visible
  tap on purpose.

The wire-in checks prove the classifier is actually consulted: a denied
command returns PermissionResultDeny without touching the ask machinery (the
owner-channel stub explodes if called), and a read-only command allows with
zero asks counted.

    python tests/test_codesession_gate.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio

import _testconfig  # noqa: F401,E402 - must precede every benham import

import benham.core.codesession as codesession

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


CLI = r"C:\Users\Tyler\Claude\Projects\Work-In-Project\benham-bot\benham.py"


def main():
    section("denied: the raw sends, however they are dressed")
    for cmd in (
        'python benham.py dm 123 "hi"',
        f'python {CLI} dm 123 "hi"',
        "python benham.py send 456 something",
        "python benham.py do dm_user user_id=123 text=hi",
        "python benham.py do send_message channel_id=1 text=x",
        'echo innocent && python benham.py dm 123 "smuggled"',
        f'cd somewhere; python {CLI} do dm_user user_id=1 text=x',
        # webhook, added 2026-08-20. Flagged by the 08-18 audit as reaching
        # Discord while on neither list; Tyler's call was to deny it.
        'python benham.py webhook "hello"',
        f'python {CLI} webhook --target general "hi"',
        'python benham.py webhook --url https://discord.com/api/webhooks/x "hi"',
        'python benham.py webhook --username Caz "impersonation"',
        'echo ok && python benham.py webhook "smuggled"',
        # Denied WHOLE: the listing is key material, not metadata, because a
        # webhook URL IS the credential. There is no read half to spare.
        "python benham.py webhook --list",
        # --face (commit 10) is accepted before or after the subcommand, so
        # the deny must catch BOTH forms - the flag-first one would otherwise
        # be a fresh route to the exact send this list exists to refuse.
        'python benham.py --face benham dm 123 "hi"',
        'python benham.py dm --face benham 123 "hi"',
        "python benham.py --face codex send 456 something",
        "python benham.py --face benham do dm_user user_id=123 text=hi",
        'python benham.py --face benham webhook "hello"',
    ):
        check(f"denied: {cmd[:60]}", codesession._classify_bash(cmd), "denied")
    check("send_message the capability name does not false-match 'send'",
          codesession._classify_bash("python benham.py sendx"), "ask")
    check("webhook does not false-match a longer word",
          codesession._classify_bash("python benham.py webhookx"), "ask")

    section("read-only: the whole command is the read, or it asks")
    for cmd in (
        "python benham.py rooms",
        f"python {CLI} rooms",
        "python benham.py rooms --as my-session",
        f"python {CLI} room read scratch",
        "python benham.py room read scratch --limit 20",
        "python benham.py conv list",
        "python benham.py conv show c14",
        "python benham.py status",
        "python benham.py ask --queue",
        # every call carries --face now; the free reads stay free with it
        "python benham.py rooms --face benham",
        "python benham.py status --face codex",
        "python benham.py conv list --face benham",
        "python benham.py ask --queue --face benham",
    ):
        check(f"read-only: {cmd[:60]}", codesession._classify_bash(cmd),
              "read_only")
    check("the --face value on the anchored read is charset-limited - "
          "no smuggling past the $ anchor",
          codesession._classify_bash('python benham.py ask --queue --face "x&evil"'),
          "ask")

    section("ask: mutations, compounds, redirects, and everything unrecognised")
    for cmd in (
        'python benham.py room post scratch "note"',          # mutation
        'python benham.py room create newroom "purpose"',     # mutation
        'python benham.py conv close c1 "done"',              # mutation
        'python benham.py ask "a real question"',             # sends a DM ask
        'python benham.py ask --queue "legacy dummy"',        # not the bare form
        'python benham.py outreach doom "question"',          # one tap, on purpose
        "python benham.py rooms && rm -rf x",                 # compound
        "python benham.py room read scratch > out.txt",       # redirect
        "python benham.py rooms | tee log",                   # pipe
        "python benham.py room read `whoami`",                # substitution
        "python benham.py room read $(x)",                    # substitution
        "echo hello",
        "python run_tests.py",
        "",
        None,
    ):
        check(f"ask: {str(cmd)[:60]!r}", codesession._classify_bash(cmd), "ask")

    section("wired in: deny never asks, read-only never asks - on BOTH shell "
            "tools (the first live test failed on exactly this)")
    calls = []

    async def exploding_ask_owner(prompt, rid):
        calls.append(prompt)
        raise AssertionError("the ask machinery must not be touched")

    old_ask, old_ro = codesession._ask_owner, codesession._read_only[0]
    codesession._ask_owner = exploding_ask_owner
    codesession._read_only[0] = False
    codesession._task_ctx["asks"] = 0
    try:
        # PowerShell first, verbatim from the live failure at 08:28:25Z: a
        # Windows session shells out through the PowerShell tool, the gate
        # checked only "Bash", and this exact command reached the ask path
        # with the description "Send test DM expected to be refused by policy".
        d = asyncio.run(codesession._can_use_tool(
            "PowerShell",
            {"command": f"python {CLI.replace(chr(92), '/')} dm 000000 "
                        "policy-test"}, None))
        check("PowerShell denied send -> PermissionResultDeny (the live regression)",
              type(d).__name__, "PermissionResultDeny")
        d2 = asyncio.run(codesession._can_use_tool(
            "Bash", {"command": 'python benham.py dm 123 "hi"'}, None))
        check("Bash denied send -> PermissionResultDeny",
              type(d2).__name__, "PermissionResultDeny")
        check("...with the bounded paths named in the message",
              "outreach" in d2.message and "tell_conversation" in d2.message,
              True)
        a = asyncio.run(codesession._can_use_tool(
            "PowerShell", {"command": f"python {CLI} room read scratch"}, None))
        check("PowerShell read-only CLI -> PermissionResultAllow",
              type(a).__name__, "PermissionResultAllow")
        check("neither touched the owner channel", calls, [])
        check("neither counted as an ask", codesession._task_ctx["asks"], 0)

        codesession._read_only[0] = True
        t = asyncio.run(codesession._can_use_tool(
            "Bash", {"command": "python benham.py rooms"}, None))
        check("triage keeps its Read/Glob/Grep-only contract - no bash "
              "fast path in read-only mode",
              type(t).__name__, "PermissionResultDeny")
    finally:
        codesession._ask_owner, codesession._read_only[0] = old_ask, old_ro
        codesession._task_ctx["asks"] = 0

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
