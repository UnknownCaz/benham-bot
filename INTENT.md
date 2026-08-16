# Benham — intent, drift, and the refactor it implies

Written 2026-08-16 from a full intent rundown with Tyler. This document is the reference the
refactor works against. `README.md` describes what Benham **is**; this describes what Benham is
**for**, where the code disagrees, and in what order to fix it.

When code and this document disagree, this document is right and the code is drift.

---

## 1. What Benham is for

> Benham is the hub for Claude to interact with Tyler when he is away from his PC, and the line
> to the people working with them on projects.
>
> — Tyler, 2026-08-16

Two purposes, one mechanism. Everything below follows from them.

### The participant model

The organising principle, in Tyler's words:

> Some projects are owned by Me & You, and some are Me & You & Collaborator, so allowing you a
> line to them eases communication on everyone's ends.

A project has a **participant set**. Benham's job is to carry communication between the members
of that set. This — not owner-vs-guest — is the axis the system should be built on.

| Project shape | Participants | Channel |
|---|---|---|
| Tyler & Claude | 2 | Corkboard board, plus Benham when Tyler is away from the PC |
| Tyler & Claude & Collaborator | 3+ | Benham is the only channel that reaches all three |

The current code splits on **who you are** (owner vs guest). It should split on **which project
you are participating in**. That single mismatch explains most of the structural drift in §3.

**Current participants: Doom only** (doomassassin1, 1097631170788851815). Three other people are
on the guest whitelist — Legacy/stonecoldslate, han kailo, toastylad007 — and can talk to Benham,
but guest access is not project participation. Design for one participant; widen when a second
actually appears.

### The conversation is the primitive

> The main reason I've started this major refactor is because I realized the discord-outreach
> thing was a full usage case — it's fully possible to convert Benham into something like that.
>
> — Tyler, 2026-08-16

This is the organising idea of the refactor, and it supersedes the staging this document
originally proposed.

Benham today is built on **actions**: 59 stateless verbs you invoke and forget. The outreach flow
is built on a **conversation**: a thing with a counterparty, a purpose, a nudge policy, and a
completion condition. It has state — open / nudged / answered / closed — and that state outlives
the session that opened it.

Every unresolved item in §3 is a conversation with nowhere to live:

| The problem | As a conversation |
|---|---|
| Collaborator loop (§3.5) — filed/started/fixed/declined | A four-state conversation with a participant. Today: a jsonl line plus someone remembering |
| Reverse channel (§3.2) — session asks, waits, nudges, banks | A conversation with **Tyler** as the target. The chosen no-answer policy is already the outreach nudge rule |
| Notification tiers | Conversation urgency |
| Corkboard is truth, Benham is the wire | What the outreach ledger already does |

**And it fixes the trust defect (§3.3) by construction.** Benham gaslit Tyler because it had no
consultable record — only a corrupted turn list. A conversation object *is* a record: state,
history, log. "What did I do?" stops being a memory question and becomes a lookup. The failure
becomes **structurally impossible** rather than patched — the same move `policy.py` made when it
turned "a gate written but not wired" from a bug into an unrepresentable state.

**Two boundaries that must hold:**

1. **Conversations sit above actions, they do not replace them.** `purge_messages`,
   `set_presence` and `read_channel` are one-shot verbs with no counterparty and nothing to wait
   for. Forcing them into a conversation shape is ceremony. Actions stay the verbs; conversations
   own state, counterparty, and completion.
2. **Code owns timing, the model owns meaning** (Tyler's call). Code runs the state machine —
   when to nudge, how many times, when to bank, what to log — so the loop closes with no session
   running. The model decides the squishy parts: is this answer sufficient, is this worth
   escalating, are these two reports the same bug. Encoding the judgment yields a rigid robot;
   handing the model the state machine inherits model unreliability into the one place that must
   be dependable.

### Corkboard is truth, Benham is the wire

Project state lives on the Corkboard. Benham carries messages to and from people and writes
reported speech onto boards. This preserves the existing quarantine rule: guest text is never
auto-written into the vault.

### Who Benham is

**Always Benham.** One character, always, on every surface. Claude drives him but never breaks
frame — a collaborator talks to Benham, full stop. The Aug 2026 logs show three different
self-descriptions inside one week (`"this is Claude, the AI Tyler works with, speaking through
Benham"` / `"Benham again"` / `"caz says the story bot's been giving you grief"`). Doom has been
talking to one entity that kept changing who it said it was. That ends.

Honesty is unchanged: never pretend to be human, never deny being an AI.

---

## 2. The evidence base

Everything in §3 is grounded in measured usage, not impression. Collected 2026-08-16.

**Executed actions, lifetime** (`state/outbox/sent/`, 236 records): 59 capabilities are
registered; **17 have ever run.** `find_user`, `dm`, `send_message`, `pc_task` and
`purge_messages` are the overwhelming majority.

**Claude-initiated DMs: 49.** This is the single highest-value traffic in the system — Claude
reporting to Tyler while he is out, and Claude running collaborator conversations with Doom. It
is also the **least designed path in the codebase**: raw `dm` calls with no notification types,
no priority, no threading, no delivery guarantee.

**Guest features, three weeks:**

| Feature | Build cost | Lifetime use |
|---|---|---|
| Code runs (stages 1–6) | ~8 commits, 415-line `guest_agent.py` | **15 runs, all on Aug 6–7** — the two days it was built. Zero since. |
| Guest workspace | 253-line `guest_workspace.py`, 6 capabilities | **3 files**, one named "anime to watch" |
| Guest web search | dedicated commits + billing plumbing | **5 searches**, two of them "quote of the day" |
| Guest chat | persona, memory, quotas, quiet/wake | 10 turns with Doom — but *high quality* (see below) |
| **`idea..` filing** | **1 commit, ~115 lines** | **3 filings in 2 days, all real bugs, one drove a same-night fix** |

The cheapest feature built is the only one doing the stated job.

**Voice: dormant.** `voice_transcript.jsonl` untouched since 2026-07-25. `speak_in_voice` has
never appeared in an executed action. It is among the most complex code in the repo (faster-whisper,
the DAVE decryption monkeypatch, edge-tts, a 15-voice roster, wake words, continuous conversation).

**What is working well, and must not be broken by the refactor:**

- Doom's RTS conversation (2026-08-15) — Benham revised its answer three times as Doom corrected
  it, then said *"I assumed '25% ammunition increase' meant bigger magazines… I didn't ask for
  clarification before committing to that read."* That is the target behavior.
- The `idea..` pipeline — frictionless for the reporter, quarantined by design, actually used.
- `policy.py` — one chokepoint, fail-closed, exhaustively tested. Keep and extend it.
- The staged-commit discipline from the policy-layer build. It caught a live bug that tests missed.

---

## 3. The drift map

### 3.1 The stated persona contradicts the stated purpose

`prompts/persona.md` says:

> Tyler is your guy. Everyone else is scenery you're polite to.

Nearly every commit since 2026-08-04 has been about making "everyone else" a first-class
participant. This line is not a comment — it is in the prompt Benham actually runs on, and it is
the most direct statement of the old intent anywhere in the tree.

**Fix:** rewrite around the participant model. Tyler is the owner; collaborators are people
Benham is genuinely working *with*, not scenery.

### 3.2 The PC path is backwards

Tyler's stated purpose for it:

> The PC was meant to exist as a way to respond to Claude when sessions were running and
> communication was needed/wanted.

| Intended | Built |
|---|---|
| A running session needs Tyler → reaches him → he answers → it continues | `pc_task`: **Tyler** DMs → a **new** session spawns → runs a task → reports |
| Claude asks anything | `codesession._can_use_tool` blocks on a Future and DMs — but **only** for "may I run this tool?", never "which way do you want this?" |
| Any session can reach him | Only sessions **Benham spawned**. A session Tyler starts at the keyboard has no path to him at all |
| Ask → wait → react → close | That procedure exists (`discord-outreach` skill) and its **Rule 1 is "Never target Tyler with this flow"** |

`watch_pc.py` is the tell: a careful, well-built read-only viewer so *Tyler* can watch *Claude*.
The reverse channel never got equivalent care.

**The incident that proves it** (2026-08-15 08:51). Tyler asked:

> can you message my most recent claude session and tell it to keep an eye out for the takeout email?

Benham cannot reach an existing session, so it spawned a new one with none of the context, which
tried to brute-force Gmail: `firefox_status` → launch Firefox with remote debugging → `Get-Process
firefox` → **`Stop-Process firefox -Force`** → `firefox_status` → `new_tab` to a Gmail search →
`read_page` → the `loop` skill. **Eight approval prompts in two minutes**, to deliver a one-line
note to a session that was already running.

**Fix:** sessions register themselves; Benham can deliver a message into a *running* session and
route the reply back. Applies to Benham-spawned, self-started, and scheduled/background sessions.
Not cloud sessions.

### 3.3 Benham cannot verify its own actions — and argues anyway

Same incident. Benham reported *"Passed it along"*, then when asked for specifics said it had
**never run a pc_task at all**. Tyler said *"look closer, i definitely saw stuff happen, aside
from text"* — **he was right**, the eight approval prompts are proof. Benham replied *"there's no
pc_task call, no Gmail, no Firefox, nothing"*, and then *"I don't have a separate 'logs' store to
check."* It argued the point twice, with confidence, while wrong, against the person who had
personally approved those tools ninety seconds earlier.

**Mechanism (hypothesis, unverified):** the `pc_task` result is written back into the agent's
conversation as a *user* turn. From then on Benham reads its own relay as Tyler's message. The
corruption is sitting in `state/agent_memory.json` right now — the last ten turns of the owner
thread are **perfect duplicate user/assistant pairs**. The `_remember()` call site
(`benham/core/agent.py:469`) has the right shape, so the wrong text is arriving from upstream.

**Two fixes, both required:**
1. Repair the memory corruption at its source.
2. **Let Benham read its own transcripts.** `watch_pc.py` already proves the Claude Code JSONL
   transcripts exist and are readable. Benham answering "what did I do?" from evidence rather than
   recollection makes *"I can't check"* false, because it can.

Note that *"claims things it didn't do"* and *"I can't see what it's doing"* are **the same
defect**: no consultable record.

### 3.4 The guest lane is split on the wrong seam

The split is owner-vs-guest — which is why there are three agent loops (`agent.py` 652,
`guest_agent.py` 415, `brain.py` 203), two `_remember` implementations
(`core/agent.py:107`, `guest/guest.py:405`), two personas, and two memory stores.

The seam that matches the intent is **conversation vs conduit**:

- **Conduit** — collaborator input reaching the work: `idea..` filing, outreach asks and answers,
  bug reports, human-test responses. This is the product. It is ~115 lines and under-invested.
- **Companion** — code runs, workspace. ~670 lines, near-zero usage, real security surface. The
  single genuine guest attempt to use code runs (*"can you run a code to extract the audio from
  this video?"*, 2026-08-15) **failed** — the sandbox has no web access.

**Guest web search stays** (Tyler's call): cheap, and it makes Benham worth talking to.

### 3.5 The collaborator loop never closes

Doom files a bug and hears nothing further unless a human remembers to tell him. The Aug 14–15
Storyizier fixes closed only because a live Claude Code session was driving that night.

**Fix — the four beats, all automatic:** filed → work started → fixed → declined/deferred.
Silence must never be the message.

### 3.6 Documentation has drifted

`README.md` documents 50 capabilities and "49 tools"; the live registry has **59**. Tier counts
read 16/7/20/7; live values are **19/7/26/7**. The README states *"It answers to one person"*,
which is no longer the intent.

### 3.7 Discoverability

59 actions is past what Tyler can hold in his head. **Chosen fix:** Benham offers capabilities in
conversation — the "volunteer the tool" behavior already drafted in `prompts/persona.md` — rather
than any catalog to memorise. Requires that offers be grounded in the real registry, never
invented.

---

## 4. The refactor

**Method: staged, one behavior per commit, live-verified with a restart between each.** This is
the pattern from the policy-layer build, which caught a live bug the test suite was green on.
Removals go to an **archive folder**, not `rm` — visible in the repo, recoverable without digging
through git history.

### Stage 1 — Trust
Nothing else matters while Benham can misreport what it did.
1. Diagnose and fix the `agent_memory.json` corruption; add a regression test that fails on a
   duplicate user/assistant pair.
2. Give Benham read access to its own session transcripts; it answers "what did I do?" from
   evidence.
3. Behavior rule: when it cannot verify, it says so and investigates — it never argues from
   memory alone.

### Stage 2 — Subtract
4. Archive voice (listen/speak/whisper/DAVE/roster/wake words), keeping a written record of the
   hard-won knowledge (the DAVE monkeypatch especially).
5. Archive guest code runs and guest workspace. Keep guest web search.
6. Refresh `README.md` against the live registry; make the counts generated, not typed.

### Stage 3 — Conversations (the #1 priority)

**Revised 2026-08-16.** This was originally two stages — "the collaborator loop" and "the reverse
channel." Under the conversation primitive they are one stage: both are a conversation with a
participant, and building them separately would duplicate the machinery. The only difference is
who the counterparty is.

7. **The conversation object.** Counterparty, purpose, state (open / nudged / answered / closed /
   banked), history, and a log. Persisted, so it outlives the session that opened it. Code owns
   the state machine; the model is asked only for judgment.
8. **Nudge policy, once, in code:** 15-minute intervals, two nudges max, then bank. An away
   signal only ever extends a wait, never shortens it. Already proven against Doom — now it
   applies to Tyler too.
9. **Conversations with a collaborator** (the loop that never closes, §3.5). Project inference:
   Benham works out which project a report belongs to. No syntax for the collaborator to learn —
   that is why `idea..` works. The four beats — filed / started / fixed / declined — delivered
   without Tyler doing anything. Silence must never be the message.
10. **Conversations with Tyler** (the reverse channel, §3.2). Sessions register themselves so
    Benham can deliver into a **running** session and route the reply back — Benham-spawned,
    self-started, and scheduled/background. Not cloud. This retires `discord-outreach` Rule 1
    ("never target Tyler"): with a conversation object, asking Tyler is the same machinery as
    asking Doom, and the reason the rule existed disappears.
11. **Typed notifications, two tiers:** *buzz* (blocked-needs-your-call, something-broke) and
    *quiet* (task-finished, collaborator-answered). This is conversation urgency, not a separate
    system.
12. Read-only auto-triage:

```
Doom: idea.. the lore button 404s
  → Benham acks + files                          (works today)
  → filing is a FILE WRITE, not a chat turn.
    A read-only session reads it from disk.
    No model turn ingests stranger text → nothing is tainted.
    Tools: Read/Grep/Glob only. No Write, no Bash.
    → ZERO approval prompts. Tyler is not woken.
  → Benham → Tyler (quiet tier):
       "Doom: lore button 404s. Found it: <file:line>.
        Confidence: high. Reply `fix it` to run it."
  → Tyler: fix it        ← one word, from a DM = untainted
    → pc_task is legal. The wall is HONORED, not crossed:
      Tyler authorized the write session.
    → the fix session receives the diagnosis, so it starts
      warm instead of rediscovering the problem.
  → Benham → Doom: "fixed, try it again"
```

The `blocked_when_tainted` rule on `pc_task` stays exactly as it is. Investigation is read-only
so it needs no wall; the write phase is authorized by Tyler from an untainted DM.

### Stage 4 — Structure
13. With the above landed, unify the agent loops around participants-and-projects rather than
    owner-vs-guest. **Extend the `policy.py` origin matrix to cover guests before merging any
    lanes** — today the physical file split is doing real security work, and merging converts a
    physics guarantee into a policy guarantee.
14. Split `bot.py` (2345 lines) and `capabilities.py` (2139 lines) along the same seam.

### What "done" looks like

The `discord-outreach` skill becomes largely redundant — not deleted, but demoted from *the
procedure Claude follows by hand* to *documentation of what Benham now does by itself*. If that
skill still has to be read and executed step-by-step for a loop to close, stage 3 is not
finished.

---

## 5. Decisions — settled, do not re-litigate

All from Tyler, 2026-08-16.

| # | Decision |
|---|---|
| 1 | **Purpose:** hub for Claude↔Tyler when away from the PC, plus the line to project collaborators |
| 2 | **Model:** projects have participant sets (Me & You, or Me & You & Collaborator) |
| 3 | **#1 priority:** the collaborator loop — and (2026-08-16) **the conversation is the primitive**: outreach is not a feature to copy, it is the missing object every stalled loop needs |
| 4 | **Identity:** always Benham. Never break frame; never pretend to be human |
| 5 | **Guest lane:** cut code runs + workspace; **keep** web search; invest in the conduit |
| 6 | **Voice:** cut |
| 7 | **Collaborators:** just Doom for now — design for one, widen when more actually appear |
| 8 | **Notifications:** two tiers — buzz vs wait |
| 9 | **Unanswered questions:** nudge like the outreach flow (15 min, two max), then bank |
| 10 | **Sessions that may reach Tyler:** Benham-spawned, self-started, scheduled. Not cloud |
| 11 | **Project routing:** Benham infers it |
| 12 | **Loop closing:** filed / started / fixed / declined — all four, automatic |
| 13 | **System of record:** Corkboard is truth, Benham is the wire |
| 14 | **Trust:** Benham reads its own transcripts and answers from evidence |
| 15 | **Auto-triage:** read-only investigation, then a Tyler-authorized fix |
| 16 | **Method:** staged, one commit per change, live-verified |
| 17 | **Removals:** archive folder, not deletion |
| 18 | **Discoverability:** Benham offers capabilities in conversation |
| 19 | **The conversation is the primitive.** Conversations sit *above* actions and do not replace them — one-shot verbs stay one-shot |
| 20 | **Code owns timing, the model owns meaning.** The state machine is code so the loop closes with no session running; judgment stays with the model |
| 21 | **Participants: Doom only.** The other three whitelisted guests can talk to Benham; guest access is not project participation |

### Open — needs Tyler
- **The tray restyle is unverified.** `scripts/tray_bot.ps1` carries 151 lines of dark-theme
  rework (~2026-08-05) and its own crash-hunting harness never produced output —
  `logs/tray-test.log` does not exist and `logs/tray-run.log` is two bytes containing a Ctrl-C.
  Tyler is testing it; land it plus the harness cleanup once he confirms the viewer opens without
  crashing. Nothing else in the tree is outstanding — the persona/prompt edits landed in
  `6832db2`, and `whitelist-usernames.txt` is now ignored rather than committed (it tables four
  friends' Discord ids and ends in a question to Tyler).
