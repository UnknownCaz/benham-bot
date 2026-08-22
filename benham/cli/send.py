"""
send.py - enqueue a Discord message for the running bot.py to deliver.

Usage:
    python benham.py send <channel_id> "message text" [--no-wait]

Writes an atomic request file into ./outbox; bot.py picks it up within ~2s,
sends it, and moves it to outbox/sent or outbox/failed with a result file.
By default this waits for that result and exits non-zero when Discord refused
the send - a caller that reads "Queued" and walks away otherwise never learns.
--no-wait restores fire-and-forget for bulk scripting; refusals then show up
only in `python benham.py status`.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome, usage)


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a != "--no-wait"]
    if len(argv) < 3:
        return usage('Usage: python benham.py send <channel_id> "message text" '
                     '[--no-wait]')
    ids, err = parse_ids(argv[1:2], ["channel_id"])
    if err:
        return usage(err)
    (channel_id,) = ids
    content = " ".join(argv[2:])

    # No "action" key on purpose: bot.py defaults to "send".
    final = enqueue(face=paths.PROCESS_FACE, channel_id=channel_id, content=content)
    print(f"Queued -> {final}")
    if no_wait:
        return EXIT_OK
    code, _ = report_outcome(final)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
