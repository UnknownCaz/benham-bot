"""
fitimage.py - fit a picture to Discord's emoji and sticker limits.

Discord's own upload screen shrinks a big picture for you; Benham used to refuse
one ("shrink it and ask again") and make Caz do it on his phone. Caz said yes to
closing that gap on 2026-11-01 08:59 local ("Lets do it!") and confirmed the
scope at 09:06 ("Build 1 is good"): pictures he sends from his DM, or a session
over the CLI, are fitted before create_emoji / create_sticker uploads them.
(INTENT decision 60, Stage 8 follow-up.)

THE POSTURE CHANGE, AND ITS PRICE. lookimage.dimensions() reads only header
bytes, on purpose: "No imaging library is installed on the bot's machine and
decoding a stranger's image buys nothing here". Fitting a picture means decoding
it, so that posture changes here - and only behind this fence, all three parts:

  1. The bytes have already passed lookimage.check (8 MB ceiling, type sniffed
     from the bytes). Nothing in this file fetches anything.
  2. A pixel and frame cap BEFORE any decode. Image.open is lazy: the size and
     the frame count come from headers, and a picture over MAX_PIXELS a frame or
     MAX_FRAMES frames is refused in one sentence. Image.MAX_IMAGE_PIXELS is set
     to the same cap and Pillow's DecompressionBombWarning is raised as an error
     inside every fit, so Pillow itself refuses past the cap as a backstop - it
     re-checks when a GIF frame grows the canvas mid-file, which no header can.
  3. The decode runs in a CHILD PROCESS with a timeout:
         python -m benham.core.fitimage <emoji|sticker> <mode>
     reads the picture on stdin and writes one JSON line (the report) and then
     the fitted bytes to stdout - or one JSON refusal line and exit 2. The bot's
     side (capabilities._fit_in_child) kills the child past its timeout. A
     Pillow crash or a hostile file therefore never takes the bot down.

The bot process imports this module for Fitted, FitError and the limits, and
never imports Pillow: _pil() loads it only when a fitter actually runs, which is
in the child (and in the offline tests).

DETERMINISTIC, because the confirmed call re-runs the action with the same
params: the same bytes and mode in give the same bytes out (no timestamps, no
randomness; Pillow's quantizers and zlib are deterministic), so the numbers the
preview shows are the upload's.

THE LADDERS stop as soon as the picture fits:
  emoji   (longest side 128 px, aspect kept, 256 KB) - a picture already under
          256 KB comes back byte for byte. PNG: scale, compact re-save, then 256
          colours. JPEG: scale, then lower quality. GIF: scale every frame, then
          256 -> 128 -> 64 colours, then drop every other frame (the kept frame
          shows for both, so it runs the same length), repeated.
  sticker (exactly 320x320, 512 KB) - pad (default: fit inside, centred on a
          transparent canvas) or crop (centre square). PNG stays PNG, APNG stays
          APNG, GIF stays GIF, a JPEG becomes a PNG (Discord takes no JPEG
          stickers). The colour and frame steps are the emoji ladder's.
A ladder that runs out is a plain sentence saying what it reached.
"""

import io
import json
import sys
import warnings

EMOJI_MAX_BYTES = 256 * 1024
EMOJI_SIDE = 128
STICKER_MAX_BYTES = 512 * 1024
STICKER_SIDE = 320
MAX_PIXELS = 40_000_000         # a frame; a 6000x6000 photo is 36 million
MAX_FRAMES = 600
MAX_INPUT = 8 * 1024 * 1024     # lookimage.MAX_BYTES - the child refuses more
STICKER_MODES = ("pad", "crop")

PNG, JPEG, GIF = "image/png", "image/jpeg", "image/gif"
_KIND = {PNG: "PNG", JPEG: "JPEG", GIF: "GIF", "image/webp": "WEBP"}
_PALETTES = (256, 128, 64)
_JPEG_QUALITY = (90, 80, 70, 60)

DAMAGED = "I couldn't open the picture to fit it myself - it may be damaged."


class FitError(Exception):
    """A plain-words reason a picture could not be fitted.

    kind says which family, so the caller can word it: "damaged" (Pillow could
    not read it - the caller puts today's limit sentence in front), "cap" (the
    fence), "format", "mode", "too_big" (the ladder ran out), "timeout" and
    "crashed" (the child process)."""

    def __init__(self, message, kind="too_big"):
        super().__init__(message)
        self.kind = kind


def size_words(n):
    """A byte count as a person says it - the same words as lookimage._size_words
    (repeated here so the child process imports nothing it does not need)."""
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def kind_of(media, raw=b""):
    """PNG, APNG, JPEG, GIF - the type as a person says it."""
    if media == PNG and raw and is_apng(raw):
        return "APNG"
    return _KIND.get(media, (media or "?").split("/")[-1].upper())


def is_apng(raw):
    """An animated PNG carries an acTL chunk before its first IDAT (header walk,
    no decode - the same reading as capabilities._is_apng)."""
    i = 8
    while i + 8 <= len(raw):
        length = int.from_bytes(raw[i:i + 4], "big")
        tag = raw[i + 4:i + 8]
        if tag == b"acTL":
            return True
        if tag in (b"IDAT", b"IEND"):
            return False
        i += 12 + length
    return False


class Fitted:
    """Fitted bytes plus the facts the preview prints.

    steps is a plain-words list of what was done, empty when the picture
    already fit - and then raw IS the input, byte for byte."""

    def __init__(self, raw, media, width, height, frames=1, steps=(),
                 source_frames=None):
        self.raw = raw
        self.media = media
        self.width = width
        self.height = height
        self.frames = frames
        self.steps = list(steps)
        self.source_frames = frames if source_frames is None else source_frames

    @property
    def changed(self):
        return bool(self.steps)

    def describe(self):
        """"PNG, 128x128, 31 KB" (", 24 frames" when it animates)."""
        out = f"{kind_of(self.media, self.raw)}, {self.width}x{self.height}, " \
              f"{size_words(len(self.raw))}"
        if self.frames > 1:
            out += f", {self.frames} frames"
        return out

    def report(self):
        """Everything but the bytes, as plain JSON types."""
        return {"media": self.media, "width": self.width, "height": self.height,
                "frames": self.frames, "source_frames": self.source_frames,
                "steps": list(self.steps), "bytes": len(self.raw)}

    @classmethod
    def from_report(cls, report, raw):
        return cls(raw, report["media"], int(report["width"]), int(report["height"]),
                   int(report.get("frames") or 1), report.get("steps") or [],
                   int(report.get("source_frames") or 1))


# --------------------------------------------------------------------------
# The fence
# --------------------------------------------------------------------------

def _pil():
    """Pillow, with the backstop cap set. Imported here, never at module level,
    so the bot process (which imports this file) never loads it."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return Image


def _too_big(w=None, h=None):
    dims = f"{w}x{h}, " if w else ""
    return (f"that picture is {dims}over {MAX_PIXELS // 1_000_000} million pixels, "
            "more than I'll work on - send a smaller one.")


def _guarded(fn, *args):
    """Run one fitter with Pillow's bomb warning as an error, every failure a
    FitError. Anything Pillow raises on a file it cannot read is "damaged" -
    a hostile file may raise anything, so the net is wide on purpose."""
    Image = _pil()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            return fn(Image, *args)
        except FitError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise FitError(_too_big(), "cap")
        except Exception:  # noqa: BLE001 - see the docstring
            raise FitError(DAMAGED, "damaged")


def _open(Image, raw):
    """(lazy image, width, height, frames) - headers only, the cap checked here.

    Pillow's warning is held back for the open itself so this check, which can
    name the size, is the one that refuses; past twice the cap Pillow's own
    DecompressionBombError still stops the open, and after it the warning is an
    error again (_guarded) for anything a later frame declares."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        img = Image.open(io.BytesIO(raw))
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise FitError(_too_big(w, h), "cap")
    n = int(getattr(img, "n_frames", 1) or 1)
    if n > MAX_FRAMES:
        raise FitError(f"that animation has {n} frames, and the most I'll work on is "
                       f"{MAX_FRAMES} - send a shorter one.", "cap")
    return img, w, h, n


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def _within(w, h, side):
    """(w, h) scaled so the longest side is `side`, aspect kept."""
    if w >= h:
        return side, max(1, round(h * side / w))
    return max(1, round(w * side / h)), side


def _resize(Image, frame, size):
    if frame.size == size:
        return frame
    return frame.resize(size, Image.Resampling.LANCZOS)


class _Shape:
    """One geometric transform, applied the same way to every frame, plus the
    words for it."""

    def __init__(self, Image, w, h, target, mode):
        self.Image = Image
        self.target = target            # "emoji" | "sticker"
        self.mode = mode
        self.steps = []
        self.box = None
        if target == "emoji":
            self.size = _within(w, h, EMOJI_SIDE) if max(w, h) > EMOJI_SIDE else (w, h)
            if self.size != (w, h):
                self.steps.append(f"scaled it down from {w}x{h} to "
                                  f"{self.size[0]}x{self.size[1]}")
            self.canvas = self.size
            return
        side = STICKER_SIDE
        self.canvas = (side, side)
        if mode == "crop":
            s = min(w, h)
            left, top = (w - s) // 2, (h - s) // 2
            self.box = (left, top, left + s, top + s)
            self.size = (side, side)
            if w != h:
                self.steps.append(f"cropped to the middle {s}x{s}"
                                  + (f", then {side}x{side}" if s != side else ""))
            elif s != side:
                self.steps.append(f"scaled it {'down' if s > side else 'up'} from "
                                  f"{w}x{h} to {side}x{side}")
            return
        self.size = _within(w, h, side)
        if self.size != (w, h):
            self.steps.append(f"scaled it {'down' if max(w, h) > side else 'up'} from "
                              f"{w}x{h} to {self.size[0]}x{self.size[1]}")
        if self.size != self.canvas:
            if self.size[0] > self.size[1]:
                self.steps.append(f"padded to {side}x{side}: transparent bars top and "
                                  "bottom (say crop instead to crop the sides)")
            else:
                self.steps.append(f"padded to {side}x{side}: transparent bars left and "
                                  "right (say crop instead to crop the top and bottom)")

    def apply(self, frame):
        """An RGBA (or RGB, for a plain emoji) frame -> the shaped frame."""
        if self.box is not None:
            frame = frame.crop(self.box)
        frame = _resize(self.Image, frame, self.size)
        if self.size == self.canvas:
            return frame
        out = self.Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        out.paste(frame.convert("RGBA"),
                  ((self.canvas[0] - self.size[0]) // 2,
                   (self.canvas[1] - self.size[1]) // 2))
        return out


# --------------------------------------------------------------------------
# Encoders
# --------------------------------------------------------------------------

def _has_alpha(frame):
    return frame.mode == "RGBA" and frame.getchannel("A").getextrema()[0] < 255


def _png_bytes(frame):
    buf = io.BytesIO()
    frame.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _quantized_png(Image, frame, colors):
    method = (Image.Quantize.FASTOCTREE if frame.mode == "RGBA"
              else Image.Quantize.MEDIANCUT)
    return _png_bytes(frame.quantize(colors=colors, method=method,
                                     dither=Image.Dither.NONE))


def _jpeg_bytes(frame, quality):
    buf = io.BytesIO()
    frame.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _gif_frame(Image, frame, colors, alpha):
    """An RGBA frame -> a P frame with exactly `colors` palette entries. With
    transparency the last entry is the transparent one (GIF has 1-bit alpha, so a
    pixel is either drawn or not, cut at half opacity)."""
    rgb = frame.convert("RGB")
    k = colors - 1 if alpha else colors
    p = rgb.quantize(colors=k, method=Image.Quantize.MEDIANCUT,
                     dither=Image.Dither.NONE)
    pal = (p.getpalette() or [])[:3 * k]
    pal += [0] * (3 * k - len(pal))
    if alpha:
        pal += [0, 0, 0]
        clear = frame.getchannel("A").point(lambda a: 255 if a < 128 else 0)
        p.putpalette(pal)
        p.paste(k, mask=clear)
        p.info["transparency"] = k
    else:
        p.putpalette(pal)
    return p


def _gif_bytes(pframes, durations, loop, alpha):
    """P frames -> GIF bytes. With transparency every frame is drawn on a cleared
    canvas (disposal 2), so a pixel that turns transparent really does; without,
    each frame draws over the last (disposal 1) and only what changed is stored.
    optimize=False keeps the palette exactly as built."""
    buf = io.BytesIO()
    kw = {"format": "GIF", "save_all": True, "append_images": pframes[1:],
          "duration": list(durations), "disposal": 2 if alpha else 1,
          "optimize": False}
    if loop is not None:
        kw["loop"] = loop
    pframes[0].save(buf, **kw)
    return buf.getvalue()


def _apng_bytes(frames, durations, loop):
    buf = io.BytesIO()
    frames[0].save(buf, format="PNG", save_all=True, append_images=frames[1:],
                   duration=list(durations), loop=loop or 0, optimize=True)
    return buf.getvalue()


def _shared_palette(Image, frames, colors):
    """RGBA frames -> P frames sharing ONE palette (an APNG has one PLTE), alpha
    kept: the frames are stacked into one tall picture, quantized once, and cut
    apart again."""
    w, h = frames[0].size
    tall = Image.new("RGBA", (w, h * len(frames)))
    for i, f in enumerate(frames):
        tall.paste(f, (0, h * i))
    q = tall.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                      dither=Image.Dither.NONE)
    return [q.crop((0, h * i, w, h * (i + 1))) for i in range(len(frames))]


def _drop(frames, durations, rounds):
    """Keep every 2**rounds-th frame; each kept frame shows for the whole group it
    stands for, so the animation runs the same length."""
    step = 2 ** rounds
    kept = frames[::step]
    times = [sum(durations[i:i + step]) for i in range(0, len(durations), step)]
    return kept, times


def _read_frames(img, n, shape):
    """Every frame, composited (Pillow applies disposal and blending as it seeks),
    shaped as it is read so only small frames are ever held; durations in ms."""
    frames, durations = [], []
    for i in range(n):
        img.seek(i)
        if img.size[0] * img.size[1] > MAX_PIXELS:      # a GIF frame can grow it
            raise FitError(_too_big(*img.size), "cap")
        durations.append(img.info.get("duration") or 0)
        frames.append(shape.apply(img.convert("RGBA")))
    return frames, durations


def _frame_count(Image, raw):
    """Frames in what was written (the encoders fold identical neighbours)."""
    return int(getattr(Image.open(io.BytesIO(raw)), "n_frames", 1) or 1)


# --------------------------------------------------------------------------
# The ladders
# --------------------------------------------------------------------------

def _drop_words(before, after, rounds):
    if rounds == 1:
        return (f"dropped every other frame ({before} -> {after} frames); each kept "
                "frame shows for two, so it runs the same length")
    return (f"kept one frame in {2 ** rounds} ({before} -> {after} frames); each kept "
            "frame shows for the ones it stands for, so it runs the same length")


def _fit_animation(Image, img, n, shape, limit, what, encoding, steps):
    """The GIF ("gif") or APNG ("apng") ladder: every frame shaped, then 256 ->
    128 -> 64 colours (an APNG tries full colour first), then every other frame
    dropped until it fits - never below 2 frames. Durations and the loop count
    are kept; disposal is chosen so the composited frames replay as they were
    (Pillow hands every frame back already composited, so the source's own
    disposal has done its work)."""
    loop = img.info.get("loop")
    frames, durations = _read_frames(img, n, shape)
    alpha = encoding == "gif" and any(_has_alpha(f) for f in frames)
    levels = list(_PALETTES) if encoding == "gif" else [None] + list(_PALETTES)
    cache = {}

    def encode(colors, rounds):
        _, times = _drop(frames, durations, rounds)
        if colors is None:
            return _apng_bytes(frames[::2 ** rounds], times, loop)
        if colors not in cache:
            cache[colors] = ([_gif_frame(Image, f, colors, alpha) for f in frames]
                             if encoding == "gif"
                             else _shared_palette(Image, frames, colors))
        pframes = cache[colors][::2 ** rounds]
        if encoding == "gif":
            return _gif_bytes(pframes, times, loop, alpha)
        return _apng_bytes(pframes, times, loop)

    out, colors = None, None
    for colors in levels:
        out = encode(colors, 0)
        if len(out) <= limit:
            break
    rounds = 0
    while len(out) > limit and len(frames[::2 ** (rounds + 1)]) >= 2:
        rounds += 1
        out = encode(colors, rounds)
    got = _frame_count(Image, out)
    if len(out) > limit:
        raise FitError(
            f"I shrank it to {shape.canvas[0]}x{shape.canvas[1]}, {colors} colours and "
            f"{got} frames and it is still {size_words(len(out))}; Discord takes "
            f"{limit // 1024} KB for {what} - send a shorter or smaller "
            f"{'GIF' if encoding == 'gif' else 'animation'}.")
    if colors is not None and (colors < 256 or encoding != "gif"):
        steps.append(f"reduced it to {colors} colours")
    if rounds:
        steps.append(_drop_words(n, got, rounds))
    if not steps:
        steps.append(f"re-saved it as a compact {'GIF' if encoding == 'gif' else 'APNG'}")
    return Fitted(out, GIF if encoding == "gif" else PNG, shape.canvas[0],
                  shape.canvas[1], got, steps, n)


def _fit_still(Image, base, shape, limit, what, encoding, steps, n):
    """One frame: shaped, then saved compact; a PNG then tries 256 colours, a
    JPEG lower qualities."""
    frame = shape.apply(base)
    if encoding == "jpeg":
        frame = frame.convert("RGB")
        out, quality = None, None
        for quality in _JPEG_QUALITY:
            out = _jpeg_bytes(frame, quality)
            if len(out) <= limit:
                break
        if len(out) > limit:
            raise FitError(
                f"I shrank it to {frame.width}x{frame.height} at JPEG quality "
                f"{quality} and it is still {size_words(len(out))}; Discord takes "
                f"{limit // 1024} KB for {what} - send a smaller picture.")
        if quality < _JPEG_QUALITY[0]:
            steps.append(f"lowered the JPEG quality to {quality}")
        media = JPEG
    else:
        out = _png_bytes(frame)
        if len(out) > limit:
            out = _quantized_png(Image, frame, 256)
            steps.append("reduced it to 256 colours")
        if len(out) > limit:
            raise FitError(
                f"I shrank it to {frame.width}x{frame.height} and 256 colours and it is "
                f"still {size_words(len(out))}; Discord takes {limit // 1024} KB for "
                f"{what} - send a smaller picture.")
        media = PNG
    if not steps:
        steps.append(f"re-saved it as a compact {'JPEG' if media == JPEG else 'PNG'}")
    return Fitted(out, media, frame.width, frame.height, 1, steps, n)


def _upright(img, steps):
    """A photo's EXIF rotation applied, so re-saving (which drops EXIF) does not
    turn it on its side."""
    from PIL import ImageOps
    try:
        turn = img.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001 - a broken EXIF block is no reason to stop
        turn = 1
    if turn in (2, 3, 4, 5, 6, 7, 8):
        steps.append("turned it upright (the photo's rotation tag)")
        return ImageOps.exif_transpose(img)
    return img


def _still_base(img):
    """The first frame as RGBA when it has any transparency, else RGB."""
    img.seek(0)
    if img.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in img.info:
        return img.convert("RGBA")
    return img.convert("RGB")


def _emoji(Image, raw, media):
    if media not in (PNG, JPEG, GIF):
        raise FitError(f"Discord takes a PNG, JPEG or GIF for an emoji, and that "
                       f"picture is a {kind_of(media)}.", "format")
    img, w, h, n = _open(Image, raw)
    if len(raw) <= EMOJI_MAX_BYTES:
        return Fitted(raw, media, w, h, n, [], n)
    steps = []
    if media == GIF:
        shape = _Shape(Image, w, h, "emoji", None)
        steps += shape.steps
        return _fit_animation(Image, img, n, shape, EMOJI_MAX_BYTES, "an emoji", "gif",
                              steps)
    if media == JPEG:
        base = _upright(img, steps).convert("RGB")
        shape = _Shape(Image, base.width, base.height, "emoji", None)
        steps += shape.steps
        return _fit_still(Image, base, shape, EMOJI_MAX_BYTES, "an emoji", "jpeg",
                          steps, n)
    if n > 1:
        steps.append("kept only its first frame (send a GIF for an animated emoji)")
    shape = _Shape(Image, w, h, "emoji", None)
    steps += shape.steps
    return _fit_still(Image, _still_base(img), shape, EMOJI_MAX_BYTES, "an emoji",
                      "png", steps, n)


def _sticker(Image, raw, media, mode):
    if mode not in STICKER_MODES:
        raise FitError(f"fit is pad or crop for a sticker (or none), not {mode!r}.",
                       "mode")
    if media not in (PNG, JPEG, GIF):
        raise FitError(f"a sticker has to be a PNG, APNG or GIF, and that picture is a "
                       f"{kind_of(media)}.", "format")
    img, w, h, n = _open(Image, raw)
    side = STICKER_SIDE
    if media in (PNG, GIF) and (w, h) == (side, side) and len(raw) <= STICKER_MAX_BYTES:
        return Fitted(raw, media, w, h, n, [], n)
    steps = []
    if media == GIF or (media == PNG and n > 1):
        shape = _Shape(Image, w, h, "sticker", mode)
        steps += shape.steps
        return _fit_animation(Image, img, n, shape, STICKER_MAX_BYTES, "a sticker",
                              "gif" if media == GIF else "apng", steps)
    base = (_upright(img, steps).convert("RGB") if media == JPEG
            else _still_base(img))
    shape = _Shape(Image, base.width, base.height, "sticker", mode)
    steps += shape.steps
    if media == JPEG:
        steps.append("made it a PNG (Discord takes no JPEG stickers)")
    return _fit_still(Image, base, shape, STICKER_MAX_BYTES, "a sticker", "png", steps,
                      n)


def fit_emoji(raw, media):
    """Bytes -> Fitted for an emoji (longest side 128, at most 256 KB), or FitError.
    Under 256 KB already: the same bytes back, steps empty."""
    return _guarded(_emoji, raw, media)


def fit_sticker(raw, media, mode="pad"):
    """Bytes -> Fitted for a sticker (exactly 320x320, at most 512 KB), or FitError.
    mode "pad" fits it inside on a transparent canvas, "crop" takes the middle
    square. Already a 320x320 PNG, APNG or GIF under 512 KB: the same bytes back."""
    return _guarded(_sticker, raw, media, mode)


# --------------------------------------------------------------------------
# The child process
# --------------------------------------------------------------------------

def main(argv=None):
    """python -m benham.core.fitimage <emoji|sticker> [pad|crop]

    stdin: the picture. stdout: one JSON line, then (when it is {"ok": true}) the
    fitted bytes, exactly report["bytes"] of them. A refusal is one JSON line
    {"ok": false, "refused": <sentence>, "kind": <family>} and exit 2."""
    argv = sys.argv[1:] if argv is None else argv
    out = sys.stdout.buffer
    target = argv[0] if argv else ""
    mode = argv[1] if len(argv) > 1 else "pad"
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise FitError(f"that picture is over {MAX_INPUT // 1048576} MB, more than "
                           "I'll work on - send a smaller one.", "cap")
        from benham.core import msgparts
        media = msgparts.sniff(raw)
        if media is None:
            raise FitError("that isn't a picture I can work on (I go by the file's "
                           "bytes, not its name).", "format")
        if target == "emoji":
            fitted = fit_emoji(raw, media)
        elif target == "sticker":
            fitted = fit_sticker(raw, media, mode)
        else:
            raise FitError("usage: python -m benham.core.fitimage <emoji|sticker> "
                           "[pad|crop]", "usage")
    except FitError as e:
        refusal = {"ok": False, "refused": str(e), "kind": e.kind}
    except BaseException:  # noqa: BLE001 - MemoryError too; the bot gets a sentence
        refusal = {"ok": False, "refused": DAMAGED, "kind": "damaged"}
    else:
        report = dict(fitted.report(), ok=True)
        out.write(json.dumps(report, ensure_ascii=True).encode("ascii") + b"\n")
        out.write(fitted.raw)
        out.flush()
        return 0
    out.write(json.dumps(refusal, ensure_ascii=True).encode("ascii") + b"\n")
    out.flush()
    return 2


if __name__ == "__main__":
    sys.exit(main())


