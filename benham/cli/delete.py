"""
delete.py - ask the running bot.py to delete ONE specific message by id.

Usage:
    python benham.py delete <channel_id> <message_id> [--confirm-token TOK] [--no-wait]

TWO STEPS, since 2026-08-26. The first call performs nothing: it runs the dry-run
and hands back a confirmation token, and you re-run with `--confirm-token` to
actually delete. That is the same shape `do.py` has always had for tier 3, and it
is deliberate rather than incidental - see the note below.

WHY THIS CHANGED. Until 2026-08-26 this enqueued a legacy `delete` action that
bot.py handled in its own branch, calling msg.delete() directly with NO
policy.authorize, NO destructive_guilds allowlist, no dry-run and no confirmation.
So the tier-3 guarantee was a property of the verb NAME - `delete_message` was
gated and `delete` was not, for the same irreversible effect. It now enqueues the
registry twin `delete_message`, which carries all three gates. Tyler's call, after
the mandatory-token work on 08-24 made the gap visible.

The bot can always delete its OWN messages; deleting someone else's needs the
Manage Messages permission. Deletion is PERMANENT.

EXIT CODES matter here and changed with the two-step: a preview-only first call
exits NON-ZERO, because nothing was deleted and a script that read 0 would believe
otherwise. Only a completed delete exits 0. --no-wait keeps the old
fire-and-forget behaviour for the enqueue itself.

Use it to clean up a stray/accidental Benham post or a bad draft. To remove many
messages at once, use purge.py.
"""

import sys

from benham import paths
from benham.core.outbox import (EXIT_FAIL, EXIT_OK, console_utf8, enqueue,
                                parse_ids, report_outcome, usage)

ACTION = "delete_message"


def run_two_step(action, describe, rerun, token=None, no_wait=False,
                 timeout=60, nothing="NOTHING WAS DELETED.", **params):
    """Enqueue a confirm-gated registry action, printing the preview or firing it.

    Shared by delete.py and purge.py because the dance is identical and the one
    thing neither may do is fire without a token - keeping it in one place means
    a future third caller cannot quietly get that wrong. `guest off` (Phase B)
    is that third caller: tier 2 always_confirm, the same dance, its own
    `nothing` line because nothing there is a deletion.
    """
    extra = {"confirm_token": token} if token else {}
    final = enqueue(face=paths.PROCESS_FACE, action=action, **params, **extra)
    print(f"Queued {action} -> {final}")
    print(f"  {describe}")
    if no_wait:
        return EXIT_OK
    code, result = report_outcome(final, timeout=timeout)
    if code != EXIT_OK or not result:
        return code
    if result.get("status") == "confirmation_required":
        preview = result.get("preview") or {}
        print()
        print(preview.get("summary", "(no preview available)"))
        if preview.get("detail"):
            print(preview["detail"])
        print()
        print(f"{nothing} To confirm, re-run with the token:")
        print(f"  {rerun(result['confirm_token'])}")
        print(f"  (expires in {result.get('expires_in_seconds', '?')}s; "
              f"expired means cancelled, never assumed yes)")
        # Non-zero on purpose: a preview is not a deletion, and a caller that
        # read 0 here would report success for an action that never ran.
        return EXIT_FAIL
    print(f"  {result.get('result', result)}")
    return EXIT_OK


def main(argv):
    console_utf8()
    no_wait = "--no-wait" in argv
    argv = [a for a in argv if a != "--no-wait"]

    token = None
    if "--confirm-token" in argv:
        i = argv.index("--confirm-token")
        if i + 1 >= len(argv):
            return usage("--confirm-token needs a token")
        token = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    if len(argv) < 3:
        return usage("Usage: python benham.py delete <channel_id> <message_id> "
                     "[--confirm-token TOK] [--no-wait]")
    ids, err = parse_ids(argv[1:3], ["channel_id", "message_id"])
    if err:
        return usage(err)
    channel_id, message_id = ids

    return run_two_step(
        ACTION,
        describe=f"channel {channel_id}, message {message_id}",
        rerun=lambda t: (f"python benham.py --face {paths.PROCESS_FACE} delete "
                         f"{channel_id} {message_id} --confirm-token {t}"),
        token=token,
        no_wait=no_wait,
        channel_id=channel_id,
        message_id=message_id,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
