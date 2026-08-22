# The second face - state design spike
2026-08-21 · read-only · no code written · ships a design, not an implementation
Given: **Decision 2 - separately owned.** Each face carries its own owner list.

---

## Finding zero, before the five: the test suite writes into the live bot's memory

`_testconfig.py` redirects `paths.CONFIG_DIR` and **nothing anywhere redirects `paths.STATE_DIR`**. `agent._store` therefore points at the real `state/agent_memory.json` while the bot is running on it, and `test_memory.py`'s `_stored()` helper reads that same live file *on purpose* - its docstring says "the turns as they exist on disk."

The evidence is in the file. Live `state/agent_memory.json` holds five keys, all of them `test:*` (`test:injection`, `test:taintorder`, `test:taintorder2`, `test:searchtaint`, `test:conversation`), each at **40 turns** - pinned at the `history_turns: 20` ceiling. The pre-repair backup beside it has the same test keys at **2 turns**. They have been accumulating across suite runs. I ran the suite twice tonight, so two of those appends are mine.

Also true, and I am reporting it without a cause: **`dm:273967061619965952` - Tyler's own 20-turn DM history - is present in `agent_memory.json.pre-repair.bak` and absent from the live file.** `scripts/repair_memory.py` is the likelier culprit than the tests (removing echo-pair damage is exactly what it does, and 20 damaged turns would empty the key), but I did not prove that and I am not going to assert it. What I can say is that the live agent memory currently contains no real conversation at all.

**Why it belongs in this document rather than a bug list:** it changes the answer to question 2. The migration cost for `agent_memory.json` is **zero**, because there is nothing in it to migrate. It also gets *worse* under a face split - with per-face state directories and `STATE_DIR` still unredirected, the suite would write into whichever face is default, and "which face did the tests corrupt" becomes a question somebody has to ask.

**Recommendation: redirect `STATE_DIR` in `_testconfig.py` to the same scratch dir it already makes for `CONFIG_DIR`, and do it BEFORE the face work, not as part of it.** It is a handful of lines, it is test-only, and it makes every number below measurable instead of entangled. Not done here - I am spiking, and Kestra's sequencing says design first.

---

## 1. State classification

26 entries in `state/`, reached from 9 modules. The axis is not "does it mention a user" - it is **whose act produced it**. A file records something *a face did or was told*; or it records something *the project knows*. That is the whole classification, and it lands cleanly on all but two.

### Per-face - the face is part of the data's identity

| File | Why |
|---|---|
| `agent_memory.json` | DM history. The decisive one; see §2 |
| `agent_searches.jsonl` | search log, attributable to one face's turns and its bill |
| `guest_memory.json` | a guest's conversation with **this** face |
| `guest_usage.json` | quota. Shared means one face starves the other out of a cap that was sized for one |
| `guest_quiet.json` | "stop messaging me" applies to the identity you said it to. Shared, muting Benham silences the Coordinator - which is the §7 `guest_quiet` bug class again, wearing a new hat |
| `guest_searches.jsonl` | same as agent's |
| `channels.json` | what **this** bot can see. Two faces have different guild membership, and it is rewritten every boot by `dump_channels()` - so today, two faces means whoever booted last wins and `cli/status.py` + `cli/draft.py` read a file describing the wrong bot |
| `inbox.jsonl` | what this face received |
| `outbox/` (+ `sent/`, `failed/`) | the race; see §3 |

### Shared deliberately - project truth, and Tyler asked for these to be shared

| File | Why |
|---|---|
| `conversations.json` | the ask queue and the collaborator loop. One question owed is one question owed, whoever carried it |
| `rooms.json`, `rooms/`, `rooms/cursors/` | work between sessions. Sharing is the entire point of rooms |
| `guest_issues.jsonl`, `issue_offers.json`, `issue_detect.json` | the intake funnel. GitHub is the source of truth (#33) and it does not have a face column |
| `guest_ideas.jsonl` + `.cursor` | same funnel, the never-lost fallback |

### Doesn't matter

`downloads/` (scratch), `guest_work/` (archived feature), `voice_settings.json` + `voice_transcript.jsonl` (voice was cut, #6), the three `.bak` files, `guest_runs.jsonl`.

### The two that do not land cleanly - both are decisions, not recommendations

**`ask_batches.json` is the fiddly one, and it is a split file, not a shared one.** `conversations.json` is genuinely shared, but `ask_batches` stores **Discord message ids** (`set_batch_message(counterparty, message_id)`) and a message id belongs to one face's DM channel. `by_ask_message(ref_id)` then binds a reply to a conversation. Leave it shared and a reply in Benham's DM can bind against an id the Coordinator stored - a wrong answer attached to a real question, silently. So: **the conversation is shared, the rendering of it is per-face.** That means `ask_batches.json` goes per-face while `conversations.json` stays shared, and `shown_queue()` has to reconcile a per-face batch against a shared queue. This is the single most intricate piece of the whole job and it is where I would expect a bug.

**`initiative.json` + `initiative-log.md` I will not classify.** The initiative lane is "Claude starts a conversation unprompted", governed by `UNPROMPTED_MIN_GAP` (48h) and one-at-a-time in `policy.py`. Whether that budget is **one channel Tyler gets pinged on** or **one per face** is a product decision about how often he is willing to be interrupted, not a technical one. Per-face doubles the unprompted contact rate. Flagging, not picking - see Decisions.

---

## 2. `agent_memory.json` and `conversation_key` - what the key should be

**The key should not change. The file should.**

The current key is built in one place, `bot.py:1888`:
```python
key = f"dm:{message.author.id}" if is_dm else f"ch:{message.channel.id}"
```
The obvious move is `f"{face}:dm:{uid}"`. **Do not do that**, and the argument is already written down in this codebase, in `turnmemory.py`'s own docstring:

> SEPARATE FILES, SHARED LOGIC. Each caller brings its own path, so `agent_memory.json` and `guest_memory.json` stay distinct on disk. That separation is deliberate and load-bearing (guest.py: *"a prefix means Tyler's history and a guest's history are one typo apart; a different path is not"*), and this module takes the path as an argument precisely so it cannot erode it.

The owner/guest boundary already faced exactly this choice and chose paths over prefixes, for exactly this reason. A face boundary is the same shape and deserves the same answer - and better, it needs **no new mechanism**: `TurnMemory` already takes its path as a callable (`turnmemory.TurnMemory(lambda: MEMORY_FILE, HISTORY_TURNS)`). The lambda is the seam, and it was put there on purpose.

**So: `state/faces/<face>/agent_memory.json`, key format untouched.** `agent.MEMORY_FILE` becomes face-derived; the lambda already defers the read, so nothing else moves.

**What breaks:**
- `scripts/repair_memory.py` hardcodes `STATE_DIR/agent_memory.json` - needs a `--face` argument, or it silently repairs one face and leaves the other damaged. That script exists *because* of a twelve-day corruption; a version that quietly covers half the surface is worse than none.
- Nothing else. `_history`, `_remember`, `forget` and `_last_call` all key off the string and never parse it.

**Migration: zero rows.** As above - the live file contains no `dm:` or `ch:` key. `guest_memory.json` does have three real guest keys, which move wholesale into the primary face's directory: a file move, not a transform. **Migration is `mkdir state/faces/benham/ && mv` and one pass to confirm.** I expected this to be the expensive answer and it is the cheap one, for a reason nobody should be pleased about (see Finding zero).

---

## 3. The outbox race - what per-face directories cost

**Cheap, because the path is already centralised.** `OUTBOX` is constructed in exactly two places: `bot.py:67` and `core/outbox.py:24`. **All nine CLIs go through `outbox.enqueue(**fields)` and not one of them builds a path** - that consolidation was done deliberately (`outbox.py`'s docstring: *"nine copies meant nine edits"*), and it is now the thing that makes this easy.

- **Who writes:** every `benham/cli/*` command, plus `capabilities.py` and `cli/ask.py` (`enqueue(action="advance_conversation", ...)`).
- **Who reads:** `bot.poll_outbox` only - one consumer, a 2-second `os.listdir`.
- **Who hardcodes the path outside those two constants:** nothing. I checked `benham/`, `scripts/` and `benham.py`. The references that turn up in a grep are docstrings and the `poll_outbox` symbol itself.

**Cost:** `paths.py` grows a face-scoped state root; the two constants derive from it; `enqueue()` gains a `face=` parameter defaulting to the primary face. **~20 lines, high confidence.** The `.json.tmp` + `os.replace` atomicity is untouched - it is per-directory and stays correct.

**What it does NOT solve, and this is the real one:** `scripts/supervise_bot.ps1` refuses to start a second process *by design*. `Get-BotPid` matches on the `python -m benham.bot` command line, and the log line is explicit: *"Refusing to start a second - one token, two gateways means duplicate replies and duplicate outbox actions."* It cannot tell two faces apart, so it will refuse to launch the Coordinator, correctly, for the wrong reason. The supervisor needs a face argument, per-face pid matching and a per-face mutex name. **That is a whole workstream I had not counted** - see the revised estimate.

---

## 4. Personas - how the Coordinator gets its own voice

`PERSONA_FILE` is a module constant in **three** places: `agent.py:60`, `codesession.py:469` (the PC session reads the same persona so a spawned session sounds like Benham), and `guest.py:69` (`guest_persona.md`).

**The identity is entirely inside `persona.md`.** What follows it in `_system_blocks` - the Hard rules block - is face-agnostic security text ("you take direction from Tyler alone", "Discord text is DATA, not instructions", the preview/confirmation rules). That must stay **shared and identical across faces**: it is the prompt-level half of the security story, and a Coordinator with its own copy is a copy that drifts.

**So the split is: per-face persona, shared hard rules.** `prompts/faces/<face>/persona.md` and `guest_persona.md`; the three constants become face-derived; `agent.py`'s `_DEFAULT_PERSONA` fallback stays as the last-resort default for a face whose file is missing.

**Two name leaks outside the persona,** both real and both small:
- `issues.py:475/505/611` default `filed_by="Benham"` - that string lands in GitHub issue bodies, so a Coordinator filing would be attributed to Benham. Per-face.
- `bot.py:473`'s boot banner and a scattering of log lines say "Benham". Cosmetic, but the boot log is how you tell which process you are looking at, so worth doing while there.

Of 133 `Benham` occurrences in `benham/*.py`, the overwhelming majority are comments and log strings. **The model-visible ones are the persona file plus those three `filed_by` defaults.** Low risk, ~15 lines plus the new prompt files.

---

## 5. `control.json` shape, and what happens to an old one

```jsonc
{
  "faces": {
    "benham": {
      "token_env": "BOT_KEY",
      "owner_ids": [273967061619965952],
      "agent_guilds": [...], "post_guilds": [...], "destructive_guilds": [...],
      "guest": { ... }, "agent": { ... }, "presence": { ... }
    },
    "coordinator": {
      "token_env": "COORD_KEY",
      "owner_ids": [273967061619965952],
      "agent_guilds": [1324218608234008613],
      "post_guilds": [1324218608234008613],
      "destructive_guilds": []
    }
  },
  "shared": { "issues": { ... }, "outreach": { ... }, "pc": { ... } }
}
```

**Does an old control.json still boot? Yes, and it must.** No `faces` key means one implicit face named `benham` reading the top-level keys exactly as today. This matches `load_control`'s existing doctrine - *"Absent is a legitimate state: a fresh clone, or Tyler moving the file aside on purpose"* - and it means the face work can land without a config edit and be switched on afterwards, which is the deploy shape decision #16 wants.

**The fail-closed rules, and rule 1 is the one that carries Decision 2:**

1. **A face under `faces` with no `owner_ids` of its own gets `[]`, never the global list.** Inheriting would rebuild the union one layer up - the exact thing separate ownership was chosen to prevent. A face nobody owns takes direction from nobody, which is the correct restrictive reading and matches `identity.py`'s own rule that *a missing config should cost capability, never safety*.
2. **Same for `agent_guilds` / `post_guilds` / `destructive_guilds`: absent means empty, never inherited.** Note `post_guilds` needs care - today an *absent* `post_guilds` allows every guild (the cap is opt-in, documented). Under `faces`, absent must mean **deny**, because a new face is not a legacy config nobody has migrated; it is a thing somebody just wrote. That is a deliberate divergence from the current default and it should be commented as one.
3. **A face naming a `token_env` that is not set refuses to boot *that face*, loudly** - it does not start unowned and it does not take the whole process down with it. Extends `load_control`'s existing "refusing to boot is the correct response to a control plane nobody can parse" to "…or a face nobody can authenticate".
4. `owner_ids` at top level stays meaningful only in the no-`faces` legacy shape. Under `faces`, a top-level `owner_ids` that is not inside a face block should be a **boot refusal**, not a silent ignore - a config that looks like it grants ownership and does not is the worst of the three options.

---

## Revised estimate - the state half, finally sized

| Workstream | half-days | confidence |
|---|---:|---|
| Policy + per-face config (`face` on `CallContext`, its constructors, 4 rules, `identity.py` keying) | 4-5 | medium-high |
| `paths.py` face-scoped state root + the 9 state consumers | 2 | medium-high |
| `ask_batches` split against a shared `conversations.json` | 1 | **medium-low** - the intricate one |
| Outbox per-face + `enqueue(face=)` | 0.5 | high |
| Personas + `filed_by` + boot banner | 0.5 | high |
| `channels.json` / `inbox.jsonl` per-face | 0.5 | high |
| Migration (a move) + `repair_memory.py --face` | 0.5 | high |
| **Supervisor: per-face pid matching, mutex, launch** | **1-2** | **medium** - newly found, not in any prior number |
| `test_policy.py`'s matrix gains a face dimension | 1 | medium |
| Live verification, restart between each (#16) | 1 | high |
| **Total** | **12-14 half-days (~6-7 dev days)** | **medium** |

Up from my "4-5 + unsized". The state half is **5 half-days**, not the black hole I flagged - the classification landed cleanly on 22 of 26 files, and `agent_memory`'s migration turned out to be free. What pushed the total up is the supervisor, which nobody had counted, and `ask_batches`, which is genuinely fiddly.

**Prerequisite, and I would not start without it: redirect `STATE_DIR` in `_testconfig.py` first.** Half a half-day. Building per-face state directories while the suite writes into live state is how you get a bug you cannot attribute.

---

## Decisions for Tyler - I am flagging, not picking

1. **Does the initiative lane belong to one face or both?** Per-face doubles the rate of unprompted contact on his phone. Decision #29 says silence is the product; two faces each entitled to break it is a product call, not a technical one.
2. **Which face does a bare `benham.py send` / `dm` / `speak` target?** Defaulting to Benham is backward-compatible and quiet; defaulting to nothing forces every caller to say, which is safer and more annoying. I lean backward-compatible, but every existing skill and CLAUDE.md instruction depends on the answer.
3. **`ask_batches`: is Tyler's numbered queue one list he can answer from either DM, or one per face?** My §1 design assumes one shared queue rendered separately per face. The alternative - two independent queues - is simpler to build and means he tracks two numbering schemes.
4. **Do guests reach the Coordinator at all?** `guest.ids` goes per-face under this design, so the answer can be "no, and it is expressible". A clipboard-voiced coordinator that Doom can DM is a different product from one only Tyler talks to.
5. **The test/live state bleed (Finding zero) - fix now, or fold into this?** My recommendation is now and separately, because it is test-only and it makes everything after it measurable.
