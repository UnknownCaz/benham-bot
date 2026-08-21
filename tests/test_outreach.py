"""test_outreach.py - the bounded way to reach a collaborator stays bounded.

`outreach` exists because Tyler denied the unbounded send commands on 2026-08-18
(`dm`, `send`, `do dm_user`, `do send_message`, plus the Gmail send tools) and that
left INTENT.md's second purpose - the line to project collaborators - with no path
at all. The value of this command is ENTIRELY in what it refuses, so that is what
this file checks. A version of it that quietly reached anyone would be worse than
not having it, because it would look like the safe option.

  IT NEVER TARGETS AN OWNER. discord-outreach rule 1. Tyler is reached with `ask`,
  which joins his queue and follows his nudge policy.

  IT NEVER REACHES SOMEONE OFF THE LIST, and refuses rather than guessing. A fuzzy
  match would DM the wrong person - the exact mistake the deny list prevents.

  IT COMPOSES NOTHING. The only outward step is advance_conversation, which cannot
  choose a recipient or words: both come from the conversation record. That is why
  this needs none of the denied permissions.
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import shutil
import sys
import tempfile

from benham.core import conversations as C, identity
from benham.cli import outreach

DOOM = 1097631170788851815
TYLER = 273967061619965952
STRANGER = 999000111

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def refused(arg):
    """resolve_target(arg) -> the id, or the string 'refused'."""
    try:
        return outreach.resolve_target(arg)
    except SystemExit:
        return "refused"


def _whitelist(*ids):
    """Set the guest allowlist - BOTH halves of it, which is the point of a helper.

    `GUEST_PEOPLE` is the source (a name->id map) and `GUEST_IDS` is derived from
    its values. Setting only one leaves identity.py internally inconsistent, and
    the half a caller reads decides whether the patch bites: `_allowed()` reads
    GUEST_PEOPLE, so an earlier version of this file that set GUEST_IDS alone went
    red the moment outreach started using real names. That was the suite doing its
    job; this helper is so it cannot happen a second time.

    Ids are their own names here on purpose - the unnamed shape. These checks are
    about the fallback that applies when nobody has written names down, so the
    fixture has to be that shape to be testing it.
    """
    identity.GUEST_PEOPLE = {str(i): int(i) for i in ids}
    identity.GUEST_IDS = set(identity.GUEST_PEOPLE.values())


def main():
    tmp = tempfile.mkdtemp(prefix="benham-outreach-")
    real_store, real_batches = C.STORE, C.BATCHES
    real_control = identity.CONTROL
    real_guests, real_owners = identity.GUEST_IDS, identity.OWNER_IDS
    real_people = identity.GUEST_PEOPLE
    real_enqueue = None
    try:
        C.STORE = os.path.join(tmp, "conversations.json")
        C.BATCHES = os.path.join(tmp, "ask_batches.json")
        identity.OWNER_IDS = {TYLER}
        _whitelist(DOOM)
        identity.CONTROL = dict(real_control)
        identity.CONTROL.pop("outreach", None)

        section("It refuses the owner - outreach never targets Tyler")
        # Rule 1 of the skill, and the reason it is code here rather than a note:
        # `ask` exists for Tyler, joins his queue and is answerable by slot. An
        # outreach conversation aimed at him would be a second, parallel channel
        # to the same person with different rules.
        # He is deliberately PUT ON THE LIST for these two, and that is the whole
        # point of the setup. With him off it the allowlist refuses him first and
        # the owner rule is never exercised at all - the first version of this
        # section passed with the owner check commented out, which is this repo's
        # oldest failure mode wearing yet another hat.
        identity.CONTROL["outreach"] = {"people": {"caz": TYLER, "doom": DOOM}}
        check("an owner is refused even when he IS on the list",
              refused(TYLER), "refused")
        check("...by name as well as by id, because the check runs on the RESOLVED "
              "id", refused("caz"), "refused")
        check("...while the real collaborator on the same list still resolves",
              refused("doom"), DOOM)

        section("It refuses anyone not on the list, rather than guessing")
        identity.CONTROL["outreach"] = {"people": {"doom": DOOM}}
        check("a stranger's id is refused", refused(STRANGER), "refused")
        check("an unknown name is refused, not fuzzy-matched", refused("doomassassin"),
              "refused")
        check("...and neither is it matched by prefix", refused("do"), "refused")
        check("the exact name works", refused("Doom"), DOOM)
        check("...and so does the bare id", refused(str(DOOM)), DOOM)

        section("Config narrows the list; the guest whitelist is the fallback")
        # Four people are whitelisted to CHAT with Benham and the skill lists one
        # it is appropriate to go and bother. Those are different questions, so
        # outreach.people can cut below the whitelist without a code change.
        identity.CONTROL.pop("outreach", None)
        _whitelist(DOOM, STRANGER)
        check("with no config, any whitelisted guest is reachable",
              [refused(str(DOOM)), refused(str(STRANGER))], [DOOM, STRANGER])
        identity.CONTROL["outreach"] = {"people": {"doom": DOOM}}
        check("...and the config narrows it below the whitelist",
              [refused(str(DOOM)), refused(str(STRANGER))], [DOOM, "refused"])

        section("It composes nothing - delivery is the bounded action")
        # The whole security argument. If this ever calls dm/send directly it needs
        # the permissions Tyler denied, and the reason for the command evaporates.
        C.forget()
        sent = []
        from benham.core import outbox
        real_enqueue = outbox.enqueue
        outbox.enqueue = lambda **kw: sent.append(kw)

        rc = outreach.main([str(DOOM), "does the image thing work now?",
                            "--project", "benham"])
        check("it exits clean", rc, 0)
        check("exactly one thing was enqueued", len(sent), 1)
        check("...and it is advance_conversation, which cannot choose recipient "
              "or words", sent[0].get("action"), "advance_conversation")
        check("...carrying only a conversation id",
              sorted(sent[0]), ["action", "id"])

        opened = C.get(sent[0]["id"])
        check("a conversation was opened for the collaborator",
              int(opened["counterparty"]), DOOM)
        check("...in the ASKING direction, so nudges and the queue apply",
              opened["direction"], C.ASKING)
        check("...with the question stored on the record, not in the message",
              opened["question"], "does the image thing work now?")
        check("...and the project rode along", opened["project"], "benham")
        check("it is live, so a reply can bind to it", opened["state"], C.OPEN)
        check("nothing has been delivered yet - that is the bot's job",
              bool(opened.get("delivered_at")), False)

        section("Refusing to ask does not open anything")
        # A refusal must leave no trace. A conversation opened for someone who is
        # then never messaged is a loop that can only ever be banked.
        before = len(C.all_conversations())
        sent.clear()
        try:
            outreach.main([str(TYLER), "should never happen"])
        except SystemExit:
            pass
        check("no conversation was opened for a refused target",
              len(C.all_conversations()), before)
        check("...and nothing was enqueued", sent, [])
        C.forget()

    finally:
        if real_enqueue is not None:
            from benham.core import outbox
            outbox.enqueue = real_enqueue
        C.STORE, C.BATCHES = real_store, real_batches
        identity.CONTROL = real_control
        identity.GUEST_IDS, identity.OWNER_IDS = real_guests, real_owners
        identity.GUEST_PEOPLE = real_people
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL PASS" if not _fails else f"{len(_fails)} FAILED"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
