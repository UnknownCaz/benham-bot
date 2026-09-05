"""
ledger_phase_b.py - the step-3 rewrite of Claude\\services.json for Phase B.

Run ONCE, on the PC, at brief step 3 - BEFORE the logon tasks are disabled and
the chain is killed (the codex lesson: the ledger first, so Banker's agent
takes its hands off the entry within one probe cycle and never tries to
revive a bot that is deliberately gone). Idempotent: a second run finds the
entry already on cazzy-mac and changes nothing.

What it does, exactly (BUILD-BRIEF step 3):
  services.benham-bot        -> host cazzy-mac, port 8903, process_match /
                                start / stop null, autostart false,
                                supervised_by = the plist, logs null (real
                                path in notes), blast_radius rewritten
  scheduled_tasks.benham-bot-task, .benham-bot-tray   -> DELETED
  reserved_ports             -> gains 8903
  scheduled_tasks.claude-initiates-daily.heartbeat    -> the PC-side .local stamp

Backs the file up first as services.json.bak-<date>-pre-phase-b. No git under
Claude\\ - the .bak IS the revival path.

    python scripts/ledger_phase_b.py            # dry run: prints the diff
    python scripts/ledger_phase_b.py --write    # writes it
"""

import json
import os
import shutil
import sys
from datetime import date

LEDGER = os.path.join(os.path.expanduser("~"), "Claude", "services.json")
HEARTBEAT = os.path.join(os.path.expanduser("~"), "Claude", "Projects", "Work-In-Project",
                         "benham-bot", "state", "initiative-heartbeat.local")

PLIST = ("launchd on cazzy-mac: /Library/LaunchDaemons/com.caz.benham-bot.plist, label "
         "com.caz.benham-bot, RunAtLoad + KeepAlive, runs /Users/administrator/benham-bot/"
         ".venv/bin/python -u -m benham.bot (MARKERLESS - the primary face, INTENT Stage 7) "
         "as administrator. A LaunchDaemon not a LaunchAgent for the console's own reason: "
         "the machine autorestarts after power loss but has no auto-login. Restart it with: "
         "sudo launchctl kickstart -k system/com.caz.benham-bot (over ssh), or `python "
         "benham.py restart --face benham` from the PC (the bot's own owner-gated restart, "
         "INTENT 44).")

BLAST = ("Benham offline = the ask queue goes quiet (no session can reach Tyler and no "
         "answer binds), Raven's delivery and the initiates lane both log failed runs "
         "against an unreachable bot, Doom's line to Benham is dead, and the Gt25 exaroton "
         "watchdog stops watching Isle of Berk. Codex on the same Mac is a separate launchd "
         "job and is untouched; nothing on the PC depends on the process itself any more - "
         "every PC caller is a client of :8903 over the tailnet and fails in one line.")

NOTES = ("MOVED to cazzy-mac at Phase B (2026-09-05; drafts\\benham-phase-b\\BUILD-BRIEF.md, "
         "INTENT decisions 38-48). Deployed by git clone to /Users/administrator/benham-bot, "
         "OUTSIDE the synced repo clone (codex's precedent), pinned commit in "
         "drafts\\benham-phase-b\\HANDOFF.md. Python 3.14.7 venv. Secrets rode scp, never git: "
         "config/environ.env on the Mac holds BOT_KEY (RESET at the Developer Portal inside "
         "the cutover window - the PC's copy died with the reset, and the PC's environ.env no "
         "longer carries it: INTENT 42) + ANTHROPIC_API_KEY; control.json; exaroton_watch.json; "
         "vendor/exaroton/.env (the Gt25 watchdog token, INTENT 40). Port 8903 is TWO sockets "
         "on one number: 127.0.0.1:8903 is the loopback LIVENESS port the console probes "
         "(BENHAM_HEALTH_PORT -> benham/core/health.py), and 100.76.11.56:8903 is the "
         "token-gated API the PC's benham.py talks to (BENHAM_API_BIND -> benham/core/"
         "server.py; token ~/.config/benham-bot.token on both machines, X-Benham-Token). "
         "process_match/recipes are null per startup_check rule 4 (they cannot cross machines; "
         "launchd owns its life). Logs: /Users/administrator/benham-bot/logs/benham.log, "
         "self-rotated (BENHAM_LOG_FILE -> rotlog); logs stays null here because the agent's "
         "/sight/tail reads desk files only. The PC-era chain ('benham-bot' logon task -> "
         "supervise_bot.ps1 -> the bot, plus the 'benham-bot-tray' task) is RETIRED with this "
         "rewrite, deliberately: both tasks DISABLED (not deleted; scripts\\*.task.backup.xml "
         "are the revival path), tray_bot.ps1 archived. HAZARD, structural not procedural: the "
         "PC has no BOT_KEY any more, so a hand-typed supervise_bot.ps1 on the PC fails at "
         "boot rather than gateway-fighting the Mac. Moving back = Reset Token at the portal "
         "AFTER `launchctl bootout system/com.caz.benham-bot` on the Mac. PC access (pc_task, "
         "the pc.. prefix) was DROPPED outright with the move (INTENT 39, Tyler's call).")


def rewrite(d):
    changes = []
    svc = d["services"]["benham-bot"]
    if svc.get("host") != "cazzy-mac":
        new = dict(svc)
        new.update({
            "title": "Benham (Discord bot, on cazzy-mac)",
            "host": "cazzy-mac", "port": 8903, "url": None,
            "process_match": None, "heartbeat": None,
            "start": None, "stop": None, "start_guard": None,
            "supervised": True, "supervised_by": PLIST,
            "autostart_default": False, "headless": True, "logs": None,
            "blast_radius": BLAST, "notes": NOTES,
        })
        for k in ("start_guard",):
            if new.get(k) is None and k not in svc:
                del new[k]
        d["services"]["benham-bot"] = new
        changes.append("services.benham-bot -> cazzy-mac :8903, hands off")
    for k in ("benham-bot-task", "benham-bot-tray"):
        if k in d["scheduled_tasks"]:
            del d["scheduled_tasks"][k]
            changes.append(f"scheduled_tasks.{k} deleted")
    if "8903" not in d["reserved_ports"]:
        d["reserved_ports"]["8903"] = (
            "Benham face on CAZZY-MAC (Phase B, 2026-09-05): loopback liveness "
            "(127.0.0.1) AND the tailnet API (100.76.11.56) the PC's benham.py talks to - "
            "two sockets, one number. Free on the PC; reserved so nothing here claims it.")
        changes.append("reserved_ports += 8903")
    lane = d["scheduled_tasks"].get("claude-initiates-daily")
    if lane and lane.get("heartbeat") != HEARTBEAT:
        lane["heartbeat"] = HEARTBEAT
        lane["notes"] = (lane.get("notes") or "") + (
            " HEARTBEAT MOVED 2026-09-05 (Phase B, INTENT 41): initiative-log.md is written "
            "by the bot on cazzy-mac now, so the proof-of-run is the PC-side stamp "
            "state\\initiative-heartbeat.local, touched by `benham.py initiate` after every "
            "successful call - silent or not - which is what this lane makes.")
        changes.append("claude-initiates-daily.heartbeat -> initiative-heartbeat.local")
    return changes


def main(argv):
    with open(LEDGER, encoding="utf-8") as f:
        d = json.load(f)
    changes = rewrite(d)
    if not changes:
        print("ledger already rewritten - nothing to do")
        return 0
    for c in changes:
        print("  " + c)
    if "--write" not in argv:
        print("(dry run - pass --write)")
        return 0
    bak = f"{LEDGER}.bak-{date.today().isoformat()}-pre-phase-b"
    if not os.path.exists(bak):
        shutil.copy2(LEDGER, bak)
        print("backup:", bak)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, LEDGER)
    print("written:", LEDGER)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
