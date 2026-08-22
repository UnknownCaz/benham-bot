"""
outbox.py - the one place that knows how to hand work to the running bot.

Every CLI in benham/cli/ (send, dm, speak, listen, stoplisten, fetch, delete,
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
import time
import uuid
from datetime import datetime, timezone

from benham import paths
OUTBOX = os.path.join(paths.STATE_DIR, "outbox")

# Where bot.py archives a processed request and its sibling _result.json.
RESULT_DIRS = ("sent", "failed")

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


def wait_result(req_path, timeout=60):
    """Poll for the result file bot.py writes next to the archived request.

    Returns (result_dict, "sent"|"failed") once the bot has answered, or
    (None, None) after `timeout` seconds without one. Lived in do.py first;
    moved here when it turned out every other enqueuer needed it too - a
    request that Discord refuses lands in outbox/failed with the error, and a
    caller that never looks there walks away believing it succeeded.
    """
    base = os.path.splitext(os.path.basename(req_path))[0]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for d in RESULT_DIRS:
            folder = os.path.join(OUTBOX, d)
            if not os.path.isdir(folder):
                continue
            for fn in os.listdir(folder):
                if fn.startswith(base) and fn.endswith("_result.json"):
                    with open(os.path.join(folder, fn), encoding="utf-8") as f:
                        return json.load(f), d
        time.sleep(0.5)
    return None, None


def report_outcome(req_path, timeout=60):
    """Wait for the bot's verdict on one queued request and say what happened.

    Returns (exit_code, result_dict_or_None). This is the default tail of every
    enqueue-style CLI: before it existed, `send`/`dm`/`delete`/... printed
    "Queued" and exited 0, so a request Discord refused was recorded faithfully
    in outbox/failed and surfaced to nobody - the caller had no error to see
    unless it hand-rolled result-file polling.

    Three outcomes:
      - archived to sent/:    one confirmation line, EXIT_OK
      - archived to failed/:  the recorded error, EXIT_FAIL
      - no result in time:    still queued (bot busy or down), EXIT_FAIL -
        the request itself is unaffected and still runs when the bot gets to it
    """
    result, where = wait_result(req_path, timeout=timeout)
    if result is None:
        print(f"no result within {timeout}s. The request is still queued and "
              f"will run when the bot gets to it; check outbox/sent/. "
              f"(is bot.py running? `python benham.py status`)", file=sys.stderr)
        return EXIT_FAIL, None
    if where == "failed":
        print(f"REFUSED: {result.get('error', '(no error recorded)')}",
              file=sys.stderr)
        return EXIT_FAIL, result
    line = f"done: status={result.get('status', '?')}"
    if result.get("message_id"):
        line += f", message_id={result['message_id']}"
    if result.get("channel"):
        line += f", channel={result['channel']}"
    print(line)
    return EXIT_OK, result
