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


def outbox_dir(face):
    """The outbox directory for one face (PLAN-second-face commit 5).

    paths.state_for carries the guarantee: the primary face resolves to
    exactly today's state/outbox, a named face to state/faces/<name>/outbox.
    Each bot process polls only its own face's directory, which is what makes
    "which face sends this message" a fact instead of a listdir race.
    """
    return os.path.join(paths.state_for(face), "outbox")


# The primary face's outbox - byte-identical to the pre-faces constant, and
# still what bot.py resolves its own paths against until commit 12 wires a
# face into the process launch.
OUTBOX = outbox_dir(paths.DEFAULT_FACE)

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


def enqueue(face=None, **fields):
    """Atomically drop one request into a face's outbox and return its final path.

    `face` is REQUIRED (Tyler's call: --face on every call), and it is a
    ValueError rather than a default because the failure it prevents is
    silent: a caller that means one identity and reaches another. Until the
    CLI grows its --face flag (commit 10), every call site passes
    paths.DEFAULT_FACE explicitly - the requirement lives here so no new
    caller can forget.

    Three fields are stamped so no caller has to remember: `queued_at`;
    `face`, so the request file itself says who is to act on it and the bot
    can refuse a misdelivered one; and `source`, the invoking command line -
    working out who enqueued a request tonight took clustering timestamps,
    and with two faces that question gets asked more, not less. Pass any
    other fields the action needs; bot.py reads them by name.
    """
    if not face:
        raise ValueError(
            "enqueue() requires face=: every request names which bot identity "
            "acts on it, so a caller meaning one face can never silently reach "
            "another. Pass face=paths.DEFAULT_FACE to mean Benham."
        )
    box = outbox_dir(face)  # validates the name; a traversal is unrepresentable
    os.makedirs(box, exist_ok=True)
    req = dict(fields)
    req["face"] = face
    req.setdefault("queued_at", datetime.now(timezone.utc).isoformat())
    req.setdefault("source", " ".join(sys.argv[:2]).strip() or "unknown")

    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(box, name + ".json.tmp")
    final = os.path.join(box, name + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, indent=2)
    os.replace(tmp, final)  # atomic - the poller never sees a partial request
    return final


def misdelivered(req, process_face):
    """Why this request must NOT run in this process, or None if it may.

    A pure decision function, wired at the one poll site in bot.py - the shape
    the rooms build named before building ("assertive stubs, pure decision
    functions"), so the wall is testable without a Discord harness. A process
    polls only its own face's directory, so a mismatch means a file was moved
    by hand or a path derivation broke; either way, acting as another identity
    is the one thing a face must never do. A request with no face field is a
    pre-faces file and runs on the primary, so nothing queued before the
    upgrade is stranded.
    """
    req_face = req.get("face", paths.DEFAULT_FACE)
    if req_face != process_face:
        return (f"request names face {req_face!r} but this process runs as "
                f"{process_face!r} - misdelivered, refusing to act as another "
                "identity")
    return None


def wait_result(req_path, timeout=60):
    """Poll for the result file bot.py writes next to the archived request.

    Returns (result_dict, "sent"|"failed") once the bot has answered, or
    (None, None) after `timeout` seconds without one. Lived in do.py first;
    moved here when it turned out every other enqueuer needed it too - a
    request that Discord refuses lands in outbox/failed with the error, and a
    caller that never looks there walks away believing it succeeded.
    """
    # The request's own directory says which face's outbox to watch - derived
    # from the path rather than from the module constant, so a result is found
    # beside its request whichever face carried it.
    box = os.path.dirname(req_path)
    base = os.path.splitext(os.path.basename(req_path))[0]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for d in RESULT_DIRS:
            folder = os.path.join(box, d)
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
