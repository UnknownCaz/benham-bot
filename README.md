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

## Capabilities

Text (via the always-on bot):
- `send.py <channel_id> "msg"` - send a message.
- `draft.py <target_channel_id> "msg"` - post a labeled DRAFT to the Testing review channel and
  print the exact `send.py` command to deliver it for real once a human approves (review-first flow).
- `delete.py <channel_id> <message_id>` - delete one specific message (its own always; others need
  Manage Messages). Bulk cleanup by age is the `purge` action in `bot.py`.
- `fetch.py <channel_id> [limit]` - pull recent history into a result file.
- `inbox.jsonl` - tail it for live incoming messages.

Read-only (log in invisible, post nothing, exit):
- `catchup.py <channel_id> [limit]` - print one channel's recent messages.
- `read_history.py [limit]` - same across every non-Testing guild at once.

Voice:
- `speak.py <voice_channel_id> "text"` - join, speak via edge-tts neural voices, leave.
- `listen.py <voice_channel_id>` / `stoplisten.py` - transcribe speech (faster-whisper) into
  `voice_transcript.jsonl`; works on discord.py 2.7 via a DAVE-decryption patch in `bot.py`.
- A named "B-voice" roster (`voices.json`) is switchable by voice; wake words "benham"/"claude".

Minecraft servers (`/server` slash commands + a watchdog):
- `/server status|start|stop|restart` (power actions gated to operators).
- A background watchdog alerts on crash / unexpected-offline / back-online for watched servers.
- Ops layer: `exaroton_ops.py`; config: `exaroton_watch.json`.

Ops:
- `status.py` - read-only health check (process up? guilds seen? AUTO_REPLY state? last login).

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
