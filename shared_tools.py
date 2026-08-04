"""
shared_tools.py - Anthropic SERVER-SIDE tools, shared across Benham's surfaces.

Extracted from guest.py (Stage 0 of PLAN-guest-permissions.md). The tool block,
the query extraction and the log format were about to be needed in three places -
guest chat, the owner agent (Stage 1 gives Tyler search too), and the future guest
tool loop - and three inline copies of a wire-format dict is how one of them
quietly ends up a version behind.

Everything in this module describes tools that execute on ANTHROPIC'S
infrastructure. That is a property, not an implementation detail: guest.py's whole
security story rests on passing no client tools, and a module named shared_tools
that grew a client tool would be the perfect place to break that story by
accident. Do not add client-tool builders here. Server-side only.

No state, no Discord, no config reads. Callers bring their own caps, their own
log paths and their own charging rules - what a search COSTS is policy that
differs per surface (guests pay double against a cap; the owner just gets a log
line), and this module answering it for them is how the surfaces would drift.
"""

import json
from datetime import datetime, timezone

# Anthropic's server-side web search tool, pinned. If Anthropic ships a newer
# revision, bump it HERE and every surface moves together - that is the point of
# the module.
WEB_SEARCH_TYPE = "web_search_20250305"


def web_search_tool(max_uses):
    """The `tools` entry for server-side web search, capped at max_uses per turn."""
    return {"type": WEB_SEARCH_TYPE, "name": "web_search", "max_uses": int(max_uses)}


def search_queries(resp):
    """The query strings a response actually searched for, in order.

    Reads the server_tool_use blocks. Unchanged from guest.respond's inline
    version: a block with no readable query reports "?" rather than being dropped,
    because the moderation trail should say a search happened even when it cannot
    say what for.
    """
    return [getattr(b, "input", {}).get("query", "?")
            for b in getattr(resp, "content", [])
            if getattr(b, "type", "") == "server_tool_use"]


def response_text(resp):
    """One response's text blocks as ONE string - concatenated, no separator.

    Moved here from agent.py (Stage 3) because both tool loops need it and the
    bug it fixes is a property of cited responses, not of either loop: a cited
    answer arrives as MANY text blocks, one per cited span, and they are
    fragments of one utterance. "" is the only correct joiner within a single
    response; whatever joiner a loop uses BETWEEN rounds is its own business.
    """
    return "".join(b.text for b in getattr(resp, "content", [])
                   if getattr(b, "type", "") == "text").strip()


def log_searches(path, actor_id, queries, role):
    """Append each query to a JSONL moderation trail.

    Same shape guest_searches.jsonl always had - {ts, user_id, query} - plus
    `role` ("guest" | "owner") so one glance at a line says which surface it came
    from; existing lines simply lack the key. Append-only, written even if the
    reply then fails to send: the search happened, so it belongs in the record.
    Swallows OSError for the same reason the original did - a full disk should
    cost the log line, not the turn.
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with open(path, "a", encoding="utf-8") as f:
            for q in queries:
                f.write(json.dumps({"ts": ts, "user_id": int(actor_id),
                                    "query": str(q), "role": str(role)}) + "\n")
    except OSError:
        pass
