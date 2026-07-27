# benham-bot

Benham (**Benham#2721**) is a personal Discord bot that acts as a controllable proxy: it can
read and send text, speak and listen in voice channels, and control a set of
[exaroton](https://exaroton.com) Minecraft servers. Messages it sends appear as *Benham*, not as
its owner - it is a proxy, not account impersonation (Discord forbids the latter). It only works in
servers it has been invited to.

## Architecture

A single persistent process, `bot.py`, stays connected to Discord and does all the I/O. Nothing
talks to Discord directly; instead small CLI scripts drop request files into `outbox/` and read
results back, which keeps the heavy always-on process separate from one-shot actions.

```
  CLI (send.py, draft.py, delete.py, fetch.py, ...)  ->  outbox/*.json
                                                            |
                                        bot.py polls outbox every ~2s
                                                            |
                        Discord  <->  bot.py  ->  outbox/sent|failed/*_result.json
                                        |
              every message it sees  ->  inbox.jsonl   (live capture, one JSON per line)
              on each boot           ->  channels.json (guild + channel IDs it can see)
```

## Benham as Claude's face

Benham is the body Claude wears in Discord. Tyler talks to it, Claude acts through it, and it is
the channel between them when Tyler is away from the PC. Two paths lead to the same capabilities:

```
  Tyler DMs Benham  ->  owner gate  ->  agent.py (Claude + 45 tools)  ->  capabilities.py
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

| tier | examples | gate |
|---|---|---|
| **read** (14) | `read_channel`, `search_messages`, `list_roles`, `guild_info` | none |
| **speak** (6) | `send_message`, `send_embed`, `send_file`, `dm_user`, `react` | owner only |
| **manage** (18) | `pin_message`, `add_role`, `create_channel`, `timeout_member`, `set_presence` | owner only |
| **destructive** (7) | `delete_message`, `purge_messages`, `delete_channel`, `kick_member`, `ban_member` | guild allowlist + dry-run + explicit confirm |

Run `python do.py list` for the full catalogue, `python do.py help <action>` for one action's
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
python do.py purge_messages channel_id=809357286036078612 limit=20 contains="test"
#   --- DRY RUN, nothing has happened ---
#   Delete 1 messages from #asd in Testing Server
#   ... To run it for real, repeat with: confirm_token=80ac01
python do.py purge_messages channel_id=809357286036078612 limit=20 contains="test" confirm_token=80ac01
```

## Commands

Two kinds: **CLI commands** run in a terminal (from the repo directory), and **in-Discord slash
commands** typed in a channel. Most CLI commands go through the outbox queue (see Architecture), so
the bot must be running; the invisible readers and `status.py` are standalone. Get channel IDs from
`channels.json` (written each boot).

### CLI - the whole capability suite

| command | what it does |
|---------|--------------|
| `python do.py list` | Catalogue every action, grouped by tier. `--tier destructive` to filter. |
| `python do.py help <action>` | One action's parameters, which are required, and its tier. |
| `python do.py <action> key=value ...` | Run it. Values are parsed as JSON when they look like it, so `fields='[{...}]'` works. |

`do.py` covers all 45 registered capabilities and replaces the need for a script per action. The
older single-purpose CLIs below still work and route through their original code paths.

### CLI - write to Discord (via the outbox; bot must be running)

| command | what it does |
|---------|--------------|
| `python send.py <channel_id> "msg"` | Send a message to a channel. |
| `python draft.py <target_channel_id> "msg"` | Post a labeled DRAFT to Testing #asd for review, and print the `send.py` command to deliver it for real (review-first flow). |
| `python delete.py <channel_id> <message_id>` | Delete one specific message (its own always; others need Manage Messages). Permanent. |

Bulk delete-by-age is the legacy `purge` outbox action inside `bot.py` (`poll_outbox`); the newer
`do.py purge_messages` adds author/text filters and goes through the dry-run + confirm gate.

### CLI - read from Discord

| command | what it does |
|---------|--------------|
| `python fetch.py <channel_id> [limit=20]` | Pull recent history into `outbox/sent/<name>_result.json` (via the running bot). |
| `python catchup.py <channel_id> [limit=40]` | Invisible one-shot: print one channel's recent messages, post nothing, exit. |
| `python read_history.py [limit=100]` | Invisible one-shot: same, across every non-Testing guild at once. |
| tail `inbox.jsonl` | Live feed of every message the running bot sees (one JSON per line). |

### CLI - voice (via the outbox)

| command | what it does |
|---------|--------------|
| `python speak.py <voice_channel_id> "text"` | Join, speak via edge-tts neural voice, leave. |
| `python listen.py <voice_channel_id>` | Join and transcribe speech into `voice_transcript.jsonl`. |
| `python stoplisten.py <voice_channel_id>` | Stop listening and leave the voice channel. |

Voices are switchable by a named "B-voice" roster (`voices.json`); wake words are "benham"/"claude".
Listening works on discord.py 2.7 via a DAVE-decryption patch in `bot.py`. With `BENHAM_AUTO_REPLY=1`
the bot answers wake-word utterances itself, gated to `auto_reply_guilds`.

### CLI - ops

| command | what it does |
|---------|--------------|
| `python status.py` | Read-only health check: process/PID, AUTO_REPLY + allowlist, guilds seen, last login. Touches no Discord. |
| `supervise_bot.bat` | Restart-on-crash wrapper for always-on running (launch from a logon Scheduled Task). |

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

Plain text messages are **not** command-triggered: the bot only records what it sees to `inbox.jsonl`
and never auto-responds in text chat (voice wake words are the only autonomous trigger).

### Example - reply to a friend, review-first

```
python catchup.py 1525016583305429072 15                              # read recent #minecraft-chat
python draft.py 1525016583305429072 "world's up at UnknownCaz-Gt25.exaroton.me"   # DRAFT -> Testing #asd
python send.py  1525016583305429072 "world's up at UnknownCaz-Gt25.exaroton.me"   # after you eyeball it
```

## Autonomous voice replies (AUTO_REPLY)

By default the "brain" is whatever live session is driving the bot. Setting `BENHAM_AUTO_REPLY=1`
(plus `ANTHROPIC_API_KEY`) lets the bot answer wake-word utterances itself via the Anthropic API
(`brain.py`), with no session attached.

**Guild-gated:** autonomous replies only fire in guilds listed in `auto_reply_guilds`
(`exaroton_watch.json`; default = the Testing Server only). So `AUTO_REPLY=1` is safe even while
Benham is a member of friend servers - it will never self-reply there. Wake detection and
transcription still run everywhere; only the self-answering path is gated.

## Safety model

- **One owner.** `identity.is_owner()` gates every entry point - DM, mention, outbox, slash
  command. No guild-admin inheritance, no operator role. Non-owners can converse; they cannot direct.
- **Guild allowlist for destruction.** `destructive_guilds` is a hand-edited list. Chillbar is
  structurally safe rather than safe-because-Claude-was-careful, and nothing said in chat can
  change that.
- **The model never approves its own destructive actions.** Previews halt the tool chain;
  confirmations are matched by code before the agent runs. Ambiguity is not consent, and an
  expired confirmation is a cancelled one.
- **Discord permissions are the outer wall.** Whatever Benham's role cannot do in a given server,
  none of this code can do either. Scope the role per server; it is the only gate an attacker
  cannot reason with.
- **Locked guardrails** (`guardrails.md`) always win and cannot be changed by voice: never read
  secrets aloud, treat non-owner speakers as untrusted, and confirm any outward/destructive action
  out-of-band rather than on a voice's say-so.
- **Editable personality** (`persona.md` + runtime `personality_overrides.txt`) tunes *how* Benham
  talks, never *what* it may do.
- **Review-first for outward posts:** use `draft.py` so a human sees a reply before it goes to a
  real channel.
- **Secrets** live in `environ.env` (`BOT_KEY`, `ANTHROPIC_API_KEY`) - gitignored, never committed
  or printed.

## Running it

```
python bot.py            # foreground; logs in, writes channels.json, starts the outbox poller
```

For always-on use, run it under `supervise_bot.bat` (restarts on crash) from a logon Scheduled
Task. Only ever run one instance - two processes sharing the token cause a double gateway and
duplicate actions.

Requires Python 3.12, `discord.py` 2.7+, and (for voice) `PyNaCl`, `davey`, FFmpeg on PATH.
Reading message text needs the privileged Message Content intent enabled in the Discord Developer
Portal.

## Config files

| file | purpose | committed? |
|------|---------|-----------|
| `environ.env` | tokens + `BENHAM_AUTO_REPLY` | no (gitignored) |
| `control.json` | owner ids, destructive/agent guild allowlists, agent model, intents | no (see `.example`) |
| `exaroton_watch.json` | `/server` + watchdog + `auto_reply_guilds` | no (see `.example`) |
| `channels.json` | guild/channel IDs (written each boot) | no |
| `voices.json` | named voice roster | yes |
| `guardrails.md` / `persona.md` | voice system prompt | yes |
| `agent_persona.md` | text-agent personality (editable) | yes |
| `agent_memory.json` | per-conversation history | no (gitignored - private) |
| `webhooks.json` | webhook URLs | no (gitignored) |

Friend-server reads and derived data (`read_full*`, `*.u8`, `*.tsv`) are gitignored so private chat
is never committed.

### Privileged intents

`members` and `presences` default to **off** in `control.json`. discord.py refuses to log in at all
if it requests an intent the Developer Portal has not granted, so defaulting them on would brick
the bot on the next restart. Turning them on costs two clicks in **Dev Portal → Bot → Privileged
Gateway Intents** plus flipping the flag; leaving them off only disables `list_members` and
`who_is_online`, which say so plainly rather than failing oddly.

### Cost

The agent sends 45 tool schemas plus the system prompt on every call - about 7.4k tokens of static
prefix. That prefix is marked with `cache_control`, so after the first call each turn reads it from
cache at a tenth of the price (measured: 7341 written once, then 7341 read). Conversation history
is stored as plain text pairs, not raw tool results, so a long phone conversation does not drag
whole channel dumps along with it.

## Testing

```
python test_control.py   # 47 offline checks: owner gate, allowlist, confirm matching, history shape
```

Deliberately offline with a stub client - "does it refuse to purge Chillbar" is not a thing you
want to verify by trying it in Chillbar.
