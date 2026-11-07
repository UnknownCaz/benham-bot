# PLAN: Benham forum posts - tags and pinning (opened 2026-11-06 23:17 local)

## Caz's words (the go)
- 2026-11-06 ~22:2x (to the forum-draft session, relayed): do the draft by hand, but give Benham tags and pinning
  before the real forum goes on Fuckass Friends.
- 2026-11-06 23:16 (to the server session): "Can you send out a couple Opus agents to properly scope and build the
  fix? the first 2 should get their grounding the deepest before starting the build."

## The gap (found by the forum-draft session, 2026-11-06)
1. `create_thread` has no `applied_tags`; no verb edits a thread. Forum posts cannot be tagged at creation or after.
2. `pin_message` pins a message in a channel; a forum POST is pinned with `thread.edit(pinned=True)`. No verb does that.
First use: the draft forum in the Testing Server, #v2-mod-votes-draft (1568460979757387908): tag the 10 mod posts
(each carries its tag on a "Kind:" line) and pin "How voting works" (1568461985614536724).
Forum-draft session's rough scope: `create_thread` gets `tags=` (names -> the forum's ForumTag objects ->
applied_tags) plus one new action that sets an existing post's tags and pinned state; ~40 lines in
`benham/core/capabilities.py`; tests (the `do --list` contract, the pinned test tables); identity tier + preview
wiring; a bot restart on cazzy-mac.

## Shape of the work (three Opus hands, effort high)
- Scout A (read-only): the CODE. How `create_thread`, `pin_message`, `react`, `edit_channel` are written (decorator,
  identity tier, params, needs_guild, outward/taints, preview, errors), how `ctx.channel`/`ctx.guild` resolve threads,
  the tests that pin the verb list and tiers (`tests/`, `scripts/gen_readme.py`), and discord.py's forum API
  (`ForumChannel.available_tags`, `create_thread(applied_tags=...)`, `Thread.edit(applied_tags=, pinned=)`, the
  limits: max 5 applied tags, one pinned post per forum?). Writes section "A. Code" below.
- Scout B (read-only): the LAW and the DEPLOY. INTENT.md: every decision that governs adding a verb (tiers, previews,
  outward actions, taints, what needs Caz's yes, the "doc is law" rule), the manual-site/README generation, and
  exactly how code gets from this tree to cazzy-mac and restarted (README "Where the bot runs", launchd
  `com.caz.benham-bot`, `python benham.py restart`, scp/git pull?, health check), plus what the 4 dirty files in
  `git status` are so the builder does not sweep them into a commit. Writes section "B. Law and deploy" below.
- Builder (after both): implements on a branch, tests green, README/manual regenerated if the contract demands,
  commits (never the scouts' unrelated dirty files), deploys per B, restarts, proves with the first use on the draft
  forum (tag the 10 posts, pin the rules post), writes section "C. Build log".

## Hard rules for every hand
- INTENT.md is law; if code and doc disagree, the doc is right. Nothing posted anywhere but the Testing Server draft
  forum, and there only the first-use proof. No DMs. Never print tokens. Local-time stamps read from the clock.
- The bot is live for Caz's friends: a restart is brief but real; the builder announces it in the build log with
  the measured time. If a tool call is refused, stop and report it exactly.

## A. Code (Scout A writes)

Written 2026-11-06 23:25 local by Scout A (read-only). `caps` = `benham/core/capabilities.py`, `dpy` =
`C:\Users\Tyler\AppData\Local\Programs\Python\Python312\Lib\site-packages\discord` (discord.py **2.7.1**, Python
3.12.3 on the PC - the Mac's version is Scout B's to confirm). Live registry today: **102** verbs
(READ 28 / SPEAK 12 / MANAGE 49 / DESTRUCTIVE 13), measured by importing caps.

### A1. How a verb is written

- **`@action(name, tier, summary, params, needs_guild, outward, taints, always_confirm, posts, origins,
  blocked_when_tainted, guest)`** - caps:116. Registers an `Action` (caps:58-113) into `REGISTRY`. Import-time
  guards: a param named `action|queued_at|confirm_token|face|source` raises (caps:143-150); `guest=True` with
  destructive/posts/outward/always_confirm/blocked_when_tainted or no `Origin.GUEST_DM` raises (caps:151-169).
- **Field meanings** (docstring caps:59-72): `outward` = other people see new content from Benham, or access changes
  - deliberately NOT pinning/renaming ("approving trivia"); gated by `policy.rule_outward_tainted` (policy.py:588)
  once the turn is tainted. `taints` = the RESULT can carry third-party-written text (the docstring names topics,
  nicknames, role, emoji and guild names; thread and tag names are the same kind). `posts` = writes content into a channel -> `rule_posting_scope` allowlist
  (policy.py:538) + crowd-ping check (policy.py:1236). `needs_guild` = **declarative only**: run() never reads it;
  it is fingerprinted and pinned by tests (test_registry.py:46, test_moderation.py:456). `origins=None` =
  `policy.DEFAULT_ORIGINS`.
- **The five neighbours:**
  - `react` caps:2381 - SPEAK, `outward=True`; catches `discord.HTTPException` itself to name the emoji (2391).
  - `pin_message` caps:2443 / `unpin_message` 2452 - MANAGE, all flags False; `ctx.message()` then `m.pin()`;
    returns `{"status": "pinned", "message_id": ...}`. test_injection.py:94,190 and test_policy.py:280 pin it as
    NOT outward and free when tainted.
  - `create_thread` caps:2476-2514 - MANAGE, **`posts=True`** (comment 2471-2475: a forum thread IS a post, so
    it answers to the posting allowlist). Params: `channel_id` int req, `name` str req, `content` str,
    `message_id` int, `auto_archive_minutes` int. Forum detect = `isinstance(ch, discord.ForumChannel)` (2491;
    MediaChannel is the same class with type media). Forum path: `ch.create_thread(content=..., **kw)` returns a
    `ThreadWithMessage`; `getattr(t, "thread", t)` unwraps (2504). Raises **ValueError** for bad combos
    (2494, 2497). Returns `{"status": "created", "thread_id", "name", "starter_message"}`.
  - `archive_thread` caps:2517 - MANAGE, flags False, param **`thread_id`** (the precedent name for a thread);
    `t = await ctx.channel(p["thread_id"])` then `t.edit(archived=...)`.
  - `edit_channel` caps:2776-2799 - MANAGE, `taints=True` (because it returns before/after `channel_dict` with
    the topic). Builds `kw`, raises **ActionError** "nothing to change - pass at least one field" when empty,
    `ch.edit(reason=VIA, **kw)`, returns `{"status": "edited", "before", "after"}` re-read via `ctx.channel`.
  - `set_channel_permissions` caps:2862 - MANAGE, `outward=True, taints=True`; uses `type: "list"` params
    (`allow`, `deny`) and validates names against discord.py's own table (caps:2810-2847). Refuses a thread with
    ActionError ("A thread inherits its parent channel's").
  - `create_channel` caps:2738-2773 - the **forum-tag precedent**: `tags` is `{"type": "str", "desc": "forum only:
    comma-separated tag names"}`, split on `,`, stripped, `ValueError` if non-forum or > `_MAX_FORUM_TAGS = 20`
    (caps:2735 - that is the per-FORUM available-tag cap, not the per-post one).
- **Param schema:** `{"type": "int"|"str"|"bool"|"list"|"flags", "required": bool, "desc": str}`.
  `validate()` caps:7880: coerces only present non-None keys (`_coerce` caps:7858; bool from str accepts
  1/true/yes/y/on; list from a bare str = ONE item, caps:7869-7873), missing required -> ActionError, unknown keys ->
  ActionError, `guild_id` always accepted. **An empty string survives** (`tags=""` reaches the handler as `""`), so
  "clear all tags" is expressible.
- **Gotcha for `type: "list"`:** agent.py:297 tells the model list items are `{"type": "object"}`, and
  `policy.screen_params` (policy.py:1470) screens **str values only** - a list is never privacy-screened. Both are
  reasons to keep `tags` a **str** (comma-separated), exactly like `create_channel`.
- **Resolution:** `Ctx.channel(cid)` caps:239 = `client.get_channel` (state.py:1818 -> guild `_resolve_channel`
  dpy guild.py:812 = `_channels` OR `_threads`, so cached ACTIVE threads resolve) then `fetch_channel` (archived
  threads resolve here). NotFound/Forbidden -> ActionError. `Ctx.guild` caps:253 is sync and cache-only.
  `Ctx.member` caps:259, `Ctx.message` caps:269. `_infer_guild` caps:7898 already resolves `thread_id`.
- **Errors:** both styles exist. `run()` (caps:7916) catches `discord.Forbidden` -> ActionError "this bot's role lacks
  the permission" (8014) and `discord.HTTPException` -> ActionError "Discord rejected" (8019); ValueError is NOT
  caught - the agent shows it as `FAILED: ValueError: ...` (agent.py:848), the outbox poller records it via its
  generic except (bot.py:2994). **Use ActionError for new code**; ValueError only where the neighbour already does.
- **Results:** a dict; `"status"` is the conventional first key; `_sensitive` keys are stripped from the log
  (caps:8024-8027). There is no per-verb "message" string - the caller formats the dict.
- **Preview wiring:** there is none per verb. A tier<3 verb only gets a preview if a rule returns CONFIRM (taint on an
  outward verb, or a crowd ping in `content` of a `posts` verb); the preview is then `describe_call` (caps:8031),
  which never calls the handler and names `channel_id/user_id/guild_id/message_id/role_id` + `content`. Optional
  nicety: add `thread_id` and `tags` to its `bits` so a confirm card shows them.

### A2. What pins the verb list (what the builder must update)

| change | must update | how |
|---|---|---|
| param added to `create_thread` | nothing fingerprinted (params/summary are NOT fingerprinted, test_registry.py:24-27) | if the **summary** text changes: re-capture `do-list` |
| new verb | `tests/registry_fingerprint.json` | `python tests/test_registry.py --update`, read the diff (it is the security review) |
| new verb | `tests/test_guest.py:105` hard count **103** (102 live + the `pc_task` test double from `_testconfig.py:200`) | edit to **104** |
| new verb | `tests/fixtures/cli-words/do-list.json` (full `do list` output, summaries included; compared by `test_cli_words.py`) | `python tests/cliwords.py --capture --only do-list` (also re-captures `do-list-tier`/`-bad-tier`; they should not change) |
| new verb | README.md tier table + count (GENERATED markers) | `python scripts/gen_readme.py`; `EXAMPLES` (gen_readme.py:38) needs no change |
| new verb | docs/ manual (`docs/inner/tiers.html` etc., GENERATED tier-table + count) | `python scripts/gen_manual.py` |
| any | the check that runs both generators | `tests/test_docs.py` (fails if either is stale) |

There is no `do --list`: the contract is **`python benham.py do list`** (benham/cli/do.py:33). Full suite:
`python run_tests.py` (standalone scripts, PASS/FAIL scan; no pytest). Baseline measured 23:2x: test_registry ALL
PASS (102), test_guest ALL PASS (103), test_docs ALL PASS. test_cli_words NOT measured (ran >2 min, stopped) - the builder runs it first for its own baseline. Fakes for a handler test: test_moderation.py:370-427
(`_Client` with get_channel/fetch_channel, driven through `capabilities.run`). Because `create_thread` uses
`isinstance(ch, discord.ForumChannel)`, a fake forum must subclass `discord.ForumChannel` (override
`available_tags`/`create_thread`) or the forum branch never runs.

### A3. discord.py 2.7.1 forum API (read from the installed source)

- `ForumChannel.available_tags` (dpy channel.py:2579) -> `Sequence[ForumTag]`; `get_tag(id)` (2586).
  `ForumChannel.flags.require_tag` (flags.py:1764) = a post must carry a tag.
- `ForumTag` (channel.py:2274): `id`, `name` (<= 20 chars), `moderated` (only `manage_threads` may add/remove it),
  `emoji`; `str(tag)` = name. A hand-built `ForumTag(name=...)` has `id == 0` - never pass one as an applied tag.
- `ForumChannel.create_thread(..., applied_tags: Sequence[ForumTag])` (2905, 2920) -> sends
  `[str(tag.id)]` (3031-3032).
- `Thread.edit(..., pinned=bool, applied_tags=Sequence[ForumTag], archived=, locked=, reason=)` (threads.py:582).
  `pinned` sets flag bit 1<<1 on the CURRENT flags (657-659; "only works if the thread is part of a forum");
  `applied_tags` **replaces** the set (661-662; "up to 5 tags"). "The thread must be unarchived to be edited"
  - pass `archived=False` in the same call for an auto-archived post. Returns a NEW `Thread` from the response.
  Needs `manage_threads` (the thread's creator may change name/archived/tags on non-moderated tags only).
- `Thread.add_tags(*tags)` / `remove_tags(*tags)` (threads.py:668, 706) merge rather than replace.
- Read-back: `Thread.applied_tags` (threads.py:274, resolved through the parent's `get_tag`), `Thread.flags.pinned`
  (flags.py:1759).
- **Limits:** 5 applied tags per post (discord.py docstrings, threads.py:625, 679). 20 available tags per forum
  (caps:2735). **One pinned post per forum: UNVERIFIED** - Discord's UI allows one; whether the API refuses a second
  pin or silently moves it is not in discord.py. Don't guess: the handler checks the parent's cached threads for
  another `flags.pinned` post and refuses, naming it. The first-use proof pins only one post, so it is not at risk.
- **Name -> tag:** match on `unicodedata.normalize("NFKC", name).casefold().strip()` (caps already imports
  unicodedata). Accept a numeric tag id as an escape hatch (a tag name may itself contain a comma). Zero matches ->
  ActionError listing the forum's tag names; two matches (Discord does not obviously forbid near-duplicates once
  case-folded) -> ActionError "ambiguous", never a guess. Dedupe by tag id; > 5 -> ActionError before Discord.

### A4. Proposed diff (pseudo-code)

New verb **`edit_forum_post`** - MANAGE, next to `archive_thread` (its inverse is itself). Flags by its neighbours:
`outward=False, posts=False` (like `pin_message`/`archive_thread`: no new content, nobody's access changes),
`taints=True` (like `edit_channel`: it echoes tag names and the post name, which others wrote), `needs_guild=False`,
`origins=None`, `guest=False`. (Taints is a judgement call against INTENT - Scout B's law wins.)

```python
_MAX_POST_TAGS = 5   # per post; _MAX_FORUM_TAGS (20) is per forum

def _norm_tag(s): return unicodedata.normalize("NFKC", str(s)).casefold().strip()

def _resolve_forum_tags(forum, raw):
    """'a, b' -> [ForumTag]; ids accepted; ActionError on unknown/ambiguous/>5."""
    names = [x.strip() for x in str(raw).split(",") if x.strip()]
    out, seen = [], set()
    for n in names:
        hit = forum.get_tag(int(n)) if n.isdigit() else None
        hits = [hit] if hit else [t for t in forum.available_tags if _norm_tag(t.name) == _norm_tag(n)]
        if not hits:
            raise ActionError(f"{forum.name} has no tag {n!r}. Its tags: "
                              + (", ".join(t.name for t in forum.available_tags) or "none"))
        if len(hits) > 1:
            raise ActionError(f"tag {n!r} is ambiguous in {forum.name}: "
                              + ", ".join(f"{t.name} ({t.id})" for t in hits))
        if hits[0].id not in seen:
            seen.add(hits[0].id); out.append(hits[0])
    if len(out) > _MAX_POST_TAGS:
        raise ActionError(f"a forum post carries at most {_MAX_POST_TAGS} tags, got {len(out)}")
    return out

# create_thread: add param  "tags": {"type": "str", "desc": "Forum only: comma-separated tag names"}
#   if p.get("tags") and not is_forum: raise ValueError("tags= is forum-only ...")   # neighbour style
#   applied = _resolve_forum_tags(ch, p["tags"]) if is_forum and p.get("tags") else []
#   if is_forum and ch.flags.require_tag and not applied: raise ActionError("this forum requires a tag - pass tags=")
#   forum path: ch.create_thread(content=content, applied_tags=applied, **kw)   # only when applied
#   result += {"tags": [t.name for t in applied]}  (only when is_forum)

@action("edit_forum_post", identity.MANAGE,
        "Set a forum post's tags and/or pin it to the top of its forum. tags replaces the "
        "post's tags (names from the forum, comma-separated; blank clears them).",
        {"thread_id": {"type": "int", "required": True, "desc": "The forum post (its thread id)"},
         "tags": {"type": "str", "desc": "Comma-separated tag names, max 5; '' clears"},
         "pinned": {"type": "bool", "desc": "true pins the post, false unpins it"}},
        taints=True)
async def _edit_forum_post(ctx, p):
    t = await ctx.channel(p["thread_id"])
    forum = getattr(t, "parent", None)
    if not isinstance(t, discord.Thread) or not isinstance(forum, discord.ForumChannel):
        raise ActionError(f"{t} is not a forum post - pin_message pins a message in a channel")
    if "tags" not in p and "pinned" not in p:
        raise ActionError("nothing to change - pass tags= and/or pinned=")
    before = {"tags": [x.name for x in t.applied_tags], "pinned": t.flags.pinned}
    kw = {}
    if "tags" in p:
        kw["applied_tags"] = _resolve_forum_tags(forum, p["tags"])
    if p.get("pinned") is not None:
        if p["pinned"]:
            other = [x for x in forum.threads if x.id != t.id and x.flags.pinned]
            if other:
                raise ActionError(f"{forum.name} already has a pinned post ({other[0].name}, "
                                  f"{other[0].id}) - unpin it first with pinned=false")
        kw["pinned"] = bool(p["pinned"])
    if t.archived:
        kw["archived"] = False          # Discord refuses edits to an archived thread
    new = await t.edit(reason=VIA, **kw)
    return {"status": "edited", "thread_id": t.id, "name": new.name, "before": before,
            "after": {"tags": [x.name for x in new.applied_tags], "pinned": new.flags.pinned},
            "unarchived": bool(kw.get("archived") is False)}
```

Then: fingerprint `--update`, test_guest 103->104, re-capture `do-list`, `gen_readme.py`, `gen_manual.py`, a new
`tests/test_forum_posts.py` (fake forum subclassing `discord.ForumChannel`: unknown tag, ambiguous, >5, case-insensitive
match, `tags=""` clears, pinned refusal when another is pinned, non-forum refusal, archived post gets
`archived=False`, create_thread forwards `applied_tags` only in a forum). First use: `do edit_forum_post
thread_id=<post> tags="<Kind>"` x10, then `do edit_forum_post thread_id=1568461985614536724 pinned=true`.

## B. Law and deploy (Scout B writes)

*Scout B, read-only, written 2026-11-06 23:26 local (clock read in the call before writing). Every claim
carries a pointer. "My reading" = Scout B's opinion, never Caz's rule. Nothing was run, sshed or posted.*

### B1. The law for this build

1. **The doc is law.** INTENT.md:7 "When code and this document disagree, this document is right and the
   code is drift." Decision 16 (INTENT.md:1194) staged, one commit per change, live-verified; §4
   (INTENT.md:322) "one behavior per commit, live-verified with a restart between each".
2. **No forum decision exists.** Zero hits for forum / applied_tags in INTENT.md, README.md, docs/; only
   `pin_message` (message pins). The home is **decision 60 / Stage 8** (INTENT.md:1240, 1032-1120), goal line
   "Anything and everyhting an admin can do through the UI, I should be able to do through asking Benham
   and prompts." Precedent for adding to the closed stage: the Stage D follow-up (INTENT.md:1093-1097,
   `list_channel_permissions`, "*Follow-up, his yes 2026-10-31 07:48 local ("Yes, add it")*", merge 1499b4e
   naming decision 60) and the fitter (INTENT.md:1053-1068). So the doc expects: a `*Follow-up, his go
   2026-11-06 23:16 local (...)*` paragraph in Stage 8, in its own "INTENT:" commit (shape of b70925b,
   73e7181), and "INTENT 60, Stage 8 follow-up" in every subject and the Merge line. Quote his 23:16 words
   uncut (plan line 6); label the 22:2x words as relayed (plan line 4 is the forum-draft session's
   paraphrase). My reading: no new decision row is owed if no gate widens and no policy rule moves; if the
   builder makes the new verb `outward`/`always_confirm` or touches policy.py, that is a posture change and
   wants a row.
3. **Tiers.** identity.py:33-36 READ 0, SPEAK 1, MANAGE 2, DESTRUCTIVE 3. MANAGE gate = owner only (README:78;
   gen_readme.py GATES); destructive = allowlist + dry-run + token (README:85-105, decision 37). Owner only =
   `rule_owner` (policy.py:327): non-owners converse, never direct. Precedent flags from
   tests/registry_fingerprint.json: `pin_message` {tier 2}, `archive_thread` {tier 2}, `edit_channel`
   {tier 2, taints}, `create_thread` {tier 2, posts}, `edit_message` {tier 2, outward}, `move_channel` /
   `edit_server_settings` {tier 2, outward, always_confirm, needs_guild, taints}.
4. **outward** (capabilities.py:60-66): other people see new or changed content from Benham, or someone's
   access/identity changes - "Deliberately narrower than 'changes something': pinning a message, renaming a
   channel or setting a presence are all changes, but nobody is harmed by an unwanted one, and gating them
   would mean approving trivia". Its only effect: `rule_outward_tainted` (policy.py:569-592) returns CONFIRM
   when the turn is tainted. tests/test_policy.py:280-281 pins "pin_message stays free even tainted (not
   outward)". Stage 8 made whole-server creates and settings changes outward + always_confirm
   (INTENT.md:1049-1050, 1076-1077, 1081-1083).
5. **taints** (capabilities.py:67-70): the result can carry third-party text; true for every read, names are
   attacker-writable. Stage 8: "Lists are READ and taint" (INTENT.md:1051-1052, 1090). `edit_channel` taints
   because it returns before/after names (capabilities.py:2781-2784).
6. **posts** = `rule_posting_scope` (policy.py:523-546): content only into `post_guilds`; no yes unlocks it.
   `create_thread` became posts=True when `content` landed (capabilities.py:2471-2475). Testing
   736988645562646619 is on `faces.benham.post_guilds`, `agent_guilds` and `destructive_guilds` (PC copy of
   control.json, which mirrors the Mac's per decision 59; read 23:2x, ids only).
7. **Previews.** needs_confirm = destructive or always_confirm (capabilities.py:105-113); only those get a
   dry-run + tap/token (README:85-105). A taint-induced CONFIRM never runs the handler to describe itself
   (policy.py:575-586).
8. **My reading on flags (the builder's call, stated in C):** the edit verb (tags / pinned on an existing
   post) sits with `pin_message` / `archive_thread`: MANAGE, not outward, not always_confirm, no dry-run;
   taints=True if its result echoes tag or thread names (edit_channel precedent). `tags=` on `create_thread`
   keeps that row (posts=True, outward=False). The fingerprint diff is the security review and goes in C
   (INTENT.md:1107-1109; tests/test_registry.py:15-21). Side note, NOT for this build: `create_thread` is
   posts but not outward while `send_message` is both, so a forum post's content escapes outward_tainted -
   flag it to Caz, never flip it quietly (a fingerprint change and a posture call).
9. **Standing permission vs his yes.**
   - Standing, decision 26 (INTENT.md:1206): with a green suite, merging to master and restarting are
     DEFAULT - "announced, not requested"; verifying the boot is part of it. It does not cover choosing the
     change, red tests, or permission config. Why: §4 INTENT.md:322-329.
   - The go for the change itself: his 23:16 (plan line 6).
   - Needs him: anything outside the Testing draft forum; the friends server is touched only by Caz, from his
     DM, after a stage ships (INTENT.md:1111-1113; decision 59). Any control.json edit (none needed). Widening
     `faces.codex.capabilities` (INTENT.md:1116) - a new verb reaches no declared face until granted
     (policy.py:383-427); leave it. Loosening any policy rule (INTENT.md:1115-1116).
   - **Flag, rings:** the global CLAUDE.md puts the Testing Server in ring 2 ("RadioTower, the Testing
     Server... My yes per post"). Tagging/pinning existing posts adds no words, and the first use is in this
     plan's hard rules (plan lines 12-13, 35-36) - but that first-use line is the forum-draft session's, not
     a quote of Caz. My reading: run the first-use edits only on the server session's word that his go covers
     them.
10. **Model-facing strings:** the privacy screen (decision 55) and the Caz-name rule (decision 58) cover every
    new string (INTENT.md:1115); the summary and param descs say Caz, never his legal name.
    `policy.screen_params` screens tier-1+ free-text params (INTENT.md:1235), so tag names pass through it.
11. **Guests:** guest=False is the default and guest-proof by construction (capabilities.py:88-94, 116-170).
12. **CLI words** (decision 38, INTENT.md:1218): no verb's words change; re-pinning a fixture is deliberate
    and named in the commit (tests/cliwords.py:37-39). A new verb adds lines to `do-list` (9a8f12c: "do-list
    gains its two lines").
13. **Tracked tree:** no other people's user ids (INTENT.md:35-38); tests use invented ids (tests/cliwords.py
    "The same invented ids _testconfig uses"). Channel and message ids of the Testing forum are fine.

### B2. What a verb change regenerates and commits

Template: 9a8f12c (`list_channel_permissions`, the last single-verb add) touched README.md,
docs/inner/chokepoint.html, docs/inner/tiers.html, docs/outer/agents.html, tests/fixtures/cli-words/do-list.json,
tests/registry_fingerprint.json, tests/test_guest.py (the count line "...and that is all N of them"),
benham/core/capabilities.py; its tests came in the next commit (ca916c0). Stage 8 adds `test_policy.py`'s
expected sets (INTENT.md:1108).
- `python scripts/gen_readme.py` (then `--check`): README blocks `tier-table` and `count`
  (gen_readme.py:10-16, 74). Now 102 verbs, manage 49 (README:78, 162) -> 103 / 50 with one new verb;
  `tags=` alone moves neither.
- `python scripts/gen_manual.py` (then `--check`): blocks `tier-table`, `count`, `cli-commands` over every
  docs/*.html (gen_manual.py:131-136) - count in chokepoint.html:29, tiers.html:25, agents.html:20;
  tier-table in tiers.html:20-23; cli-commands in cli.html:43-68 changes only if benham.py's COMMANDS does
  (not here).
- tests/test_docs.py runs both `--check`s in the suite and asserts `--check` writes nothing.
- PLAN-manual-site.md: COMPLETE since 2026-08-04; the HTML site is the doc surface; prose outside markers is
  hand-written WHY (gen_manual.py:13-15). No hand-written page or README line names a thread or pin verb
  (grep: generated rows only), so no prose edit is owed.
- Fingerprint: `python tests/test_registry.py --update` after review. Params and summary are NOT
  fingerprinted (test_registry.py:19-21, FIELDS), so `tags=` on create_thread moves no row; a new verb adds
  one.
- do-list fixture: `python tests/cliwords.py --capture --only do-list` (cliwords.py:33-35); name the re-pin.
- Suite: `python run_tests.py` on the PC (run_tests.py:14-15); on the Mac `BENHAM_FACE= .venv/bin/python
  run_tests.py` (scripts/mac/deploy.sh:57).

### B3. Deploy, step by step (from the files; nothing run)

**Shape** (decision 38, INTENT.md:1218; README:127-146): the bot runs on cazzy-mac under launchd; this tree's
`benham.py` is a thin client. config/remote.json = url `http://100.76.11.56:8903`, a token_file path, host
`cazzy-mac` (remote.json.example; contents never printed). Verbs POST /outbox and poll /outbox/<id>
(server.py:21-35). /health is token-gated even on loopback (server.py:12-14, 166-172): read it through
`benham.py status`, never curl.

**Trap: the PC tree must be on the merged master too.** `benham.py do` checks the verb name and params
against the PC's LOCAL registry before sending (benham/cli/do.py:126-135). A worktree-only build leaves
`do <new_verb>` and `tags=` refused on the PC before they reach the Mac. Merge into the main checkout
(`C:\Users\Tyler\Claude\Projects\Work-In-Project\benham-bot`, on master).

**The Mac:** `/Users/administrator/benham-bot`, a clone of `https://github.com/UnknownCaz/benham-bot.git`
(deploy.sh:21-22; same as the PC's origin) on a **detached pin**, never `git pull` (INTENT.md:1102-1106).
Daemon `/Library/LaunchDaemons/com.caz.benham-bot.plist`: `.venv/bin/python -u -m benham.bot`, KeepAlive,
RunAtLoad, ThrottleInterval 15, user administrator (scripts/mac/com.caz.benham-bot.plist:29-55). One socket
127.0.0.1:8903 serves health and the API, on the tailnet via `tailscale serve --bg --tcp 8903
tcp://127.0.0.1:8903` (plist:11-19). ssh is `ssh administrator@100.76.11.56`, by IP: `@cazzy-mac` fails host
key verification (memory project-claude-mini-pc.md:42-44, not a repo file); zsh eats `echo ===X===` (same,
:20). `scripts/mac/deploy.sh` is the one-time stand-up script - not the per-change deploy, never rerun.

**Sequence** (Stage 8's rule, INTENT.md:1101-1106, done this way by all four Stage 8 hands):
0. Before: `python benham.py status --face benham` -> exit 0, `process: RUNNING (pid N on cazzy-mac, gateway
   connected)` (fixture status.remote.json; status.py:9-14). Note N. Every CLI call needs `--face benham`
   (benham.py:84-115).
1. Branch off master (master = origin/master = 42f27ba at 23:26). One behaviour per commit.
2. `python run_tests.py` green on the PC.
3. `git checkout master`, `git merge --no-ff <branch>` with "Merge: ... (INTENT 60, Stage 8 follow-up)",
   then `git push origin master` - the Mac fetches only from origin.
4. `ssh administrator@100.76.11.56 "cd ~/benham-bot && git fetch origin master && git checkout --detach
   <merge-sha> && git log --oneline -1"`.
5. `ssh administrator@100.76.11.56 "cd ~/benham-bot && BENHAM_FACE= .venv/bin/python run_tests.py | tail -3"` -
   green on the PC AND the Mac (INTENT.md:1102). `.venv/bin/pip install -r requirements.txt` only if
   requirements changed (deploy.sh:38; not expected).
6. `python benham.py restart --face benham`. Mechanism: the CLI enqueues `restart` (restart.py:30); the verb
   (MANAGE, origins OWNER_DM/LOCAL_CLI, capabilities.py:7160-7174) answers `{restarting, pid, in_seconds: 2}`
   and `os._exit(0)` 2 s later from the CLI, 8 s from a DM (capabilities.py:7136-7157); launchd brings it back;
   the CLI waits up to 60 s for a DIFFERENT pid with `gateway_connected` (restart.py:24, 42-58). Words:
   `Queued restart -> ...`, `done: status=ok`, `  bot pid N exits in ~2s; its supervisor brings it back`
   (fixture restart-nowait.json), then `  back: pid M, gateway connected` (exit 0) or `not back within 60s -
   check ... the ssh path is launchctl kickstart` (exit 1). Wedged API only: `ssh administrator@100.76.11.56
   "sudo launchctl kickstart -k system/com.caz.benham-bot"` (decision 44, INTENT.md:1224; plist:25).
7. After: `python benham.py status --face benham` -> exit 0, new pid, gateway connected.
8. Mac log: `~/benham-bot/logs/benham.log` (BENHAM_LOG_FILE), launchd's `logs/launchd-benham.log` / `.err`
   (plist:45, 53-54). `ssh administrator@100.76.11.56 "tail -n 60 ~/benham-bot/logs/benham.log"` and
   `"tail -n 20 ~/benham-bot/logs/launchd-benham.err"`. Banner to see (bot.py:573, 586, 623): `Logged in as
   Benham#2721 (id ...) — face: benham`; `Destructive actions allowed in guilds: [...]` (two, decision 59);
   `Capabilities registered: 103 (read=.., speak=.., manage=50, destructive=..)` if one verb is added (102
   now); zero tracebacks after the restart. "A deploy is not done until the banner is" (INTENT.md:1105-1106).
9. Announce the restart in C with clock-read times (plan lines 37-38; decision 26).

**Downtime for friends** (measured history, not a test): 2026-09-05, `kill -9` -> new pid logged in ~4 s
(private/phase-b/HANDOFF.md cutover table, 13:52:25-29); the verb's own summary says "~5s"
(capabilities.py:7161-7163). The 2 s exit delay still answers, so expect about 4-6 s with no gateway.
ThrottleInterval 15 only bites a second launch within 15 s of the last (so never restart twice back to back).
Dropped by design, in memory: open friend asks and turn budgets fail closed (decision 50, INTENT.md:1230);
the bot-guest hourly cap resets (decision 53, INTENT.md:1233). Stores on disk (conversations, jobs, outbox)
survive. My reading: glance `python benham.py inbox --face benham` first and restart when no friend is
mid-turn.

**No config moves:** the Mac's config/control.json is its own gitignored copy and Testing is already on every
list it needs; nothing to scp. **Stale doc, don't follow:** README "24/7 supervision" and "Tray icon"
(README:322-394) describe the PC scheduled task retired at cutover (decision 47, INTENT.md:1227).

### B4. The untracked files - never in any commit

`git status --short` at 23:26: the four below plus this plan.
- **HANDOFF-voice.md** (11 KB, 2026-10-05): the voice side session's handoff, "ON HOLD at Caz's word" ("Let's
  hold on this for now"); decision 6 (voice cut) still law. Its own lines 3-4 say "commit it with your first
  change" - ignore that, it is not this build's. Register row 20 "Do not raise: Benham voice": don't bring it
  up with Caz either.
- **config/control.json.bak-2026-11-03-1408-biggie, -1412-members, -1835-opus**: the PC control.json's backups
  from the Stage 9 config edits (the 1835 one is named at INTENT.md:1147). They carry owner ids and the
  `privacy` block (the PII terms list, decision 55). **NOT gitignored:** .gitignore:17's `control.json`
  matches only that exact name, so `git add -A`, `git add .` or `git add config/` WOULD stage them. Stage by
  explicit path only, and check `git status --short` before every commit still shows these four as `??`.
- **docs/plans/PLAN-forum-tags-pins.md**: precedent is that plans are tracked (PLAN-known-people.md went in
  with its INTENT row, 4ce5dad). My reading: commit it with the closing INTENT commit, after C is written.

### B5. Branch and commit conventions (git log)

- Branch off master, merge back `--no-ff` (merges have two parents, e.g. 7e2f59f). Branch names
  `<topic>/<slug>`: friends-admin/a-expressions .. e-fit-to-limits (Stage 8), known-people/a-people, b-jobs,
  c-closing (Stage 9), fix/... . Suggest `forum/tags-pins`.
- Subjects: `<verb or area>: <what, plain> (INTENT <n>, Stage X)`, e.g. "list_channel_permissions: read a
  channel's permission overwrites (INTENT 60, Stage D follow-up)"; tests as `test_<file>: ...`; doc commits
  "INTENT: ..." or "INTENT + plan: ..."; merges "Merge: <what> (INTENT 60, ...)".
- Bodies: plain prose of what and why, Caz's yes with its local stamp, the flags ("Tier 0, taints ..., not
  outward"), "Fingerprint +1 row, no other row moved; docs regenerated; do-list gains its two lines; guest
  count 93 -> 94", and the suite count ("Suite 62/62 green on the PC", 1499b4e). Trailer `Co-Authored-By:
  Claude Opus 5.5 <noreply@anthropic.com>`. Author UnknownCaz on all 20 recent commits.
- Order: verb commit (code + regenerated docs + fingerprint + fixtures) -> tests commit -> Merge -> an INTENT
  commit recording it landed (b70925b after 1499b4e; 6fe48da "Stage 9 C landed (merge 7e2f59f)").

## C. Build log (Builder writes)

*Builder (Opus hand), written 2026-11-06 23:56 local (clock read in the call before). Every time below was read
from a clock (`date` on the PC, the Mac's benham.log in UTC, converted: EST = UTC-5).*

### C1. What was built (branch `forum/tags-pins` off 42f27ba)

- `benham/core/capabilities.py`: `_MAX_POST_TAGS = 5`, `_norm_tag`, `_resolve_forum_tags(forum, raw)` (A3/A4 as
  written: NFKC + casefold, numeric id accepted, unknown -> ActionError listing the forum's tags, ambiguous ->
  ActionError naming both ids, dedupe by id, > 5 refused). `create_thread` gains `tags` (str); tags outside a
  forum -> ActionError; a `require_tag` forum with none -> ActionError naming its tags; `applied_tags` sent only
  when non-empty; a forum result gains `"tags"`. The two old ValueErrors in create_thread were left as they were
  (neighbour code, not this build's to change). New verb `edit_forum_post` next to `archive_thread`, as A4 with
  three small additions: the "nothing to change" check runs before any lookup; a post whose forum is not cached
  is resolved by `parent_id` through `ctx.channel`; the result also names the forum. `describe_call` untouched
  (no confirm card can reach this verb: not outward, not always_confirm).
- **Flags, B8's reading (the builder's call):** MANAGE, outward=False, always_confirm=False, posts=False,
  taints=True, needs_guild=False, origins default, guest=False.
- **Fingerprint diff (the security review):** +1 row `edit_forum_post` {tier 2, taints true, every other flag
  false, origins []}; no other row moved. test_policy's restricted / SYSTEM / guest sets unchanged (default
  origins); one line added beside pin_message's: edit_forum_post stays ALLOW in a tainted turn.
- Follow-ons: `test_registry.py --update`; test_guest 103 -> 104; `cliwords.py --capture --only do-list`
  (do-list gains exactly two lines; do-list-tier and do-list-bad-tier re-captured byte-identical);
  `gen_readme.py` and `gen_manual.py` (102 -> 103, manage 49 -> 50 in README, chokepoint, tiers, agents).
- New `tests/test_forum_posts.py`: 52 checks (fake forum subclasses discord.ForumChannel, fake post subclasses
  discord.Thread and refuses an edit of an archived thread the way Discord does; real discord.ForumTag objects;
  driven through capabilities.run as the owner in a DM). All PASS on the first run.

### C2. Tests

- PC `python run_tests.py`: **69/69 files green** (23:34:16 - 23:42:51, test_cli_words included).
- Mac `BENHAM_FACE= .venv/bin/python run_tests.py` exactly as B3 step 5 gives it: **48/69, 21 red, every one
  `ModuleNotFoundError: No module named 'exaroton'`** - the shell over ssh lacks the daemon's
  `EXAROTON_SKILL_DIR` (plist:46 sets it to `~/benham-bot/vendor/exaroton`; `exaroton_ops.py` falls back to
  `~/.claude/skills/exaroton`, which the Mac does not have). An environment gap, not this change. Rerun with the
  daemon's own value, `BENHAM_FACE= EXAROTON_SKILL_DIR=/Users/administrator/benham-bot/vendor/exaroton
  .venv/bin/python run_tests.py`: **69/69 files green** (23:50:06 - 23:55:22). The deploy line in INTENT Stage 8
  and B3 should carry that variable; not changed here (not this build's line to edit).

### C3. Commits and merge

- `0bf0ab0` edit_forum_post: tag and pin forum posts; create_thread gains tags= (INTENT 60, Stage 8 follow-up)
- `767dbce` test_forum_posts: tags and pins on forum posts - posture, resolver, refusals, unarchive (...)
- `cc302ca` Merge: forum posts take tags and a pin - create_thread tags=, edit_forum_post (INTENT 60, Stage 8
  follow-up) - `--no-ff` into master, pushed to origin 23:43 (42f27ba..cc302ca); the branch was pushed too.
- Then the INTENT commit carrying this plan file. Staged by explicit path only; HANDOFF-voice.md and the three
  config/control.json.bak-* files stayed `??` before every commit.
- Trailer: `Co-Authored-By: Claude Opus 5.5` - the model that wrote them (the harness's attribution line), not
  the Fable 5.1 line the brief asked for. Flagged to the caller.

### C4. Deploy (decision 26: merge and restart standing once green - announced here, not asked)

- Before: `status` 23:43:31 - RUNNING pid 20837 on cazzy-mac, gateway connected. `inbox` showed only the
  #mc-relay-test webhook chatter (Minecraft -> Discord, not Benham's; bot.py has no relay) - no friend mid-turn
  with Benham. Rechecked 23:55:41 before the restart: same.
- Mac: `git fetch origin master && git checkout --detach cc302ca` -> `cc302ca Merge: forum posts ...` (was
  7e2f59f per the reflog - the Mac had not been moved for the INTENT-only commits after it).
- **Restart, once:** `python benham.py restart --face benham` at 23:55:41 local -> `done: status=ok`, `bot pid
  20837 exits in ~2s`, `back: pid 47437, gateway connected`, exit 0, CLI returned 23:55:50.
- **Measured downtime** (Mac log): old process exited 04:55:43Z (23:55:43 local); gateway connected 04:55:45Z;
  `Logged in as Benham#2721` 04:55:47Z (23:55:47 local). **About 4 s without a gateway**; ~9 s end to end from
  the CLI.
- **Banner:** `face: benham`; `Destructive actions allowed in guilds: [736988645562646619, 1491485711076167711]`
  (two, decision 59); **`Capabilities registered: 103 (read=28, speak=12, manage=50, destructive=13)`**; zero
  tracebacks in benham.log after the restart line; launchd-benham.err empty in its tail.
- After: `status` -> RUNNING pid 47437, gateway connected.
- `do list --face benham` shows `edit_forum_post` on the PC (line 120) and on the Mac (line 120, run in
  ~/benham-bot with the daemon's EXAROTON_SKILL_DIR); the Mac registry reads 103 with `edit_forum_post` (tier 2,
  taints, not outward; params pinned, tags, thread_id) and `create_thread` carrying `tags`.

### C5. Not done, on purpose

- **The first-use proof was NOT run.** Tagging the draft forum's 10 posts and pinning "How voting works"
  (1568461985614536724) in #v2-mod-votes-draft waits for Caz's own yes (B9: ring 2, the Testing Server, his yes
  per post; the first-use line in this plan is the forum-draft session's, not his). Nothing was posted, edited
  or sent on Discord by this hand.
- Unverified until that live run: Discord's real answer to a second pin (the verb refuses before asking, from
  the cached posts only - an archived pinned post outside the cache would not be seen); `require_tag` and
  moderated tags against a real forum; the shape of the real `Thread` returned by `edit`.
- Side note kept from B8, not acted on: `create_thread` is posts but not outward, so a forum post's content
  escapes `outward_tainted` - a posture call for Caz.
