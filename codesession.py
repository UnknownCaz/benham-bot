"""
codesession.py - gives Benham a real Claude Code session on Tyler's PC.

Not a reimplementation. This drives the actual Claude Code CLI through the agent
SDK, so the session Benham gets is the same one Tyler gets at his desk: the same
tools, and - because setting_sources loads his user settings - the same skills.
"restart Isle of Berk" works because the exaroton skill is right there, not
because anything here knows what exaroton is.

The permission model Tyler chose is "anything, but ask before changing", and
can_use_tool is where that lives. Reading, searching and fetching run freely.
Anything that writes a file, runs a command, or spawns a subagent stops, DMs him,
and waits for a real answer.

Two things worth understanding about that gate:

  It blocks, it does not queue. The tool call is suspended on a future until Tyler
  answers or it times out. A timeout denies - the session is told no and carries on
  with that knowledge, rather than silently proceeding or hanging forever.

  The allowlist is of READS, not of writes. Anything unrecognised is treated as a
  change and asks. When a future Claude Code version adds a tool this file has
  never heard of, the failure mode is an unnecessary question, not an unreviewed
  write.

Tyler explicitly chose to leave secret files readable. That is his call, and the
gate above does not protect them - reads are free, so anyone who can DM Benham can
ask for a token. What this module does instead is make it loud: every read of a
credential-shaped path is logged at SECRET-READ, so there is a trail even though
there is no block.
"""

import asyncio
import os
import re

from dotenv import load_dotenv

import identity

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "environ.env"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.path.join(os.path.dirname(BASE_DIR), "..", "Discord-Claude")
WORKDIR = os.path.abspath(
    identity.CONTROL.get("pc", {}).get("workdir")
    or os.path.join(os.path.expanduser("~"), "Claude", "Discord-Claude")
)

_pc_cfg = identity.CONTROL.get("pc", {}) or {}
ENABLED = bool(_pc_cfg.get("enabled", False))
MODEL = _pc_cfg.get("model") or None          # None = Claude Code's own default
PERMISSION_TIMEOUT = int(_pc_cfg.get("permission_timeout_seconds", 300))
MAX_TURNS = int(_pc_cfg.get("max_turns", 30))

# Tools that only observe. Everything else asks. Keep this list conservative -
# adding a tool here removes a question Tyler would otherwise have been asked.
READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "WebFetch", "WebSearch", "NotebookRead",
    "TodoWrite", "BashOutput", "ListMcpResourcesTool", "ReadMcpResourceTool",
}

# Credential-shaped paths. Not a block - Tyler chose full access - purely so a
# read of one is greppable in the log afterwards.
_SECRET_RE = re.compile(
    r"(\.env$|\.env\.|environ\.env|webhooks\.json|credentials|token|secret|"
    r"\.pem$|\.key$|id_rsa|\.npmrc|\.git-credentials)",
    re.IGNORECASE,
)

_client = None
_log = None
_ask_owner = None            # async fn(prompt_text) -> None, set by bot.py
_pending = {}                # request_id -> asyncio.Future[bool]
_seq = [0]


def configure(log, ask_owner):
    """Wire in bot.py's logger and its "DM Tyler and wait" callback."""
    global _log, _ask_owner
    _log = log
    _ask_owner = ask_owner


def log(msg):
    if _log:
        _log(msg)
    else:
        print(msg, flush=True)


# --------------------------------------------------------------------------
# The permission gate
# --------------------------------------------------------------------------

def _describe(tool_name, tool_input):
    """A one-line, human-readable version of what the session wants to do.

    Tyler is approving this on a phone, so the command itself has to be visible -
    "run a Bash command" is not something anyone can meaningfully consent to.
    """
    inp = tool_input or {}
    # Every shell tool, not just Bash. This environment's is called PowerShell, and
    # when it fell through to the generic branch below the command was truncated to
    # 80 characters - which would have had Tyler approving a command he could not
    # actually read. Match on the presence of a command field rather than a name
    # list, so a future shell tool cannot reintroduce the same hole.
    if "command" in inp:
        cmd = str(inp.get("command", "")).strip()
        return f"run a **{tool_name}** command:\n```\n{cmd[:1200]}\n```"
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or "?"
        verb = "create/overwrite" if tool_name == "Write" else "edit"
        return f"{verb} a file:\n`{path}`"
    if tool_name == "Task":
        return f"spawn a sub-agent:\n> {str(inp.get('description', ''))[:200]}"
    if tool_name == "KillShell":
        return f"kill a running shell (`{inp.get('shell_id', '?')}`)"
    detail = ", ".join(f"{k}={str(v)[:80]}" for k, v in list(inp.items())[:3])
    return f"use **{tool_name}**" + (f" ({detail})" if detail else "")


def _progress_label(block):
    """A short 'Bash: ls -la' style label for a live feed.

    Deliberately terser than _describe(), which exists to get an approval decision
    right and therefore shows the whole command. This is glanceable status on a
    phone; the full text still goes to the approval prompt when one is needed.
    """
    inp = getattr(block, "input", None) or {}
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
              or inp.get("description") or inp.get("task") or "")
    detail = " ".join(str(detail).split())
    if len(detail) > 60:
        detail = detail[:57] + "..."
    return f"{block.name}: {detail}" if detail else block.name


def answer(request_id, approved):
    """Resolve a pending permission request. Called from bot.py's message handler.

    Returns True if it matched something still waiting. A late answer - one that
    arrives after the timeout already denied - matches nothing and is ignored,
    which is the safe direction: the session has already been told no.
    """
    fut = _pending.pop(str(request_id), None)
    if fut is None or fut.done():
        return False
    fut.set_result(bool(approved))
    return True


def pending_request():
    """The id of the one outstanding permission request, or None."""
    for rid, fut in list(_pending.items()):
        if not fut.done():
            return rid
        _pending.pop(rid, None)
    return None


async def _can_use_tool(tool_name, tool_input, context):
    """The SDK permission callback. Free for reads, ask for everything else."""
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    if tool_name == "Read":
        path = str((tool_input or {}).get("file_path", ""))
        if _SECRET_RE.search(path):
            # Allowed on Tyler's explicit instruction; logged so it is never silent.
            log(f"SECRET-READ: session read a credential-shaped path: {path}")

    if tool_name in READ_ONLY_TOOLS:
        return PermissionResultAllow()

    if _ask_owner is None:
        return PermissionResultDeny(message="No owner channel is available to approve this.")

    _seq[0] += 1
    rid = str(_seq[0])
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _pending[rid] = fut

    what = _describe(tool_name, tool_input)
    log(f"PC-PERMISSION-ASK [{rid}] {tool_name}: {str(tool_input)[:300]}")
    try:
        # The request id rides along so the prompt can carry Approve/Deny buttons
        # that resolve THIS request. The buttons and the typed reply both land in
        # answer(), which is idempotent - first decision wins, the rest no-op.
        await _ask_owner(
            f"**Benham wants to {what}**\n\n"
            f"Tap a button, or reply **yes** / **no**. "
            f"(expires in {PERMISSION_TIMEOUT // 60}m)",
            rid,
        )
    except Exception as e:  # noqa: BLE001 — if we cannot ask, we must not proceed
        _pending.pop(rid, None)
        log(f"PC-PERMISSION [{rid}] could not reach owner: {e}")
        return PermissionResultDeny(message=f"Could not reach the owner to ask: {e}")

    try:
        approved = await asyncio.wait_for(fut, timeout=PERMISSION_TIMEOUT)
    except asyncio.TimeoutError:
        _pending.pop(rid, None)
        log(f"PC-PERMISSION [{rid}] TIMED OUT -> denied")
        return PermissionResultDeny(
            message="Timed out waiting for the owner. Treat this as a no; "
                    "tell him what you were trying to do and stop.")
    finally:
        _pending.pop(rid, None)

    if approved:
        log(f"PC-PERMISSION [{rid}] APPROVED {tool_name}")
        return PermissionResultAllow()
    log(f"PC-PERMISSION [{rid}] DENIED {tool_name}")
    return PermissionResultDeny(message="The owner said no. Do not retry this; "
                                        "explain what you wanted and stop.")


# --------------------------------------------------------------------------
# Running a task
# --------------------------------------------------------------------------

def _options():
    from claude_agent_sdk import ClaudeAgentOptions
    os.makedirs(WORKDIR, exist_ok=True)
    kw = dict(
        cwd=WORKDIR,
        can_use_tool=_can_use_tool,
        permission_mode="default",
        max_turns=MAX_TURNS,
        # Load Tyler's real settings so his skills are present. Without this the
        # session is a bare Claude Code with none of the tooling that makes it
        # useful to him specifically.
        setting_sources=["user", "project", "local"],
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": _persona() + _APPEND_PROMPT},
    )
    if MODEL:
        kw["model"] = MODEL

    # Authentication. The CLI has two routes and they bill to different places:
    #
    #   API key   - what this does by default. Uses ANTHROPIC_API_KEY from
    #               environ.env (the same key brain.py and Double already use) and
    #               bills Tyler's API credit per task. Works headlessly with no
    #               setup, which matters for a bot that must run unattended.
    #
    #   CLI login - `claude` run once interactively, signed in to his Claude
    #               subscription. Cheaper if he has a paid plan, but it needs a
    #               browser and an interactive terminal, so it cannot be done from
    #               here. Set pc.use_api_key false once he has logged in; passing
    #               the key would otherwise take precedence and keep billing the API.
    # Measured, not assumed. With a deliberately INVALID key in the environment the
    # CLI still authenticated and answered, so the stored OAuth login wins over the
    # key - the opposite of what the earlier comment here claimed. Which means
    # simply not passing the key does NOT select API billing, and simply passing it
    # does not select it either.
    #
    # So be explicit in both directions rather than relying on a precedence that
    # turned out to be backwards. Note that omitting `env` entirely does not clear
    # anything: the subprocess inherits bot.py's environment, which has already
    # loaded ANTHROPIC_API_KEY from environ.env.
    if _pc_cfg.get("use_api_key", True):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            kw["env"] = {"ANTHROPIC_API_KEY": key}
    else:
        kw["env"] = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    return ClaudeAgentOptions(**kw)


PERSONA_FILE = os.path.join(BASE_DIR, "persona.md")


def _persona():
    """The shared personality, so the Benham running commands is the same character
    as the one in DMs and voice. Appended to Claude Code's own preset prompt, which
    keeps its tool knowledge intact and only changes who is doing the talking."""
    try:
        with open(PERSONA_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return f"\n\n# Who you are\n\n{text}\n" if text else ""
    except OSError:
        return ""


_APPEND_PROMPT = """
You are running as Benham, reached through Discord rather than a terminal. Tyler is
on his phone, away from the PC, and is reading your output as chat messages.

- Keep answers short and phone-readable. No long code dumps unless he asks.
- To LOOK at things, use Read, Glob and Grep rather than shell commands. Those run
  without interrupting him; a shell command costs him an approval round-trip even
  when all it does is list a folder. Reach for the shell when you genuinely need to
  run something, not to inspect.
- You have his real machine and his real skills. Prefer using a skill over
  reinventing what it does.
- Every write, command, and subagent needs his approval, delivered by DM. Expect
  a pause; that is normal. If he denies something, do not look for another route
  to the same effect - tell him and stop.
- Your working directory is Discord-Claude. Put scratch files there rather than
  scattering them across his workspace.
"""


async def run_task(prompt, on_progress=None):
    """Run one task in a Claude Code session and return its final text.

    A fresh session per task, deliberately. A long-lived one would accumulate the
    context of every unrelated thing Tyler asked over days, and the Discord side
    already carries the conversational thread - this layer is for doing, not for
    remembering.
    """
    # ClaudeSDKClient rather than the simpler query() helper: can_use_tool only works
    # in streaming mode, and query() with a plain string prompt is not streaming.
    # The SDK rejects that combination outright rather than silently dropping the
    # permission callback, which is the right call - a permission gate that quietly
    # did nothing would be worse than no gate at all.
    from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient, ResultMessage,
                                  TextBlock, ToolUseBlock)

    if not ENABLED:
        return ("PC access is turned off. Set pc.enabled to true in control.json "
                "and restart me.")

    parts, tools_used = [], []
    async with ClaudeSDKClient(options=_options()) as session:
        await session.query(prompt)
        async for msg in session.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        parts.append(block.text.strip())
                        # The session's own narration between steps. Reported as
                        # progress too, because "what is it thinking" is most of
                        # what makes a long task bearable to wait through - the
                        # tool names alone read as activity without reason.
                        if on_progress:
                            await on_progress("text", block.text.strip())
                    elif isinstance(block, ToolUseBlock):
                        tools_used.append(block.name)
                        if on_progress:
                            await on_progress("tool", _progress_label(block))
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "is_error", False):
                    parts.append(f"(the session ended with an error: "
                                 f"{getattr(msg, 'result', 'unknown')})")
                cost = getattr(msg, "total_cost_usd", None)
                log(f"PC task finished: "
                    f"{('$%.4f' % cost) if cost else 'cost n/a'}, tools: {tools_used}")

    return "\n\n".join(parts).strip() or "(the session returned nothing)"
