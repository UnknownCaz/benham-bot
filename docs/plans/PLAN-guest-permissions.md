# Guest Capability Refactor — Plan

**Status: proposal, nothing implemented.**
*(File paths updated 2026-08-04 for the source reorg - see PLAN-src-reorg.md.)*
Scope agreed with Tyler 2026-08-03: shared web search (owner + guest, one implementation),
a guest file workspace (per-guest folders plus a read-only commons), limited Discord
reads of allowlisted channels, and sandboxed code runs. Grant model: a capability is
guest-reachable only when the registry flag AND control.json both say so.

This is the first time a non-owner gets past `rule_owner`, which makes it the most
security-sensitive change the bot has had. The plan is therefore staged the way
policy.py itself was: seven stages, each leaving the system fully working, so a
regression is always attributable to a single step.

---

## 1. Where we are today (ground truth)

What the refactor builds on, file by file:

> **Historical note, 2026-08-17.** The paragraph below describes the state BEFORE this
> plan was carried out, and the plan itself changed it: §"New caller rule" adds
> `rule_guest` and gives `rule_owner` the line `if ctx.origin in Origin.GUEST: return
> None`. So `rule_owner` has not refused guests since this landed. The live pair is
> `rule_guest` + `rule_origin_allowed`. Left in place because a plan is a record of what
> was decided, not live documentation - but the same sentence had been copied into
> `policy.py` and `README.md`, where it WAS live, and both are now corrected.

- **benham/core/policy.py** — single chokepoint. Caller rules (`RULES`) then target rules
  (`TARGET_RULES`). Guests are denied twice, independently: `rule_owner` refuses any
  human non-owner (GUEST_DM is deliberately in `Origin.HUMAN` for exactly this), and
  `DEFAULT_ORIGINS` omits `GUEST_DM` so every capability is guest-proof the day it is
  written. `may_chat_as_guest` authorises the *conversation*, not any capability.
- **benham/guest/guest.py** — chat mode. Its founding property: **no CLIENT tools ever**. The only
  tool passed is Anthropic's server-side `web_search_20250305`, which runs on their
  infrastructure. Quota (`_reserve`/`refund`/`charge_search`), cooldown, separate
  memory file, separate persona, search log (`state/guest_searches.jsonl`), searched turns
  count double.
- **benham/core/identity.py** — `GUEST_MODES = frozenset({"chat"})`, with the comment already
  reserving `"workspace"` for Phase 2. An unrecognised mode disables guest chat
  rather than guessing. Config is read once at import; kill switches require a
  restart, deliberately.
- **benham/core/capabilities.py** — the registry. `Action` carries `tier`, `outward`, `taints`,
  `always_confirm`, `posts`, `origins`, `blocked_when_tainted`. `run()` is the single
  execution chokepoint: authorize → validate → authorize_target → confirm-or-execute.
  Also home of `_safe_filename` / `_confined_path`, the attachment-download path
  hygiene we will reuse.
- **benham/core/agent.py** — owner tool loop. Compiles the registry into tool schemas. **Has no
  web search today** — guests can search and Tyler cannot, which is half the reason
  "shared search" is on this list.
- **benham/bot.py** — `on_message` routes a DM from a known guest to `handle_guest_dm`
  (~line 1595/1665) before anything else; a guest never falls through to the owner
  paths. `handle_guest_dm` does check → respond-in-thread → refund-on-failure.

Invariants that must survive this refactor, verbatim:

1. A new capability added next year is guest-proof on the day it is written.
2. The model cannot confirm its own destructive actions; nothing a guest does can
   put a confirmation in front of the model either.
3. `pc_task` and every outward/posting/destructive capability stay unreachable from
   any guest origin, no matter what control.json says.
4. Guest turns are born tainted (`CallContext.guest_dm` sets it at construction) and
   stay tainted.
5. No code a guest influences ever executes on Tyler's machine.

---

## 2. The grant model: three declarations, all must agree

A capability is guest-reachable only when **all three** hold:

1. **Registry flag** — `Action` gains `guest=False`. Set `guest=True` on the
   declaration line. Code says what is *possible*.
2. **Origins** — the declaration must also include `policy.Origin.GUEST_DM` in its
   `origins` set. `DEFAULT_ORIGINS` continues to omit it, so `rule_origin_allowed`
   remains the second, independent denial — exactly the "either alone would be
   sufficient" structure policy.py documents today.
3. **config/control.json** — `guest.capabilities` must list the action name. Config says
   what is *on*. Empty list (the default) = nothing granted, whatever the code says.

A typo in config can therefore only ever *disable* something; it can never expose an
action the code did not mark. And a capability marked in code but absent from config
stays off — Tyler can stage rollout per-capability without touching code.

**Registration-time invariant (fail at import, not at runtime).** The `action()`
decorator refuses to register any `guest=True` capability that is also `destructive`,
`posts`, `always_confirm`, or `outward` — and also refuses `blocked_when_tainted=True`,
because a guest turn is born tainted, so such a capability would be granted in config
and dead in practice, which is exactly the kind of lie a registry must not tell.
So: `guest=True` requires `not outward, not posts, not destructive, not
always_confirm, not blocked_when_tainted`, and `GUEST_DM in origins`. A violating
declaration crashes the bot at startup. This is the "a rule cannot exist without
being enforced" philosophy applied to the declarations themselves.

### policy.py changes

New caller rule `rule_guest`, inserted **before** `rule_owner`:

```
def rule_guest(action, ctx):
    if ctx.origin not in Origin.GUEST:
        return None                      # not our lane; owner rules proceed
    if not identity.guest_enabled():     # mode + switch, as today
        return _deny(...)
    if not identity.is_guest(ctx.actor_id):
        return _deny(...)                # stranger on a guest origin
    if not action.guest:
        return _deny("guest_capability", ...)
    if action.name not in identity.guest_capabilities():
        return _deny("guest_config", ...)
    return _ALLOW_GUEST_LANE             # see below
```

`rule_owner` then adds one line: `if ctx.origin in Origin.GUEST: return None` — guest
origins are `rule_guest`'s to decide, and `rule_guest` is fail-closed (its default
answer for a guest is deny). The double denial is preserved because
`rule_origin_allowed` still runs afterwards and still refuses any capability that did
not name GUEST_DM.

**`_ALLOW_GUEST_LANE` is `None`, not an early ALLOW.** rule_guest passing means "the
guest lane does not object"; the remaining rules (`rule_origin_allowed`,
`rule_blocked_when_tainted`) still run. Never short-circuit to ALLOW from inside one
rule — first-non-None-wins is the contract.

New **target** rule, first in `TARGET_RULES`:

```
def rule_guest_never_confirms(action, ctx):
    if ctx.origin in Origin.GUEST and action.needs_confirm:
        return _deny(...)
    if ctx.origin in Origin.GUEST and action.outward:
        return _deny(...)   # taint would CONFIRM; for guests that must be DENY
    return None
```

Reason: CONFIRM means "park a preview and ask Tyler". A guest must get neither half —
not the preview (it leaks what would happen), and not the ability to generate
approval traffic at Tyler. On the guest lane, anything that would confirm is a flat
no. Ordering matters and follows the existing comment in TARGET_RULES: deny rules
before confirm rules, so a guest is never offered a yes to something that was never
on offer. The registration-time invariant makes this rule unreachable in practice;
it exists for the same reason `rule_owner` re-states the entry-point check — a
security check worth having is worth having twice.

`may_engage_agent` is untouched: the owner tool loop stays owner-only. Guests get
their own, smaller loop (§5).

### identity.py changes

- `GUEST_MODES = frozenset({"chat", "workspace"})` — the reserved Phase 2 word.
- `guest_capabilities()` → `frozenset(GUEST.get("capabilities") or [])`.
- `guest_read_channels()` → `frozenset(int(c) for c in GUEST.get("read_channels") or [])`.
- Workspace quota accessors (§4 config block).
- Everything stays import-time, restart-to-apply. A live-reload path would create a
  second answer to "who may do what" — identity.py already documents why that is
  worse than the delay. No exceptions for the new keys.

---

## 3. Shared search — one implementation, per-role knobs (Stage 1–2)

The "shared function" ask. Today the server-side web-search tool block, the
`server_tool_use` query extraction, and the search log format all live inline in
`benham/guest/guest.py`. Extract into a new module, then give the owner agent search from the
same code:

**`benham/core/shared_tools.py`** (new, no Discord imports, no state of its own):

- `web_search_tool(max_uses)` → the `{"type": "web_search_20250305", ...}` dict.
- `search_queries(resp)` → list of query strings from `server_tool_use` blocks
  (the exact extraction guest.respond does today).
- `log_searches(path, actor_id, queries, role)` → append JSONL; same shape as
  today's `state/guest_searches.jsonl` lines plus a `role` field ("guest"/"owner"). The
  existing file name and consumer keep working; old lines just lack `role`.

Consumers:

- **benham/guest/guest.py** — swaps its inline block for the module. Behaviour identical:
  same log file, same double-charge via `charge_search`, same `searches_per_turn`.
  Its module docstring's property is untouched — this is still the server-side
  tool, still zero client tools.
- **benham/core/agent.py** — gains web search for the owner, config-gated:
  `agent.web_search` (default true) and `agent.searches_per_turn` (default 3) in
  control.json. Owner queries logged to `state/agent_searches.jsonl` — separate file from
  the guest one for the same reason guest memory is a separate file: one typo apart
  is too close. No quota (Tyler is billing himself), but log always.
- **benham/guest/guest_agent.py** (§5) — same module again when workspace mode arrives.

This stage is a pure win with zero guest exposure, which is why it ships first.

---

## 4. The guest workspace (Stage 4)

### Layout

```
state/guest_work/                  (new, gitignored, inside BASE_DIR)
  commons/                   Tyler-curated; guests read, never write
  <user_id>/                 one per guest, created on first write
```

Per-guest + commons, as agreed. Rules:

- **Reads**: own folder and `commons/` only. Never another guest's folder — the
  folder name is the guest's own actor_id from the CallContext, never a parameter.
- **Writes/deletes**: own folder only. `commons/` is read-only for guests; Tyler
  populates it by hand or through the normal owner agent.
- **Path discipline**: every capability takes a *relative* path parameter. Resolution
  is `confined(root_for(ctx.actor_id), rel)` built from the same two functions the
  attachment path already trusts — `_safe_filename` and `_confined_path` — **moved**
  out of capabilities.py into a new `benham/core/pathsafe.py` that both call sites import
  (Stage 0). One implementation of "may this path exist", not two drifting copies.
  Absolute paths, drive letters, `..`, reserved device names, and both slash
  directions are all refused by construction.
- **Runnable suffixes**: guest *writes* with an extension in `_RUNNABLE_SUFFIXES`
  are refused outright. The downloads path flags-not-blocks because Tyler is handed
  jar files legitimately; a guest has no legitimate reason to author `.bat` files
  onto Tyler's disk. Reads of such files from commons stay allowed (flagged).

### Capabilities (all `guest=True`, `taints=True`, origins = GUEST_DM only)

| name        | does                                                        |
|-------------|-------------------------------------------------------------|
| `ws_list`   | list own folder + commons (names, sizes, mtimes)             |
| `ws_read`   | return a text file's contents (truncated at `MAX_TEXT_CHARS`), or metadata for binary |
| `ws_write`  | create/overwrite a file in own folder from provided text     |
| `ws_import` | save attachments from a message THE GUEST SENT in THIS DM into own folder (§ below) |
| `ws_delete` | delete a file in own folder (own files are theirs to lose — no confirm; not tier-3, nothing of Tyler's can be named) |

Note the origins set: these are **not** in `DEFAULT_ORIGINS` + GUEST_DM — they are
`{GUEST_DM}` (plus `LOCAL_CLI` for Tyler's debugging if wanted). Tyler operating on
guest folders goes through the ordinary filesystem or `pc_task`; keeping the ws_*
surface guest-only means its parameter validation never has to reason about an
owner-shaped caller.

These are Discord-tier READ in spirit (nothing outward, nothing posted) but they do
touch local disk, so they carry their own limits rather than borrowing tier
semantics:

- per-file cap: `guest.workspace.per_file_mb` (default 5)
- per-guest folder cap: `guest.workspace.per_guest_mb` (default 20), enforced on
  write by summing the folder — refuse, never truncate
- file count cap: `guest.workspace.max_files` (default 100)
- one `threading.Lock` around check-then-write, same reasoning as `_quota_lock`

### Getting files back to the guest

**No send capability.** `guest_agent.respond()` returns `(reply_text, [paths])` and
`handle_guest_dm` attaches those files to the DM reply it was already sending. The
audience of that DM is the person who asked — it is the reply channel itself, not a
new outward surface, and it keeps the outward capability count on the guest lane at
exactly zero. Paths returned are re-verified against the guest's own root in bot.py
before attaching (check twice, as always). Discord's 25MB/10-file limits enforced by
reusing `_load_files`' checks.

### Getting files in: `ws_import`

In scope for Stage 4 (Tyler's call, 2026-08-03: guests are close, individually
invited friends — they deserve **more capabilities, not more power over the
machine**, and inert bytes in a quarantined folder are capability, not power).

The design copies `read_attachments`' three-property shape exactly, plus one
pinning rule of its own:

1. **It fetches what a NAMED MESSAGE carries — there is no URL parameter.** A url
   parameter would be "download anything from anywhere" aimed by whatever text the
   model last read; that shape stays refused on the guest lane just as it is on the
   owner's.
2. **The message must be the guest's own, in this DM.** Parameters are
   `message_id` (default: the message that started this turn) and `index`. The
   handler fetches the message via the channel pinned in the CallContext (the guest
   DM — never a parameter) and refuses unless `message.author.id == ctx.actor_id`.
   Worst case of a doctored `message_id` is importing an *older* attachment from
   their own DM, which is a feature, not a leak — the channel contains only this
   guest and Benham.
3. **Bytes land only in their own folder**, filename rewritten through `pathsafe`,
   size checked from message metadata *before* download (the `read_attachments`
   bandwidth rule), all three workspace quotas enforced.
4. **Nothing is ever run**, and the one-rule-everywhere suffix policy holds: the
   guest workspace never contains a runnable-suffixed file however it arrives —
   imports refuse `.exe`/`.bat`/etc. the same as `ws_write` does. A friend who
   genuinely needs to hand over a script sends it as `.txt`; Tyler moving it out of
   quarantine is the deliberate human step that makes it live.

Unlike `ws_write` (text in a tool parameter), `ws_import` is the one path that puts
**binary** guest-chosen bytes on disk. That widens what the folder can *contain*,
not what can *happen*: the caps bound the size, the suffix rule bounds the shape,
and nothing in the bot ever opens workspace files except `ws_read`'s
decode-as-text. Each import is one more line in run()'s ordinary action log —
that is the moderation trail, same as searches.

---

## 5. guest_agent.py — the guest tool loop (Stage 3)

A third module, and deliberately not a flag on either neighbour:

- **benham/guest/guest.py** keeps its docstring property forever: that file passes no client
  tools, full stop. It remains the `mode: "chat"` implementation, untouched.
- **benham/core/agent.py** stays the owner's. Its loop parks confirmations, carries the full
  registry, reads persona.md. "Same loop but smaller list" is one wrong conditional
  from being the same list — the exact collapse guest.py's docstring warns about.

`benham/guest/guest_agent.py` (mode `"workspace"`):

- Builds tool schemas from `capabilities.catalog()` filtered to the **effective
  grant set** (flag ∧ config ∧ GUEST_DM-in-origins — computed by one function,
  `policy.guest_grants()`, so the loop and the tests use the same answer), plus the
  server-side search tool from `benham/core/shared_tools.py`.
- Every tool call goes through `capabilities.run(..., call_ctx=CallContext.guest_dm(uid, ch))`
  — the same chokepoint, the same logging, the same denials. The loop adds no
  authority; it only decides how many rounds to pay for.
- `max_tool_rounds`: `guest.tool_rounds` (default 4, vs the owner's 8).
- A CONFIRM verdict is structurally impossible on this path (policy denies first),
  but the loop still treats any non-(result, None) shape from run() as a hard error
  and ends the turn — belt and braces.
- Memory: `state/guest_memory.json`, same `guest:<id>` keys, same text-pairs-only
  persistence trick as agent.py (store no tool blocks — same 400-proofing and same
  cost logic).
- Persona: `prompts/guest_persona.md` updated to describe the real, tiny tool set —
  the current text says "you have NO tools", which becomes a lie in workspace mode,
  and a model told wrong things about its tools promises wrong things.
- Cooldown/quota: unchanged `guest.check()` / `refund()`. New: `charge_rounds(n)` in
  guest.py alongside `charge_search` — each tool round beyond the first counts one
  extra message (config `guest.tool_round_cost`, default 1). Same after-the-fact,
  never-refuses shape as `charge_search`, same honest-ledger reasoning.
- bot.py routing: `handle_guest_dm` picks `guest.respond` or `guest_agent.respond`
  by `identity.GUEST.get("mode")`. Everything before and after the call is shared —
  check, thread, refund, attach.

Stage 3 ships this loop with the grant set **empty** (search only). That is chat
mode with a different engine — a pure plumbing stage, testable in production with
zero new authority, before any capability is granted in Stage 4.

---

## 6. Limited Discord reads (Stage 5)

The riskiest of the four, so it is its own late stage with its own switch.

- New capability `read_shared_channel` (`guest=True`, `taints=True`,
  origins `{GUEST_DM}`): parameters `channel_id`, `limit` (≤50). Handler refuses any
  `channel_id` not in `identity.guest_read_channels()` **before** resolving it —
  naming what is in a channel a guest may not read is the same small leak
  `rule_destructive_guild` guards against.
- A **separate action**, not a grant of `read_channel`. `read_channel`'s parameter
  space is every channel Benham can see; scoping it per-caller would put a guest
  branch inside an owner path, which is the pattern this codebase exists to avoid.
  Serialization is shared (`msg_dict` etc.), the capability is not.
- `guest.read_channels` in control.json is the allowlist. Empty default = feature
  off even if the capability is in `guest.capabilities`. Document loudly in
  control.json.example: **every listed channel becomes readable by every guest** —
  choose channels as if posting their contents publicly.
- Each read is logged (`guest chat` log line already carries actor; run() logs the
  action) — the moderation trail matches the search log's spirit.
- No new taint work needed: guest turns are born tainted, all reads taint, and
  nothing confirmable or outward exists on the lane for poisoned content to steer.
  A hostile message *inside* the shared channel can, at worst, waste the guest's own
  quota — the blast radius section (§9) stays true.

---

## 7. Sandboxed code runs (Stage 6, feature-flagged separately)

**Recommendation: Anthropic's server-side code-execution tool, not local execution.**
The founding property of the guest lane is "no code runs here". A Windows-local
sandbox (restricted tokens, job objects, AppContainer) is a research project with a
catastrophic failure mode — one bug is stranger-authored code on Tyler's actual PC.
The server-side tool has the same shape as web search: it executes on Anthropic's
infrastructure, in their container, billed per use, and nothing on this machine or
network is reachable from it. It keeps guest.py's paragraph-you-must-reread true in
spirit across the whole guest lane.

Design:

- Config: `guest.code_execution: {enabled: false, runs_per_day: 10}` — its own
  switch, default off, independent of everything else.
- When enabled, `benham/guest/guest_agent.py` adds the code-execution server tool (exact tool
  type/version pinned at implementation time from current Anthropic docs — verify
  then, not from memory) to the API call, `max_uses` capped per turn.
- Workspace bridge, if wanted later: upload named `ws_` files to the container via
  the Files API before the turn, pull declared outputs back into the guest's folder
  after — both directions capped in bytes and logged. **V1 ships without the
  bridge**: code runs are self-contained (write code, see output). The bridge is a
  second design (Files API lifecycle, output vetting: sanitise names, size caps,
  runnable-suffix refusal — outputs land via the same `pathsafe` gate as `ws_write`)
  and should not block the simple version.
- Ledger: every run appended to `state/guest_runs.jsonl` (ts, user_id, bytes in/out),
  runs count against the daily message cap like searches do (double), plus their own
  `runs_per_day`.

Rejected: local `subprocess` in the guest folder, however restricted — breaks
invariant 5; Docker/WSL — real isolation but a heavy operational dependency for a
bot that must survive reboots unattended, revisit only if the server-side tool
proves insufficient.

---

## 8. control.json schema (full new guest block)

```json
"guest": {
  "enabled": true,
  "mode": "workspace",
  "ids": [ ... ],
  "model": "claude-haiku-4-5",
  "max_tokens": 500,
  "history_turns": 10,
  "cooldown_seconds": 3,
  "daily_message_cap": 100,
  "global_daily_cap": 400,
  "web_search": true,
  "searches_per_turn": 2,

  "capabilities": ["ws_list", "ws_read", "ws_write", "ws_import", "ws_delete"],
  "tool_rounds": 4,
  "tool_round_cost": 1,
  "workspace": { "per_guest_mb": 20, "max_files": 100, "per_file_mb": 5 },
  "read_channels": [],
  "code_execution": { "enabled": false, "runs_per_day": 10 }
}
```

And in the agent block: `"web_search": true, "searches_per_turn": 3`.

Unknown keys are already tolerated (load_control copies over defaults); an old
control.json keeps meaning exactly what it meant — absent `capabilities` is the
empty set, absent `mode` is `"chat"`, and `"workspace"` on a pre-refactor build
disables guest chat entirely (the existing unknown-mode behaviour, which is the
correct failure direction and already has a test).

---

## 9. Threat model — what a hostile guest gets, per stage

The question every stage must re-answer: *doomassassin1 turns malicious, or a
guest's account is compromised. What is the worst case?*

- **Stage 1–2** (shared search, policy groundwork, nothing granted): today's answer,
  unchanged — burn daily caps, log nasty search queries. Nothing else.
- **Stage 3** (loop, empty grants): identical. The loop with no tools is chat.
- **Stage 4** (workspace, incl. `ws_import`): + fill 20MB × N guests of Tyler's
  disk with garbage or malware-as-inert-bytes — now including *binary* bytes via
  import, still never executed, runnable suffixes refused however the file
  arrives, and import sources pinned to the guest's own messages in their own DM
  (no URL fetch exists); read whatever Tyler put in commons. Cannot: touch any
  path outside their folder (pathsafe + confinement + double-check in bot.py
  before attach), see another guest's folder, or make Benham post/DM anyone but
  themselves.
- **Stage 5** (shared reads): + read the allowlisted channels, i.e. exfiltrate
  whatever Tyler explicitly chose to expose — a curation decision, not a bug class.
  Poisoned content in those channels lands in an already-tainted turn on a lane
  with no confirmable, outward, or destructive capability: nothing to steer.
- **Stage 6** (code runs): + spend Anthropic compute within caps. Runs on
  Anthropic's servers; nothing local.

At no stage: any tier ≥ 1 Discord action, any posting, any DM to a third party,
`pc_task`, Tyler's memory/persona/guardrails files, or a confirmation prompt in
front of Tyler or the model. The persona-directive stripping (`strip_directive`,
never `parse_persona_directive`) carries over to guest_agent.py unchanged.

---

## 10. Stages, in order (each independently shippable)

| # | Ships | Risk added | Proves |
|---|-------|-----------|--------|
| 0 | ~~`benham/core/pathsafe.py` + `benham/core/shared_tools.py` extractions~~ **SHIPPED** (a91a07f) | none | extraction is faithful (existing tests still green) |
| 1 | ~~Owner web search in agent.py via shared_tools; `state/agent_searches.jsonl`~~ **SHIPPED**, live-tested 2026-08-03. Two decisions made during implementation: **a searched turn is tainted** (a web page is stranger-written text; taint set BEFORE that response's tool calls run, since server-side results arrive inside the response that chose them — proven by test_injection's search-taint case); and cited answers must be reassembled with `_response_text` ("" join within a response — the first live search came back as confetti without it) | none (owner-only) | shared module works in production |
| 2 | ~~policy: `rule_guest`, `rule_guest_never_confirms`, `Action.guest`, registration-time invariant, `identity.guest_capabilities()`; config empty~~ **SHIPPED**, suite + clean boot verified 2026-08-04. One placement deviation from this plan: `guest_grants()` lives in capabilities.py, not policy.py — the registry lives there and policy cannot import capabilities without a cycle. Only observable delta: a guest probing a capability is now refused by `guest_capability` instead of `owner` in the logs | none (grants empty ⇒ behaviour identical; invariant tests prove it) | the gate exists and fails closed |
| 3 | ~~`benham/guest/guest_agent.py` loop, `"workspace"` in GUEST_MODES, bot.py mode routing; grants still empty~~ **SHIPPED** (suite + clean boot 2026-08-04; live workspace-mode soak with a real guest still pending — do that before granting anything). Pricing decision made during implementation: extra rounds are charged by API calls beyond the first (calls − 1), not by tool rounds entered — the two differ exactly at the round limit. The loop never imports confirm.py (absence as property); a preview arriving anyway logs GUEST-CONFIRM-LEAK, tells the model nothing, parks nothing | ~none | the loop, charging, routing, refunds |
| 4 | ~~`ws_*` capabilities (incl. `ws_import`) + quotas + reply-attachment path + persona rewrite~~ **SHIPPED** (full suite green on Windows 2026-08-04; grants still empty — live enablement checklist in OWNERS-GUIDE, soak workspace-with-zero-grants first). Deviations from plan, all narrowing: SIX capabilities not five (`ws_attach` added — deliverables ride the reply via an on_attach collector on Ctx, re-verified in bot.py by `verify_outgoing`; no send capability exists). Workspace is FLAT (filenames only, no subdirs — smaller parameter space). Writes refuse a name that sanitisation would change; imports sanitise-and-report (uploader-controlled). Persona is not rewritten: guest_agent appends a grants-generated correction paragraph to the system prompt only when grants are non-empty, so no static file can lie in any config | disk-fill (capped), guest-uploaded inert bytes | the workspace |
| 5 | `read_shared_channel` + `read_channels` config | curated exposure | shared reads |
| 6 | ~~server-side code execution, own flag~~ **CODE COMPLETE, off by default.** Built without the file bridge (Tyler's call — revisit after real use cases). Tool pinned to `code_execution_20250825`, the version every model including Haiku supports; no beta header needed. Reading the docs first caught a latent bug: `search_queries` filtered on block TYPE, so once a second server-side tool existed every code run would have been logged as a search with query `?` and charged as one — now filtered on tool NAME. Also handles `pause_turn`, which the loop would otherwise have read as a finished answer and truncated | Anthropic-side compute | code runs |

Every stage lands with: its tests, a control.json.example update, a guest_guide.md
update, and a README section touch-up. No stage begins until the previous one has
run in production for at least a few days of real guest traffic.

---

## 11. Test plan

Extend the existing check/section style suites:

**test_policy.py**
- rule ordering: `rule_guest` before `rule_owner`; guest origin never reaches
  rule_owner's deny (the refusal names the guest rule).
- fail-closed table: guest ctx × {no flag, flag-no-config, config-no-flag,
  flag+config-no-origin} ⇒ all denied, each naming its own rule.
- `rule_guest_never_confirms`: a hypothetical guest+needs_confirm action ⇒ DENY,
  never CONFIRM.
- `may_engage_agent` still refuses guests (unchanged, but re-asserted here because
  this refactor is exactly the "later change" its comment warns about).

**test_guest.py**
- `"workspace"` mode now *enables* on the new build (updating the line-127 check
  that today expects it to disable) — and a genuinely unknown mode still disables.
- `charge_rounds` accounting; round-cost refund interaction.

**new test_guest_workspace.py**
- path corpus against `pathsafe`: `..`, `..\\`, absolute, `C:` drive, UNC,
  reserved stems (CON/NUL/COM1), trailing dots/spaces, both slashes, 200-char
  names, empty, unicode confusables ⇒ all confined or refused.
- cross-guest isolation: guest A's ctx can never yield a path under B's folder.
- commons: readable, never writable; ws_delete refuses commons paths.
- quota: per-file, per-folder, file-count; two threads racing one write slot.
- runnable-suffix refusal on BOTH paths — ws_write and ws_import.
- ws_import pinning: a message authored by anyone but the guest ⇒ refused; a
  channel other than the CallContext's DM is unreachable by construction (assert
  the handler takes no channel parameter); oversize refused from metadata before
  any download; imported filename passes the same pathsafe corpus.
- bot.py attach path re-verification (a doctored return path outside the root is
  dropped, not sent).

**invariant suite (the important one) — new test_guest_grants.py**
- iterate `REGISTRY`: every action without `guest=True` is denied for a guest ctx
  by `authorize()` — all ~50 of them, by actually calling authorize, not by reading
  flags.
- the effective grant set (`policy.guest_grants()`) equals exactly
  {flag ∧ config ∧ GUEST_DM-in-origins} — computed one way in code, recomputed
  independently in the test.
- registration invariant: declaring guest=True + outward (etc.) raises at import.
- config listing `pc_task`/`purge_messages`/`send_message` grants nothing (flag
  false) — the typo-can-only-disable property, proven.
- guest ctx is tainted at construction, still.

**test_injection.py**
- guest asks Benham to post to a channel / DM Tyler / run pc_task ⇒ denial text,
  no confirmation parked (assert confirm store empty).
- hostile content inside a shared-read channel cannot unlock anything (turn already
  tainted; grant set contains nothing confirmable).

---

## 12. Rejected alternatives (so they stay rejected)

- **Guests as a low tier of owner** — a guest is not a weaker owner; identity.py
  says so today and stays right. Tiers grade *Discord consequence*, not *trust in
  the caller*; conflating them is how a tier edit becomes a privilege escalation.
- **One agent loop with a filtered registry** — "the same thing but the list is
  smaller" is one conditional from "the same list". Three loops, three files, three
  properties that cannot be collapsed by accident.
- **Granting `read_channel` directly with a parameter check** — puts a guest branch
  in an owner path; separate capability instead.
- **A `ws_send`/`dm_user` grant for deliverables** — keeps outward count nonzero on
  the guest lane for no benefit over attaching to the reply.
- **A URL parameter on `ws_import`** — "fetch this link into my folder" is a
  general downloader aimed by whatever text the model last read, the exact shape
  `read_attachments` was built to refuse. Imports come from a named message the
  guest authored, or not at all.
- **Local code execution, however sandboxed** — breaks invariant 5 with a
  catastrophic failure mode; server-side or nothing.
- **Live-reloading guest config** — creates a second answer to "who may do what";
  restart semantics stay uniform.

## 13. Open questions (decide before their stage, not now)

1. Per-guest capability overrides (`capabilities_by_id`) — e.g. only doomassassin1
   gets code runs. The grant model extends cleanly (intersect a per-id set), but it
   is scope creep until someone asks.
2. Model bump for workspace mode — Haiku may fumble multi-step tool use; Sonnet
   costs more per guest message. Decide from Stage 3's real traffic.
3. Commons write access for Tyler *through Benham* (owner-side ws capability) —
   probably unnecessary (pc_task and the filesystem exist), revisit on demand.

(`ws_import` was originally parked here; promoted into Stage 4 on 2026-08-03 —
see §4 for the pinning rules that made it safe to promote.)
