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

import sys

import capabilities
import identity
import policy
from policy import CallContext, Origin

TYLER = 273967061619965952
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
    raise AssertionError(origin)


def allowed(action_name, origin, tainted=False, guild_id=TESTING):
    act = capabilities.REGISTRY[action_name]
    return policy.authorize(act, ctx_for(origin, tainted, guild_id)).allowed


# --------------------------------------------------------------------------
section("Fail closed — the property that makes threading mistakes safe")
send = capabilities.REGISTRY["send_message"]
check("no context is denied", policy.authorize(send, None).allowed, False)
check("an unknown origin is denied",
      policy.authorize(send, CallContext("something_new", TYLER)).allowed, False)
check("the denial names the rule",
      policy.authorize(send, None).rule, "context_present")

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
base = CallContext.owner_dm(TYLER, 111, tainted=True)
derived = base.with_taint(False)
check("with_taint returns a new object", derived is base, False)
check("the original keeps its taint", base.tainted, True)

section("Full matrix — every action against every origin")
ORIGINS = [Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE,
           Origin.LOCAL_CLI, Origin.SYSTEM]
matrix = {}
for name in sorted(capabilities.REGISTRY):
    matrix[name] = {o: allowed(name, o) for o in ORIGINS}

# Anything that is not reachable from every human route is, by definition, a
# deliberate restriction - so it must be one we can name. An action drifting into
# this list without a matching line here is exactly the silent-scope-change this
# test exists to catch.
EXPECTED_RESTRICTED = {"pc_task"}
restricted = {n for n, row in matrix.items()
              if not all(row[o] for o in
                         (Origin.OWNER_DM, Origin.OWNER_GUILD, Origin.OWNER_VOICE,
                          Origin.LOCAL_CLI))}
check("exactly the expected capabilities are origin-restricted",
      restricted, EXPECTED_RESTRICTED)

EXPECTED_SYSTEM = {"set_presence"}
system_ok = {n for n, row in matrix.items() if row[Origin.SYSTEM]}
check("exactly the expected capabilities are SYSTEM-reachable",
      system_ok, EXPECTED_SYSTEM)

print(f"\n  matrix covers {len(matrix)} actions x {len(ORIGINS)} origins "
      f"= {len(matrix) * len(ORIGINS)} combinations")

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
