"""
gen_readme.py - regenerate the parts of README.md that describe the registry.

Written because those parts had drifted, quietly and in a way nobody would catch by
reading: on 2026-08-16 the README documented 50 capabilities and "49 tools" while the
registry held 59, and its tier table read 16/7/20/7 against a live 19/7/26/7. Every
one of those numbers was correct when it was typed. That is the whole problem with
typing them.

So they are generated now. The README carries marker pairs:

    <!-- GENERATED:tier-table --> ... <!-- /GENERATED:tier-table -->
    <!-- GENERATED:count --> ... <!-- /GENERATED:count -->

and this script replaces what sits between each pair from the live registry. Text
outside the markers is hand-written and never touched.

    python scripts/gen_readme.py --check    # exit 1 if stale (no writes)
    python scripts/gen_readme.py            # rewrite in place

--check is the useful one: run it after any registry change and the README either
matches or fails loudly, instead of being wrong for twelve days.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benham.core import capabilities, identity  # noqa: E402
from benham import paths  # noqa: E402

README = os.path.join(paths.ROOT, "README.md")

# One example row per tier. Names are checked against the registry before use, so a
# renamed capability fails here rather than becoming a lie in the docs.
EXAMPLES = {
    0: ["read_channel", "search_messages", "find_user", "read_attachments", "guild_info"],
    1: ["send_message", "send_embed", "send_file", "dm_user", "react"],
    2: ["pin_message", "add_role", "create_channel", "set_channel_permissions", "timeout_member"],
    3: ["delete_message", "purge_messages", "delete_channel", "kick_member", "ban_member"],
}

GATES = {
    0: "none",
    1: "owner only",
    2: "owner only",
    3: "guild allowlist + dry-run + explicit confirm",
}


def tier_table():
    by_tier = {t: [] for t in (0, 1, 2, 3)}
    for name, act in capabilities.REGISTRY.items():
        by_tier[act.tier].append(name)
    rows = ["| tier | examples | gate |", "|---|---|---|"]
    for t in (0, 1, 2, 3):
        missing = [e for e in EXAMPLES[t] if e not in capabilities.REGISTRY]
        if missing:
            raise SystemExit(f"gen_readme: example(s) no longer registered: {missing}. "
                             "Update EXAMPLES in scripts/gen_readme.py.")
        ex = ", ".join(f"`{e}`" for e in EXAMPLES[t])
        rows.append(f"| **{identity.TIER_NAMES[t]}** ({len(by_tier[t])}) | {ex} | {GATES[t]} |")
    return "\n".join(rows)


def count_line():
    return (f"`do` covers all {len(capabilities.REGISTRY)} registered capabilities and replaces "
            "the need for a script per action. The older single-purpose CLIs below still work "
            "and route through their original code paths.")


BLOCKS = {"tier-table": tier_table, "count": count_line}


def main():
    check = "--check" in sys.argv
    with open(README, encoding="utf-8") as fh:
        text = original = fh.read()

    for name, build in BLOCKS.items():
        pat = re.compile(
            rf"(<!-- GENERATED:{name} -->\n).*?(\n<!-- /GENERATED:{name} -->)",
            re.DOTALL)
        if not pat.search(text):
            raise SystemExit(f"gen_readme: no marker pair for {name!r} in README.md")
        text = pat.sub(lambda m: m.group(1) + build() + m.group(2), text)

    if text == original:
        print("README.md is current.")
        return 0
    if check:
        print("README.md is STALE - run: python scripts/gen_readme.py", file=sys.stderr)
        return 1
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"README.md regenerated ({len(capabilities.REGISTRY)} capabilities).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
