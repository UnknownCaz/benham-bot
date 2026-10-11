"""
lookimage.py - the picture behind an "embroidery" change.

Caz's word for changing how things LOOK: a server's icon, banner and invite
splash, and the bot's own avatar and banner (INTENT 52). The two capabilities
that do it (`edit_server_look`, `edit_bot_profile`) live in capabilities.py,
where every gate is readable in one place; this file is only the picture half -
turning "which image?" into checked bytes - so the gate code stays short.

One grammar names the source, the same for every slot:

    attachment            the ONE file on the named message
    attachment:2          file number 2 (0-based) on the named message
    asset:logo.png        a file in the repo's assets/ folder
    https://host/pic.png  a public https link

THE BYTES ARE THE FACT. The type comes from the first bytes (msgparts.sniff), not
from the filename or the content-type header - both are chosen by whoever made
the file, and Discord rejects a mismatch with a bare 400.

THE URL ROUTE IS FENCED, because a link is text somebody else may have written
and this fetch runs on the machine the bot lives on. https only; the host must
resolve to public addresses only (no loopback, no LAN, no tailnet - is_global is
False for all of those); redirects are followed by hand and every hop is checked
again; the body is read in chunks and abandoned the moment it passes the size
cap. A URL is also the one source whose bytes can change between the preview and
the confirmed run, so the preview prints a short hash of what it saw.
(read_attachments has no url parameter for the same reason; this one is allowed
because the result can only ever be an image handed to Discord's icon slots, and
because the action it feeds is previewed and confirmed every time.)

Every failure is a LookImageError whose message is a plain sentence - the model
relays it, and Caz reads it on a phone.
"""

import asyncio
import hashlib
import ipaddress
import os
import socket
import struct
from urllib.parse import urljoin, urlparse

from benham import paths
from benham.core import msgparts
from benham.core import pathsafe

ASSETS_DIR = os.path.join(paths.ROOT, "assets")

# Discord takes up to 10 MiB for these slots; 8 matches what read_attachments
# treats as an ordinary file and leaves headroom under the base64 inflation.
MAX_BYTES = 8 * 1024 * 1024
FORMATS = ("image/png", "image/jpeg", "image/gif", "image/webp")
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
        "image/webp": ".webp"}
FETCH_TIMEOUT = 20          # seconds, whole download
MAX_REDIRECTS = 3


class LookImageError(Exception):
    """A plain-words reason a picture cannot be used. Never a traceback."""


class Picture:
    """Checked image bytes plus the facts the preview prints."""

    def __init__(self, raw, media, width, height, source):
        self.raw = raw
        self.media = media
        self.width = width
        self.height = height
        self.source = source            # a human phrase: "attachment cat.png", "assets/x.png"
        self.sha = hashlib.sha256(raw).hexdigest()[:10]

    @property
    def ext(self):
        return _EXT[self.media]

    @property
    def animated_capable(self):
        """A GIF may animate, and Discord gates that, not the format name."""
        return self.media == "image/gif"

    def describe(self):
        size = (f"{len(self.raw) / 1048576:.1f} MB" if len(self.raw) >= 1048576
                else f"{max(1, len(self.raw) // 1024)} KB")
        dims = f"{self.width}x{self.height}" if self.width else "size unknown"
        kind = self.media.split("/")[1].upper()
        return f"{kind}, {dims}, {size}, from {self.source} (#{self.sha})"


# --------------------------------------------------------------------------
# Checking bytes
# --------------------------------------------------------------------------

def dimensions(raw, media):
    """(width, height) read from the file header, or (None, None).

    No imaging library is installed on the bot's machine and decoding a stranger's
    image buys nothing here - Discord does the real decode - so this reads only the
    few header bytes each format keeps its size in. Unknown or truncated -> None,
    and the caller treats a missing size as "unknown", never as an error.
    """
    try:
        if media == "image/png" and len(raw) >= 24:
            return struct.unpack(">II", raw[16:24])
        if media == "image/gif" and len(raw) >= 10:
            return struct.unpack("<HH", raw[6:10])
        if media == "image/webp" and len(raw) >= 30:
            tag = raw[12:16]
            if tag == b"VP8 ":                       # lossy
                w, h = struct.unpack("<HH", raw[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if tag == b"VP8L":                       # lossless: 14 + 14 bits
                bits = struct.unpack("<I", raw[21:25])[0]
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if tag == b"VP8X":                       # extended: 24-bit minus one
                w = int.from_bytes(raw[24:27], "little") + 1
                h = int.from_bytes(raw[27:30], "little") + 1
                return w, h
        if media == "image/jpeg":
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seglen = struct.unpack(">H", raw[i + 2:i + 4])[0]
                # SOF0..SOF15 hold the size, except DHT (C4), JPG (C8), DAC (CC).
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return w, h
                i += 2 + seglen
    except (struct.error, IndexError):
        pass
    return None, None


def ext_for(raw):
    """File extension for these bytes (".png" when they are not a known image) -
    used to name the saved copy of a picture that is about to be replaced."""
    return _EXT.get(msgparts.sniff(raw), ".png")


def check(raw, source, max_bytes=MAX_BYTES):
    """Raw bytes -> Picture, or LookImageError saying why not."""
    if not raw:
        raise LookImageError(f"{source} is empty - there is no picture in it.")
    if len(raw) > max_bytes:
        raise LookImageError(
            f"{source} is {len(raw) / 1048576:.1f} MB; the most I'll send Discord "
            f"for a picture is {max_bytes // 1048576} MB. Shrink it or send a "
            "smaller one.")
    media = msgparts.sniff(raw)
    if media not in FORMATS:
        raise LookImageError(
            f"{source} isn't a PNG, JPEG, GIF or WebP picture (I go by the file's "
            "actual bytes, not its name), so Discord wouldn't take it.")
    w, h = dimensions(raw, media)
    return Picture(raw, media, w, h, source)


# --------------------------------------------------------------------------
# Getting bytes: attachment / asset / url
# --------------------------------------------------------------------------

async def from_attachment(message, index, label="the message"):
    """The `index`-th attachment of `message` (None = the only one)."""
    atts = list(message.attachments)
    if not atts:
        raise LookImageError(f"{label} has no attachment - attach the picture to "
                             "the message and ask again.")
    if index is None:
        if len(atts) > 1:
            raise LookImageError(
                f"{label} has {len(atts)} attachments, so I need to know which: "
                "say attachment:0, attachment:1 and so on (counting from 0).")
        index = 0
    if not 0 <= index < len(atts):
        raise LookImageError(f"{label} has {len(atts)} attachment(s), so "
                             f"attachment:{index} doesn't exist (they count from 0).")
    a = atts[index]
    if a.size and a.size > MAX_BYTES:
        # The metadata is the uploader's claim - check() re-checks the real bytes -
        # but refusing here saves downloading something we will certainly refuse.
        raise LookImageError(
            f"{a.filename} is {a.size / 1048576:.1f} MB; the most I'll send Discord "
            f"for a picture is {MAX_BYTES // 1048576} MB.")
    try:
        raw = await a.read()
    except Exception as e:  # noqa: BLE001 - discord.NotFound / Forbidden / HTTP
        raise LookImageError(f"I couldn't download {a.filename} from Discord "
                             f"({type(e).__name__}) - it may have been deleted.")
    return check(raw, f"attachment {a.filename}")


def from_asset(name, root=None):
    """A file inside the repo's assets/ folder, confined to it."""
    root = root or ASSETS_DIR
    name = str(name or "").strip()
    if not name:
        raise LookImageError("asset: needs a file name from the assets folder.")
    try:
        path = pathsafe.confined_path(root, name)
    except pathsafe.ConfinementError:
        raise LookImageError("assets can only come from the assets folder - "
                             f"{name!r} points outside it.")
    if not os.path.isfile(path):
        have = sorted(f for f in os.listdir(root)
                      if os.path.splitext(f.lower())[1] in (".png", ".jpg", ".jpeg",
                                                            ".gif", ".webp")) \
            if os.path.isdir(root) else []
        raise LookImageError(
            f"there's no picture called {name} in the assets folder"
            + (f" (it has: {', '.join(have)})." if have else " (it has no pictures)."))
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    return check(raw, f"assets/{os.path.basename(path)}")


def _public_ip(text):
    try:
        return ipaddress.ip_address(text).is_global
    except ValueError:
        return False


async def _vet_host(url):
    """Refuse anything but https to a host that resolves to public addresses only."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise LookImageError("I only take pictures from https:// links - this one "
                             "isn't.")
    host = u.hostname
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, u.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise LookImageError(f"I couldn't find {host} on the internet.")
        addrs = [i[4][0] for i in infos]
    if not addrs or not all(_public_ip(a) for a in addrs):
        raise LookImageError(
            f"{host} points at a private or local address, and I won't fetch from "
            "those. Use a link to a public picture.")


async def fetch_url(url, max_bytes=MAX_BYTES):
    """Download `url` (public https only) and return the raw bytes."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _hop in range(MAX_REDIRECTS + 1):
                await _vet_host(url)
                async with session.get(url, allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308):
                        loc = r.headers.get("Location")
                        if not loc:
                            raise LookImageError("that link redirects nowhere.")
                        url = urljoin(url, loc)
                        continue
                    if r.status != 200:
                        raise LookImageError(
                            f"that link answered {r.status}, not a picture.")
                    if r.content_length and r.content_length > max_bytes:
                        raise LookImageError(
                            f"that picture is {r.content_length / 1048576:.1f} MB; "
                            f"my limit is {max_bytes // 1048576} MB.")
                    buf = bytearray()
                    async for chunk in r.content.iter_chunked(65536):
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise LookImageError(
                                f"that picture is bigger than my "
                                f"{max_bytes // 1048576} MB limit.")
                    return bytes(buf)
            raise LookImageError("that link redirects too many times.")
    except LookImageError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise LookImageError(f"I couldn't download that link ({type(e).__name__}).")


async def from_url(url):
    raw = await fetch_url(url)
    host = urlparse(url).hostname or "link"
    return check(raw, f"the link on {host}")


async def load(spec, message_getter, slot):
    """One source string -> Picture.

    `message_getter` is an async callable returning the message that attachments
    are taken from (or raising LookImageError if none was named) - called only if
    the spec is an attachment, so a request with just an asset or link never needs
    a message. `slot` names what the picture is for, for error sentences.
    """
    s = str(spec or "").strip()
    low = s.lower()
    if low == "attachment" or low.startswith("attachment:"):
        index = None
        if ":" in s:
            tail = s.split(":", 1)[1].strip()
            try:
                index = int(tail)
            except ValueError:
                raise LookImageError(f"{slot}: {s!r} isn't a valid attachment - "
                                     "use attachment or attachment:0.")
        msg = await message_getter()
        return await from_attachment(msg, index, label="that message")
    if low.startswith("asset:"):
        return from_asset(s.split(":", 1)[1])
    if low.startswith(("https://", "http://")):
        return await from_url(s)
    raise LookImageError(
        f"{slot}: I don't know where {s!r} is. Use attachment (or attachment:0), "
        "asset:<file in the assets folder>, or an https:// link.")
