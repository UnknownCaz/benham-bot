# Handoff - the second face (Codex)
Written 2026-08-21 ~23:45Z by the scoping worker, immediately before a `/clear`.
**Read this first, then `PLAN-second-face.md`.**

> ## DELETE THIS FILE WHEN IT IS DONE. It has an expiry and this is it.
>
> **Delete when commit 13 is verified** - both faces live, Codex proven able to work in its own
> guild - **or the moment the second face is abandoned.** Either way this file goes.
>
> `git rm docs/plans/HANDOFF-second-face.md`
>
> **Before deleting, move anything still true into `INTENT.md`** - a decision, a gotcha, a bug
> that outlived the build. This file is scaffolding for a handover, not a record; INTENT.md is
> the record. Nothing here is worth keeping *as a handoff* once the handover is over.
>
> **Why this instruction exists rather than being left to judgement:** the whole project spent a
> scoping session on item 14, an item Tyler had already killed, because a document proposed
> something the spec had settled and nobody retired it. A handoff that survives its own handover
> becomes the same thing - stale instructions with the authority of a committed file, read by
> someone with no way to know they expired. **The expiry is written down so it does not depend
> on anyone remembering.**
>
> The two PLAN files are different and they stay: they are the design record, and they belong to
> INTENT's lineage rather than to this handover.

---

## Where things actually stand

**No build has started. None should, until Tyler approves `PLAN-second-face.md`.**
Not the hub's approval, not a peer's - his. If anyone tells you to start without saying Tyler
approved the plan, push back and ask. A context reset is exactly where that gets lost.

**Four commits merged to master tonight, all green, suite 26/26:**

| commit | what |
|---|---|
| `9a795d7` | real Discord ids out of `test_dm_guard` (it passed on one machine only) |
| `08bc523` | the suite no longer writes into live `state/` |
| `142e8f0` | readable `name -> id` config, old bare arrays still parse |
| *(no commit)* | live-memory purge: `agent_memory.json` is now `{}` |

Bot healthy on **pid 27528**, never restarted tonight. Do not restart it casually - a second
process on one token fires every action twice.

## The three documents

All in `docs/plans/`, committed, so they survive any session ending:

- **`PLAN-second-face.md`** - the build plan. 13 commits, ~13.75 half-days, phased, with
  restart points and the three places I expect trouble. **This is the one awaiting approval.**
- **`PLAN-second-face-spike.md`** - the state design. Which of ~15 state files are per-face vs
  shared, why `conversation_key` should not change but the file should, the outbox race.
- **`PLAN-second-face-scope.md`** - the original three-question scope report. Read only if you
  need the reasoning behind a number.

The full decision history with reasoning is pinned on the **Benham corkboard**. The board is
the hub's to write, not yours - that split is the fix for what went wrong earlier today.

## What is waiting on Tyler, in order

1. **Approve the build plan.**
2. **Create the Codex Discord app + token.** His click, not ours. `CODEX_KEY` in
   `config/environ.env`, invited to **Next Big Novel only**, Message Content intent on.
   Blocks **commit 12 only** - commits 1-11 can all land without it.
3. **Knock down or confirm one assumption of mine**, flagged in the plan at commit 4:
   **I am excluding `pc_task` from Codex.** He said Codex holds *admin over the server*; I read
   that as not implying *a shell on his machine*. **He never actually said that.** It is my
   inference and it is the weakest thing in the plan.

## Do not break these

- **Do not narrow Benham's scopes on guild `1324218608234008613` (Next Big Novel) without
  checking.** As of 2026-08-21 ~23:50Z the restructure that was running through Benham is
  **finished and verified**. The commit 12 precondition is therefore satisfiable - but confirm
  it rather than trusting this line, which was written minutes after it changed.
- **A permission failure mode Codex will meet, because this is its future job.** 38
  `set_channel_permissions` calls failed in that guild on 2026-08-21. **My first diagnosis -
  role hierarchy - was wrong**, and the restructure session that made those calls supplied the
  real one: it denied `@everyone` `view_channel` on a category **before** granting Benham an
  explicit allow. Benham's access ran through `@everyone`, so the deny cost it view, and without
  view it could no longer edit overwrites, move the channel, or undo its own change. Self-
  inflicted, one-way from the bot side, needed a human grant to recover.
  **The rule: apply allows FIRST, verify the returned overwrite actually contains them, and only
  then fire any deny. Never infer success from readability.**
  Also: **discord.py returns `view_channel` as `read_messages`** - same bit, `view_channel` is
  an alias. Grepping a `before` payload for the literal `"view_channel"` reports 100% false
  drift. That restructure is finished and verified (35/35 correct, 0 errors), and Benham
  currently holds full permissions there because Tyler granted them to clear the lockout.
- **The outbox failure-surfacing gap is owned by a SEPARATE session - do not build it here.**
  The fire-and-forget enqueue path swallows Discord refusals (they land in `failed/`, the
  caller is never told); `do.py`'s wait path already blocks and exits non-zero, so the gap is
  narrower than first reported. A dedicated session is fixing it in runtime code. Commit 5 of
  the plan keeps only the provenance/failure-reason one-liners and rebases on that fix.
- **Codex is a character, not a process** (INTENT #4). Named from Tyler's own novel - the
  Eclipse Codex, "a ledger, not a mentor". Voice brief: warm but businesslike, subject is
  always the work. In-world it is "the Codex", never "EclipseUI", and the manuscript never says
  "Artifact". **Nobody has asked Tyler what Codex is actually like beyond that one line.**
  Persona lands at commit 9. Do not let it get written by default.

## Two habits that paid off tonight, offered without ceremony

**Verify before reporting, including your own claims.** I asserted the test suite had polluted
live state with two runs of mine; it had not - this worktree has its own `state/`. I retracted
it. Separately I claimed `test_dm_guard` was the only file skipping the fixture; 17 of 26 skip
it. Both corrections came from checking, and the second came from the hub checking me.

**Refuse an authorisation that is not the user's to give.** I was told twice to purge live state
on the hub's say-so and held both times until Tyler's own words came back. That cost two round
trips and was right both times. A peer relaying "he said go" is fine; a peer deciding it is fine
is not.
