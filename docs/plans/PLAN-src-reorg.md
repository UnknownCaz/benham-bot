# Source Directory Reorganization — Plan

**Status: stages 0–6 executed 2026-08-04; Stage 7 (live verification + Task
Scheduler re-import) pending.** Deviations found during execution, all landed:
`channels.json`, `voice_settings.json` and `personality_overrides.txt` are
bot-written so they live in `state/`, not `config/` (voice_settings was also
untracked from git); the Task Scheduler XML exports embed the machine SID and
account name, so they are gitignored in `scripts/` rather than committed;
`inbox.jsonl.bak` had escaped both jsonl ignore patterns and is now covered;
test invocation is `python tests/test_x.py` (self-running scripts, not pytest).

**Original plan as agreed:**
Decisions locked with Tyler 2026-08-04: full package layout (`benham/` with `core`,
`cli`, `guest` subpackages), a single root CLI dispatcher (`benham.py`, clean cut — the
old per-command scripts are deleted, not shimmed), a `config/` + `state/` split for
non-code files, and the reorg happens now rather than waiting on the guest work.

Staged the house way: seven stages, each leaving the system fully working, so any
regression is attributable to a single step. Stages 0–2 need no downtime; 3–5 each
need a short stop/restart of the bot.

---

## 1. Where we are today (ground truth)

Everything lives flat in the repo root: 32 runtime `.py` modules, 11 `test_*.py`,
hand-edited config JSON, bot-mutated state (inbox, memories, usage, searches),
personas, ops scripts, stray logs, and two plan docs. The couplings that make moving
things dangerous:

- **`BASE_DIR = os.path.dirname(os.path.abspath(__file__))` in 14 modules** (agent,
  bot, brain, catchup, codesession, draft, guest, guest_workspace, identity, outbox,
  read_history, status, usage, webhook). Every state/config file is resolved relative
  to the module's own location — move the module and it looks for its files somewhere
  else. `environ.env` is loaded the same way in bot.py, agent.py, brain.py.
- **All imports are flat** (`import policy`, `import identity`, …). A package layout
  touches every module and every test.
- **Process detection by command line.** `tray_bot.ps1`, `supervise_bot.ps1`, and
  `status.py` all find the bot with the same CIM query:
  `CommandLine -match 'bot\.py'`. The three deliberately agree on what "running"
  means; all three must change together.
- **Windows Task Scheduler** launches `tray_bot.ps1` / `supervise_bot.ps1` by absolute
  path (the `*.task.backup.xml` files are exports of those tasks). Moving the scripts
  silently breaks boot-time startup unless the live tasks are re-pointed.
- **`docs/` manual references CLI script names 100+ times** (`do.py` ×15, `guest.py`
  ×13, `bot.py` ×13, `webhook.py` ×12, `usage.py` ×11, `send.py` ×8, …), and every
  CLI script's own docstring shows `python <name>.py` usage lines.
- **`.gitignore` is the privacy fence.** Its patterns (`control.json`, `*.jsonl`,
  `outbox/`, `logs/`, `downloads/`, `guest_work/`, …) are slash-less so they match at
  any depth — but every move must be re-verified against it, because a miss commits
  private message content.
- **Open file handles.** A running bot holds `inbox.jsonl`; the supervisor holds
  `supervise.log` exclusively. State cannot move while the stack is up.
- There are uncommitted modifications in the working tree right now.

## 2. Target layout

```
benham-bot/
├── benham.py                  # CLI dispatcher — the only entry script at root
├── benham/                    # the package
│   ├── __init__.py
│   ├── paths.py               # NEW — ROOT, CONFIG_DIR, STATE_DIR, LOG_DIR, PROMPTS_DIR
│   ├── bot.py                 # the Discord process:  python -u -m benham.bot
│   ├── core/                  # shared libraries (imported, not invoked)
│   │   ├── agent.py  brain.py  capabilities.py  codesession.py  confirm.py
│   │   ├── exaroton_ops.py  identity.py  jsonio.py  outbox.py  pathsafe.py
│   │   ├── policy.py  shared_tools.py
│   ├── guest/
│   │   ├── guest.py  guest_agent.py  guest_workspace.py
│   └── cli/                   # one module per subcommand
│       ├── catchup.py  delete.py  dm.py  do.py  draft.py  fetch.py  listen.py
│       ├── purge.py  read_history.py  send.py  speak.py  status.py
│       ├── stoplisten.py  usage.py  watch_pc.py  webhook.py
├── tests/                     # the 11 test_*.py + conftest.py
├── config/                    # hand-edited; safe for the owner to touch
│   ├── control.json (+.example)  channels.json  webhooks.json
│   ├── voices.json  voice_settings.json  exaroton_watch.json (+.example)
│   └── environ.env
├── state/                     # bot-mutated; never hand-edit, never commit
│   ├── inbox.jsonl (+.bak)  agent_memory.json  agent_searches.jsonl
│   ├── guest_memory.json  guest_usage.json  guest_searches.jsonl
│   ├── voice_transcript.jsonl
│   ├── outbox/  downloads/  guest_work/
├── logs/                      # existing logs/ + root strays (supervise.log, boot2x.*)
├── prompts/                   # persona.md  guest_persona.md  guardrails.md  guest_guide.md
├── scripts/                   # tray_bot.ps1  supervise_bot.ps1  supervise_bot.bat  *.task.backup.xml
├── docs/                      # unchanged, plus:
│   └── plans/                 # PLAN-guest-permissions.md  PLAN-manual-site.md  this file
├── assets/                    # unchanged
├── README.md  OWNERS-GUIDE.md  requirements.txt  .gitignore
```

Classification rule: a module goes in `core/` if any other module imports it, in
`cli/` if nothing imports it and it exists to be invoked (`usage.py` and `webhook.py`
have `__main__` blocks and no importers → `cli/`). `guest.py` is imported by bot.py
*and* has a CLI — it stays in `guest/`; the dispatcher calls into it.

**Invocation after the cut:**

| Today | After |
|---|---|
| `python send.py "#ch" "hi"` | `python benham.py send "#ch" "hi"` |
| `python do.py …` | `python benham.py do …` |
| `python guest.py status` | `python benham.py guest status` |
| `python status.py` | `python benham.py status` |
| `python -u bot.py` | `python -u -m benham.bot` |

Subcommand names are the old filenames verbatim (`read_history`, `watch_pc` keep
their underscores) so every doc edit is the same mechanical rewrite:
`python X.py` → `python benham.py X`.

## 3. The stages

### Stage 0 — Freeze and baseline (no downtime)
Commit the current working-tree modifications, tag `pre-reorg`, run the full test
suite and record it green. Delete `__pycache__/` from root. Every later stage is its
own commit so any one is revertable in isolation.

### Stage 1 — `benham/paths.py`, nothing moves (no downtime)
Create the package skeleton (`benham/__init__.py`, `benham/paths.py`). `paths.py`
defines `ROOT` (the repo root, computed from its own location), and `CONFIG_DIR`,
`STATE_DIR`, `LOG_DIR`, `PROMPTS_DIR` — **all initially equal to `ROOT`**, because
nothing has moved yet. Convert all 14 `BASE_DIR` modules, the three `load_dotenv`
call sites, and every literal state/config filename to route through `paths.py`.
Pure refactor, zero behavior change; restart the bot on it and run the tests. After
this stage, relocating a whole category of files is a one-line flip in `paths.py`.

### Stage 2 — Libraries into the package (no downtime)
`git mv` the `core/` and `guest/` modules into place; rewrite imports everywhere
(remaining root entry scripts and tests: `import policy` → `from benham.core import
policy`). Move the 11 tests to `tests/` in the same commit (their imports are being
edited anyway) with a `conftest.py` that puts the repo root on `sys.path`. Entry
scripts and `bot.py` are still flat at root and still work. Restart bot, tests green.

### Stage 3 — Dispatcher, clean cut (brief restart)
Add root `benham.py`: argparse subcommand table mapping each name to its module's
main in `benham/cli/` (plus `guest` → `benham.guest.guest._main`). Shared
boilerplate lives once in the dispatcher: UTF-8 stdout reconfigure (currently
copy-pasted per script), consistent exit codes. `git mv` the 16 CLI scripts into
`benham/cli/`, fixing their imports and their docstring usage lines. Update the two
places that invoke CLI scripts by name: the tray menu (`python status.py; python
guest.py status` → `python benham.py status; python benham.py guest status`) and
any cross-references between scripts (`draft.py` mentions `send.py`). `bot.py` is
untouched. Verify: every subcommand smoke-runs, `python benham.py --help` lists all.

### Stage 4 — `bot.py` into the package (stop/start the bot)
`git mv bot.py benham/bot.py`; the process becomes `python -u -m benham.bot`.
Update, in the same commit, all three process-detection sites — supervise_bot.ps1
(start command and `-match 'bot\.py'`), tray_bot.ps1 (detection and its
explicit-kill path), status.py (`bot_pid()`) — to `-match '-m benham\.bot'`.
Regex check: the pattern must not match CLI invocations (`…benham-bot\benham.py
send` contains a hyphen, not a dot — safe) and must match the new launch line.
Also update supervise_bot.bat, and the three tests that `import bot` directly
(test_guest, test_owner_gate, test_pc_reply). Stop the bot, land, restart under the
supervisor, confirm tray/status/supervisor all agree it's running.

### Stage 5 — config / state / logs / prompts / scripts (stop the whole stack)
The big physical move. Stop tray, supervisor, and bot first (open handles on
`inbox.jsonl` and `supervise.log`).

1. `git mv` the tracked files (`*.example`, `voices.json`, `voice_settings.json`,
   persona/guardrails/guide `.md`, the `.ps1`/`.bat`/`.xml` scripts); plain-move the
   untracked private state (inbox, memories, usage, searches, transcript, `outbox/`,
   `downloads/`, `guest_work/`) and stray root logs (`supervise.log`, `boot23–25.*`)
   into `logs/`.
2. Flip the `paths.py` constants to `config/`, `state/`, `logs/`, `prompts/`.
3. `.gitignore`: update the path-anchored intent comments and add explicit `state/`
   coverage. Then **audit**: `git status --ignored` plus `git check-ignore -v` on
   every private file in its new home — nothing private may become trackable.
4. Update the file paths embedded in `tray_bot.ps1` (control.json, inbox.jsonl,
   guest_usage.json, guest_searches.jsonl, supervise.log) and the supervisor's
   `$Log` target.
5. Re-point Task Scheduler: edit the two `*.task.backup.xml` exports to the
   `scripts/` paths and re-import them (`schtasks /create /xml … /f`), or edit the
   live tasks directly. **This is the step that silently breaks boot startup if
   skipped.**

Restart the stack. Verify: bot boots, a `benham.py send` round-trips through
`state/outbox/`, guest state intact (`benham.py guest status` shows prior counters),
tray menu items work.

### Stage 6 — Documentation (no downtime)
- `docs/` manual: mechanical rewrite of every command example
  (`python X.py` → `python benham.py X`; `python bot.py` → `python -m benham.bot`),
  plus any prose describing the flat layout.
- `README.md`: layout section, install/run instructions, command examples.
- `OWNERS-GUIDE.md` stub: confirm it still points correctly.
- `PLAN-guest-permissions.md`: update its file references (policy.py →
  benham/core/policy.py etc.) since it's still active; then `git mv` both PLAN docs
  and this file into `docs/plans/`.
- Claude's memory: architecture, PC-access, and manual-site entries updated to the
  new layout and invocation style.
- `.vscode/settings.json` if it pins any paths.

### Stage 7 — Final sweep and tag (no downtime)
Global greps for stragglers: `python \w+\.py`, `bot\.py`, each old root filename
across `*.py *.ps1 *.bat *.md docs/`. Full test suite. Live smoke test: send via
dispatcher, kill the bot process and watch the supervisor restart it, reboot-level
check of the scheduled tasks if convenient. Tag `post-reorg`.

## 4. Risks and their mitigations

- **Supervisor/tray/status disagree on "running"** → all three regexes change in one
  commit (Stage 4), with an explicit non-match check against CLI invocations.
- **Scheduled tasks point at moved scripts** → Stage 5 step 5 is mandatory, not
  cleanup; verified by re-importing the edited XMLs.
- **Private state becomes committable** → Stage 5 step 3 audit before the restart;
  the slash-less ignore patterns already match at depth, but trust the audit, not
  the assumption.
- **State moved while a handle is open** → full-stack stop is the first action of
  Stage 5.
- **Tests patch module-level path constants** (e.g. guest_workspace `ROOT`) → they
  patch `paths.py` attributes after Stage 1; adjust in the same commits.
- **In-flight guest work collides with renames** → the guest plan's remaining stages
  rebase onto the new paths; its doc is updated in Stage 6.
- **Anything missed** → `pre-reorg` tag; every stage is one revertable commit; the
  Stage 5 commit message records the exact untracked-file move list (git won't).
