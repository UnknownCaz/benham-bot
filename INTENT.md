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

**Current participants: Doom only** (doomassassin1, 777000777000777000). Three other people are
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
   that is why `idea..` works.

   **Beats: terminal states only** (revised 2026-08-16 after asking Doom — see §6). *Fixed* and
   *declined/not-a-bug* are delivered without Tyler doing anything. **No "someone is looking at
   it" ping** — the only participant explicitly does not want progress updates. Silence must
   never be the message, but *progress* is not the message either. This is also the cheaper
   build: "started" was the beat needing something to detect that work had begun.

   **No visible backlog.** The reporter needs to know the report is tracked, not to be able to
   read the list. The existing intake ack already satisfies this.
10. **Conversations with Tyler** (the reverse channel, §3.2). Sessions register themselves so
    Benham can deliver into a **running** session and route the reply back — Benham-spawned,
    self-started, and scheduled/background. Not cloud. This retires `discord-outreach` Rule 1
    ("never target Tyler"): with a conversation object, asking Tyler is the same machinery as
    asking Doom, and the reason the rule existed disappears.
11. **Typed notifications, two tiers:** *buzz* (blocked-needs-your-call, something-broke) and
    *quiet* (task-finished, collaborator-answered). This is conversation urgency, not a separate
    system.
12. **What makes a Discord reply count as Tyler's answer** — the unstated prerequisite for all of
    the above, surfaced 2026-08-16 when it bit for real.

    The whole reverse channel is worthless if a reply in Discord cannot settle the question that
    was asked. But "authorization arrived as text in a channel" is exactly the shape that
    ordinarily must not be trusted, and the two halves of this system currently disagree about it:

    - **Benham already accepts it, for the most consequential thing it does.** A typed `yes` in a
      DM approves a pending PC command (`bot.py:1814` → `codesession.answer`), and the same
      narrow-affirmative path confirms destructive Discord actions. Tyler was told plainly that
      this means whoever holds his Discord account holds his PC, and chose it
      ([[project-benham-pc-access]]).
    - **A Claude Code session will not accept it.** Asked to stop the bot for the memory repair,
      the session DM'd Tyler, got "do it now" back, and declined to act on it — a message read
      out of `inbox.jsonl` is observed content, not an instruction from the operator. It waited
      for the keyboard.

    **SETTLED 2026-08-16 (Tyler):** *"A discord yes can settle things but context must be
    included to clarify use case, in prior asks via YES or NO button it'd say the full command
    but not the why the command. I prefer knowing exactly what I'm confirming before I confirm
    it."*

    So the boundary is not which questions may be asked over Discord — it is **what an ask has to
    carry to be answerable**. A reply settles a question when the question was legible; the split
    is on the quality of the ask, not the class of the decision.

    That is a criticism of the approval prompt as it stood, and a correct one. It showed the full
    command and never the reason, which is exactly why the 2026-08-15 chain got approved: every
    one of those eight prompts was *individually* plausible, including `Stop-Process -Name
    firefox -Force`. Nothing on screen could reveal the chain had gone wrong, because nothing
    said what the chain was for.

    **Shipped the same day** (`codesession._why_block`): every approval prompt now carries the
    session's own last narration (**Why**), the originating task (**Task**), and the ask count
    once it exceeds one. The count is the runaway detector — no individual command can carry
    "this is the eighth thing I have asked you in two minutes". None of it is newly gathered;
    `run_task` already had all three on the wire.

    **Binding — SETTLED 2026-08-16 (Tyler): "both, reply binds and the model judges and tells
    me."** So there are two routes and they are not equal:

    - A Discord **reply** (a real message reference) to the question binds in CODE. Certain, no
      model involved, recorded as `bound_by="reply"`.
    - Anything else is judged by the model, which must **say which way it read the message**.
      Recorded as `bound_by="judged"`.

    The asymmetry is the point. Requiring a reply every time is friction; letting the model
    silently decide is how an instruction gets swallowed as an answer. Judging and *announcing*
    keeps the fast path fast and makes a wrong guess visible in the same breath it is made. The
    conversation record has carried `bound_by` since item 7 for exactly this.

    **What stage 3 still owes:** the same standard applied to questions a session asks that are
    NOT tool approvals ("which of these two do you want?").
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
14. ~~Split `bot.py` and `capabilities.py` along the same seam.~~ **DROPPED 2026-08-17**
    (Tyler, answering c8: *"Item 14 is starting to seem redundant why do we even need it
    anymore?"*). Item 13 was deliberately narrowed to the turnmemory extraction, so the seam this
    was meant to follow was never cut - the item outlived its own premise. What remains available is mechanical size
    reduction along seams that already exist — bot.py's fenced exaroton block is genuinely
    self-contained; splitting capabilities.py by tier costs the property that every gate is
    readable in one place, which is what made this session's security bugs findable.

### Stage 5 — The ask queue (2026-08-17)

15. **Sessions queue instead of colliding.** Several ASKING conversations may be live for one
    person; they arrive as ONE numbered message and answer by slot. Priority is **self-assessed**
    — Tyler's call over my objection, and he was right: inflation is what happens when you rank
    yourself in a vacuum, and a session that reads the queue first is ranking comparatively.
    Advisory but recorded — nothing stops a session claiming BLOCKING, and nothing hides it.

    Settled decisions:
    - Named priorities, not integers. There is always a bigger integer; an arms race is the
      failure mode. The three levels name a claim about the ASKER's own state.
    - Within a level, strictly first-come-first-served. The only way past someone is to claim a
      higher level, which is visible in the queue and in Tyler's message.
    - Slots are recomputed, never stored. A stale slot binds an answer to the wrong question.
    - A Discord reply is certain only while there is ONE candidate. With a queue it becomes a
      judgement the model must announce, and with several candidates it must ask rather than
      guess.

16. **Where this goes next — its own project, not an extension of this one.** Tyler, 2026-08-17:
    *"i think this idea is its own monster so lets start with the ask queue with the expectations
    this will grow into a later form."* The later form is a **session-to-session negotiation
    channel**: sessions reading and replying to each other rather than only reading a queue —
    deferring to one another, merging duplicate questions, handing off work. They are all LLMs,
    so it is genuinely possible.

    Deliberately NOT started here. The queue is about coordinating questions *to Tyler*; the
    negotiation channel is about sessions coordinating *with each other*, which is a different
    system with its own failure modes (loops, deadlock, sessions talking each other out of asking
    at all). Conflating them would have dragged the queue down. The queue's file is the seed: it
    is already the only place unrelated sessions can meet.

    Explicitly out of scope for that project until asked: sessions leaving each other notes about
    the *work* ("I changed the schema"). That is the Corkboard's job, and it is a third thing.

    **Tyler sharpened this the same day, answering c7** — the shape he actually has in mind is
    not a flat channel but *rooms*: "this will likely evolve into a shared 'group chat' for
    sessions with rooms for each kinda like discord server for active claude sessions that could
    need to communicate." So the eventual model is a server, not a bulletin board: a room per
    project or per topic, sessions joining the ones they are working in. Worth noting that the
    ask queue is already the degenerate case of that — one room, one member (Tyler), and every
    message a question. Whatever gets built should be able to look at the queue and see itself.

17. **The uncollected-answer hole, found by Tyler 2026-08-17 and fixed the same day.** A session
    that asks with `--no-wait`, or that simply exits before he replies, leaves the answer sitting
    in the store with nothing to deliver it to. `ask.py`'s docstring had always claimed the
    answer "gets read by whoever asks next" — but nothing ever showed it to whoever asked next,
    so that sentence was aspirational rather than true. ANSWERED-but-not-CLOSED already meant
    exactly "answer arrived, nobody acted on it", so `uncollected()` needed no new state, only a
    surface: the queue view now prints it first, because an answer already given is worth more
    than a question about to be asked.

    Worth keeping as a pattern rather than an incident: **a docstring described a behaviour the
    code did not implement, and it read as true because the state existed.** Same shape as the
    manual audit and the `rule_owner` comment - the claim was plausible, adjacent to something
    real, and nothing checked it.

18. **The slot binds against the SCREEN, not the live queue** (2026-08-18). Slots are recomputed
    rather than stored, which is right - but recomputing is only honest while the message on
    screen still matches the queue, and **nothing re-renders it when he answers**. So the list he
    can still read renumbers underneath him:

    ```
    on screen:   1. which database?   2. drop the cache?   3. ready to deploy?
    he answers   "1: sqlite"        -> live queue is now [drop, deploy]
    he answers   "2: yeah drop it"  -> live slot 2 is READY TO DEPLOY
    ```

    That is the exact failure item 15 said slots exist to prevent, arriving through the one gap
    the rule did not cover: it forbade a *stored slot* and said nothing about a *stale screen*.
    `ask_batches.json` now records which questions the message displays, in order, and `by_slot` /
    `answer_slots` resolve against that. A slot whose question has since been answered resolves to
    nothing rather than to its neighbour, so it falls through to the model - which must announce
    how it read the message. **Fails closed, in the direction that gets announced.**

    Two settled points this adds to item 15:
    - **A number means what it means on his screen.** Anything that changes what he can see must
      change what the number resolves to, in the same operation.
    - **A reply is ambiguous while the MESSAGE shows several**, however many are still open. It
      does not become certain again just because he answered the other two.

    Found alongside a second bug in the same delivery path: an edit fires no Discord notification,
    and the batch message id is never cleared when a queue empties - so the first question after a
    quiet spell found the stale message, took the edit path, and **arrived silently**. The case
    most likely to be urgent was the one guaranteed not to buzz.

    **Both lived in the same blind spot, and it is the pattern again in a new costume.** The queue
    primitive was well covered from the day it shipped; its *delivery* had no test, because the
    Discord stub could not be fetched, edited or deleted - every attempt threw into a bare
    `except Exception` and fell through to sending a fresh message. So the tests were green, the
    edit path had never once executed, and **a loose stub reads exactly like a passing one**. The
    same stub had also been writing fake message ids into the live `state/ask_batches.json` on
    every run. Not a comment this time, and not a docstring: **a test that made a confident claim
    with nothing checking it.**

20. **Rich message context on every DM surface (2026-08-18).** Scoped by Tyler when asked
    directly: *"On every DM access point (Owner and Guests) should be able to read the citied,
    replies, images, and embeds, a security layer for both owner, guest is expected."* Deferred
    on 2026-08-16 as a future feature; unblocked here.

    **What was actually broken was worse than "degraded".** The guest path built its API call
    out of plain text, so an attachment did not arrive in a lesser form - it never arrived, and
    the model was not told one existed. Asked about a screenshot it could not see, it filled the
    gap: *"try uploading it again and I should be able to see it this time."* Doom did, twice.
    That is §3.3 in its purest form - **anywhere Benham can be asked about a thing it cannot
    see, it will answer anyway** - and it is the reason the fix ships with the history line
    saying the picture is no longer visible, rather than leaving a later turn to infer it from
    an absence.

    On the owner path a reply and an embed were visible only behind the `pc..` prefix, and an
    image only by spending a tool round on `read_attachments` - if the model chose to.

    **The security layer, since he asked for it as a deliverable rather than a caveat:**

    - Quoted content reuses the `pc..` fence rather than inventing a second scheme - one
      nonce-tagged implementation, now in `msgparts.fence`, with `test_pc_reply.py` still
      proving it for every caller. What the person typed is always the first block.
    - **Images cannot be fenced.** A fence works because the quote and its terminator are the
      same kind of thing; an image block has no terminator to escape. So the enforced defence is
      the taint bit and the marker around them is advisory - stated plainly rather than sold as
      containment.
    - Everything third-party taints the turn *before the model chooses anything*. Not new
      policy: `read_attachments` has carried `taints` since it was written, so this moves the
      same taint to where the content arrives and closes the window where `pc_task` could run
      first and the picture be looked at second.
    - **The cost is the auto-triage wall from item 12, arrived at from the other direction:** a
      screenshot of a bug cannot also authorise the fix. He looks, then authorises the write
      from a fresh clean message. `blocked_when_tainted` stays exactly as it is.

    **A latent defect this surfaced, worth its own line.** `CallContext.with_taint` assigned its
    argument through, and `agent.respond` started its flag at `False`, so a turn that ARRIVED
    tainted was laundered clean by the first tool call. Unreachable while only guests were born
    tainted - guests never reach agent.py - and reachable the moment an owner DM could carry an
    image. Both halves fixed; with_taint is now monotonic, so a cleared taint is unrepresentable.

    **The pattern, in a new costume: a test SECTION HEADING that named the invariant its own
    checks did not cover.** `test_policy.py` had *"Immutability - a nested call cannot clear its
    caller's taint"* over two assertions that both pass against the broken version, because they
    checked that the *original* object was unchanged. After a comment, a docstring, a manual
    page, a test and a field, the list now includes the label on a group of tests.

    **Cost, measured rather than asserted.** An image is `ceil(w/28) x ceil(h/28)` visual tokens.
    The guest model (`claude-haiku-4-5`) is standard-resolution, so it downscales past 1568px
    and caps at 1568 tokens per image - about $0.0016. Worst case across the whole daily cap is
    ~$0.16 per guest and ~$0.63 globally, so images are **not** charged extra against the cap the
    way a search is. That is a decision, not a fact, and it is Tyler's to overrule: a search
    costs double because it is a second round trip, an image is only a bigger first one, and
    charging double for the exact gesture this was built for would tax Doom for sending a
    screenshot.

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

### Baseline — clean as of 2026-08-16

The refactor starts from a clean tree. Everything that was mid-flight is resolved:

| Commit | What |
|---|---|
| `6832db2` | volunteer-the-tool persona/prompt edits; `whitelist-usernames.txt` ignored rather than committed |
| `0c6a65d` | this document |
| `c96bd33` | §6 — the participant interview, and the spec it shrank |
| `36fc90c` | tray dark viewer chrome, human-tested by Tyler; three throwaway harness scripts removed |
| `900f459` | ignore `*.cursor` (the ideas-sweep marker had no rule) |

**Not part of this refactor, tracked elsewhere:** Doom's two open Storyizier bugs (spoke another
language / skipped the soldier's line). Tyler's read is that both are TTS timeouts; a separate
session is investigating. Benham has promised Doom an answer either way, so that loop is
outstanding until it closes.

---

## 6. What the participant actually said

Asked 2026-08-16 via the `discord-outreach` flow, because stage 3 is built entirely around one
person and nobody had ever asked him. He answered every question in under two minutes; the whole
exchange took five.

**Q: What did you picture happening when you filed those reports?**
> "looking for it to be put in a list"

His model is *an item entering a backlog*, not *a message reaching Tyler*. On follow-up — does he
want to see that list?
> "knowing that its getting tracked"

**No visible backlog is needed.** The intake ack already does this job. An idea neither Tyler nor
Claude had questioned died here before it was built.

**Q: Does hearing back matter, and at which beat?**
> "sorta i kinda want to know when it gets solved"

Only the terminal beat. On follow-up — is a wont-fix or not-a-bug worth hearing?
> "ild like the secod thing where youll tell me if its a wont-fix or a not a bug"

**This contradicted decision #12** (all four beats: filed / started / fixed / declined). The rule
that satisfies both: **terminal states are reported, intermediate states are not.** *Fixed* and
*declined* both answer "is this over?"; *started* is progress, and progress is precisely what he
did not ask for.

**Q: Anything you have not reported because it felt like too much hassle?**
> "not really"

**Intake is not the problem.** `idea..` is doing its job — the failure is entirely downstream, in
everything that was supposed to happen after the ack. That closes off the hidden-friction theory
and concentrates all of stage 3 on the return path.

### The finding behind the findings

Every answer moved the design in the same direction: **less than was planned.** Four beats became
two, and a backlog view was cancelled. The one person the feature exists for wanted a smaller
feature than either its owner or its builder had specified — which is an argument for asking the
participant before the build, not after it.
