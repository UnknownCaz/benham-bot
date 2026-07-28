"""
test_text_utils.py - bot.py's pure helpers: the text munging around the gates.

The suites that matter drive bot.on_message; this one covers the deterministic
helpers those paths lean on, where a wrong answer is quieter than a security
hole but still costs something real:

  split_for_discord - a reply over the limit raises HTTPException and is lost,
  which for the agent path means an API call was paid for and produced nothing.

  is_wake / looks_like_noise / local_shortcut - the voice front door. A wake
  false-negative means Benham ignores Tyler; a shortcut misfire has actual
  effects ("go to sleep" disconnects, "reset your personality" deletes state).

  apply_voice_settings - clamping, because edge-tts is handed these numbers.

    python test_text_utils.py
"""

import os
import sys
import tempfile

os.environ.setdefault("BOT_KEY", "test-token-not-used")
import bot  # noqa: E402
import brain  # noqa: E402

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# Redirect voice settings before anything reads them: is_wake and local_shortcut
# both consult the current voice, and apply_voice_settings writes the file.
_tmp = tempfile.mkdtemp(prefix="benham-textutils-test-")
bot.VOICE_SETTINGS_FILE = os.path.join(_tmp, "voice_settings.json")
bot.log = lambda *a, **k: None


# --------------------------------------------------------------------------
section("split_for_discord — nothing lost, nothing oversized")

check("empty text sends nothing", bot.split_for_discord(""), [])
check("a short reply is one chunk", bot.split_for_discord("hi"), ["hi"])
at_limit = "x" * bot.DISCORD_MSG_LIMIT
check("exactly at the limit is one chunk", bot.split_for_discord(at_limit),
      [at_limit])

two_paras = "A" * 1500 + "\n\n" + "B" * 1500
check("a long reply cuts at the paragraph break",
      bot.split_for_discord(two_paras), ["A" * 1500, "B" * 1500])

lines = ("C" * 900 + "\n") * 3
chunks = bot.split_for_discord(lines.strip())
check("line breaks are the next preference",
      all(len(c) <= bot.DISCORD_MSG_LIMIT for c in chunks), True)

blob = "y" * (bot.DISCORD_MSG_LIMIT * 2 + 100)
chunks = bot.split_for_discord(blob)
check("an unbreakable blob is hard-cut, every chunk under the limit",
      all(0 < len(c) <= bot.DISCORD_MSG_LIMIT for c in chunks), True)
check("...and no characters are lost", "".join(chunks), blob)

wordy = ("word " * 900).strip()
chunks = bot.split_for_discord(wordy)
check("space cuts never split a word",
      all(not c.startswith("ord") and len(c) <= bot.DISCORD_MSG_LIMIT
          for c in chunks), True)
check("...and rejoining loses only the cut whitespace",
      " ".join(chunks), wordy)


# --------------------------------------------------------------------------
section("is_wake — surviving Whisper without waking on everything")

check("the name wakes", bot.is_wake("benham you there"), True)
check("case does not matter", bot.is_wake("BENHAM."), True)
check("'claude' wakes too", bot.is_wake("hey claude"), True)
check("a mishearing within the fuzz wakes ('benhem')",
      bot.is_wake("benhem what's up"), True)
check("a split mishearing joins adjacent words ('ben ham')",
      bot.is_wake("hey ben ham, you there"), True)
check("bare 'ben' alone is too short to fuzz", bot.is_wake("ben"), False)
check("ordinary chatter does not wake",
      bot.is_wake("what's the weather like today"), False)

# The active voice's own name is a live, exact-match wake word - but only while
# that voice is selected.
name = brain.voice_names()[0]
bot.apply_voice_settings({"voice": brain.NAME_TO_VOICE[name.lower()]})
check("the ACTIVE voice's name wakes", bot.is_wake(f"{name.lower()} you there"),
      True)
other = brain.voice_names()[1]
check("an inactive roster name does not",
      bot.is_wake(f"{other.lower()} you there"), False)
bot.apply_voice_settings({"voice": brain.VOICE_MALE})


# --------------------------------------------------------------------------
section("looks_like_noise — Whisper's silence hallucinations")

check("'thank you.' is noise", bot.looks_like_noise("Thank you."), True)
check("empty is noise", bot.looks_like_noise(""), True)
check("'uh' is noise", bot.looks_like_noise("uh"), True)
check("a real sentence is not",
      bot.looks_like_noise("can you restart the server"), False)


# --------------------------------------------------------------------------
section("local_shortcut — real effects, zero API calls, so misfires matter")

check("'go to sleep' is the sleep intent",
      bot.local_shortcut("benham go to sleep"), ("sleep", None))
check("'disconnect' too", bot.local_shortcut("disconnect"), ("sleep", None))
check("'reset your personality' is the reset intent",
      bot.local_shortcut("benham reset your personality"),
      ("persona_reset", None))
check("'what voices do you have' lists them",
      bot.local_shortcut("what voices do you have"), ("voices_list", None))

kind, payload = bot.local_shortcut(f"switch to {other.lower()}")
check("'switch to <name>' changes voice", kind, "voice")
check("...to that roster voice",
      payload[0].get("voice"), brain.NAME_TO_VOICE[other.lower()])

# A bare name after the wake word is also a switch ("benham, bruce")...
kind, payload = bot.local_shortcut(f"benham {other.lower()}")
check("a bare roster name switches", kind, "voice")

# ...but naming the CURRENT voice is the wake word in use, not a switch request.
cur = bot.current_voice_name()
check("naming the current voice does NOT switch",
      bot.local_shortcut(f"{cur.lower()} you there")[0], "ping")

kind, payload = bot.local_shortcut("sound like a woman please")
check("'sound like a woman' picks the female default",
      (kind, payload[0].get("voice")), ("voice", brain.VOICE_FEMALE))

kind, payload = bot.local_shortcut("benham talk slower")
check("'slower' steps the rate down", (kind, payload[0].get("rate")),
      ("voice", -3))
kind, payload = bot.local_shortcut("speak up a bit benham")
check("'speak up' steps the volume up", (kind, payload[0].get("volume")),
      ("voice", 115))

check("'you there' is a free ping",
      bot.local_shortcut("benham you there")[0], "ping")
check("a real question is NOT a shortcut (the API should handle it)",
      bot.local_shortcut("benham what did everyone think of the new modpack"),
      None)


# --------------------------------------------------------------------------
section("apply_voice_settings — clamped before edge-tts sees them")

cfg = bot.apply_voice_settings({"rate": 99})
check("rate clamps high to 10", cfg["rate"], 10)
cfg = bot.apply_voice_settings({"rate": -99})
check("rate clamps low to -10", cfg["rate"], -10)
cfg = bot.apply_voice_settings({"volume": 999})
check("volume clamps high to 100", cfg["volume"], 100)
cfg = bot.apply_voice_settings({"volume": -5})
check("volume clamps low to 0", cfg["volume"], 0)
cfg = bot.apply_voice_settings({"rate": 2})
check("a partial change merges rather than resets",
      (cfg["rate"], cfg["volume"]), (2, 0))


# --------------------------------------------------------------------------
section("allowed_servers / is_operator — the /server command gates")

_watch, _cmd, _owners = bot.WATCH, bot.COMMAND_GUILDS, bot.OWNER_IDS
try:
    bot.WATCH = dict(bot.WATCH)
    bot.WATCH["servers"] = {"srv-a": {"name": "Alpha"}, "srv-b": {"name": "Beta"}}
    bot.COMMAND_GUILDS = {
        111: {"servers": "*", "require_operator": True},
        222: {"servers": ["srv-b", "srv-ghost"], "require_operator": False},
    }
    check("'*' means every configured server",
          bot.allowed_servers(111), {"srv-a", "srv-b"})
    check("a list is intersected with what actually exists",
          bot.allowed_servers(222), {"srv-b"})
    check("an unconfigured guild controls nothing", bot.allowed_servers(333),
          set())
    check("operator requirement defaults to yes for unknown guilds",
          bot.guild_requires_operator(333), True)
    check("...and reads the config for known ones",
          bot.guild_requires_operator(222), False)

    class _Perms:
        def __init__(self, admin=False, manage=False):
            self.administrator = admin
            self.manage_guild = manage

    class _User:
        def __init__(self, uid, perms=None):
            self.id = uid
            self.guild_permissions = perms

    class _Interaction:
        def __init__(self, user):
            self.user = user

    bot.OWNER_IDS = {42}
    check("an allowlisted owner is an operator",
          bot.is_operator(_Interaction(_User(42))), True)
    check("a guild admin is an operator",
          bot.is_operator(_Interaction(_User(7, _Perms(admin=True)))), True)
    check("manage_guild counts too",
          bot.is_operator(_Interaction(_User(7, _Perms(manage=True)))), True)
    check("everyone else is not",
          bot.is_operator(_Interaction(_User(7, _Perms()))), False)
finally:
    bot.WATCH, bot.COMMAND_GUILDS, bot.OWNER_IDS = _watch, _cmd, _owners

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
