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


# ======================================================================= stubs
#
# Everything above the HTTP layer is discord.py's own: the guild stub borrows
# Guild's real event methods, and every event is a real ScheduledEvent built
# from the JSON Discord would send. _Http stands in for Discord and records
# every request, so "Discord was never asked to change anything" is a fact
# about a list.

from benham.core import capabilities, lookimage, policy  # noqa: E402

OWNER = 273967061619965952
GUILD = 736988645562646619          # the Testing Server: on the tier-3 allowlist
WALLED = 8800009                    # a guild that is not
BOT = 8800001
CHAN = 8800002                      # a text channel; the attachment message lives here
MSG = 8800003
VOICE = 8800004
STAGE = 8800005
LOCKED = 8800006                    # a voice channel the bot can't join
NOW = eventtime.now()
SOON = NOW + timedelta(hours=2)     # a start that is safely in the future
_ids = iter(range(990001, 999999))


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png(w=800, h=320, pad=64):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", b"\x00" * pad)
            + _chunk(b"IEND", b""))


COVER = png(800, 320)


class _Resp:
    def __init__(self, status=400):
        self.status = status
        self.reason = "stub"


class _User:
    def __init__(self, data):
        self.id = int(data["id"])
        self.name = data["username"]

    def __str__(self):
        return self.name


def ev_data(name, start=None, *, kind=2, status=1, channel_id=VOICE, location=None,
            end=None, description=None, image=None, creator=None, user_count=None,
            eid=None):
    """One event as Discord's JSON has it."""
    start = start or SOON
    d = {"id": str(eid or next(_ids)), "guild_id": str(GUILD), "name": name,
         "entity_type": kind, "status": status, "privacy_level": 2,
         "scheduled_start_time": start.isoformat(),
         "scheduled_end_time": end.isoformat() if end else None,
         "channel_id": str(channel_id) if kind != 3 else None,
         "entity_metadata": {"location": location} if kind == 3 else None,
         "description": description, "image": image, "creator_id": str(BOT)}
    if creator:
        d["creator"] = {"id": "8800777", "username": creator, "discriminator": "0",
                        "avatar": None}
        d["creator_id"] = "8800777"
    if user_count is not None:
        d["user_count"] = user_count
    return d


class _Http:
    """Discord, as far as events go: a dict of event JSON, and a record."""

    def __init__(self):
        self.events = {}
        self.writes = []            # (verb, event_id or None, payload, reason)
        self.reads = []
        self.raise_on_write = None
        self.cdn = {}               # url -> bytes for cover reads
        self.cdn_fails = False

    async def get_scheduled_events(self, guild_id, with_user_count):
        self.reads.append(("list", with_user_count))
        return [dict(d) for d in self.events.values()]

    async def get_scheduled_event(self, guild_id, eid, with_user_count):
        self.reads.append(("get", int(eid)))
        if str(eid) not in self.events:
            raise discord.NotFound(_Resp(404), {"code": 10070,
                                                "message": "Unknown Guild Scheduled Event"})
        return dict(self.events[str(eid)])

    async def create_guild_scheduled_event(self, guild_id, *, reason=None, **payload):
        self.writes.append(("create", None, payload, reason))
        if self.raise_on_write is not None:
            raise self.raise_on_write
        meta = payload.get("entity_metadata")
        d = {"id": str(next(_ids)), "guild_id": str(guild_id), "name": payload["name"],
             "entity_type": payload.get("entity_type"), "status": 1,
             "privacy_level": payload.get("privacy_level"),
             "scheduled_start_time": payload["scheduled_start_time"],
             "scheduled_end_time": payload.get("scheduled_end_time"),
             "channel_id": payload.get("channel_id") and str(payload["channel_id"]),
             "entity_metadata": meta, "description": payload.get("description"),
             "image": "cov3r" if payload.get("image") else None,
             "creator_id": str(BOT)}
        self.events[d["id"]] = d
        return dict(d)

    async def edit_scheduled_event(self, guild_id, eid, *, reason=None, **payload):
        self.writes.append(("edit", int(eid), payload, reason))
        if self.raise_on_write is not None:
            raise self.raise_on_write
        d = self.events[str(eid)]
        for key in ("name", "description", "scheduled_start_time", "scheduled_end_time",
                    "status", "entity_metadata", "entity_type"):
            if key in payload:
                d[key] = payload[key]
        if "channel_id" in payload:
            d["channel_id"] = payload["channel_id"] and str(payload["channel_id"])
        if "image" in payload:
            d["image"] = "n3w" if payload["image"] else None
        return dict(d)

    async def get_from_cdn(self, url):
        if self.cdn_fails:
            raise discord.HTTPException(_Resp(500), "cdn hiccup")
        return self.cdn.get(url, COVER)


class _State:
    def __init__(self, guild):
        self.http = _Http()
        self._guild = guild

    def _get_guild(self, gid):
        return self._guild

    def store_user(self, data, **_):
        return _User(data)

    def get_user(self, uid):
        return None


class _Perms:
    def __init__(self, manage=True, create=True, view=True, connect=True):
        self.manage_events = manage
        self.create_events = create
        self.view_channel = view
        self.connect = connect


class _Me:
    def __init__(self, **kw):
        self.id = BOT
        self.guild_permissions = _Perms(**kw)


class _Att:
    def __init__(self, aid, filename, data):
        self.id = aid
        self.filename = filename
        self.data = data
        self.size = len(data)
        self.content_type = "application/octet-stream"

    async def read(self):
        return self.data


class _Msg:
    def __init__(self, atts):
        self.id = MSG
        self.attachments = list(atts)


class _Chan:
    def __init__(self, cid, name, ctype, perms=None, atts=()):
        self.id = cid
        self.name = name
        self.type = ctype
        self.guild = None
        self._perms = perms or _Perms()
        self._atts = list(atts)

    def permissions_for(self, member):
        return self._perms

    async def fetch_message(self, mid):
        return _Msg(self._atts)


class _Guild:
    # discord.py's own, so their keyword checks and payload building are real.
    fetch_scheduled_events = discord.Guild.fetch_scheduled_events
    fetch_scheduled_event = discord.Guild.fetch_scheduled_event
    create_scheduled_event = discord.Guild.create_scheduled_event

    def __init__(self, gid=GUILD, name="Testing Server", atts=(), **perms):
        self.id = gid
        self.name = name
        self.me = _Me(**perms)
        self._state = _State(self)
        self.channels = {
            CHAN: _Chan(CHAN, "asd", discord.ChannelType.text, atts=atts),
            VOICE: _Chan(VOICE, "Game Night", discord.ChannelType.voice),
            STAGE: _Chan(STAGE, "Town Hall", discord.ChannelType.stage_voice),
            LOCKED: _Chan(LOCKED, "Secret", discord.ChannelType.voice,
                          perms=_Perms(view=False, connect=False)),
        }

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    def add(self, *datas):
        for d in datas:
            d["guild_id"] = str(self.id)
            self._state.http.events[d["id"]] = d
        return [int(d["id"]) for d in datas]


class _Client:
    def __init__(self, guilds):
        self.guilds = {g.id: g for g in guilds}

    def get_guild(self, gid):
        return self.guilds.get(int(gid))

    def get_channel(self, cid):
        for g in self.guilds.values():
            ch = g.get_channel(cid)
            if ch is not None:
                return ch
        return None

    async def fetch_channel(self, cid):
        ch = self.get_channel(cid)
        if ch is None:
            raise discord.NotFound(_Resp(404), "Unknown Channel")
        return ch


def fresh(atts=(), **gkw):
    g = _Guild(atts=atts, **gkw)
    return _Client([g, _Guild(gid=WALLED, name="Elsewhere")]), g


def go(client, name, params, force=False):
    """capabilities.run as the owner in a DM -> (result, preview). Raises ActionError."""
    return asyncio.run(capabilities.run(
        client, lambda *_: None, name, params, actor_id=OWNER, force=force,
        call_ctx=policy.CallContext.owner_dm(OWNER, channel_id=CHAN)))


def refusal(client, name, params, force=True):
    try:
        go(client, name, params, force=force)
        return None
    except capabilities.ActionError as e:
        return str(e)


def backups():
    d = capabilities.LOOK_BACKUP_DIR
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


TMP = tempfile.mkdtemp(prefix="benham-events-")
atexit.register(shutil.rmtree, TMP, True)
capabilities.LOOK_BACKUP_DIR = os.path.join(TMP, "look_backups")


def registered(name, tier, outward, always_confirm, taints):
    a = capabilities.REGISTRY[name]
    check(f"{name}: tier {tier}, needs a guild", (a.tier, a.needs_guild), (tier, True))
    check(f"{name}: outward={outward}, always_confirm={always_confirm}, taints={taints}",
          (a.outward, a.always_confirm, a.taints), (outward, always_confirm, taints))
    check(f"{name}: never a guest capability, and a guest is refused",
          (a.guest, policy.authorize(a, policy.CallContext.guest_dm(111, 5)).allowed),
          (False, False))


# ================================================================= list_events
section("list_events - a read that taints (names and descriptions are other people's)")
registered("list_events", 0, False, False, True)
cl, g = fresh()
late = NOW + timedelta(days=3)
g.add(ev_data("Movie night", late, kind=3, location="Doom's place",
              end=late + timedelta(hours=3), description="bring snacks", image="abc123",
              creator="draco", user_count=4),
      ev_data("Among Us", SOON, status=2, user_count=2))
result, preview = go(cl, "list_events", {"guild_id": GUILD})
check("no preview for a read", preview, None)
check("fetched from Discord with the interested counts, not read from a cache",
      g._state.http.reads, [("list", True)])
check("count, open count and Discord's cap", (result["count"], result["open"],
                                              result["limit"]), (2, 2, 100))
first, second = result["events"]
check("soonest first", (first["name"], second["name"]), ("Among Us", "Movie night"))
check("a voice event: status, kind and its channel by name and id",
      (first["status"], first["kind"], first["where"]),
      ("active", "voice", {"channel": "Game Night", "channel_id": VOICE}))
check("an external event: its location, start and end in Detroit time and UTC",
      (second["kind"], second["where"], second["start"], second["end"]),
      ("external", {"location": "Doom's place"}, eventtime.both(late),
       eventtime.both(late + timedelta(hours=3))))
check("...the local half is Detroit's clock, the utc half ends in Z",
      second["start"]["local"].endswith((" EDT", " EST"))
      and second["start"]["utc"].endswith("Z"), True)
check("...creator by name only, interested count, description, cover link, event link",
      (second["creator"], second["user_count"], second["description"],
       second["cover"].startswith("https://cdn.discordapp.com/guild-events/"),
       second["url"].startswith(f"https://discord.com/events/{GUILD}/")),
      ("draco", 4, "bring snacks", True, True))
check("no end and no cover read as None", (first["end"], first["cover"]), (None, None))
check("...and nothing was written", g._state.http.writes, [])
cl, g = fresh()
g.add(ev_data("old", status=3), ev_data("gone", status=4), ev_data("next"))
result, _ = go(cl, "list_events", {"guild_id": GUILD})
check("completed and cancelled say so, in this file's spelling, and aren't open",
      ([e["status"] for e in result["events"]], result["open"]),
      (["completed", "cancelled", "scheduled"], 1))
cl, g = fresh()
check("an empty server is an empty list", go(cl, "list_events", {"guild_id": GUILD})[0]
      ["events"], [])


print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
