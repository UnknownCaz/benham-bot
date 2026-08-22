# Benham scoping report - three questions, measured
2026-08-21 · read-only pass at commit `2eb2d7f` · no production code written

---

## Before the three: two things found on the way in

**1. INTENT and the board disagree about whether question 1 is even sanctioned.**
INTENT §4 Stage 4 item 14 - *"Split `bot.py` and `capabilities.py` along the same seam"* - is struck through and marked **DROPPED 2026-08-17**, on Tyler's own words (c8: *"Item 14 is starting to seem redundant why do we even need it anymore?"*). What the doc leaves standing is narrower: *"mechanical size reduction along seams that already exist - bot.py's fenced exaroton block is genuinely self-contained."*

The board's recommendation revives the dropped item. The brief's rule (doc wins, code is drift) does not resolve this, because here the doc says **don't** and the board says **do**. That is Tyler's call, not mine. I sized the full split anyway - below - and flagged which slice item 14 as written still authorizes (the exaroton block, 296 lines, 1 half-day).

**2. The suite is 25/26 in a git worktree and 26/26 in the main repo.** `test_dm_guard.py` (the newest test, `9d541e9`) is the one file that does **not** `import _testconfig`, so it reads the real gitignored `config/control.json`. A worktree has none, so `identity` falls back to restrictive defaults, `GUEST_IDS` is empty, `is_guest(DRACO)` is False, three checks fail and `_msg` is None (`TypeError: argument of type 'NoneType' is not iterable`).

This is precisely the trap `_testconfig.py`'s own docstring was written to close - *"the tests were reading their fixture out of a file nobody can commit"* - re-set by the newest test. It also means **`run_tests.py` is red on master's tip for anyone who is not Tyler on his own machine.** The fix is small (import the fixture, swap the two real ids for `_testconfig.GUEST_ID`) but it needs a thought: the fixture whitelists one invented guest, and the test's point is *"not about one person"*, so the fixture needs a second id. **Not fixed here - scoping task.**

---

## Q1. Stage 4 (Structure) - what splitting `bot.py` actually takes

### The map (measured, 2,345 lines)

| Lines | Block | Seam |
|---:|---|---|
| 77 | docstring + imports | - |
| 70 | exaroton watch config, `COMMAND_GUILDS`, `allowed_servers` | clean |
| 93 | intents, `client`, `tree`, `log`, `record_message`, `dump_channels` | **the hub** |
| 226 | `/server` slash group + watchdog (already fenced by comment banners) | clean |
| 103 | `on_ready` | **the hub** |
| 126 | `split_for_discord`, `reply_in`, `react`, `LiveProgress` | clean |
| 164 | `ApprovalView`, `_views`, `retire_view`, `send_with_view`, `ask_owner_dm`, `fire_confirmed` | clean |
| 338 | inbound parsing: `attachment_note`, `resolve_reply`, `_quoted_lines`, `_embed_lines`, `reply_context_block`, `quoted_block`, `pc_label`, `inbound_content`, `strip_mention` | clean |
| 283 | guest lane: `file_guest_report`, `handle_guest_dm` | clean, **test-coupled** |
| 469 | `on_message` | **tangled - see below** |
| 72 | `tick_conversations`, `tick_loopclose` | clean |
| 279 | `poll_outbox`, `_finish`, `before_poll` | clean |
| 45 | `console_utf8`, `configure_logging`, `main` | clean |

### Proposed module list

New package `benham/discord/`, plus one runtime module. **`benham/bot.py` stays as the module tests import** and keeps the hub - this is a constraint, not a preference (see test coupling).

| Module | ~lines | Contents |
|---|---:|---|
| `benham/discord/runtime.py` | 30 | `client`, `tree`, `intents`, `log` - the globals everything binds to |
| `benham/discord/send.py` | 126 | `split_for_discord`, `reply_in`, `react`, `LiveProgress` |
| `benham/discord/inbound.py` | 340 | all message parsing + `inbound_content` |
| `benham/discord/approvals.py` | 164 | `ApprovalView`, `_views`, `retire_view`, `send_with_view`, `ask_owner_dm`, `fire_confirmed` |
| `benham/discord/guestlane.py` | 283 | `file_guest_report`, `handle_guest_dm` |
| `benham/discord/pc_prefix.py` | 180 | the `pc..` fast-path body, lifted whole out of `on_message` |
| `benham/discord/outboxpoll.py` | 279 | `poll_outbox`, `_finish`, `before_poll` |
| `benham/discord/ticks.py` | 72 | `tick_conversations`, `tick_loopclose` |
| `benham/discord/exaroton_cmds.py` | 296 | config + `/server` group + watchdog |
| `benham/runner.py` | 45 | `console_utf8`, `configure_logging`, `main` |
| **`benham/bot.py` (residual)** | **~770** | imports, hub globals, `record_message`, `dump_channels`, `on_ready`, `on_message` (~290 after `pc..` leaves), re-export shims |

2,345 to ~770 in the entry module, ten modules of 30-340.

### What is clean

The dependency direction is already one-way and that is the single best fact here: **nothing in `benham/` imports `benham.bot`** (only `cli/status.py` greps for the process name, and the supervisor scripts launch it). `bot.py` is a leaf. `capabilities.run(client, log, ...)` already takes `client` and `log` as parameters - the core is dependency-injected, not reaching back. Circular-import risk for the split is therefore **low**, with one caveat below.

Module-scope mutable state is small and each cluster moves as a unit: `_wd_*` (6 dicts) belong entirely to the exaroton block; `_views` belongs entirely to approvals. **No global is shared across two proposed modules.**

### What is tangled

**a) `client` / `tree` / `log` are import-time bindings.** `@client.event`, `@tasks.loop(...)`, `@server_group.command(...)` all bind when the module is imported. An extracted module cannot do `from benham import bot` (bot imports it). Fix is `runtime.py` holding the three, imported by both - ~30 lines, and that is why it is first on the list rather than an afterthought.

**b) `on_message` does not split, and should not be asked to.** Its 469 lines are one ordered gate chain where **the order is the security property**, documented at length in the file: *"a guest must never fall through this block. Not 'is filtered out later' - never reaches it."* Splitting it into `handle_owner_dm` / `handle_guild` duplicates the chain, which is exactly how you get two copies that drift. What *does* lift out cleanly is the `pc..` fast path (lines ~1591-1770): a self-contained leaf that returns on every branch. That takes `on_message` to ~290 and is the whole of the win available here. **Recommendation: do not split the gate chain in Stage 4.**

**c) `on_ready` is a boot orchestrator, not a concern.** It touches identity, agent, guest, capabilities, codesession, presence, channels, rooms, three task loops, issues and slash-command sync. It stays in `bot.py` and imports the pieces. Correct as-is; nothing to extract.

### What would force test rewrites

Five test files drive the real `bot.on_message` - **47 call sites** across `test_owner_gate.py`, `test_guest.py`, `test_pc_reply.py`, `test_rich_context.py`, `test_attachments.py`. They monkeypatch module attributes on `bot`:

`bot.log` · `bot.reply_in` · `bot.record_message` · `bot.strip_mention` · `bot.fire_confirmed` · `bot.client` · `bot.agent.respond` · `bot.capabilities.run` · `bot.codesession._pending`

A re-export (`from .send import reply_in`) keeps the patch working **only for calls `on_message` makes directly by that name** - the patch rebinds `bot`'s global and `on_message` reads `bot`'s global. It stops working for calls made from *inside* a moved module, which reads its own namespace.

Two concrete consequences:

- **`guestlane.py` forces a `test_guest.py` edit.** `test_guest.py:555` patches `bot.reply_in` and drives `on_message` into `handle_guest_dm` into `reply_in`. Once `handle_guest_dm` lives elsewhere, that patch is bypassed. ~15 lines of test edit; it fails loudly, which is the good failure mode.
- **`bot.log` is the dangerous one.** `test_guest.py:550` sets `bot.log = lambda *a, **k: None` purely to silence output. If `log` moves to `runtime.py`, that patch **silently stops doing anything** - no test failure, just noise. Not a correctness break, but it is the shape of breakage this refactor can produce: a patch that still assigns an attribute nobody reads. Any moved name a test patches needs an explicit check that the patch still bites.

### Size

| Slice | half-days | confidence |
|---|---:|---|
| `runtime.py` + `send.py` + `ticks.py` + `runner.py` | 1 | **high** - mechanical, no test impact |
| `exaroton_cmds.py` (the only slice item 14 still authorizes) | 1 | **medium** - `app_commands.Group` + `@tasks.loop` across a module boundary is the kind of thing that passes tests and fails at boot; needs a live restart and a guild sync check |
| `inbound.py` | 1 | high - re-exports keep `test_attachments` / `test_rich_context` / `test_msgparts` green; verify only |
| `approvals.py` | 1 | high |
| `pc_prefix.py` | 1 | medium-high - `test_pc_reply.py` (612 lines) drives it through `on_message`, should survive untouched |
| `outboxpoll.py` | 1 | **medium** - the machine channel; needs a live outbox round-trip, not just a green suite |
| `guestlane.py` + the `test_guest.py` patch-target rewrite | 1.5 | medium-high |
| live verification, restart between each (decision #16) | 1 | high |
| **total** | **8.5-9 half-days** (~4.5 dev days) | **medium-high overall** |

Subtract 2.5 half-days if `pc_prefix`, `outboxpoll` and `guestlane` are deferred - the remaining six give 2,345 to ~1,500 with essentially no test risk.

**Where I am not confident:** the exaroton decorator rework and `outboxpoll`. Everything else I would commit to the number.

---

## Q2. The participant axis - how far off is the code from §1?

### Every owner-vs-guest branch, exhaustively

`is_owner` has **7 real call sites** (9 grep hits; 2 are the definition and a docstring). `is_guest` / `is_known_guest` have **6**. That is the entire surface - much smaller than §3.4's framing suggests.

| Site | Branches on | Verdict under a participant model |
|---|---|---|
| `bot.py:1506` `on_message` owner gate | may you direct Benham | **subsume** - gains a third answer (participant); the owner/not-owner cut still has to exist |
| `bot.py:1507` `is_known_guest` to guest lane | may you get a reply at all | **subsume** - becomes "participant in a project, or plain guest" |
| `bot.py:735` `ApprovalView._click` | may you press Approve | **leave alone** - authority, not participation |
| `agent.py:430` | defence-in-depth before spending an API call | **leave alone** |
| `capabilities.py:879` `_room_author` | render "tyler" vs an id in a room line | **leave alone** - cosmetic attribution |
| `capabilities.py:2018` `guest_quiet` | is this id a real guest to mute | **leave alone** |
| `cli/dm.py:55` (the new DM guard) | do I owe this person a tracked conversation | **replace** - the only true replacement in the list. "Is a collaborator" is participant-shaped; `is_guest` is standing in for it |
| `cli/outreach.py:86` | never outreach Tyler | **leave alone** |
| `policy.rule_guest` / `rule_owner` / `may_engage_agent:587` / `may_chat_as_guest:623` | which capabilities you reach | **leave alone** - INTENT §1 settles this itself: *"guest access is not project participation"* |

**One replacement, two subsumptions, six left alone.**

### Can anything answer "which project are you participating in"?

**No.** The word `participant` appears **zero times** in `benham/`. What exists is a `project` free-text string on a conversation (`open_conversation(..., project=None)`), set only when a caller passes `--project`, and a validated 4-name `PROJECTS` list in `issues`.

But the participant data is already in `control.json` - **as four flat lists, none joined to a project**:

| List | Members | Means |
|---|---:|---|
| `guest.ids` | 5 | may chat with Benham |
| `outreach.people` | 2 (doom, draco) | may be asked a tracked question |
| `issues.issuers` | 1 (doom) | may file into the tracker |
| `issues.projects` | 4 names | project labels, attached to nobody |

*(Aside: `guest.ids` holds 5 ids; INTENT §1 says four. Minor doc drift.)*

### What a minimal participant model has to hold

Per decision #21 (Doom only), the honest minimum is **a join, not a subsystem**:

```json
"participants": { "doom": { "id": 1097631170788851815,
                            "projects": ["storyizier", "minecraft-clone"] } }
```

plus two functions in `identity.py` - `participates_in(user_id) -> frozenset[str]` and `participants_of(project) -> [ids]` - and three wiring points that already have the hole cut: `conversations.open_conversation`'s `project=` defaults from the map when the caller omits it and the person has exactly one; `issues` validates against the same source instead of a second list; `cli/dm.py`'s guard asks "is a participant" instead of "is a guest".

**Size: ~40 lines of code, one config block, one test file. 1-1.5 half-days. Confidence: high** - this is small precisely because `project` already threads through conversations and issues. The only missing piece is the person-to-project map.

### Where I think §3.4 overstates its case, and this is the part to push back on

§3.4 blames the owner-vs-guest seam for *"three agent loops, two `_remember` implementations, two personas and two memory stores"*, and §1 says that mismatch *"explains most of the structural drift in §3."*

Having read every branch: **it doesn't.** That duplication is a **capability** split - the owner lane has a tool loop, the guest lane deliberately has none, and INTENT §1 itself says guest access is not project participation. A participant model is orthogonal routing information; it does not merge those loops, because what keeps them apart is the security property §4 item 13 explicitly says must be preserved (*"today the physical file split is doing real security work, and merging converts a physics guarantee into a policy guarantee"*).

So the participant model is worth building - it is cheap, it is the coordinator's routing question, and it is genuinely absent. But it is **not** the lever that removes the duplication, and buying it expecting Stage 4's structural win is buying the wrong thing. The two pieces of work are independent, and the participant model is the far cheaper of them.

---

## Q3. A second face - what it costs in `policy.py`

### Does anything assume a single bot identity or a single token?

**`policy.py` itself: no - and that is the problem.** `Origin` has six members, all about *direction of arrival*; `CallContext` has `__slots__ = ("origin", "actor_id", "guild_id", "channel_id", "tainted")`. **There is no field for which bot is acting.** So policy does not *assume* one identity; it is *unaware* identity exists. Every rule therefore applies to both faces equally, reading the same allowlists. No rule can say "the coordinator may not do X."

**Everything around it: yes, hard.** `identity.py` resolves `CONTROL_FILE` at import and builds `OWNER_IDS`, `AGENT_GUILDS`, `DESTRUCTIVE_GUILDS`, `GUEST_IDS` as **module-level globals from one `config/control.json`**. `paths.py` computes one `CONFIG_DIR`/`STATE_DIR`/`PROMPTS_DIR` from the repo root. `agent.PERSONA_FILE` and `guest.PERSONA_FILE` are single module constants. The token is one env var name, `BOT_KEY`, in one `environ.env`. Import-once semantics are load-bearing and documented (*"the restart is the kill switch"*).

### Is the owner gate per-bot or global?

**Global.** `owner_ids` is one list; `is_owner()` reads one module-level set. For Tyler that is arguably correct - he owns both faces - but it means **the coordinator inherits Benham's entire capability registry at every tier**, including tier 3, on day one. Nothing gates by face because nothing can name a face.

### What breaks, and what silently weakens

**Breaks outright** (shared `state/`, one directory):

1. **`outbox/` double-fires.** `poll_outbox` does `os.listdir(OUTBOX)` every 2s. Two processes pointed at one directory race for every request file. One wins the `_finish` rename, the other errors - and **which face sent the message is decided by a race**. This is the brief's own two-processes-one-token gotcha, generalized: two processes on *different* tokens sharing state has the same failure with a worse symptom, because the message actually goes out, just possibly wearing the wrong face.
2. **The DM thread Tyler wants separated is shared.** `agent.py`'s turn memory keys on `conversation_key = f"dm:{user_id}"` in one `agent_memory.json`. Both faces reading and writing one history for the same person is the *exact* thing a second face exists to prevent.
3. **`channels.json` is rewritten every boot** by whichever booted last.
4. **Guest caps and quiet merge.** `guest_usage.json`, `guest_quiet.json`, `guest_memory.json` are one file each - Doom's 100/day is spent across both faces, and quieting him on one silences the other.
5. **Ask-queue batch ids cross-talk.** `set_batch_message(counterparty, message_id)` stores a Discord message id in one file with no channel or bot column; `by_ask_message(ref_id)` then binds a reply. A reply in face A's DM can be matched against an id face B stored.

**Silently weakens** - this is the answer to "what breaks at the chokepoint", and it is the worst category because nothing errors:

6. **The scope caps become a union.** `post_guilds` / `post_channels` / `agent_guilds` / `destructive_guilds` are single global lists read by `rule_posting_scope`, `rule_agent_guild`, `rule_destructive_guild`. Adding the coordinator's guilds **widens Benham's reach to them too**, and vice versa. `identity.posting_allowed`'s docstring calls this cap *"arithmetic"* rather than a judgement call - it is the one non-judgement defence against Benham being invited somewhere and reading planted text. Two faces on one list turns it back into a judgement.
7. **`guest.ids` is one allowlist.** Anyone who may talk to the coordinator may talk to Benham.
8. **`guest.capabilities`, model, caps** - one block, so the two faces cannot be tuned apart.

**Per-process and therefore safe** (in-memory, one dict per process): `confirm._pending`, `codesession._pending`, `_views`, `guest._last_call`, the `_quiet` cache, `_wd_*`. Confirmation tokens do **not** cross faces. Good news, and slightly surprising - worth stating, because the in-memory-only property that makes it safe is described elsewhere as a limitation.

### What it would take

1. A face object loaded at process start (token env var, persona path, state namespace, config block) - ~60 lines.
2. `control.json` restructured into per-face blocks for guilds / guests / persona / model, with `owner_ids` staying global. Config schema change plus `identity.py` rework: ~150 lines.
3. **Classify all ~15 state files as per-face or shared.** Tyler explicitly wants rooms, the ask queue and conversations shared, and DM memory separated. This is a per-file judgement call, not a mechanical namespace, and it is where the design actually lives.
4. `policy.py`: add `face` to `CallContext` and its six constructors, and make `rule_agent_guild` / `rule_posting_scope` / `rule_destructive_guild` / `rule_guest` read the face's block. ~4 rules. **`test_policy.py` is 443 lines built around an origin-by-capability matrix that gains a face dimension** - that is the largest single test cost in this report.
5. `outbox/` gains a per-face subdirectory (simpler and safer than a `face` field plus a filter).

### The honest answer on size

**I cannot give a number for Q3 I would stand behind, and I would rather say so.**

Items 1, 2, 4 and 5 I would put at **6-8 half-days** with medium confidence. Item 3 - deciding which of fifteen state files are shared and which are per-face - is **not sizeable by reading**. It is a design question about how much two faces are meant to be the same entity, and every wrong answer is a silent bug of exactly the kind INTENT §7 is a list of (`guest_quiet` not surviving a restart; the delivery race live for 18 hours). Reading the code tells me the files; it does not tell me the answer.

**What I would do instead of estimating: a one-day spike.** Boot a second process with a second token against a *copy* of `state/`, DM both faces as Doom, and watch what collides. That produces a real list in a day and turns items 1-5 into a number. Guessing at it now would be the confident number the brief specifically asked me not to give.

**And one thing worth weighing before any of it:** the second face is the *only* one of the three questions whose cost lands squarely on `policy.py` - the file the whole security story rests on, and the one place the codebase has deliberately kept every gate readable in one spot. Q1 and Q2 are cheap, bounded and barely touch it. Q3 is the expensive one, and its expense is concentrated exactly where mistakes are worst.

---

## Bottom line

| | size | confidence | risk |
|---|---|---|---|
| **Q1 Stage 4 split** | 8.5-9 half-days (6 if the risky three slices are deferred) | medium-high | contained; one forced test edit, one silent-patch trap |
| **Q2 participant model** | 1-1.5 half-days | high | very low - it is a join over data already in config |
| **Q3 second face** | 6-8 half-days **plus an unsizeable design pass** | **low** | high, and concentrated in `policy.py` |

If Tyler wants one thing: **Q2.** It is a day and a half, it is genuinely missing, and it is his coordinator's routing question. Q1 is real work with a real payoff and a decision to make first (item 14 was dropped by him). Q3 needs a spike before it needs an estimate.
