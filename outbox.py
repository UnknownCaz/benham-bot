"""
outbox.py - the one place that knows how to hand work to the running bot.

Every CLI in this directory (send, dm, speak, listen, stoplisten, fetch, delete,
draft, purge) enqueues a request the same way, and until this module existed each
one carried a byte-identical copy of the write: build a timestamped name, write a
.json.tmp, os.replace it into place. Nine copies meant nine edits for any change to
the request envelope, which is why `purge` had no CLI for so long - an extra copy
cost more than the feature was worth.

The atomic rename is the important part. bot.py's poller globs "*.json" every two
seconds, so a request written in place could be read half-finished. Writing to
.json.tmp (outside the glob) and renaming makes the request appear complete or not
at all.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE_DIR, "outbox")

# Conventional exit codes, shared by every CLI here:
#   0 ok, 1 runtime failure, 2 usage error.
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def console_utf8():
    """Force UTF-8 on stdout/stderr, with a replacing fallback.

    These CLIs echo Discord content and channel names back to the terminal, which
    routinely contain emoji. On Windows a redirected stream defaults to cp1252 and
    printing one raises UnicodeEncodeError - the same failure that used to mark
    successful sends as FAILED inside bot.py.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def usage(message):
    """Print a usage line to stderr and return the usage exit code."""
    print(message, file=sys.stderr)
    return EXIT_USAGE


def parse_ids(values, labels):
    """Parse positional ids, returning (ids, error_message).

    Discord ids are 64-bit ints and a mistyped one is the most common mistake at
    this boundary, so the message names which argument was wrong.
    """
    out = []
    for value, label in zip(values, labels):
        try:
            out.append(int(value))
        except ValueError:
            return None, f"{label} must be an integer, got {value!r}"
    return out, None


def enqueue(**fields):
    """Atomically drop one request into the outbox and return its final path.

    `queued_at` is stamped here so every request carries it and no caller has to
    remember. Pass any other fields the action needs; bot.py reads them by name.
    """
    os.makedirs(OUTBOX, exist_ok=True)
    req = dict(fields)
    req.setdefault("queued_at", datetime.now(timezone.utc).isoformat())

    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(OUTBOX, name + ".json.tmp")
    final = os.path.join(OUTBOX, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)  # atomic - the poller never sees a partial request
    return final
