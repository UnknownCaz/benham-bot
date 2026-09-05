"""
rpc.py - the ONE table of store operations the bot executes for a remote CLI.

Phase B (INTENT decision 38): the Benham face runs on cazzy-mac and the PC's
`benham.py` is a client of it. Half of the CLI already spoke to the bot through
the outbox; the other half - ask, outreach, initiate, conv, ideas, issues,
rooms, guest - wrote the shared stores DIRECTLY from the calling process. A
bot on another machine never sees a file a PC session wrote, so those verbs
had to become calls the bot executes in-process. This is the queue fold-in:
the bot is now the single writer of every store, the rule rooms v1 already
lived by.

THE TABLE IS THE ALLOWLIST. server.py dispatches `POST /store/<name>` only
for names listed here, and remote.py's proxies forward only these names -
everything else a CLI touches on a store module (constants, pure helpers like
face_of, valid_name) stays local. The two sides import the same table, so
they cannot disagree about what is reachable.

Each entry maps a dotted name to "module:function". Names under `rpc.` are
implemented in this file: the composite reads a CLI needs (a status
snapshot, an inbox tail, a usage scan) that no store module owns. `slow`
names talk to GitHub through the gh CLI and run on a worker thread rather
than the bot's event loop; everything else runs ON the loop, which is what
makes the bot the single writer - a store call from the PC serialises with
the bot's own ticks instead of racing them.

Nothing here imports discord. The client imports this module too, and a
`benham.py conv list` from a PC session should not pay for a Discord client
it never opens. Store modules are resolved lazily, by name, at call time.
"""

import collections
import glob
import importlib
import os
import time
from datetime import datetime, timezone

from benham import paths

# name -> "module:function". Keep it sorted by module; it is read by humans.
TABLE = {
    "conversations.all_conversations": "benham.core.conversations:all_conversations",
    "conversations.bank": "benham.core.conversations:bank",
    "conversations.close": "benham.core.conversations:close",
    "conversations.get": "benham.core.conversations:get",
    "conversations.open_conversation": "benham.core.conversations:open_conversation",
    "conversations.queue_for": "benham.core.conversations:queue_for",
    "conversations.slot_of": "benham.core.conversations:slot_of",
    "conversations.uncollected": "benham.core.conversations:uncollected",
    "guest.forget": "benham.guest.guest:forget",
    "ideas.entries": "benham.core.ideas:_entries",
    "ideas.mark_swept": "benham.core.ideas:mark_swept",
    "ideas.new_since_sweep": "benham.core.ideas:new_since_sweep",
    "initiative.add_thread": "benham.core.initiative:add_thread",
    "initiative.close_thread": "benham.core.initiative:close_thread",
    "initiative.drop_thread": "benham.core.initiative:drop_thread",
    "initiative.lane_state": "benham.core.initiative:lane_state",
    "initiative.mark_thread_asked": "benham.core.initiative:mark_thread_asked",
    "initiative.open_question": "benham.core.initiative:open_question",
    "initiative.read_markdown": "benham.core.initiative:read_markdown",
    "initiative.record_run": "benham.core.initiative:record_run",
    "initiative.reset": "benham.core.initiative:reset",
    "initiative.sweep": "benham.core.initiative:sweep",
    "initiative.threads": "benham.core.initiative:threads",
    "issues.entries": "benham.core.issues:_entries",
    "issues.retry_unsent": "benham.core.issues:retry_unsent",
    "issues.unsent": "benham.core.issues:unsent",
    "loopclose.run": "benham.core.loopclose:run",
    "policy.authorize_unprompted": "benham.core.rpc:authorize_unprompted",
    "rooms.get": "benham.core.rooms:get",
    "rooms.listing": "benham.core.rooms:listing",
    "rooms.read_and_mark": "benham.core.rooms:read_and_mark",
    "rpc.guest_status": "benham.core.rpc:guest_status",
    "rpc.identity_snapshot": "benham.core.rpc:identity_snapshot",
    "rpc.inbox_tail": "benham.core.rpc:inbox_tail",
    "rpc.status_snapshot": "benham.core.rpc:status_snapshot",
    "rpc.usage_report": "benham.core.rpc:usage_report",
}

# These reach GitHub via the gh CLI (seconds, sometimes thirty). Off the loop.
SLOW = frozenset({"issues.retry_unsent", "loopclose.run"})


def resolve(name):
    """The callable behind one table name, or KeyError. Imports at call time."""
    spec = TABLE[name]
    mod, fn = spec.split(":")
    return getattr(importlib.import_module(mod), fn)


# --------------------------------------------------------------------------
# What the serving process knows about itself. server.py sets this once; a
# CLI running these functions locally (no bot, tests, the Mac's own shell)
# sees None for the live fields and prints "not running", which is true.
# --------------------------------------------------------------------------

RUNTIME = {"client": None, "face": paths.PROCESS_FACE, "host": None,
           "started": time.time(), "log_file": None}


def set_runtime(client, face, host=None, log_file=None):
    RUNTIME.update(client=client, face=face, host=host, log_file=log_file)


def _gateway_connected():
    c = RUNTIME["client"]
    if c is None:
        return None
    try:
        return bool(c.is_ready() and not c.is_closed())
    except Exception:  # noqa: BLE001 - a probe must never raise
        return None


# --------------------------------------------------------------------------
# Composite reads
# --------------------------------------------------------------------------

def authorize_unprompted(conv):
    """policy.authorize_unprompted, with the Decision flattened for the wire."""
    from benham.core import policy
    d = policy.authorize_unprompted(conv)
    return {"verdict": d.verdict, "allowed": d.allowed, "denied": d.denied,
            "rule": d.rule, "reason": d.reason}


def identity_snapshot():
    """The identity facts the CLIs read: who owns this face, who its guests
    are, who outreach may contact. Read from the SERVING process, so a PC
    whose control.json has drifted from the Mac's still guards by the truth."""
    from benham.core import identity
    cfg = identity.CONTROL.get("outreach") or {}
    people = cfg.get("people") or {}
    return {
        "face": paths.PROCESS_FACE,
        "owner_ids": sorted(identity.OWNER_IDS),
        "guest_people": {str(k): int(v) for k, v in identity.GUEST_PEOPLE.items()},
        "guest_ids": sorted(identity.GUEST_IDS),
        "outreach_people": ({str(k).strip().lower(): int(v) for k, v in people.items()}
                            if people else None),
    }


def guest_status():
    """Everything `benham.py guest status` prints, as data."""
    from benham.core import identity, jsonio
    from benham.guest import guest
    u = guest._usage()
    mem = jsonio.read_json(guest.MEMORY_FILE, default={})
    return {
        "enabled": identity.guest_enabled(),
        "mode": identity.guest_config().get("mode", "chat"),
        "allowlist": sorted(identity.people_map(identity.guest_config().get("ids")).values()),
        "model": guest.MODEL, "max_tokens": guest.MAX_TOKENS,
        "daily_cap": guest.DAILY_CAP, "global_cap": guest.GLOBAL_CAP,
        "cooldown": guest.COOLDOWN,
        "usage": {"date": u.get("date"), "global": u.get("global", 0),
                  "users": dict(u.get("users", {}))},
        "conversations_stored": len(mem),
    }


def inbox_tail(limit=20, dms=False):
    """The last N records of this face's inbox.jsonl, oldest first.

    `dms` keeps only direct messages. Reads the whole file - it is capped by
    rotation at 5 MB - rather than seeking, because a torn last line must be
    skipped the same way every other jsonl reader here skips it.
    """
    from benham.core import jsonio
    path = os.path.join(paths.process_state_dir(), "inbox.jsonl")
    rows = []
    for rec in jsonio.iter_jsonl(path):
        if dms and rec.get("guild_id") is not None:
            continue
        rows.append(rec)
    limit = max(1, min(int(limit or 20), 500))
    return rows[-limit:]


def _recent_failures(within_hours=24, limit=10):
    """Newest outbox/failed results inside the window - status.py's read,
    moved here so it runs where the outbox actually is."""
    from benham.core import jsonio, outbox
    folder = os.path.join(outbox.outbox_dir(paths.PROCESS_FACE), "failed")
    if not os.path.isdir(folder):
        return []
    cutoff = time.time() - within_hours * 3600
    results = [os.path.join(folder, fn) for fn in os.listdir(folder)
               if fn.endswith("_result.json")]
    results.sort(key=os.path.getmtime, reverse=True)
    out = []
    for path in results:
        if os.path.getmtime(path) < cutoff:
            break
        res = jsonio.read_json(path, default={})
        req = res.get("request") or {}
        action = res.get("action") or req.get("action") or "send"
        error = str(res.get("error") or "(no error recorded)")
        if len(error) > 120:
            error = error[:117] + "..."
        when = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        out.append({"when": when.isoformat(), "action": action, "error": error})
        if len(out) >= limit:
            break
    return out


def _log_candidates():
    """Every bot capture worth scanning - usage.py's list, run where the logs are."""
    cands = []
    for d in {paths.LOG_DIR, os.path.join(paths.ROOT, "logs")}:
        cands += glob.glob(os.path.join(d, "*.out"))
        cands += glob.glob(os.path.join(d, "bot*.log"))
        cands += glob.glob(os.path.join(d, "benham*.log"))
    sup = os.path.join(paths.LOG_DIR, "supervise.log")
    if os.path.isfile(sup):
        cands.append(sup)
    if RUNTIME["log_file"] and os.path.isfile(RUNTIME["log_file"]):
        cands.append(RUNTIME["log_file"])
    return sorted(set(cands))


def _newest_log_tail(keep=("Logged in as", "Synced")):
    logs = _log_candidates()
    logs += [p for p in glob.glob(os.path.join(paths.LOG_DIR, "*.log")) if p not in logs]
    if not logs:
        return None, []
    newest = max(logs, key=os.path.getmtime)
    lines = []
    try:
        with open(newest, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if any(k in line for k in keep):
                    lines.append(line.rstrip())
    except OSError:
        pass
    return os.path.basename(newest), lines[-6:]


def status_snapshot():
    """Everything `benham.py status` prints, as data - from the body the bot
    stands on. `pid`/`gateway_connected` are None when no bot serves this."""
    from benham.core import jsonio
    ch_path = os.path.join(paths.process_state_dir(), "channels.json")
    channels = jsonio.read_json(ch_path, default=None) if os.path.exists(ch_path) else None
    ch_mtime = None
    if os.path.exists(ch_path):
        ch_mtime = datetime.fromtimestamp(os.path.getmtime(ch_path), timezone.utc).isoformat()
    log_name, tail = _newest_log_tail()
    return {
        "face": RUNTIME["face"], "host": RUNTIME["host"],
        "pid": os.getpid() if RUNTIME["client"] is not None else None,
        "gateway_connected": _gateway_connected(),
        "uptime_s": int(time.time() - RUNTIME["started"]),
        "channels": channels if isinstance(channels, list) else None,
        "channels_written": ch_mtime,
        "failures": _recent_failures(),
        "log_name": log_name, "log_tail": tail,
    }


def _process_stats():
    """RAM / CPU / threads / start for THIS process, portable. None when no
    bot is serving - a bare CLI running this locally is not the bot."""
    if RUNTIME["client"] is None:
        return None
    import threading
    try:
        import resource
        import sys as _sys
        ru = resource.getrusage(resource.RUSAGE_SELF)
        # maxrss is BYTES on macOS and KILOBYTES on Linux (getrusage(2) says so
        # on each). Decided by platform, not by magnitude: the first cut guessed
        # from the size and printed 69932 MB for a 70 MB process.
        rss = ru.ru_maxrss / (1024 * 1024) if _sys.platform == "darwin" else ru.ru_maxrss / 1024
        cpu = ru.ru_utime + ru.ru_stime
    except ImportError:  # Windows: resource does not exist
        rss, cpu = 0.0, time.process_time()
    return {"pid": os.getpid(), "mb": round(rss, 1), "cpu": round(cpu, 1),
            "threads": threading.active_count(),
            "start": datetime.fromtimestamp(RUNTIME["started"], timezone.utc).isoformat()}


def usage_report(log=None, all=False, today=False):  # noqa: A002 - the CLI's flag names
    """The scan `benham.py usage` prints, run where the logs are. Counters
    come back as plain dicts; the CLI rehydrates them."""
    from benham.cli import usage as usage_cli
    if log:
        sources = [log if os.path.isabs(log) else os.path.join(paths.LOG_DIR, log)]
        if not os.path.isfile(sources[0]) and os.path.isfile(log):
            sources = [log]
    elif all:
        sources = _log_candidates()
    else:
        cands = sorted(_log_candidates(), key=os.path.getmtime)
        sources = [cands[-1]] if cands else []
    if not sources:
        return {"sources": [], "scan": None, "process": _process_stats()}
    day = time.strftime("%Y-%m-%d", time.gmtime()) if today else None
    s = usage_cli.scan(sources, day)
    flat = {}
    for k, v in s.items():
        if isinstance(v, collections.defaultdict):
            flat[k] = {kk: dict(vv) for kk, vv in v.items()}
        elif isinstance(v, collections.Counter):
            flat[k] = dict(v)
        else:
            flat[k] = v
    return {"sources": [os.path.basename(p) for p in sources], "scan": flat,
            "process": _process_stats()}
