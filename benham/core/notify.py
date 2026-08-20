"""
notify.py - how loudly Benham is allowed to interrupt Tyler.

Stage 3 item 11. The intent rundown found that the highest-value traffic in the
whole system - Claude reporting to Tyler while he is away - was also the least
designed: forty-nine raw `dm` calls with no type, no priority and no way to tell
"your build finished" from "the server is down". Everything buzzed his phone
equally, including Doom filing an idea at 23:51.

Tyler's call, asked directly: two tiers.

  BUZZ   blocked-needs-your-call, something-broke.   Wake the phone.
  QUIET  task-finished, collaborator-answered.       There when he looks.

The mechanism is Discord's own `silent=True`, not an invention: a silent message
lands in the channel normally and suppresses the push. So QUIET is genuinely
"wait", not "never" - nothing is withheld, it just does not vibrate.

CALLERS STATE THE FACT, NOT THE VOLUME. A caller says `kind="broke"`, never
`tier="buzz"`. The mapping lives here, once, so "should this wake him" is a policy
decision in one file rather than a judgement made forty-nine times at forty-nine
call sites - which is exactly how the current mess happened.

AN UNKNOWN KIND IS REFUSED, not defaulted. Both defaults are bad in a way that
hides: defaulting to quiet means a new class of breakage silently stops waking
him, and defaulting to buzz means notification fatigue creeps back until he
learns to ignore the channel - and a channel he ignores is the failure this whole
stage is trying to prevent. Refusing forces one deliberate line here whenever a
new kind of news appears.
"""

BUZZ, QUIET = "buzz", "quiet"

# The whole policy. Adding a row is a decision; the ValueError below is what makes
# someone take it.
KINDS = {
    # --- wake the phone ---------------------------------------------------
    "blocked":  (BUZZ,  "work stopped and cannot continue without Tyler"),
    "broke":    (BUZZ,  "something failed - a server, a sync, a scheduled task"),
    # --- there when he looks ----------------------------------------------
    "finished": (QUIET, "a long task he asked for is done"),
    "answered": (QUIET, "a collaborator replied, filed an idea, or reported a bug"),
    # Added 2026-08-20 with the initiative lane. QUIET is not a tuning choice
    # here, it is the definition: this is the one kind of message Claude sends
    # WITHOUT being asked, and something nobody requested has no business
    # vibrating his phone. It waits in the channel until he looks, which is
    # exactly the standing a question Claude chose to ask should have.
    "curious": (QUIET, "Claude chose to ask Tyler something, unprompted"),
}


def tier_for(kind):
    """BUZZ or QUIET for this kind of news. Raises on anything unclassified."""
    row = KINDS.get(str(kind))
    if row is None:
        raise ValueError(
            f"unknown notification kind {kind!r}. Known: {', '.join(sorted(KINDS))}. "
            "Classify it in notify.KINDS rather than guessing - defaulting either "
            "way fails silently, one by never waking him again and the other by "
            "training him to ignore the channel.")
    return row[0]


def is_silent(kind):
    """What to pass Discord as `silent=`. QUIET means no push, not no message."""
    return tier_for(kind) == QUIET
