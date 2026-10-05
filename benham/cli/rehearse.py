"""rehearse.py - what Benham would read, and say, if Tyler @mentioned him in a channel.

Runs against the REAL channel through the running bot: the same read a mention
makes (channelread.py), the same prompt, the same memory for that channel. Then
it prints what came back. Nothing is posted, nothing is remembered, and the
model sees its tools under tool_choice "none", so it cannot call one. Works on a
server that is not on agent_guilds yet - rehearsing one before it goes on the
list is the point.

Usage:
    python -u benham.py rehearse <channel_id> "what you'd say to him"
    python -u benham.py rehearse <channel_id>            # a bare @Benham - "jump in"
    python -u benham.py rehearse <channel_id> --look     # the read only, no model call

A rehearsal with a message costs one model call (a few cents); --look costs
nothing. Like catchup, this prints a channel's recent messages - keep
friend-server reads light.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_OK, console_utf8, enqueue, parse_ids,
                                report_outcome)

USAGE = 'Usage: python -u benham.py rehearse <channel_id> ["message"] [--look]'
TIMEOUT = 180      # a model call, behind whatever else the poller is doing


def main(argv):
    console_utf8()
    args = list(argv[1:])
    look = "--look" in args
    args = [a for a in args if a != "--look"]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr)
        return 2
    ids, err = parse_ids(args[0:1], ["channel_id"])
    if err:
        print(err, file=sys.stderr)
        return 2
    (channel_id,) = ids
    text = " ".join(args[1:]).strip()

    final = enqueue(face=paths.PROCESS_FACE, action="rehearse",
                    channel_id=channel_id, text=text, look=look)
    code, result = report_outcome(final, timeout=TIMEOUT)
    if code != EXIT_OK or not result:
        return code

    pics = result.get("pictures") or []
    head = f"--- what Benham reads: {result.get('where')} ({result.get('read', 0)} messages"
    if result.get("dropped"):
        head += f", {result['dropped']} older left out for length"
    if pics:
        head += f", {len(pics)} picture(s) looked at"
    print(head + ") ---")
    print(result.get("seen") or "")
    for p in pics:
        print(f"  [picture he'd look at: {p}]")
    for s in result.get("skipped") or []:
        print(f"  [picture he couldn't look at: {s}]")
    print("(actions on this turn would wait for your tap)" if result.get("tainted")
          else "(nothing here was written by anyone else - actions would not need a tap)")
    if not look:
        print("--- what Benham would say ---")
        print(result.get("reply") or "(nothing - he would stay quiet)")
        usage = result.get("usage") or {}
        if usage:
            print(f"(tokens: {usage.get('input_tokens')} in, "
                  f"{usage.get('output_tokens')} out, "
                  f"{usage.get('cache_read_input_tokens') or 0} from cache)")
    print("=== DONE (rehearsal; nothing was posted or remembered) ===")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
