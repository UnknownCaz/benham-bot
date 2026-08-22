"""caller.py - which Claude session is driving this CLI process, if any.

Conversation records carry the asker's `local_` session id so the Raven courier
lane (C:\\Users\\Tyler\\Claude\\RAVEN.md) can deliver an arrived answer straight
to the session that asked, instead of matching by cwd and hoping. cwd-matching
stays as Raven's fallback for records opened before this existed - c18's answer
sat uncollected for two days because its owner died and nothing else could be
identified as the owner.

Two sources, in order of directness, and then None - NEVER a guess. A wrong id
here routes somebody's answer into an unrelated session, which is strictly worse
than the cwd fallback this exists to improve on:

  1. CLAUDE_CODE_HOST_SESSION_ID - the harness exports the `local_` id itself
     into every shell it runs. Taken only when it actually looks like one.
  2. CLAUDE_CODE_SESSION_ID - the CLI-side UUID (the one in the scratchpad
     path). The desktop app's session store maps it to the `local_` id; scan
     for the file whose `cliSessionId` matches.

A terminal-only session, a scheduled run without the env, or a bot process has
neither and resolves to None. IMPORTANT for callers: only resolve in a process a
Claude session actually started - the BOT must never call this. Its environment
is whatever its supervisor inherited at launch, so a leaked env var would stamp
every bot-opened conversation with some long-dead session and Raven would route
real answers at a ghost.
"""

import glob
import json
import os
import re

_LOCAL_RE = re.compile(r"^local_[0-9a-fA-F-]{36}$")

# The desktop app's session store - the same path tools/corkboard-hook checks.
# A list so a future non-packaged install location is one line, not a rewrite.
_STORES = [os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Packages", "Claude_pzs8sxrjxfjjc", "LocalCache",
                        "Roaming", "Claude", "claude-code-sessions")]


def session_id():
    """The calling session's `local_` id, or None. Never a guess."""
    host = (os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or "").strip()
    if _LOCAL_RE.match(host):
        return host

    cli = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not cli:
        return None
    for store in _STORES:
        if not store or not os.path.isdir(store):
            continue
        for path in glob.glob(os.path.join(store, "**", "local_*.json"),
                              recursive=True):
            try:
                with open(path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            if meta.get("cliSessionId") == cli:
                sid = str(meta.get("sessionId") or "")
                return sid if _LOCAL_RE.match(sid) else None
    return None
