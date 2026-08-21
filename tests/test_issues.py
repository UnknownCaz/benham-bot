"""
test_issues.py - the GitHub intake funnel (INTENT item 23).

The defect classes this file guards, each learned somewhere else in this repo:

  The quarantine. Guest text must reach the issue body only inside the nonce
  fence, under a machine-written header, with needs-triage on every guest
  filing - an issue is read by future Claude sessions as a work item, which is
  exactly where laundering matters (ideas.py's property, moved to GitHub).

  The stub that records. file_issue is tested against an ASSERTIVE gh stub
  that records what was passed - the ask-queue delivery bugs taught that a
  loose stub reads exactly like a passing one. Nothing here ever calls gh.

  The one-shot offer. A parked offer is consumed by exactly one message; a
  stray "yes" three topics later must never file something stale. TTL, the
  supersede rule, and the issuer gate all get their own checks.

  The strip net. The offer tag lives inside the <<...>> directive family ON
  PURPOSE, so directives.strip_directive removes it even if parse_offer never
  runs. That property is asserted, not assumed.

    python test_issues.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import json
import os
import tempfile
import time

from benham.core import directives
from benham.core import issues

DOOM = 1097631170788851815
STRANGER = 999000999000999000

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# Point the module at a test config and test files. These are module globals
# read the same way control.json fills them at import.
_tmp = tempfile.mkdtemp(prefix="benham-issues-test-")
issues.ISSUES_FILE = os.path.join(_tmp, "guest_issues.jsonl")
issues.OFFERS_FILE = os.path.join(_tmp, "issue_offers.json")
issues.ENABLED = True
issues.REPO = "example/intake"
issues.ISSUER_IDS = {DOOM}
issues.DAILY_CAP = 3
issues.PROJECTS = {"storyizier", "benham"}


class GhStub:
    """Records every call; answers with a fixed URL or raises on demand."""

    def __init__(self):
        self.calls = []
        self.fail = None

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        if self.fail:
            raise OSError(self.fail)
        return "https://github.com/example/intake/issues/7"


gh = GhStub()
issues._run_gh = gh


section("extract - the bug../want.. prefixes")
check("bug.. extracts", issues.extract("bug.. the lore button 404s"),
      ("bug", "the lore button 404s"))
check("WANT.. case-insensitive", issues.extract("WANT.. pdf export"),
      ("want", "pdf export"))
check("feature.. aliases want", issues.extract("feature.. pdf export"),
      ("want", "pdf export"))
check("mid-sentence is conversation", issues.extract("that bug.. thing"), None)
check("plain chat is None", issues.extract("hey benham"), None)
check("bare prefix yields empty text", issues.extract("bug.."), ("bug", ""))
check("empty message is None", issues.extract(""), None)


section("parse_offer - the model's tag, treated as untrusted")
check("well-formed tag",
      issues.parse_offer("sounds broken.\n<<issue: bug | lore button 404s>>"),
      {"category": "bug", "title": "lore button 404s", "project": None})
check("project inside the known set",
      issues.parse_offer("<<issue: want | pdf export | storyizier>>"),
      {"category": "want", "title": "pdf export", "project": "storyizier"})
check("hallucinated project dropped, filing survives",
      issues.parse_offer("<<issue: bug | oops | not-a-project>>")["project"], None)
check("unknown category is no offer",
      issues.parse_offer("<<issue: exploit | oops>>"), None)
check("empty title is no offer", issues.parse_offer("<<issue: bug |  >>"), None)
check("no tag is no offer", issues.parse_offer("just words"), None)
_long = issues.parse_offer("<<issue: bug | " + "x" * 300 + ">>")
check("title capped", len(_long["title"]) <= issues.MAX_TITLE, True)

section("the strip net - the tag never reaches the guest even unparsed")
check("strip_directive removes the tag",
      directives.strip_directive("hi\n<<issue: bug | broken thing>>"), "hi")


section("offer_from_reply - park only for an enabled issuer")
line = issues.offer_from_reply(DOOM, "reply\n<<issue: bug | lore 404s>>",
                               "I was expecting the lore button to work?")
check("issuer gets the offer line", isinstance(line, str) and "yes" in line, True)
parked = issues.pending_offer(DOOM)
check("offer parked", parked is not None, True)
check("quote is the guest's own words, captured by code",
      parked["quote"], "I was expecting the lore button to work?")
check("non-issuer parks nothing",
      issues.offer_from_reply(STRANGER, "<<issue: bug | x>>", "hello"), None)
check("non-issuer has no pending offer", issues.pending_offer(STRANGER), None)
issues.ENABLED = False
check("disabled funnel parks nothing",
      issues.offer_from_reply(DOOM, "<<issue: bug | x>>", "hello"), None)
issues.ENABLED = True
check("no quote text parks nothing",
      issues.offer_from_reply(DOOM, "<<issue: bug | x>>", "   "), None)


section("the parked offer - one shot, superseding, mortal")
issues.offer_from_reply(DOOM, "<<issue: bug | first>>", "first report")
issues.offer_from_reply(DOOM, "<<issue: want | second>>", "second report")
check("newer offer supersedes", issues.pending_offer(DOOM)["title"], "second")
issues.clear_offer(DOOM)
check("clear drops it", issues.pending_offer(DOOM), None)
issues.clear_offer(DOOM)   # idempotent, must not raise
issues.offer_from_reply(DOOM, "<<issue: bug | stale>>", "old report")
_offers = json.load(open(issues.OFFERS_FILE, encoding="utf-8"))
_offers[str(DOOM)]["ts"] = time.time() - issues.OFFER_TTL_SECONDS - 1
json.dump(_offers, open(issues.OFFERS_FILE, "w", encoding="utf-8"))
check("expired offer is gone on read", issues.pending_offer(DOOM), None)


section("file_issue - what actually reaches gh, recorded by the stub")
gh.calls.clear()
ok, url = issues.file_issue("bug", "lore button 404s",
                            "I was expecting the lore button to work?",
                            guest_id=DOOM, guest_name="doomassassin1",
                            project="storyizier")
check("filing succeeds", ok, True)
check("url returned", url, "https://github.com/example/intake/issues/7")
args = gh.calls[-1]
check("targets the configured repo",
      args[args.index("--repo") + 1], "example/intake")
check("title carries the category prefix",
      args[args.index("--title") + 1], "[Bug] lore button 404s")
labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
check("category label", "bug" in labels, True)
check("needs-triage on every guest filing", "needs-triage" in labels, True)
check("guest-report label", "guest-report" in labels, True)
check("project label", "project:storyizier" in labels, True)
body = args[args.index("--body") + 1]
check("body names the guest",
      "doomassassin1" in body and str(DOOM) in body, True)
check("body carries the verbatim quote",
      "I was expecting the lore button to work?" in body, True)
check("body fences the quote as data (nonce markers + boundary warning)",
      "--- guest report [" in body and "--- end of guest report [" in body
      and "real boundaries" in body, True)
check("body states the triage gate", "approved" in body, True)
check("body marks quoted text untrusted", "third-party" in body, True)
rec = [json.loads(l) for l in open(issues.ISSUES_FILE, encoding="utf-8")]
check("jsonl record written", len(rec), 1)
check("record carries the url", rec[0]["url"], url)
check("record carries the author", rec[0]["author_id"], DOOM)

section("file_issue - refusals and the fallback contract")
ok, why = issues.file_issue("exploit", "x", "y", guest_id=DOOM)
check("unknown category refused", ok, False)
ok, why = issues.file_issue("bug", "", "report", guest_id=DOOM)
check("empty title refused", ok, False)
gh.fail = "connection refused"
n_before = len(open(issues.ISSUES_FILE, encoding="utf-8").readlines())
ok, why = issues.file_issue("bug", "t", "report text", guest_id=DOOM)
check("gh failure returns not-ok", ok, False)
check("gh failure is worded, not raised", "tracker" in why, True)
n_after = len(open(issues.ISSUES_FILE, encoding="utf-8").readlines())
check("no jsonl record for a failed filing", n_after, n_before)
gh.fail = None
issues.ENABLED = False
ok, why = issues.file_issue("bug", "t", "q", guest_id=DOOM)
check("disabled funnel refuses", ok, False)
issues.ENABLED = True

section("the daily cap counts real filings only")
# One real filing exists from above; cap is 3, so two more fill it.
for i in range(2):
    ok, _ = issues.file_issue("idea", f"idea {i}", f"text {i}", guest_id=DOOM)
    check(f"filing {i + 2}/3 accepted", ok, True)
ok, why = issues.file_issue("idea", "one too many", "text", guest_id=DOOM)
check("cap refuses the 4th", ok, False)
check("cap refusal is worded for the guest", "cap" in why, True)
ok, _ = issues.file_issue("bug", "owner path", "owner text",
                          filed_by="Caz (owner/CLI)")
check("owner filings are not capped", ok, True)


section("registry - file_issue exists and guests cannot reach it")
from benham.core import capabilities  # noqa: E402
from benham.core import policy  # noqa: E402
act = capabilities.REGISTRY.get("file_issue")
check("registered", act is not None, True)
check("tier is manage", act.tier, 2)
check("not a guest capability", act.guest, False)
check("default origins (no GUEST_DM, no SYSTEM)", act.origins, None)
check("guest grants exclude it",
      "file_issue" in capabilities.guest_grants(), False)
d = policy.authorize(act, policy.CallContext.guest_dm(DOOM))
check("policy denies a guest call", d.allowed, False)


section("the guest prompt knows filing exists, and won't deny it in plain English")
# 2026-08-20, DM 1531359829682028634: a guest asked twice for "a note for
# claude" and got "I can't send messages or post anywhere" then "I can't make
# notes or save anything". Both are false of this system - the prefix path is
# right there - and the report was lost until a session read raw inbox history
# days later. The prompt is the only thing steering that reply, so assert it.
import io as _io  # noqa: E402
import os as _os  # noqa: E402
from benham import paths as _paths  # noqa: E402
_persona = _io.open(_os.path.join(_paths.PROMPTS_DIR, "guest_persona.md"),
                   encoding="utf-8").read().lower()
check("it names every filing prefix",
      all(pfx in _persona for pfx in ("idea..", "bug..", "want..")), True)
check("it reads a note/remember/pass-along request as a filing request",
      "is a filing request, and the answer is yes" in _persona, True)
check("...and forbids the flat inability that was actually said",
      "never answer that with a flat inability" in _persona, True)
check("...quoting the sentence, so a reword can't drift back into it",
      "make notes or save anything" in _persona, True)
check("the no-tools list no longer swallows it",
      "about acting in the world, never about" in _persona, True)
check("'ask him yourself' is carved out for this case",
      "written down and reach him" in _persona, True)

_guide = _io.open(_os.path.join(_paths.PROMPTS_DIR, "guest_guide.md"),
                 encoding="utf-8").read().lower()
check("the handout documents all three prefixes, not just idea..",
      all(pfx in _guide for pfx in ("idea..", "bug..", "want..")), True)



section("the deterministic detector - code notices what the model missed")
# Both of these are VERBATIM from real DMs where the model's <<issue:>> tag did
# not fire and a real report was lost. They are the reason this exists, so they
# are asserted rather than described.
check("Doom naming the forgetting defect outright (2026-08-20, missed live)",
      (issues.detect_complaint(
          "i like the first set so ill go with those, can you also make a "
          "naming text schematic that i can relay to you due to the issue of "
          "you easily forgetting past conversation?") or (None,))[0], "bug")
check("a Storyizier UI report with no second person in it",
      (issues.detect_complaint(
          "the agree and respond button for the party choice doesnt seem to "
          "work properly") or (None,))[0], "bug")
check("a capability gap phrased as a question",
      (issues.detect_complaint("why cant you remember what i said an hour "
                               "ago?") or (None,))[0], "bug")
check("'having a hard time' counts as a complaint",
      (issues.detect_complaint("benham is having a hard time seeing images "
                               "for some reason") or (None,))[0], "bug")
check("a real wish files as want, not bug",
      (issues.detect_complaint("it should be able to export my story, you "
                               "should be able to do that") or (None,))[0],
      "want")

# Precision is the whole problem: these guests talk about broken video games
# constantly, and a wrong offer reads as not listening. Every line below was
# either said for real or is one word away from something that was.
check("game talk stays out (no subject)",
      issues.detect_complaint("division 2 is broken right now, the servers "
                              "keep crashing"), None)
check("someone else's hardware stays out",
      issues.detect_complaint("my controller doesnt work properly anymore"),
      None)
check("'can you add X' is a request to Benham, not a feature request",
      issues.detect_complaint("can you add a text note to my files named "
                              "anime to watch"), None)
check("...and the other real one that used to false-positive",
      issues.detect_complaint("can you add some verity to the names"), None)
check("ordinary chat stays out",
      issues.detect_complaint("ty, and i hope yopu have a good day"), None)
check("too short to act on",
      issues.detect_complaint("it broke"), None)
check("an explicit prefix wins - the detector never doubles up",
      issues.detect_complaint("bug.. the thing is broken and you cant see it"),
      None)
check("...idea.. too, though bot.py handles it before the brain runs",
      issues.detect_complaint("idea.. the lore button doesnt work for you"),
      None)

section("the detector parks like the tag does")
issues._DETECT_FILE = os.path.join(_tmp, "issue_detect.json")
issues.OFFERS_FILE = os.path.join(_tmp, "detect_offers.json")
_complaint = "why cant you remember what i said an hour ago?"
_line = issues.offer_from_message(DOOM, _complaint)
check("issuer gets the SAME offer wording the tag produces",
      isinstance(_line, str) and "say **yes** and it's filed" in _line, True)
_parked = issues.pending_offer(DOOM)
check("a proposal is parked", _parked is not None, True)
check("the quote is the guest's own words, captured by code",
      _parked["quote"], _complaint)
check("nothing was filed yet - parking is not filing",
      _parked.get("url"), None)
check("a second complaint inside the cooldown is not offered again",
      issues.offer_from_message(DOOM, "you keep forgetting things we said"),
      None)
issues.clear_offer(DOOM)
check("...still silent while the cooldown holds, offer or no offer",
      issues.offer_from_message(DOOM, "the lore button doesnt work at all"),
      None)
issues._DETECT_FILE = os.path.join(_tmp, "issue_detect2.json")
check("a non-issuer is never offered anything",
      issues.offer_from_message(STRANGER, _complaint), None)

section("never lost - a report survives GitHub being down")
# The hole this closes: ideas.py's fallback is NARROWER than this funnel
# (MAX_LEN 1000 vs MAX_QUOTE 1500, separate daily caps), so a report could pass
# every check the guest was subject to and still be dropped when GitHub blinked.
issues.ISSUES_FILE = os.path.join(_tmp, "unsent.jsonl")
_long = "x" * 1200
check("record_unsent writes the record",
      issues.record_unsent("bug", _long, guest_id=DOOM, guest_name="doom",
                           reason="couldn't reach the tracker"), True)
_pending = issues.unsent()
check("exactly one report is waiting", len(_pending), 1)
check("it is marked unsent", _pending[0]["unsent"], True)
check("it has no url yet", _pending[0]["url"], "")
check("the guest's words are kept in full, not truncated to ideas' limit",
      len(_pending[0]["quote"]) > 1000, True)
check("it counts against the cap - the guest was told it was filed",
      issues.filed_today(DOOM), 1)

gh.calls = []
gh.fail = None
_sent, _failed, _urls = issues.retry_unsent()
check("the retry sends it", (_sent, _failed), (1, 0))
check("nothing is left waiting", issues.unsent(), [])
_args = " ".join(gh.calls[0])
check("the guest-report label survives the retry",
      "guest-report" in _args, True)
check("...and so does needs-triage - a retry is not a promotion",
      "needs-triage" in _args, True)
check("the guest is still named in the body, not lost to the retry",
      "doom" in _args, True)
check("the placeholder is gone, so one report is one record",
      len([e for e in issues._entries() if e.get("quote")]), 0)
check("the delivered record carries the url",
      issues._entries()[-1]["url"].startswith("http"), True)
check("the cap was not charged twice for one report",
      issues.filed_today(DOOM), 1)

gh.fail = "still down"
issues.record_unsent("bug", "the button doesnt work", guest_id=DOOM,
                     guest_name="doom", reason="down")
_sent2, _failed2, _ = issues.retry_unsent()
check("a retry that fails keeps the record", (_sent2, _failed2), (0, 1))
check("...still waiting, to be tried again", len(issues.unsent()), 1)
gh.fail = None

section("the persona no longer contradicts a real capability")
_persona2 = _io.open(_os.path.join(_paths.PROMPTS_DIR, "guest_persona.md"),
                    encoding="utf-8").read().lower()
check("web search is carved out of the no-tools absolute",
      "no tools on this path except web search" in _persona2, True)
check("the false answer that was actually given is quoted",
      "knowledge cutoff in early 2024" in _persona2, True)
check("...and the blanket link refusal too",
      "load content from outside" in _persona2, True)

section("the guest path caches its system prompt")
_guestsrc = _io.open(_os.path.join(_os.path.dirname(_paths.PROMPTS_DIR),
                                  "benham", "guest", "guest.py"),
                    encoding="utf-8").read()
check("the persona is sent as a cacheable block",
      "cache_control" in _guestsrc, True)
check("cache hits are logged, because a dead cache is silent otherwise",
      "cache_read=" in _guestsrc, True)

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    _sys.exit(1)
print("all green")
