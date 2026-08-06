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

    Filters on the tool NAME, not merely the block type. That was a latent bug
    the moment a second server-side tool existed: code execution emits
    server_tool_use blocks too (named bash_code_execution /
    text_editor_code_execution), so a type-only filter would have logged every
    code run as a search with query "?" and charged the guest a search for it.
    Caught reading the docs before Stage 6 shipped rather than after.

    A web_search block with no readable query still reports "?" rather than
    being dropped: the moderation trail should say a search happened even when
    it cannot say what for.
    """
    return [getattr(b, "input", {}).get("query", "?")
            for b in getattr(resp, "content", [])
            if getattr(b, "type", "") == "server_tool_use"
            and getattr(b, "name", "") == "web_search"]


# Anthropic's server-side code execution. Same charter as web search: it runs
# on THEIR infrastructure, in a container with no internet access and full
# isolation from any host - which is the whole reason a guest may have it. The
# 20250825 version is the one every model supports, including the Haiku the
# guest lane runs on; newer versions add REPL persistence and programmatic
# tool calling, neither of which works on Haiku anyway.
CODE_EXECUTION_TYPE = "code_execution_20250825"
# The two sub-tools it exposes, by the names they appear under in responses.
CODE_TOOL_NAMES = ("bash_code_execution", "text_editor_code_execution")


def code_execution_tool():
    """The `tools` entry for server-side code execution. No parameters exist."""
    return {"type": CODE_EXECUTION_TYPE, "name": "code_execution"}


def code_runs(resp):
    """What the container was asked to do, in order, as loggable strings.

    One entry per sub-tool call - which is what a run costs, and what the
    moderation trail wants: the command for a bash call, the operation and path
    for a file edit. Deliberately NOT file contents; a guest writing a hundred
    lines of Python should not put a hundred lines into the ops log.
    """
    out = []
    for b in getattr(resp, "content", []):
        if getattr(b, "type", "") != "server_tool_use":
            continue
        name = getattr(b, "name", "")
        if name not in CODE_TOOL_NAMES:
            continue
        inp = getattr(b, "input", {}) or {}
        if name == "bash_code_execution":
            out.append("bash: " + str(inp.get("command", "?"))[:200])
        else:
            out.append(f"{inp.get('command', 'edit')}: "
                       + str(inp.get("path", "?"))[:120])
    return out


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
