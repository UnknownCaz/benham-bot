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
from datetime import datetime, timezone

from dotenv import load_dotenv

from benham.core import identity

from benham import paths
load_dotenv(os.path.join(paths.CONFIG_DIR, "environ.env"))

WORKDIR = os.path.join(os.path.dirname(paths.ROOT), "..", "Benhams-inbox")
WORKDIR = os.path.abspath(
    identity.CONTROL.get("pc", {}).get("workdir")
    or os.path.join(os.path.expanduser("~"), "Claude", "Benhams-inbox")
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

# --------------------------------------------------------------------------
# The bash command policy for spawned sessions - Tyler's answer, 2026-08-18,
# to "should these sessions be able to message Doom?": may, VISIBLY, through
# bounded shapes. Three classes, decided by _classify_bash:
#
#   denied     the raw send commands (dm / send / do dm_user / do send_message).
#              Tyler denied these in his workspace settings on 2026-08-18, but a
#              spawned session runs in Benhams-inbox - a different project root
#              that never loads that file - so the wall has to live HERE, where
#              every spawned session actually passes. Checked FIRST, greedily,
#              on the raw string: a send smuggled into a compound command is
#              still a send. A deny never asks - approval fatigue is how the
#              2026-08-15 chain got approved.
#
#   read_only  the CLI reads a session needs reflexively (rooms, room read,
#              conv list/show, status, ask --queue). These cost an approval
#              today for no security - the same commands are allowlisted for
#              keyboard sessions - and the rooms pointer tells every worker to
#              read its room, which must not bill Tyler a tap per spawn. The
#              match is STRICT: the whole command, no shell metacharacters, or
#              it falls through to ask. Unnecessary question beats unreviewed
#              write, same as the tool-class list below.
#
#   ask        everything else - unchanged, the existing DM approval flow.
#              outreach, room post, conv close all stay here on purpose: one
#              visible tap is the price of reaching a person or mutating state,
#              and the prompt carries the why.
# --------------------------------------------------------------------------

_DENIED_SENDS = re.compile(
    r"benham\.py\s+(?:dm|send)\b|benham\.py\s+do\s+(?:dm_user|send_message)\b")

# Any of these anywhere in a command disqualifies the read-only fast path:
# chaining, pipes, redirects, substitution, newlines. The command must BE the
# read, not merely contain one.
_SHELL_META = re.compile(r"[&|;<>`$\r\n]")

_READ_ONLY_CMD = re.compile(
    r"^\s*python\s+\S*benham\.py\s+"
    r"(?:rooms\b|room\s+read\s+\S+|conv\s+(?:list|show)\b|status\b|"
    r"ask\s+--queue\s*$)")


def _classify_bash(command):
    """'denied' | 'read_only' | 'ask' for one shell command. Pure, so the tests
    can hold the whole matrix against it without touching the ask machinery.

    Deny is greedy and first; allow is strict and last; everything unrecognised
    asks, which is the fail-safe this module already promised for tools.
    """
    cmd = str(command or "")
    if _DENIED_SENDS.search(cmd):
        return "denied"
    if _SHELL_META.search(cmd):
        return "ask"
    if _READ_ONLY_CMD.match(cmd):
        return "read_only"
    return "ask"


_DENY_SEND_MESSAGE = (
    "That command sends a Discord message with an unbounded recipient and "
    "unbounded words, and sessions never get it - Tyler denied the raw send "
    "paths on 2026-08-18. Do not look for another route to the same effect. "
    "The bounded paths exist and are the right tool: "
    "`benham.py outreach <collaborator> \"question\"` opens a tracked ask "
    "(Tyler approves it, delivery and nudges are handled for you), and "
    "`benham.py do tell_conversation id=<id> outcome=\"...\" tell=true` "
    "delivers an outcome on a conversation that already exists. If the task "
    "genuinely needs a raw DM, say so in your report and stop.")


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

# What the CURRENT task is doing, so an approval prompt can say WHY, not only
# WHAT. Tyler's requirement, 2026-08-16: "context must be included to clarify use
# case ... I prefer knowing exactly what I'm confirming before I confirm it."
#
# The 2026-08-15 Gmail run is the case for it. Eight prompts in two minutes, each
# individually plausible - including `Stop-Process -Name firefox -Force`, which
# reads fine until you know the session was brute-forcing its way to Gmail because
# it could not find a mail API. No single prompt could reveal that the CHAIN had
# gone wrong, because none of them said what the chain was for.
#
# Every field here is already produced by run_task; none of it is newly gathered.
# `asks` is the runaway detector: "request 8 for this task" is the signal no
# individual command can carry.
_task_ctx = {"task": None, "narration": None, "asks": 0, "last_why": None}

# READ-ONLY mode (stage 3 item 13). A triage session investigating someone else's
# bug report must cost ZERO approval prompts - otherwise a stranger's report can
# make Tyler's phone buzz, which turns a helpful feature into a way to pester him.
#
# So in this mode a non-read tool is DENIED OUTRIGHT rather than asked about. That
# is a stronger guarantee than "he will probably say no": there is no path from a
# report to a write, so the taint wall pc_task sits behind is not crossed - it is
# not needed, because nothing here can act.
_read_only = [False]
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


def _why_block():
    """The context under an approval prompt: why this step, and how many so far.

    Ordered why-then-what-for on purpose. The session's own last words are the most
    useful thing on the screen - they are the step being justified - and the task is
    the frame it has to fit inside. A step that reads sensibly but does not serve
    the task is exactly the failure this exists to make visible.

    The count is last and unadorned. It says nothing on ask 1 and everything on
    ask 8.
    """
    lines = []
    narration = " ".join((_task_ctx.get("narration") or "").split())[:400]
    if narration:
        # Only when it has CHANGED. A session can fire several tool calls without
        # narrating between them, and the first version of this repeated the same
        # 400 characters under four different commands - which is worse than
        # omitting it, because identical reasoning under changing commands reads as
        # a claim that they share a reason. Caught in Tyler's first real test.
        if narration == _task_ctx.get("last_why"):
            lines.append("**Why:** _same reasoning as the last ask - it has not "
                         "said anything new since._")
        else:
            lines.append(f"**Why:** {narration}")
            _task_ctx["last_why"] = narration
    task = (_task_ctx.get("task") or "").strip()
    if task:
        lines.append(f"**Task:** {' '.join(task.split())[:220]}")
    asks = int(_task_ctx.get("asks") or 0)
    if asks > 1:
        lines.append(f"_Request {asks} for this task._")
    return ("\n" + "\n".join(lines)) if lines else ""


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
    """The SDK permission callback. Free for reads, ask for everything else.

    In read-only mode, "everything else" is refused instead of asked. See
    _read_only above for why that distinction is the whole safety story of triage.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    if tool_name == "Read":
        path = str((tool_input or {}).get("file_path", ""))
        if _SECRET_RE.search(path):
            # Allowed on Tyler's explicit instruction; logged so it is never silent.
            log(f"SECRET-READ: session read a credential-shaped path: {path}")

    if tool_name in READ_ONLY_TOOLS:
        return PermissionResultAllow()

    if _read_only[0]:
        # Deny, and say why in a way the model can act on: it should report what it
        # could not check rather than looking for another route, which is the same
        # rule the prompt gives it about a human denial.
        log(f"READ-ONLY session refused {tool_name}")
        return PermissionResultDeny(
            message=(f"{tool_name} is not available - this is a read-only "
                     "investigation. Do not look for another way to do it. Read, "
                     "Glob and Grep are available; if something cannot be settled "
                     "without writing or running, say so and stop."))

    # The command policy, AFTER the triage wall above on purpose: a read-only
    # investigation keeps its Read/Glob/Grep-only contract, and the pointed
    # deny below is for full sessions, where the generic refusal would not say
    # which door to use instead.
    if tool_name == "Bash":
        cmd = str((tool_input or {}).get("command", ""))
        verdict = _classify_bash(cmd)
        if verdict == "denied":
            log(f"PC-DENIED-SEND (no ask): {cmd[:200]}")
            return PermissionResultDeny(message=_DENY_SEND_MESSAGE)
        if verdict == "read_only":
            log(f"PC-AUTO-ALLOW read-only CLI: {cmd[:200]}")
            return PermissionResultAllow()

    if _ask_owner is None:
        return PermissionResultDeny(message="No owner channel is available to approve this.")

    _seq[0] += 1
    rid = str(_seq[0])
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _pending[rid] = fut

    what = _describe(tool_name, tool_input)
    _task_ctx["asks"] += 1
    log(f"PC-PERMISSION-ASK [{rid}] {tool_name} (ask #{_task_ctx['asks']}): "
        f"{str(tool_input)[:300]}")
    try:
        # The request id rides along so the prompt can carry Approve/Deny buttons
        # that resolve THIS request. The buttons and the typed reply both land in
        # answer(), which is idempotent - first decision wins, the rest no-op.
        await _ask_owner(
            # No bold around {what}: _describe already bolds the tool name and may
            # return a fenced code block, and wrapping a fence in ** leaves a stray
            # pair of asterisks hanging off the end on mobile.
            f"Benham wants to {what}\n"
            f"{_why_block()}\n"
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

def _options(resume=None):
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
    # The rooms wake mechanism (INTENT 20.6): resume takes the session id a
    # prior run handed back on ResultMessage, and the session comes back with
    # its context intact. fork_session is deliberately NOT set - one id per
    # room worker means the transcript IS the thread, and the id is that
    # worker's identity across runs.
    if resume:
        kw["resume"] = str(resume)

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


PERSONA_FILE = os.path.join(paths.PROMPTS_DIR, "persona.md")


def _persona():
    """The shared personality, so the Benham running commands is the same character
    as the one in DMs. Appended to Claude Code's own preset prompt, which
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
- Your working directory is Benhams-inbox. Put scratch files there rather than
  scattering them across his workspace.

## This machine (so you do not spend his approvals finding out)

Every approval is a phone notification and a wait. Guessing at the environment is
the most expensive way to learn it, and on 2026-08-16 a single "run the test
suite" cost him SIX approvals - four of them probing for a Python. So:

- `python` is the right interpreter. It is Python 3.12 and it has the installed
  packages (discord.py, anthropic, requests).
- `py -3` and `py -3.x` resolve to a Windows Store Python with NOTHING installed.
  Never reach for them; a ModuleNotFoundError from one means the launcher, not a
  missing dependency.
- **pytest is not installed.** benham-bot's tests are standalone scripts.
  `python run_tests.py` runs the whole suite with one command and one exit code
  (one approval instead of a loop's worth); `python run_tests.py policy` narrows
  to matching files, and `python scripts/gen_readme.py --check` verifies the
  README's generated blocks.
- If you do not know something about this machine, READ for it (Read/Glob/Grep are
  free) or say you do not know. Do not run a command to find out - that is the one
  case where the shell costs him something and returns nothing he asked for.
- Plan the whole shell sequence before the first one. Several commands in a row,
  each discovered by the last, is the pattern that turns one task into six
  notifications.
"""


async def run_task(prompt, on_progress=None, read_only=False, resume=None):
    """Run one task in a Claude Code session and return FACTS, not only prose.

    `resume` continues an earlier session by id (rooms hands the worker id in);
    None is a fresh session per task, which stays the right default - the
    docstring warning about accumulating unrelated context was written for
    exactly that, and rooms bounds it with the handoff rule instead.

    A fresh session per task, deliberately. A long-lived one would accumulate the
    context of every unrelated thing Tyler asked over days, and the Discord side
    already carries the conversational thread - this layer is for doing, not for
    remembering.

    Returns a dict rather than a string, because a string was the mechanism of
    INTENT §7 Bug 2's surviving half: the result arrived as narration, so nothing
    structured ever said "this fired, here is its id", and Benham told Tyler "I
    can't independently verify it, I'm relying on the session's own self-report"
    while the send sat in its own action log. The keys:

      text        what the session said, joined - the half callers relay
      session_id  the Claude Code session id off ResultMessage. run_task used to
                  read is_error and total_cost_usd from that object and throw
                  this away; it is the handle ClaudeAgentOptions.resume takes,
                  so keeping it is also step one of the rooms wake design
                  (INTENT item 20.6)
      cost_usd    what the task cost, or None when the SDK does not say
      is_error    the session ended in an error
      tools_used  tool names in the order the session used them
      asks        how many approval prompts the task sent Tyler - the runaway
                  detector, on the record instead of only in the prompt footer
      started/ended  UTC ISO bounds of the run; the window callers hold against
                  the action log to say what verifiably happened during it
    """
    # ClaudeSDKClient rather than the simpler query() helper: can_use_tool only works
    # in streaming mode, and query() with a plain string prompt is not streaming.
    # The SDK rejects that combination outright rather than silently dropping the
    # permission callback, which is the right call - a permission gate that quietly
    # did nothing would be worse than no gate at all.
    from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient, ResultMessage,
                                  TextBlock, ToolUseBlock)

    def _facts(text, session_id=None, cost=None, is_error=False, tools=(),
               started=None, ended=None):
        return {"text": text, "session_id": session_id, "cost_usd": cost,
                "is_error": bool(is_error), "tools_used": list(tools),
                "asks": int(_task_ctx.get("asks") or 0),
                "started": started, "ended": ended}

    if not ENABLED:
        return _facts("PC access is turned off. Set pc.enabled to true in "
                      "control.json and restart me.")

    parts, tools_used = [], []
    session_id, cost, is_error = None, None, False
    started = datetime.now(timezone.utc).isoformat()
    _task_ctx.update(task=str(prompt), narration=None, asks=0, last_why=None)
    _read_only[0] = bool(read_only)
    async with ClaudeSDKClient(options=_options(resume=resume)) as session:
        await session.query(prompt)
        async for msg in session.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        parts.append(block.text.strip())
                        # Kept for the NEXT approval prompt: the session explaining
                        # itself, in its own words, immediately before it asks to do
                        # something. That is the "why", and it costs nothing - it is
                        # already on the wire.
                        _task_ctx["narration"] = block.text.strip()
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
                is_error = bool(getattr(msg, "is_error", False))
                if is_error:
                    parts.append(f"(the session ended with an error: "
                                 f"{getattr(msg, 'result', 'unknown')})")
                cost = getattr(msg, "total_cost_usd", None)
                # The same object run_task always read is_error and cost from.
                # The id was being thrown away; it is the resume handle.
                session_id = getattr(msg, "session_id", None)
                log(f"PC task finished: "
                    f"{('$%.4f' % cost) if cost else 'cost n/a'}, "
                    f"session {session_id or '?'}, tools: {tools_used}")

    return _facts("\n\n".join(parts).strip() or "(the session returned nothing)",
                  session_id=session_id, cost=cost, is_error=is_error,
                  tools=tools_used, started=started,
                  ended=datetime.now(timezone.utc).isoformat())
