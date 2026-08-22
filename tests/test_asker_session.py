"""test_asker_session.py - a conversation knows which session asked, or says null.

The incident (c18): an answered ask sat uncollected for two days because the
session that asked had died and nothing identified it as the owner. The Raven
courier now routes arrived answers by the asker's `local_` session id recorded
at open; cwd-matching on `origin` stays as its fallback for older records.

The rule under every check here: NEVER a guess. A wrong id routes somebody's
answer into an unrelated session, which is strictly worse than the cwd fallback.
Anything unresolvable - missing env, gibberish env, a store entry with a bogus
sessionId - must come back None, and the record must store null.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _testconfig                 # noqa: F401,E402 - must precede every benham import

from benham.core import caller, conversations  # noqa: E402
from benham.cli import outreach                # noqa: E402

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


VALID = "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLI_UUID = "11111111-2222-3333-4444-555555555555"

# The suite itself runs inside a Claude session, so these env vars are REALLY
# set. Every check below controls them explicitly and restores them at the end -
# a test that inherits its fixture from whoever runs it is the trap _testconfig
# exists to end.
_saved = {k: os.environ.get(k)
          for k in ("CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID")}
_saved_stores = caller._STORES


def env(host=None, cli=None):
    for k, v in (("CLAUDE_CODE_HOST_SESSION_ID", host),
                 ("CLAUDE_CODE_SESSION_ID", cli)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


try:
    section("source 1: the harness exports the local_ id itself")
    env(host=VALID)
    check("a well-formed host id is taken as-is", caller.session_id(), VALID)
    env(host="not-a-local-id")
    check("a malformed host id is not trusted (and nothing else is set -> None)",
          caller.session_id(), None)

    section("source 2: the session store maps cliSessionId -> local_ id")
    store = tempfile.mkdtemp(prefix="benham-fake-sessions-")
    caller._STORES = [store]
    sub = os.path.join(store, "acct", "org")
    os.makedirs(sub)
    with open(os.path.join(sub, f"{VALID}.json"), "w", encoding="utf-8") as f:
        json.dump({"sessionId": VALID, "cliSessionId": CLI_UUID,
                   "title": "some session"}, f)
    env(cli=CLI_UUID)
    check("the matching store entry resolves (recursive, like the real store's "
          "account/org nesting)", caller.session_id(), VALID)
    env(cli="99999999-9999-9999-9999-999999999999")
    check("an unknown cliSessionId resolves to None, not to a near miss",
          caller.session_id(), None)

    with open(os.path.join(sub, f"{VALID}.json"), "w", encoding="utf-8") as f:
        json.dump({"sessionId": "gibberish", "cliSessionId": CLI_UUID}, f)
    env(cli=CLI_UUID)
    check("a store entry whose sessionId is not a local_ id is refused - "
          "never a guess", caller.session_id(), None)

    env()
    check("no env at all (the bot, a bare terminal) -> None",
          caller.session_id(), None)

    section("the record stores it - and stores null honestly")
    conv = conversations.open_conversation(_testconfig.GUEST_ID, "p", "q?",
                                           asker_session=VALID)
    check("asker_session lands on the record", conv.get("asker_session"), VALID)
    conv = conversations.open_conversation(_testconfig.GUEST_ID, "p", "q?")
    check("...and defaults to null, present rather than absent, so 'unknown' "
          "is a stated fact", ("asker_session" in conv, conv["asker_session"]),
          (True, None))

    section("the CLI paths wire the resolver through")
    env(host=VALID)
    outreach.main([str(_testconfig.GUEST_ID), "who is asking?"])
    latest = max(conversations.all_conversations(), key=lambda c: c.get("seq", 0))
    check("outreach records the resolved session",
          latest.get("asker_session"), VALID)
    check("...alongside the cwd+pid origin Raven falls back on",
          "cwd=" in (latest.get("origin") or ""), True)
    # ask.py and initiative.open_question take the same one-argument path; the
    # wiring is one keyword each, asserted by source so this file does not have
    # to drive Tyler's real ask queue to prove a keyword exists.
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in (os.path.join("benham", "cli", "ask.py"),
                os.path.join("benham", "core", "initiative.py")):
        with open(os.path.join(_root, rel), encoding="utf-8") as f:
            check(f"{rel} passes asker_session=caller.session_id()",
                  "asker_session=caller.session_id()" in f.read(), True)
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    caller._STORES = _saved_stores

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
