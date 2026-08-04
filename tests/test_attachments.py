"""
test_attachments.py - reading files off Discord and sending files to it, offline.

Two halves with different risks. Sending is mostly Discord's limits, checked locally
so the failure names the file instead of arriving as a bare 413. Reading is the half
worth real paranoia: it takes bytes and a filename chosen by whoever uploaded them
and writes them onto Tyler's machine, so the traversal and containment checks here
are the point of the file rather than an afterthought.

Everything runs against stubs, and DOWNLOAD_DIR is redirected into a temp directory
so a test run never writes into the repo's real downloads/.

    python test_attachments.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package and bot.py live.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio
import os
import shutil
import sys
import tempfile

import discord

from benham.core import capabilities
from benham.core import policy

TESTING = 736988645562646619
TESTING_CHAN = 5552
MSG = 900001

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------------------- stubs

class _StubAttachment:
    def __init__(self, aid, filename, data=b"", content_type=None, size=None, fail=False):
        self.id = aid
        self.filename = filename
        self.content_type = content_type
        self._data = data
        # size is metadata, so it can disagree with the bytes - which is exactly how
        # the oversize check gets to refuse before downloading anything.
        self.size = len(data) if size is None else size
        self.url = f"https://cdn.example/{filename}"
        self.fail = fail

    async def read(self):
        if self.fail:
            raise discord.NotFound(_StubResponse(), "Unknown Attachment")
        return self._data


class _StubResponse:
    status = 404
    reason = "Not Found"


class _StubGuild:
    def __init__(self, gid=TESTING):
        self.id = gid
        self.name = "Testing Server"


class _Sent:
    def __init__(self, content, files):
        self.id = 777
        self.content = content
        self.files = files
        self.jump_url = "https://discord.com/x/777"


class _StubChannel:
    def __init__(self, cid=TESTING_CHAN, attachments=()):
        self.id = cid
        self.guild = _StubGuild()
        self.attachments = list(attachments)
        self.sends = []

    def __str__(self):
        return "#stub"

    async def fetch_message(self, mid):
        return _StubMessage(int(mid), self, self.attachments)

    async def send(self, content=None, files=None, **kw):
        self.sends.append({"content": content, "files": list(files or [])})
        return _Sent(content, files)


class _StubMessage:
    def __init__(self, mid, channel, attachments):
        self.id = mid
        self.channel = channel
        self.attachments = list(attachments)
        self.author = "someone"


class _StubUser:
    def __init__(self, uid=42):
        self.id = uid
        self.dm_channel = _StubChannel(cid=999)
        self.bot = False

    def __str__(self):
        return "someone#0001"


class _StubClient:
    def __init__(self, channel):
        self.channel = channel
        self.user_obj = _StubUser()

    def get_channel(self, cid):
        return self.channel if int(cid) == self.channel.id else None

    async def fetch_channel(self, cid):
        ch = self.get_channel(cid)
        if ch is None:
            raise discord.NotFound(_StubResponse(), "Unknown Channel")
        return ch

    def get_guild(self, gid):
        return _StubGuild() if int(gid) == TESTING else None

    def get_user(self, uid):
        return self.user_obj


def run(client, action, **params):
    result, _pending = asyncio.run(capabilities.run(
        client, lambda *_: None, action, params,
        call_ctx=policy.CallContext.local()))
    return result


def err(client, action, **params):
    try:
        run(client, action, **params)
        return None
    except capabilities.ActionError as e:
        return str(e)


# ------------------------------------------------------------------ temp downloads
TMP = tempfile.mkdtemp(prefix="benham-att-")
capabilities.DOWNLOAD_DIR = os.path.join(TMP, "downloads")

TEXT = b"line one\nline two\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4

try:
    # ------------------------------------------------------------------ filenames
    section("Filename sanitising - the uploader controls this string")
    S = capabilities._safe_filename
    check("an ordinary name is left alone", S("crash-report.txt", "fb"), "crash-report.txt")
    check("posix traversal is stripped", S("../../../etc/passwd", "fb"), "passwd")
    check("windows traversal is stripped", S(r"..\..\Windows\System32\evil.dll", "fb"), "evil.dll")
    check("a device name cannot be created", S("CON.txt", "fb"), "_CON.txt")
    check("nul byte and pipes are scrubbed", S("we\x00ird|name?.png", "fb"), "we_ird_name_.png")
    check("dots and spaces alone fall back", S("  ..  ", "fb"), "fb")
    check("empty falls back", S("", "fb"), "fb")
    check("a long name keeps its extension", S("a" * 300 + ".log", "fb").endswith(".log"), True)
    check("...and is actually shortened", len(S("a" * 300 + ".log", "fb")) <= 120, True)

    section("Containment - the check that fires only if the sanitiser is wrong")
    root = os.path.join(TMP, "root")
    os.makedirs(root, exist_ok=True)
    check("a normal name resolves inside", capabilities._confined_path(root, "a.txt"),
          os.path.realpath(os.path.join(root, "a.txt")))
    escaped = None
    try:
        capabilities._confined_path(root, os.path.join("..", "..", "escaped.txt"))
    except capabilities.ActionError as e:
        escaped = str(e)
    check("an escaping name is refused outright", escaped is not None, True)

    # ------------------------------------------------------------------- reading
    section("read_attachments - text")
    ch = _StubChannel(attachments=[_StubAttachment(1, "notes.txt", TEXT, "text/plain")])
    client = _StubClient(ch)
    r = run(client, "read_attachments", channel_id=TESTING_CHAN, message_id=MSG)
    a0 = r["attachments"][0]
    check("one attachment reported", r["count"], 1)
    check("contents come back inline", a0["text"], TEXT.decode())
    check("not truncated", a0["text_truncated"], False)
    check("saved under downloads/<message_id>/",
          os.path.dirname(a0["saved_to"]), os.path.join(capabilities.DOWNLOAD_DIR, str(MSG)))
    check("the file really exists", os.path.isfile(a0["saved_to"]), True)
    check("with the right bytes", open(a0["saved_to"], "rb").read(), TEXT)

    section("read_attachments - binary")
    ch = _StubChannel(attachments=[_StubAttachment(2, "shot.png", PNG, "image/png")])
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG)
    a0 = r["attachments"][0]
    check("binary is saved", os.path.isfile(a0["saved_to"]), True)
    check("but not decoded into garbage", "text" in a0, False)
    check("size is reported", a0["bytes"], len(PNG))

    section("read_attachments - a hostile filename lands somewhere safe")
    ch = _StubChannel(attachments=[
        _StubAttachment(3, r"..\..\..\Users\Tyler\evil.txt", b"pwned", "text/plain")])
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG)
    a0 = r["attachments"][0]
    check("the rewrite is disclosed, not silent", a0["sanitised_to"], "evil.txt")
    check("it wrote inside downloads/",
          os.path.realpath(a0["saved_to"]).startswith(os.path.realpath(capabilities.DOWNLOAD_DIR)),
          True)
    check("nothing was created up at Users\\Tyler",
          os.path.exists(os.path.join(TMP, "evil.txt")), False)

    section("read_attachments - executables are flagged, not blocked")
    ch = _StubChannel(attachments=[_StubAttachment(4, "totally_safe.exe", b"MZ", None)])
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG)
    check("flagged", r["attachments"][0]["executable"], True)
    check("and still downloaded, because it is only ever bytes on disk",
          os.path.isfile(r["attachments"][0]["saved_to"]), True)
    ch = _StubChannel(attachments=[_StubAttachment(5, "mod.jar", b"PK", None)])
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG)
    check("a .jar is not flagged - it would cry wolf on normal work",
          "executable" in r["attachments"][0], False)

    section("read_attachments - size ceiling")
    big = _StubAttachment(6, "huge.zip", b"x" * 10, size=50 * 1024 * 1024)
    r = run(_StubClient(_StubChannel(attachments=[big])),
            channel_id=TESTING_CHAN, message_id=MSG, action="read_attachments")
    a0 = r["attachments"][0]
    check("skipped with the size named", a0["skipped"].startswith("50.0MB is over"), True)
    check("nothing was written", "saved_to" in a0, False)
    check("raising max_bytes fetches it",
          "saved_to" in run(_StubClient(_StubChannel(attachments=[big])), "read_attachments",
                            channel_id=TESTING_CHAN, message_id=MSG,
                            max_bytes=60 * 1024 * 1024)["attachments"][0], True)

    section("read_attachments - save=false")
    ch = _StubChannel(attachments=[_StubAttachment(7, "peek.log", TEXT, "text/plain")])
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG, save=False)
    a0 = r["attachments"][0]
    check("still returns the text", a0["text"], TEXT.decode())
    check("but writes nothing", "saved_to" in a0, False)

    section("read_attachments - several, one broken")
    ch = _StubChannel(attachments=[
        _StubAttachment(8, "a.txt", b"first", "text/plain"),
        _StubAttachment(9, "gone.txt", b"", "text/plain", fail=True),
        _StubAttachment(10, "c.txt", b"third", "text/plain")])
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG)
    check("all three are accounted for", r["count"], 3)
    check("the broken one says why", r["attachments"][1]["skipped"].startswith("download failed"), True)
    check("the others still came through",
          [r["attachments"][0].get("text"), r["attachments"][2].get("text")], ["first", "third"])
    check("indexes are the message's, not the survivors'",
          [a["index"] for a in r["attachments"]], [0, 1, 2])

    section("read_attachments - picking one, and none")
    r = run(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN, message_id=MSG, index=2)
    check("index selects a single attachment", r["count"], 1)
    check("and keeps its real index", r["attachments"][0]["index"], 2)
    check("out of range is refused",
          err(_StubClient(ch), "read_attachments", channel_id=TESTING_CHAN,
              message_id=MSG, index=9) is not None, True)
    empty = run(_StubClient(_StubChannel()), "read_attachments",
                channel_id=TESTING_CHAN, message_id=MSG)
    check("a message with no files is an answer, not an error", empty["count"], 0)
    check("and says so", "no attachments" in empty["note"], True)

    # ------------------------------------------------------------------- sending
    section("send_file")
    f1 = os.path.join(TMP, "one.txt")
    f2 = os.path.join(TMP, "two.png")
    open(f1, "wb").write(b"hello")
    open(f2, "wb").write(PNG)

    ch = _StubChannel()
    c = _StubClient(ch)
    r = run(c, "send_file", channel_id=TESTING_CHAN, path=f1, content="here")
    check("single file still works", r["filenames"], ["one.txt"])
    check("bytes reported", r["bytes"], 5)
    check("one attachment actually reached send()", len(ch.sends[-1]["files"]), 1)

    r = run(c, "send_file", channel_id=TESTING_CHAN, paths=[f1, f2])
    check("several files in one message", r["filenames"], ["one.txt", "two.png"])
    check("both reached send()", len(ch.sends[-1]["files"]), 2)

    check("a missing file is named",
          err(c, "send_file", channel_id=TESTING_CHAN, path=os.path.join(TMP, "nope.txt")) is not None,
          True)
    check("no file at all is refused",
          err(c, "send_file", channel_id=TESTING_CHAN, content="hi"),
          "send_file needs `path` (one file) or `paths` (several)")
    check("more than 10 files is refused before opening any",
          (err(c, "send_file", channel_id=TESTING_CHAN, paths=[f1] * 11) or "").startswith(
              "Discord takes at most 10"), True)

    section("send_file - size limits")
    big_path = os.path.join(TMP, "big.bin")
    with open(big_path, "wb") as fh:
        fh.write(b"\0" * (26 * 1024 * 1024))
    check("one oversized file is refused",
          "Discord's limit is 25MB here" in (err(c, "send_file", channel_id=TESTING_CHAN,
                                                 path=big_path) or ""), True)
    half = os.path.join(TMP, "half.bin")
    with open(half, "wb") as fh:
        fh.write(b"\0" * (13 * 1024 * 1024))
    check("two files that only overflow together are refused too",
          "25MB per message" in (err(c, "send_file", channel_id=TESTING_CHAN,
                                     paths=[half, half]) or ""), True)

    section("dm_user")
    r = run(c, "dm_user", user_id=42, content="text only")
    check("text-only DM still works", r["status"], "sent")
    check("and sends no files", "filenames" in r, False)
    r = run(c, "dm_user", user_id=42, path=f1)
    check("a DM can carry a file with no text", r["filenames"], ["one.txt"])
    check("neither text nor file is refused",
          err(c, "dm_user", user_id=42), "dm_user needs `content`, a file, or both")

    section("Confirmation previews name the file")
    # A tainted send needs a yes, and the preview is the whole basis for it.
    d = capabilities.describe_call("send_file", {"channel_id": 1, "path": "C:/keys/environ.env"})
    check("the filename is in the summary", "environ.env" in d["summary"], True)
    d = capabilities.describe_call("dm_user", {"user_id": 2, "paths": ["a.png", "b.png"]})
    check("several files are all named", "a.png, b.png" in d["summary"], True)
    d = capabilities.describe_call("send_message", {"channel_id": 1, "content": "hi"})
    check("a plain message preview is unchanged", "files=" in d["summary"], False)

    section("Parameter coercion")
    check("a bare string path list is one path, not a list of letters",
          capabilities.validate(capabilities.REGISTRY["send_file"],
                                {"channel_id": 1, "paths": "C:/x.png"})["paths"], ["C:/x.png"])

    section("Message metadata")
    att = _StubAttachment(11, "report.pdf", b"%PDF", "application/pdf")
    d = capabilities.att_dict(att)
    check("a read reports the name, not just a url", d["filename"], "report.pdf")
    check("and the size", d["bytes"], 4)
    check("and the type", d["content_type"], "application/pdf")
    check("and still the url", d["url"], "https://cdn.example/report.pdf")

    section("Embeds carry their media URLs - a GIF is invisible without them")
    # Discord's GIF picker posts a bare Tenor/Klipy URL whose embed has no
    # title, description or fields; the picture is only reachable through the
    # embed's media slots. Built with the real discord.Embed so the proxy
    # attribute shapes are the installed library's, not a stub's guess.
    tenor = discord.Embed.from_dict({
        "type": "gifv", "url": "https://tenor.com/view/dance-gif-123",
        "video": {"url": "https://media.tenor.com/x/dance.mp4"},
        "thumbnail": {"url": "https://media.tenor.com/x/raw.png",
                      "proxy_url": "https://media.discordapp.net/x/dance.png"}})
    d = capabilities.embed_dict(tenor)
    check("the video URL is reported", d["video"], "https://media.tenor.com/x/dance.mp4")
    check("the thumbnail prefers proxy_url", d["thumbnail"],
          "https://media.discordapp.net/x/dance.png")
    check("the source link rides along", d["url"], "https://tenor.com/view/dance-gif-123")
    check("no empty keys pad the record", "title" in d or "image" in d, False)

    rich = discord.Embed.from_dict({
        "title": "Server down", "description": "crashed",
        "fields": [{"name": "Exit code", "value": "137"}],
        "image": {"url": "https://cdn.example/graph.png"}})
    d = capabilities.embed_dict(rich)
    check("title and description still read", (d["title"], d["description"]),
          ("Server down", "crashed"))
    check("fields still read", d["fields"], [{"name": "Exit code", "value": "137"}])
    check("an image URL is reported", d["image"], "https://cdn.example/graph.png")
    check("a truly empty embed serialises to nothing",
          capabilities.embed_dict(discord.Embed()), {})

    section("Images are handed to the model as pictures, not as descriptions")
    # The bug this covers: read_attachments downloaded a JPEG perfectly, reported
    # its name/size/type, and Benham still answered "I can't see the image" -
    # because a description of a picture is not the picture.
    from benham.core import agent

    img = os.path.join(TMP, "shot.png")
    open(img, "wb").write(PNG)

    def blocks(**rec):
        return agent._image_blocks({"attachments": [rec]})

    b, sk = blocks(filename="shot.png", content_type="image/png", saved_to=img)
    check("an image becomes an image block", len(b), 1)
    check("with the right media type", b[0]["source"]["media_type"], "image/png")
    check("and the actual bytes", __import__("base64").b64decode(b[0]["source"]["data"]), PNG)
    check("nothing skipped", sk, [])

    b, sk = blocks(filename="notes.txt", content_type="text/plain", saved_to=img)
    check("a text file is not sent as a picture", (len(b), sk), (0, []))

    b, sk = blocks(filename="photo.heic", content_type="image/heic", saved_to=img)
    check("an unviewable image says why rather than going quiet",
          (len(b), "not a format I can view" in sk[0]), (0, True))

    b, sk = blocks(filename="shot.png", content_type="image/png")
    check("save=false explains that there is nothing to show",
          (len(b), "not saved" in sk[0]), (0, True))

    b, sk = blocks(filename="mystery.png", content_type="application/octet-stream", saved_to=img)
    check("a mistyped png is still shown, via its extension", len(b), 1)

    big = os.path.join(TMP, "big.png")
    with open(big, "wb") as fh:
        fh.write(b"\x89PNG" + b"\0" * (5 * 1024 * 1024))
    _b, sk = blocks(filename="big.png", content_type="image/png", saved_to=big)
    check("an oversized image is refused with its size",
          "over the 4MB limit" in sk[0], True)

    many = {"attachments": [{"filename": f"{i}.png", "content_type": "image/png",
                             "saved_to": img} for i in range(6)]}
    b, sk = agent._image_blocks(many)
    check("no more than 4 images per call", len(b), 4)
    check("and the rest are accounted for", len(sk), 2)

    section("A claimed save that never happened is corrected, not relayed")
    # The bug this covers: Tyler DM'd two PDFs, and Benham replied "Got both saved:
    # downloads/<message_id>/May_2026.pdf..." without ever calling read_attachments -
    # the paths were invented from the message id, and the folder never existed.
    # The guard checks every downloads/ path in an outgoing reply against the disk.
    logged = []
    V = lambda text: agent._verify_saved_claims(text, log=logged.append)

    real_dir = os.path.join(capabilities.DOWNLOAD_DIR, "12345")
    os.makedirs(real_dir, exist_ok=True)
    open(os.path.join(real_dir, "May_2026.pdf"), "wb").write(b"%PDF")

    out = V("Got both saved: downloads/99999/May_2026.pdf and downloads/99999/June_2026.pdf")
    check("a phantom path triggers a correction", "Correction" in out, True)
    check("both phantom paths are named",
          "downloads/99999/May_2026.pdf" in out.split("Correction")[1]
          and "downloads/99999/June_2026.pdf" in out.split("Correction")[1], True)
    check("and the lie is logged", len(logged), 1)

    out = V("Saved to downloads/12345/May_2026.pdf.")
    check("a real save passes untouched (trailing dot ignored)", "Correction" in out, False)
    check("backslashes verify against the same file",
          "Correction" in V(r"Saved to downloads\12345\May_2026.pdf"), False)

    open(os.path.join(real_dir, "May report 2026.pdf"), "wb").write(b"%PDF")
    check("a filename with spaces is not falsely branded a phantom",
          "Correction" in V("Saved to downloads/12345/May report 2026.pdf"), False)

    check("a reply with no paths is left alone", V("all good"), "all good")
    check("mentioning the folder generically is fine",
          "Correction" in V("check the downloads folder"), False)
    mixed = V("Saved downloads/12345/May_2026.pdf and downloads/12345/ghost.pdf")
    check("one real + one phantom flags only the phantom",
          "ghost.pdf" in mixed.split("Correction")[1]
          and "May_2026.pdf" not in mixed.split("Correction")[1], True)

    check("the hard rule about invented paths is in the system prompt",
          "read_attachments result" in agent._system_prompt("a DM", "caz6666"), True)

    section("The DM path can see an attachment at all")
    # bot.py hands the agent TEXT, so an attachment is invisible unless it is
    # described. These two facts are what make "what's in this?" answerable.
    import bot

    msg = _StubMessage(4242, _StubChannel(cid=555), [
        _StubAttachment(1, "crash.log", b"x" * 10, "text/plain"),
        _StubAttachment(2, "shot.png", PNG, "image/png")])
    note = bot.attachment_note(msg)
    check("names every file", "crash.log" in note and "shot.png" in note, True)
    check("gives the sizes", "10 bytes" in note, True)
    check("carries the channel id the tool needs", "channel_id=555" in note, True)
    check("carries the message id the tool needs", "message_id=4242" in note, True)
    check("an unknown type is not a blank", "unknown type" in bot.attachment_note(
        _StubMessage(1, _StubChannel(cid=5), [_StubAttachment(3, "x.bin", b"z", None)])), True)

    section("Registration")
    ra = capabilities.REGISTRY["read_attachments"]
    check("read_attachments is tier 0", ra.tier, 0)
    check("it taints - the bytes are someone else's", ra.taints, True)
    check("it is not outward", ra.outward, False)
    check("it takes no url parameter, by design", "url" in ra.params, False)
    check("send_file still posts (allowlist applies)", capabilities.REGISTRY["send_file"].posts, True)
    check("dm_user is still outward", capabilities.REGISTRY["dm_user"].outward, True)

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
