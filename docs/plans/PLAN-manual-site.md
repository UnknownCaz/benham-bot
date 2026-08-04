# PLAN — Owner's manual as a local HTML site

Status: **COMPLETE** (2026-08-04). All stages shipped: scaffold (+ collapsible
sidebar), Outer (incl. full per-command CLI option docs at Tyler's request),
Inner (13 pages), badge pass, OWNERS-GUIDE stubbed, tray "Open manual" item,
full-site verification green. Capability counts reconciled against the live
registry (56, not README's stale 49). This file is now the record of how the
site was built; the site itself is the doc surface going forward.

Decisions made (Tyler, 2026-08-04): multi-page folder site · retire
OWNERS-GUIDE.md to a stub · Inner covers every functionality · dark theme +
SHIPPED/PLANNED badges + search + tray shortcut · labels stay "Inner"/"Outer" ·
every page documents its logs (see Paper trail below) · webhook identity gets
its own Inner page · HTML replaces markdown as the ongoing doc surface ·
sidebar collapses via ☰ toggle (state remembered) so text/diagrams get the
full width.

## What it is

A private, offline, multi-page site in `docs/` inside the repo. Open by
double-clicking `docs/index.html` (or the new tray menu item). No server, no
network, plain file:// links. README.md is untouched — it stays the repo-facing
doc. Runtime-loaded files (guardrails.md, persona.md, guest_*.md, control.json)
are **never modified** — only read as content sources.

## Layout

```
docs/
  index.html              main menu: Inner door, Outer door, status strip
  assets/site.css         shared styles, dark theme (default)
  assets/site.js          renders sidebar nav from one PAGES manifest + search
  inner/  outer/          one .html per topic (inventory below)
```

Shared nav/search live in `site.js` with a single PAGES manifest — the menu is
edited in exactly one place. Works on file:// because everything loads via
`<script src>`, never fetch (fetch is blocked by CORS on file://).

**Search:** sidebar box filtering the manifest (page titles, section anchors,
keywords) with jump-to-anchor. It's a fast section-finder, not full-text grep —
full text would mean duplicating every page's body into the index and letting it
drift.

**Badges:** `SHIPPED` / `PLANNED` / `OFF BY DEFAULT` pills. Every guest-refactor
feature carries one, mirroring PLAN-guest-permissions.md stages. Rule: the manual
never claims something works that doesn't.

## Page inventory — Inner ("why it's built this way")

Every page = two halves: **What it does** / **Why we chose this**.

| page | covers | sourced from |
|---|---|---|
| inner/index.html | map of the safety model, how the pieces interlock | README §Safety model |
| chokepoint.html | bot.py as sole Discord process; CLI → outbox file bus; inbox.jsonl | README §Architecture |
| tiers.html | the four capability tiers, owner gate, why tiers not per-command flags | README §Four tiers |
| confirm-flow.html | three-gate destructive flow, tokens, TTL, why the model can't self-confirm | README §Destructive actions |
| taint.html | tainted turns: stranger text gates outward actions; why web results count | OWNERS-GUIDE + README |
| pc-access.html | codesession.py, read-freely/ask-before-changing, SECRET-READ logging, why workdir isn't a sandbox | README §PC access |
| guests.html | guest system + the three invariants (code-AND-config, no guest confirmations, never your PC) | PLAN-guest-permissions.md |
| web-search.html | owner + guest search, search-as-tainted-input, logs, why double-count for guests | OWNERS-GUIDE + PLAN |
| voice.html | speak/listen/transcript, wake words, auto-reply gating and why it's guild-scoped | README §Voice |
| exaroton.html | slash commands, per-guild gating, watchdog | README §Slash commands |
| attachments.html | downloads/ quarantine, why NOT Windows Downloads, runnable-file refusal | README §Attachments |
| identity-persona.html | persona.md vs guest_persona.md, Benham-not-Tyler rule, why Discord impersonation is out | README + persona files |
| webhook-identity.html | webhook.py + webhooks.json: what posts under a webhook face, how that differs from Benham's own account, why it stays outside the chokepoint and what that costs | README §Outside the chokepoint |

## Paper trail — every page shows its logs

Every Inner page (and each Outer page where you'd check on something) ends with
a standard **Paper trail** section: which file the feature writes, one real
sample line **with IDs truncated and message content replaced**, and a one-line
"how to read it". Redaction is deliberate — the site is private, but samples
shouldn't leak friends' messages into a screenshare, and fake-but-shaped
examples never go stale. Verified mapping (formats checked against live files):

| feature | file(s) | shape |
|---|---|---|
| everything Benham sees | `inbox.jsonl` | JSONL: ts, guild, channel, author, is_self, content |
| every queued action + result | `outbox/sent/<ts>_<id>.json` + `…_result.json` | JSON pair: request, outcome |
| owner searches | `agent_searches.jsonl` | JSONL: ts, user_id, query, role |
| guest searches | `guest_searches.jsonl` | JSONL: ts, user_id, query |
| voice | `voice_transcript.jsonl` | JSONL: ts, speaker, text, contains_wake |
| guest caps | `guest_usage.json` | per-day counters, per-user + global |
| PC sessions | Claude Code transcript `*.jsonl` read by `watch_pc.py`; `SECRET-READ` lines in bot log | transcript + flagged reads |
| boot/runtime | `boot<N>.out/.err` (root), older in `logs/`, `supervise.log` | text |
| cost | `usage.py` parses the bot log | derived |
| guest code runs | `guest_runs.jsonl` | **PLANNED** (Stage 6) — page ships badge until real |

## Page inventory — Outer ("how to drive it")

| page | covers | sourced from |
|---|---|---|
| outer/index.html | task-oriented map: "I want to… → go here" | OWNERS-GUIDE intro |
| daily-driving.html | DM patterns, confirmations, refusal table, "why is it asking approval" | OWNERS-GUIDE §Daily driving |
| agents.html | the agents side by side: owner agent / guest agent / PC codesession — which fires when, how to pick, cost profile of each | README + OWNERS-GUIDE |
| cli.html | full shorthand cheat-sheet grouped (universal do.py, everyday, voice, ops), what needs bot.py running | OWNERS-GUIDE §CLI |
| guest-admin.html | add/remove, restart-as-kill-switch, workspace flip procedure, caps, guest.py status/forget | OWNERS-GUIDE §Guest admin |
| tray-restart.html | tray icon (what it is, monitor-only), Scheduled Task start/stop, supervise scripts, boot logs | README §Running it |
| health-logs-cost.html | status.py, usage.py, inbox tail, watch_pc, log locations | OWNERS-GUIDE §Health |
| config-reference.html | every control.json knob, restart-after-edit rule, most-restrictive defaults | OWNERS-GUIDE §Config + control.json.example |

## Aftermath (same change, not follow-ups)

- OWNERS-GUIDE.md → 3-line stub: "the manual is now docs/index.html".
- tray_bot.ps1 gains an "Open manual" menu item (`Start-Process` on index.html).
- README.md unchanged.

## Build order

1. Scaffold: full docs/ tree, css/js, working nav + search over stub pages — **you click around and approve the feel before any content lands**
2. Outer pages (ports existing text — fast)
3. Inner pages (what/why prose — the real writing)
4. Badge pass against PLAN-guest-permissions.md stage status
5. Stub OWNERS-GUIDE.md + tray edit
6. Verification: every link clicked on file://, search exercised, badge audit (nothing PLANNED shown as working), content spot-checked against README

## Resolved (Tyler, 2026-08-04)

1. Labels stay "Inner" / "Outer" — it's his site.
2. Every report page documents where/what its logs look like (→ Paper trail
   section above); webhook-identity added as Inner page 13.
3. Confirmed: this session's purpose is documentation cleanup for human
   accessibility; the HTML site becomes the ongoing doc surface.
