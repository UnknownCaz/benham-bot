"""loopclose.py - telling the reporter what happened to their report.

The fourth side of decision #12's loop (filed / started / fixed / declined) and
the half of the intake funnel that decision #28 deferred "until the funnel
proves itself". It was deferred on 2026-08-20. Doom had asked for it on
2026-08-16, in these words:

    "1. knowing that its getting tracked 2. ild like the secod thing where
     youll tell me if its a wont-fix or not a bug"

Half of that shipped immediately - the "filed for caz" ack, plus an OWED
conversation so the report has to reach a terminal state. This module is the
other half.

WHAT COUNTS AS NEWS, AND WHY "started" DOES NOT. conversations.py's TERMINAL
STATES ONLY paragraph settles this, and it is grounded in the same conversation
with the same person: CLOSED-with-an-outcome and BANKED are reportable,
ANSWERED is not, because progress is not the message. Decision #12 lists
"started" among the four, and this module deliberately does NOT send one - a DM
every time Tyler flips a label to `approved` is a notification stream, and the
one person it would reach asked for the opposite. `approved` is still read
here, because it is how a filing stops being untouched, but it is recorded, not
sent. If Tyler wants the started ping after all it is one entry away.

WHERE TRUTH LIVES. GitHub, not this file. The tracker is where Tyler actually
works, so a label flip or a close there IS the decision - there is no separate
"and now notify the reporter" step for him to forget. This module only reads
that state and carries it onward, which is what lets the loop close without a
human remembering to close it.

IDEMPOTENCE IS THE WHOLE RISK. The filing lane's failure mode is losing a
report; this lane's is telling someone the same thing twice. Every send is
guarded by `issues.mark_told`, written immediately after the DM is enqueued,
and a record carrying `told` is never considered again.
"""

import json
import shutil
import subprocess

from benham import paths
from benham.core import conversations
from benham.core import issues
from benham.core import outbox

MAX_PER_RUN = 5          # a triage session that closes twenty is not twenty DMs
MAX_REASON = 400


def _gh_json(args, timeout=30):
    exe = shutil.which("gh")
    if not exe:
        raise OSError("gh CLI not on PATH")
    proc = subprocess.run([exe] + list(args), capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise OSError((proc.stderr or proc.stdout or "gh failed").strip()[:300])
    return json.loads(proc.stdout or "{}")


def issue_number(url):
    """The trailing number of an issue URL, or None."""
    try:
        return int(str(url).rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, AttributeError):
        return None


def classify(state, state_reason, labels):
    """The outcome a tracker state represents, or None if it is not news yet.

    Reads only what Tyler actually does in the tracker. A `declined` label is
    the explicit no; closing an issue as "not planned" is that same decision
    expressed with GitHub's own control. Both land on `declined`, because
    letting the second one fall through to the closed branch would tell a
    reporter their rejected request had been fixed.
    """
    names = set(str(n).lower() for n in (labels or []))
    closed = str(state or "").upper() == "CLOSED"
    reason = str(state_reason or "").upper()
    if "declined" in names or (closed and reason == "NOT_PLANNED"):
        return "declined"
    if closed:
        return "fixed"
    return None


def _fetch(number, repo):
    data = _gh_json(["issue", "view", str(number), "--repo", repo, "--json",
                     "number,state,stateReason,labels,title,comments"])
    labels = [lab.get("name") for lab in (data.get("labels") or [])]
    comments = data.get("comments") or []
    last = comments[-1].get("body") if comments else ""
    return {"state": data.get("state"),
            "state_reason": data.get("stateReason"),
            "labels": labels,
            "title": data.get("title") or "",
            "last_comment": " ".join(str(last or "").split())[:MAX_REASON]}


def _message(outcome, entry, info):
    """What the reporter reads.

    The closing comment rides along when there is one, because "wont-fix" with
    no reason is the answer Doom already gets by being told nothing. It is
    Tyler's own text from his own private repo, so it is quoted as his - but it
    is still truncated, because an issue thread is not a DM.
    """
    what = {"bug": "bug", "want": "feature request",
            "idea": "idea", "question": "question"}.get(
                entry.get("category"), "report")
    title = entry.get("title") or info.get("title") or "your report"
    if outcome == "fixed":
        body = ('update on the ' + what + ' you filed - "' + title +
                '": it is fixed. thanks for reporting it, that one was worth '
                'having.')
    else:
        body = ('update on the ' + what + ' you filed - "' + title +
                '": caz is not taking this one forward.')
    note = info.get("last_comment")
    if note:
        # "note on it", not "his note". The closing comment is whoever wrote it
        # - Tyler by hand, or a Claude session doing triage - and telling a
        # reporter that Tyler said something a session actually wrote is a
        # small lie in the exact place this lane is meant to be building trust.
        body += "\n\nnote on it: " + note
    return body


def pending(limit=MAX_PER_RUN):
    """Filings whose tracker state is news the reporter has not been told.

    Read-only - it talks to GitHub and to nothing else, so it is always safe to
    run for a look. A filing GitHub cannot answer for is SKIPPED rather than
    guessed at: an unreachable tracker must never be able to produce a
    "declined" DM.
    """
    out = []
    for e in issues.guest_filings():
        if e.get("told"):
            continue
        number = issue_number(e.get("url"))
        if number is None:
            continue
        try:
            info = _fetch(number, issues.REPO)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
        outcome = classify(info["state"], info["state_reason"], info["labels"])
        if not outcome:
            continue
        out.append({"entry": e, "info": info, "outcome": outcome,
                    "number": number,
                    "message": _message(outcome, e, info)})
        if len(out) >= limit:
            break
    return out


def run(dry_run=False, limit=MAX_PER_RUN):
    """Close every loop that has news. Returns what was (or would be) sent.

    The order is deliberate. Enqueue the DM, then mark told, then close the
    conversation: marking told first would lose an outcome to a crash in
    between, and closing the conversation first would leave the rail claiming
    "resolved" about something nobody has been told. A conversation that fails
    to close is swallowed on purpose - the reporter has been told, which is the
    property this lane exists for, and a stuck rail is the smaller problem.
    """
    done = []
    for item in pending(limit=limit):
        entry = item["entry"]
        if dry_run:
            item["sent"] = False
            done.append(item)
            continue
        outbox.enqueue(face=paths.DEFAULT_FACE, action="dm", user_id=int(entry["author_id"]),
                       content=item["message"])
        issues.mark_told(entry["url"], item["outcome"])
        cid = entry.get("conversation")
        if cid:
            try:
                conversations.close(cid, item["outcome"], told=True)
            except Exception:  # noqa: BLE001 - they were told; that is the point
                pass
        item["sent"] = True
        done.append(item)
    return done
