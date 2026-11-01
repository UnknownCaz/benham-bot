"""
test_fitimage.py - fitting a picture to Discord's emoji and sticker limits, offline.

fitimage.py is where Benham started decoding pictures (Caz's yes 2026-11-01,
INTENT 60 follow-up): an emoji over 256 KB or a sticker that is not a 320x320
PNG/APNG/GIF under 512 KB is fitted instead of refused. What is pinned:

  A PICTURE THAT FITS IS UNTOUCHED. The same bytes come back, byte for byte, with
  no steps - so nothing that worked before the fitter changes behind it.

  THE LADDERS REACH THE LIMIT, AND SAY HOW. Scale (128 longest side for an emoji;
  exactly 320x320 for a sticker, padded or cropped), then colours, then frames.
  Every step is a plain sentence; an animation keeps its length and loop.

  THE FENCE HOLDS BEFORE ANY DECODE. A header that declares more than 40 million
  pixels, or more than 600 frames, is refused from the header alone; a file Pillow
  cannot read is "damaged", never a traceback.

  SAME BYTES IN, SAME BYTES OUT. The confirmed call re-runs the fit, so the
  preview's numbers must be the upload's: every fit run twice, sha256 compared,
  in-process and through the child process.

  THE CHILD PROCESS SPEAKS ITS PROTOCOL. python -m benham.core.fitimage reads the
  picture on stdin and writes one JSON line plus the bytes (binary-safe: a PNG
  signature carries \\r\\n), or one JSON refusal and exit 2. The bot's side, which
  kills a child past its timeout, is pinned in test_expressions.py beside the
  verbs that use it.

Every picture is generated here with Pillow; no binary fixtures.

    python tests/test_fitimage.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import hashlib
import io
import json
import random
import struct
import subprocess
import sys
import time
import zlib

from PIL import Image, ImageSequence

from benham import paths
from benham.core import fitimage

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!a}\n        want {want!a}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ pictures

def noise(size, seed, mode="RGB"):
    """Colour noise from a seeded generator - the worst case for every encoder."""
    bands = len(mode)
    return Image.frombytes(mode, size,
                           random.Random(seed).randbytes(size[0] * size[1] * bands))


def save(im, fmt, **kw):
    buf = io.BytesIO()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def gif(frames, duration=40, loop=0):
    return save(frames[0], "GIF", save_all=True, append_images=frames[1:],
                duration=duration, loop=loop)


def apng(frames, duration=100):
    return save(frames[0], "PNG", save_all=True, append_images=frames[1:],
                duration=duration, loop=0)


def frames_of(raw):
    """[(RGBA frame, duration)] as a viewer would play them."""
    im = Image.open(io.BytesIO(raw))
    return [(f.convert("RGBA"), f.info.get("duration") or 0)
            for f in ImageSequence.Iterator(im)]


def png_header(w, h):
    """A valid PNG signature and IHDR declaring w x h, then a few junk bytes - a
    'tiny file' whose header lies about its size."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">II", w, h)
                                         + b"\x08\x06\x00\x00\x00")
            + chunk(b"IDAT", b"\x78\x9c" + b"\x00" * 16) + chunk(b"IEND", b""))


def refusal(fn, *args):
    try:
        fn(*args)
        return None
    except fitimage.FitError as e:
        return (e.kind, str(e))


KB = 1024

# ================================================================== emoji
section("emoji - a noisy 1800x1800 PNG over 256 KB becomes 128x128 under 256 KB")
big = save(noise((1800, 1800), 1), "PNG")
check("the source really is over 256 KB", len(big) > fitimage.EMOJI_MAX_BYTES, True)
f = fitimage.fit_emoji(big, "image/png")
check("PNG stays PNG, 128x128", (f.media, f.width, f.height), ("image/png", 128, 128))
check("...under 256 KB", len(f.raw) <= fitimage.EMOJI_MAX_BYTES, True)
check("...and the bytes say so too", Image.open(io.BytesIO(f.raw)).size, (128, 128))
check("the steps say what was done, in words",
      f.steps[:1], ["scaled it down from 1800x1800 to 128x128"])
check("describe() is the preview's words", f.describe().startswith("PNG, 128x128, "),
      True)

section("emoji - aspect kept, longest side 128")
f = fitimage.fit_emoji(save(noise((1600, 400), 2), "PNG"), "image/png")
check("1600x400 -> 128x32", (f.width, f.height), (128, 32))

section("emoji - transparency kept")
rgba = noise((900, 900), 3, "RGBA")
rgba.paste((0, 0, 0, 0), (0, 0, 900, 300))               # top third clear
f = fitimage.fit_emoji(save(rgba, "PNG"), "image/png")
out = Image.open(io.BytesIO(f.raw)).convert("RGBA")
check("the clear band is still clear, the rest is not",
      (out.getpixel((64, 10))[3], out.getpixel((64, 100))[3] > 0), (0, True))

section("emoji - a picture that already fits is untouched, byte for byte")
small = save(noise((320, 320), 4).quantize(64), "PNG")
check("a 320x320 64-colour noisy PNG is under 256 KB", len(small) <= fitimage.EMOJI_MAX_BYTES, True)
f = fitimage.fit_emoji(small, "image/png")
check("the same bytes back, no steps", (f.raw == small, f.steps, f.changed),
      (True, [], False))
check("...with its facts read from the header", (f.width, f.height, f.frames),
      (320, 320, 1))

section("emoji - a JPEG stays a JPEG, and a phone photo stays upright")
photo = noise((2000, 1500), 5)
jpg = save(photo, "JPEG", quality=95)
check("the source JPEG is over 256 KB", len(jpg) > fitimage.EMOJI_MAX_BYTES, True)
f = fitimage.fit_emoji(jpg, "image/jpeg")
check("JPEG, 128x96, under 256 KB",
      (f.media, f.width, f.height, len(f.raw) <= fitimage.EMOJI_MAX_BYTES),
      ("image/jpeg", 128, 96, True))
check("...and its bytes are a JPEG", f.raw[:3], b"\xff\xd8\xff")
exif = Image.Exif()
exif[0x0112] = 6                                          # "rotate 90 to view"
turned = fitimage.fit_emoji(save(photo, "JPEG", quality=95, exif=exif.tobytes()),
                            "image/jpeg")
check("a rotation tag is applied before the shrink (2000x1500 shown tall -> 96x128)",
      ((turned.width, turned.height), turned.steps[0]),
      ((96, 128), "turned it upright (the photo's rotation tag)"))

section("emoji - a 60-frame noisy GIF over 256 KB")
gframes = [noise((400, 400), 100 + i).quantize(8) for i in range(60)]
src = gif(gframes, duration=40)
check("the source GIF is over 256 KB (and under lookimage's 8 MB)",
      fitimage.EMOJI_MAX_BYTES < len(src) <= 8 * 1048576, True)
t0 = time.time()
f = fitimage.fit_emoji(src, "image/gif")
played = frames_of(f.raw)
check("GIF stays GIF, 128x128, under 256 KB",
      (f.media, f.width, f.height, len(f.raw) <= fitimage.EMOJI_MAX_BYTES),
      ("image/gif", 128, 128, True))
check("...still animated, and the frame count is the real one",
      (f.frames > 1, f.frames, f.source_frames), (True, len(played), 60))
check("...it runs the same length (60 x 40 ms)", sum(d for _, d in played), 2400)
check("...and keeps its loop", Image.open(io.BytesIO(f.raw)).info.get("loop"), 0)
check("the steps: scaled, fewer colours, then frames dropped (they were needed)",
      (f.steps[0], "reduced it to 64 colours" in f.steps,
       any("60 -> " in s and "same length" in s for s in f.steps)),
      ("scaled it down from 400x400 to 128x128", True, True))
print(f"        ({time.time() - t0:.1f}s; {f.describe()})")

section("emoji - a GIF that only needs scaling keeps every frame")
calm = [Image.new("RGB", (512, 512), (i * 8, 40, 200 - i * 8)) for i in range(20)]
for i, fr in enumerate(calm):
    fr.paste(noise((300, 300), 300 + i), (106, 106))
src = gif([fr.quantize(256) for fr in calm], duration=[50 + i for i in range(20)])
check("this source is over 256 KB too", len(src) > fitimage.EMOJI_MAX_BYTES, True)
f = fitimage.fit_emoji(src, "image/gif")
played = frames_of(f.raw)
check("all 20 frames, their own durations (50..69 ms, GIF keeps 10 ms steps)",
      [d for _, d in played], [(50 + i) // 10 * 10 for i in range(20)])
check("no drop step, because none was needed",
      any("frame" in s for s in f.steps), False)

section("emoji - refused in plain words")
check("a WebP is not an emoji type",
      refusal(fitimage.fit_emoji, save(noise((64, 64), 6), "WEBP"), "image/webp"),
      ("format", "Discord takes a PNG, JPEG or GIF for an emoji, and that picture is "
                 "a WEBP."))
check("bytes Pillow cannot read are 'damaged', not a traceback",
      refusal(fitimage.fit_emoji, b"\x89PNG\r\n\x1a\n" + b"\x00" * 300000, "image/png"),
      ("damaged", fitimage.DAMAGED))

# ================================================================= sticker
WIDE = Image.new("RGB", (1000, 600), (0, 200, 0))       # green middle...
WIDE.paste((255, 0, 0), (0, 0, 200, 600))                # ...red left 200
WIDE.paste((0, 0, 255), (800, 0, 1000, 600))             # ...blue right 200
WIDE.paste(noise((600, 200), 7), (200, 200))             # and a noisy band (weight)

section("sticker - pad: fit inside, centred on a transparent 320x320")
f = fitimage.fit_sticker(save(WIDE, "PNG"), "image/png", "pad")
out = Image.open(io.BytesIO(f.raw)).convert("RGBA")
check("PNG, exactly 320x320, under 512 KB",
      (f.media, out.size, len(f.raw) <= fitimage.STICKER_MAX_BYTES),
      ("image/png", (320, 320), True))
check("1000x600 scales to 320x192, so 64 clear rows top and bottom",
      (out.getpixel((160, 0))[3], out.getpixel((160, 63))[3],
       out.getpixel((160, 319))[3], out.getpixel((160, 256))[3]), (0, 0, 0, 0))
check("...and the picture between them, edge to edge (red left, blue right)",
      (out.getpixel((2, 100))[:3], out.getpixel((317, 100))[:3],
       out.getpixel((160, 70))[3]), ((255, 0, 0), (0, 0, 255), 255))
check("the steps say it in words, and name the other mode",
      f.steps, ["scaled it down from 1000x600 to 320x192",
                "padded to 320x320: transparent bars top and bottom (say crop instead "
                "to crop the sides)"])

section("sticker - crop: the middle square, then 320x320")
f = fitimage.fit_sticker(save(WIDE, "PNG"), "image/png", "crop")
out = Image.open(io.BytesIO(f.raw)).convert("RGBA")
check("exactly 320x320, no transparency", (out.size, out.getextrema()[3]),
      ((320, 320), (255, 255)))
check("the middle 600x600 is all that is left: green at both edges, no red or blue",
      (out.getpixel((1, 20))[:3], out.getpixel((318, 20))[:3]),
      ((0, 200, 0), (0, 200, 0)))
check("the step names the crop", f.steps, ["cropped to the middle 600x600, then 320x320"])
f = fitimage.fit_sticker(save(Image.new("RGB", (100, 250), "white"), "PNG"),
                         "image/png", "pad")
check("a small tall picture is scaled UP to fit, with bars left and right",
      f.steps, ["scaled it up from 100x250 to 128x320",
                "padded to 320x320: transparent bars left and right (say crop instead "
                "to crop the top and bottom)"])

section("sticker - a 320x320 PNG under the limits is untouched")
ok = save(noise((320, 320), 8), "PNG")
f = fitimage.fit_sticker(ok, "image/png", "pad")
check("same bytes, no steps", (f.raw == ok, f.steps), (True, []))
f = fitimage.fit_sticker(ok, "image/png", "crop")
check("...in crop mode too", (f.raw == ok, f.steps), (True, []))

section("sticker - a JPEG becomes a PNG, and says so")
f = fitimage.fit_sticker(save(WIDE, "JPEG"), "image/jpeg", "pad")
check("PNG, 320x320", (f.media, f.width, f.height, f.raw[:8]),
      ("image/png", 320, 320, b"\x89PNG\r\n\x1a\n"))
check("...the last step says why", f.steps[-1],
      "made it a PNG (Discord takes no JPEG stickers)")

section("sticker - an APNG stays animated")
aframes = [Image.new("RGBA", (200, 120), (i * 60, 0, 255 - i * 60, 255)) for i in range(4)]
src = apng(aframes, duration=[100, 200, 300, 400])
check("the source is an APNG", fitimage.is_apng(src), True)
f = fitimage.fit_sticker(src, "image/png", "pad")
played = frames_of(f.raw)
check("an APNG again, 320x320, 4 frames",
      (fitimage.is_apng(f.raw), (f.width, f.height), f.frames, len(played)),
      (True, (320, 320), 4, 4))
check("...each frame its own colour, durations kept, bars clear",
      ([p.getpixel((160, 160))[:3] for p, _ in played], [d for _, d in played],
       played[2][0].getpixel((160, 5))[3]),
      ([(i * 60, 0, 255 - i * 60) for i in range(4)], [100, 200, 300, 400], 0))
check("describe() names it", f.describe().startswith("APNG, 320x320, "), True)

section("sticker - a GIF sticker: padded per frame, transparent bars, under 512 KB")
gw = [noise((480, 270), 400 + i).quantize(128) for i in range(30)]
src = gif(gw, duration=60)
f = fitimage.fit_sticker(src, "image/gif", "pad")
played = frames_of(f.raw)
check("GIF, 320x320, animated, under 512 KB",
      (f.media, (f.width, f.height), f.frames > 1, len(f.raw) <= fitimage.STICKER_MAX_BYTES),
      ("image/gif", (320, 320), True, True))
check("...every frame has clear bars and a drawn middle",
      all(p.getpixel((160, 2))[3] == 0 and p.getpixel((160, 160))[3] == 255
          for p, _ in played), True)
check("...and runs the same length (30 x 60 ms)", sum(d for _, d in played), 1800)
f = fitimage.fit_sticker(gif([Image.new("P", (320, 320), 3)] * 1), "image/gif", "pad")
check("a 320x320 GIF under 512 KB is untouched", f.steps, [])

section("sticker - refused in plain words")
check("an unknown mode", refusal(fitimage.fit_sticker, ok, "image/png", "stretch"),
      ("mode", "fit is pad or crop for a sticker (or none), not 'stretch'."))
check("a WebP", refusal(fitimage.fit_sticker, save(noise((64, 64), 9), "WEBP"),
                        "image/webp", "pad"),
      ("format", "a sticker has to be a PNG, APNG or GIF, and that picture is a WEBP."))

# ============================================================ determinism
section("same bytes in, same bytes out")
for label, fn, args in (
        ("emoji PNG", fitimage.fit_emoji, (big, "image/png")),
        ("emoji GIF", fitimage.fit_emoji, (gif(gframes[:20]), "image/gif")),
        ("sticker pad", fitimage.fit_sticker, (save(WIDE, "PNG"), "image/png", "pad")),
        ("sticker APNG", fitimage.fit_sticker, (apng(aframes), "image/png", "crop")),
        ("sticker GIF", fitimage.fit_sticker, (gif(gw[:10]), "image/gif", "pad"))):
    a, b = fn(*args), fn(*args)
    check(f"{label}: run twice, one sha256", (sha(a.raw), a.steps), (sha(b.raw), b.steps))

# ================================================================== fence
section("the fence - refused from the header, before any decode")
t0 = time.time()
r = refusal(fitimage.fit_emoji, png_header(50000, 50000), "image/png")
check("a tiny file declaring 50000x50000 is refused as over the cap, not as damaged",
      (r or ("", ""))[0], "cap")
check("...in plain words", "million pixels" in (r or ("", ""))[1], True)
r = refusal(fitimage.fit_sticker, png_header(7000, 7000), "image/png", "pad")
check("7000x7000 (49 million) is over the 40 million cap, and the sentence says the size",
      r, ("cap", "that picture is 7000x7000, over 40 million pixels, more than I'll "
                 "work on - send a smaller one."))
check("...both refused at once (no decode was attempted)", time.time() - t0 < 2, True)
check("Pillow's own backstop is set to the same cap",
      (Image.MAX_IMAGE_PIXELS, fitimage.MAX_PIXELS), (40_000_000, 40_000_000))
many = gif([Image.new("RGB", (2, 2), (255, 0, 0) if i % 2 else (0, 0, 255))
            for i in range(601)], duration=20)
r = refusal(fitimage.fit_emoji, many, "image/gif")
check("601 frames is over the 600-frame cap",
      r, ("cap", "that animation has 601 frames, and the most I'll work on is 600 - "
                 "send a shorter one."))

# ============================================================ child process
CHILD = [sys.executable, "-m", "benham.core.fitimage"]


def child(args, raw):
    p = subprocess.run(CHILD + args, input=raw, capture_output=True, cwd=paths.ROOT,
                       timeout=120)
    head, _, body = p.stdout.partition(b"\n")
    return p.returncode, json.loads(head.decode("ascii")), body


section("the child process - its protocol")
pic = save(noise((700, 500), 10), "PNG")                 # ~1 MB, under 8 MB
code, report, body = child(["emoji", "auto"], pic)
check("exit 0, ok, and exactly report['bytes'] bytes after the line",
      (code, report["ok"], len(body) == report["bytes"]), (0, True, True))
check("the bytes are a whole PNG (the \\r\\n in its signature survived the pipe)",
      (body[:8], Image.open(io.BytesIO(body)).size), (b"\x89PNG\r\n\x1a\n", (128, 91)))
check("the report carries the facts and the steps",
      (report["media"], report["width"], report["height"], report["frames"],
       report["steps"][0]),
      ("image/png", 128, 91, 1, "scaled it down from 700x500 to 128x91"))
check("Fitted.from_report rebuilds the same thing the in-process fit makes",
      sha(fitimage.Fitted.from_report(report, body).raw),
      sha(fitimage.fit_emoji(pic, "image/png").raw))
code2, report2, body2 = child(["emoji", "auto"], pic)
check("the child is deterministic too", (sha(body2), report2), (sha(body), report))
code, report, body = child(["sticker", "crop"], save(WIDE, "PNG"))
check("sticker crop through the child", (code, report["width"], report["height"],
                                         report["steps"]),
      (0, 320, 320, ["cropped to the middle 600x600, then 320x320"]))
code, report, body = child(["emoji", "auto"], png_header(50000, 50000))
check("a refusal is exit 2 and one JSON line, nothing after it",
      (code, report["ok"], report["kind"], body), (2, False, "cap", b""))
code, report, _ = child(["emoji", "auto"], b"not a picture at all")
check("bytes that are no picture: exit 2, a sentence",
      (code, report["kind"], report["refused"]),
      (2, "format", "that isn't a picture I can work on (I go by the file's bytes, "
                    "not its name)."))
code, report, _ = child(["emoji", "auto"], b"x" * (8 * 1048576 + 1))
check("more than 8 MB on stdin is refused, not read whole",
      (code, report["kind"]), (2, "cap"))
code, report, _ = child(["banner"], pic)
check("an unknown target is a usage refusal", (code, report["kind"]), (2, "usage"))

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
