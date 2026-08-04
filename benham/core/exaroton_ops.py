"""
exaroton_ops.py - async, bot-safe access layer over Tyler's exaroton skill.

The skill's api() used to sys.exit on every error path, which is fatal inside a bot
that runs for days, so this module reimplemented the whole HTTP layer just to get an
exception instead. The skill now raises ExarotonError and only converts it to an exit
code at its own CLI boundary, so there is one request implementation again and one
status table - this file just moves the call off the event loop.

All ops take a RAW exaroton server ID (never `resolve_server`) - that avoids a
per-call `GET /servers/` and the "'Isle of Berk' is a world label, not the API name"
resolution trap.
"""

import asyncio
import os
import sys

# The exaroton skill holds the token (.env), the request layer and the status table.
# Path is overridable because this hardcoded absolute path was the one thing in the
# file that made it machine-specific.
EXA_DIR = os.environ.get(
    "EXAROTON_SKILL_DIR",
    os.path.join(os.path.expanduser("~"), ".claude", "skills", "exaroton"),
)
if EXA_DIR not in sys.path:
    sys.path.insert(0, EXA_DIR)

# ExarotonError is re-exported so callers (bot.py) keep importing it from here.
from exaroton import ExarotonError, api, status_label  # noqa: E402,F401


async def _exa(method, path):
    """Async, non-blocking exaroton call. Raises ExarotonError / requests exceptions."""
    return await asyncio.to_thread(api, method, path)


# --- high-level ops (all by raw server ID) ---
async def all_servers():
    """List all servers (one call) -> list of dicts with id/name/status/players/..."""
    return await _exa("GET", "/servers/")


async def one_server(sid):
    """One server's full status dict."""
    return await _exa("GET", f"/servers/{sid}")


async def start(sid):
    return await _exa("GET", f"/servers/{sid}/start/")


async def stop(sid):
    return await _exa("GET", f"/servers/{sid}/stop/")


async def restart(sid):
    return await _exa("GET", f"/servers/{sid}/restart/")


async def credits():
    """Remaining account credits (float)."""
    data = await _exa("GET", "/account/")
    return data["credits"]
