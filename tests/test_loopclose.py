"""test_loopclose.py - the close-the-loop lane.

The filing lane's failure mode is losing a report. THIS lane's is telling
someone the same thing twice, or telling them the wrong thing - so the tests
that matter here are the idempotence guard, the declined/fixed split, and the
refusal to guess when GitHub cannot be reached.

Doom asked for this on 2026-08-16: "1. knowing that its getting tracked 2. ild
like the secod thing where youll tell me if its a wont-fix or not a bug". The
second half is what this file covers.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham.core import issues        # noqa: E402
from benham.core import loopclose     # noqa: E402

DOOM = 1097631170788851815

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


_tmp = tempfile.mkdtemp(prefix="benham-loopclose-test-")
issues.ISSUES_FILE = os.path.join(_tmp, "guest_issues.jsonl")
issues.ENABLED = True
issues.REPO = "example/intake"


class Outbox:
    """Records every DM instead of sending one."""

    def __init__(self):
        self.sent = []

    def enqueue(self, **fields):
        self.sent.append(fields)
        return "queued"


class Closer:
    def __init__(self):
        self.closed = []

    def close(self, cid, outcome, told=False):
        self.closed.append((cid, outcome, told))


_out = Outbox()
_conv = Closer()
loopclose.outbox = _out
loopclose.conversations = _conv

# The tracker, as gh would answer for it. Swapped per test.
_TRACKER = {}


def _fake_fetch(number, repo):
    if number not in _TRACKER:
        raise OSError("gh: no such issue")
    return _TRACKER[number]


loopclose._fetch = _fake_fetch


def _filing(number, category="bug", told=None, conversation="c1",
            author_id=DOOM):
    return {"ts": f"2026-08-21T00:00:0{number}+00:00", "day": "2026-08-21",
            "author_id": author_id, "author": "doomassassin1",
            "category": category, "title": f"report {number}",
            "url": f"https://github.com/example/intake/issues/{number}",
            "project": None, "told": told, "conversation": conversation}


def _seed(*entries):
    with open(issues.ISSUES_FILE, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps({k: v for k, v in e.items() if v is not None})
                    + "\n")


section("classify - what the tracker state actually means")
check("an open, untriaged issue is not news",
      loopclose.classify("OPEN", None, ["bug", "needs-triage"]), None)
check("approved-but-open is still not news - progress is not the message",
      loopclose.classify("OPEN", None, ["bug", "approved"]), None)
check("closed as completed is a fix",
      loopclose.classify("CLOSED", "COMPLETED", ["bug"]), "fixed")
check("the declined label is the explicit no",
      loopclose.classify("OPEN", None, ["bug", "declined"]), "declined")
check("closed as NOT_PLANNED is the same no, said with GitHub's own control",
      loopclose.classify("CLOSED", "NOT_PLANNED", ["bug"]), "declined")
check("declined beats closed - a rejected request is never reported as fixed",
      loopclose.classify("CLOSED", "COMPLETED", ["bug", "declined"]),
      "declined")
check("labels are read case-insensitively",
      loopclose.classify("OPEN", None, ["Declined"]), "declined")

section("issue_number - the url is the only handle we have")
check("a normal issue url",
      loopclose.issue_number("https://github.com/x/y/issues/42"), 42)
check("a trailing slash does not break it",
      loopclose.issue_number("https://github.com/x/y/issues/42/"), 42)
check("nonsense is None, not a crash", loopclose.issue_number("nope"), None)
check("None is None", loopclose.issue_number(None), None)

section("pending - only news, and only news nobody has heard")
_TRACKER.clear()
_TRACKER[1] = {"state": "OPEN", "state_reason": None, "labels": ["bug"],
               "title": "one", "last_comment": ""}
_TRACKER[2] = {"state": "CLOSED", "state_reason": "COMPLETED",
               "labels": ["bug"], "title": "two", "last_comment": ""}
_TRACKER[3] = {"state": "CLOSED", "state_reason": "COMPLETED",
               "labels": ["bug"], "title": "three", "last_comment": ""}
_seed(_filing(1), _filing(2), _filing(3, told="fixed"))
_p = loopclose.pending()
check("the open one is left alone", [i["number"] for i in _p], [2])
check("...and the one already told is never raised again",
      any(i["number"] == 3 for i in _p), False)

section("owner filings have nobody to report back to")
_seed({"ts": "2026-08-21T00:00:00+00:00", "day": "2026-08-21",
       "author_id": None, "author": "Caz (owner/CLI)", "category": "bug",
       "title": "smoke test", "url": "https://github.com/example/intake/issues/2"})
check("a filing with no guest behind it is skipped", loopclose.pending(), [])

section("an unreachable tracker never invents an outcome")
_seed(_filing(99))          # 99 is not in _TRACKER, so _fetch raises
check("gh failing means no news, NOT a declined DM", loopclose.pending(), [])

section("run - the send, once, in the right order")
_TRACKER.clear()
_TRACKER[7] = {"state": "CLOSED", "state_reason": "NOT_PLANNED",
               "labels": ["enhancement"], "title": "seven",
               "last_comment": "not doing this one, too niche"}
_seed(_filing(7, category="want", conversation="c9"))
_out.sent, _conv.closed = [], []
_done = loopclose.run()
check("one DM enqueued", len(_out.sent), 1)
check("...to the reporter, as a DM", _out.sent[0]["action"], "dm")
check("...addressed to the right person", _out.sent[0]["user_id"], DOOM)
check("the wording says declined, not fixed",
      "not taking this one forward" in _out.sent[0]["content"], True)
check("it names what they filed, in their words",
      "report 7" in _out.sent[0]["content"], True)
check("the closing note rides along - a wont-fix with no reason is silence",
      "too niche" in _out.sent[0]["content"], True)
check("the note is not attributed to Tyler, who may not have written it",
      "his note" in _out.sent[0]["content"], False)
check("the OWED conversation is closed with the outcome, marked told",
      _conv.closed, [("c9", "declined", True)])

_out.sent, _conv.closed = [], []
check("a second run sends nothing - told is written, and it sticks",
      loopclose.run(), [])
check("...and really nothing", len(_out.sent), 0)

section("dry run reads, and only reads")
_seed(_filing(7, category="want", conversation="c9"))
_out.sent, _conv.closed = [], []
_dry = loopclose.run(dry_run=True)
check("it reports what it would do", len(_dry), 1)
check("...marked unsent", _dry[0]["sent"], False)
check("no DM was enqueued", len(_out.sent), 0)
check("no conversation was closed", len(_conv.closed), 0)
check("nothing was marked told, so the real run still works",
      len(loopclose.pending()), 1)

section("the per-run cap - a triage session is not a notification storm")
_TRACKER.clear()
for n in range(1, 9):
    _TRACKER[n] = {"state": "CLOSED", "state_reason": "COMPLETED",
                   "labels": ["bug"], "title": str(n), "last_comment": ""}
_seed(*[_filing(n) for n in range(1, 9)])
check("eight closed issues do not become eight DMs at once",
      len(loopclose.pending()), loopclose.MAX_PER_RUN)

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
