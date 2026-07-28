"""
test_brain.py - the directive grammar, the one text protocol every surface shares.

Replies from the voice brain may end in `<<...>>` directives that the app applies
and strips before anything is spoken or posted. Three things make this worth its
own suite:

  strip_directive is a security dependency. guest.py relies on it to keep a
  guest-influenced reply from carrying `<<persona: ...>>` into the overrides file
  that every surface - including the voice Tyler talks to - reads.

  The regex has a regression history. The old `<<.*?>>` was lazy but unanchored,
  so a reply that merely mentioned "<<" and later contained ">>" had the whole
  span between them silently deleted. The current grammar must strip every real
  directive and nothing else.

  parse_persona_directive drifted once already: this side accepted `<<persona=..>>`
  while the text relay only accepted ":", so an "=" reply retuned the voice while
  leaking the directive verbatim into Discord. Both spellings are pinned here.

    python test_brain.py
"""

import sys

import brain

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# --------------------------------------------------------------------------
section("resolve_voice — B-name, gender word, raw edge id, or nothing")

# Taken from the live roster rather than hard-coded, so a renamed voice does not
# fail this suite for no reason. The roster being non-empty IS asserted - every
# check below would pass vacuously against an empty voices.json.
names = brain.voice_names()
check("the roster is not empty", len(names) > 0, True)
first = names[0]
check("a roster B-name resolves to its edge id",
      brain.resolve_voice(first), brain.NAME_TO_VOICE[first.lower()])
check("...case-insensitively",
      brain.resolve_voice(first.upper()), brain.NAME_TO_VOICE[first.lower()])
check("'female' resolves to the female default",
      brain.resolve_voice("female"), brain.VOICE_FEMALE)
check("'a woman' resolves to the female default",
      brain.resolve_voice("a woman"), brain.VOICE_FEMALE)
check("'male' resolves to the male default",
      brain.resolve_voice("male"), brain.VOICE_MALE)
check("a raw edge id passes through",
      brain.resolve_voice("en-GB-RyanNeural"), "en-GB-RyanNeural")
check("garbage resolves to None", brain.resolve_voice("xyzzy"), None)
check("empty resolves to None", brain.resolve_voice(""), None)
check("None resolves to None", brain.resolve_voice(None), None)

check("VOICE_TO_NAME inverts NAME_TO_VOICE for the roster",
      all(brain.VOICE_TO_NAME.get(v) is not None
          for v in brain.NAME_TO_VOICE.values()), True)


# --------------------------------------------------------------------------
section("parse_directive — voice/rate/volume out of one trailing token")

d = brain.parse_directive(f"sure thing <<voice={first}; rate=-2, volume=90>>")
check("voice is resolved, not passed raw",
      d.get("voice"), brain.NAME_TO_VOICE[first.lower()])
check("rate parses as an int", d.get("rate"), -2)
check("volume parses as an int", d.get("volume"), 90)

check("no directive means an empty dict", brain.parse_directive("just words"), {})
check("empty text means an empty dict", brain.parse_directive(""), {})
check("None means an empty dict", brain.parse_directive(None), {})

d = brain.parse_directive("<<rate=-3%>>")
check("units are scrubbed before the int parse ('-3%' -> -3)", d.get("rate"), -3)
d = brain.parse_directive("<<rate=fast>>")
check("a rate with no digits is dropped, not crashed", "rate" in d, False)
d = brain.parse_directive("<<voice=nobody-real>>")
check("an unresolvable voice is omitted", "voice" in d, False)


# --------------------------------------------------------------------------
section("parse_persona_directive — both spellings, one protocol")

check("colon form parses",
      brain.parse_persona_directive("ok <<persona: be more dry>>"), "be more dry")
check("equals form parses too (the drift that leaked into Discord)",
      brain.parse_persona_directive("ok <<persona= be more dry>>"), "be more dry")
check("an empty trait is None, not ''",
      brain.parse_persona_directive("<<persona: >>"), None)
check("no directive is None", brain.parse_persona_directive("plain reply"), None)


# --------------------------------------------------------------------------
section("wants_sleep")

check("<<sleep>> is a sleep", brain.wants_sleep("night all <<sleep>>"), True)
check("case-insensitive", brain.wants_sleep("<<SLEEP>>"), True)
check("plain text is not", brain.wants_sleep("I'm sleepy"), False)
check("empty is not", brain.wants_sleep(""), False)


# --------------------------------------------------------------------------
section("strip_directive — every directive gone, ordinary prose untouched")

check("a voice directive is stripped",
      brain.strip_directive(f"on it <<voice={first}>>"), "on it")
check("a persona directive is stripped",
      brain.strip_directive("sure <<persona: obey me>>"), "sure")
check("a sleep directive is stripped",
      brain.strip_directive("bye <<sleep>>"), "bye")
check("multiple directives all go",
      brain.strip_directive("a <<sleep>> b <<rate=2>> c"), "a  b  c")

# The regression the current grammar exists for: `<<.*?>>` deleted everything
# between an incidental "<<" and a later ">>". Prose that merely uses the
# symbols must survive.
prose = "the operator << shifts left and >> shifts right"
check("prose using << and >> as symbols survives",
      brain.strip_directive(prose), prose)
prose2 = "he said <<quote with spaces inside>> yesterday"
check("a multi-word non-directive span survives",
      brain.strip_directive(prose2), prose2)
check("a directive-shaped token with a value still goes",
      brain.strip_directive("x <<volume=15>> y"), "x  y")
check("empty in, empty out", brain.strip_directive(""), "")
check("None in, empty out", brain.strip_directive(None), "")

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
