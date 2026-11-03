"""
job.py - jobs from the shell (INTENT decisions 61 and 62, Stage 9 B).

    python benham.py job list [open | closed | all]
    python benham.py job open "<goal>" --with <name | @mention | id | caz>
                              [--opener "text"] [--tools web_search]
    python benham.py job open ... --confirm-token TOK
    python benham.py job close <job_id> --outcome "how it ended" [--confirm-token TOK]

A thin face over three registry verbs - list_jobs, open_job, close_job - through
the outbox, exactly as `do` reaches them; nothing here decides anything. So the
bot that answers is the one whose state/jobs.json is live (cazzy-mac since Phase
B), and an opener goes out from there.

open and close are tier 2, always confirmed: the first call prints the card the
bot built (who, the goal, the tools, the exact opener) and a token, and changes
nothing; the second, with --confirm-token, does it. A preview exits NON-ZERO, the
delete.py rule: a script that read 0 would believe a job was opened that was not.
"""

import sys

from benham import paths
from benham.cli.delete import run_two_step
from benham.core.outbox import (EXIT_FAIL, EXIT_OK, console_utf8, enqueue,
                                report_outcome, usage)

USAGE = ("Usage: python benham.py job list [open|closed|all]\n"
         "       python benham.py job open \"<goal>\" --with <name|@mention|id|caz> "
         "[--opener \"text\"] [--tools web_search] [--confirm-token TOK] [--no-wait]\n"
         "       python benham.py job close <job_id> --outcome \"how it ended\" "
         "[--confirm-token TOK] [--no-wait]")


def _take(argv, flag):
    """Pull `flag value` out of argv. Returns (value or None, rest, error)."""
    if flag not in argv:
        return None, argv, None
    i = argv.index(flag)
    if i + 1 >= len(argv):
        return None, argv, f"{flag} needs a value"
    return argv[i + 1], argv[:i] + argv[i + 2:], None


def _list(state, timeout=60):
    final = enqueue(face=paths.PROCESS_FACE, action="list_jobs", state=state)
    code, result = report_outcome(final, timeout=timeout)
    if code != EXIT_OK or not result:
        return code if code != EXIT_OK else EXIT_FAIL
    r = result.get("result") or {}
    rows = r.get("jobs") or []
    print(f"jobs ({len(rows)} shown, {r.get('open', 0)} open)")
    if not rows:
        print(f"  ({r.get('message') or 'none'})")
    for j in rows:
        tools = ", ".join(j.get("tools") or []) or "chat only"
        print(f"  {j['id']:<5} {str(j.get('state')):<7} with {j.get('with_label') or j.get('with')}"
              f"  [{tools}]")
        print(f"        goal: {j.get('goal')}")
        line = f"        opened {j.get('opened') or '?'} by {j.get('opened_by') or '?'}"
        if j.get("last_activity"):
            line += f"; last activity {j['last_activity']} ({j.get('turns') or 0} from them)"
        print(line)
        if j.get("closed"):
            print(f"        closed {j['closed']} by {j.get('closed_by') or '?'}: "
                  f"{j.get('outcome') or '-'}")
    return EXIT_OK


def _q(text):
    return "\"" + str(text).replace("\"", "\\\"") + "\""


def main(argv):
    console_utf8()
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        return usage(USAGE)
    cmd, rest = args[0], args[1:]

    if cmd == "list":
        if len(rest) > 1 or (rest and rest[0] not in ("open", "closed", "all")):
            return usage(USAGE)
        return _list(rest[0] if rest else "all")

    if cmd not in ("open", "close"):
        return usage(f"unknown job command {cmd!r}\n{USAGE}")

    no_wait = "--no-wait" in rest
    rest = [a for a in rest if a != "--no-wait"]
    token, rest, err = _take(rest, "--confirm-token")
    if err:
        return usage(err)

    if cmd == "open":
        who, rest, err = _take(rest, "--with")
        if err:
            return usage(err)
        opener, rest, err = _take(rest, "--opener")
        if err:
            return usage(err)
        tools, rest, err = _take(rest, "--tools")
        if err:
            return usage(err)
        if who is None or not who.strip():
            return usage(f"job open needs --with <name|@mention|id|caz>\n{USAGE}")
        if len(rest) != 1 or not rest[0].strip():
            return usage(f"job open takes exactly one goal (quote it)\n{USAGE}")
        goal = rest[0].strip()
        params = {"goal": goal, "with": who.strip()}
        extra = f" --with {_q(who.strip())}"
        if opener is not None:
            params["opener"] = opener
            extra += f" --opener {_q(opener)}"
        if tools is not None:
            params["tools"] = [t.strip() for t in tools.split(",") if t.strip()]
            extra += f" --tools {_q(tools)}"
        return run_two_step(
            "open_job",
            describe=f"open a job with {who.strip()!r}: {goal[:80]!r}",
            rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} job open "
                             f"{_q(goal)}{extra} --confirm-token {t}"),
            token=token, no_wait=no_wait,
            nothing="NO JOB WAS OPENED AND NOTHING WAS SENT YET.", **params)

    outcome, rest, err = _take(rest, "--outcome")
    if err:
        return usage(err)
    if outcome is None or not outcome.strip():
        return usage(f"job close needs --outcome \"how it ended\"\n{USAGE}")
    if len(rest) != 1 or not rest[0].strip():
        return usage(f"job close takes exactly one job id (e.g. j3)\n{USAGE}")
    job_id = rest[0].strip()
    return run_two_step(
        "close_job",
        describe=f"close job {job_id}",
        rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} job close "
                         f"{job_id} --outcome {_q(outcome)} --confirm-token {t}"),
        token=token, no_wait=no_wait,
        nothing="NO JOB WAS CLOSED YET.", job_id=job_id, outcome=outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
