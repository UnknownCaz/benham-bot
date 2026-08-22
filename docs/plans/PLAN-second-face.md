# Benham + Codex - the build plan
2026-08-21 · 13 commits, ~13.75 half-days (~7 dev days) · staged one behaviour per commit, restart between each (decision #16)
**Nothing starts until you approve this sequence.**

---

## One thing you have to do that I can't

**Codex needs a Discord application and a bot token.** That's the Dev Portal, your account, your click - I can't create it and shouldn't. It's needed at **commit 12**, not before, so the first eleven commits can land while it doesn't exist. When you make it:

- give it a bot user, copy the token into `config/environ.env` as `CODEX_KEY`
- invite it to **Next Big Novel** only
- it needs Message Content intent (same as Benham); Members/Presences stay off

Tell me the moment it exists and I'll wire it. Until then commit 12 is blocked and everything before it isn't.

---

## What your four answers changed

| Answer | Effect on this plan |
|---|---|
| **Reduced capabilities**, then **AMENDED to grant tier 3** | **New workstream** either way - nothing gates capability by face today. Adds commit 4 (~1 half-day), mirroring `rule_guest` rather than inventing a mechanism. The amendment changed *why* that commit exists; see the flag on it. |
| **Codex only in Next Big Novel** | Config at commit 12 - and it uncovered a live restructure plus a contradiction between two of your answers. Both below. |
| **Codex holds admin there** | Tier-3 scoping comes free from the existing per-guild `destructive_guilds`. No new code. |
| **Provenance + failure reasons** | Folded into commit 5 (+0.25). |
| **Warm but businesslike** | Persona at commit 9. No structural effect. |
| **Show the plan first** | This document. |

### The detail at commit 12 - REVISED, and my first version was aimed at the wrong half

I originally proposed keeping `post_guilds` to protect whatever wrote that 22:46 message. **That was wrong, and it protected the half that doesn't matter.** Traced it: the 22:46 post is one action inside a **live Next Big Novel restructure being driven right now by another Claude session through Benham**.

Measured from `state/outbox/sent`, today:

| count | action |
|---:|---|
| 47 | `set_channel_permissions` |
| 11 | `list_channels` |
| 9 | `create_role` |
| 8 | `create_channel` |
| 8 | `add_role` |
| 7 | `pin_message` |
| 7 | `send_message` |

111 requests today, **100 of them in the 22:00-23:23 window**, last at 23:23 against 23:33 now. Zero pending in `outbox/`, so it is keeping up, not stalled. 73 requests across the sent history carry guild `1324218608234008613` - Next Big Novel.

**Those are agent-tier and destructive-tier actions, not posting.** Keeping `post_guilds` protects nothing here; dropping `agent_guilds` + `destructive_guilds` is precisely what would break it.

**Two consequences, and the second is the one that matters:**

1. **Commit 12 gains a hard precondition: the restructure must be finished.** Not "Tyler confirms `post_guilds`" - confirmed complete. The human prerequisite already buys time; this makes it explicit rather than lucky.

2. **Decisions 1 and 2 contradicted each other and nobody noticed, including me** - decision 1 gave Codex no tier 3, decision 2 moved the server to Codex, and together no face could administer it. **RESOLVED: Codex gets tier 3. Decision 1 is amended.** One face owns Next Big Novel completely, conversation and admin.

**And that resolution comes with a free win.** `destructive_guilds` is already per-guild, and under the per-face design it becomes per-face-per-guild. So putting **only** Next Big Novel in Codex's `destructive_guilds` gives the precise outcome - **Codex holds tier 3 in the server it coordinates and nowhere else** - by composing two mechanisms that already exist, with no new code.

**Commit 12 gets simpler and its precondition gets harder.** With Codex holding admin, Benham drops Next Big Novel *entirely* - agent, destructive and post - so the `post_guilds` question I got wrong dissolves on its own. But: **Benham's scopes on that guild must not change until (a) the restructure is confirmed finished AND (b) Codex is live and proven able to do that work.** Between those two points the capability gap would be real.

### Related, found while tracing - and my first diagnosis of it was WRONG

38 `set_channel_permissions` calls failed in that guild today. I reported them as current,
unnoticed, and caused by role hierarchy. **All three were wrong.** The session that made those
calls had already diagnosed and fixed them hours earlier; the new roles sat at position 1 with
Benham at position 3, so Benham was always *above* what it was editing.

**The real mechanism, and it matters because it is squarely Codex's future job:** that session
denied `@everyone` `view_channel` on a category **before** granting Benham an explicit allow.
Benham's access to those channels ran through `@everyone`, so the deny cost it view - and
without view it could no longer edit overwrites, move the channel, or undo its own change.
Self-inflicted, one-way from the bot side, only a human grant could recover it.

**Rule for Codex's permission work: apply allows FIRST, verify the returned overwrite actually
contains them, and only then fire any deny. Never infer success from readability.**

**Gotcha for whoever writes the verifier: discord.py returns `view_channel` as `read_messages`**
- same permission bit, `view_channel` is an alias. Grepping a `before` payload for the literal
string `"view_channel"` reports 100% false drift.

**Status: that restructure is finished and verified** - re-applied as an idempotent check,
35/35 already correct, 0 errors. Benham currently holds full permissions on that guild because
Tyler granted them to clear the lockout above.

**What survived from my report, and it is the part that matters:** those 38 failures were
visible to me at all only because they sat in `outbox/failed/` where nothing surfaces them.
See the commit 5 note.

## Phase 1 - Foundations (no behaviour change at all)

**1. `paths.py` gains face-scoped roots.** `state_for(face)` and `prompts_for(face)`, with the default face `benham` resolving to **exactly today's paths**. Every existing file stays byte-identical in place.
*Verify:* suite green; assert resolved paths are unchanged for the default face. **No restart needed - nothing observable changed.** ~1 half-day, high confidence.

**2. `identity.py` learns `faces`.** Parses the `faces` block; **no `faces` key = one implicit face reading top-level keys**, so your current `control.json` boots untouched. The four fail-closed rules from the spike: a face with no `owner_ids` of its own gets `[]` (never the global list - that's Decision 2's whole point); guild lists absent means empty; `post_guilds` absent means **deny** for a named face (deliberate divergence from today's allow-everything default, commented as one); a face naming an unset token env var refuses to boot *that face*, loudly, without taking the process down.
*Verify:* parse your real `control.json` through the new code and assert `OWNER_IDS`/`GUEST_IDS`/`ISSUER_IDS`/`AGENT_GUILDS` come out identical - the same check that caught nothing on the readable-config change and is why I trust it. **Restart after this one.** ~1.5 half-days.

## Phase 2 - The chokepoint

**3. `face` on `CallContext`.** Added to `__slots__` and the six constructors; `rule_agent_guild`, `rule_posting_scope`, `rule_destructive_guild`, `rule_guest` read the face's block instead of module globals. **`test_policy.py`'s origin×capability matrix gains a face dimension** - the largest single test cost in the job.
*Verify:* the matrix, plus every existing policy assertion unchanged for the default face. ~2 half-days, **medium** - this is permission code and the riskiest slice.

**4. `rule_face_capability` - per-face capability grants.** Each face config carries a capability allowlist; anything not on it is denied. Modelled directly on `rule_guest`: fail closed, distinct refusal reasons, and passing means returning `None` so later rules still run.

> **Justification, single and load-bearing: per-face grant expression, required by Decision 1 as amended.**
> Without this commit there is no per-face `destructive_guilds` to put Next Big Novel into, so Codex holds tier 3 **everywhere or nowhere**. The "free win" above depends on this rule existing - the amendment that killed the original premise ("Codex cannot purge a channel") is the same one that made this load-bearing.
> Stating one real reason rather than two co-equal ones on purpose: two reasons where only one is real is how item 14 got built twice and dropped twice.
>
> **ASSUMPTION, MINE, KNOCK IT DOWN CHEAPLY IF WRONG:** I am excluding `pc_task` from Codex. You said Codex holds *admin over the server*; I am reading that as not implying *a shell on your machine*. **You did not actually say that** - it is my inference, and it is the weakest thing in this plan. One word from you now costs nothing; finding out at commit 4 costs a rework of the grant table.

*Verify:* a test asserting the exact set Codex can reach, that `pc_task` refuses from Codex's face with the right rule name, and that Codex's tier 3 is confined to Next Big Novel. ~1 half-day, medium-high.

## Phase 3 - State per face

**5. Outbox per face.** Two path constants derive from the face; `enqueue()` **requires** `face=` (your answer: `--face` on every call). ~0.5 half-days, high.

> **CONFIRMED for this commit: outbox provenance + failure reasons - and a third thing that is now the most valuable of them.**
> Working out who enqueued the 22:46 post took clustering timestamps, because **no request file records who enqueued it**; working out why 38 requests failed took reading the bot log, because **a request in `outbox/failed/` does not record why it died**. Both are one-liners while `enqueue()` is open for the required `face=`.
>
> **But the real gap is bigger than either: the fire-and-forget enqueue path never surfaces a failure back to the caller.** A request fails, lands in `failed/`, and whoever asked for it is never told. **With two faces that is worse, not better** - a coordinator whose permission edits silently no-op is a specific, foreseeable disaster.
> **Two narrowings since first written (2026-08-22), both of which shrink this commit.** First, the gap is narrower than reported: `do.py`'s wait path already blocks on the result and exits non-zero, so the blind spot is the fire-and-forget path only. Second, **a separate session owns fixing it in code - it is not part of this plan.** Commit 5 keeps the two one-liners (~0.75 half-days) and rebases on whatever that fix does to `enqueue()`; result-surfacing is off this plan's books. The question this note used to ask you is withdrawn - it has an owner.

**6. The nine per-face stores.** `agent_memory`, `agent_searches`, `guest_memory`, `guest_usage`, `guest_quiet`, `guest_searches`, `channels.json`, `inbox.jsonl`, plus `repair_memory.py --face`. **Migration is `mkdir` + `mv`** - the classification is already done and `agent_memory` is empty as of tonight's purge, so there is almost nothing to move. ~2 half-days, medium-high.

**7. Initiative per face, separate budgets.** Your decision. `initiative.json` + `initiative-log.md` per face. **I'm making `UNPROMPTED_MIN_GAP` per-face-configurable while I'm in here** - not because you asked, but because at four faces that's the number you'll want to change, and doing it now makes it a config edit instead of a refactor. ~0.5 half-days.

**8. `ask_batches` per face - two independent queues.** Your decision, and it deleted the intricate part: no shared-queue reconciliation, no cross-face bind hazard. Still writing the regression test Kestra asked for (a batch id from face A must not bind a reply in face B). ~0.5 half-days, **up from medium-low to high confidence** because of your answer.

## Phase 4 - Identity

**9. Personas per face.** `prompts/faces/<face>/persona.md`. **The Hard-rules block stays shared and identical** - it's the prompt-level half of the security story and a per-face copy is a copy that drifts. Codex's persona written here: warm, businesslike, subject is always the work; "the Codex" in-world, never "EclipseUI", never "Artifact". Plus `filed_by` per face and the boot banner naming which face it is. ~1 half-day.

**10. CLI `--face` required.** The flag, a clear error when it's missing, and **11 documented call sites** updated (7 in repo README/plan docs, 4 in `discord-proxy/SKILL.md`). ~0.5 half-days.

## Phase 5 - Running two

**11. Supervisor.** `supervise_bot.ps1` currently refuses to start a second process *by design* - `Get-BotPid` matches the `python -m benham.bot` command line and cannot tell two faces apart. Needs a face argument, per-face pid matching, per-face mutex name. **This is the workstream nobody had counted and it scales linearly with N.** ~1.5 half-days, medium.

**12. Codex goes live.** Config block, token, persona, invite.
**Two hard preconditions, both outside my control, and both DEMONSTRATED rather than declared:** (a) the Discord app exists, (b) the Next Big Novel restructure is confirmed finished, **and Codex has successfully completed a real permission edit in that guild.** Not "Codex is live" - Codex has actually done the work. After tonight's 38 refusals that is not a hypothetical failure mode: a face can be up, authenticated, and still unable to touch the server it owns. Benham's scopes on that guild change only per the admin-capability answer, which is a separate decision from the character question. **Blocked on both.** ~0.5 half-days.

## Phase 6

**13. Live end-to-end.** Both faces up, DM each as yourself, DM Codex as Draco, confirm separate memory / separate queues / separate outboxes / Codex refused at tier 3. ~1 half-day.

---

## Where the restarts fall

After commits **2, 3, 4, 6, 7, 9, 12**. Commits 1, 5, 8, 10, 11 are inert until something uses them. Decision #26 applies throughout: green suite → merge and restart, announced not requested.

## Where I expect trouble

1. **Commit 3** is permission code and the only slice I'd call medium. If it goes wrong it goes wrong quietly, which is why the matrix test comes with it and not after.
2. **Commit 11**, the supervisor - PowerShell process matching is fiddly and I can't fully test it without two real tokens.
3. **Commit 6** is where a wrong per-face/shared call shows up as a silent bug of the kind INTENT §7 is a list of. The classification is written down; I'll follow it rather than re-deciding per file.

## Total

**~13.75 half-days (~7 dev days)**, medium-high. Up from 11-13 because your reduced-capability answer added commit 4 - which I think is the right trade: one half-day now for per-face grants that keep Codex's tier 3 confined to the one server it coordinates.

## The handoff file

`HANDOFF-second-face.md` carries its own expiry and this plan owns pulling the trigger: **delete it when commit 13 is verified, or the moment the second face is abandoned**, moving anything still true into `INTENT.md` first. No separate task tracks this - a trigger-gated chip for it was retired as premature, so if this plan is rejected, deleting the handoff (and marking these PLAN docs superseded) is part of the rejection.

**Sequence is yours to approve, change, or reorder.**
