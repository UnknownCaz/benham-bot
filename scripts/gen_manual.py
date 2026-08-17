"""
gen_manual.py - regenerate the parts of docs/ that describe the live system.

The manual went stale the way documentation always does: three pages said "57
capabilities" against a live 56, and eight of twenty-two described voice, guest
workspaces and code execution - all archived 2026-08-16 and none of it existing
any more. Nobody noticed, because nothing checks a manual.

So the same split gen_readme.py uses. Facts that can be derived are DERIVED, from
the registry and the CLI dispatcher, into marked blocks:

    <!-- GENERATED:name --> ... <!-- /GENERATED:name -->

Prose outside the markers stays hand-written, and should be about WHY rather than
WHAT - the "what" is where staleness hides, and the "why" ages far better.

    python scripts/gen_manual.py --check    # exit 1 if stale, no writes
    python scripts/gen_manual.py            # rewrite in place

--check belongs in the suite. A manual nobody verifies is worse than no manual:
it is believed. This session watched a stale claim in policy.py get read,
believed, and reproduced verbatim into a NEW comment by someone who had just been
told it was wrong. Generated blocks cannot do that.
"""

import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham.core import capabilities, identity  # noqa: E402
from benham import paths  # noqa: E402

DOCS = os.path.join(paths.ROOT, "docs")

# One example row per tier, checked against the registry before use so a renamed
# capability fails loudly here instead of becoming a lie on the page.
EXAMPLES = {
    0: ["read_channel", "search_messages", "find_user", "what_i_did"],
    1: ["send_message", "dm_user", "notify_owner", "tell_conversation"],
    2: ["pin_message", "add_role", "pc_task", "triage_conversation"],
    3: ["delete_message", "purge_messages", "kick_member", "ban_member"],
}
GATES = {
    0: "none",
    1: "owner only",
    2: "owner only",
    3: "guild allowlist + dry-run + explicit confirm",
}


def _counts():
    by = {t: 0 for t in (0, 1, 2, 3)}
    for act in capabilities.REGISTRY.values():
        by[act.tier] += 1
    return by


def block_tier_table():
    by = _counts()
    rows = []
    for t in (0, 1, 2, 3):
        missing = [e for e in EXAMPLES[t] if e not in capabilities.REGISTRY]
        if missing:
            raise SystemExit(f"gen_manual: example(s) no longer registered: {missing}. "
                             "Update EXAMPLES in scripts/gen_manual.py.")
        ex = ", ".join(f"<code>{html.escape(e)}</code>" for e in EXAMPLES[t])
        rows.append(f"      <tr><td><strong>{identity.TIER_NAMES[t]}</strong> "
                    f"({by[t]})</td><td>{ex}</td><td>{GATES[t]}</td></tr>")
    return "\n".join(rows)


def block_count():
    """Just the number, for inline use mid-sentence."""
    return str(len(capabilities.REGISTRY))


def block_cli():
    """The dispatcher's own command list - so a retired command cannot linger here.

    Read from benham.py rather than imported: importing the dispatcher would run
    it, and the table it holds is a plain literal that is trivial to parse.
    """
    src = open(os.path.join(paths.ROOT, "benham.py"), encoding="utf-8").read()
    pairs = re.findall(r'"([a-z_]+)":\s*\("benham\.cli\.[a-z_]+",\s*"([^"]+)"\)', src)
    if not pairs:
        raise SystemExit("gen_manual: could not parse the command table from benham.py")
    rows = []
    for name, desc in pairs:
        rows.append(f"      <tr><td><code>python benham.py {html.escape(name)}</code></td>"
                    f"<td>{html.escape(desc)}</td></tr>")
    return "\n".join(rows)


BLOCKS = {
    "tier-table": block_tier_table,
    "count": block_count,
    "cli-commands": block_cli,
}


def main():
    check = "--check" in sys.argv
    changed, missing = [], []

    for root, _dirs, files in os.walk(DOCS):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as fh:
                text = original = fh.read()
            for name, build in BLOCKS.items():
                pat = re.compile(
                    rf"(<!-- GENERATED:{name} -->).*?(<!-- /GENERATED:{name} -->)",
                    re.DOTALL)
                if not pat.search(text):
                    continue
                text = pat.sub(lambda m: m.group(1) + build() + m.group(2), text)
            if text != original:
                changed.append(os.path.relpath(path, paths.ROOT))
                if not check:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)

    # A page that lost its markers is the silent failure this exists to prevent -
    # it would simply stop being checked, and nobody would see a diff.
    marked = set()
    for root, _dirs, files in os.walk(DOCS):
        for fn in files:
            if fn.endswith(".html"):
                t = open(os.path.join(root, fn), encoding="utf-8").read()
                for name in BLOCKS:
                    if f"<!-- GENERATED:{name} -->" in t:
                        marked.add(name)
    missing = sorted(set(BLOCKS) - marked)
    if missing:
        print(f"gen_manual: no page carries these blocks any more: {missing}. "
              "Either restore the markers or drop the block from BLOCKS.",
              file=sys.stderr)
        return 1

    if not changed:
        print("docs/ is current.")
        return 0
    if check:
        print("docs/ is STALE - run: python scripts/gen_manual.py", file=sys.stderr)
        for c in changed:
            print(f"  {c}", file=sys.stderr)
        return 1
    print(f"regenerated {len(changed)} page(s): {', '.join(changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
