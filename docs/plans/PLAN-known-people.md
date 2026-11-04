# Known people, purposes and borrowed tools - Plan

**Status: SET IN STONE 2026-11-03 16:10 local, Caz's word "Set in stone" in chat after the four calls below were
decided on forms. INTENT decision 61, Stage 9. All three stages landed 2026-11-03; the required test is owed.** Written 2026-11-03 15:58 local by the
session that ran Biggie's game search (b78c87fb), from Caz's words in that chat.

## Purpose, in Caz's words (2026-11-03, 15:0x-15:4x local)

> "the allow list is archaic, anytime I want to have Benham go and message someone the process to reach
> from; Unknown Person to Known User (with set tooling), has genuinely been unachievable."
>
> "I want to be able to message Benham about, 'Hey can you add the Biggie, or Blank, or Doom, to the
> guest list they need help finding a game online. Message them about them being added and start up the
> conversation about finding the game', and Benham would then be able to find that person and request
> to add that person via the prompt thing, and same for the tooling for that person."
>
> "The purpose should expire the guest shouldnt. So say they need access to websearch (or something)
> they use it, and then I get a response from Benham on what was done and found with the tool, in the
> form of a friendly heads up, then I get prompted to have the tool removed, or kept."
>
> On who closes a purpose: "1 and 2, Benham should know to nudge or ask Biggie if they're done... Benham
> should be able to determine the completion state of an ask if the guest doesnt say they done."

## What today cost (the measurement behind the plan)

Getting Biggie from stranger to "Benham can talk to him" on 2026-11-03 took: his user id typed by Caz
(name lookup needed the members intent, off until that day); four hand edits of `config/control.json`
on two machines (`guest.ids`, then `outreach.people`, then `web_search`/`searches_per_turn`, then
`max_tokens`), each with a backup; three restarts, because every list is read once at boot; one Dev
Portal change. There is no `add` command: `benham.py guest` offers status, forget, off. Then the
opener went through `outreach`, whose bound-answer note turned the brain into a relay, and the 500-token
guest cap cut three searched replies off before their results. Caz: "This is awful cut this off, and
mark this as a major failure." Record: Benham board Now block, 14:28 local.

## Goals - each with its why on the same line

1. **One grant, from Caz's words, anywhere Benham hears him (DM now; channels where INTENT 49/50
   already let him @).** *Why: the whole point of Benham is reaching Caz away from the PC; a grant that
   needs ssh is not a grant.*
2. **Benham finds the person by name and puts the add up as a preview card.** *Why: his "request to add
   that person via the prompt thing" - the approve flow tier 2/3 already uses; nothing new to learn.*
3. **The person stays known for good; the purpose and any extra tool expire.** *Why: his words, "The
   purpose should expire the guest shouldnt."*
4. **Default grant is chat + web search, nothing else; any further tool is its own card, per person,
   per tool.** *Why: the guest lane's security floor is "no client tools, ever" (guest.py docstring);
   today's failure happened with zero tools; each tool a guest gets is a thing their words can make
   Benham do. Caz picks per person.*
5. **A conversation carries its purpose into the brain.** *Why: step 3 of his flow, "start up the
   conversation about finding the game" - today a chat is only history; the brain never knows the goal.
   And NEVER through `outreach` for a brain-carried chat: its bound-answer note is what relayed Biggie.*
6. **Benham closes the purpose himself: the guest says done or satisfied, or it goes quiet and Benham
   asks once ("did one of those land, or keep digging?"), or the quiet timer ends it.** *Why: his "1
   and 2"; Biggie never says done; a loop Benham cannot close is a loop Caz has to.*
7. **On close, a friendly heads-up DM to Caz: what the purpose was, what Benham did, what it found,
   which tools it used.** *Why: his words; the heads-up IS the audit, in his DM, in Benham's voice.*
8. **Under the heads-up, one card per borrowed tool: keep or remove.** *Why: his words; deciding after
   real use beats deciding up front.*
9. **Lists live in the bot's own state and take effect without a restart.** *Why: three restarts in one
   afternoon, and `guest_quiet` already learned this lesson (INTENT known bug 2026-08-20).*

## What exists and is reused (do not rebuild)

- The approve card: tier 2/3 confirm flow in `bot.py`, Approve/Deny, token for tier 3.
- `find_user` by name: works since the members intent went on 2026-11-03 (side session, pid 7560).
- The guest origin in `policy.py`: every verb unreachable by a guest unless declared `guest=True`
  (two are, today). The archived `archive/guest-tools/guest_agent.py` ran per-guest grants through the
  same `capabilities.run` chokepoint - the skeleton for goal 4.
- Conversations store (`conversations`, `conv close/bank`) for the lifecycle; `guest_note` for giving
  the brain Benham's side of a thread; `guest_quiet` for a state file with a deadline that survives
  restarts (the shape goal 9 wants).
- The owner agent's max_tokens retry (INTENT known bug 2026-10-11, `agent._create`): the guest path
  never got it. Today's cut-offs are that bug on the other lane.

## What is new

- Verbs: `add_person` (find + card + write state), `grant_tool` / `revoke_tool` (per person, per tool,
  card each), `open_job` (was `open_purpose`; DM the opener WITH the job's goal in the brain's note), and the close path
  (done-detection, one nudge, quiet timer, heads-up, keep/remove card).
- State: `state/people.json` (who is known, since when, by whose word) and `state/jobs.json` (was
  `purposes.json`; open jobs with their goal, `with`, tools and deadlines), read live, not at boot.
- Guest brain: purpose in the system note while open; the max_tokens retry ported from `agent._create`;
  `max_tokens` default raised (2000 is live since 14:25 local on both configs).

## Required test, not optional

Before any friend sees it: rehearse the FIRST TURN in the Testing Server with a fake guest id (the
`_testconfig` two invented ids), through the real path - opener with purpose, one searched reply, read
the reply text in the log for a cut-off. Then one real run with a friend Caz names, watched live.
Today's failure was on the one part that already existed; the first turn is the test.

## Out of scope here

Voice; the collaborator/project axis (INTENT 3.4) beyond what goal 5 needs; the Chillbar @ ("Not yet"
since 10-05, his call); any tool a guest can run on the PC (Phase B removed PC access, INTENT 39).

## Open calls for Caz

- ~~Quiet timer length before the nudge, and before the close~~ **DECIDED 2026-11-03 16:0x local, Caz's pick on the
  call-1 form: "1 day nudge, 1 day close."** One day of quiet, Benham asks once; another day, it closes and the
  heads-up goes to Caz.
- ~~Whether the heads-up also goes to the guest~~ **DECIDED 2026-11-03 16:0x local, Caz's pick on the call-2 form:
  "You only; guest gets the normal closing line."** The report is Caz's; the guest gets Benham's friendly close in
  the chat and nothing about the report.
- ~~Which model the guest brain runs for a purposed chat~~ **DECIDED 2026-11-03 16:1x local, Caz's pick on the
  call-3 form: "Opus 5.5 for everything."** The guest brain moves to `claude-opus-5-5`, purposed or not. One config
  line (`guest.model`) plus the restart; FLIPPED 2026-11-03 18:35 local on both configs with Stage B, pid 17310. Cost: the
  guest bill grows roughly five-fold per token; the daily caps (100/guest, 400 global) stay as the ceiling.
- ~~Biggie himself~~ **DECIDED 2026-11-03 16:1x local, Caz's pick on the call-4 form: "Wait for the plan, no retry."**
  He stays a guest; nobody messages him until this is built. The mute on Benham for him lifts at 19:28 local
  2026-11-03 (the verb caps at 240 min); if HE writes to Benham after that, the brain answers with the fixed cap.

All four calls are decided. What is left is Caz reading the whole page and saying "set in stone", or sending it back.

## Refinement after Stage A (2026-11-03 17:17 local, decision 62)

Caz: *"I want Benham to exist more as something like that ask, 'Talk to Biggie about X', or 'Create Role for XYZ, in Server F, and interview me about its permissions after it's made.'"* His pick: *"Job it, reach me in DM."* Stage B's object is a **job** (`state/jobs.json`): goal, `with` (a known person's id or `caz`), tools, state. The guest-with-a-purpose is one kind of job; the role-then-interview example is Stage 10 on the same object. A job that needs Caz midway reaches him in his DM, always. Stage A landed 2026-11-03 17:17: merge `1eec592`, 96 verbs, Mac pid 13553.

## Stage B landed (2026-11-03 18:31 local)

Merge `01b0470` = origin/master = Mac pin; 99 verbs; suite 67/67 PC and Mac. `open_job` / `list_jobs` / `close_job`,
`benham.py job`, the job note in the guest brain (precedence over the outreach notes), the guest max_tokens retry,
Opus by default and live. The `with=caz` round trip ran live on the Mac (j1 opened and closed on cards); the person
opener is proven offline only. Next: Stage C, then the required test.

**Caz's call on the hand's open items (chat, 2026-11-03 18:5x local):** on the three kept defaults shown to him (one open job per person; template opener; refuse a person's job while guest chat is off) his one pick, uncut: *"Call 1: allow several open jobs per person"*. LIFTED AND BUILT in Stage C (`4fa9f90`): a person may hold several open jobs; the brain note lists every open goal; done detection says which job closed; one heads-up per job. The other two defaults stand.

## Stage C landed (2026-11-03 19:48 local)

Merge `7e2f59f` = origin/master = Mac pin; 102 verbs; suite 68/68 PC and Mac; pid 20837. Goals 6, 7 and 8 are built:
done detection (a Haiku judge names which open job a message closed), one check-in after a quiet day and a "went quiet"
close after another (`guest.job_quiet_hours` 24), the heads-up to Caz's DM on every close (owed in state, delivered by the
60 s tick through `advance_job`, so a restart cannot lose it) with a Keep / Remove card per tool used (no answer = keep);
`grant_tool` / `revoke_tool` per person per tool on cards, and web search is the only grantable tool - a client tool for a
guest is a later stage, not this one. Several open jobs per person, Caz's call. The guest gets only the close line.
Proven live in Testing only: the cards and state. Proven offline only: the heads-up text, the cards under it, the judge.
**Brain rehearsal DONE 2026-11-03 20:01 local (the session, on the Mac):**
one `guest.respond` call with an invented id and the job note, Opus 5.5, one web search, 1116 chars back, out=1397 tokens under the 2000 cap, no cut-off, no CUT_NOTICE; the reply guessed Mega Man X7 with three alternatives and ended by asking for more detail - helping, not relaying. Fake id forgotten after.
**Owed:** the first-turn rehearsal in Testing through the real path (the opener needs a real Discord account, so a fake id cannot receive it - the brain half is the rehearsal above), then the
required test above, then one friend Caz names on his yes. j2 (`with: caz`, "stage C rehearsal") is left open on the
Mac: confirming its close sends Caz the first real heads-up, a good first human test once he says yes.
**First real heads-up DELIVERED 2026-11-03 20:20 local, on Caz's 'Yes, close j2':**
`job close j2` card -> token -> closed 20:20:15, `advance_job` sent the heads-up to his DM 20:20:23 (outward.jsonl `owner_dm` via advance_job), j2 `headsup: sent`; no Keep/Remove card because the job used no tool. His read, 2026-11-03 20:2x local, uncut: *"Reads right"*.
**The friend test WAITS for his word** - asked who and what 20:2x local, his pick uncut: *"Not tonight"*. Nobody is messaged until he names a friend and says yes to the shown opener. Biggie stays untouched.
