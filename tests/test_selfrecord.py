"""
test_selfrecord.py - Benham can answer "what did I do?" from evidence.

The bug this closes is not a crash, it is a false confidence. On 2026-08-15
Benham told Tyler it had never run a pc_task about Gmail - twice, arguing the
point - while he was looking at the approval prompts it had just sent. Its memory
was corrupted, and memory was the only thing it could consult.

So the checks here are mostly about the SHAPE of an answer rather than its
content, because the dangerous failure was never "returned the wrong entry". It
was turning "I have no record of that" into "that did not happen". A log covers a
window; asked about a moment outside it, honest silence and damning silence look
identical unless something says which is which. `covers` and `covered` are that
something, and most of this file exists to hold them to it.

Reads a temporary log file, never the real one - a test that depended on what
happened to be in logs/ would pass or fail on the bot's history.

    python test_selfrecord.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from benham import paths
from benham.core import capabilities, selfrecord

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def stamp(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).strftime("[%Y-%m-%d %H:%M:%SZ]")


LOG = "\n".join([
    f"{stamp(600)} Logged in as Benham#2721 (id 752313060970201218)",
    f"{stamp(500)} action read_channel by 273967061619965952: "
    "{'channel': 'asd', 'messages': [{'content': 'a stranger wrote this'}]}",
    f"{stamp(120)} action pc_task by 273967061619965952: "
    "{'status': 'completed', 'task': 'keep an eye out for a takeout email'}",
    f"{stamp(90)} DENIED purge_messages by 273967061619965952 (not an allowed guild)",
    f"{stamp(60)} PROPOSED delete_channel by 273967061619965952 preview",
    f"{stamp(30)} action set_presence by code-session: {'{'}'status': 'presence_set'{'}'}",
    "a line that is not a log line at all",
    "",
]) + "\n"


def main():
    tmp = tempfile.mkdtemp(prefix="benham-selfrecord-")
    real_log_dir = paths.LOG_DIR
    try:
        with open(os.path.join(tmp, "supervise.log"), "w", encoding="utf-8") as fh:
            fh.write(LOG)
        paths.LOG_DIR = tmp   # selfrecord reads paths.LOG_DIR at call time

        section("It reads the record it already writes")
        r = selfrecord.read(limit=50, now=NOW)
        check("every action/DENIED/PROPOSED line is parsed, and nothing else",
              r["matched"], 5)
        check("newest first", r["entries"][0]["action"], "set_presence")
        kinds = {e["kind"] for e in r["entries"]}
        check("all three kinds are captured", kinds, {"action", "DENIED", "PROPOSED"})

        section("Filtering")
        r = selfrecord.read(limit=10, action="pc_task", now=NOW)
        check("one pc_task in the file", r["matched"], 1)
        check("...and it is the one that was disputed",
              "takeout" in r["entries"][0]["detail"], True)
        r = selfrecord.read(limit=10, actor="code-session", now=NOW)
        check("actor filter separates Claude's own calls from Tyler's", r["matched"], 1)

        section("The distinction that matters: no record vs no event")
        # Inside the window, nothing matches -> silence MEANS something.
        r = selfrecord.read(limit=10, action="ban_member", since_minutes=200, now=NOW)
        check("nothing matched", r["matched"], 0)
        check("but the window IS covered, so silence is evidence", r["covered"], True)

        # Outside the window -> silence means nothing at all. This is the 08-15 bug.
        r = selfrecord.read(limit=10, action="pc_task", since_minutes=60 * 24 * 30, now=NOW)
        check("a 30-day question reaches past the log", r["covered"], False)

        # The span is measured over every parsed line, not just matches - otherwise
        # a filter matching nothing would report "no log" instead of "no match".
        r = selfrecord.read(limit=10, action="nonexistent_action", since_minutes=200, now=NOW)
        check("an unmatched filter still reports the real coverage",
              r["covered"], True)
        check("...and still names the span", bool(r["covers"]), True)

        section("An empty log admits it")
        empty = tempfile.mkdtemp(prefix="benham-selfrecord-empty-")
        try:
            paths.LOG_DIR = empty
            r = selfrecord.read(limit=5, since_minutes=60, now=NOW)
            check("no entries", r["matched"], 0)
            check("covers is None, not a fabricated span", r["covers"], None)
            check("covered is False - it cannot vouch for a window it has no file for",
                  r["covered"], False)
        finally:
            shutil.rmtree(empty, ignore_errors=True)
            paths.LOG_DIR = tmp

        section("The capability wired on top")
        act = capabilities.REGISTRY.get("what_i_did")
        check("what_i_did is registered", act is not None, True)
        check("...at READ tier", act.tier, 0)
        # Logged action results embed text other people wrote (a read_channel result
        # contains the messages it read). Without taints=True this is a laundering
        # path: stranger text -> log -> untainted turn -> pc_task.
        check("...and TAINTS, because logged results carry third-party text",
              act.taints, True)
        check("its filter param is not called 'action' (the outbox envelope owns that)",
              "action" not in (act.params or {}), True)

        section("An approval prompt says WHY, not only WHAT")
        # Tyler's requirement, 2026-08-16: "context must be included to clarify use
        # case ... I prefer knowing exactly what I'm confirming before I confirm it."
        from benham.core import codesession
        codesession._task_ctx.update(
            task="keep an eye out for a takeout email",
            narration="Firefox was already running so the flag was ignored. Restarting it.",
            asks=1)
        why = codesession._why_block()
        check("the session's own reasoning is quoted", "**Why:** Firefox was already" in why, True)
        check("the originating task is quoted", "takeout email" in why, True)
        check("a single ask is not labelled with a count", "Request" in why, False)

        codesession._task_ctx["asks"] = 8
        why = codesession._why_block()
        check("the eighth ask says so - the runaway signal no single command carries",
              "_Request 8 for this task._" in why, True)

        # Repeated narration is marked, not reprinted. A session can fire several
        # tool calls without narrating between them; the first version repeated the
        # same 400 characters under four different commands, which reads as a claim
        # that they share a reason. Found in the first real test, 2026-08-16.
        codesession._task_ctx.update(
            task="run the suite",
            narration="Board read - suite was green as of the last refactor pass.",
            asks=2, last_why=None)
        first = codesession._why_block()
        check("the first ask prints the reasoning",
              "Board read" in first and "same reasoning" not in first, True)
        codesession._task_ctx["asks"] = 3
        second = codesession._why_block()
        check("an unchanged reason is marked, not repeated verbatim",
              "same reasoning as the last ask" in second and "Board read" not in second, True)
        codesession._task_ctx.update(narration="Now running the loop.", asks=4)
        third = codesession._why_block()
        check("...and new reasoning prints again", "Now running the loop." in third, True)

        codesession._task_ctx.update(task=None, narration=None, asks=0, last_why=None)
        check("nothing to say means nothing added, not an empty heading",
              codesession._why_block(), "")

        section("A capability may not shadow an outbox envelope key")
        try:
            @capabilities.action("_bad_test_action", 0, "should not register",
                                 {"action": {"type": "str"}})
            async def _bad(ctx, p):
                return {}
            registered = "_bad_test_action" in capabilities.REGISTRY
        except ValueError as e:
            registered = False
            check("...and the refusal names the problem", "reserves" in str(e), True)
        check("declaring a reserved param refuses to register", registered, False)
    finally:
        paths.LOG_DIR = real_log_dir
        capabilities.REGISTRY.pop("_bad_test_action", None)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
