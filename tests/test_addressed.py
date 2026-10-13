"""
test_addressed.py - what counts as pinging Benham.

2026-10-12: Caz typed @Benham in a server and Discord resolved it to the bot's
MANAGED role (role_mentions), not the user mention (mentions). on_message only
looked at mentions, so the message was logged and ignored. A managed bot role is
held by that one bot, so pinging it is addressing him; @everyone, @here and
ordinary roles are not.

    python test_addressed.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import sys

from benham import bot

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


class _User:
    def __init__(self, uid, name):
        self.id, self.name = uid, name


class _Tags:
    def __init__(self, bot_id):
        self.bot_id = bot_id


class _Role:
    def __init__(self, rid, name, managed=False, tags=None):
        self.id, self.name, self.managed, self.tags = rid, name, managed, tags


class _Msg:
    def __init__(self, content="", mentions=(), role_mentions=(), guild=object()):
        self.content, self.guild = content, guild
        self.mentions, self.role_mentions = list(mentions), list(role_mentions)


BENHAM = _User(752313060970201218, "Benham")
OTHER_BOT = _User(111222333444555666, "Other")
OWN_ROLE = _Role(1558814016246259806, "Benham", managed=True, tags=_Tags(BENHAM.id))
OTHER_BOT_ROLE = _Role(42, "Other", managed=True, tags=_Tags(OTHER_BOT.id))
PLAIN_ROLE = _Role(43, "Benham", managed=False)          # a human role that borrowed the name
NO_TAGS_OWN = _Role(44, "Benham", managed=True, tags=None)
NO_TAGS_OTHER = _Role(45, "Somebody", managed=True, tags=None)
EVERYONE = _Role(7, "@everyone", managed=False)

print("\nThe user mention")
check("user mention -> addressed", bot._addressed(_Msg(mentions=[BENHAM]), BENHAM), True)
check("someone else's mention -> not", bot._addressed(_Msg(mentions=[OTHER_BOT]), BENHAM), False)
check("no mention at all -> not", bot._addressed(_Msg("hello"), BENHAM), False)

print("\nThe managed role")
check("own managed role (bot_id matches) -> addressed",
      bot._addressed(_Msg(role_mentions=[OWN_ROLE]), BENHAM), True)
check("another bot's managed role -> not",
      bot._addressed(_Msg(role_mentions=[OTHER_BOT_ROLE]), BENHAM), False)
check("an ordinary role, even named Benham -> not",
      bot._addressed(_Msg(role_mentions=[PLAIN_ROLE]), BENHAM), False)
check("@everyone / @here -> not", bot._addressed(_Msg(role_mentions=[EVERYONE]), BENHAM), False)
check("managed role, no tags, same name -> addressed (fallback)",
      bot._addressed(_Msg(role_mentions=[NO_TAGS_OWN]), BENHAM), True)
check("managed role, no tags, other name -> not",
      bot._addressed(_Msg(role_mentions=[NO_TAGS_OTHER]), BENHAM), False)
check("own role among others -> addressed",
      bot._addressed(_Msg(role_mentions=[PLAIN_ROLE, OTHER_BOT_ROLE, OWN_ROLE]), BENHAM), True)

print("\nEdges")
check("not logged in (user None) -> not", bot._addressed(_Msg(mentions=[BENHAM]), None), False)
check("a stub with no role_mentions attribute still works",
      bot._addressed(type("M", (), {"mentions": [BENHAM]})(), BENHAM), True)
check("a DM carries no mention and the helper does not pretend otherwise",
      bot._addressed(_Msg("hi", guild=None), BENHAM), False)

print("\nStripping the address from the text")
bot.client = type("C", (), {"user": BENHAM})()
check("role form is removed",
      bot.strip_mention(_Msg("<@&1558814016246259806> hello", role_mentions=[OWN_ROLE])), "hello")
check("user form is removed",
      bot.strip_mention(_Msg("<@752313060970201218> hello", mentions=[BENHAM])), "hello")
check("another bot's role stays in the text",
      bot.strip_mention(_Msg("<@&42> hello", role_mentions=[OTHER_BOT_ROLE])), "<@&42> hello")

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
