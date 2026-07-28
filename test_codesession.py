"""
test_codesession.py - the SDK-free half of the PC permission gate.

codesession.py suspends a real Claude Code tool call on Tyler's machine until he
answers a DM. The streaming half needs the agent SDK and a live session; these
helpers do not, and they are the parts where a wrong answer has him approving
something he could not read, or a stale "yes" landing on a request that already
timed out:

  _describe builds the approval text. It keys shell tools on the PRESENCE of a
  command field rather than a name list, because the day the shell tool was
  called PowerShell instead of Bash, the generic branch truncated the command to
  80 characters - an approval prompt for a command Tyler could not actually read.

  answer() must ignore late and unknown answers: after a timeout has already
  denied, a "yes" arriving matches nothing, which is the safe direction.

  _SECRET_RE decides which reads get the SECRET-READ audit line. Reads are free
  by Tyler's explicit choice; the log line is the only trail, so the pattern
  matching nothing would make credential reads silent.

    python test_codesession.py
"""

import asyncio
import sys

import codesession

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------------------
section("_describe — what Tyler actually consents to on his phone")

long_cmd = "powershell -Command " + ("Remove-Item x; " * 100)   # ~1500 chars
text = codesession._describe("PowerShell", {"command": long_cmd})
check("ANY tool with a command field shows the command (name list not trusted)",
      "run a **PowerShell** command" in text, True)
check("...with a 1200-char window, not the 80-char generic one",
      long_cmd[:1200] in text, True)
check("...but not unboundedly", long_cmd in text, False)

text = codesession._describe("Bash", {"command": "rm -rf build"})
check("the full short command is visible verbatim", "rm -rf build" in text, True)

text = codesession._describe("Write", {"file_path": "C:/x/y.txt"})
check("Write says create/overwrite and names the path",
      "create/overwrite" in text and "C:/x/y.txt" in text, True)
text = codesession._describe("Edit", {"file_path": "a.py"})
check("Edit says edit", "edit a file" in text and "a.py" in text, True)

text = codesession._describe("Task", {"description": "d" * 300})
check("a sub-agent description is truncated to 200",
      "d" * 200 in text and "d" * 201 not in text, True)

text = codesession._describe("MysteryTool", {"arg": "v" * 200})
check("an unknown tool without a command falls to the generic 80-char line",
      "use **MysteryTool**" in text and "v" * 80 in text and "v" * 81 not in text,
      True)
check("no input at all still describes something",
      codesession._describe("MysteryTool", None), "use **MysteryTool**")


# --------------------------------------------------------------------------
section("_progress_label — glanceable, and never the approval surface")

class _Block:
    def __init__(self, name, **inp):
        self.name = name
        self.input = inp


check("a short command labels as 'Tool: cmd'",
      codesession._progress_label(_Block("Bash", command="ls -la")),
      "Bash: ls -la")
label = codesession._progress_label(_Block("Bash", command="x" * 100))
check("a long detail is cut to 60 with an ellipsis",
      len(label) <= len("Bash: ") + 60 and label.endswith("..."), True)
check("no detail is just the name",
      codesession._progress_label(_Block("WebSearch")), "WebSearch")


# --------------------------------------------------------------------------
section("answer / pending_request — late answers land on nothing")


async def _gate_checks():
    loop = asyncio.get_running_loop()
    codesession._pending.clear()

    check("no pending request when nothing waits",
          codesession.pending_request(), None)
    check("an answer with nothing waiting matches nothing",
          codesession.answer("1", True), False)

    fut = loop.create_future()
    codesession._pending["7"] = fut
    check("the outstanding request is visible", codesession.pending_request(),
          "7")
    check("an answer to the wrong id matches nothing",
          codesession.answer("8", True), False)
    check("...and leaves the real one waiting", fut.done(), False)

    check("the right id resolves it", codesession.answer("7", True), True)
    check("...with the approval", fut.result(), True)
    check("answering again matches nothing (single resolution)",
          codesession.answer("7", False), False)
    check("...and cannot flip the answer", fut.result(), True)

    # The timeout path pops the future and denies; a "yes" arriving after that
    # is the late answer the docstring promises to ignore.
    fut2 = loop.create_future()
    codesession._pending["9"] = fut2
    codesession._pending.pop("9")          # what the timeout handler does
    check("an answer after a timeout matches nothing",
          codesession.answer("9", True), False)

    # A done-but-unpopped future must not read as an open request.
    fut3 = loop.create_future()
    fut3.set_result(True)
    codesession._pending["10"] = fut3
    check("a completed future is not reported as pending",
          codesession.pending_request(), None)
    codesession._pending.clear()


asyncio.run(_gate_checks())


# --------------------------------------------------------------------------
section("_SECRET_RE — the audit line for credential-shaped reads")

for path in (".env", "project/.env.local", "environ.env", "webhooks.json",
             "C:/Users/Tyler/.ssh/id_rsa", "service-credentials.json",
             "api_token.txt", "secrets.yaml", "server.pem", "signing.key",
             ".npmrc", ".git-credentials"):
    check(f"flags {path!r}", bool(codesession._SECRET_RE.search(path)), True)

for path in ("README.md", "bot.py", "notes/environment.md", "envelope.txt"):
    check(f"does not flag {path!r}",
          bool(codesession._SECRET_RE.search(path)), False)


# --------------------------------------------------------------------------
section("READ_ONLY_TOOLS — the allowlist is of reads, and stays that way")

for tool in ("Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit",
             "Task", "KillShell"):
    check(f"{tool} is NOT free", tool in codesession.READ_ONLY_TOOLS, False)
check("reading is free", "Read" in codesession.READ_ONLY_TOOLS, True)
check("searching is free", "Grep" in codesession.READ_ONLY_TOOLS, True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
