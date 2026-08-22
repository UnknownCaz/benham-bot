# Handoff - the second face (Codex)
Rewritten 2026-08-22 ~01:20Z by the build worker, on Tyler's ask, mid-build with
nothing half-finished. **Read this first, then `PLAN-second-face.md`.**

> ## DELETE THIS FILE WHEN IT IS DONE. It has an expiry and this is it.
>
> **Delete when commit 13 is verified** - both faces live, Codex proven able to work in its own
> guild - **or the moment the second face is abandoned.** Either way this file goes.
>
> `git rm docs/plans/HANDOFF-second-face.md`
>
> **Before deleting, move anything still true into `INTENT.md`** - a decision, a gotcha, a bug
> that outlived the build. This file is scaffolding for a handover, not a record; INTENT.md is
> the record. A handoff that survives its own handover becomes stale instructions with the
> authority of a committed file - this project already lost a scoping session to exactly that.

---

## Where things stand (2026-08-22 ~01:20Z)

**Tyler said GO on 2026-08-22 (explicit, in-session: "GO - build Codex per the plan") and
confirmed the pc_task exclusion.** The build ran the same night. Of the plan's 13 commits:

| commits | state |
|---|---|
| 1-4 | **On master, deployed, live-verified** (merged 7702eff + 356dc64) |
| 5-7, 9, 10, 11 | **On master, deployed** (merged dba8316; bot restarted onto it 01:10Z, pid 11924, boot banner healthy, legacy launch line unchanged) |
| 8 | **Built and green on the branch** (`9d1e778` + sync merges), awaiting Kestra's review - the last reviewable |
| 12 | **Blocked on Tyler alone**: Codex Discord app + `CODEX_KEY` in `config/environ.env`, the `faces` config block (shape in `PLAN-second-face-spike.md` §5), invite to Next Big Novel only, Message Content intent on |
| 13 | The two-token live run. **Also needs Tyler's read of the Codex persona first** (see below) |

Branch: `claude/festive-elion-b8e2b5`, worktree `busy-gagarin-024bcc`. Suite **34/34** run
bare at the tip, which sits on master's `dba8316`. The live bot runs master; the branch holds
only commit 8 and sync merges beyond it.

## What got built, in one paragraph each

- **paths/identity (1-2):** face-scoped roots (`state_for`/`prompts_for`; the primary face
  resolves to exactly the pre-faces paths) and the two-shape control plane (`faces` block or
  legacy; four fail-closed rules - no inherited owners, absent scope is empty, absent
  `post_guilds` DENIES for a declared face, unset token refuses that face only; `faces` +
  top-level `owner_ids` refuses to boot). Tyler's real config verified byte-identical through
  the new parse.
- **policy (3-4):** `face` on CallContext, carried by both copies; every gate rule reads the
  acting face's own block; the 63x6 matrix re-asserted per face and cell-for-cell identical for
  the primary across the config migration. `rule_face_capability` adds per-face grant tables
  (primary unconfined unless it says otherwise, new faces empty until granted) and **the
  machine wall**: `pc_task`/`spawn_in_room` refuse every non-primary face WHATEVER config says
  - Tyler's confirmed decision, in code so reversing it means deleting a rule and its test.
- **state (5-8):** per-face outbox (required `face=` on enqueue, `source` provenance,
  `misdelivered()` guard at the poller), the nine per-face stores behind validated
  `BENHAM_FACE` (typo refuses the interpreter; shared stores asserted to STAY shared in a real
  codex subprocess), per-face initiative budgets (`initiative.min_gap_hours`, one number per
  face), and two independent ask queues: conversations carry a `face` stamped at open (absent
  = primary), only the carrying face's tick nudges, each face numbers only its own asks,
  per-face `ask_batches.json` makes cross-face binds unrepresentable. The nudge-cap worker's
  `asker_session`/`nudge_cap` fields ride through untouched, their tests green.
- **identity surfaces (9-11):** per-face personas (Hard-rules block stays shared on purpose),
  `FILED_BY` from the process face, boot banner leads with the face; `--face` required on
  every CLI call, parsed once in benham.py, with the codesession deny covering the flag-first
  evasion; per-face supervisor (legacy names byte-identical for benham, `--face` argv marker
  for others, `_launch_face_problem` refuses a marker/env mismatch), and bot.py already boots
  through `face_boot_problem` + the face's own `token_env` - **so commit 12 is config-only.**

## What is waiting on Tyler, in order

1. **Create the Codex Discord app + token.** `CODEX_KEY` in `config/environ.env`, invited to
   **Next Big Novel only**, Message Content intent on. Blocks commit 12; everything else is done
   or in review.
2. **Read the Codex persona draft** - `prompts/faces/codex/persona.md`. Written to his chosen
   voice (warm but businesslike, a ledger not a mentor, never breaks frame, in-world "the
   Codex", never "EclipseUI"/"Artifact") but it is HIS character in HIS project's server. On
   the board as a READ item; commit 13 should not run before his read.
3. Nothing else. All four question rounds from the scoping night are answered and recorded in
   `PLAN-second-face.md`.

## Who else is in this, and the working rules

- **Kestra (hub, `local_7608a4d7-0c60-4471-96b6-d8ce234627b0`)** runs the review-and-merge
  flow: worker commits on the branch, Kestra reviews the diff, merges --no-ff, restarts,
  verifies the boot. **All git merge-flow happens through Kestra or is announced first** - two
  sessions ran git in the main checkout concurrently tonight and composed cleanly only by
  luck. The worktree is the worker's alone.
- **The nudge-cap worker** shipped `asker_session` + `nudge_cap` (a19a276) and the outbox
  failure-surfacing work, then released both surfaces. Nothing further planned there.
- **Raven (courier lane, `RAVEN.md` in the Claude root)** reads conversations across ALL
  faces - which is why `conv show` always prints `face:` and `conv list` tags non-primary
  faces. Do not remove that visibility; a codex conversation invisible to collection rebuilds
  the c18 rot one face over.
- **Announce before touching shared surfaces** (conversation record shape especially), per
  ORCHESTRATOR.md section 7. It worked tonight - the commit-8 collision was caught by an
  announce, sequenced by Kestra, and built conflict-free after release.

## Do not break these

- **The primary face is byte-identical everywhere, by construction.** state_for(benham) IS
  STATE_DIR, the legacy launch line carries no marker, the old mutex name survives, absent
  config fields mean what they always meant. Every commit's tests pin this; a change that
  breaks "declaring faces changes nothing for benham" is wrong even if it looks cleaner.
- **The machine wall stays code.** Granting pc_task/spawn_in_room to a non-primary face must
  cost deleting `rule_face_capability`'s wall and its test, never a config edit. Tyler's
  confirmed call.
- **`--face` is required on every CLI call** (deploy-visible since dba8316; 2am merge chosen
  deliberately). The docs that teach it: repo README, discord-proxy SKILL.md, RAVEN.md,
  discord-outreach SKILL.md (flag note; full rewrite is Raven's task), and the ask-queue line
  in Tyler's global CLAUDE.md. A doc found still teaching the bare form is stale - fix it.
- **A permission failure mode Codex will meet, because server admin is its future job:** apply
  allows FIRST, verify the returned overwrite contains them, only then fire any deny - a bot
  that denies @everyone view_channel before granting itself an explicit allow locks ITSELF out,
  unrecoverably from the bot side (the 38-refusal incident, 2026-08-21). And discord.py returns
  `view_channel` as `read_messages` - grepping payloads for the literal name reports 100% false
  drift.
- **Codex is a character, not a process** (INTENT #4). One identity, never breaks frame,
  honesty unchanged. The persona draft exists but is NOT settled until Tyler reads it.

## If this session is gone and you are picking up cold

Read `INTENT.md` first (the reference; 33+ settled decisions - do not re-litigate), then
`PLAN-second-face.md` (the build plan with Tyler's answers folded in), then
`PLAN-second-face-spike.md` §5 (the config shape commit 12 writes). The Benham corkboard holds
the decision history. Check `git log master..claude/festive-elion-b8e2b5` for what still awaits
merge; run the suite bare (`python run_tests.py`, exit code first). The live bot must never
share a token with a second process - the supervisor and mutex exist for that, and restarts go
through the supervisor, announced.
