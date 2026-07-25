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

## Commands

Two kinds: **CLI commands** run in a terminal (from the repo directory), and **in-Discord slash
commands** typed in a channel. Most CLI commands go through the outbox queue (see Architecture), so
the bot must be running; the invisible readers and `status.py` are standalone. Get channel IDs from
`channels.json` (written each boot).

### CLI - write to Discord (via the outbox; bot must be running)

| command | what it does |
|---------|--------------|
| `python send.py <channel_id> "msg"` | Send a message to a channel. |
| `python draft.py <target_channel_id> "msg"` | Post a labeled DRAFT to Testing #asd for review, and print the `send.py` command to deliver it for real (review-first flow). |
| `python delete.py <channel_id> <message_id>` | Delete one specific message (its own always; others need Manage Messages). Permanent. |

Bulk delete-by-age is the `purge` outbox action inside `bot.py` (`poll_outbox`), not a CLI script.

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

Typed in a channel of the **Testing Server** - they are guild-scoped there (synced to `guild_id` in
`exaroton_watch.json`), so they do not appear in other servers. Backed by `exaroton_ops.py`.

| command | who | what it does |
|---------|-----|--------------|
| `/server status <server>` | anyone | Show a server's status and player count. |
| `/server start <server>` | operators | Start a server. |
| `/server stop <server>` | operators | Stop a server. |
| `/server restart <server>` | operators | Restart a server. |

`<server>` autocompletes from `exaroton_watch.json`. **Operator** = a Discord user ID in `owner_ids`,
OR anyone with Administrator / Manage Server in that guild. A background **watchdog** also posts
crash / offline / back-online alerts for servers flagged `watch: true`.

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
| `exaroton_watch.json` | `/server` + watchdog + `auto_reply_guilds` | no (see `.example`) |
| `channels.json` | guild/channel IDs (written each boot) | no |
| `voices.json` | named voice roster | yes |
| `guardrails.md` / `persona.md` | system prompt | yes |
| `webhooks.json` | webhook URLs | no (gitignored) |

Friend-server reads and derived data (`read_full*`, `*.u8`, `*.tsv`) are gitignored so private chat
is never committed.
