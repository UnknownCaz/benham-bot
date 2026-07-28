# Test coverage analysis

An audit of what the five test suites actually exercise, where the gaps are, and
which gaps are worth closing first. Recommendations assume the existing
hand-rolled script convention stays (no pytest migration).

## Current state

The suite is five standalone scripts in the repo root, each using the same
hand-rolled `check()` / `section()` harness: assertions execute at import time,
failures accumulate, and the script ends with `sys.exit(1)` if anything failed.
There is no pytest, no CI, no aggregate runner, and no coverage tooling — each
suite is run by hand:

| Suite | What it covers |
|---|---|
| `test_policy.py` | The rule functions in `policy.py`, plus a full matrix of every capability x five origins with expected restrictions asserted as sets |
| `test_control.py` | `identity` predicates, `capabilities.run` error routing, the whole `confirm` phrase/token parser, `agent` history bookkeeping, registry shape invariants |
| `test_owner_gate.py` | Strangers vs. owner through the **real** `bot.on_message` / `handle_auto_reply`, voice gate, `pc..` prefix routing — the load-bearing assertion everywhere is "a stranger costs zero API calls" |
| `test_injection.py` | Taint flag wiring, posting allowlists surviving `force=True`, and an end-to-end scripted-model attack: poisoned channel read → attempted send → parked for confirmation |
| `test_guest.py` | Guest denial of all capabilities (twice, independently), daily quota incl. a 200-thread reservation race, memory isolation, persona-directive stripping, live dispatch through `bot.on_message` |

What's genuinely strong: the policy/permission layer. Fail-closed defaults,
owner gating, taint handling, and guest denial are tested behaviorally *and*
through the real message handler, with control cases proving the harness is
live. The methodology encoded here (drive real handlers, not helpers; watch
every invocation, not the interesting flag; always include a positive control)
is the best thing about the suite and should be preserved in anything new.

Two housekeeping notes before the gaps:

- `README.md`'s `## Testing` block (line 460) lists four suites and omits
  `test_guest.py`, even though the README discusses it in prose. One-line fix.
- Nothing runs the suites automatically. Until CI exists, every gap below is
  only as good as someone remembering to run five scripts.

## Priority 1 — security-critical code that exists in production but is never exercised

> **Status: all five closed.** The gaps below were found by the audit and have
> since been covered — items 1 and 4 in `test_guest.py`, item 2 in
> `test_owner_gate.py`, item 3 in `test_injection.py`, item 5 by the new
> `test_outbox.py`. The descriptions are kept as written because they explain
> what those tests exist to hold.

These are gaps *inside* areas that look covered, which is exactly why they rank
highest: a regression here would be invisible to the current suite.

### 1. The global guest quota has zero coverage

`guest._reserve` has two budget branches. The per-guest daily cap is tested
thoroughly (including under concurrency). The global cap (`guest.py:132`,
rule `guest_global_quota`) is never exercised — worse, the concurrency test
deliberately disables it (`test_guest.py:207` sets `GLOBAL_CAP = 10**6`) and
nothing ever re-enables it. `bot.py:1308` also routes this rule name to a
distinct user-facing refusal, which is likewise untested. This is a spend
limit; a broken comparison or a rollover bug would let guests burn the global
budget silently.

**Test:** set `GLOBAL_CAP` low, drive `check()` from two different guest ids,
assert the denial rule is `guest_global_quota` (not `guest_quota`), that the
refusal path in `handle_guest_dm` uses the quota wording, and that a date
rollover resets the global counter.

### 2. The confirmation *execution* path is never run

Every suite proves strangers can't confirm a parked action; none proves the
owner *can*. `bot.fire_confirmed` (`bot.py:1254`) — the function that replays a
parked action with `force=True` and the original `call_ctx` — is stubbed in
`test_guest.py` and cancelled out in `test_owner_gate.py`. Nothing verifies
that after a legitimate owner "yes": the handler actually runs, it runs with
the *parked* params (not re-read from anywhere), it runs under the original
origin context, and the pending entry is consumed so it cannot fire twice.

**Test:** park a benign action with a stub handler, send owner "yes" through
the real `on_message`, assert the handler ran once with the parked params and
`force=True`, and that a second "yes" does nothing.

### 3. The `<untrusted-data>` wrapper is tested against a hand-written copy

`test_injection.py:175` asserts labeling properties on a `sample` string
written inside the test file — a mirror of what `agent.py:344` emits, not its
output. If `agent.py` stopped wrapping tainted tool results tomorrow, the
section still passes. This is the exact failure mode the same file warns about
regarding its `gated()` mirror.

**Test:** run `agent.respond` with the existing fake-client pattern, have a
scripted tool round return a tainted `read_channel` result, and assert the
message list actually sent to the (fake) API contains
`<untrusted-data source="read_channel">` around the tool result body.

### 4. Guest failure refund and the live guest gate

`guest.refund` exists specifically for the `except` path in `handle_guest_dm`
(`bot.py:1323`) — API call fails after quota was reserved, quota is returned.
That path is never driven; `refund` is only tested in isolation. Separately,
`guest.is_known_guest` is the live gate at `bot.py:1356` but is never directly
asserted, only exercised transitively.

**Test:** patch `guest.respond` to raise, send a guest DM through
`on_message`, assert spent-today is unchanged afterwards. Add direct
`is_known_guest` checks (guest / owner / stranger / garbage id).

### 5. The outbox → poller ingress path is untested

`outbox.py` and `bot.poll_outbox` (`bot.py:1484`) have no coverage at all, yet
this is a real command-ingress surface: every CLI (`send.py`, `purge.py`,
`do.py`, …) enqueues JSON that the poller executes. Untested behaviors that
matter:

- **Atomicity contract:** `enqueue` writes `.json.tmp` then `os.replace`, so
  the poller's `*.json` glob never reads a half-written request. Nothing pins
  this; a refactor to a plain `open(...).write()` would pass every suite.
- **Confirm-token redemption:** the poller verifies `parked.action == action`
  and fires `parked.params` rather than re-reading the request — a small but
  real security property (a queued file can't swap the action under a token).
- Result routing to `outbox/sent` / `outbox/failed`, and legacy verb handling.

**Test:** point `outbox.OUTBOX` and the poller at a temp dir, enqueue requests,
run one poll iteration with stub handlers, assert dispatch/result placement and
the token-redemption mismatch case. (`webhook.py` is *documented* as outside
the policy chokepoint — note it in reviews, but there is no security boundary
there to test.)

## Priority 2 — hardening what's already tested

> **Status: closed.** `test_policy.py` now carries the six-origin matrix
> (GUEST_DM column asserted all-No), a full `authorize_target` matrix in clean
> and tainted turns with hard-coded expected confirm sets, a registry-wide
> deny-beats-confirm sweep, and by-name assertions for every previously
> unasserted rule string. The `int(ctx.guild_id)` edge was **fixed** in
> `policy.py` (malformed guild ids now deny under `agent_guild` /
> `engage_guild`) and pinned by test. The confirm timing surface (`_ttl_for`,
> `seconds_left`, `cancel(token)`, config-driven expiry) is covered in
> `test_control.py`, and the hard-coded registry count in `test_guest.py` was
> replaced with a non-vacuousness check.

- **Add `GUEST_DM` to the policy matrix.** `test_policy.py`'s "every action x
  every origin" is really x5 of 6 origins; guest denial lives only in
  `test_guest.py`. Adding the sixth column with expected-empty reachability
  makes the matrix actually total.
- **Matrix `authorize_target()` and a tainted variant.** The target phase and
  taint behavior are spot-checked on ~6 actions; ~10 rule names
  (`origin_allowed`, `agent_guild`, `blocked_when_tainted`, `always_confirm`,
  `engage_*`, `target_context`, …) are never asserted by name, so a denial
  attributed to the wrong rule would slip through — and `bot.py` branches on
  rule names (e.g. `bot.py:1308`), so the names are API.
- **Pin two latent sharp edges found during this audit:**
  - `policy.py:300` and `policy.py:483` call `int(ctx.guild_id)` unguarded. A
    non-numeric guild id makes an authorization rule *raise* instead of deny.
    In practice guild ids come from Discord as ints, but an auth path that can
    throw is worth either a guard or a test documenting the assumption.
  - `test_guest.py:82` hard-codes the registry count (`47`). Any new capability
    fails an unrelated suite for no security reason. The adjacent assertion
    (every capability denies a guest) is the real property; drop the count or
    derive it.
- **`confirm` timing surface:** `_ttl_for` (per-origin TTLs) is never called —
  expiry is tested by forcing `expires_at = 0`. `Pending.seconds_left` and
  `cancel(token)` with an explicit token are also untested.

## Priority 3 — untested pure logic (cheap wins, in value order)

> **Status: closed.** Four new suites — `test_brain.py` (the directive grammar,
> including the prose-eating regression and the `:`/`=` persona drift),
> `test_text_utils.py` (`split_for_discord`, `is_wake`, `looks_like_noise`,
> `local_shortcut`, `apply_voice_settings` clamping, `allowed_servers` /
> `is_operator`), `test_codesession.py` (`_describe` incl. the command-field
> rule and truncation windows, `_progress_label`, `answer`/`pending_request`
> late-answer semantics, `_SECRET_RE`, the read-allowlist shape), and
> `test_jsonio.py` (defaults, atomic replace, rotation, torn-final-line
> tolerance). `identity.load_control` defaults and `posting_allowed`'s
> three-way precedence are covered directly in `test_control.py`.

None of these need Discord stubs; most need no mocking at all.

1. **`brain.py` directive grammar.** `strip_directive` is a security
   dependency of `guest.py` (it's what stops a guest-influenced reply from
   carrying `<<persona: ...>>` into the shared persona file), and the regex has
   a documented regression history (`<<.*?>>` used to eat spans of ordinary
   prose). `parse_directive`, `parse_persona_directive`, `resolve_voice`, and
   `wants_sleep` are all pure text munging.
2. **`bot.py` pure islands:** `split_for_discord` (2000-char cut heuristics),
   `is_wake` (fuzzy matching), `local_shortcut` (70-line intent classifier
   whose misfires have real effects — "go to sleep", "reset your
   personality"), `apply_voice_settings` (clamping), `convo_active`. The suites
   already pay `bot.py`'s import cost, so these are marginal additions to
   `test_owner_gate.py` or a new `test_text_utils.py`.
3. **`codesession.py` SDK-free helpers:** `_describe` builds the approval text
   the owner reads before allowing a shell command on their real PC (it keys on
   the presence of a `command` field so unknown shell-like tools can't hide
   behind an 80-char truncation); `answer` must ignore late/unknown answers;
   `_SECRET_RE` decides what gets `SECRET-READ` logged. All testable without
   the SDK installed.
4. **`identity.py` direct coverage:** `load_control` restrictive defaults,
   `posting_allowed`'s three-way precedence (`post_channels` >
   `post_guilds` > allow-all), `guest_enabled` failing closed on an
   unrecognized mode. Behavior is partially covered transitively; direct tests
   make the root of trust self-documenting.
5. **`jsonio.py`:** atomic replace, corrupt-file → default, `rotate_if_large`,
   `iter_jsonl` with a truncated final line. Zero dependencies; the easiest
   full-coverage module in the repo, and the poller's atomicity story rests
   on it.

**Explicitly deprioritized:** the 47 capability handler bodies (pure discord.py
glue; the registry metadata and `run()` ordering are the testable parts and are
covered), the voice stack (whisper/edge-tts/subprocess), `catchup.py` and
`read_history.py` (all logic inside `on_ready`), `exaroton_ops.py` (import-time
dependency on an external skill dir), and the thin enqueue CLIs beyond their
arg parsing.

## Infrastructure

Keeping the script convention, two small additions would raise the floor:

- **`run_tests.py`:** run each `test_*.py` as a subprocess (subprocess because
  the suites assert at import and call `sys.exit`), aggregate exit codes,
  print one summary. This makes "run the tests" a single command and is the
  prerequisite for CI.
- **CI eventually:** a GitHub Actions job running `run_tests.py` on push. The
  suites are already offline-by-design with stub clients, so they should run
  in CI as-is; the only environment needs are `BOT_KEY` set to a dummy value
  and an `exaroton_watch.json` (or making `bot.py` tolerate its absence).
- **README:** ~~add `test_guest.py` to the `## Testing` run list~~ (done, along
  with `test_outbox.py`), and
  consider writing down the suite's conventions for future test authors —
  drive the real handlers, include a positive control case, watch every
  invocation rather than the interesting flag, restore patched module
  attributes in `finally`. Those rules are the distilled lessons of this
  repo's actual bugs and are currently only discoverable by reading the tests.
