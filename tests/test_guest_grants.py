"""
test_guest_grants.py - the guest grant machinery, proven fail-closed. (Stage 2)

The stage this suite guards ships with ZERO grants, so the headline assertion is
that nothing changed: every capability still denies a guest, exactly as before,
only now the refusal comes from the guest lane's own gate. The rest proves the
machinery will behave when Stage 4 starts flipping flags:

  The three-part grant. Registry flag AND origins AND control.json must agree.
  Each is checked missing-one-at-a-time, because "all three present works" is
  consistent with two of them being decorative.

  Typo-can-only-disable. Config listing pc_task grants nothing, because the
  flag is code and config cannot add to code.

  The registration invariant. A guest capability that is destructive, posting,
  confirming, outward, or taint-blocked refuses to REGISTER - the bot crashes at
  import rather than carrying a lying declaration.

  Never-confirms. On the guest lane, a would-be CONFIRM is a DENY. Tested with
  a hand-built Action because the invariant makes a registered one impossible -
  which is precisely why the rule must be tested directly: it is the layer that
  survives a future edit relaxing the invariant.

    python test_guest_grants.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package and bot.py live.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys

from benham.core import capabilities
from benham.core import identity
from benham.core import policy
from benham.core.policy import CallContext, Origin

TYLER = 273967061619965952
DOOM = 1097631170788851815
STRANGER = 999000999000999000

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


def set_guest(ids=(DOOM,), enabled=True, mode="chat", caps=()):
    """Same module-global surgery test_guest.py uses; adds the capabilities list."""
    identity.GUEST = {"enabled": enabled, "mode": mode, "ids": list(ids),
                      "capabilities": list(caps)}
    identity.GUEST_IDS = set(int(u) for u in ids)


set_guest()
gctx = CallContext.guest_dm(DOOM, 111)


# --------------------------------------------------------------------------
section("Stage 2 changes nothing: every capability still denies a guest")

reachable = [n for n, a in capabilities.REGISTRY.items()
             if policy.authorize(a, gctx).allowed]
check("zero capabilities reachable with empty config", reachable, [])
check("guest_grants() agrees", capabilities.guest_grants(), {})
check("a guest context is still born tainted", gctx.tainted, True)
check("rule ordering: rule_guest sits before rule_owner",
      policy.RULES.index(policy.rule_guest) < policy.RULES.index(policy.rule_owner),
      True)
check("rule_guest_never_confirms is first in TARGET_RULES",
      policy.TARGET_RULES[0], policy.rule_guest_never_confirms)


# --------------------------------------------------------------------------
section("A typo in config can only disable, never expose")

set_guest(caps=("pc_task", "purge_messages", "send_message", "dm_user",
                "read_channel", "delete_channel"))
reachable = [n for n, a in capabilities.REGISTRY.items()
             if policy.authorize(a, gctx).allowed]
check("config listing dangerous names grants none of them", reachable, [])
check("guest_grants() is still empty (no flag in code)",
      capabilities.guest_grants(), {})
for name in ("pc_task", "send_message", "read_channel"):
    check(f"{name}: refusal names the missing FLAG, not the config",
          policy.authorize(capabilities.REGISTRY[name], gctx).rule,
          "guest_capability")
set_guest()


# --------------------------------------------------------------------------
section("The three-part grant, one part missing at a time")

async def _noop(ctx, p):
    return {"ok": True}

# A legal guest declaration, registered for real and cleaned up at the end.
try:
    capabilities.action(
        "_test_ws_probe", identity.READ, "test-only guest capability", {},
        origins={Origin.GUEST_DM}, taints=True, guest=True)(_noop)

    probe = capabilities.REGISTRY["_test_ws_probe"]

    check("flag + origins, config missing -> guest_config refusal",
          policy.authorize(probe, gctx).rule, "guest_config")

    set_guest(caps=("_test_ws_probe",))
    check("all three present -> caller rules ALLOW",
          policy.authorize(probe, gctx).allowed, True)
    check("guest_grants() now contains exactly it",
          list(capabilities.guest_grants()), ["_test_ws_probe"])

    check("...and the OWNER cannot reach it (origins say guest DM only)",
          policy.authorize(probe, CallContext.owner_dm(TYLER, 111)).rule,
          "origin_allowed")

    check("a stranger on the guest origin is refused by the allowlist",
          policy.authorize(probe, CallContext.guest_dm(STRANGER, 111)).rule,
          "guest_allowlist")

    set_guest(enabled=False, caps=("_test_ws_probe",))
    check("guest.enabled=false shuts the whole lane",
          policy.authorize(probe, gctx).rule, "guest_disabled")

    set_guest(mode="definitely-not-a-mode", caps=("_test_ws_probe",))
    check("an unrecognised mode shuts the lane too",
          policy.authorize(probe, gctx).rule, "guest_disabled")

    set_guest(caps=("_test_ws_probe",))
    check("target rules pass a harmless granted capability",
          policy.authorize_target(probe, gctx).allowed, True)
finally:
    capabilities.REGISTRY.pop("_test_ws_probe", None)
    set_guest()


# --------------------------------------------------------------------------
section("The registration invariant refuses lying declarations at import")

BAD = [
    ("destructive", dict(tier=identity.DESTRUCTIVE, origins={Origin.GUEST_DM})),
    ("posts", dict(tier=identity.SPEAK, posts=True, origins={Origin.GUEST_DM})),
    ("always_confirm", dict(tier=identity.MANAGE, always_confirm=True,
                            origins={Origin.GUEST_DM})),
    ("outward", dict(tier=identity.SPEAK, outward=True,
                     origins={Origin.GUEST_DM})),
    ("blocked_when_tainted", dict(tier=identity.READ, blocked_when_tainted=True,
                                  origins={Origin.GUEST_DM})),
    ("origins missing GUEST_DM", dict(tier=identity.READ,
                                      origins={Origin.OWNER_DM})),
    ("origins omitted entirely", dict(tier=identity.READ)),
]
for label, kw in BAD:
    tier = kw.pop("tier")
    try:
        capabilities.action("_test_bad_guest", tier, "must not register", {},
                            guest=True, **kw)(_noop)
        registered = "_test_bad_guest" in capabilities.REGISTRY
        capabilities.REGISTRY.pop("_test_bad_guest", None)
        check(f"guest=True + {label} refuses to register",
              "registered" if registered else "silently skipped", "ValueError")
    except ValueError:
        check(f"guest=True + {label} refuses to register", "ValueError", "ValueError")

# Stage 2 shipped this as "zero capabilities carry the flag". Stage 4 added
# exactly the six workspace ones; anything beyond that set appearing here means
# someone flagged a capability without updating the suites that audit the set -
# which is the drift this check exists to catch.
check("the guest-flagged capabilities are exactly the Stage 4 six",
      sorted(n for n, a in capabilities.REGISTRY.items() if a.guest),
      ["ws_attach", "ws_delete", "ws_import", "ws_list", "ws_read", "ws_write"])


# --------------------------------------------------------------------------
section("On the guest lane, a would-be CONFIRM is a flat DENY")

# Hand-built, unregistered - the invariant makes a registered one impossible,
# and this rule exists precisely for the day that invariant is relaxed.
confirming = capabilities.Action(
    "_fake_confirming", identity.MANAGE, "", {}, _noop, False,
    always_confirm=True)
outward = capabilities.Action(
    "_fake_outward", identity.SPEAK, "", {}, _noop, False, outward=True)

d = policy.authorize_target(confirming, gctx)
check("needs_confirm on guest lane -> DENY", d.verdict, policy.Decision.DENY)
check("...by the never-confirms rule", d.rule, "guest_never_confirms")
d = policy.authorize_target(outward, gctx)
check("outward on guest lane -> DENY, never CONFIRM",
      d.verdict, policy.Decision.DENY)
check("the owner still gets CONFIRM for the same action, not DENY",
      policy.authorize_target(
          confirming, CallContext.owner_dm(TYLER, 111)).needs_confirm, True)


# --------------------------------------------------------------------------
section("The rest of the wall is where it was")

check("may_engage_agent still refuses guests",
      policy.may_engage_agent(gctx).rule, "engage_guest")
check("GUEST_DM is still absent from DEFAULT_ORIGINS",
      Origin.GUEST_DM in policy.DEFAULT_ORIGINS, False)
check("guest_capabilities() reads the config",
      (set_guest(caps=("a", "b")) or identity.guest_capabilities()),
      frozenset({"a", "b"}))
check("...and an absent key is the empty set",
      (set_guest() or identity.guest_capabilities()), frozenset())

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
