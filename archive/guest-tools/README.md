# The guest tool loop — archived 2026-08-16

Guests could run Python and shell in an Anthropic-hosted sandbox, keep a private file
workspace, import attachments, get files sent back, and read a set of Discord channels Tyler had
shared. It was built across seven staged commits between 2026-08-04 and 2026-08-07, it was
carefully gated, and it worked.

It was archived because **the usage never came.**

| Feature | Lifetime use |
|---|---|
| Code runs | **15**, every one of them on 2026-08-06/07 — the two days it was built. None since. |
| Workspace | **3 files**, one named "anime to watch" |
| `read_shared_channel` | 3 channels configured; no recorded use |

The one genuine attempt to use code execution came from a guest on 2026-08-15: *"hey benham can
you run a code to extract the audio from this video?"* — and it **failed**, because the sandbox
has no network. The single real demand the feature ever saw was one it could not serve.

Meanwhile the `idea..` filing path — about 115 lines, shipped in one commit — was being used for
actual work. That contrast is what drove the [intent rundown](../../INTENT.md)'s decision to
invest in the conduit and subtract the companion.

## What is in here

| File | Was |
|---|---|
| `guest_agent.py` | The tool loop: build tools, run rounds, verify file claims, charge runs. |
| `guest_workspace.py` | All file logic — per-guest folders, the shared commons, path safety, caps. |
| `capabilities_guest_block.py` | The seven capability declarations, lifted verbatim from `capabilities.py`. |
| `test_guest_agent.py`, `test_guest_workspace.py`, `test_guest_grants.py` | Their tests, which all passed. |

## What stayed behind, and why

- **`core/pathsafe.py` stays.** It is not workspace-only.
- **Server-side web search stays.** Tyler's explicit call: it is cheap and it makes Benham worth
  talking to. It never needed the tool loop — it runs in `guest.py`'s plain chat path because it
  executes on Anthropic's infrastructure, not here.
- **The grant machinery stays** (`identity.guest_capabilities`, `capabilities.guest_grants`,
  `policy`'s guest rule). Nothing declares `guest=True` any more, so it grants nothing — but it
  is the two-key gate (registry flag AND config list) that a future guest capability would have
  to pass. Deleting it would mean the next one ships ungated. `bot.py`'s boot banner now prints
  `guest_grants()` expecting it to be **empty**, and says so loudly if it is not.
- **`GUEST_MODES` lost `"workspace"` on purpose.** A `control.json` still asking for the archived
  mode now switches guests **off** rather than silently running them through chat — chat cannot
  do what that config was written to request, and quietly downgrading would be the "guess what
  they meant" failure the mode check exists to prevent. `test_guest` pins this from the new side.

## The knowledge worth keeping

**The blindfold.** Stage 4's first real test: Doom attached a file and said "keep this for me"
four times, and the model answered as chat every time. `ws_import` was in its tool list, but
nothing in its *context* said a file existed — the loop hands the model text, not a Message. A
tool a model cannot tell is applicable is a tool it does not have. Any future tool surface needs
the same note injected alongside the tool.

**Check twice, on the way out.** Every path the loop handed back was re-verified against that
guest's own folder before a byte left the process. A bug upstream should cost an attachment, not
ship someone else's file.

**Tell the model WHY a tool vanished.** When a guest hit the daily run cap, the tool was simply
removed from the list — and the model, asked to compute something, answered from memory and got
it wrong (1,299,709, confidently). Silence about a missing capability reads to a model as "do it
yourself." The fix was to say, in the prompt, that runs were exhausted and that the only
acceptable answer was to say so.

**Wrong answers may only come from code.** Related, and stronger: once a tool exists to compute
something, the model must not be allowed to substitute its own arithmetic. That escape hatch was
closed in `62445fa`.

## Bringing it back

Restore the files, re-insert `capabilities_guest_block.py` into `capabilities.py`, re-add
`code_execution_tool` / `code_runs` to `shared_tools.py` and the run-accounting
(`runs_today`, `charge_run`, `log_runs`) to `guest.py`, put `"workspace"` back in `GUEST_MODES`,
and restore the mode branch in `handle_guest_dm`. Then read the four lessons above before
re-enabling anything for a real person.

**Ask first.** Doom was told (guide v4) that these went because nobody used them, and that saying
he wants them back counts as a real vote. If that vote arrives, it is better evidence than
anything in this file.
