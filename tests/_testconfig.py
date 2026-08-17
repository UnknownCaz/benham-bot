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
"""

import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham import paths  # noqa: E402 - imports nothing but os; cannot be a cycle

_EXAMPLE = os.path.join(paths.CONFIG_DIR, "control.json.example")

_fixture_dir = tempfile.mkdtemp(prefix="benham-testconfig-")
atexit.register(shutil.rmtree, _fixture_dir, True)

shutil.copyfile(_EXAMPLE, os.path.join(_fixture_dir, "control.json"))

# Must happen before identity.py is imported - it joins this at import time and
# reads the result immediately.
paths.CONFIG_DIR = _fixture_dir
