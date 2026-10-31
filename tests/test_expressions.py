"""
test_expressions.py - emoji, stickers and soundboard sounds, offline (INTENT 60, Stage 8 A).

Seven capabilities give the server's Emoji, Stickers and Soundboard screens to a
prompt: create_emoji, list_stickers, create_sticker, delete_sticker, list_sounds,
create_sound, delete_sound. One module (looksound.py) turns "which sound?" into
checked bytes, beside lookimage.py for pictures. What is pinned:

  THE POSTURE IS edit_server_look's. Creates are tier 2, outward, always
  confirmed; deletes are tier 3 (allowlist, dry-run, token); lists are reads
  that taint. A guest reaches none of them.

  DISCORD'S LIMITS ARE CHECKED AT THE PREVIEW. Size, type, dimensions, length,
  name and free slots, from the bytes and the guild's own counts. A request that
  breaks one is refused in one plain sentence and Discord is never called - not
  at the preview, not at the confirm.

  THE PREVIEW TOUCHES NOTHING. The first call of a create parks a preview (type,
  size, dimensions or length, hash, source, name, slots left); the first call of
  a delete is a dry run naming what goes. Nothing is uploaded, deleted or saved.

  A DELETE KEEPS A COPY. The sticker's picture or the sound's file is saved to
  look_backups before Discord is asked, byte for byte - and when the copy cannot
  be made, the delete does not happen.

  THE SOUND IS CHECKED BY ITS BYTES. MP3 (Layer III frames, after any ID3 tag) or
  OGG, never the name; its length measured from the frames or the last Ogg page.
  The link route is lookimage's fence: https only, public hosts only, a size cap
  that holds when Content-Length lies - run against a real local aiohttp server.

Everything runs against stubs; the backup folder is a temp directory.

    python test_expressions.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import atexit
import logging
import os
import shutil
import struct
import sys
import tempfile
import zlib

import discord
from aiohttp import web

# The cap test makes the client hang up mid-body on purpose; aiohttp's server
# would log that as an error traceback and bury the PASS lines.
logging.getLogger("aiohttp.server").setLevel(logging.CRITICAL)

from benham.core import capabilities, lookimage, policy

OWNER = 273967061619965952
GUILD = 736988645562646619          # the Testing Server: on the tier-3 allowlist
WALLED = 8800009                    # a guild that is not
CHAN = 8800002
MSG = 8800003
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "expressions")
_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!a}\n        want {want!a}")     # ascii: emoji in a pipe
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# ----------------------------------------------------------------- picture bytes

def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png(w=320, h=320, pad=64, apng=False):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    body = _chunk(b"IHDR", ihdr)
    if apng:
        body += _chunk(b"acTL", struct.pack(">II", 2, 0))
    body += _chunk(b"IDAT", b"\x00" * pad) + _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


def jpeg(w=320, h=320):
    return (b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", h, w)
            + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01" + b"\x00" * 32)


def gif(w=128, h=128):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 32


def webp_x(w=128, h=128):
    return (b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8X" + struct.pack("<I", 10)
            + b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little")
            + (h - 1).to_bytes(3, "little") + b"\x00" * 16)


# ------------------------------------------------------------------- sound bytes
#
# A Layer III frame at 128 kb/s, 44.1 kHz: header FF FB 90 00, 417 bytes, 1152
# samples. Real enough for the frame walk; the fixtures below are real encoder
# output for what a hand-built frame cannot show (an ID3 tag, a LAME Info frame).

_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413


def mp3(seconds=1.0, id3=False):
    """Whole frames, at least `seconds` long (one frame is 26 ms)."""
    n = max(1, -(-int(seconds * 44100) // 1152))
    body = _FRAME * n
    if id3:
        tag = b"TIT2\x00\x00\x00\x05\x00\x00\x03hey\x00"
        size = len(tag)
        safe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F,
                      size & 0x7F])
        body = b"ID3\x04\x00\x00" + safe + tag + body
    return body


def _page(granule, serial=b"\x01\x02\x03\x04", seq=0, packet=b""):
    return (b"OggS\x00" + (b"\x02" if seq == 0 else b"\x04")
            + struct.pack("<q", granule) + serial + struct.pack("<I", seq)
            + b"\x00\x00\x00\x00" + bytes([1, len(packet)]) + packet)


def ogg_vorbis(seconds=1.0, rate=44100):
    ident = (b"\x01vorbis" + struct.pack("<I", 0) + b"\x01" + struct.pack("<I", rate)
             + b"\x00" * 13 + b"\x01")
    return _page(0, packet=ident) + b"\x00" * 200 + _page(int(seconds * rate), seq=1)


def ogg_opus(seconds=1.0, preskip=312):
    head = b"OpusHead\x01\x01" + struct.pack("<H", preskip) + struct.pack("<I", 48000) \
        + b"\x00\x00\x00"
    return _page(0, packet=head) + b"\x00" * 200 \
        + _page(int(seconds * 48000) + preskip, seq=1)


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


# ----------------------------------------------------------------------- stubs

class _Resp:
    def __init__(self, status=400):
        self.status = status
        self.reason = "stub"


class _Att:
    def __init__(self, aid, filename, data, size=None, fail=False):
        self.id = aid
        self.filename = filename
        self.data = data
        self.size = len(data) if size is None else size
        self.content_type = "application/octet-stream"   # the header is not the fact
        self.fail = fail

    async def read(self):
        if self.fail:
            raise discord.NotFound(_Resp(404), "Unknown Attachment")
        return self.data


class _Msg:
    def __init__(self, atts):
        self.id = MSG
        self.attachments = list(atts)


class _Chan:
    def __init__(self, atts):
        self.id = CHAN
        self.guild = None
        self._atts = atts

    async def fetch_message(self, mid):
        return _Msg(self._atts)


class _Perms:
    def __init__(self, create=True, manage=True):
        self.create_expressions = create
        self.manage_expressions = manage


class _Me:
    def __init__(self, **kw):
        self.guild_permissions = _Perms(**kw)


class _Role:
    def __init__(self, rid, name):
        self.id = rid
        self.name = name


class _Emoji:
    def __init__(self, eid, name, animated=False):
        self.id = eid
        self.name = name
        self.animated = animated

    def __str__(self):
        return f"<{'a' if self.animated else ''}:{self.name}:{self.id}>"


class _Fmt:
    def __init__(self, name):
        self.name = name


class _Item:
    """A sticker or a sound: something with bytes behind a url, that can be deleted."""

    def __init__(self, iid, name, data=b"", fail_read=False):
        self.id = iid
        self.name = name
        self.data = data
        self.fail_read = fail_read
        self.deleted = []

    async def read(self):
        if self.fail_read:
            raise discord.HTTPException(_Resp(500), "cdn hiccup")
        return self.data

    async def delete(self, reason=None):
        self.deleted.append(reason)


class _Sticker(_Item):
    def __init__(self, sid, name, data=b"", fmt="png", description="", emoji="",
                 **kw):
        super().__init__(sid, name, data, **kw)
        self.format = _Fmt(fmt)
        self.description = description
        self.emoji = emoji
        self.available = True
        self.url = f"https://media.discordapp.net/stickers/{sid}.png"


class _Sound(_Item):
    def __init__(self, sid, name, data=b"", volume=1.0, emoji=None, **kw):
        super().__init__(sid, name, data, **kw)
        self.volume = volume
        self.emoji = emoji
        self.available = True
        self.url = f"https://cdn.discordapp.com/soundboard-sounds/{sid}"


class _Http:
    def __init__(self, guild):
        self.guild = guild
        self.calls = []
        self.raise_on_create = None

    async def create_soundboard_sound(self, guild_id, *, reason, **payload):
        self.calls.append({"guild_id": guild_id, "reason": reason, **payload})
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return {"sound_id": "990001", "name": payload["name"],
                "volume": payload["volume"], "emoji_id": payload.get("emoji_id"),
                "emoji_name": payload.get("emoji_name"), "available": True}


class _State:
    def __init__(self, guild):
        self.http = _Http(guild)


class _Guild:
    def __init__(self, gid=GUILD, name="Testing Server", tier=0, emojis=(),
                 stickers=(), sounds=(), emoji_limit=50, sticker_limit=5, **perms):
        self.id = gid
        self.name = name
        self.premium_tier = tier
        self.features = []
        self.me = _Me(**perms)
        self.emojis = list(emojis)
        self.stickers = list(stickers)
        self.sounds = list(sounds)
        self.emoji_limit = emoji_limit
        self.sticker_limit = sticker_limit
        self.roles = [_Role(501, "mods"), _Role(502, "regulars")]
        self.calls = []
        self.raise_on_create = None
        self._state = _State(self)

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == int(rid)), None)

    async def create_custom_emoji(self, **kw):
        self.calls.append(("emoji", kw))
        if self.raise_on_create is not None:
            raise self.raise_on_create
        e = _Emoji(770001, kw["name"], animated=kw["image"][:3] == b"GIF")
        self.emojis.append(e)
        return e

    async def create_sticker(self, **kw):
        kw = dict(kw)
        kw["file_bytes"] = kw.pop("file").fp.read()
        self.calls.append(("sticker", kw))
        if self.raise_on_create is not None:
            raise self.raise_on_create
        s = _Sticker(880001, kw["name"], kw["file_bytes"],
                     description=kw["description"], emoji=kw["emoji"])
        self.stickers.append(s)
        return s

    async def fetch_sticker(self, sid):
        raise discord.NotFound(_Resp(404), "Unknown Sticker")

    async def fetch_soundboard_sounds(self):
        return list(self.sounds)

    async def fetch_soundboard_sound(self, sid):
        for s in self.sounds:
            if s.id == int(sid):
                return s
        raise discord.NotFound(_Resp(404), "Unknown Sound")


class _Client:
    def __init__(self, guilds, atts=()):
        self.guilds = {g.id: g for g in guilds}
        self.chan = _Chan(list(atts))

    def get_guild(self, gid):
        return self.guilds.get(int(gid))

    def get_channel(self, cid):
        return self.chan if int(cid) == CHAN else None

    async def fetch_channel(self, cid):
        ch = self.get_channel(cid)
        if ch is None:
            raise discord.NotFound(_Resp(404), "Unknown Channel")
        return ch


def fresh(atts=(), **gkw):
    g = _Guild(**gkw)
    return _Client([g, _Guild(gid=WALLED, name="Elsewhere")], atts), g


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


TMP = tempfile.mkdtemp(prefix="benham-expr-")
atexit.register(shutil.rmtree, TMP, True)
capabilities.LOOK_BACKUP_DIR = os.path.join(TMP, "look_backups")
ATT = {"channel_id": CHAN, "message_id": MSG}


def registered(name, tier, outward, always_confirm, taints):
    a = capabilities.REGISTRY[name]
    check(f"{name}: tier {tier}", a.tier, tier)
    check(f"{name}: outward={outward}, always_confirm={always_confirm}, taints={taints}",
          (a.outward, a.always_confirm, a.taints), (outward, always_confirm, taints))
    check(f"{name}: never a guest capability, and a guest is refused",
          (a.guest, policy.authorize(a, policy.CallContext.guest_dm(111, 5)).allowed),
          (False, False))


# ================================================================ create_emoji
section("create_emoji - registered like edit_server_look")
registered("create_emoji", 2, True, True, True)

section("create_emoji - previewed first, nothing touched")
cl, g = fresh([_Att(1, "wave.png", png(128, 128))], emojis=[_Emoji(1, "old")])
result, preview = go(cl, "create_emoji",
                     {"guild_id": GUILD, "name": "wave", "image": "attachment", **ATT})
check("the first call is a preview, not a result", (result, preview is not None),
      (None, True))
check("...Discord was never asked", g.calls, [])
check("...the summary names the emoji and the server",
      ":wave:" in preview["summary"] and "Testing Server" in preview["summary"], True)
check("...the detail gives type, dimensions, size, source and hash",
      all(w in preview["detail"] for w in ("PNG, 128x128", "KB", "wave.png", "#")), True)
check("...and the slots left after it (1 of 50 used -> 48 left)",
      "1 of 50 static emoji in use; 48 left" in preview["detail"], True)
check("...and who can use it", "Everyone in the server can use it" in preview["detail"],
      True)

section("create_emoji - confirmed: Discord gets the bytes, the name and a reason")
cl, g = fresh([_Att(1, "wave.png", png(128, 128))])
result, preview = go(cl, "create_emoji",
                     {"guild_id": GUILD, "name": "wave", "image": "attachment", **ATT,
                      "roles": [501]}, force=True)
kind, kw = g.calls[0]
check("one create call, with the picture's own bytes",
      (len(g.calls), kind, kw["image"] == png(128, 128)), (1, "emoji", True))
check("...the name, the roles and the audit reason",
      (kw["name"], [r.name for r in kw["roles"]], kw["reason"]),
      ("wave", ["mods"], capabilities.VIA))
check("the result says what was made and the slots left",
      (result["status"], result["emoji"]["name"], result["roles"], result["slots_left"]),
      ("emoji_created", "wave", ["mods"], 49))
cl, g = fresh([_Att(1, "w.png", png())])
go(cl, "create_emoji", {"guild_id": GUILD, "name": "wave", "image": "attachment", **ATT,
                        "reason": "for game night"}, force=True)
check("a typed reason reaches the audit log instead of the default",
      g.calls[0][1]["reason"], "for game night")

section("create_emoji - an asset works with no message at all")
cl, g = fresh()
result, _ = go(cl, "create_emoji", {"guild_id": GUILD, "name": "benham",
                                    "image": "asset:benham-discord-avatar.png"}, force=True)
check("the assets/ picture is sent as its own bytes",
      g.calls[0][1]["image"] == open(os.path.join(lookimage.ASSETS_DIR,
                                                  "benham-discord-avatar.png"), "rb").read(),
      True)

section("create_emoji - a GIF is animated and counts against the animated pool")
cl, g = fresh([_Att(1, "spin.gif", gif())],
              emojis=[_Emoji(i, f"s{i}") for i in range(50)])
_, preview = go(cl, "create_emoji", {"guild_id": GUILD, "name": "spin",
                                     "image": "attachment", **ATT})
check("50 static emoji don't block an animated one",
      "0 of 50 animated emoji in use" in preview["detail"]
      and "animated emoji" in preview["detail"], True)

section("create_emoji - refused BEFORE anything changes, at the preview too")
def emoji_refused(params, atts=None, force=True, **gkw):
    cl, g = fresh(atts if atts is not None else [_Att(1, "p.png", png(128, 128))], **gkw)
    msg = refusal(cl, "create_emoji", {"guild_id": GUILD, "name": "ok_name",
                                       "image": "attachment", **ATT, **params}, force=force)
    return msg, g.calls

msg, calls = emoji_refused({}, atts=[_Att(1, "big.png", png(pad=300 * 1024))])
check("over 256 KB -> says the size and the cap",
      "256 KB" in (msg or "") and "shrink" in (msg or "") and calls == [], True)
msg, _ = emoji_refused({}, atts=[_Att(1, "big.png", png(pad=300 * 1024))], force=False)
check("...and the same refusal comes at the preview", "256 KB" in (msg or ""), True)
msg, calls = emoji_refused({}, atts=[_Att(1, "p.webp", webp_x())])
check("a WebP is refused by type", "PNG, JPEG or GIF" in (msg or "")
      and "WEBP" in (msg or "") and calls == [], True)
msg, calls = emoji_refused({}, atts=[_Att(1, "cat.png", b"just words, not a picture")])
check("words named .png are refused by their bytes",
      "isn't a PNG, JPEG, GIF or WebP" in (msg or "") and calls == [], True)
for bad in ("x", "has space", "colon:in", "a" * 33, "émoji"):
    msg, calls = emoji_refused({"name": bad})
    check(f"name {bad!a} refused", "2 to 32 letters" in (msg or "") and calls == [], True)
check("surrounding colons are forgiven (:wave: is wave)",
      emoji_refused({"name": ":wave:"})[0], None)
msg, calls = emoji_refused({}, emojis=[_Emoji(i, f"e{i}") for i in range(50)])
check("no free static slot -> says how many are used",
      "no free static emoji slot (50 of 50 in use)" in (msg or "") and calls == [], True)
msg, calls = emoji_refused({"roles": [999]})
check("an unknown role id is refused", "no role 999" in (msg or "") and calls == [], True)
msg, calls = emoji_refused({}, create=False, manage=False)
check("no Create Expressions permission -> says which",
      "lacks Create Expressions" in (msg or "") and calls == [], True)
msg, calls = emoji_refused({"image": "https://127.0.0.1/a.png"})
check("a link to a private address is refused", "private or local" in (msg or "")
      and calls == [], True)
msg, calls = emoji_refused({"image": "asset:../INTENT.md"})
check("an asset path that escapes is refused", "outside" in (msg or ""), True)
msg, calls = emoji_refused({"channel_id": None, "message_id": None})
check("an attachment with no message named asks for the ids",
      "channel_id and message_id" in (msg or ""), True)

section("create_emoji - Discord's own refusal is one sentence")
cl, g = fresh([_Att(1, "p.png", png(128, 128))])
g.raise_on_create = discord.HTTPException(
    _Resp(400), {"code": 30008, "message": "Maximum number of emojis reached (50)"})
m = refusal(cl, "create_emoji", {"guild_id": GUILD, "name": "late", "image": "attachment",
                                 **ATT})
check("a full server at the confirm keeps Discord's words and says nothing changed",
      "Maximum number of emojis reached" in (m or "") and "Nothing was changed" in (m or "")
      and "\n" not in (m or ""), True)
g.raise_on_create = discord.HTTPException(_Resp(429), "You are being rate limited.")
m = refusal(cl, "create_emoji", {"guild_id": GUILD, "name": "late", "image": "attachment",
                                 **ATT})
check("a rate limit is the rate-limit sentence", "rate-limiting" in (m or "")
      and "Nothing was changed" in (m or ""), True)
g.raise_on_create = discord.Forbidden(_Resp(403), "Missing Permissions")
m = refusal(cl, "create_emoji", {"guild_id": GUILD, "name": "late", "image": "attachment",
                                 **ATT})
check("Forbidden is run()'s permission sentence", "lacks the permission" in (m or ""), True)

# =============================================================== list_stickers
section("list_stickers - a read that taints (names and descriptions are other people's)")
registered("list_stickers", 0, False, False, True)
cl, g = fresh(stickers=[_Sticker(11, "hello", fmt="png", description="says hi",
                                 emoji="\U0001F44B"),
                        _Sticker(12, "dance", fmt="lottie")], sticker_limit=5)
result, preview = go(cl, "list_stickers", {"guild_id": GUILD})
check("no preview for a read", preview, None)
check("count and the server's sticker slots", (result["count"], result["limit"]), (2, 5))
check("each sticker: id, name, description, related emoji, format, link",
      result["stickers"][0], {"id": 11, "name": "hello", "format": "png",
                              "url": "https://media.discordapp.net/stickers/11.png",
                              "description": "says hi", "emoji": "\U0001F44B",
                              "available": True})
check("a Lottie one says so (it can't be downloaded)", result["stickers"][1]["format"],
      "lottie")

# ============================================================== create_sticker
section("create_sticker - registered like edit_server_look")
registered("create_sticker", 2, True, True, True)
WAVE = "\U0001F44B"
STK = {"guild_id": GUILD, "name": "hello there", "emoji": WAVE, "image": "attachment",
       **ATT}

section("create_sticker - previewed first, nothing touched")
cl, g = fresh([_Att(1, "hi.png", png(320, 320))], stickers=[_Sticker(1, "old")])
result, preview = go(cl, "create_sticker", {**STK, "description": "says hi"})
check("the first call is a preview, Discord never asked", (result, g.calls), (None, []))
check("...names the sticker and the server",
      "hello there" in preview["summary"] and "Testing Server" in preview["summary"], True)
check("...type, dimensions, size, source, hash",
      all(w in preview["detail"] for w in ("PNG, 320x320", "hi.png", "#")), True)
check("...the related emoji and the description",
      WAVE in preview["detail"] and "'says hi'" in preview["detail"], True)
check("...and the slots (1 of 5 used -> 3 left)",
      "1 of 5 stickers in use; 3 left" in preview["detail"], True)
cl, g = fresh([_Att(1, "hi.png", png(320, 320, apng=True))])
_, preview = go(cl, "create_sticker", STK)
check("an animated PNG is named as one", "APNG (animated), 320x320" in preview["detail"],
      True)

section("create_sticker - confirmed: a File with the PNG's bytes")
cl, g = fresh([_Att(1, "hi.png", png(320, 320))])
result, _ = go(cl, "create_sticker", {**STK, "description": "says hi"}, force=True)
kind, kw = g.calls[0]
check("one create call, the bytes inside the File",
      (len(g.calls), kind, kw["file_bytes"] == png(320, 320)), (1, "sticker", True))
check("...name, description, emoji and reason",
      (kw["name"], kw["description"], kw["emoji"], kw["reason"]),
      ("hello there", "says hi", WAVE, capabilities.VIA))
check("the result names it and the slots left",
      (result["status"], result["sticker"]["name"], result["slots_left"]),
      ("sticker_created", "hello there", 4))

section("create_sticker - refused BEFORE anything changes")
def sticker_refused(params, atts=None, force=True, **gkw):
    cl, g = fresh(atts if atts is not None else [_Att(1, "p.png", png(320, 320))], **gkw)
    msg = refusal(cl, "create_sticker", {**STK, **params}, force=force)
    return msg, g.calls

msg, calls = sticker_refused({}, atts=[_Att(1, "p.png", png(512, 512))])
check("not 320x320 -> says the size it got",
      "exactly 320x320" in (msg or "") and "512x512" in (msg or "") and calls == [], True)
msg, _ = sticker_refused({}, atts=[_Att(1, "p.png", png(512, 512))], force=False)
check("...at the preview too", "512x512" in (msg or ""), True)
msg, calls = sticker_refused({}, atts=[_Att(1, "p.jpg", jpeg(320, 320))])
check("a JPEG is refused by type", "PNG or APNG" in (msg or "") and "JPEG" in (msg or "")
      and calls == [], True)
msg, calls = sticker_refused({}, atts=[_Att(1, "p.gif", gif(320, 320))])
check("a GIF is refused by type", "PNG or APNG" in (msg or "") and calls == [], True)
msg, calls = sticker_refused({}, atts=[_Att(1, "p.png", png(320, 320, pad=600 * 1024))])
check("over 512 KB -> the size and the cap", "512 KB" in (msg or "") and calls == [], True)
msg, calls = sticker_refused({}, atts=[_Att(1, "p.png", b"\x89PNG\r\n\x1a\n")])
check("a PNG whose size can't be read is refused, not guessed",
      "couldn't read that PNG's size" in (msg or "") and calls == [], True)
msg, calls = sticker_refused({}, stickers=[_Sticker(i, f"s{i}") for i in range(5)])
check("no free sticker slot -> says how many are used",
      "no free sticker slot (5 of 5 in use)" in (msg or "") and calls == [], True)
for bad in ("x", "y" * 31):
    msg, calls = sticker_refused({"name": bad})
    check(f"name of {len(bad)} characters refused", "2 to 30 characters" in (msg or "")
          and calls == [], True)
msg, calls = sticker_refused({"description": "y" * 101})
check("a 101-character description refused", "2 to 100" in (msg or "") and calls == [],
      True)
for bad in ("smile", "my name", ":smile:", "<:wave:123456789012345678>", WAVE + " hi",
            "名", ""):
    msg, calls = sticker_refused({"emoji": bad})
    check(f"related emoji {bad!a} refused (words can't ride in the tags field)",
          msg is not None and calls == [], True)
for good in ("\U0001F600", "❤️", "1️⃣", "\U0001F44D\U0001F3FD",
             "\U0001F1FA\U0001F1F8"):
    check(f"related emoji {good!a} accepted", capabilities._unicode_emoji(good), good)
msg, calls = sticker_refused({}, create=False, manage=False)
check("no Create Expressions permission -> says which",
      "lacks Create Expressions" in (msg or "") and calls == [], True)

section("create_sticker - Discord's own refusal is one sentence")
cl, g = fresh([_Att(1, "p.png", png(320, 320))])
g.raise_on_create = discord.HTTPException(
    _Resp(400), {"code": 50045, "message": "Invalid Form Body",
                 "errors": {"file": {"_errors": [{"code": "BINARY_TYPE_MAX_SIZE",
                                                  "message": "File too large"}]}}})
m = refusal(cl, "create_sticker", STK)
check("a multi-line form error is flattened to one line ending 'Nothing was changed.'",
      "\n" not in (m or "") and "File too large" in (m or "")
      and (m or "").endswith("Nothing was changed."), True)

# ============================================================== delete_sticker
section("delete_sticker - tier 3 like delete_emoji")
registered("delete_sticker", 3, False, False, False)
check("delete_sticker: needs the confirm round trip",
      capabilities.REGISTRY["delete_sticker"].needs_confirm, True)

section("delete_sticker - the dry run names it and touches nothing")
cl, g = fresh(stickers=[_Sticker(21, "hello", png(320, 320), description="says hi",
                                 emoji="\U0001F44B")])
result, preview = go(cl, "delete_sticker", {"guild_id": GUILD, "sticker_id": 21})
check("the first call is a dry run", (result, preview is not None), (None, True))
check("...nothing deleted, nothing saved", (g.stickers[0].deleted, backups()), ([], []))
check("...it names the sticker, the server, its link and the copy",
      "hello" in preview["summary"] and "Testing Server" in preview["summary"]
      and "stickers/21.png" in preview["detail"] and "saved" in preview["detail"], True)

section("delete_sticker - confirmed: a copy first, byte for byte, then the delete")
result, _ = go(cl, "delete_sticker", {"guild_id": GUILD, "sticker_id": 21}, force=True)
check("deleted once, with the audit reason", g.stickers[0].deleted, [capabilities.VIA])
saved = result["copy_saved"]
check("the copy exists and is the sticker's own bytes",
      bool(saved) and open(saved, "rb").read() == png(320, 320), True)
check("...named for the guild, the sticker id and a stamp, as a .png",
      os.path.basename(saved).startswith(f"{GUILD}-sticker-21-") and saved.endswith(".png"),
      True)

section("delete_sticker - no copy, no delete")
cl, g = fresh(stickers=[_Sticker(22, "flaky", png(), fail_read=True)])
m = refusal(cl, "delete_sticker", {"guild_id": GUILD, "sticker_id": 22})
check("a copy that can't be made stops the delete, in one sentence",
      "couldn't save a copy" in (m or "") and "Nothing was changed" in (m or "")
      and g.stickers[0].deleted == [], True)
cl, g = fresh(stickers=[_Sticker(23, "dance", fmt="lottie")])
_, preview = go(cl, "delete_sticker", {"guild_id": GUILD, "sticker_id": 23})
check("a Lottie sticker's preview says no copy can be kept",
      "not recoverable" in preview["detail"], True)
result, _ = go(cl, "delete_sticker", {"guild_id": GUILD, "sticker_id": 23}, force=True)
check("...and it deletes with copy_saved None", (g.stickers[0].deleted != [],
                                                 result["copy_saved"]), (True, None))

section("delete_sticker - the gates")
m = refusal(cl, "delete_sticker", {"guild_id": GUILD, "sticker_id": 99}, force=False)
check("an unknown sticker is a sentence", "no sticker 99" in (m or ""), True)
cl, g = fresh()
m = refusal(cl, "delete_sticker", {"guild_id": WALLED, "sticker_id": 21}, force=False)
check("a guild off the destructive allowlist is refused outright",
      "destructive_guilds" in (m or ""), True)

# =================================================================== looksound
from benham.core import looksound  # noqa: E402


def sreason(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except looksound.LookSoundError as e:
        return str(e)


section("looksound - the type comes from the BYTES")
check("hand-built Layer III frames are an MP3", looksound.sniff(mp3(1.0)), "audio/mpeg")
check("...after an ID3 tag too", looksound.sniff(mp3(1.0, id3=True)), "audio/mpeg")
check("an Ogg page is an OGG", looksound.sniff(ogg_vorbis()), "audio/ogg")
check("a real LAME mp3 (ID3 + Info frame) is an MP3",
      looksound.sniff(fixture("tone1.mp3")), "audio/mpeg")
check("a real Opus-in-Ogg is an OGG", looksound.sniff(fixture("tone1-opus.ogg")),
      "audio/ogg")
check("a PNG is not a sound", looksound.sniff(png()), None)
check("an ID3 tag with no MP3 behind it is not a sound",
      looksound.sniff(b"ID3\x04\x00\x00\x00\x00\x00\x02xx" + b"RIFFjunk" * 8), None)
check("text named .mp3 is refused by its bytes",
      "isn't an MP3 or OGG" in (sreason(looksound.check, b"hello world", "x.mp3") or ""),
      True)
check("empty is refused", "empty" in (sreason(looksound.check, b"", "x.mp3") or ""), True)

section("looksound - the length is measured from the bytes")
def secs(raw):
    return round(looksound.seconds(raw, looksound.sniff(raw)), 2)

check("1 s of frames measures 1.0 s, to the frame", secs(mp3(1.0)), 1.02)
check("...with an ID3 tag in front", secs(mp3(1.0, id3=True)), 1.02)
check("the real LAME mp3: its Info frame is not counted as sound",
      secs(fixture("tone1.mp3")), 1.04)
check("Vorbis: the last page's position over the stated rate", secs(ogg_vorbis(2.5)), 2.5)
check("Opus: 48 kHz less the pre-skip", secs(ogg_opus(1.5)), 1.5)
check("the real Opus file measures 1.0 s", secs(fixture("tone1-opus.ogg")), 1.0)
check("frames that stop making sense halfway: length unknown, not a guess",
      looksound.seconds(mp3(1.0) + b"\x01\x02junk" * 400 + mp3(4.0), "audio/mpeg"), None)
check("an ID3v1 tag at the end is fine",
      round(looksound.seconds(mp3(1.0) + b"TAG" + b"\x00" * 125, "audio/mpeg"), 2), 1.02)
check("an Ogg holding another codec: length unknown",
      looksound.seconds(b"OggS\x00\x02" + b"\x00" * 20 + b"\x01\x10" + b"fLaC" + b"\x00" * 12,
                        "audio/ogg"), None)
check("over 5.2 s is refused, saying how long it is",
      "6.01 seconds long" in (sreason(looksound.check, mp3(6.0), "long.mp3") or "")
      and "5.2" in (sreason(looksound.check, mp3(6.0), "long.mp3") or ""), True)
check("5.0 s passes", looksound.check(mp3(5.0), "ok.mp3").seconds is not None, True)
check("over 512 KB is refused with both sizes in the sentence",
      "512 KB" in (sreason(looksound.check, mp3(1.0) + b"\x00" * 600000, "big.mp3") or ""),
      True)
check("a good sound describes itself",
      looksound.check(mp3(1.0), "attachment a.mp3").describe().startswith("MP3, 1.02 s, "),
      True)

section("looksound - the source grammar")
async def _no_message():
    raise looksound.LookSoundError("no message named")

def sload(spec, getter=_no_message):
    return asyncio.run(looksound.load(spec, getter, "sound"))

async def _two():
    return _Msg([_Att(1, "a.mp3", mp3()), _Att(2, "b.ogg", ogg_vorbis())])

check("attachment:1 picks the second, typed by its bytes",
      (sload("attachment:1", _two).media, sload("attachment:1", _two).source),
      ("audio/ogg", "attachment b.ogg"))
check("two attachments and no index asks which",
      "which" in (sreason(sload, "attachment", _two) or ""), True)
async def _huge():
    return _Msg([_Att(1, "x.mp3", mp3(), size=9 * 1048576)])

check("an attachment over the cap is refused before downloading",
      "9.0 MB" in (sreason(sload, "attachment", _huge) or ""), True)
check("asset: is confined to assets/",
      "outside" in (sreason(looksound.from_asset, "../INTENT.md") or ""), True)
check("a missing asset is a sentence",
      "no sound called" in (sreason(looksound.from_asset, "nope.mp3") or ""), True)
check("a nonsense source says what it can be",
      "https://" in (sreason(sload, "banana") or ""), True)

section("looksound - the URL route is lookimage's fence, in sound words")
def svet(url):
    return sreason(lambda: asyncio.run(
        lookimage._vet_host(url, "sound", looksound.LookSoundError)))

check("http refused, and the sentence says sounds",
      "I only take sounds from https://" in (svet("http://example.com/a.mp3") or ""), True)
check("...the same through load()", "https" in (sreason(sload, "http://example.com/a.mp3")
                                                 or ""), True)
for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.9", "169.254.169.254", "100.64.0.1", "::1"):
    host = f"[{ip}]" if ":" in ip else ip
    check(f"literal {ip} refused (loopback/LAN/metadata/tailnet)",
          "private or local" in (svet(f"https://{host}/a.mp3") or ""), True)
check("a public literal address passes", svet("https://93.184.216.34/a.mp3"), None)


async def _serve_and_fetch():
    async def ok(_r):
        return web.Response(body=mp3(1.0), content_type="audio/mpeg")

    async def big(_r):
        return web.Response(body=b"x" * (looksound.MAX_BYTES + 5000))

    async def liar(_r):
        # No Content-Length and an endless-ish body: only the streaming cap stops it.
        resp = web.StreamResponse()
        await resp.prepare(_r)
        try:
            for _ in range(100):
                await resp.write(b"y" * 65536)
        except ConnectionError:
            pass            # the client gave up at the cap - that is the point
        return resp

    async def notsound(_r):
        return web.Response(body=png(), content_type="audio/mpeg")   # the header lies

    async def redir(_r):
        raise web.HTTPFound("/ok")

    app = web.Application()
    app.add_routes([web.get("/ok", ok), web.get("/big", big), web.get("/liar", liar),
                    web.get("/notsound", notsound), web.get("/redir", redir)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    seen = []

    async def vetted(url, *_how):
        seen.append(url)
    real_vet = lookimage._vet_host
    lookimage._vet_host = vetted          # the fence would (rightly) refuse 127.0.0.1
    out = {}
    try:
        for key in ("ok", "big", "liar", "redir"):
            try:
                out[key] = len(await looksound.fetch_url(base + "/" + key))
            except looksound.LookSoundError as e:
                out[key] = str(e)
        try:
            raw = await looksound.fetch_url(base + "/notsound")
            looksound.check(raw, "the link")
            out["notsound"] = "accepted"
        except looksound.LookSoundError as e:
            out["notsound"] = str(e)
    finally:
        lookimage._vet_host = real_vet
        await runner.cleanup()
    out["vetted_hops"] = len(seen)
    return out


got = asyncio.run(_serve_and_fetch())
check("a sound downloads whole", got["ok"], len(mp3(1.0)))
check("a redirect is followed", got["redir"], len(mp3(1.0)))
check("over the cap with Content-Length known: refused in sound words",
      "that sound is" in str(got["big"]) and "512 KB" in str(got["big"]), True)
check("NO Content-Length: the streaming cap still cuts it off at 512 KB",
      "bigger than my 512 KB limit" in str(got["liar"]), True)
check("a server that says audio/mpeg but sends a PNG is refused by the bytes",
      "isn't an MP3 or OGG" in str(got["notsound"]), True)
check("every hop went through the fence (5 fetches, one redirect = 6 vets)",
      got["vetted_hops"], 6)

# ================================================================= list_sounds
section("list_sounds - a read that taints")
registered("list_sounds", 0, False, False, True)
cl, g = fresh(sounds=[_Sound(31, "airhorn", volume=0.5, emoji="\U0001F4EF"),
                      _Sound(32, "boop")], tier=1)
result, preview = go(cl, "list_sounds", {"guild_id": GUILD})
check("no preview for a read", preview, None)
check("count, and the slots for Boost Level 1 (24)", (result["count"], result["limit"]),
      (2, 24))
check("each sound: id, name, volume, emoji, link - and not who uploaded it",
      result["sounds"][0], {"id": 31, "name": "airhorn", "volume": 0.5,
                            "emoji": "\U0001F4EF", "available": True,
                            "url": "https://cdn.discordapp.com/soundboard-sounds/31"})
check("no emoji is None", result["sounds"][1]["emoji"], None)
check("the slot table: 8 / 24 / 36 / 48 by Boost Level",
      [capabilities._sound_slots(_Guild(tier=t)) for t in (0, 1, 2, 3)], [8, 24, 36, 48])

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
