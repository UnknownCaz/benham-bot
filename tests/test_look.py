"""
test_look.py - "embroidery": edit_server_look and edit_bot_profile, offline (INTENT 52).

Two capabilities change how things LOOK to everyone who sees them - a server's
icon/banner/splash/name/description, and the bot's own avatar and banner - and one
module (lookimage.py) turns "which picture?" into checked bytes. What is pinned:

  THE GATES RUN BEFORE ANYTHING CHANGES. A missing permission, a server not boosted
  enough for the banner, a file that is not a picture, a request to set and remove
  the same thing: each is refused in one plain sentence with Discord's edit call
  never made. A half-embroidered server is harder to explain than a refused one.

  THE CHANGE IS PREVIEWED, THEN CONFIRMED. always_confirm means the first call
  parks a preview (with the picture's type, size and hash) and touches nothing;
  only the confirmed call edits - and it saves the picture it replaces first, which
  is what makes "tier 2, has an exact inverse" true rather than hoped.

  THE URL ROUTE IS FENCED. https only, public hosts only, a size cap that holds
  even when the server lies about Content-Length, redirects re-vetted hop by hop.
  Tested against a real local aiohttp server so the streaming and redirect logic
  actually executes - a loose stub reads exactly like a passing one.

  DISCORD'S OWN REFUSALS ARE SENTENCES. Rate limit (the bot-avatar one is real),
  not-boosted, a timeout inside discord.py's own 429 sleep, anything else: never a
  traceback, always "Nothing was changed".

Everything runs against stubs; the backup folder is a temp directory.

    python test_look.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import os
import shutil
import struct
import sys
import tempfile

import logging

import discord
from aiohttp import web

# The cap test makes the client hang up mid-body on purpose; aiohttp's server
# would log that as an error traceback and bury the PASS lines.
logging.getLogger("aiohttp.server").setLevel(logging.CRITICAL)

from benham.core import capabilities, lookimage, policy

TYLER = 273967061619965952
GUILD = 8800001
CHAN = 8800002
MSG = 8800003
_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# ----------------------------------------------------------------- picture bytes

def png(w=512, h=512, pad=64):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" + b"\x00" * pad)


def jpeg(w=640, h=480):
    return (b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", h, w)
            + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01" + b"\x00" * 32)


def gif(w=200, h=100):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 32


def webp_x(w=300, h=150):
    return (b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8X" + struct.pack("<I", 10)
            + b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little")
            + (h - 1).to_bytes(3, "little") + b"\x00" * 16)


# ----------------------------------------------------------------------- stubs

class _Resp:
    def __init__(self, status=400):
        self.status = status
        self.reason = "stub"


class _Asset:
    def __init__(self, data):
        self.data = data

    async def read(self):
        return self.data


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
    def __init__(self, manage_guild=True):
        self.manage_guild = manage_guild


class _Me:
    def __init__(self, manage_guild=True):
        self.guild_permissions = _Perms(manage_guild)


class _Guild:
    def __init__(self, features=(), tier=0, manage_guild=True, name="RadioTower"):
        self.id = GUILD
        self.name = name
        self.features = list(features)
        self.premium_tier = tier
        self.me = _Me(manage_guild)
        self.icon = _Asset(png(256, 256, pad=3))     # something to save before replacing
        self.banner = None
        self.splash = None
        self.description = None
        self.calls = []
        self.raise_on_edit = None
        self.slow = False

    async def edit(self, **kw):
        self.calls.append(kw)
        if self.slow:
            await asyncio.sleep(5)
        if self.raise_on_edit is not None:
            raise self.raise_on_edit
        for slot in ("icon", "banner", "splash"):
            if slot in kw:
                setattr(self, slot, None if kw[slot] is None else _Asset(kw[slot]))
        if "name" in kw:
            self.name = kw["name"]
        if "description" in kw:
            self.description = kw["description"]
        return self


class _Bot:
    def __init__(self):
        self.id = 4242
        self.avatar = _Asset(png(128, 128, pad=5))
        self.banner = None
        self.calls = []
        self.raise_on_edit = None

    async def edit(self, **kw):
        self.calls.append(kw)
        if self.raise_on_edit is not None:
            raise self.raise_on_edit
        return self


class _Client:
    def __init__(self, guild, atts=()):
        self.guild = guild
        self.chan = _Chan(list(atts))
        self.user = _Bot()

    def get_guild(self, gid):
        return self.guild if int(gid) == GUILD else None

    def get_channel(self, cid):
        return self.chan if int(cid) == CHAN else None

    async def fetch_channel(self, cid):
        ch = self.get_channel(cid)
        if ch is None:
            raise discord.NotFound(_Resp(404), "Unknown Channel")
        return ch


def go(client, name, params, force=False):
    """capabilities.run as the owner in a DM -> (result, preview). Raises ActionError."""
    return asyncio.run(capabilities.run(
        client, lambda *_: None, name, params, actor_id=TYLER, force=force,
        call_ctx=policy.CallContext.owner_dm(TYLER, channel_id=CHAN)))


def refusal(client, name, params, force=True):
    try:
        go(client, name, params, force=force)
        return None
    except capabilities.ActionError as e:
        return str(e)


TMP = tempfile.mkdtemp(prefix="benham-look-")
capabilities.LOOK_BACKUP_DIR = os.path.join(TMP, "look_backups")
ATT = {"channel_id": CHAN, "message_id": MSG}


def fresh(atts=(), **gkw):
    return _Client(_Guild(**gkw), atts)


try:
    # ======================================================== the registry's word
    section("Registered as agreed: MANAGE, outward, always confirmed, never a guest")
    for name in ("edit_server_look", "edit_bot_profile"):
        a = capabilities.REGISTRY[name]
        check(f"{name}: tier 2 (MANAGE)", a.tier, 2)
        check(f"{name}: outward + always_confirm", (a.outward, a.always_confirm), (True, True))
        check(f"{name}: not destructive, not a guest capability", (a.destructive, a.guest),
              (False, False))
    check("only the server one taints (it echoes the guild's own name)",
          (capabilities.REGISTRY["edit_server_look"].taints,
           capabilities.REGISTRY["edit_bot_profile"].taints), (True, False))
    check("a guest reaches neither",
          [n for n in ("edit_server_look", "edit_bot_profile")
           if policy.authorize(capabilities.REGISTRY[n],
                               policy.CallContext.guest_dm(111, 5)).allowed], [])

    # =========================================================== lookimage: bytes
    section("lookimage - the type comes from the BYTES, the size from the header")
    check("png size read from its header", lookimage.dimensions(png(512, 256), "image/png"),
          (512, 256))
    check("jpeg size read from its SOF marker", lookimage.dimensions(jpeg(640, 480), "image/jpeg"),
          (640, 480))
    check("gif size", lookimage.dimensions(gif(200, 100), "image/gif"), (200, 100))
    check("webp (extended) size", lookimage.dimensions(webp_x(300, 150), "image/webp"),
          (300, 150))
    check("a truncated png is unknown size, not a crash",
          lookimage.dimensions(b"\x89PNG\r\n\x1a\n", "image/png"), (None, None))
    check("a real repo asset parses as 512x512",
          (lookimage.from_asset("benham-discord-avatar.png").width,
           lookimage.from_asset("benham-discord-avatar.png").height), (512, 512))

    def reason(fn, *a, **k):
        try:
            fn(*a, **k)
            return None
        except lookimage.LookImageError as e:
            return str(e)

    check("empty bytes refused", "empty" in (reason(lookimage.check, b"", "x.png") or ""), True)
    check("text renamed .png is refused by its bytes",
          "isn't a PNG, JPEG, GIF or WebP" in
          (reason(lookimage.check, b"hello world, not a picture", "cat.png") or ""), True)
    check("a zip is refused", reason(lookimage.check, b"PK\x03\x04" + b"0" * 40, "x.gif") is not None, True)
    check("over the ceiling refused with the size in the sentence",
          "most I'll send Discord" in (reason(lookimage.check, png(pad=2048), "big.png",
                                              max_bytes=1024) or ""), True)
    check("a good picture passes and describes itself",
          lookimage.check(png(512, 512), "attachment a.png").describe().startswith(
              "PNG, 512x512"), True)

    section("lookimage - asset: is confined to assets/")
    check("traversal refused", "outside" in (reason(lookimage.from_asset, "../INTENT.md") or ""), True)
    check("a missing file lists what exists",
          "benham-discord-avatar.png" in (reason(lookimage.from_asset, "nope.png") or ""), True)
    check("an empty name refused", reason(lookimage.from_asset, "") is not None, True)

    section("lookimage - the source grammar")
    async def no_message():
        raise lookimage.LookImageError("no message named")

    def load(spec, getter=no_message):
        return asyncio.run(lookimage.load(spec, getter, "icon"))

    check("a nonsense source says what it can be",
          "attachment" in (reason(load, "banana") or "") and "https://" in (reason(load, "banana") or ""), True)
    check("an attachment with no message named is a sentence", reason(load, "attachment"),
          "no message named")
    check("a bad attachment index is a sentence",
          "valid attachment" in (reason(load, "attachment:x") or ""), True)

    async def two_atts():
        return _Msg([_Att(1, "a.png", png()), _Att(2, "b.png", png())])

    async def one_att():
        return _Msg([_Att(1, "a.png", png(300, 300))])

    async def none_att():
        return _Msg([])

    check("two attachments and no index asks which",
          "which" in (reason(load, "attachment", two_atts) or ""), True)
    check("an index past the end says how many there are",
          "2 attachment" in (reason(load, "attachment:5", two_atts) or ""), True)
    check("no attachment at all is a sentence",
          "no attachment" in (reason(load, "attachment", none_att) or ""), True)
    check("the only attachment needs no index", load("attachment", one_att).width, 300)
    check("an attachment:1 picks the second", load("attachment:1", two_atts).source,
          "attachment b.png")
    check("http:// is refused (https only)",
          "https" in (reason(load, "http://example.com/a.png") or ""), True)

    section("lookimage - the URL route is fenced")
    def vet(url):
        return reason(lambda: asyncio.run(lookimage._vet_host(url)))

    check("http refused", "https" in (vet("http://example.com/a.png") or ""), True)
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.9", "169.254.169.254", "100.64.0.1"):
        check(f"literal {ip} refused (loopback/LAN/metadata/tailnet)",
              "private or local" in (vet(f"https://{ip}/a.png") or ""), True)
    check("a public literal address passes", vet("https://93.184.216.34/a.png"), None)

    class _FakeLoop:
        def __init__(self, ips):
            self.ips = ips

        async def getaddrinfo(self, host, port, **kw):
            return [(0, 0, 0, "", (ip, port)) for ip in self.ips]

    _real = asyncio.get_running_loop
    def vet_resolving(ips):
        async def inner():
            asyncio.get_running_loop = lambda: _FakeLoop(ips)
            try:
                await lookimage._vet_host("https://pics.example/a.png")
            finally:
                asyncio.get_running_loop = _real
        return reason(lambda: asyncio.run(inner()))

    check("a name that resolves to a private address is refused",
          "private or local" in (vet_resolving(["10.0.0.5"]) or ""), True)
    check("...even when only ONE of its addresses is private",
          "private or local" in (vet_resolving(["93.184.216.34", "127.0.0.1"]) or ""), True)
    check("a name that resolves public passes", vet_resolving(["93.184.216.34"]), None)

    # A real local server, so the redirect and streaming-cap logic runs. The host
    # fence is patched OUT for this section only (it would, correctly, refuse
    # 127.0.0.1) and the scheme is http because the server has no certificate.
    async def serve_and_fetch():
        hits = []

        async def ok(_r):
            return web.Response(body=png(300, 300), content_type="image/png")

        async def big(_r):
            return web.Response(body=b"x" * (lookimage.MAX_BYTES + 5000))

        async def liar(_r):
            # No Content-Length, endless-ish body: only the streaming cap can stop it.
            resp = web.StreamResponse()
            await resp.prepare(_r)
            try:
                for _ in range(200):
                    await resp.write(b"y" * 65536)
            except ConnectionError:
                pass            # the client gave up at the cap - that is the point
            return resp

        async def redir(_r):
            hits.append("redir")
            raise web.HTTPFound("/ok")

        async def loop(_r):
            raise web.HTTPFound("/loop")

        async def gone(_r):
            raise web.HTTPNotFound()

        app = web.Application()
        app.add_routes([web.get("/ok", ok), web.get("/big", big), web.get("/liar", liar),
                        web.get("/redir", redir), web.get("/loop", loop),
                        web.get("/gone", gone)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        async def vetted(_url):
            return None
        real_vet = lookimage._vet_host
        lookimage._vet_host = vetted
        out = {}
        try:
            for key, path in (("ok", "/ok"), ("big", "/big"), ("liar", "/liar"),
                              ("redir", "/redir"), ("loop", "/loop"), ("gone", "/gone")):
                try:
                    out[key] = len(await lookimage.fetch_url(base + path))
                except lookimage.LookImageError as e:
                    out[key] = str(e)
        finally:
            lookimage._vet_host = real_vet
            await runner.cleanup()
        return out

    got = asyncio.run(serve_and_fetch())
    check("a normal picture downloads whole", got["ok"], len(png(300, 300)))
    check("a redirect is followed", got["redir"], len(png(300, 300)))
    check("a redirect loop gives up in a sentence", "too many times" in str(got["loop"]), True)
    check("a 404 is a sentence naming the status", "404" in str(got["gone"]), True)
    check("a body over the cap is refused (Content-Length known)", "limit" in str(got["big"]), True)
    check("a body with NO Content-Length is still cut off by the streaming cap",
          "bigger than my" in str(got["liar"]), True)

    # ============================================================ edit_server_look
    section("edit_server_look - previewed first, nothing touched")
    cl = fresh([_Att(1, "logo.png", png(512, 512))])
    result, preview = go(cl, "edit_server_look",
                         {"guild_id": GUILD, "icon": "attachment", **ATT,
                          "name": "Radio Tower"}, force=False)
    check("the first call is a preview, not a result", (result, preview is not None), (None, True))
    check("...Discord's edit was never called", cl.guild.calls, [])
    check("...the preview names the server and the change",
          "RadioTower" in preview["summary"] and "new icon" in preview["summary"], True)
    check("...and the picture's type, size, source and hash",
          "PNG, 512x512" in preview["detail"] and "logo.png" in preview["detail"]
          and "#" in preview["detail"], True)
    check("...and the rename", "'Radio Tower'" in preview["detail"], True)
    check("...and says the old picture is saved first", "saved first" in preview["detail"], True)
    check("a preview saved nothing to disk", os.path.exists(capabilities.LOOK_BACKUP_DIR), False)

    section("edit_server_look - confirmed: edits, and saves what it replaces")
    cl = fresh([_Att(1, "logo.png", png(512, 512))])
    result, preview = go(cl, "edit_server_look",
                         {"guild_id": GUILD, "icon": "attachment", **ATT,
                          "name": "Radio Tower", "description": "Late-night radio."},
                         force=True)
    check("no preview on the confirmed call", preview, None)
    check("Discord got the icon bytes, the name, the description and an audit reason",
          (cl.guild.calls[0]["icon"] == png(512, 512), cl.guild.calls[0]["name"],
           cl.guild.calls[0]["description"], cl.guild.calls[0]["reason"]),
          (True, "Radio Tower", "Late-night radio.", capabilities.VIA))
    check("exactly one edit call", len(cl.guild.calls), 1)
    check("the result says what changed, from what Discord now holds",
          (result["status"], result["changed"], result["now_has"]["icon"]),
          ("server_look_changed", ["icon", "name", "description"], True))
    saved = result["old_pictures_saved"]
    check("the OLD icon was saved first", (len(saved), os.path.isfile(saved[0]) if saved else None),
          (1, True))
    check("...byte for byte", open(saved[0], "rb").read() == png(256, 256, pad=3), True)
    check("...named for the guild, the slot and a stamp", os.path.basename(saved[0]).startswith(
        f"{GUILD}-icon-"), True)

    section("edit_server_look - clear, and an empty description means 'none'")
    cl = fresh()
    cl.guild.banner = _Asset(png(960, 540, pad=2))
    cl.guild.features = ["BANNER"]
    result, _ = go(cl, "edit_server_look",
                   {"guild_id": GUILD, "clear": ["banner"], "description": ""}, force=True)
    check("clearing sends None for the banner and the description",
          (cl.guild.calls[0]["banner"], cl.guild.calls[0]["description"]), (None, None))
    check("...and the banner it removed was saved", len(result["old_pictures_saved"]), 1)

    section("edit_server_look - refused BEFORE anything changes")
    def refused(params, force=True, atts=(), **gkw):
        cl = fresh(atts or [_Att(1, "p.png", png(1024, 576))], **gkw)
        msg = refusal(cl, "edit_server_look", {"guild_id": GUILD, **ATT, **params}, force=force)
        return msg, cl.guild.calls

    msg, calls = refused({"icon": "attachment"}, manage_guild=False)
    check("no Manage Server -> says so", "lacks Manage Server" in (msg or ""), True)
    check("...and made no edit", calls, [])

    msg, calls = refused({"banner": "attachment"}, tier=0)
    check("banner on an unboosted server -> one plain sentence naming both levels",
          ("Boost Level 0" in (msg or "") and "banner (Boost Level 2)" in (msg or "")
           and "Nothing was changed" in (msg or "")), True)
    check("...and made no edit", calls, [])
    msg, _ = refused({"banner": "attachment"}, force=False, tier=0)
    check("...it refuses at the PREVIEW too - you aren't asked to confirm the impossible",
          "Boost Level 2" in (msg or ""), True)

    msg, calls = refused({"splash": "attachment"}, tier=0)
    check("invite splash needs Level 1", "invite splash (Boost Level 1)" in (msg or "") and calls == [], True)
    msg, calls = refused({"icon": "attachment"}, atts=[_Att(1, "a.gif", gif(300, 300))], tier=0)
    check("a GIF icon needs Level 1 and points at PNG/JPEG",
          "GIF icon" in (msg or "") and "PNG or JPEG works at any level" in (msg or "")
          and calls == [], True)
    msg, calls = refused({"icon": "attachment", "banner": "attachment:0", "splash": "attachment:0"},
                         tier=0)
    check("several limits are ONE sentence, not three",
          (msg or "").count("Nothing was changed") == 1 and "banner" in msg and "splash" in msg, True)
    msg, calls = refused({"banner": "attachment"}, features=["BANNER"], tier=2)
    check("...but at Level 2 the same banner goes through", (msg, len(calls)), (None, 1))
    msg, calls = refused({"splash": "attachment"}, features=["INVITE_SPLASH"], tier=1)
    check("...and a splash at Level 1", (msg, len(calls)), (None, 1))
    msg, calls = refused({"icon": "attachment"}, atts=[_Att(1, "a.gif", gif(300, 300))],
                         features=["ANIMATED_ICON"], tier=1)
    check("...and a GIF icon with ANIMATED_ICON", (msg, len(calls)), (None, 1))

    msg, calls = refused({"icon": "attachment"}, atts=[_Att(1, "notes.txt", b"just words")])
    check("not a picture -> refused by its bytes", "isn't a PNG, JPEG, GIF or WebP" in (msg or "")
          and calls == [], True)
    msg, calls = refused({"icon": "attachment"}, atts=[_Att(1, "x.png", png(), size=30 * 1048576)])
    check("a 30 MB attachment is refused before downloading", "30.0 MB" in (msg or "") and calls == [], True)
    msg, calls = refused({"icon": "attachment"}, atts=[_Att(1, "x.png", png(), fail=True)])
    check("a deleted attachment is a sentence", "couldn't download" in (msg or "") and calls == [], True)
    msg, calls = refused({"icon": "attachment:3"})
    check("a wrong attachment number is a sentence", "attachment:3 doesn't exist" in (msg or ""), True)
    msg, calls = refused({"icon": "asset:../INTENT.md"})
    check("an asset path that escapes is refused", "outside" in (msg or "") and calls == [], True)
    msg, calls = refused({"icon": "https://127.0.0.1/a.png"})
    check("a link to a private address is refused", "private or local" in (msg or "") and calls == [], True)
    msg, calls = refused({"icon": "attachment", "channel_id": None, "message_id": None})
    check("an attachment with no message named asks for the ids",
          "channel_id and message_id" in (msg or ""), True)
    msg, calls = refused({})
    check("nothing to change is said", "nothing to change" in (msg or ""), True)
    msg, calls = refused({"icon": "attachment", "clear": ["icon"]})
    check("set and remove in one request is refused", "pick one" in (msg or ""), True)
    msg, calls = refused({"clear": ["flavour"]})
    check("an unknown clear target names the real ones", "icon, banner, splash, description" in (msg or ""), True)
    msg, calls = refused({"name": "x"})
    check("a one-letter server name is refused", "2 to 100" in (msg or ""), True)

    section("edit_server_look - Discord's own refusals become sentences")
    def discord_says(exc, slow=False):
        cl = fresh([_Att(1, "p.png", png())])
        cl.guild.raise_on_edit = exc
        cl.guild.slow = slow
        return refusal(cl, "edit_server_look", {"guild_id": GUILD, "icon": "attachment", **ATT})

    m = discord_says(discord.HTTPException(_Resp(429), "You are being rate limited."))
    check("429 -> rate-limit sentence", "rate-limiting" in (m or "") and "Nothing was changed" in (m or ""), True)
    m = discord_says(discord.HTTPException(_Resp(400), "You are changing your icon too fast. Try again later."))
    check("'too fast' -> the same rate-limit sentence", "rate-limiting" in (m or ""), True)
    m = discord_says(discord.HTTPException(_Resp(400), "This server needs more boosts for that feature"))
    check("a boost complaint -> the not-boosted sentence", "isn't boosted enough" in (m or ""), True)
    m = discord_says(discord.HTTPException(_Resp(400), "Image is invalid"))
    check("anything else keeps Discord's words and still says nothing changed",
          "Image is invalid" in (m or "") and "Nothing was changed" in (m or ""), True)
    m = discord_says(discord.Forbidden(_Resp(403), "Missing Permissions"))
    check("Forbidden is run()'s own permission sentence", "lacks the permission" in (m or ""), True)
    capabilities.LOOK_TIMEOUT = 0.05
    m = discord_says(None, slow=True)
    capabilities.LOOK_TIMEOUT = 60
    check("a stuck edit (discord.py asleep in a 429) ends in a sentence, not a hang",
          "didn't answer" in (m or ""), True)

    section("edit_server_look - small pictures are warned about, not refused")
    cl = fresh([_Att(1, "tiny.png", png(64, 64))])
    _, preview = go(cl, "edit_server_look", {"guild_id": GUILD, "icon": "attachment", **ATT},
                    force=False)
    check("a 64x64 icon previews with a blur warning", "only 64x64" in preview["detail"], True)

    section("edit_server_look - works with no message at all (asset)")
    cl = fresh()
    result, _ = go(cl, "edit_server_look",
                   {"guild_id": GUILD, "icon": "asset:benham-discord-avatar.png"}, force=True)
    check("an assets/ picture is sent as its own bytes",
          cl.guild.calls[0]["icon"] == open(lookimage.ASSETS_DIR + "/benham-discord-avatar.png", "rb").read(), True)

    section("edit_server_look - a link (fetch stubbed; the fence is tested above)")
    async def fake_fetch(url, max_bytes=lookimage.MAX_BYTES):
        return jpeg(800, 600)
    real_fetch = lookimage.fetch_url
    lookimage.fetch_url = fake_fetch
    try:
        cl = fresh(features=["BANNER"], tier=2)
        result, _ = go(cl, "edit_server_look",
                       {"guild_id": GUILD, "banner": "https://pics.example.com/b.jpg"}, force=True)
        check("a link's bytes reach Discord", cl.guild.calls[0]["banner"] == jpeg(800, 600), True)
    finally:
        lookimage.fetch_url = real_fetch

    # ============================================================ edit_bot_profile
    section("edit_bot_profile - previewed, then confirmed, old avatar saved")
    cl = fresh([_Att(1, "me.png", png(512, 512))])
    result, preview = go(cl, "edit_bot_profile", {"avatar": "attachment", **ATT}, force=False)
    check("preview first, bot untouched", (result, preview is not None, cl.user.calls), (None, True, []))
    check("the preview says it shows everywhere and that Discord rate-limits avatars",
          "every server" in preview["detail"] and "limits how often" in preview["detail"], True)
    result, preview = go(cl, "edit_bot_profile", {"avatar": "attachment", **ATT}, force=True)
    check("the bot's own avatar is edited with the bytes", cl.user.calls, [{"avatar": png(512, 512)}])
    check("result names what changed", (result["status"], result["changed"]),
          ("bot_profile_changed", ["avatar"]))
    check("the previous avatar was saved first",
          len(result["old_pictures_saved"]) == 1
          and open(result["old_pictures_saved"][0], "rb").read() == png(128, 128, pad=5), True)

    cl = fresh([_Att(1, "b.png", png(960, 540))])
    result, _ = go(cl, "edit_bot_profile", {"banner": "attachment:0", "clear": ["avatar"], **ATT},
                   force=True)
    check("banner set and avatar cleared in one call",
          cl.user.calls, [{"banner": png(960, 540), "avatar": None}])

    section("edit_bot_profile - refusals and Discord's rate limit")
    cl = fresh([_Att(1, "n.txt", b"words")])
    check("not a picture", "isn't a PNG" in (refusal(cl, "edit_bot_profile",
          {"avatar": "attachment", **ATT}) or "") and cl.user.calls == [], True)
    check("nothing to change", "nothing to change" in (refusal(fresh(), "edit_bot_profile", {}) or ""), True)
    check("set + remove together refused",
          "pick one" in (refusal(fresh([_Att(1, "a.png", png())]), "edit_bot_profile",
                                 {"avatar": "attachment", "clear": ["avatar"], **ATT}) or ""), True)
    check("clear takes only avatar and banner",
          "avatar, banner" in (refusal(fresh(), "edit_bot_profile", {"clear": ["icon"]}) or ""), True)

    cl = fresh([_Att(1, "me.png", png())])
    cl.user.raise_on_edit = discord.HTTPException(
        _Resp(400), "You are changing your avatar too fast. Try again later.")
    m = refusal(cl, "edit_bot_profile", {"avatar": "attachment", **ATT})
    check("the avatar rate limit is one plain sentence",
          m is not None and "rate-limiting" in m and "a couple of times an hour" in m
          and "Nothing was changed" in m and "Traceback" not in m, True)
    cl.user.raise_on_edit = discord.HTTPException(_Resp(429), "slow down")
    check("a 429 on the bot profile is the same sentence",
          "rate-limiting" in (refusal(cl, "edit_bot_profile", {"avatar": "attachment", **ATT}) or ""), True)

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
