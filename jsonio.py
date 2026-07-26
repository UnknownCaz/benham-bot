"""
jsonio.py - the JSON and JSONL helpers this package kept rewriting.

Six modules each had their own "read a config, swallow everything, return a
default" block and their own JSONL append. They mostly agreed, but not on which
exceptions to catch: several used a bare `except Exception`, which also hides a
typo in the calling code as if the file were merely missing.

These catch (OSError, ValueError) instead: OSError covers missing/unreadable
files, ValueError covers malformed JSON (json.JSONDecodeError subclasses it).
Anything else is a real bug and should surface.
"""

import json
import os
import shutil

# Rotate a JSONL once it passes this size. inbox.jsonl and voice_transcript.jsonl
# grow forever otherwise, and double-relay's responder re-parses the whole inbox
# every three seconds, so unbounded growth is a slow performance leak rather than
# just untidiness.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024


def read_json(path, default=None):
    """Read a JSON file, returning `default` if it is missing or malformed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}


def write_json(path, obj, indent=2):
    """Write JSON atomically, so a reader never sees a half-written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def rotate_if_large(path, max_bytes=DEFAULT_MAX_BYTES):
    """Move `path` aside to `path.1` once it exceeds max_bytes. Returns True if rotated.

    One generation only. These are convenience transcripts, not an audit log, and
    keeping a single previous file bounds disk use without a cleanup job of its own.
    """
    try:
        if os.path.getsize(path) < max_bytes:
            return False
    except OSError:
        return False
    try:
        shutil.move(path, path + ".1")   # replaces any previous .1
        return True
    except OSError:
        return False


def append_jsonl(path, record, max_bytes=DEFAULT_MAX_BYTES):
    """Append one record as a JSON line, rotating first if the file is oversized."""
    rotate_if_large(path, max_bytes)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def iter_jsonl(path, skip_bad=True):
    """Yield records from a JSONL file, skipping unparseable lines by default.

    A partially-written final line is normal when something is appending as this
    reads, so a bad line is not treated as corruption.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    if not skip_bad:
                        raise
    except OSError:
        return
