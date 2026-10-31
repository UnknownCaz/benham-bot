"""
looksound.py - the sound behind a soundboard upload (INTENT 60, Stage 8 A).

The sibling of lookimage.py. `create_sound` (in capabilities.py, where every gate
is readable in one place) needs "which sound?" turned into checked bytes, and this
file is only that half.

One grammar names the source, the same one pictures use:

    attachment            the ONE file on the named message
    attachment:2          file number 2 (0-based) on the named message
    asset:beep.mp3        a file in the repo's assets/ folder
    https://host/a.mp3    a public https link

THE BYTES ARE THE FACT. Discord's soundboard takes MP3 or OGG, and the type is
read from the first bytes - an MPEG audio frame (Layer III, after any ID3 tag) or
an Ogg page - never from the filename or a content-type header, both of which are
chosen by whoever made the file.

THE URL ROUTE IS lookimage's FENCE, called with this file's words: https only,
public addresses only (no loopback, LAN or tailnet), redirects re-vetted hop by
hop, a streaming cap that holds when Content-Length lies. One fence for both, so
the two routes cannot drift apart.

THE LENGTH IS MEASURED FROM THE BYTES where that is possible without a decoder:
an MP3's frames are walked and their samples counted; an Ogg file's last page
carries the stream's position in samples (Vorbis states its rate, Opus is always
48 kHz less its pre-skip). Anything else - an Ogg holding another codec, an MP3
whose frames stop making sense halfway - reports "length unknown", and the caller
says Discord will enforce its 5.2 second cap itself. A number that might be wrong
is never reported as a measurement.

Every failure is a LookSoundError whose message is a plain sentence.
"""

import hashlib
import os
import struct
from urllib.parse import urlparse

from benham.core import lookimage
from benham.core import pathsafe

ASSETS_DIR = lookimage.ASSETS_DIR

MAX_BYTES = 512 * 1024      # Discord's soundboard cap
MAX_SECONDS = 5.2           # Discord's soundboard cap
FORMATS = ("audio/mpeg", "audio/ogg")
_EXT = {"audio/mpeg": ".mp3", "audio/ogg": ".ogg"}
_KIND = {"audio/mpeg": "MP3", "audio/ogg": "OGG"}


class LookSoundError(Exception):
    """A plain-words reason a sound cannot be used. Never a traceback."""


class Sound:
    """Checked sound bytes plus the facts the preview prints."""

    def __init__(self, raw, media, seconds, source):
        self.raw = raw
        self.media = media
        self.seconds = seconds          # float, or None when the bytes can't say
        self.source = source            # a human phrase: "attachment beep.mp3"
        self.sha = hashlib.sha256(raw).hexdigest()[:10]

    @property
    def ext(self):
        return _EXT[self.media]

    def describe(self):
        length = (f"{self.seconds:.2f} s" if self.seconds is not None
                  else "length unknown")
        return (f"{_KIND[self.media]}, {length}, {lookimage._size_words(len(self.raw))}, "
                f"from {self.source} (#{self.sha})")


# --------------------------------------------------------------------------
# What the bytes are
# --------------------------------------------------------------------------

# MPEG audio, Layer III. Index by the header's bitrate bits; 0 (free format) and
# 15 (bad) are refused rather than guessed at.
_KBPS_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_KBPS_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
# Version bits: 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5 (1 is reserved).
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _id3_end(raw):
    """Where the audio starts after an ID3v2 tag (0 when there is none)."""
    if raw[:3] != b"ID3" or len(raw) < 10:
        return 0
    size = 0
    for b in raw[6:10]:                 # "syncsafe": 7 bits per byte
        size = (size << 7) | (b & 0x7F)
    footer = 10 if raw[5] & 0x10 else 0
    return 10 + size + footer


def _mp3_frame(raw, i):
    """(frame_bytes, samples, rate) for a Layer III frame header at `i`, or None."""
    if i < 0 or i + 4 > len(raw):
        return None
    b1, b2 = raw[i + 1], raw[i + 2]
    if raw[i] != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
    if version == 1 or layer != 1:      # reserved version, or not Layer III
        return None
    bi, ri, pad = b2 >> 4, (b2 >> 2) & 3, (b2 >> 1) & 1
    if bi in (0, 15) or ri == 3:
        return None
    rate = _RATES[version][ri]
    if version == 3:
        return 144000 * _KBPS_V1[bi] // rate + pad, 1152, rate
    return 72000 * _KBPS_V2[bi] // rate + pad, 576, rate


def sniff(raw):
    """The media type the BYTES say this is - audio/mpeg, audio/ogg - or None."""
    if not raw:
        return None
    if raw[:4] == b"OggS":
        return "audio/ogg"
    if _mp3_frame(raw, _id3_end(raw)) is not None:
        return "audio/mpeg"
    return None


def ext_for(raw):
    """File extension for these bytes (".bin" when they are not a known sound) -
    used to name the saved copy of a sound that is about to be deleted."""
    return _EXT.get(sniff(raw), ".bin")


def _mp3_seconds(raw):
    i = _id3_end(raw)
    first = _mp3_frame(raw, i)
    if first is None:
        return None
    samples = 0.0
    frames = 0
    head = True
    last_len = 0
    while True:
        f = _mp3_frame(raw, i)
        if f is None:
            break
        flen, spf, rate = f
        if flen <= 4:
            return None
        if i + flen > len(raw):         # a cut-off last frame: count what is there
            last_len = flen
            break
        # A Xing/Info/VBRI frame at the head is the encoder's table, not sound.
        if not (head and any(tag in raw[i:i + 64] for tag in (b"Xing", b"Info", b"VBRI"))):
            samples += spf / rate
            frames += 1
        head = False
        last_len = flen
        i += flen
    tail = raw[i:]
    tidy = (not tail or tail[:3] == b"TAG" or tail[:8] == b"APETAGEX"
            or tail[:11] == b"LYRICSBEGIN" or len(tail) < last_len
            or not tail.strip(b"\x00"))
    if frames == 0 or not tidy:
        return None                     # the frames stopped making sense: don't guess
    return samples


def _ogg_seconds(raw):
    if raw[:4] != b"OggS" or len(raw) < 28:
        return None
    start = 27 + raw[26]
    serial = raw[14:18]
    packet = raw[start:start + 20]
    if packet[:7] == b"\x01vorbis" and len(packet) >= 16:
        rate, skip = struct.unpack("<I", packet[12:16])[0], 0
    elif packet[:8] == b"OpusHead" and len(packet) >= 12:
        rate, skip = 48000, struct.unpack("<H", packet[10:12])[0]
    else:
        return None                     # another codec in an Ogg: no claim
    if not rate:
        return None
    end = len(raw)
    while True:
        j = raw.rfind(b"OggS", 0, end)
        if j < 0:
            return None
        if j + 18 <= len(raw) and raw[j + 4] == 0 and raw[j + 14:j + 18] == serial:
            granule = struct.unpack("<q", raw[j + 6:j + 14])[0]
            if granule >= 0:
                return max(0, granule - skip) / rate
        end = j


def seconds(raw, media):
    """How long the sound lasts, from its bytes, or None when they can't say."""
    try:
        if media == "audio/mpeg":
            return _mp3_seconds(raw)
        if media == "audio/ogg":
            return _ogg_seconds(raw)
    except (struct.error, IndexError, KeyError):
        pass
    return None


def check(raw, source, max_bytes=MAX_BYTES):
    """Raw bytes -> Sound, or LookSoundError saying why not."""
    if not raw:
        raise LookSoundError(f"{source} is empty - there is no sound in it.")
    if len(raw) > max_bytes:
        raise LookSoundError(
            f"{source} is {lookimage._size_words(len(raw))}; Discord's soundboard "
            f"takes at most {lookimage._limit_words(max_bytes)}. Trim it or send a "
            "shorter one.")
    media = sniff(raw)
    if media not in FORMATS:
        raise LookSoundError(
            f"{source} isn't an MP3 or OGG sound (I go by the file's actual bytes, "
            "not its name), so Discord's soundboard wouldn't take it.")
    secs = seconds(raw, media)
    if secs is not None and secs > MAX_SECONDS:
        raise LookSoundError(
            f"{source} is {secs:.2f} seconds long; Discord's soundboard takes at "
            f"most {MAX_SECONDS}. Trim it and ask again.")
    return Sound(raw, media, secs, source)


# --------------------------------------------------------------------------
# Getting bytes: attachment / asset / url
# --------------------------------------------------------------------------

async def from_attachment(message, index, label="the message"):
    """The `index`-th attachment of `message` (None = the only one)."""
    atts = list(message.attachments)
    if not atts:
        raise LookSoundError(f"{label} has no attachment - attach the sound to the "
                             "message and ask again.")
    if index is None:
        if len(atts) > 1:
            raise LookSoundError(
                f"{label} has {len(atts)} attachments, so I need to know which: "
                "say attachment:0, attachment:1 and so on (counting from 0).")
        index = 0
    if not 0 <= index < len(atts):
        raise LookSoundError(f"{label} has {len(atts)} attachment(s), so "
                             f"attachment:{index} doesn't exist (they count from 0).")
    a = atts[index]
    if a.size and a.size > MAX_BYTES:
        # The metadata is the uploader's claim - check() re-checks the real bytes -
        # but refusing here saves downloading something certain to be refused.
        raise LookSoundError(
            f"{a.filename} is {lookimage._size_words(a.size)}; Discord's soundboard "
            f"takes at most {lookimage._limit_words(MAX_BYTES)}.")
    try:
        raw = await a.read()
    except Exception as e:  # noqa: BLE001 - discord.NotFound / Forbidden / HTTP
        raise LookSoundError(f"I couldn't download {a.filename} from Discord "
                             f"({type(e).__name__}) - it may have been deleted.")
    return check(raw, f"attachment {a.filename}")


def from_asset(name, root=None):
    """A file inside the repo's assets/ folder, confined to it."""
    root = root or ASSETS_DIR
    name = str(name or "").strip()
    if not name:
        raise LookSoundError("asset: needs a file name from the assets folder.")
    try:
        path = pathsafe.confined_path(root, name)
    except pathsafe.ConfinementError:
        raise LookSoundError("assets can only come from the assets folder - "
                             f"{name!r} points outside it.")
    if not os.path.isfile(path):
        have = sorted(f for f in os.listdir(root)
                      if os.path.splitext(f.lower())[1] in (".mp3", ".ogg")) \
            if os.path.isdir(root) else []
        raise LookSoundError(
            f"there's no sound called {name} in the assets folder"
            + (f" (it has: {', '.join(have)})." if have else " (it has no sounds)."))
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    return check(raw, f"assets/{os.path.basename(path)}")


async def fetch_url(url, max_bytes=MAX_BYTES):
    """Download a SOUND from `url` through lookimage's one fence."""
    return await lookimage.fetch_public(url, max_bytes, "sound", LookSoundError)


async def from_url(url):
    raw = await fetch_url(url)
    return check(raw, f"the link on {urlparse(url).hostname or 'link'}")


async def load(spec, message_getter, slot="sound"):
    """One source string -> Sound.

    `message_getter` is an async callable returning the message attachments are
    taken from (or raising if none was named) - called only for an attachment.
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
                raise LookSoundError(f"{slot}: {s!r} isn't a valid attachment - "
                                     "use attachment or attachment:0.")
        msg = await message_getter()
        return await from_attachment(msg, index, label="that message")
    if low.startswith("asset:"):
        return from_asset(s.split(":", 1)[1])
    if low.startswith(("https://", "http://")):
        return await from_url(s)
    raise LookSoundError(
        f"{slot}: I don't know where {s!r} is. Use attachment (or attachment:0), "
        "asset:<file in the assets folder>, or an https:// link.")
