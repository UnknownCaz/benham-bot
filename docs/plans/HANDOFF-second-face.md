# Handoff - the second face (Codex)
Written 2026-08-21 ~23:45Z by the scoping worker, immediately before a `/clear`.
**Read this first, then `PLAN-second-face.md`.**

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

- **Do not narrow Benham's scopes on guild `1324218608234008613` (Next Big Novel).** Another
  session is restructuring that server through Benham right now - ~111 outbox actions today,
  last at 23:23Z. Commit 12 carries a precondition about this; honour it.
- **38 `set_channel_permissions` calls were REFUSED BY DISCORD today** - "Missing Access" /
  "Missing Permissions". Benham's *role* in that guild is under-privileged; my diagnosis is
  role hierarchy (several refused targets are roles that same run had just created, and Discord
  refuses edits on roles above your own). Routed to the restructure session and to Tyler.
  **Nothing surfaces a failed outbox request back to whoever enqueued it**, so that session may
  believe those 38 landed.
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
