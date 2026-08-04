"""
usage.py - what Benham has actually consumed, from the log it already writes.

    python usage.py                 # newest log
    python usage.py --log bot.log   # a specific one
    python usage.py --all           # every *.out / bot.log together
    python usage.py --today         # only today's lines

Answers three questions that were previously guesswork:

  What is the process costing? Memory, CPU seconds, uptime.
  What are the API calls costing? Tokens per path, and crucially the cache hit
  rate - the static prefix is ~7.4k tokens, so whether it is read from cache or
  rebuilt dominates the price of a message.
  What is it doing? Which capabilities run, what policy refuses, what gets
  proposed and confirmed.

Prices below are list rates for estimating only. They are wrong in two known ways
and the output says so: PC sessions bill Tyler's Max plan rather than per-token,
and the printed dollar figure is what the same work would have cost on the API.

No dependencies - process stats come from PowerShell, since psutil is not
installed and this is not worth an install.
"""

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import time

# Aliased: `paths` is a local or parameter name throughout this file.
from benham import paths as _paths
# Rotated-out captures live here; the live boot*.out stays in LOG_DIR while its
# process holds the handle, so both directories have to be searched.
LOGS_DIR = os.path.join(_paths.ROOT, "logs")

# Per million tokens, list price. Cache reads are a tenth of input; cache writes
# are 1.25x. Only used for the estimate, and only for the direct-API paths.
PRICES = {
    "claude-sonnet-5": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}
DEFAULT_MODEL = "claude-sonnet-5"

RE_AGENT = re.compile(
    r"agent usage \[(?P<label>[^\]]+)\] (?P<body>.*?) model=(?P<model>\S+)")
RE_BRAIN = re.compile(r"brain: in=(?P<in>\d+) out=(?P<out>\d+)")
RE_PCTASK = re.compile(r"PC task finished: (?:\$(?P<cost>[\d.]+)|cost n/a), tools: \[(?P<tools>[^\]]*)\]")
RE_ACTION = re.compile(r"\] action (?P<name>\w+) by (?P<who>[^:]+):")
RE_DENIED = re.compile(r"\] DENIED (?P<name>\w+) by (?P<who>\S+) \[rule=(?P<rule>\w+)")
RE_PROPOSED = re.compile(r"\] PROPOSED (?P<name>\w+) by")
RE_CONFIRMED = re.compile(r"\] CONFIRMED (?P<name>\w+) ")
RE_PCPERM = re.compile(r"PC-PERMISSION \[\d+\] (?P<verdict>APPROVED|DENIED|TIMED OUT)")
RE_SECRET = re.compile(r"SECRET-READ")
RE_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2})")


def kv(body):
    """Parse 'in=123 out=45 cache_read=678' into ints."""
    out = {}
    for tok in body.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                out[k] = int(v)
            except ValueError:
                pass
    return out


def process_stats():
    """Ask PowerShell about the running bot. Returns a dict or None."""
    ps = (
        "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*bot.py*' } | Select-Object -First 1; "
        "if ($p) { $q = Get-Process -Id $p.ProcessId; "
        "[pscustomobject]@{pid=$p.ProcessId; mb=[math]::Round($q.WorkingSet64/1MB,1); "
        "cpu=[math]::Round($q.CPU,1); threads=$q.Threads.Count; "
        "start=$q.StartTime.ToString('o')} | ConvertTo-Json -Compress }"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout.strip()) if r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def scan(paths, day=None):
    s = {
        "agent_calls": 0, "agent_in": 0, "agent_out": 0,
        "agent_cache_read": 0, "agent_cache_write": 0, "agent_models": collections.Counter(),
        "brain_calls": 0, "brain_in": 0, "brain_out": 0,
        "pc_tasks": 0, "pc_cost": 0.0, "pc_tools": collections.Counter(),
        "actions": collections.Counter(), "actors": collections.Counter(),
        "denied": collections.Counter(),
        # Per rule, not global: printing one shared dict beside every rule read as
        # though each rule had denied all of them.
        "denied_actions": collections.defaultdict(collections.Counter),
        "proposed": 0, "confirmed": 0,
        "pc_perm": collections.Counter(), "secret_reads": 0,
        "lines": 0, "files": 0,
    }
    for p in paths:
        if not os.path.isfile(p):
            continue
        s["files"] += 1
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if day:
                    m = RE_STAMP.match(line)
                    if not m or m.group(1) != day:
                        continue
                s["lines"] += 1
                m = RE_AGENT.search(line)
                if m:
                    d = kv(m.group("body"))
                    s["agent_calls"] += 1
                    s["agent_in"] += d.get("in", 0)
                    s["agent_out"] += d.get("out", 0)
                    s["agent_cache_read"] += d.get("cache_read", 0)
                    s["agent_cache_write"] += d.get("cache_write", 0)
                    s["agent_models"][m.group("model")] += 1
                    continue
                m = RE_BRAIN.search(line)
                if m:
                    s["brain_calls"] += 1
                    s["brain_in"] += int(m.group("in"))
                    s["brain_out"] += int(m.group("out"))
                    continue
                m = RE_PCTASK.search(line)
                if m:
                    s["pc_tasks"] += 1
                    if m.group("cost"):
                        s["pc_cost"] += float(m.group("cost"))
                    for t in m.group("tools").split(","):
                        t = t.strip().strip("'\"")
                        if t:
                            s["pc_tools"][t] += 1
                    continue
                m = RE_ACTION.search(line)
                if m:
                    s["actions"][m.group("name")] += 1
                    s["actors"][m.group("who").strip()] += 1
                    continue
                m = RE_DENIED.search(line)
                if m:
                    s["denied"][m.group("rule")] += 1
                    s["denied_actions"][m.group("rule")][m.group("name")] += 1
                    continue
                if RE_PROPOSED.search(line):
                    s["proposed"] += 1
                elif RE_CONFIRMED.search(line):
                    s["confirmed"] += 1
                m = RE_PCPERM.search(line)
                if m:
                    s["pc_perm"][m.group("verdict")] += 1
                if RE_SECRET.search(line):
                    s["secret_reads"] += 1
    return s


def estimate(s):
    """List-price estimate for the direct-API paths only."""
    model = (s["agent_models"].most_common(1)[0][0] if s["agent_models"] else DEFAULT_MODEL)
    ap = PRICES.get(model, PRICES[DEFAULT_MODEL])
    agent = (s["agent_in"] / 1e6 * ap["in"]
             + s["agent_cache_read"] / 1e6 * ap["in"] * 0.10
             + s["agent_cache_write"] / 1e6 * ap["in"] * 1.25
             + s["agent_out"] / 1e6 * ap["out"])
    bp = PRICES["claude-haiku-4-5"]
    brain = s["brain_in"] / 1e6 * bp["in"] + s["brain_out"] / 1e6 * bp["out"]
    return agent, brain, model


def bar(label, value, total, width=28):
    fill = int(width * value / total) if total else 0
    return f"  {label:<16} {'#' * fill}{'.' * (width - fill)} {value}"


def log_candidates():
    """Every bot capture worth scanning, live ones and archived ones alike.

    supervise.log is on the list because it is where the lines actually go now:
    under the benham-bot logon task, the supervisor captures bot.py's stream, and
    boot*.out stopped being written (the newest one here predates weeks of real
    traffic). Before this line existed, the default pick was that stale boot file
    and --today reported a running bot as all zeros.
    """
    cands = []
    # A set: after the Stage 5 move LOG_DIR and LOGS_DIR are the same directory,
    # and globbing it twice would double-count every capture.
    for d in {_paths.LOG_DIR, LOGS_DIR}:
        cands += glob.glob(os.path.join(d, "*.out"))
        cands += glob.glob(os.path.join(d, "bot*.log"))
    sup = os.path.join(_paths.LOG_DIR, "supervise.log")
    if os.path.isfile(sup):
        cands.append(sup)
    return cands


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Benham runtime usage report")
    ap.add_argument("--log", help="specific log file")
    ap.add_argument("--all", action="store_true", help="every bot log in the folder")
    ap.add_argument("--today", action="store_true", help="only today's lines")
    args = ap.parse_args(argv)

    if args.log:
        paths = [args.log]
    elif args.all:
        paths = sorted(log_candidates())
    else:
        cands = sorted(log_candidates(), key=os.path.getmtime)
        paths = [cands[-1]] if cands else []
    if not paths:
        print("No bot logs found.", file=sys.stderr)
        return 1

    # UTC, because the log stamps are UTC ([2026-08-04 03:45:46Z]). Local date
    # here meant every line written after 00:00 UTC - early evening in Tyler's
    # timezone, exactly when the bot is busiest - failed the "today" filter and
    # the report zeroed out while the bot was demonstrably chatting.
    day = time.strftime("%Y-%m-%d", time.gmtime()) if args.today else None
    s = scan(paths, day)

    print("=" * 60)
    print(" BENHAM RUNTIME USAGE")
    print("=" * 60)
    print(f" source: {', '.join(os.path.basename(p) for p in paths[:4])}"
          + (f" (+{len(paths)-4} more)" if len(paths) > 4 else ""))
    print(f" scope : {day or 'all time in these files'}  ({s['lines']} lines)")

    proc = process_stats()
    print("\n-- process --")
    if proc:
        up = ""
        try:
            import datetime
            st = datetime.datetime.fromisoformat(proc["start"])
            secs = (datetime.datetime.now(st.tzinfo) - st).total_seconds()
            up = f"{int(secs // 3600)}h {int(secs % 3600 // 60)}m"
        except Exception:  # noqa: BLE001
            pass
        print(f"  pid {proc['pid']} | RAM {proc['mb']} MB | CPU {proc['cpu']}s"
              f" | {proc['threads']} threads | up {up}")
    else:
        print("  bot is not running")

    print("\n-- Discord text agent (billed: API credits) --")
    if s["agent_calls"]:
        cached = s["agent_cache_read"]
        fresh = s["agent_in"] + s["agent_cache_write"]
        rate = 100 * cached / (cached + fresh) if (cached + fresh) else 0
        print(f"  API calls      : {s['agent_calls']}")
        print(f"  input          : {s['agent_in']:,}")
        print(f"  output         : {s['agent_out']:,}")
        print(f"  cache read     : {cached:,}   <- the cheap path")
        print(f"  cache written  : {s['agent_cache_write']:,}")
        print(f"  cache hit rate : {rate:.0f}%  (higher is better; the prefix is ~7.4k tokens)")
    else:
        print("  no calls recorded (usage logging was added 2026-07-27;"
              " earlier runs have none)")

    print("\n-- Voice brain (billed: API credits) --")
    print(f"  calls {s['brain_calls']} | in {s['brain_in']:,} | out {s['brain_out']:,}")

    print("\n-- PC sessions (billed: Max plan, NOT per-token) --")
    print(f"  tasks {s['pc_tasks']} | reported ${s['pc_cost']:.4f} equivalent")
    if s["pc_tools"]:
        tot = sum(s["pc_tools"].values())
        print(f"  {tot} tool rounds, ~${s['pc_cost']/tot:.3f} equivalent each"
              if tot else "")
        for t, n in s["pc_tools"].most_common(6):
            print(bar(t, n, tot))

    print("\n-- capabilities used --")
    if s["actions"]:
        tot = sum(s["actions"].values())
        for n, c in s["actions"].most_common(8):
            print(bar(n, c, tot))
        print(f"  by caller: {dict(s['actors'])}")
    else:
        print("  none")

    print("\n-- policy --")
    print(f"  proposed {s['proposed']} | confirmed {s['confirmed']}"
          f" | denied {sum(s['denied'].values())}")
    for rule, c in s["denied"].most_common():
        which = ", ".join(f"{n} x{k}" for n, k in s["denied_actions"][rule].most_common())
        print(f"    {rule}: {c}  ({which})")
    if s["pc_perm"]:
        print(f"  PC approvals: {dict(s['pc_perm'])}")
    if s["secret_reads"]:
        print(f"  !! SECRET-READ events: {s['secret_reads']}")

    a, b, model = estimate(s)
    print("\n-- estimated API spend (list price, direct-API paths only) --")
    print(f"  text agent ({model}): ${a:.4f}")
    print(f"  voice brain          : ${b:.4f}")
    print(f"  TOTAL billed to API  : ${a + b:.4f}")
    print(f"  (PC's ${s['pc_cost']:.2f} is NOT included - that is plan usage, not a charge)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
