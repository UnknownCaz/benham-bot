# benham-bot

Benham (**Benham#2721**) is a personal Discord bot that acts as a controllable proxy: it can
read and send text, run a real Claude Code session on its owner's PC, and control a set of
[exaroton](https://exaroton.com) Minecraft servers. Messages it sends appear as *Benham*, not as
its owner - it is a proxy, not account impersonation (Discord forbids the latter). It only works in
servers it has been invited to.

## Architecture

A single persistent process, `benham/bot.py`, stays connected to Discord and does all the I/O.
Nothing talks to Discord directly; instead CLI commands drop request files into `state/outbox/`
and read results back, which keeps the heavy always-on process separate from one-shot actions.

```
  CLI (python benham.py send|draft|delete|fetch ...)  ->  state/outbox/*.json
                                                            |
                                      the bot polls the outbox every ~2s
                                                            |
                  Discord  <->  the bot  ->  state/outbox/sent|failed/*_result.json
                                        |
        every message it sees  ->  state/inbox.jsonl   (live capture, one JSON per line)
        on each boot           ->  state/channels.json (guild + channel IDs it can see)
```

Source layout (since the Aug 2026 reorg): the `benham/` package holds all code —
`benham/core/` (shared libraries), `benham/cli/` (one module per command), `benham/guest/`
(the guest lane), `benham/bot.py` (the process), `benham/paths.py` (the one module that knows
where every file lives). `benham.py` at the root is the CLI dispatcher; `python benham.py --help`
is the command catalog. Non-code splits by who writes it: `config/` (hand-edited), `state/`
(bot-written, never committed), `prompts/` (personas + guardrails), `logs/`, `scripts/`
(supervisor, tray, Task Scheduler exports, `gen_readme.py`), `tests/`.

`archive/` holds features that were built, shipped, and then removed for lack of use rather than
for being broken - **voice** (TTS, listening, wake words, autonomous replies) and the **guest tool
loop** (code execution, per-guest file workspaces, shared-channel reads), both retired 2026-08-16.
Each carries a README with the knowledge that was expensive to acquire and would not survive a
`git rm`: the DAVE decryption monkeypatch and why downgrading discord.py cannot work, why the
voice library versions are pinned, and the four lessons the guest loop taught about handing tools
to a model. Read those before reinstating anything.

## Benham as Claude's face

Benham is the body Claude wears in Discord. Tyler talks to it, Claude acts through it, and it is
the channel between them when Tyler is away from the PC. Two paths lead to the same capabilities:

```
  Tyler DMs Benham  ->  owner gate  ->  agent.py (Claude + 49 tools)  ->  capabilities.py
                                                                              |
  Claude Code       ->  do.py  ->  outbox/*.json  ->  bot.py poller  ->  capabilities.py
                                                                              |
                                                                          Discord
```

**It answers to one person.** `identity.is_owner()` is checked at every entry point. Anyone else
in a server can talk to Benham and be talked to, but cannot direct it - there is no guild-admin
escape hatch and no role that grants control. Message text Benham reads from channels is treated
as data, never as instructions.

**No command syntax.** Plain messages go to the model, which decides whether to answer, act, or
both. "what's going on in #general" reads it; "post the server IP in #general" posts it.

### The four tiers

Every capability declares one, and `capabilities.run()` enforces it.

<!-- GENERATED:tier-table -->
| tier | examples | gate |
|---|---|---|
| **read** (16) | `read_channel`, `search_messages`, `find_user`, `read_attachments`, `guild_info` | none |
| **speak** (6) | `send_message`, `send_embed`, `send_file`, `dm_user`, `react` | owner only |
| **manage** (22) | `pin_message`, `add_role`, `create_channel`, `set_channel_permissions`, `timeout_member` | owner only |
| **destructive** (7) | `delete_message`, `purge_messages`, `delete_channel`, `kick_member`, `ban_member` | guild allowlist + dry-run + explicit confirm |
<!-- /GENERATED:tier-table -->

Run `python benham.py do list` for the full catalogue, `python benham.py do help <action>` for one action's
parameters.

### How destructive actions work

Three independent gates, in this order:

1. **Guild allowlist.** `destructive_guilds` in `control.json`. A tier-3 action anywhere else is
   refused outright - no confirmation unlocks it, only editing the file. Checked *before* the
   dry-run, so a refused action never even reports what it would have touched.
2. **Mandatory dry-run.** The first call performs nothing and returns real facts: counts, date
   spans, author breakdowns, who currently holds the role. This is what catches a wrong channel
   id or an `older_than_days` off by 10x.
3. **Explicit confirm.** In a DM, Benham shows the preview and waits for a narrow affirmative
   ("yes", "do it") bound to a token. From the CLI, you re-run with `confirm_token=<token>`.
   There is no inline shortcut in either path.

**The model cannot confirm its own actions.** A tier-3 tool call returns a preview and halts the
tool chain. Tyler's "yes" is matched by code in `on_message` *before* the agent is invoked, and
fires the parked action directly - so the approval path never passes through the model, and no
message content anywhere can talk its way into a delete. Expiry means cancelled; silence is never
assent.

```bash
python benham.py do purge_messages channel_id=809357286036078612 limit=20 contains="test"
#   --- DRY RUN, nothing has happened ---
#   Delete 1 messages from #asd in Testing Server
#   ... To run it for real, repeat with: confirm_token=80ac01
python benham.py do purge_messages channel_id=809357286036078612 limit=20 contains="test" confirm_token=80ac01
```

## PC access - a real Claude Code session

`codesession.py` gives Benham the machine itself. It is not a reimplementation: it drives the
actual Claude Code CLI through the agent SDK, with `setting_sources` loading Tyler's own settings,
so the session has his real skills. "Restart Isle of Berk" works because the `exaroton` skill is
there, not because anything in this repo knows what exaroton is.

Reached through the `pc_task` capability, so the Discord agent delegates to it when a request is
about the PC rather than about Discord.

**Permission model: read freely, ask before changing.**

| what | behaviour |
|---|---|
| `Read`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `TodoWrite`, ... | runs immediately |
| anything else - writes, edits, shell commands, subagents | DMs Tyler, **blocks** until he answers |
| no answer within `permission_timeout_seconds` | **denied** - the session is told no and stops |

The allowlist is of *reads*, not of writes: anything unrecognised is treated as a change and asks.
When a future Claude Code version adds a tool this file has never heard of, the failure mode is an
unnecessary question rather than an unreviewed write.

The approval DM shows the **full command**, up to 1200 characters. An early version truncated it to
80, which would have meant approving a command you could not actually read.

### Two things this does NOT do

**The working directory is not a sandbox.** `Benhams-inbox/` is where the session starts. It can
`cd` anywhere or use absolute paths. It keeps scratch files tidy; it contains nothing.

**Secrets are readable.** Tyler chose full file access deliberately. Reads are free, so anyone who
can DM Benham can ask it to read `config/environ.env` and get the bot token and API key. The
ask-before-changing gate does not help here, because reading is not a change. What the code does
instead is make it loud: every read of a credential-shaped path logs at `SECRET-READ`. That is a
trail, not a block. Flipping this to a hard deny is a one-line change to `_SECRET_RE`'s use in
`_can_use_tool`.

### Billing

`use_api_key: true` (default) passes `ANTHROPIC_API_KEY` to the CLI, so PC tasks bill to API credit
rather than a Claude subscription. It works headlessly with no setup, which is what an unattended
bot needs. To use a subscription instead, run `claude` once in a real terminal, log in, then set
`use_api_key: false` - the env key takes precedence otherwise and would keep billing the API.
Measured: a trivial task runs a few cents; these are not free.

## Commands

Two kinds: **CLI commands** run in a terminal (from the repo directory), and **in-Discord slash
commands** typed in a channel. Most CLI commands go through the outbox queue (see Architecture), so
the bot must be running; the invisible readers and `status.py` are standalone. Get channel IDs from
`channels.json` (written each boot).

### CLI - the whole capability suite

| command | what it does |
|---------|--------------|
| `python benham.py do list` | Catalogue every action, grouped by tier. `--tier destructive` to filter. |
| `python benham.py do help <action>` | One action's parameters, which are required, and its tier. |
| `python benham.py do <action> key=value ...` | Run it. Values are parsed as JSON when they look like it, so `fields='[{...}]'` works. |

<!-- GENERATED:count -->
`do` covers all 51 registered capabilities and replaces the need for a script per action. The older single-purpose CLIs below still work and route through their original code paths.
<!-- /GENERATED:count -->

### CLI - write to Discord (via the outbox; bot must be running)

| command | what it does |
|---------|--------------|
| `python benham.py send <channel_id> "msg"` | Send a message to a channel. |
| `python benham.py draft <target_channel_id> "msg"` | Post a labeled DRAFT to Testing #asd for review, and print the `send.py` command to deliver it for real (review-first flow). |
| `python benham.py delete <channel_id> <message_id>` | Delete one specific message (its own always; others need Manage Messages). Permanent. |

Bulk delete-by-age is the legacy `purge` outbox action inside `bot.py` (`poll_outbox`); the newer
`do.py purge_messages` adds author/text filters and goes through the dry-run + confirm gate.

### CLI - read from Discord

| command | what it does |
|---------|--------------|
| `python benham.py fetch <channel_id> [limit=20]` | Pull recent history into `outbox/sent/<name>_result.json` (via the running bot). |
| `python benham.py catchup <channel_id> [limit=40]` | Invisible one-shot: print one channel's recent messages, post nothing, exit. |
| `python benham.py read_history [limit=100]` | Invisible one-shot: same, across every non-Testing guild at once. |
| `python benham.py do find_user query=<name>` | Turn a name, @mention or id into the `user_id` every other action wants. Searches every server unless given `guild_id=`. |
| `python benham.py do read_attachments channel_id=<id> message_id=<id>` | Download that message's files into `downloads/<message_id>/`; text files come back with their contents. `save=false` to look without keeping. |
| tail `state/inbox.jsonl` | Live feed of every message the running bot sees (one JSON per line). |

### CLI - ops

| command | what it does |
|---------|--------------|
| `python benham.py status` | Read-only health check: process/PID, guilds seen, last login. Touches no Discord. |
| `supervise_bot.bat` | Restart-on-crash wrapper for always-on running, for running by hand. Shim over `supervise_bot.ps1`, which the `benham-bot` logon task launches directly and windowless. |
| `tray_bot.ps1` | Tray icon showing supervisor/bot state. A monitor only - closing it does not stop anything. |

### In-Discord slash commands

Registered per Discord guild via `command_guilds` in `exaroton_watch.json`. Backed by
`exaroton_ops.py`.

| command | what it does |
|---------|--------------|
| `/server status <server>` | Show a server's status and player count. |
| `/server start <server>` | Start a server. |
| `/server stop <server>` | Stop a server. |
| `/server restart <server>` | Restart a server. |

**What can be controlled is gated per Discord guild, not per user** (`command_guilds`):
- `servers`: which exaroton servers that guild may see/control - `"*"` for all, or a list of IDs.
  The `<server>` argument only autocompletes to these; anything else is rejected.
- `require_operator`: if `true`, `start`/`stop`/`restart` also need an **operator** (a user ID in
  `owner_ids`, or anyone with Administrator / Manage Server); if `false`, anyone in that guild may
  control the whitelisted servers. `status` is always open within the whitelist.

Current setup: **Testing Server** = all servers, operators required; **Chillbar** = Isle of Berk
only, no operator needed. A background **watchdog** also posts crash / offline / back-online alerts
for servers flagged `watch: true`.

Plain text messages are **not** command-triggered: the bot records what it sees to `inbox.jsonl`
and only engages when addressed - an owner DM or an @-mention, or a guest DM if guest chat is on.
There is no autonomous trigger; the wake-word path that used to be one went with voice.

### Example - reply to a friend, review-first

```
python benham.py catchup 1525016583305429072 15                              # read recent #minecraft-chat
python benham.py draft 1525016583305429072 "world's up at UnknownCaz-Gt25.exaroton.me"   # DRAFT -> Testing #asd
python benham.py send  1525016583305429072 "world's up at UnknownCaz-Gt25.exaroton.me"   # after you eyeball it
```

## Safety model

Every capability decision is made in one place: `policy.py`. A capability declares what it
requires, one function decides, and every entry point must state where the request came from.

This exists because of a specific bug. `identity.agent_allowed()` encoded "the owner cannot drive
the agent from Chillbar", was asserted by a passing test, and was **dead code** - `bot.on_message`
re-implemented a subset inline and left the guild check out. The test was green while the live
code did the opposite. A gate that is written but not wired is indistinguishable, from the suite,
from a gate that works. So the design goal is narrow: make that shape impossible.

**Origins.** Where a request arrived from, not who sent it - the same person carries different
assurance by channel. `OWNER_DM`, `OWNER_GUILD`, `OWNER_VOICE`, `LOCAL_CLI`, `SYSTEM`. A call with
no context, or an unrecognised origin, is **denied** - so threading a new call site wrong fails
loudly instead of permitting silently. `SYSTEM` is opt-in per capability, so an automated caller
reaches only what named it (today: `set_presence`).

**Two phases.** `authorize()` answers the caller rules with no network access at all; a refusal
never resolves a channel and never reports anything about one. `authorize_target()` runs afterwards
on the resolved target, because "may this touch that guild" is not answerable until the parameters
are validated.

| rule | phase | verdict |
|---|---|---|
| `context_present` | caller | deny - no context, no decision |
| `owner` | caller | deny - human origins must be Tyler |
| `origin_allowed` | caller | deny - the capability must permit this route |
| `agent_guild` | caller | deny - guild mentions need `agent_guilds` |
| `blocked_when_tainted` | caller | deny - `pc_task` after reading strangers' text |
| `destructive_guild` | target | deny - tier 3 outside `destructive_guilds` |
| `posting_scope` | target | deny - content outside `post_guilds` |
| `always_confirm` | target | **confirm** - destructive + role changes |
| `outward_tainted` | target | **confirm** - outward action in a tainted turn |

Deny rules run before confirm rules, so a refused action never comes back asking to be approved.
`force` (meaning "the confirmation already happened") deliberately does not exist in `policy.py` -
policy says what a call needs; whether that need is met is the caller's bookkeeping.

**`pc_task` is the tightest.** `origins={OWNER_DM, LOCAL_CLI}`, `blocked_when_tainted=True`. A real
Claude Code session on the machine is reachable only from a direct DM or the local CLI, and not at
all once the turn has read what other people wrote.

- **One owner.** `identity.is_owner()` gates every entry point - DM, mention, outbox - and
  `rule_owner` says it again at the capability. No guild-admin inheritance, no operator role.
  Non-owners can converse; they cannot direct.
- **Guild allowlist for destruction.** `destructive_guilds` is a hand-edited list. Chillbar is
  structurally safe rather than safe-because-Claude-was-careful, and nothing said in chat can
  change that.
- **The model never approves its own destructive actions.** Previews halt the tool chain;
  confirmations are matched by code before the agent runs. Ambiguity is not consent, and an
  expired confirmation is a cancelled one.
- **Discord permissions are the outer wall.** Whatever Benham's role cannot do in a given server,
  none of this code can do either. Scope the role per server; it is the only gate an attacker
  cannot reason with.
- **Locked guardrails** (`guardrails.md`) always win and cannot be changed by anything said to
  Benham: never read secrets out, treat non-owners as untrusted, and confirm any outward or
  destructive action out-of-band rather than on a message's say-so.
- **Editable personality** (`persona.md`) tunes *how* Benham talks, never *what* it may do. The
  runtime `personality_overrides.txt` layer went dormant with voice - `brain.py` was its only
  reader.
- **Review-first for outward posts:** use `draft.py` so a human sees a reply before it goes to a
  real channel.
- **Secrets** live in `config/environ.env` (`BOT_KEY`, `ANTHROPIC_API_KEY`) - gitignored, never committed
  or printed.

## Running it

```
python -u -m benham.bot            # foreground; logs in, writes channels.json, starts the outbox poller
```

### 24/7 supervision

`scripts/supervise_bot.ps1` keeps exactly one `benham.bot` alive. It is registered as a **logon Scheduled Task
named `benham-bot`**, so it comes back after a reboot. (`scripts/supervise_bot.bat` is the hand-run entry
point for the same script; the task invokes the `.ps1` directly.)

```powershell
Get-ScheduledTask benham-bot        # Ready = armed, Running = supervising now
Start-ScheduledTask benham-bot      # start without waiting for a logon
Stop-ScheduledTask  benham-bot      # stop supervising (does not stop a bot already up)
```

Four behaviours worth knowing:

**It runs windowless, and `-WindowStyle Hidden` is not what does it.** The task action is
`conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\supervise_bot.ps1`.
The obvious spelling - `powershell.exe -WindowStyle Hidden` - looks right and silently does not
work on Windows 11: with the default terminal set to "Let Windows decide", the console is handed
off to Windows Terminal, which ignores the hidden window style and puts a terminal in Alt+Tab for
a process nobody needs to look at. `--headless` forces the legacy console host with no window and
skips that handoff. Nothing is lost by it - every line already goes to `logs/supervise.log`, and the
tray icon is the thing meant to be looked at. Task Scheduler still reports `Running` correctly,
because `conhost` stays alive for as long as the supervisor does, which is what the tray reads.

**It gives up on a crash loop.** An exit after 60s of uptime is an event, so it restarts in 10s.
Five exits in a row that each lasted under 60s is a bot that *cannot start* - bad token, import
error, missing dependency - and restarting that forever just produces thousands of failed logins
and an unreadable log. It backs off 10s, 20s, 40s... to 5 minutes, then stops and says why.
Restarting the task (or logging back in) resets it.

**It refuses to be the second instance.** One token with two gateways means duplicate replies and
duplicate outbox actions. Guarded by a named mutex against another supervisor, and by a process
check against a `bot.py` someone started by hand - in either case it logs the conflict and exits
1 rather than connecting.

**Its log rotates.** `logs/supervise.log` holds the supervisor's own lines plus all bot output, and
moves aside to `supervise.log.1` past 5MB - one generation, the same convention
`jsonio.rotate_if_large` uses for transcripts.

The limit it cannot cover: if the machine sleeps, the supervisor sleeps with it.

To run the bot in the foreground instead, stop the task first - otherwise the supervisor restarts
the one you just stopped, or refuses to start alongside yours.

### Tray icon

`tray_bot.ps1` puts Benham's state in the notification area, registered as a second logon task
`benham-bot-tray`. It is a **monitor, not the supervisor** - it polls every 5s and owns no state,
so closing it changes nothing about whether the bot runs. Supervision staying headless is
deliberate: the process that restarts the bot should be the least interesting one on the machine,
not the one drawing pictures.

| icon | meaning |
|------|---------|
| grey | supervisor not running - nothing will restart the bot |
| green | supervisor running, bot up (tooltip shows pid + uptime) |
| red | supervisor running, bot down - a restart is presumably in flight |

Menu: restart bot (via the supervisor, so there is one restart path rather than two), start/stop
supervisor, open `supervise.log`, full status, and hide the icon.

**Guest chat is deliberately one-way.** The menu can turn it OFF and cannot turn it ON. Off is the
fail-safe direction and worth a panic button; on widens who may talk to Benham, and that stays a
deliberate edit to `control.json` rather than something a right-click can do.

**Windows 11 hides new tray icons.** It lands in the overflow behind the taskbar's `^` chevron -
drag it onto the taskbar to pin it. A balloon on startup says so, since otherwise a working app
looks like a broken one.

Requires Python 3.12 and the four packages in `requirements.txt`.
Reading message text needs the privileged Message Content intent enabled in the Discord Developer
Portal.

## Config files

| file | purpose | committed? |
|------|---------|-----------|
| `config/environ.env` | tokens (`BOT_KEY`, `ANTHROPIC_API_KEY`, exaroton) | no (gitignored) |
| `config/control.json` | owner ids, destructive/agent guild allowlists, agent model, intents | no (see `.example`) |
| `config/exaroton_watch.json` | `/server` command guilds + watchdog | no (see `.example`) |
| `config/webhooks.json` | webhook URLs | no (gitignored) |
| `prompts/guardrails.md` / `prompts/persona.md` | locked safety rules + the one shared personality | yes |
| `prompts/guest_persona.md` / `prompts/guest_guide.md` | guest-facing prompt + the doc guests are onboarded with | yes |
| `state/channels.json` | guild/channel IDs (written each boot) | no |
| `state/agent_memory.json` | per-conversation history | no (gitignored - private) |
| `state/guest_memory.json` / `state/guest_usage.json` | guest conversations + daily counters | no (gitignored - private) |
| `logs/` | supervise.log + boot captures and run logs | no (gitignored) |

Friend-server reads and derived data (`read_full*`, `*.u8`, `*.tsv`) are gitignored so private chat
is never committed.

### Guests

Benham takes direction from one person. Guests are the one exception, and a narrow one: a
whitelisted non-owner can hold a **conversation** with Claude by DM, and can do nothing else.

The property that makes this safe is not a rule, it is an absence. `guest.py` calls the Messages
API with **no `tools` argument** - not an empty list, not a filtered one. So "can a guest reach
capability X" has the same answer for every one of them, for `pc_task`, and for anything added
later, without that code knowing what a capability is. (A tool loop DID exist beside this from
2026-08-04 to 2026-08-16 - guests could run code and keep files. It was archived unused; see
`archive/guest-tools/`. The grant machinery it needed is still in place and still grants nothing,
which the boot banner asserts out loud.)

Two independent denials back it up in `policy.py`, either of which would be sufficient:

- `Origin.GUEST_DM` is in `Origin.HUMAN`, so `rule_owner` refuses it
- `Origin.GUEST_DM` is **not** in `DEFAULT_ORIGINS`, so `rule_origin_allowed` refuses it too

A capability written next year inherits both without anyone remembering guests exist.

Other properties worth knowing:

- **DM only.** A mention in a guild is not a guest route.
- **Separate memory file.** `guest_memory.json`, not a prefixed key in `agent_memory.json`, so a
  guest and Tyler cannot end up in one thread through a typo.
- **Its own prompt.** `guest_persona.md`, because `persona.md` names Tyler, says the model has
  real tools, and describes operating his machine - all wrong here, the last one dangerously so.
- **Directives stripped, never applied.** A reply's `<<persona: ...>>` is removed rather than
  acted on (`core/directives.py`). Nothing applies one since voice was archived, but stripping
  stays load-bearing: the persona still describes the syntax, so an unstripped `<<...>>` would
  reach a friend's DM as a leaked internal.
- **The owner is never a guest**, even if his id is added to the list.
- **Capped**, because every guest message bills Tyler: per-guest daily, global daily, and a
  cooldown. `check()` reserves the message under a lock rather than reading the counter and
  spending it later, so concurrent turns cannot both pass the same cap; a turn that then fails
  is refunded.

Turn it on by editing `control.json` (see `_guest` in `control.json.example`) and restarting.
`python benham.py guest status` shows the allowlist, caps and today's spend; `python benham.py guest forget
<user_id>` drops one conversation.

**Turning it off needs a restart too.** `control.json` is read once at import, so `enabled:
false` - or removing an id - does nothing to a running bot. To cut a guest off immediately,
bounce the process. Every other control-plane setting behaves this way (`owner_ids`,
`destructive_guilds`); giving this one live reload would mean two different answers in the
codebase to "who may talk to Benham", which is a worse problem than the delay.

`test_guest.py` drives the real `bot.on_message` rather than a helper, because the failure this
codebase already had once was a gate that was written, tested and green while the live path went
somewhere else. It asserts that a guest saying "yes" cannot fire a pending tier-3 confirmation or
approve a suspended Claude Code permission request - and includes an owner case to prove those
assertions are not passing merely because nothing ran.

### Logs

Everything lives in `logs/` since the source reorg: `supervise.log` (supervisor lines + all
bot output, rotated at 5MB) and the `boot<N>.out` / `.err` captures. `python benham.py usage --all`
still sees the full history; pruning `logs/` is what actually discards it.

### Attachments

**Sending.** `send_file` takes `path=` for one file or `paths=` for several (Discord's limits are
10 files and 25MB per message, both checked locally so the failure names the file rather than
arriving as a bare 413). `dm_user` takes the same parameters, and its `content` is optional when a
file is attached. Both are tier 1 and `outward`, so once Benham has read anything a third party
wrote, sending a file needs an explicit confirmation - and that preview **names the files**, which
is the only reason it is a meaningful approval. `send_file` is also `posts`, so the posting
allowlist caps which servers it can reach at all.

**Reading.** `read_attachments channel_id= message_id=` downloads what one message carries into
`downloads/<message_id>/` (gitignored) and returns each file's name, size and type; text files come
back with their contents inline, capped at 20k characters. `save=false` reports without keeping
anything, `index=` picks one file, `max_bytes=` raises the 8MB-per-file default.

It is tier 0 because three properties make a mistaken call cost nothing, and the tier only holds
while all three do:

1. **No `url` parameter.** It fetches what a *named message* carries. A general "download this URL"
   tool would be aimed by whatever text Benham last read, which is the exact shape of attack the
   injection defences exist to refuse.
2. **Bytes land only under `downloads/`, under a name this module rewrites.** The uploader controls
   that filename and nothing else - `..\..\Windows\System32\evil.dll` is a legal thing to call a
   Discord upload, and `CON.txt` is a device rather than a file. The rewrite is reported in the
   result rather than done silently, and a final `realpath` check refuses anything that still
   resolves outside the folder.
3. **Nothing is ever run.** A Windows-runnable extension is flagged `executable: true` and written
   anyway. Flagging rather than blocking is deliberate: a blocklist would refuse ordinary work
   (`.jar` mod files) while preventing nothing, since the file is only ever bytes on disk.

Everything it returns is `taints`-marked, so the agent wraps it in `<untrusted-data>` markers before
the model sees it - a crash log someone uploaded is information to report, not instructions.

### Privileged intents

`members` and `presences` default to **off** in `control.json`. discord.py refuses to log in at all
if it requests an intent the Developer Portal has not granted, so defaulting them on would brick
the bot on the next restart. Turning them on costs two clicks in **Dev Portal → Bot → Privileged
Gateway Intents** plus flipping the flag; leaving them off only disables `list_members` and
`who_is_online`, which say so plainly rather than failing oddly.

`find_user` is the one capability that degrades instead of failing. With `members` on it scans the
member cache and matches anywhere in a username, display name or nickname. With it off it falls
back to a gateway query, which Discord permits without the intent but only matches the **start** of
a name - so `caz` finds `caz6666` but never `UnknownCaz`. Every result from that path carries a
`note` saying so, because a prefix search that returns two of the three matching people looks
exactly like one that returned all of them.

### Cost

The agent sends 49 tool schemas plus the system prompt on every call - about 7.4k tokens of static
prefix. That prefix is marked with `cache_control`, so after the first call each turn reads it from
cache at a tenth of the price (measured: 7341 written once, then 7341 read). Conversation history
is stored as plain text pairs, not raw tool results, so a long phone conversation does not drag
whole channel dumps along with it.

## Testing

```
python tests/test_control.py      # gates, allowlists, confirm matching, agent history shape
python tests/test_owner_gate.py   # drives the real bot.on_message with fake messages
python tests/test_injection.py    # can text someone else wrote make Benham act?
python tests/test_policy.py       # every capability x every origin, plus the rule matrix
python tests/test_memory.py       # what gets stored is what was actually said
python tests/test_selfrecord.py   # "what did I do?" answered from the log, not memory
python tests/test_guest.py        # the guest lane's gate, caps and refusals
python tests/test_pc_reply.py     # pc.. reading the message a DM replies to
python tests/test_attachments.py  # attachments in and out
python tests/test_find_user.py    # name -> user id, both implementations
python scripts/gen_readme.py --check   # this file's generated blocks are current
```

Deliberately offline with stub clients - "does it refuse to purge Chillbar" is not a thing you want
to verify by trying it in Chillbar.

**Three of these exist because of mistakes worth not repeating**, and they are the same mistake
wearing different clothes: a test can be green about the wrong thing.

- `test_owner_gate` drives the real handlers rather than the helper functions, because the
  original bug was a helper that passed while nothing called it.
- `test_injection`'s watcher records **every** invocation, not just forced ones - an earlier
  version watched the wrong flag and reported a pass while the action it guarded actually ran.
- `test_memory` asserts on the bytes that reach disk. `test_injection` drives that same code path
  and spent twelve days corrupting the stored history **while passing**, because it only ever
  asked which tools fired. Its last check is shape-only, so it fails on any future echo pair
  whatever causes one.

`gen_readme.py --check` belongs in that list for the same reason. The counts in this file were
hand-typed and drifted to 50-documented against 59-registered; they are generated now, and
`--check` fails loudly instead of letting them rot.

## Outside the chokepoint

Two things deliberately do not go through `policy.py`, and it is worth knowing which:

- **`webhook.py`** posts via a saved webhook URL with no bot involved at all - no gateway, no
  token, works while `bot.py` is stopped. It exists because Benham is only in Testing and Chillbar,
  while the Isle of Berk changelog channel is in a server it was never invited to; `send_message`
  physically cannot reach it. Nothing in the codebase calls it. The cost of leaving it out is a
  hole in the audit trail: a webhook post writes no log line anywhere, so `bot.log` will not show
  it. The agent cannot reach it either - only a `pc_task` shell command could, which needs a DM
  origin, an untainted turn, and a per-command approval.
- **The exaroton watchdog** posts crash alerts straight through `channel.send`.
