"""
_testconfig.py - the control plane the gate tests measure against.

Import this BEFORE any `from benham...` line in a test that asserts on a gate
(who Benham obeys, which guilds allow the agent, where tier 3 may run, where it
may post). Importing it later does nothing: identity.py resolves and reads its
control file at import, so the redirect has to be in place first.

    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    import _testconfig  # noqa: F401,E402 - must precede every benham import

WHY THIS EXISTS. config/control.json is gitignored - it names real people and
real guilds - so a fresh clone or a git worktree has none. identity.py then falls
back to its restrictive defaults, and five test files went red: no agent guilds,
no destructive guilds, and (in the other direction) no posting cap at all. That
fallback is the RIGHT production behaviour and is deliberately not what this file
changes; "a missing config should cost capability, never safety" is identity.py's
own rule and it stays. The problem was never the fallback. It was that the tests
were reading their fixture out of a file nobody can commit, so what they measured
depended on whose machine ran them.

config/control.json.example is committed and already carries exactly the ids the
tests hardcode, so it IS the fixture - copied to a scratch dir as control.json,
which paths.CONFIG_DIR then points at. Two things follow, both wanted:

  * The example file is now CHECKED rather than merely documented. Let it drift
    from the shape identity.py expects and five test files say so.
  * The suite means the same thing everywhere. It deliberately does NOT prefer a
    real control.json when one exists: the tests assert on specific guild ids, so
    reading Tyler's live file would turn any legitimate edit to it - a new guild,
    a retired one - into a test failure about nothing. That is the trap that put
    this file here, and preferring the real file would just re-set it.

The scratch dir gets no exaroton_watch.json, on purpose. bot.py has to survive
that file being absent (it did not, until 2026-08-17), so every run of the suite
now exercises the absent-config path rather than trusting it.

Pointing paths.* at a fixture is the idiom test_selfrecord.py already uses for
LOG_DIR; this does the same for CONFIG_DIR, one import earlier.

THE ONE PLACE THE FIXTURE DIVERGES FROM THE EXAMPLE is the guest block, and it
has to. The example ships guests OFF with an empty allowlist, which is the right
default for a file people copy - but it makes test_policy's central guest
assertion vacuous. "NOTHING in the registry is reachable by a guest" is supposed
to be produced by two independent rules (rule_guest grants nothing, and GUEST_DM
is not in DEFAULT_ORIGINS), and with the surface switched off a THIRD rule -
guest_disabled - short-circuits ahead of both. Fifty-six capabilities would then
report "refused" for a reason nobody is testing, and the day someone re-declares
guest=True on a capability the matrix would keep saying set() anyway.

So the fixture switches guests on and whitelists GUEST_ID. It is a made-up number
in the style of test_policy's STRANGER, which is the second half of why this
block exists: the fallback it replaces was a REAL guest's Discord id, hardcoded
into a tracked test file. The tests never needed a real one - they need an id
that is on whatever allowlist they run against, and the fixture owns that
allowlist. An env var would have been the wrong fix twice over: it would relocate
a real id rather than retire it, and it would put the tests back to depending on
a value that is not in the repo, which is the bug this file was written to end.
"""

import atexit
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham import paths  # noqa: E402 - imports nothing but os; cannot be a cycle

# Invented, and belongs to nobody. Never an owner id - identity.is_guest() refuses
# the overlap, so an owner here would silently make every guest row a non-guest row.
GUEST_ID = 555000555000555000

_EXAMPLE = os.path.join(paths.CONFIG_DIR, "control.json.example")

_fixture_dir = tempfile.mkdtemp(prefix="benham-testconfig-")
atexit.register(shutil.rmtree, _fixture_dir, True)

with open(_EXAMPLE, encoding="utf-8") as _f:
    _cfg = json.load(_f)

# Minimum divergence: flip the switch and name a guest, leave everything else -
# including the absent "mode" key, so the fixture exercises the same default
# guest_enabled() reads in production.
_cfg["guest"] = {**_cfg.get("guest", {}), "enabled": True, "ids": [GUEST_ID]}

with open(os.path.join(_fixture_dir, "control.json"), "w", encoding="utf-8") as _f:
    json.dump(_cfg, _f, indent=2)

# Must happen before identity.py is imported - it joins this at import time and
# reads the result immediately.
paths.CONFIG_DIR = _fixture_dir
