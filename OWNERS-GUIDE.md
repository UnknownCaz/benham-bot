# Benham — Owner's Quick Guide

Tyler's cheat-sheet. Task-oriented: find what you want to do, do it. The **why**
behind everything lives in README.md; the guest refactor design lives in
PLAN-guest-permissions.md. Sections marked **[PLANNED]** are from that refactor and
**do not work yet** — they'll be unmarked as stages ship.

---

## Daily driving

**Just DM Benham.** No prefixes, no commands. The agent decides whether to answer,
act, or both — "what's going on in #general" reads it, "post X in #general" posts.

| you want | do |
|---|---|
| Discord stuff (read/send/manage/anything) | DM it in plain language |
| something on the PC | DM starting with `pc..` (goes straight to a Claude Code session, no agent round-trip) — or just ask normally and the agent delegates to `pc_task` |
| drive it from a guild channel | @mention it — only works in guilds on `agent_guilds` (currently Testing only) |
| talk without it acting | just talk; it won't act unless asked |

**When it asks you to confirm:** destructive actions (delete/purge/kick/ban) and
role changes always come back as a preview + token first. Reply naming the token to
fire. A stray "yes" without the token does nothing, on purpose. Previews expire
(`confirm.ttl_seconds`, currently 1h) — expired = cancelled, never assumed yes.

**"Why is it asking approval for a normal send?"** The turn is *tainted* — Benham
read something a non-owner wrote (a channel, a nickname, a forwarded message, **or
a web search result**). Outward actions then need your yes. Not a bug. Want it free
again? Ask in a fresh message that doesn't read stranger content first.

**Web search (yours).** Just ask for current info — it searches when it's actually
needed. Queries land in `agent_searches.jsonl`. The trade-off worth knowing: **a
turn that searched is tainted**, same as reading a channel, because a web page is
text a stranger wrote (and cheaper to publish than a Discord message is to post).
So "search then post it in #general" will ask you to confirm the post, and `pc_task`
is blocked until a fresh message. Deliberate. `agent.web_search: false` in
`control.json` turns search off if you'd rather have the ungated tool loop.

**"Why did it refuse outright?"** Common ones:

| refusal mentions | fix |
|---|---|
| destructive_guilds | that guild isn't on the tier-3 allowlist in `control.json` — edit by hand + restart, no confirmation can unlock it |
| posting allowlist / post_guilds | hard scope cap on where Benham may put content; edit `control.json` + restart |
| agent_guilds | you mentioned it in a guild not on the agent list — DM instead |
| "already read content other people wrote" | tainted turn; `pc_task` is flat-blocked then — fresh message |
| unknown origin / no call context | a code path bug — that's a "bug Claude about it" one |

---

## PC tasks (`pc..` / pc_task)

- Config: `pc` block in `control.json` (`enabled`, workdir = `Benhams-inbox`,
  `permission_timeout_seconds` 300, `use_api_key`).
- **Read freely, ask before changing**: reads run instantly; every write/command
  DMs you the full command and blocks until you answer. No answer in 5 min = denied.
- Watch live: `python watch_pc.py` (follow), `--last` (replay), `--list`.
- DM-only + never from a tainted turn. That's the design, not a bug.
- Remember: workdir is a starting point, **not a sandbox**; reads are free, so
  credential reads are logged loud (`SECRET-READ` in the log) rather than blocked.
- Billing: `use_api_key: false` right now = bills the Claude subscription.

## CLI cheat-sheet

Run from the repo directory. Most need `bot.py` running (outbox queue); the
invisible readers and `status.py` don't. Channel ids: `channels.json`.

**The universal one:**

```
python do.py list                    # every capability, by tier
python do.py help <action>           # its params
python do.py <action> key=value ...  # run it (destructive → dry-run + confirm_token)
```

**Everyday shortcuts:**

| command | does |
|---|---|
| `python send.py <channel_id> "msg"` | send |
| `python draft.py <channel_id> "msg"` | review-first: DRAFT to Testing #asd, prints the real send command |
| `python dm.py <user_id> "msg"` (or `--tyler`) | DM someone |
| `python delete.py <channel_id> <message_id>` | delete one message (permanent) |
| `python fetch.py <channel_id> [limit]` | pull history → `outbox/sent/…_result.json` |
| `python catchup.py <channel_id> [limit]` | invisible read of one channel, prints, exits |
| `python read_history.py [limit]` | invisible read of every non-Testing guild |
| `python do.py find_user query=<name>` | name → user_id |
| `python do.py read_attachments channel_id=.. message_id=..` | files → `downloads/<msg_id>/` (quarantine, NOT Windows Downloads) |

**Voice:** `speak.py <vc_id> "text"` / `listen.py <vc_id>` (→
`voice_transcript.jsonl`) / `stoplisten.py <vc_id>`. Wake words "benham"/"claude";
auto-reply only in `auto_reply_guilds`.

**Exaroton (in Discord):** `/server status|start|stop|restart <server>` — per-guild
gating in `exaroton_watch.json` (Testing = all + operator required; Chillbar = Isle
of Berk, anyone). Watchdog posts crash/offline alerts for `watch: true` servers.

## Health, logs, cost

| question | answer |
|---|---|
| is it up? | `python status.py` (read-only, instant) |
| what's it costing? | `python usage.py --today` (or `--all`) |
| what's it seeing live? | tail `inbox.jsonl` |
| what's it doing on the PC right now? | `python watch_pc.py` |
| restart it | it's the `benham-bot` logon Scheduled Task: `Stop-ScheduledTask benham-bot` / `Start-ScheduledTask benham-bot`; hand-run = `supervise_bot.bat`; tray icon = monitor only |
| boot logs | `boot<N>.out/.err` in repo root, older in `logs/` |

## Guest admin (today: chat mode)

Guests = whitelisted friends who can DM Claude-through-Benham. Chat + web search
only; zero client tools, zero Discord reach, zero PC reach.

| task | how |
|---|---|
| add/remove a guest | edit `guest.ids` in `control.json`, **then restart** — config reads once at boot, both directions |
| cut someone off NOW | remove id + bounce the bot (restart IS the kill switch) |
| see spend/caps/allowlist | `python guest.py status` |
| wipe a conversation | `python guest.py forget <user_id>` (or `forget-all`) |
| read what they searched | `guest_searches.jsonl` (append-only; searched turns count double) |
| caps | `daily_message_cap` 100/guest, `global_daily_cap` 400, 3s cooldown — all in the `guest` block |
| what guests were told | `guest_guide.md` — keep it honest when features change |

## config knobs (control.json — restart after ANY edit)

| key | is |
|---|---|
| `owner_ids` | who Benham obeys. Everything else flows from this |
| `destructive_guilds` | where tier-3 may run AT ALL (hand-edit only) |
| `agent_guilds` | where @mention drives the agent |
| `post_guilds` / `post_channels` | hard cap on where content can be posted (channels wins if non-empty) |
| `agent.*` | owner agent: model, max_tool_rounds, cooldown, `web_search`, `searches_per_turn` |
| `pc.*` | PC session: enabled, workdir, timeout, billing, `pc..` prefix |
| `guest.*` | everything guest (see above) |
| `confirm.ttl_seconds` | how long a parked confirmation lives |
| `intents.members` | off = find_user is prefix-only (it warns you in results) |

`control.json.example` documents every key with `_comments`. Missing config =
most-restrictive defaults, always.

---

## [PLANNED] Guest refactor — what changes for you when it ships

From PLAN-guest-permissions.md, stages 0–6. **None of this exists yet.** As each
stage lands this section gets unmarked and expanded.

| stage | you get | your admin surface |
|---|---|---|
| ~~1~~ | **SHIPPED** — owner web search (documented above, not planned any more) | — |
| 2–3 | plumbing; guests unchanged | `guest.mode: "workspace"` becomes valid; `guest.capabilities` list appears (empty = today's behaviour) |
| 4 | guests get files: create/read/upload (`ws_import`)/get-back, each confined to `guest_work/<their_id>/` + read-only `guest_work/commons/` you curate | quotas in `guest.workspace` (20MB/guest default); drop files in `commons/` to share with all guests; runnable files (.exe/.bat) refused everywhere |
| 5 | guests can read channels **you list** | `guest.read_channels` — treat listing a channel as publishing it to every guest |
| 6 | guests can run code — on **Anthropic's servers only**, never your PC | `guest.code_execution.enabled` (own switch, default off), runs → `guest_runs.jsonl` |

Three rules that stay true through all of it: a capability reaches guests only if
code marks it AND `guest.capabilities` lists it (config typos can only turn things
OFF); guests never see a confirmation prompt (anything that would ask you is a flat
no for them); nothing a guest does executes on your machine, ever.
