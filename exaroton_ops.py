"""
exaroton_ops.py — async, bot-safe access layer over Tyler's exaroton skill.

Reuses the skill's auth (`headers`/`load_token`) and `BASE` URL, but wraps every HTTP call
so a network blip or API error raises a NORMAL exception (ExarotonError / requests errors)
instead of the skill's `sys.exit(...)`, which would kill the long-lived bot process. Calls
run in a worker thread so they never block the discord.py event loop.

All ops take a RAW exaroton server ID (never `resolve_server`) — that avoids a per-call
`GET /servers/` and the "'Isle of Berk' is a world label, not the API name" resolution trap.
"""

import sys
import asyncio

import requests

# The exaroton skill holds the token (.env) + constants. Reuse ONLY its auth + BASE,
# NOT its sys.exit-y api() helper.
EXA_DIR = r"C:\Users\Tyler\.claude\skills\exaroton"
if EXA_DIR not in sys.path:
    sys.path.insert(0, EXA_DIR)

from exaroton import headers, BASE  # noqa: E402


class ExarotonError(Exception):
    """Raised on any non-success exaroton API response (never SystemExit)."""


def _sync_request(method, path, timeout=15.0):
    r = requests.request(method, f"{BASE}{path}", headers=headers(), timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if not body.get("success", False):
        raise ExarotonError(body.get("error") or f"{method} {path} failed")
    return body.get("data")


async def _exa(method, path):
    """Async, non-blocking exaroton call. Raises ExarotonError / requests exceptions."""
    return await asyncio.to_thread(_sync_request, method, path)


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
