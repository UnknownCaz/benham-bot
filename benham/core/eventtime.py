"""
eventtime.py - the clock behind a scheduled event's start and end (INTENT 60, Stage 8 B).

Caz's house rule is one clock everywhere he reads a time: the PC's local clock,
which is America/Detroit. The bot runs on a Mac whose own zone is NOT assumed,
so nothing here calls datetime.now() without a zone or .astimezone() with none.
Every naive time is read in Detroit explicitly, and every time printed carries
both halves - Detroit and UTC - so a wrong read shows in the preview before he
confirms it.

    parse("2026-10-31 20:00", "start")         naive -> Detroit (EDT that night)
    parse("2026-10-31T20:00Z", "start")        taken as written: UTC
    parse("2026-10-31T20:00-07:00", "start")   taken as written: that offset

THE FALL-BACK HOUR. 01:00-01:59 on the first Sunday of November happens twice
in Detroit (2026-11-01 is one). A naive time inside it is read as the FIRST
pass, still on daylight time (PEP 495's fold=0, what zoneinfo does), and the
parse says so in `ambiguous`, so the preview can flag it. 02:00 that morning
exists once, on standard time. A naive time in the spring-forward gap (02:00-
02:59 on the second Sunday of March) never shows on a Detroit clock at all, so
it is refused rather than silently moved an hour.

THE ZONE. zoneinfo's America/Detroit wherever the machine has a time-zone
database - the Mac does. Windows ships none and the PC has no tzdata package,
so there the US Eastern rule in force since 2007 stands in (_USEastern below);
tests/test_events.py checks it against zoneinfo hour by hour on any machine
that has both. Only the bot's handlers read event times, and they run on the
Mac, so the stand-in is what the PC's offline tests exercise.

Every failure is an EventTimeError whose message is one plain sentence.
"""

import re
from datetime import datetime, timedelta, timezone, tzinfo


class EventTimeError(Exception):
    """A plain-words reason a time can't be used. Never a traceback."""


class _USEastern(tzinfo):
    """America/Detroit by the US rule in force since 2007: daylight time from
    02:00 on the second Sunday of March to 02:00 on the first Sunday of
    November. Implements PEP 495's fold the way zoneinfo does - fold=0 takes
    the offset in force BEFORE a transition, in the gap and in the repeated
    hour alike. Wrong for dates before 2007 and for any future change to US
    law; it only runs where no time-zone database exists."""

    STD = timedelta(hours=-5)
    DST = timedelta(hours=-4)

    @staticmethod
    def _bounds(year):
        """Local wall-clock (naive) start and end of daylight time in `year`."""
        mar1 = datetime(year, 3, 1)
        start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=2)
        nov1 = datetime(year, 11, 1)
        end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=2)
        return start, end

    def utcoffset(self, dt):
        if dt is None:
            return self.STD
        wall = dt.replace(tzinfo=None, fold=0)
        start, end = self._bounds(wall.year)
        hour = timedelta(hours=1)
        if start <= wall < start + hour:            # the gap: never on the clock
            return self.STD if dt.fold == 0 else self.DST
        if end - hour <= wall < end:                # the repeated hour
            return self.DST if dt.fold == 0 else self.STD
        return self.DST if start <= wall < end else self.STD

    def dst(self, dt):
        return self.utcoffset(dt) - self.STD

    def tzname(self, dt):
        return "EDT" if self.utcoffset(dt) == self.DST else "EST"

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=None)
        start, end = self._bounds(utc.year)
        start_utc = start - self.STD                # 02:00 EST -> 07:00 UTC
        end_utc = end - self.DST                    # 02:00 EDT -> 06:00 UTC
        if start_utc <= utc < end_utc:
            return (utc + self.DST).replace(tzinfo=self, fold=0)
        fold = 1 if end_utc <= utc < end_utc + timedelta(hours=1) else 0
        return (utc + self.STD).replace(tzinfo=self, fold=fold)

    def __repr__(self):
        return "_USEastern()"


def _detroit():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Detroit")
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, or no zoneinfo at all
        return _USEastern()


DETROIT = _detroit()
ZONE_NAME = "America/Detroit"
FALLBACK = isinstance(DETROIT, _USEastern)

# ISO 8601 as a person types it: a date, a T or a space, HH:MM[:SS[.ffffff]],
# then optionally Z or +HH:MM / -HH:MM. A date with no time is refused on
# purpose - "2026-10-31" would quietly mean midnight.
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}(:\d{2}(\.\d{1,6})?)?"
                  r"(Z|[+-]\d{2}:?\d{2})?", re.IGNORECASE)


class When:
    """A parsed time: `dt` is aware (in UTC), `assumed_local` says the text
    carried no zone, `ambiguous` says it fell in the repeated autumn hour."""

    def __init__(self, dt, assumed_local, ambiguous):
        self.dt = dt
        self.assumed_local = assumed_local
        self.ambiguous = ambiguous


def parse(raw, field):
    """Text -> When, or EventTimeError naming `field`."""
    text = str(raw or "").strip()
    if not text:
        raise EventTimeError(f"{field} is empty - give a date and time like "
                             "2026-10-31 20:00.")
    if not _ISO.fullmatch(text):
        raise EventTimeError(
            f"{field}: I can't read {text!r} as a time - write it like 2026-10-31 20:00 "
            "(read as Detroit time), or with Z or an offset like 2026-11-01T00:00Z.")
    norm = text[:-1] + "+00:00" if text[-1] in "Zz" else text
    norm = norm[:10] + "T" + norm[11:]
    m = re.search(r"([+-])(\d{2})(\d{2})$", norm)
    if m and ":" not in norm[-5:]:
        norm = norm[:m.start()] + f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        dt = datetime.fromisoformat(norm)
    except ValueError:
        raise EventTimeError(f"{field}: {text!r} isn't a real date and time.")
    dt = dt.replace(microsecond=0)
    if dt.tzinfo is not None:
        return When(dt.astimezone(timezone.utc), False, False)

    local = dt.replace(tzinfo=DETROIT, fold=0)
    back = local.astimezone(timezone.utc).astimezone(DETROIT)
    if back.replace(tzinfo=None, fold=0) != dt:
        raise EventTimeError(
            f"{field}: {dt:%Y-%m-%d %H:%M} never happens on a Detroit clock - the "
            "clocks jump an hour forward that night. Pick a time outside that hour.")
    ambiguous = local.utcoffset() != local.replace(fold=1).utcoffset()
    return When(local.astimezone(timezone.utc), True, ambiguous)


def now():
    """The current instant, aware, in UTC."""
    return datetime.now(timezone.utc)


def local(dt):
    """`dt` on the Detroit clock, as a person reads it: 'Sat 2026-10-31 20:00 EDT'."""
    d = dt.astimezone(DETROIT)
    return f"{d:%a %Y-%m-%d %H:%M} {d.tzname()}"


def utc_iso(dt):
    """`dt` in UTC, ISO 8601 with a Z: '2026-11-01T00:00:00Z'."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def both(dt):
    """{"local": ..., "utc": ...} for a result field, or None for no time."""
    if dt is None:
        return None
    return {"local": local(dt), "utc": utc_iso(dt)}


def line(dt):
    """One preview line's worth: 'Sat 2026-10-31 20:00 EDT = 2026-11-01 00:00 UTC'."""
    if dt is None:
        return "not set"
    return f"{local(dt)} = {dt.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def notes(*whens):
    """The preview's plain warnings for the times a request gave."""
    out = []
    if any(w is not None and w.assumed_local for w in whens):
        out.append(f"No time zone was given, so the time is read as Detroit time "
                   f"({ZONE_NAME}); the UTC half is there to check it.")
    for w in whens:
        if w is not None and w.ambiguous:
            out.append(f"{local(w.dt)} falls in the hour the clocks repeat when "
                       "daylight time ends; it is read as the first pass (EDT). "
                       "Give an offset (-05:00) for the second.")
    return out
