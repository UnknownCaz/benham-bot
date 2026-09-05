"""
cliwords.py - the words-and-exit-code harness behind test_cli_words.py.

THE WORDS ARE THE CONTRACT. RAVEN.md, discord-proxy, discord-outreach and
Tyler's global CLAUDE.md quote `benham.py` output and exit codes, and Raven's
permission allowlist is literal - a verb that prints one line differently
breaks an unattended lane silently. Phase B rewrote every verb's BODY (the PC
CLI became a client of the Mac bot) and this harness is what let that happen:
every verb's output was captured BEFORE the rewrite into
tests/fixtures/cli-words/, and the suite compares against those captures after.

How it works, so the next rewrite can reuse it:

  * A scratch CLONE of the repo (benham/, benham.py, prompts/, the example
    control.json switched to the same fixture _testconfig uses) with SEEDED
    stores - a few conversations, a thread, an idea, a filing, two rooms,
    channels.json, a bot log - written by the code's own store functions so
    the shape can never drift from what the bot writes.
  * A FAKE BOT: a thread that answers the outbox the way bot.py's poller does
    (sent/ + _result.json), deterministically. No Discord, no token.
  * Each case is `python benham.py <argv> --face benham` run as a real
    subprocess in the clone, stdout/stderr/exit captured and NORMALISED
    (paths, request names, timestamps, pids) so only the words remain.

Two modes. LOCAL is how the pre-Phase-B CLI ran: the clone IS the bot's tree.
REMOTE is Phase B: a second clone plays the Mac (the server module runs in
this process against it, fake bot answering its outbox) and the client clone
carries config/remote.json + a token pointing at it. The same cases run in
both, and the fixtures were captured in LOCAL against the pre-rewrite tree.

    python tests/cliwords.py --capture            # (re)write every fixture
    python tests/cliwords.py --capture --only ask  # just the cases whose name has "ask"
    python tests/test_cli_words.py                # the comparison the suite runs

Re-pinning a fixture is a deliberate act: it means the words changed and
somebody decided that was fine. Say so in the commit.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "cli-words")

# The same invented ids _testconfig uses; they belong to nobody.
OWNER_ID = 273967061619965952
GUEST_ID = 555000555000555000
GUEST_ID_2 = 555000555000555001
TESTING_GUILD = 736988645562646619
ASD_CHANNEL = 809357286036078612

# Files/dirs of the repo that a clone needs. Deliberately NOT state/, logs/ or
# config/ (the clone gets fixture versions of those) and never .git.
_COPY = ("benham", "benham.py", "prompts", "requirements.txt")


def make_clone(tag):
    """A scratch copy of the repo at <tmp>/cliwords-<tag>-<rand>/. The name
    carries 'cliwords' so normalise() can find and replace it."""
    # realpath: on macOS the temp dir is /var/... and os.getcwd() inside the
    # clone says /private/var/..., so a path the server prints would miss the
    # <ROOT> replacement - the exact symlink test_attachments met (1f06f4a).
    base = os.path.realpath(tempfile.mkdtemp(prefix=f"cliwords-{tag}-"))
    for name in _COPY:
        src = os.path.join(ROOT, name)
        dst = os.path.join(base, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.jsonl", "*.log"))
        else:
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(base, "config"), exist_ok=True)
    os.makedirs(os.path.join(base, "state"), exist_ok=True)
    os.makedirs(os.path.join(base, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "config", "control.json.example"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["guest"] = {**cfg.get("guest", {}), "enabled": True,
                    "ids": {"doom": GUEST_ID, "draco": GUEST_ID_2}}
    with open(os.path.join(base, "config", "control.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return base


# The seed runs INSIDE the clone with the clone's own code, so the record
# shapes are whatever that code writes. It is a script on purpose: the harness
# process must not import benham (its stores would resolve to this checkout).
_SEED = r'''
import json, os, sys
os.environ["BENHAM_FACE"] = "benham"
sys.path.insert(0, os.getcwd())
from benham import paths
from benham.core import conversations as C, initiative, ideas, rooms
OWNER, GUEST = %(owner)d, %(guest)d
# conversations: c1 open (Tyler), c2 answered but uncollected (Tyler),
# c3 open outreach to the guest, c4 closed, c5 owed (guest -> us)
c1 = C.open_conversation(OWNER, purpose="deploy gate", question="ready to deploy?",
                         priority="normal", placement_reason="gates the merge",
                         origin="session cwd=X pid=1")
c2 = C.open_conversation(OWNER, purpose="db", question="sqlite or json?",
                         priority="whenever", origin="session cwd=X pid=2")
C.answer(c2["id"], "sqlite")
c3 = C.open_conversation(GUEST, purpose="image test", question="does the image thing work now?",
                         project="storyizier", direction=C.ASKING, origin="session cwd=X pid=3")
c4 = C.open_conversation(OWNER, purpose="old", question="an old one?", origin="session cwd=X pid=4")
C.close(c4["id"], "fixed in a1b2c3")
# a thread and a silent run for the initiative lane
initiative.add_thread("did the tv-cast fix hold", why="he mentioned it twice", source="board")
initiative.record_run(initiative.R_SILENT, "boards quiet, nothing worth asking", read=["Benham board"])
# one idea, unswept
ideas.file_idea(GUEST, "doom", "a dark mode for the wallboard")
# one filing, already told (no url -> loopclose never calls gh for it)
os.makedirs(paths.STATE_DIR, exist_ok=True)
with open(os.path.join(paths.STATE_DIR, "guest_issues.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": "2026-08-20T10:00:00+00:00", "day": "2026-08-20",
                        "author_id": GUEST, "author": "doom", "category": "bug",
                        "title": "zoom skips a frame", "quote": "zoom skips a frame",
                        "url": "", "project": None, "told": "fixed"}) + "\n")
# rooms: scratch + one project room with two lines
rooms.ensure(rooms.SCRATCH, "default room - pc.. tasks land and resume here", "system")
rooms.create("build", "the phase-b build thread", "tyler")
rooms.post("build", "tyler", "schema changed; regen before testing")
rooms.post("build", "code-session", "regen done")
# channels.json, guest usage/memory, a bot log, an inbox
with open(os.path.join(paths.STATE_DIR, "channels.json"), "w", encoding="utf-8") as f:
    json.dump([{"guild": "Testing Server", "guild_id": %(guild)d,
                "text_channels": [{"id": %(asd)d, "name": "asd"}],
                "voice_channels": []}], f)
with open(os.path.join(paths.STATE_DIR, "guest_usage.json"), "w", encoding="utf-8") as f:
    json.dump({"date": "2026-01-01", "global": 3, "users": {str(GUEST): 3}}, f)
with open(os.path.join(paths.STATE_DIR, "guest_memory.json"), "w", encoding="utf-8") as f:
    json.dump({"dm:%%d" %% GUEST: []}, f)
with open(os.path.join(paths.ROOT, "logs", "bot.log"), "w", encoding="utf-8") as f:
    f.write("[2026-09-01 10:00:00Z] Logged in as Benham#2721 (id 1) - face: benham\n")
    f.write("[2026-09-01 10:00:01Z] Synced 7 command(s) to Testing Server\n")
    f.write("[2026-09-01 10:05:00Z] agent usage [dm:1] in=1200 out=80 cache_read=7000 cache_write=0 model=claude-sonnet-5\n")
    f.write("[2026-09-01 10:06:00Z] action send_message by 1: {}\n")
    f.write("[2026-09-01 10:07:00Z] DENIED purge_messages by 2 [rule=owner] no\n")
with open(os.path.join(paths.STATE_DIR, "inbox.jsonl"), "w", encoding="utf-8") as f:
    for i, (ch, who, txt) in enumerate([
            ("asd", "doom", "hello there"),
            ("Direct Message with caz6666", "caz6666", "pc.. what's in downloads"),
            ("Direct Message with doom", "doom", "does it work now"),
            ("asd", "Benham#2721", "sure")]):
        f.write(json.dumps({"ts": "2026-09-01T10:%%02d:00+00:00" %% i, "guild": None if "Direct" in ch else "Testing Server",
                            "guild_id": None if "Direct" in ch else %(guild)d, "channel": ch,
                            "channel_id": 10 + i, "author": who, "author_id": 1 + i,
                            "is_self": who.startswith("Benham"), "content": txt,
                            "message_id": 100 + i}) + "\n")
print("seeded")
'''


def seed(clone):
    script = _SEED % {"owner": OWNER_ID, "guest": GUEST_ID, "guild": TESTING_GUILD,
                      "asd": ASD_CHANNEL}
    proc = subprocess.run([sys.executable, "-c", script], cwd=clone, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=_env(), timeout=120)
    if proc.returncode != 0 or "seeded" not in proc.stdout:
        raise RuntimeError(f"seed failed in {clone}:\n{proc.stdout}\n{proc.stderr}")


# --------------------------------------------------------------------------
# The fake bot: answers the outbox like bot.py's poller, deterministically.
# --------------------------------------------------------------------------

_NEEDS_TOKEN = {"delete_message", "purge_messages", "purge_guild", "delete_channel",
                "delete_role", "kick_member", "ban_member", "delete_emoji",
                "guest_off"}   # always_confirm, tier 2 - same two-step shape
FAKE_TOKEN = "abc123"
REFUSED_CHANNEL = 404  # a channel id that "Discord refuses", for the failed/ path


def _answer(req):
    """(dest, result) for one request - the poller's outcomes, in miniature."""
    action = req.get("action", "send")
    base = {"processed_at": "2026-09-05T00:00:00+00:00", "request": req}
    if req.get("channel_id") == REFUSED_CHANNEL or req.get("user_id") == REFUSED_CHANNEL:
        return "failed", {**base, "status": "failed", "action": action,
                          "error": "Forbidden: 403 Forbidden (error code: 50007): "
                                   "Cannot send messages to this user"}
    if action in ("send", "dm"):
        return "sent", {**base, "status": "sent", "message_id": 123456789, "channel": "#asd"}
    if action == "history":
        return "sent", {**base, "status": "fetched", "channel": "#asd", "messages": [
            {"ts": "2026-09-01T10:00:00+00:00", "author": "doom", "author_id": 1,
             "content": "hello there", "message_id": 100},
            {"ts": "2026-09-01T10:01:00+00:00", "author": "Benham#2721", "author_id": 2,
             "content": "sure", "message_id": 101}]}
    if action in ("listen", "stop_listen", "speak"):
        return "failed", {**base, "status": "failed",
                          "error": f"'{action}' was removed with voice on 2026-08-16"}
    if action in ("purge", "delete"):
        return "failed", {**base, "status": "failed",
                          "error": f"'{action}' was retired on 2026-08-26 - it bypassed the tier-3 gates."}
    if action == "nope_not_real":
        return "failed", {**base, "status": "failed", "action": action,
                          "error": "KeyError: 'channel_id'"}
    if action in _NEEDS_TOKEN and not req.get("confirm_token"):
        return "sent", {**base, "status": "confirmation_required", "action": action,
                        "confirm_token": FAKE_TOKEN,
                        "preview": {"summary": f"{action}: 1 message in #asd would go",
                                    "detail": "by doom, 2026-09-01"},
                        "expires_in_seconds": 3600}
    if action in _NEEDS_TOKEN:
        if req.get("confirm_token") != FAKE_TOKEN:
            return "failed", {**base, "status": "failed", "action": action,
                              "error": f"ValueError: confirm_token {req.get('confirm_token')!r} "
                                       "is unknown or expired. Expiry means cancelled"}
        return "sent", {**base, "status": "ok", "action": action, "result": {"deleted": 1}}
    if action == "restart":
        return "sent", {**base, "status": "ok", "action": action,
                        "result": {"status": "restarting", "pid": 4242, "in_seconds": 2}}
    # every other registry capability
    return "sent", {**base, "status": "ok", "action": action,
                    "result": {"status": "sent", "message_id": 123456789}}


class FakeBot(threading.Thread):
    """Polls <state>/outbox every 0.2s and archives each request with a result."""

    def __init__(self, state_dir):
        super().__init__(name="fake-bot", daemon=True)
        self.outbox = os.path.join(state_dir, "outbox")
        self.stop = threading.Event()
        self.seen = []

    def run(self):
        while not self.stop.is_set():
            try:
                names = sorted(f for f in os.listdir(self.outbox) if f.endswith(".json"))
            except FileNotFoundError:
                names = []
            for fname in names:
                path = os.path.join(self.outbox, fname)
                try:
                    with open(path, encoding="utf-8") as f:
                        req = json.load(f)
                except (OSError, ValueError):
                    continue
                dest, result = _answer(req)
                folder = os.path.join(self.outbox, dest)
                os.makedirs(folder, exist_ok=True)
                base = os.path.splitext(fname)[0]
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                os.replace(path, os.path.join(folder, f"{base}_{stamp}.json"))
                with open(os.path.join(folder, f"{base}_{stamp}_result.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
                self.seen.append(req)
            self.stop.wait(0.2)


# --------------------------------------------------------------------------
# Running a case
# --------------------------------------------------------------------------

def _env(extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_CODE_", "BENHAM_"))}
    env["PYTHONIOENCODING"] = "utf-8"
    # argparse wraps help to the terminal width; pin it so the same words come
    # out on Windows and macOS (the fixtures were captured at 80).
    env["COLUMNS"] = "80"
    if extra:
        env.update(extra)
    return env


def run_case(clone, argv, face=True, env=None, timeout=120):
    cmd = [sys.executable, "benham.py"] + list(argv) + (["--face", "benham"] if face else [])
    proc = subprocess.run(cmd, cwd=clone, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=_env(env),
                          timeout=timeout)
    return {"argv": list(argv), "face": face, "exit": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


_RULES = [
    (re.compile(r"\d{8}_\d{6}_[0-9a-f]{8}(?:_\d{8}_\d{6})?"), "<REQ>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00)?"), "<TS>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
    (re.compile(r"\b\d{2}:\d{2}Z"), "<HH:MM>Z"),
    (re.compile(r"\bpid[ =]\d+"), "pid <PID>"),
    (re.compile(r"cwd=\S+"), "cwd=<CWD>"),
    (re.compile(r"expires in \d+s"), "expires in <N>s"),
    (re.compile(r"RAM [\d.]+ MB \| CPU [\d.]+s \| \d+ threads \| up [^|\n]*"), "RAM <PROC>"),
    (re.compile(r"uptime[ =:]+\d+s?"), "uptime <N>"),
    (re.compile(r"\(\d+ lines\)"), "(<N> lines)"),
    (re.compile(r"local_[0-9a-f-]{36}"), "<SESSION>"),
]


def normalise(text, roots):
    for root in roots:
        for form in (root, root.replace("\\", "/"), root.replace("\\", "\\\\")):
            text = text.replace(form, "<ROOT>")
    # Separators after the root are the platform's, not the words': the Mac
    # prints forward slashes where the PC printed backslashes.
    text = re.sub(r"<ROOT>[\\/]+([^\s\"'`]*)",
                  lambda m: "<ROOT>/" + m.group(1).replace("\\", "/"), text)
    for rx, sub in _RULES:
        text = rx.sub(sub, text)
    # argparse wraps its `usage:` block differently across Python versions
    # (3.12 on the PC, 3.14 on the Mac); the WORDS are the contract, the
    # wrap is not. Continuation lines of a usage block fold into one line.
    text = _USAGE_BLOCK.sub(lambda m: re.sub(r"\s+", " ", m.group(0)).rstrip() + "\n", text)
    return text


_USAGE_BLOCK = re.compile(r"^usage: [^\n]*(?:\n[ \t]+[^\n]*)*\n", re.M)


def normalised(case, roots):
    return {**case, "stdout": normalise(case["stdout"], roots),
            "stderr": normalise(case["stderr"], roots)}


# --------------------------------------------------------------------------
# The cases. Name -> argv. Order matters where a case mutates the seed
# (conv close, ideas --sweep, room create ...): each name is run in a FRESH
# clone unless it carries a "chain" - the harness reseeds per case otherwise,
# because a fixture that depends on what an earlier case did is a fixture
# nobody can re-run alone.
# --------------------------------------------------------------------------

G = str(GUEST_ID)
CASES = {
    # --- the entry point itself
    "help": ["--help"],
    "no-face": {"argv": ["status"], "face": False},
    "unknown-command": ["frobnicate"],
    # --- send / dm / draft / fetch (outbox transport)
    "send-usage": ["send"],
    "send-bad-id": ["send", "x", "hi"],
    "send-nowait": ["send", str(ASD_CHANNEL), "hello", "there", "--no-wait"],
    "send-ok": ["send", str(ASD_CHANNEL), "hello there"],
    "send-refused": ["send", str(REFUSED_CHANNEL), "hello"],
    "dm-usage": ["dm"],
    "dm-empty": ["dm", "--tyler", "   "],
    "dm-guest-question": ["dm", G, "does it work?"],
    "dm-guest-untracked": ["dm", G, "does it work?", "--untracked"],
    "dm-tyler": ["dm", "--tyler", "phase b test"],
    "dm-tyler-nowait": ["dm", "--tyler", "phase b test", "--no-wait"],
    "dm-refused": ["dm", str(REFUSED_CHANNEL), "hello"],
    "draft-usage": ["draft"],
    "draft-ok": ["draft", str(ASD_CHANNEL), "yo, sounds good"],
    "draft-unknown-channel": ["draft", "12345", "yo"],
    "fetch-usage": ["fetch"],
    "fetch-bad-limit": ["fetch", str(ASD_CHANNEL), "x"],
    "fetch-ok": ["fetch", str(ASD_CHANNEL), "5"],
    "fetch-nowait": ["fetch", str(ASD_CHANNEL), "--no-wait"],
    # --- delete / purge (tier 3 two-step)
    "delete-usage": ["delete"],
    "delete-preview": ["delete", str(ASD_CHANNEL), "100"],
    "delete-fire": ["delete", str(ASD_CHANNEL), "100", "--confirm-token", FAKE_TOKEN],
    "delete-bad-token": ["delete", str(ASD_CHANNEL), "100", "--confirm-token", "zzz"],
    "delete-nowait": ["delete", str(ASD_CHANNEL), "100", "--no-wait"],
    "purge-usage": ["purge"],
    "purge-preview": ["purge", str(ASD_CHANNEL), "--days", "3"],
    "purge-fire": ["purge", str(ASD_CHANNEL), "--days", "3", "--confirm-token", FAKE_TOKEN],
    "purge-scope-guild": ["purge", str(ASD_CHANNEL), "--scope", "guild"],
    "purge-guild-preview": ["purge", "--guild", str(TESTING_GUILD), "--limit", "5"],
    "purge-negative": ["purge", str(ASD_CHANNEL), "--days", "-1"],
    "purge-unknown-flag": ["purge", str(ASD_CHANNEL), "--bogus"],
    # --- do (the registry from the shell)
    "do-usage": ["do"],
    "do-list": ["do", "list"],
    "do-list-tier": ["do", "list", "--tier", "destructive"],
    "do-list-bad-tier": ["do", "list", "--tier", "bogus"],
    "do-help": ["do", "help", "send_message"],
    "do-help-unknown": ["do", "help", "bogus"],
    "do-unknown": ["do", "bogus"],
    "do-bad-kv": ["do", "send_message", "channel_id"],
    "do-validation": ["do", "send_message", "content=hi"],
    "do-send": ["do", "send_message", f"channel_id={ASD_CHANNEL}", "content=hi"],
    "do-preview": ["do", "delete_message", f"channel_id={ASD_CHANNEL}", "message_id=100"],
    "do-fire": ["do", "delete_message", f"channel_id={ASD_CHANNEL}", "message_id=100",
                f"confirm_token={FAKE_TOKEN}"],
    "do-refused": ["do", "dm_user", f"user_id={REFUSED_CHANNEL}", "content=hi"],
    # --- ask / conv / outreach (store transport)
    "ask-queue": ["ask", "--queue"],
    "ask-no-question": ["ask"],
    "ask-bad-cap": ["ask", "q?", "--nudge-cap", "9"],
    "ask-nowait": ["ask", "which db?", "--no-wait", "--priority", "whenever",
                   "--why", "no rush", "--project", "benham"],
    "ask-timeout": ["ask", "which db?", "--timeout", "1", "--purpose", "db choice"],
    "conv-help": ["conv"],
    "conv-list": ["conv", "list"],
    "conv-list-all": ["conv", "list", "--all"],
    "conv-show": ["conv", "show", "c2"],
    "conv-show-missing": ["conv", "show", "c99"],
    "conv-close": ["conv", "close", "c1", "fixed in a1b2c3"],
    "conv-close-tell": ["conv", "close", "c3", "shipped", "--tell", "--note", "sorry for the wait"],
    "conv-close-missing": ["conv", "close", "c99", "x"],
    "conv-close-twice": {"chain": [["conv", "close", "c1", "done"], ["conv", "close", "c1", "again"]]},
    "conv-bank": ["conv", "bank", "c1", "not worth chasing"],
    "outreach-usage": ["outreach"],
    "outreach-unknown": ["outreach", "nobody", "hi?"],
    "outreach-owner": ["outreach", str(OWNER_ID), "hi?"],
    "outreach-list": ["outreach", "doom", "--list"],
    "outreach-ok": ["outreach", "draco", "does the image thing work now?", "--project", "storyizier"],
    "outreach-no-question": ["outreach", "draco"],
    # --- initiate
    "initiate-help": ["initiate"],
    "initiate-status": ["initiate", "status"],
    "initiate-status-json": ["initiate", "status", "--json"],
    "initiate-threads": ["initiate", "threads"],
    "initiate-threads-all": ["initiate", "threads", "--all", "--json"],
    "initiate-note": ["initiate", "note", "the wallboard tile", "--why", "he asked twice", "--source", "board"],
    "initiate-note-nowhy": ["initiate", "note", "a bare thread"],
    "initiate-drop": ["initiate", "drop", "t1", "resolved itself"],
    "initiate-close": ["initiate", "close", "t1", "he answered"],
    "initiate-drop-missing": ["initiate", "drop", "t9", "x"],
    "initiate-ask-dry": ["initiate", "ask", "Did the tv-cast fix hold up?", "--dry-run"],
    "initiate-ask-blocked": ["initiate", "ask", "You MUST answer this now!!!", "--why", "test"],
    "initiate-ask-send": ["initiate", "ask", "Did the tv-cast fix hold up?", "--thread", "t1", "--why", "two mentions"],
    "initiate-silent": ["initiate", "silent", "boards quiet", "--read", "Benham board"],
    "initiate-sweep": ["initiate", "sweep"],
    "initiate-reset": ["initiate", "reset"],
    "initiate-log": ["initiate", "log"],
    # --- ideas / issues
    "ideas": ["ideas"],
    "ideas-all": ["ideas", "--all"],
    "ideas-sweep": ["ideas", "--sweep"],
    "ideas-help": ["ideas", "--help"],
    "issues": ["issues"],
    "issues-loop-dry": ["issues", "loop", "--dry-run"],
    "issues-retry": ["issues", "retry"],
    "issues-unknown": ["issues", "bogus"],
    # --- rooms / room
    "rooms": ["rooms"],
    "rooms-as": ["rooms", "--as", "my-session"],
    "room-usage": ["room"],
    "room-read": ["room", "read", "build"],
    "room-read-again": {"chain": [["room", "read", "build"], ["room", "read", "build", "--limit", "1"]]},
    "room-read-missing": ["room", "read", "ghost"],
    "room-post": ["room", "post", "build", "regen done twice"],
    "room-post-nowait": ["room", "post", "build", "note", "--no-wait"],
    "room-post-missing": ["room", "post", "ghost", "hi"],
    "room-create": ["room", "create", "storyizier", "Doom's story bot work"],
    "room-create-bad": ["room", "create", "Bad Name", "x"],
    "room-create-dup": ["room", "create", "build", "x"],
    # --- guest / status / usage / webhook
    "guest-status": ["guest", "status"],
    "guest-forget": ["guest", "forget", G],
    "guest-forget-all": ["guest", "forget-all"],
    "guest-bogus": ["guest", "bogus"],
    "status": ["status"],
    "usage": ["usage"],
    "usage-today": ["usage", "--today"],
    "usage-all": ["usage", "--all"],
    "usage-missing-log": ["usage", "--log", "nope.log"],
    "webhook-list": ["webhook", "--list"],
    "webhook-nothing": ["webhook"],
    # --- Phase B verbs (pinned AFTER the build - these words are new)
    "inbox": ["inbox"],
    "inbox-dms": ["inbox", "--dms", "--limit", "2"],
    "inbox-bad-limit": ["inbox", "--limit", "0"],
    "restart-nowait": ["restart", "--no-wait"],
    "guest-off-preview": ["guest", "off"],
    "guest-off-fire": ["guest", "off", "--confirm-token", FAKE_TOKEN],
    "guest-off-nowait": ["guest", "off", "--no-wait"],
    # --- the invisible readers
    "catchup-usage": ["catchup"],
    "catchup-bad-id": ["catchup", "x"],
    "read-history-usage": ["read_history", "x"],
}


def case_spec(name):
    spec = CASES[name]
    if isinstance(spec, list):
        return {"chain": [spec], "face": True}
    if "chain" in spec:
        return {"chain": spec["chain"], "face": spec.get("face", True)}
    return {"chain": [spec["argv"]], "face": spec.get("face", True)}


class Harness:
    """LOCAL mode: one seeded clone that IS the bot's tree, a fake bot over it."""

    def __init__(self):
        self.client = make_clone("client")
        seed(self.client)
        self.roots = [self.client]
        self.env = {}
        self.bot = FakeBot(os.path.join(self.client, "state"))
        self.bot.start()

    def run(self, name):
        spec = case_spec(name)
        out = None
        for argv in spec["chain"]:
            out = run_case(self.client, argv, face=spec["face"], env=self.env)
        out["argv"] = spec["chain"][-1] if len(spec["chain"]) == 1 else spec["chain"]
        return normalised(out, self.roots)

    def close(self):
        self.bot.stop.set()
        for d in self.roots:
            shutil.rmtree(d, ignore_errors=True)


def run_all(names, remote=False, progress=None):
    """Each case against a FRESH seed, so no case leans on another. Local
    mode builds a harness per case; remote mode keeps one rig and reseeds."""
    results = {}
    rig = RemoteRig() if remote else None
    try:
        for name in names:
            if rig:
                results[name] = rig.run(name)
            else:
                h = Harness()
                try:
                    results[name] = h.run(name)
                finally:
                    h.close()
            if progress:
                progress(name, results[name])
    finally:
        if rig:
            rig.close()
    return results


# --------------------------------------------------------------------------
# REMOTE mode: server.py as a real subprocess INSIDE the Mac clone - the
# clone's own code, the clone's own paths, exactly the Mac's shape. One rig
# for the whole run (the server holds import-time store paths, so it must
# outlive the clone); the stores are wiped and reseeded per case.
# --------------------------------------------------------------------------

_SERVER = r'''
import asyncio, os, sys, threading, time
sys.path.insert(0, os.getcwd())
from benham.core import server


class C:
    def is_ready(self):
        return True

    def is_closed(self):
        return False


loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
srv = server.start("127.0.0.1", 0, C(), "benham", lambda m: None,
                   loop=loop, host_name="fake-mac", retries=1)
print("PORT", srv.server_address[1], flush=True)
while True:
    time.sleep(3600)
'''


class RemoteRig:
    """A Mac clone with the real server over it, plus one client clone."""

    def __init__(self):
        self.mac = make_clone("mac")
        seed(self.mac)
        self.client = make_clone("client")
        seed(self.client)
        self.roots = [self.client, self.mac]
        self.token_file = os.path.join(tempfile.mkdtemp(prefix="cliwords-token-"),
                                       "benham-bot.token")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _SERVER], cwd=self.mac, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env=_env({"BENHAM_FACE": "benham", "BENHAM_API_TOKEN_FILE": self.token_file}))
        port = None
        for _ in range(200):
            line = self.proc.stdout.readline()
            if not line:
                break
            if line.startswith("PORT "):
                port = int(line.split()[1])
                break
        if port is None:
            raise RuntimeError("the fake Mac server did not report a port")
        self.env = {"BENHAM_REMOTE_URL": f"http://127.0.0.1:{port}",
                    "BENHAM_REMOTE_TOKEN_FILE": self.token_file,
                    "BENHAM_REMOTE_HOST": "fake-mac"}
        self.bot = FakeBot(os.path.join(self.mac, "state"))
        self.bot.start()

    def reseed(self):
        for d in ("state", "logs"):
            shutil.rmtree(os.path.join(self.mac, d), ignore_errors=True)
            os.makedirs(os.path.join(self.mac, d), exist_ok=True)
        seed(self.mac)

    def run(self, name):
        self.reseed()
        spec = case_spec(name)
        out = None
        for argv in spec["chain"]:
            out = run_case(self.client, argv, face=spec["face"], env=self.env)
        out["argv"] = spec["chain"][-1] if len(spec["chain"]) == 1 else spec["chain"]
        return normalised(out, self.roots)

    def close(self):
        self.bot.stop.set()
        self.proc.kill()
        for d in self.roots:
            shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# Fixtures. <name>.json is the contract in BOTH modes; a <name>.<mode>.json
# beside it overrides for one mode only - `status` is the case: with no bot
# host at all it says NOT running (exit 1), through the wire it says RUNNING.
# --------------------------------------------------------------------------

def fixture_path(name, mode=None):
    if mode:
        p = os.path.join(FIXTURES, f"{name}.{mode}.json")
        if os.path.exists(p):
            return p
    return os.path.join(FIXTURES, name + ".json")


def load_fixture(name, mode=None):
    with open(fixture_path(name, mode), encoding="utf-8") as f:
        return json.load(f)


def save_fixture(name, case, mode=None):
    os.makedirs(FIXTURES, exist_ok=True)
    path = os.path.join(FIXTURES, f"{name}.{mode}.json" if mode else name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(case, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(argv):
    if "--capture" not in argv:
        print(__doc__)
        return 2
    only = argv[argv.index("--only") + 1] if "--only" in argv else ""
    # --remote captures a per-mode override (<name>.remote.json); the plain
    # capture is the contract for both modes.
    mode = "remote" if "--remote" in argv else None
    names = [n for n in CASES if only in n]
    t0 = time.time()

    def progress(name, case):
        save_fixture(name, case, mode)
        print(f"  captured {name:<24} exit {case['exit']}  ({time.time() - t0:.0f}s)")

    run_all(names, remote=bool(mode), progress=progress)
    print(f"{len(names)} fixture(s) written to {FIXTURES}" + (f" ({mode})" if mode else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
