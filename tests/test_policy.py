"""
test_policy.py - every capability against every origin.

The bug this whole refactor answers was not a wrong rule. It was a right rule that
nothing called: identity.agent_allowed() encoded "the owner cannot drive the agent
from Chillbar", was asserted by a passing test, and was dead code. So the test
that mattered here is not "does authorize() return the right answer for pc_task" -
it is the exhaustive one. Every action, every origin, compared against an expected
matrix, so a capability that quietly gains a route shows up as a diff rather than
as nothing at all.

    python test_policy.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package and bot.py live.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import sys

from benham.core import capabilities
from benham.core import identity
from benham.core import policy
from benham.core.policy import CallContext, Origin

# Phase B (INTENT decision 39) deleted pc_task and spawn_in_room. The machine
# wall and the taint wall stayed in code, so this file registers a TEST-ONLY
# pc_task with the deleted lane's exact profile to keep proving them.
_testconfig.walled_pc_task()

TYLER = 273967061619965952
DOOM = 777000777000777000
TESTING = 736988645562646619
CHILLBAR = 1491485711076167711

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def ctx_for(origin, tainted=False, guild_id=TESTING):
    if origin == Origin.OWNER_DM:
        return CallContext.owner_dm(TYLER, 111, tainted=tainted)
    if origin == Origin.OWNER_GUILD:
        return CallContext.owner_guild(TYLER, guild_id, 111, tainted=tainted)
    if origin == Origin.OWNER_VOICE:
        return CallContext.owner_voice(TYLER, guild_id, 111)
    if origin == Origin.LOCAL_CLI:
        c = CallContext.local(TYLER)
        c.tainted = tainted
        return c
    if origin == Origin.SYSTEM:
        return CallContext.system(guild_id)
    if origin == Origin.GUEST_DM:
        # A whitelisted guest, not a stranger. Using an id that is not on the list
        # would make every row below pass for the wrong reason - refused for not
        # being a guest at all, rather than refused for being one. _testconfig
        # whitelists this one; the check under "the fixture is live" below is what
        # stops that silently ceasing to be true.
        return CallContext.guest_dm(_testconfig.GUEST_ID, 111)
    raise AssertionError(origin)


def allowed(action_name, origin, tainted=False, guild_id=TESTING):
    act = capabilities.REGISTRY[action_name]
    return policy.authorize(act, ctx_for(origin, tainted, guild_id)).allowed


# --------------------------------------------------------------------------
section("The fixture is live — everything about guests below rests on this")
# Not ceremony. If the guest surface is OFF, rule_guest_enabled short-circuits
# ahead of the two rules the guest matrix claims to be testing, and all 56 rows
# report "refused" for a reason nothing here asserts on. The suite stayed green
# through exactly that for one commit. Assert the precondition, not the symptom.
check("guests are switched on", identity.guest_enabled(), True)
check("and the id every guest row uses is actually whitelisted",
      identity.is_guest(_testconfig.GUEST_ID), True)

section("Fail closed — the property that makes threading mistakes safe")
send = capabilities.REGISTRY["send_message"]
check("no context is denied", policy.authorize(send, None).allowed, False)
check("an unknown origin is denied",
      policy.authorize(send, CallContext("something_new", TYLER)).allowed, False)
check("the denial names the rule",
      policy.authorize(send, None).rule, "context_present")

section("Stage 2 — the owner rule, now stated at the capability too")
STRANGER = 999000999000999000


def as_user(origin, uid, guild_id=TESTING):
    if origin == Origin.OWNER_DM:
        return CallContext.owner_dm(uid, 111)
    if origin == Origin.OWNER_GUILD:
        return CallContext.owner_guild(uid, guild_id, 111)
    if origin == Origin.OWNER_VOICE:
        return CallContext.owner_voice(uid, guild_id, 111)
    raise AssertionError(origin)


for origin in (Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE):
    check(f"a stranger is refused from {origin}",
          policy.authorize(send, as_user(origin, STRANGER)).allowed, False)
    check(f"Tyler is allowed from {origin}",
          policy.authorize(send, as_user(origin, TYLER)).allowed, True)

check("the refusal names the owner rule",
      policy.authorize(send, as_user(Origin.OWNER_DM, STRANGER)).rule, "owner")
check("a stranger cannot engage the agent either",
      policy.may_engage_agent(CallContext.owner_dm(STRANGER)).allowed, False)
check("None as an actor is refused",
      policy.authorize(send, CallContext.owner_dm(None)).allowed, False)
check("a spoofed string actor id is still checked",
      policy.authorize(send, CallContext.owner_dm("999")).allowed, False)
check("Tyler's id as a string still works",
      policy.authorize(send, CallContext.owner_dm(str(TYLER))).allowed, True)

# LOCAL_CLI and SYSTEM carry no Discord actor to verify. Requiring one would deny
# every automated call; they are constrained by rule_origin_allowed instead.
check("LOCAL_CLI needs no actor id",
      policy.authorize(send, CallContext.local()).allowed, True)
check("SYSTEM needs no actor id (but reaches almost nothing)",
      policy.authorize(capabilities.REGISTRY["set_presence"],
                       CallContext.system()).allowed, True)

section("pc_task — the capability this stage exists for")
check("reachable from Tyler's DM", allowed("pc_task", Origin.OWNER_DM), True)
check("reachable from the local CLI", allowed("pc_task", Origin.LOCAL_CLI), True)
check("NOT from a guild mention", allowed("pc_task", Origin.OWNER_GUILD), False)
check("NOT from voice", allowed("pc_task", Origin.OWNER_VOICE), False)
check("NOT from an automated trigger", allowed("pc_task", Origin.SYSTEM), False)
check("NOT from a tainted DM", allowed("pc_task", Origin.OWNER_DM, tainted=True), False)
check("NOT from a tainted local CLI",
      allowed("pc_task", Origin.LOCAL_CLI, tainted=True), False)
d = policy.authorize(capabilities.REGISTRY["pc_task"],
                     ctx_for(Origin.OWNER_GUILD))
check("the refusal explains where it IS available",
      "direct DM" in d.reason and "local CLI" in d.reason, True)

section("Ordinary capabilities keep every human route")
for name in ("send_message", "read_channel", "pin_message", "purge_messages"):
    for origin in (Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE,
                   Origin.LOCAL_CLI):
        check(f"{name} from {origin}", allowed(name, origin), True)

section("SYSTEM is opt-in, not inherited")
check("set_presence opted in (on_ready needs it)",
      allowed("set_presence", Origin.SYSTEM), True)
for name in ("send_message", "read_channel", "purge_messages", "pc_task", "dm_user"):
    check(f"{name} NOT reachable from SYSTEM", allowed(name, Origin.SYSTEM), False)

section("agent_guilds is enforced now, on the path that runs")
check("mention in Testing engages",
      policy.may_engage_agent(CallContext.owner_guild(TYLER, TESTING)).allowed, True)
check("mention in Chillbar does NOT",
      policy.may_engage_agent(CallContext.owner_guild(TYLER, CHILLBAR)).allowed, False)
check("mention in an unknown guild does NOT",
      policy.may_engage_agent(CallContext.owner_guild(TYLER, 12345)).allowed, False)
check("a DM always engages",
      policy.may_engage_agent(CallContext.owner_dm(TYLER)).allowed, True)
check("a guild mention with no guild id does NOT",
      policy.may_engage_agent(CallContext(Origin.OWNER_GUILD, TYLER)).allowed, False)
check("a capability called from a non-agent guild is refused too",
      allowed("send_message", Origin.OWNER_GUILD, guild_id=CHILLBAR), False)

section("Immutability — a nested call cannot clear its caller's taint")
# This section named the guarantee and then checked something else. Both of its
# original assertions pass against a with_taint that assigns straight through:
# "returns a new object" and "the ORIGINAL keeps its taint" are true of a copy
# that came back clean. The property in the heading - the DERIVED context still
# being tainted - was the one nobody asserted, and agent.py was relying on the
# clearing every turn. Same shape as the manual page and the rule_owner comment:
# a confident claim with nothing checking it.
base = CallContext.owner_dm(TYLER, 111, tainted=True)
derived = base.with_taint(False)
check("with_taint returns a new object", derived is base, False)
check("the original keeps its taint", base.tainted, True)
check("...and so does the COPY - with_taint cannot clear", derived.tainted, True)
check("a bare with_taint() still clears nothing",
      base.with_taint().tainted, True)
check("with_taint(False) on a clean context leaves it clean",
      CallContext.owner_dm(TYLER, 111).with_taint(False).tainted, False)
check("with_taint(True) on a clean context taints it",
      CallContext.owner_dm(TYLER, 111).with_taint(True).tainted, True)
# for_target carries taint across the second authorization phase too - the
# target rules include rule_outward_tainted, so losing it there would matter.
check("for_target carries the taint",
      base.for_target(TESTING, 111).tainted, True)
check("a guest context cannot be laundered either",
      CallContext.guest_dm(DOOM, 111).with_taint(False).tainted, True)

section("Stage 3 — the destructive guild allowlist, now a policy rule")
purge = capabilities.REGISTRY["purge_messages"]
send_a = capabilities.REGISTRY["send_message"]


def target(guild_id, channel_id=999):
    return CallContext.owner_dm(TYLER, 111).for_target(guild_id, channel_id)


check("purge not DENIED in Testing (it asks for confirmation instead)",
      policy.authorize_target(purge, target(TESTING)).denied, False)
check("...and what it asks for is a confirmation",
      policy.authorize_target(purge, target(TESTING)).needs_confirm, True)
check("purge refused in Chillbar",
      policy.authorize_target(purge, target(CHILLBAR)).allowed, False)
check("purge refused with no guild (a DM)",
      policy.authorize_target(purge, target(None)).allowed, False)
check("purge refused in an unknown guild",
      policy.authorize_target(purge, target(4242)).allowed, False)
check("the refusal names the rule",
      policy.authorize_target(purge, target(CHILLBAR)).rule, "destructive_guild")
check("the refusal says a confirmation cannot unlock it",
      "No confirmation can override" in policy.authorize_target(purge, target(CHILLBAR)).reason,
      True)
check("non-destructive actions are unaffected in Chillbar",
      policy.authorize_target(send_a, target(CHILLBAR)).allowed, True)
check("a destructive action is still DENIED, not merely asked, in Chillbar",
      policy.authorize_target(purge, target(CHILLBAR)).denied, True)
check("a missing target context is refused",
      policy.authorize_target(purge, None).allowed, False)

# The caller phase must stay answerable without a resolved target - an origin
# refusal should never require a channel lookup.
check("caller phase still decides pc_task with no target resolved",
      policy.authorize(capabilities.REGISTRY["pc_task"],
                       CallContext.owner_guild(TYLER, TESTING)).allowed, False)

section("Stage 4 — the posting allowlist, now a policy rule")
OUTSIDE_GUILD = 4040404040404040404
check("posting into Testing allowed",
      policy.authorize_target(send_a, target(TESTING, 809357286036078612)).allowed, True)
check("posting into Chillbar allowed (it is on the list)",
      policy.authorize_target(send_a, target(CHILLBAR, 123)).allowed, True)
check("posting into a guild Benham is invited to later is REFUSED",
      policy.authorize_target(send_a, target(OUTSIDE_GUILD, 999)).allowed, False)
check("the refusal names the rule",
      policy.authorize_target(send_a, target(OUTSIDE_GUILD, 999)).rule, "posting_scope")
for nm in ("send_embed", "send_file"):
    check(f"{nm} is capped too",
          policy.authorize_target(capabilities.REGISTRY[nm],
                                  target(OUTSIDE_GUILD, 999)).allowed, False)
check("non-posting actions are unaffected outside the list",
      policy.authorize_target(capabilities.REGISTRY["pin_message"],
                              target(OUTSIDE_GUILD, 999)).allowed, True)
check("reading is never capped by posting scope",
      policy.authorize_target(capabilities.REGISTRY["read_channel"],
                              target(OUTSIDE_GUILD, 999)).allowed, True)

section("Stage 5 — confirms and outward-taint are policy decisions now")
D = policy.Decision


def verdict(action_name, tainted=False, guild_id=TESTING, channel_id=809357286036078612):
    act = capabilities.REGISTRY[action_name]
    ctx = CallContext.owner_dm(TYLER, 111, tainted=tainted).for_target(guild_id, channel_id)
    return policy.authorize_target(act, ctx)


check("purge asks for confirmation", verdict("purge_messages").verdict, D.CONFIRM)
check("add_role asks every time", verdict("add_role").verdict, D.CONFIRM)
check("send_message is free in a clean turn", verdict("send_message").verdict, D.ALLOW)
check("send_message asks once the turn is tainted",
      verdict("send_message", tainted=True).verdict, D.CONFIRM)
check("...and names the taint rule",
      verdict("send_message", tainted=True).rule, "outward_tainted")
check("dm_user asks once tainted", verdict("dm_user", tainted=True).verdict, D.CONFIRM)
check("pin_message stays free even tainted (not outward)",
      verdict("pin_message", tainted=True).verdict, D.ALLOW)
check("read_channel stays free even tainted",
      verdict("read_channel", tainted=True).verdict, D.ALLOW)

# Deny must beat confirm: an action refused outright should never come back asking
# to be approved, or Tyler could say yes to something that was never on offer.
check("a refused destructive action DENIES, it does not ask",
      verdict("purge_messages", guild_id=CHILLBAR).verdict, D.DENY)
check("a refused post DENIES even when tainted",
      verdict("send_message", tainted=True, guild_id=4040404040404040404,
              channel_id=999).verdict, D.DENY)

section("Full matrix — every action against every origin")
# GUEST_DM was missing from this list until stage 4, and it was the one that
# mattered most. Today the guest lane is a separate file that passes no client
# tools, so the boundary is PHYSICS - a guest cannot reach a capability because
# the code that would call one is not in the file they talk to. Stage 4 merges the
# lanes, and the moment it does, the only thing between a guest and the whole
# registry is policy.py. INTENT.md is explicit that this matrix must cover guests
# BEFORE that merge, not after - so this row exists first, and the merge has to
# keep it green.
ORIGINS = [Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE,
           Origin.LOCAL_CLI, Origin.SYSTEM, Origin.GUEST_DM]
matrix = {}
for name in sorted(capabilities.REGISTRY):
    matrix[name] = {o: allowed(name, o) for o in ORIGINS}

# Anything that is not reachable from every human route is, by definition, a
# deliberate restriction - so it must be one we can name. An action drifting into
# this list without a matching line here is exactly the silent-scope-change this
# test exists to catch.
# pc_task: DM + local CLI only, since the stage this file is named for. It is the
# ONLY entry now. The seven guest capabilities that used to join it (six ws_* plus
# read_shared_channel, restricted the other way round - guest DM only, so the
# owner's own routes could not reach them) were archived 2026-08-16; see
# archive/guest-tools/. A guest capability reappearing here without a deliberate
# decision is exactly what this assertion is for.
# answer_conversation joins it for stage 3 item 10: {OWNER_DM, LOCAL_CLI}, the same
# pair as pc_task and for a related reason. It writes words into the record as
# TYLER'S ANSWER, so it must not be reachable from a room other people can write in
# - a guild message that talked the model into calling it would be putting words in
# his mouth on a record other decisions are then made from. SYSTEM is excluded for
# the sharper version of the same thing: a timer must never answer on his behalf.
# spawn_in_room joined 2026-08-18 as pc_task's successor and LEFT with it in
# Phase B (INTENT 39); the pc_task in this matrix is _testconfig's double.
# restart and guest_off joined 2026-09-05 (INTENT 43, 44): {OWNER_DM,
# LOCAL_CLI}, because a mention in a room strangers write in must not be able
# to bounce the process or lock guests out, and a timer must never do either.
# deliver_unprompted joined 2026-08-20 with the initiative lane, and it is
# restricted the OTHER way round from the three above: {LOCAL_CLI, SYSTEM}, with
# every human origin excluded. Not a hardening of something Tyler might want to
# do by hand - there is nothing to do by hand. It sends a question Claude decided
# to ask him, so a human who wants to say something to Tyler simply says it. The
# grant is the two routes that actually drive it: the daily job through the CLI,
# and any future timer. Every name added to that set is a new way for a message
# he did not ask for to reach him.
EXPECTED_RESTRICTED = {"pc_task", "answer_conversation", "deliver_unprompted",
                       "restart", "guest_off"}
restricted = {n for n, row in matrix.items()
              if not all(row[o] for o in
                         (Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE,
                          Origin.LOCAL_CLI))}
check("exactly the expected capabilities are origin-restricted",
      restricted, EXPECTED_RESTRICTED)

# The most security-relevant line in this file. SYSTEM is what a timer gets, and a
# timer has no human to refuse anything - so every name here is a thing the machine
# may do while nobody is watching.
#
#   set_presence         - cosmetic, applied on login by on_ready.
#   (triage_conversation - stage 3 item 13's read-only Claude Code session -
#                          stood here until Phase B deleted the PC lane, INTENT 39.)
#   notify_owner         - added for stage 3 item 11. The recipient is ALWAYS the
#                          owner and cannot be chosen, so the worst an automated
#                          caller can do is talk to Tyler - and a watchdog noticing
#                          a dead server is the archetypal case, with nobody to ask.
#                          It also cannot pick its own volume: callers state the
#                          KIND and notify.py maps it to a tier.
#   tell_conversation    - added for stage 3 item 9. Same bounded shape: it takes
#                          a conversation id, the recipient comes from the record,
#                          and the substance is the OUTCOME already written there.
#                          It refuses outright unless the conversation is closed
#                          with a real outcome, so a timer cannot use it to send an
#                          empty "update". SYSTEM is the point - closing the loop
#                          must not depend on anyone remembering to.
#   advance_conversation - added for stage 3 item 8, one of two outward actions
#                          SYSTEM can reach. It is safe to grant because it cannot
#                          CHOOSE anything: it takes a conversation id, and both the
#                          recipient and the words come from a record a human
#                          opened. The nudge count is capped in conversations.py and
#                          the 15-minute clock rate-limits it. Granting dm_user to
#                          SYSTEM would have been the lazy version and would have
#                          handed a loop the ability to message anyone anything.
#
#   deliver_unprompted   - added 2026-08-20, the initiative lane. The THIRD outward
#                          action SYSTEM can reach, and it wears the same bounded
#                          shape as the other two for the same reason: a
#                          conversation id in, recipient and words off the record,
#                          nothing chosen at the call site. What is different is
#                          that the record it reads was written by a timer rather
#                          than by a human, so the rate limiting that
#                          advance_conversation gets for free from the nudge cap
#                          is done explicitly instead - policy.authorize_unprompted
#                          allows one unanswered question at a time, a 48-hour
#                          floor, and dormancy after two go unanswered. It is also
#                          the only one of the three a human origin CANNOT reach.
#
# A further name appearing here without a deliberate decision is what this asserts on.
EXPECTED_SYSTEM = {"set_presence", "advance_conversation", "tell_conversation",
                   "notify_owner", "deliver_unprompted"}
system_ok = {n for n, row in matrix.items() if row[Origin.SYSTEM]}
check("exactly the expected capabilities are SYSTEM-reachable",
      system_ok, EXPECTED_SYSTEM)

# THE ASSERTION STAGE 4 EXISTS TO PROTECT.
#
# Adding GUEST_DM as a matrix ROW is worth nothing on its own - the first version
# of this change computed the column and asserted nothing about it, which is the
# same "collected but never checked" shape that let test_injection corrupt memory
# for twelve days while passing.
#
# EMPTY is the correct answer today: the guest tool loop was archived 2026-08-16,
# so nothing declares guest=True and guest_grants() is empty. Two independent
# rules produce that - rule_guest refuses anything not named in guest.capabilities
# (fail-closed, and nothing is named), and rule_origin_allowed refuses because
# GUEST_DM is not in DEFAULT_ORIGINS - and either alone would be sufficient.
#
# This comment said "rule_owner" on its first draft, copied from the docs it was
# written alongside. That was the stale claim, reproduced one more time by someone
# who had just read it. Which is the argument for the assertions below.
#
# The merge in item 13 dissolves the physical file boundary that currently makes
# this true by construction. When it does, THIS LINE is what stands between a
# guest and fifty-six capabilities. It must still read `set()` afterwards, and a
# capability appearing here is a security regression, not a test to update.
EXPECTED_GUEST = set()
guest_ok = {n for n, row in matrix.items() if row[Origin.GUEST_DM]}
check("NOTHING in the registry is reachable by a guest", guest_ok, EXPECTED_GUEST)

# And the REASON, not just the result: assert each denial independently, so an edit
# that weakens one is caught by the other rather than by nobody.
#
# Writing this found a stale claim in the security documentation. Both policy.py's
# comment on Origin.HUMAN and README's guest section said the two denials were
# rule_owner and rule_origin_allowed - but rule_owner STEPS ASIDE for guest
# origins ("Guest origins are rule_guest's lane"), which changed in the guest
# refactor and neither doc followed. Not a hole - rule_guest and
# rule_origin_allowed still refuse independently - but a comment that names a
# defence which no longer fires is how a real hole gets opened later, by someone
# deleting rule_guest on the belief that rule_owner has it covered. Both docs
# corrected; these assertions now pin the true pair.
_probe = capabilities.REGISTRY["read_channel"]
_gctx = ctx_for(Origin.GUEST_DM)
check("rule_guest alone refuses a guest (nothing is granted)",
      policy.rule_guest(_probe, _gctx) is not None, True)
check("rule_origin_allowed alone refuses a guest too",
      policy.rule_origin_allowed(_probe, _gctx) is not None, True)
check("and rule_owner deliberately does NOT - it is rule_guest's lane",
      policy.rule_owner(_probe, _gctx), None)

print(f"\n  matrix covers {len(matrix)} actions x {len(ORIGINS)} origins "
      f"= {len(matrix) * len(ORIGINS)} combinations")

# ==========================================================================
section("The face dimension — the same matrix, per identity (commit 3)")
# ==========================================================================
# PLAN-second-face commit 3: CallContext carries which bot identity a call acts
# AS, and the gate rules read that face's config block. Three properties, in
# rising order of consequence:
#   1. a context that never mentions faces IS the primary face, everywhere -
#      commit 3 must be invisible to every pre-faces call site;
#   2. the two context copies carry the face - a copy that reverted to the
#      primary would be the with_taint laundering bug wearing a new field;
#   3. under a declared faces config, each face answers from its OWN block -
#      owners, agent guilds, guest lane, destructive scope, posting scope.

_base = CallContext.owner_dm(TYLER, 111)
check("a context built without a face carries the primary face",
      _base.face, identity.PRIMARY_FACE)
check("for_target carries the face", _base.for_target(TESTING, 999).face, _base.face)
check("with_taint carries the face", _base.with_taint().face, _base.face)
_cx = CallContext.owner_dm(TYLER, 111, face="codex")
check("an explicit face survives for_target", _cx.for_target(TESTING).face, "codex")
check("an explicit face survives with_taint", _cx.with_taint().face, "codex")


def ctx_for_face(origin, face, tainted=False, guild_id=TESTING):
    c = ctx_for(origin, tainted, guild_id)
    return CallContext(c.origin, c.actor_id, c.guild_id, c.channel_id, c.tainted,
                       face=face)


def allowed_face(action_name, origin, face):
    act = capabilities.REGISTRY[action_name]
    return policy.authorize(act, ctx_for_face(origin, face)).allowed


# Property 1, stated over the WHOLE matrix rather than a sample: naming the
# primary face explicitly changes no cell anywhere.
_matrix_explicit = {name: {o: allowed_face(name, o, identity.PRIMARY_FACE)
                           for o in ORIGINS}
                    for name in matrix}
check("naming the primary face explicitly changes nothing, anywhere",
      _matrix_explicit == matrix, True)

# Property 3 needs a declared faces config. Reload identity against one whose
# benham block mirrors the fixture's top-level keys EXACTLY, so the benham
# matrix stays comparable cell for cell. reload() mutates the module in place,
# so policy's `identity` reference follows automatically - and so does every
# check below, which is why the fixture restore at the end is itself asserted.
import importlib
import json as _json
import shutil as _shutil
import tempfile as _tempfile

OTHER_OWNER = 888777666555444333  # invented, in the style of STRANGER above
CODEX_GUILD = 1777000777000777001  # invented: the one guild codex coordinates

_orig_config_dir = __import__("benham.paths", fromlist=["paths"]).CONFIG_DIR
from benham import paths as _paths
_face_scratch = _tempfile.mkdtemp(prefix="benham-policy-faces-")
_two_face = {
    "faces": {
        "benham": {k: identity.CONTROL[k] for k in identity._FACE_KEYS
                   if k in identity.CONTROL},
        "codex": {
            "token_env": "CODEX_KEY",
            "owner_ids": [TYLER],
            "agent_guilds": [],
            "destructive_guilds": [CODEX_GUILD],
            "post_guilds": [CHILLBAR],
            # pc_task granted HERE on purpose: the machine wall must beat the
            # config, or "no shell for the second face" is a convention.
            "capabilities": ["send_message", "read_channel", "purge_messages",
                             "pc_task"],
        },
        # One grant, so the owner-separation checks below can see past the
        # (correct) empty-until-granted default that commit 4 added.
        "otherface": {"owner_ids": [OTHER_OWNER],
                      "capabilities": ["send_message"]},
    },
    "shared": {"issues": identity.CONTROL.get("issues") or {}},
}
with open(_os.path.join(_face_scratch, "control.json"), "w", encoding="utf-8") as _f:
    _json.dump(_two_face, _f)
_paths.CONFIG_DIR = _face_scratch
importlib.reload(identity)

_matrix_b = {name: {o: allowed_face(name, o, "benham") for o in ORIGINS}
             for name in matrix}
check("declaring faces changes NOTHING for the primary face - full matrix identical",
      _matrix_b == matrix, True)

_matrix_c = {name: {o: allowed_face(name, o, "codex") for o in ORIGINS}
             for name in matrix}
check("codex reaches nothing by guild mention (its agent_guilds is empty)",
      all(not row[Origin.OWNER_GUILD] for row in _matrix_c.values()), True)

# Commit 4: the grant table, asserted as an EXACT set per column, the way the
# guest matrix is. Four names are granted in config; pc_task is refused anyway
# by the machine wall, so exactly three survive from a DM. (An earlier
# revision asserted codex's columns EQUAL benham's, with a note that commit 4
# must consciously edit that line. This is that edit.)
check("codex reaches EXACTLY its grant, minus the machine wall, from a DM",
      {n for n in _matrix_c if _matrix_c[n][Origin.OWNER_DM]},
      {"send_message", "read_channel", "purge_messages"})
check("...and the same from the local CLI",
      {n for n in _matrix_c if _matrix_c[n][Origin.LOCAL_CLI]},
      {"send_message", "read_channel", "purge_messages"})
check("nothing in codex's grant is SYSTEM-reachable, so its SYSTEM column is empty",
      {n for n in _matrix_c if _matrix_c[n][Origin.SYSTEM]}, set())

# The machine wall: config granted pc_task to codex above, and it must lose.
_pd = policy.authorize(capabilities.REGISTRY["pc_task"],
                       ctx_for_face(Origin.OWNER_DM, "codex"))
check("pc_task refuses from codex even though the config grants it",
      _pd.denied, True)
check("...and the machine wall names itself", _pd.rule, "face_machine")
check("an UNGRANTED capability names the grant rule, not the wall",
      policy.authorize(capabilities.REGISTRY["pin_message"],
                       ctx_for_face(Origin.OWNER_DM, "codex")).rule,
      "face_capability")
check("benham's own pc_task is untouched by the wall",
      allowed_face("pc_task", Origin.OWNER_DM, "benham"), True)

# Separately owned (decision 2): being Tyler buys nothing on a face whose
# owner list does not name him, and the refusal is the owner rule doing it.
_d = policy.authorize(send, CallContext.owner_dm(TYLER, 111, face="otherface"))
check("Tyler does not direct a face that does not name him", _d.allowed, False)
check("...and the refusal names the owner rule", _d.rule, "owner")
check("the face's own owner directs it",
      policy.authorize(send, CallContext.owner_dm(OTHER_OWNER, 111,
                                                  face="otherface")).allowed, True)
check("an UNDECLARED face obeys nobody (fail closed)",
      policy.authorize(send, CallContext.owner_dm(TYLER, 111,
                                                  face="ghost")).allowed, False)

# The guest lane is per face: the fixture guest is benham's guest, not codex's.
_gb = CallContext.guest_dm(_testconfig.GUEST_ID, 111)
_gc = CallContext.guest_dm(_testconfig.GUEST_ID, 111, face="codex")
check("benham's guest lane is on under the faces shape",
      policy.may_chat_as_guest(_gb).allowed, True)
check("the same person is refused on codex's lane",
      policy.may_chat_as_guest(_gc).allowed, False)
check("...because codex's guest surface is off, not because of who they are",
      policy.rule_guest(_probe, _gc).rule, "guest_disabled")


def target_face(face, guild_id, channel_id=999):
    return CallContext.owner_dm(TYLER, 111, face=face).for_target(guild_id, channel_id)


# Per-face-per-guild tier 3 - the composition the plan calls the free win:
# codex holds tier 3 in the one guild it coordinates and nowhere else, with no
# new mechanism. Same guild, same action, three answers by face alone.
check("purge in Testing still CONFIRMS for benham under the faces shape",
      policy.authorize_target(purge, target_face("benham", TESTING)).needs_confirm,
      True)
check("purge in codex's OWN guild CONFIRMS - tier 3 where it coordinates",
      policy.authorize_target(purge, target_face("codex", CODEX_GUILD)).needs_confirm,
      True)
_dc = policy.authorize_target(purge, target_face("codex", TESTING))
check("purge in Testing is DENIED for codex - not on ITS list", _dc.denied, True)
check("...naming the destructive rule", _dc.rule, "destructive_guild")
check("benham holds no tier 3 in codex's guild - the confinement cuts both ways",
      policy.authorize_target(purge, target_face("benham", CODEX_GUILD)).denied,
      True)
check("purge is denied for an undeclared face too",
      policy.authorize_target(purge, target_face("ghost", TESTING)).denied, True)

# Posting scope per face, including the declared-face deny default.
check("codex posts where its own list says (Chillbar)",
      policy.authorize_target(send_a, target_face("codex", CHILLBAR, 123)).allowed,
      True)
check("codex may NOT post into Testing, though benham may",
      (policy.authorize_target(send_a,
                               target_face("codex", TESTING, 809357286036078612)).allowed,
       policy.authorize_target(send_a,
                               target_face("benham", TESTING, 809357286036078612)).allowed),
      (False, True))
check("otherface declared no post_guilds at all: DENIED, not allow-everything",
      policy.authorize_target(send_a,
                              target_face("otherface", TESTING, 123)).allowed, False)

# Restore the fixture and prove the module comes back whole - the reload above
# would otherwise leak into any check added after this section.
_paths.CONFIG_DIR = _orig_config_dir
importlib.reload(identity)
_shutil.rmtree(_face_scratch, ignore_errors=True)
check("fixture restored: the legacy matrix answer holds again",
      allowed("send_message", Origin.OWNER_DM), True)
check("fixture restored: no faces declared", identity.FACES_DECLARED, False)

print(f"\n  face dimension: {len(matrix)} actions x {len(ORIGINS)} origins x 2 faces "
      f"re-checked, plus per-face target rules")

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
