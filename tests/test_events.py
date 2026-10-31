"""
test_events.py - scheduled events, offline (INTENT 60, Stage 8 B).

Five capabilities give the server's Events screen to a prompt: list_events,
create_event, edit_event, start_event, cancel_event. One module (eventtime.py)
turns typed text into an instant and back into the two clocks a person checks.
What is pinned:

  THE CLOCK IS DETROIT'S, SAID BOTH WAYS. A naive time is Caz's clock
  (America/Detroit), never the bot machine's zone; Z and offsets are taken as
  written; every time printed carries Detroit and UTC. The fall-back hour is
  flagged, the spring-forward gap refused. Where zoneinfo has no database (the
  PC), the US Eastern stand-in agrees with it hour by hour (checked where both
  exist - the Mac).

  THE POSTURE. create, edit and start are tier 2, outward, always confirmed (a
  scheduled event shows to the whole server); cancel is tier 3 (allowlist,
  dry-run, token); list is a read that taints. A guest reaches none of them.

  REFUSED AT THE PREVIEW, WHOLE. A start in the past, an end before the start,
  an external event without an end or a location, a voice event without a
  voice channel, Discord's 100 open events: one plain sentence, and Discord is
  never asked to change anything - not at the preview, not at the confirm.

  THE STATUS MOVES ARE DISCORD'S. scheduled -> active (start), scheduled ->
  cancelled and active -> completed (cancel), and a closed event refuses every
  change. A cancel saves the event's details and cover first; no copy, no cancel.

The stubs feed REAL discord.ScheduledEvent objects and call discord.py's own
Guild.create_scheduled_event / fetch_scheduled_events and ScheduledEvent.edit /
start / end / cancel - only the HTTP layer underneath is fake - so a keyword
argument discord.py 2.7.1 would reject fails here, not on Discord. The backup
folder is a temp directory.

    python test_events.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import atexit
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from datetime import datetime, timedelta, timezone

import discord

from benham.core import eventtime

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!a}\n        want {want!a}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def time_refusal(text):
    try:
        eventtime.parse(text, "start")
        return None
    except eventtime.EventTimeError as e:
        return str(e)


# ================================================================== eventtime
section("eventtime - a naive time is Detroit's, whatever zone the machine is in")
w = eventtime.parse("2026-10-31 20:00", "start")
check("8 pm on Halloween is EDT, so 00:00 UTC the next day",
      (eventtime.utc_iso(w.dt), w.assumed_local, w.ambiguous),
      ("2026-11-01T00:00:00Z", True, False))
check("...printed both ways", eventtime.both(w.dt),
      {"local": "Sat 2026-10-31 20:00 EDT", "utc": "2026-11-01T00:00:00Z"})
check("...and as one preview line", eventtime.line(w.dt),
      "Sat 2026-10-31 20:00 EDT = 2026-11-01 00:00 UTC")
check("a T works as well as a space, seconds too",
      eventtime.utc_iso(eventtime.parse("2026-10-31T20:00:30", "start").dt),
      "2026-11-01T00:00:30Z")
check("January is EST (-5)", eventtime.utc_iso(eventtime.parse("2027-01-15 09:00", "s").dt),
      "2027-01-15T14:00:00Z")
_tz = os.environ.get("TZ")
os.environ["TZ"] = "Asia/Tokyo"
if hasattr(__import__("time"), "tzset"):
    __import__("time").tzset()
check("...even with the process zone set to Tokyo",
      eventtime.utc_iso(eventtime.parse("2026-10-31 20:00", "s").dt), "2026-11-01T00:00:00Z")
if _tz is None:
    os.environ.pop("TZ", None)
else:
    os.environ["TZ"] = _tz
if hasattr(__import__("time"), "tzset"):
    __import__("time").tzset()

section("eventtime - Z and offsets are taken as written")
w = eventtime.parse("2026-10-31T20:00Z", "start")
check("Z is UTC, and the Detroit half says 16:00 EDT",
      (eventtime.both(w.dt), w.assumed_local),
      ({"local": "Sat 2026-10-31 16:00 EDT", "utc": "2026-10-31T20:00:00Z"}, False))
check("a lower-case z too", eventtime.utc_iso(eventtime.parse("2026-10-31T20:00z", "s").dt),
      "2026-10-31T20:00:00Z")
w = eventtime.parse("2026-10-31T20:00-07:00", "start")
check("-07:00 is taken as written: 03:00 UTC, 23:00 in Detroit",
      (eventtime.both(w.dt), w.assumed_local),
      ({"local": "Sat 2026-10-31 23:00 EDT", "utc": "2026-11-01T03:00:00Z"}, False))
check("+0200 without a colon works", eventtime.utc_iso(
    eventtime.parse("2026-07-04T12:00+0200", "s").dt), "2026-07-04T10:00:00Z")
check("an offset time is never flagged ambiguous, even inside the repeated hour",
      eventtime.parse("2026-11-01T01:30-05:00", "s").ambiguous, False)

section("eventtime - the fall-back morning, 2026-11-01 (verified with zoneinfo on the Mac)")
w = eventtime.parse("2026-11-01 02:00", "start")
check("02:00 happens once, on standard time: 07:00 UTC, not flagged",
      (eventtime.both(w.dt), w.ambiguous),
      ({"local": "Sun 2026-11-01 02:00 EST", "utc": "2026-11-01T07:00:00Z"}, False))
w = eventtime.parse("2026-11-01 00:59", "start")
check("00:59 is still EDT: 04:59 UTC", (eventtime.utc_iso(w.dt), w.ambiguous),
      ("2026-11-01T04:59:00Z", False))
w = eventtime.parse("2026-11-01 01:30", "start")
check("01:30 happens twice: read as the first pass (EDT, 05:30 UTC) and flagged",
      (eventtime.both(w.dt), w.ambiguous),
      ({"local": "Sun 2026-11-01 01:30 EDT", "utc": "2026-11-01T05:30:00Z"}, True))
check("...the preview says so, and how to get the second pass",
      any("repeat" in n and "-05:00" in n for n in eventtime.notes(w)), True)
check("the second pass, given as an offset, prints as EST",
      eventtime.local(eventtime.parse("2026-11-01T01:30-05:00", "s").dt),
      "Sun 2026-11-01 01:30 EST")
check("a naive time gets the 'read as Detroit time' note",
      any("read as Detroit time" in n for n in eventtime.notes(w)), True)
check("...a Z time doesn't", eventtime.notes(eventtime.parse("2026-11-01T05:30Z", "s")), [])

section("eventtime - the spring-forward gap is refused, not moved")
for gap in ("2026-03-08 02:30", "2027-03-14 02:00"):
    m = time_refusal(gap)
    check(f"{gap} never shows on a Detroit clock", "never happens on a Detroit clock"
          in (m or ""), True)
check("03:00 that morning is fine (EDT, 07:00 UTC)",
      eventtime.utc_iso(eventtime.parse("2026-03-08 03:00", "s").dt), "2026-03-08T07:00:00Z")

section("eventtime - what isn't a time is one sentence")
for bad, words in (("2026-10-31", "can't read"), ("tomorrow at 8", "can't read"),
                   ("", "empty"), ("2026-02-30 10:00", "isn't a real date"),
                   ("2026-10-31 25:00", "isn't a real date"),
                   ("8pm", "can't read")):
    m = time_refusal(bad)
    check(f"{bad!r} refused ({words})", words in (m or "") and "\n" not in (m or ""), True)
try:
    eventtime.parse("soon", "end")
    _m = None
except eventtime.EventTimeError as e:
    _m = str(e)
check("a refusal names the field it was for", (_m or "").startswith("end:"), True)

section("eventtime - the stand-in clock agrees with zoneinfo wherever both exist")
try:
    from zoneinfo import ZoneInfo
    _real = ZoneInfo("America/Detroit")
except Exception:  # noqa: BLE001
    _real = None
check(f"this machine runs {'the stand-in' if eventtime.FALLBACK else 'zoneinfo'}",
      eventtime.FALLBACK, _real is None)
if _real is None:
    print("  (no time-zone database here, so the stand-in is what runs; the hour-by-hour "
          "comparison runs on the Mac)")
else:
    _fake = eventtime._USEastern()
    _bad = []
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    while t < datetime(2030, 1, 1, tzinfo=timezone.utc):
        a, b = t.astimezone(_real), t.astimezone(_fake)
        if (a.replace(tzinfo=None), a.fold, a.utcoffset(), a.tzname()) != \
                (b.replace(tzinfo=None), b.fold, b.utcoffset(), b.tzname()):
            _bad.append(t.isoformat())
        t += timedelta(minutes=30)
    check("every half hour 2026-2029: same wall clock, fold, offset and name", _bad[:3], [])
    _bad = []
    for y in (2026, 2027, 2028, 2029):
        for m_, d in ((3, range(7, 16)), (11, range(1, 9))):
            for day in d:
                for h in range(0, 4):
                    for mi in (0, 30, 59):
                        for f in (0, 1):
                            n = datetime(y, m_, day, h, mi, fold=f)
                            if n.replace(tzinfo=_real).utcoffset() != \
                                    n.replace(tzinfo=_fake).utcoffset():
                                _bad.append(n.isoformat() + f" fold={f}")
    check("every wall-clock time near the transitions, both folds: same offset",
          _bad[:3], [])


print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
