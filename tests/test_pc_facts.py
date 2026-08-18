"""
test_pc_facts.py - a pc_task result carries EVIDENCE, not only the session's word.

INTENT §7 Bug 2's surviving half. On 2026-08-17 Benham told Tyler "I can't
independently verify it, I'm relying on the session's own self-report" about a
dm_user that was sitting in its own action log with a real message id. The
mechanism was that run_task returned prose: nothing structured ever said "this
fired, here is its id", so the model was left choosing between a session's
narration and a tool call it might not think to make.

What these checks pin:

  run_task returns a dict of facts - text, session id, cost, error state, ask
  count, and the started/ended window - including on the disabled path, so no
  caller ever has to guess which shape it got.

  _cli_actions_between reads the ACTION LOG for the task's window: `action`
  lines by code-session inside it are in, everything else - other actors,
  refusals, actions outside the window - is out. Oldest first, because that is
  task order.

  The real _pc_task handler ships both halves in separate fields, with the
  session's prose in `result` and the record in `cli_actions`, so a later turn
  asked "did it actually send?" is answering from the log it was handed.

Reads a temporary log file, never the real one, same as test_selfrecord.

    python tests/test_pc_facts.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import _testconfig  # noqa: F401,E402 - must precede every benham import

from benham import paths
from benham.core import capabilities, policy
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


NOW = datetime.now(timezone.utc)
FACT_KEYS = {"text", "session_id", "cost_usd", "is_error", "tools_used",
             "asks", "started", "ended"}


def stamp(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).strftime("[%Y-%m-%d %H:%M:%SZ]")


def iso(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


# During the window (120s ago .. 60s ago): one dm_user and one send_file by
# code-session. Outside it: an earlier dm_user (an hour old), a DENIED line
# (not an action), and an action by Tyler himself (wrong actor).
LOG = "\n".join([
    f"{stamp(3600)} action dm_user by code-session: {{'message_id': 111}}",
    f"{stamp(110)} action dm_user by code-session: {{'message_id': 222}}",
    f"{stamp(100)} DENIED pc_task by code-session [rule=blocked_when_tainted]",
    f"{stamp(90)} action send_file by code-session: {{'file': 'a.png'}}",
    f"{stamp(80)} action read_channel by 273967061619965952: {{'ok': 1}}",
    f"{stamp(5)} action dm_user by code-session: {{'message_id': 333}}",
])


def main():
    tmp = tempfile.mkdtemp(prefix="benham-pcfacts-")
    old_logdir = paths.LOG_DIR
    paths.LOG_DIR = tmp
    with open(os.path.join(tmp, "supervise.log"), "w", encoding="utf-8") as fh:
        fh.write(LOG + "\n")
    try:
        section("run_task returns facts even when PC access is off")
        old_enabled = codesession.ENABLED
        codesession.ENABLED = False
        try:
            r = asyncio.run(codesession.run_task("anything"))
            check("disabled path is a dict", isinstance(r, dict), True)
            check("...with every fact key", set(r) >= FACT_KEYS, True)
            check("...and the refusal in text", "turned off" in r["text"], True)
        finally:
            codesession.ENABLED = old_enabled

        section("_cli_actions_between reads only the window, only actions, "
                "only code-session")
        acts = capabilities._cli_actions_between(iso(120), iso(60))
        check("exactly the two in-window actions", len(acts), 2)
        check("oldest first (task order)",
              [a["action"] for a in acts], ["dm_user", "send_file"])
        check("the id is in the detail - the evidence half",
              "222" in acts[0]["detail"], True)
        check("an hour-old action is out",
              any("111" in a["detail"] for a in acts), False)
        check("a just-now action outside the window is out",
              any("333" in a["detail"] for a in acts), False)
        check("garbage timestamps return empty, not a crash",
              capabilities._cli_actions_between(None, "not-a-date"), [])

        section("end slop admits an action logged a beat after session exit")
        acts = capabilities._cli_actions_between(iso(120), iso(8))
        check("the 5s-ago action is inside a window ending 8s ago (10s slop)",
              any("333" in a["detail"] for a in acts), True)

        section("the real _pc_task handler ships prose and record separately")
        old_run, old_en = codesession.run_task, codesession.ENABLED

        async def fake_run_task(prompt, on_progress=None, read_only=False):
            return {"text": "I sent it, promise.", "session_id": "sess-abc123",
                    "cost_usd": 0.21, "is_error": False, "tools_used": ["Bash"],
                    "asks": 2, "started": iso(120), "ended": iso(60)}

        async def fast_sleep(_):
            return None

        codesession.run_task, codesession.ENABLED = fake_run_task, True
        old_sleep = asyncio.sleep
        capabilities.asyncio.sleep = fast_sleep
        try:
            result, preview = asyncio.run(capabilities.run(
                None, lambda m: None, "pc_task", {"task": "send doom the file"},
                actor_id=None, force=True, call_ctx=policy.CallContext.local()))
        finally:
            codesession.run_task, codesession.ENABLED = old_run, old_en
            capabilities.asyncio.sleep = old_sleep

        check("no preview parked", preview, None)
        check("the session's words are result, unchanged",
              result["result"], "I sent it, promise.")
        check("session facts ride along",
              result["session"],
              {"id": "sess-abc123", "cost_usd": 0.21, "is_error": False,
               "asks": 2})
        check("cli_actions holds the two in-window log entries",
              [a["action"] for a in result["cli_actions"]],
              ["dm_user", "send_file"])
        check("the note says which half is evidence",
              "action log" in result.get("note", ""), True)
    finally:
        paths.LOG_DIR = old_logdir

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    sys = _sys
    sys.exit(main())
