"""
test_privacy_screen.py - the owner's personal terms never leave the machine (issue 11).

2026-10-19: Benham filed two tracker issues that carried the owner's legal name.
The persona and the tool descriptions name him, the brain repeated what it read,
and nothing between the brain and the wire looked at the words. The fix is code
at the wire, so this file pins three things:

  The screen (policy.screen_text / screen_value). Whole words of a configured
  term, any case, possessive included, become "the owner"; a full name goes
  before its parts. Anything still there afterwards - inside a longer word,
  split by a zero-width character - is a DENY, and so is an empty term list.

  The wire (core/wire.py). discord's own send / edit / webhook / interaction
  methods are wrapped, so every path inherits the screen. Driven here through
  the REAL wrapped methods, with the HTTP layer underneath replaced by a
  recording stub: a send to a channel goes out rewritten, a survivor is refused
  and never reaches the stub, a poll / button / file name carrying a term is
  refused, and the owner's own DM passes untouched (ring 1).

  The record (core/outwardlog.py). Every send - and every refusal - is written
  locally with its full text and, inside a capability, the capability's name.

The terms are invented. A real name never goes in a tracked file.

    python test_privacy_screen.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - control.json fixture; must precede benham imports

import asyncio
import sys
import tempfile

import discord

from benham import paths
from benham.core import identity, outwardlog, policy, wire

paths.STATE_DIR = tempfile.mkdtemp(prefix="benham-privacy-test-")

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


TERMS = ("Zebulon Quartermaine", "Quartermaine", "Zebulon")
OWNER = int(__import__("json").load(open(_os.path.join(paths.CONFIG_DIR, "control.json"),
                                         encoding="utf-8"))["owner_ids"][0])
STRANGER = 111000111000111000

section("the fixture carries invented terms, and identity reads them longest first")
check("privacy_terms from control.json.example, each word of a full name added",
      identity.privacy_terms(), TERMS)
check("the owner id the fixture uses is the owner", identity.is_owner(OWNER), True)


section("screen_text - rewrite whole words, refuse anything left")
t, d = policy.screen_text("Zebulon said hi", TERMS)
check("a whole word is rewritten", (t, d.allowed, d.rule), ("the owner said hi", True, "privacy_rewrote"))
t, d = policy.screen_text("ask ZEBULON's bot, or zebulon", TERMS)
check("any case, possessive kept", t, "ask the owner's bot, or the owner")
t, d = policy.screen_text("Zebulon Quartermaine filed it", TERMS)
check("the full name goes as one unit", t, "the owner filed it")
t, d = policy.screen_text(r"path C:\Users\Zebulon\Claude", TERMS)
check("inside a path it is still a whole word", t, r"path C:\Users\the owner\Claude")
t, d = policy.screen_text("nothing personal here", TERMS)
check("clean text passes unchanged", (t, d.allowed, d.rule), ("nothing personal here", True, ""))
t, d = policy.screen_text("the Zebulons came", TERMS)
check("inside a longer word: not rewritten, so REFUSED", (d.denied, d.rule), (True, "privacy_survivor"))
t, d = policy.screen_text("Zebu\u200blon", TERMS)
check("split by a zero-width space: REFUSED", d.denied, True)
t, d = policy.screen_text("xZebulonx", TERMS)
check("glued to other letters: REFUSED", d.denied, True)
t, d = policy.screen_text("anything", ())
check("no terms = REFUSED (fail-closed)", (d.denied, d.rule), (True, "privacy_unconfigured"))


section("hardening from the 2026-10-19 review - readings a person still sees as the name")
for label, text in [
        ("combining grapheme joiner", "Zebu͏lon"),
        ("variation selector", "Zebu️lon"),
        ("Hangul filler", "Zebuㅤlon"),
        ("fullwidth letter", "Ｚebulon"),
        ("markdown bold inside", "Ze**b**ulon"),
        ("spoiler inside", "Zeb||ulon||"),
        ("HTML entity", "&#90;ebulon"),
        ("empty HTML tag inside", "Ze<b></b>bulon"),
        ("letters spaced out", "Z e b u l o n"),
        ("letters dotted out", "Z.e.b.u.l.o.n")]:
    _, d = policy.screen_text(text, TERMS)
    check(f"{label}: REFUSED", d.denied, True)
t, d = policy.screen_text("Zebulon  Quartermaine and Zebulon\nQuartermaine", TERMS)
check("a full name split by a double space or a line break goes as one unit",
      t, "the owner and the owner")
t, d = policy.screen_text("Mr Quartermaine said", TERMS)
check("the surname alone is a term too", t, "Mr the owner said")
t, d = policy.screen_text("https://example.com/zebulon/pic.png", TERMS)
check("a term inside a link is refused, not rewritten", d.rule, "privacy_in_url")
t, d = policy.screen_text("see https://example.com/a - Zebulon", TERMS)
check("...a clean link beside a term is fine", (d.allowed, t), (True, "see https://example.com/a - the owner"))
for text in ("pretty lerp", "about the cab", "zeb ulon the band"):
    _, d = policy.screen_text(text, TERMS + ("Ab",))
    check(f"ordinary words still pass: {text!r}", d.allowed, True)
t, d = policy.screen_text("hi AB, and xab", ("Ab",))
check("a short term matches as a whole word only", (t, d.allowed), ("hi the owner, and xab", True))

section("screen_value - every string inside a structure")
v, d = policy.screen_value({"title": "Zebulon", "fields": [{"value": "hi zebulon"}], "n": 3}, TERMS)
check("nested strings rewritten, other values kept",
      v, {"title": "the owner", "fields": [{"value": "hi the owner"}], "n": 3})
check("...reported as a rewrite", d.rule, "privacy_rewrote")
v, d = policy.screen_value(["fine", "Zebulonian"], TERMS)
check("one survivor anywhere refuses the whole value", d.denied, True)


# ---- the wire, through the real wrapped discord methods -------------------

SENT = []


async def _stub_send(self, content=None, **kw):
    SENT.append({"to": self, "content": content, **kw})
    return type("Msg", (), {"id": 42})()


async def _stub_edit(self, **kw):
    SENT.append({"to": "edit", **kw})
    return self


async def _stub_hook(self, content=discord.utils.MISSING, **kw):
    SENT.append({"to": "webhook", "content": content, **kw})


discord.abc.Messageable.send = _stub_send
discord.Message.edit = _stub_edit
discord.Webhook.send = _stub_hook
wire._ORIG.clear()
LOGS = []
wired = wire.install(LOGS.append)
check("install wraps send, edit, webhook and interaction responses", wired,
      ["InteractionResponse.edit_message", "InteractionResponse.send_message",
       "Message.edit", "Messageable.send", "Webhook.send"])
check("install is idempotent", wire.install(LOGS.append), wired)


class FakeChannel(discord.abc.Messageable):
    """A guild channel as far as the wire can tell: not a DM, not a user."""
    id = 1560197136363163650
    name = "kestra"
    guild = None

    async def _get_channel(self):
        return self


class FakeUser:
    def __init__(self, uid):
        self.id = uid


def dm_with(uid):
    ch = discord.DMChannel.__new__(discord.DMChannel)
    ch.id = 999
    ch.recipients = [FakeUser(uid)]
    return ch


async def wire_checks():
    section("the wire - a channel send is screened")
    SENT.clear()
    before = len(outwardlog.entries())
    await FakeChannel().send("Zebulon asked me to say hi")
    check("the words that reach discord are rewritten",
          SENT[-1]["content"], "the owner asked me to say hi")
    rec = outwardlog.entries()[before:]
    check("one record, sent, with the words that went",
          [(r["verdict"], r["text"]) for r in rec], [("sent", "the owner asked me to say hi")])
    check("a rewrite leaves a log line", any("privacy screen" in l for l in LOGS), True)

    section("the wire - a survivor is refused and never reaches discord")
    SENT.clear()
    raised = None
    try:
        await FakeChannel().send("hey Zebulonbot")
    except policy.OutwardBlocked as e:
        raised = e.decision.rule
    check("OutwardBlocked, rule privacy_survivor", raised, "privacy_survivor")
    check("nothing was sent", SENT, [])
    check("the refusal is logged", any(l.startswith("BLOCKED send") for l in LOGS), True)
    check("...and recorded", outwardlog.entries()[-1]["verdict"], "blocked")

    section("the wire - embeds are rewritten, polls / buttons / file names refuse")
    SENT.clear()
    await FakeChannel().send(embed=discord.Embed(title="for Zebulon", description="ok"))
    check("embed title rewritten", SENT[-1]["embed"].title, "for the owner")
    poll = discord.Poll(question="Zebulon's pick?", duration=__import__("datetime").timedelta(hours=1))
    poll.add_answer(text="a")
    raised = None
    try:
        await FakeChannel().send(poll=poll)
    except policy.OutwardBlocked as e:
        raised = e.decision.rule
    check("a poll naming him is refused (cannot be rewritten)", raised, "privacy_unrewritable")
    raised = None
    try:
        await FakeChannel().send("see file", file=discord.File(__file__, filename="zebulon.txt"))
    except policy.OutwardBlocked as e:
        raised = e.decision.rule
    check("a file name naming him is refused", raised, "privacy_unrewritable")

    section("the wire - text attachments are read; a term inside refuses the send")
    import io
    SENT.clear()
    raised = None
    try:
        await FakeChannel().send(file=discord.File(io.BytesIO(b"notes for Zebulon"), filename="notes.md"))
    except policy.OutwardBlocked as e:
        raised = e.decision.rule
    check("a .md file naming him is refused", (raised, SENT), ("privacy_unrewritable", []))
    buf = io.BytesIO(b"plain notes")
    await FakeChannel().send(file=discord.File(buf, filename="notes.txt"))
    check("a clean text file goes, and is rewound for the upload",
          (len(SENT), SENT[-1]["file"].fp.read()), (1, b"plain notes"))
    await FakeChannel().send(file=discord.File(io.BytesIO(b"Zebulon"), filename="pic.png"))
    check("a binary attachment is not read (out of reach, stated)", len(SENT), 2)
    raised = None
    try:
        await FakeChannel().send(file=discord.File(io.BytesIO(b"x"), filename="a.png",
                                                   description="photo of Zebulon"))
    except policy.OutwardBlocked as e:
        raised = e.decision.rule
    check("an attachment description naming him is refused", raised, "privacy_unrewritable")

    section("the wire - prescreen rewrites before a caller splits at 2000")
    long = ("x" * 1990) + " Zebulon"
    check("prescreen rewrites for a channel", wire.prescreen(FakeChannel(), long).endswith("the owner"), True)
    check("...and leaves the owner's DM alone", wire.prescreen(dm_with(OWNER), long), long)

    section("the wire - the owner's own DM is ring 1 and passes untouched")
    SENT.clear()
    await dm_with(OWNER).send("Zebulon, your build is done")
    check("owner DM: words unchanged", SENT[-1]["content"], "Zebulon, your build is done")
    check("...still recorded, marked owner_dm", outwardlog.entries()[-1]["rule"], "owner_dm")
    SENT.clear()
    await dm_with(STRANGER).send("Zebulon says hi")
    check("anyone else's DM is screened", SENT[-1]["content"], "the owner says hi")
    unknown = discord.DMChannel.__new__(discord.DMChannel)
    unknown.id, unknown.recipients = 998, []
    SENT.clear()
    await unknown.send("Zebulon says hi")
    check("a DM with an unknown recipient is screened, never assumed his",
          SENT[-1]["content"], "the owner says hi")

    section("the wire - edits and webhooks are screened too")
    SENT.clear()
    msg = discord.Message.__new__(discord.Message)
    msg.channel = FakeChannel()
    await msg.edit(content="edited for Zebulon")
    check("an edit's new content is rewritten", SENT[-1]["content"], "edited for the owner")
    hook = discord.Webhook.__new__(discord.Webhook)
    hook.id = 7
    SENT.clear()
    await hook.send("Zebulon via webhook", thread_name="Zebulon thread", username="Zebulon bot")
    check("webhook content, thread name and username rewritten",
          (SENT[-1]["content"], SENT[-1]["thread_name"], SENT[-1]["username"]),
          ("the owner via webhook", "the owner thread", "the owner bot"))

    section("the record knows which capability sent it")
    token = outwardlog.current_action.set("send_message")
    try:
        await FakeChannel().send("plain")
    finally:
        outwardlog.current_action.reset(token)
    check("via carries the capability name", outwardlog.entries()[-1]["via"], "send_message")
    await FakeChannel().send("plain again")
    check("...and None outside one", outwardlog.entries()[-1]["via"], None)

    section("no terms configured = nothing leaves except to the owner")
    saved = identity._PRIVACY
    identity._PRIVACY = {}
    try:
        SENT.clear()
        raised = None
        try:
            await FakeChannel().send("hello")
        except policy.OutwardBlocked as e:
            raised = e.decision.rule
        check("a channel send is refused", (raised, SENT), ("privacy_unconfigured", []))
        raised = None
        try:
            await FakeChannel().send(file=discord.File(__file__, filename="x.txt"))
        except policy.OutwardBlocked as e:
            raised = e.decision.rule
        check("a bare file with no text is refused too", raised, "privacy_unconfigured")
        await dm_with(OWNER).send("the screen is not configured")
        check("the owner's DM still works, so he hears about it",
              SENT[-1]["content"], "the screen is not configured")
    finally:
        identity._PRIVACY = saved


asyncio.run(wire_checks())


section("screen_params - text that reaches people without a send (names, nicknames, presence)")
from benham.core import capabilities  # noqa: E402
R = capabilities.REGISTRY
p, d = policy.screen_params(R["create_channel"], {"name": "zebulon-chat", "topic": "for Zebulon", "guild_id": "1"})
check("a channel name is screened as a whole word... ",
      (d.denied, p.get("topic")), (False, "for the owner"))
p, d = policy.screen_params(R["create_channel"], {"name": "zebulonchat"})
check("...and a glued one refuses the call", d.denied, True)
p, d = policy.screen_params(R["set_nickname"], {"nickname": "Zebulon", "user_id": "5", "guild_id": "6"})
check("a nickname is rewritten, ids untouched",
      p, {"nickname": "the owner", "user_id": "5", "guild_id": "6"})
p, d = policy.screen_params(R["post_room"], {"name": "x", "text": "Zebulon said"})
check("rooms stay on this machine and are not screened", p["text"], "Zebulon said")
p, d = policy.screen_params(R["dm_user"], {"user_id": str(OWNER), "content": "Zebulon"})
check("a DM to the owner is ring 1 and not screened", p["content"], "Zebulon")
p, d = policy.screen_params(R["dm_user"], {"user_id": str(STRANGER), "content": "Zebulon"})
check("a DM to anyone else is", p["content"], "the owner")
p, d = policy.screen_params(R["purge_messages"], {"channel_id": "1", "contains": "Zebulon"})
check("a purge filter is local and is NOT rewritten (it would purge the wrong messages)",
      p["contains"], "Zebulon")
p, d = policy.screen_params(R["read_channel"], {"channel_id": "1"})
check("read-only capabilities are not screened", d.allowed, True)
_saved = identity._PRIVACY
identity._PRIVACY = {}
p, d = policy.screen_params(R["advance_conversation"], {"id": "c12"})
check("unconfigured: an id-only call still runs (its words pass the wire)", d.allowed, True)
p, d = policy.screen_params(R["set_presence"], {"activity_type": "playing", "activity_name": "x"})
check("unconfigured: free text is refused", (d.denied, d.rule), (True, "privacy_unconfigured"))
identity._PRIVACY = _saved


class PresenceClient:
    def __init__(self):
        self.calls = []

    async def change_presence(self, **kw):
        self.calls.append(kw)


async def run_checks():
    section("capabilities.run applies it before the handler")
    client, lines = PresenceClient(), []
    await capabilities.run(client, lines.append, "set_presence",
                           {"status": "online", "activity_type": "playing",
                            "activity_name": "with Zebulon"},
                           call_ctx=policy.CallContext.local())
    check("the handler saw the rewritten words",
          client.calls[-1]["activity"].name, "with the owner")
    check("the rewrite is logged", any("privacy screen" in l for l in lines), True)
    client.calls.clear()
    err = None
    try:
        await capabilities.run(client, lines.append, "set_presence",
                               {"activity_type": "playing", "activity_name": "Zebulonmania"},
                               call_ctx=policy.CallContext.local())
    except capabilities.ActionError as e:
        err = str(e)
    check("a survivor refuses the call with an ordinary ActionError",
          bool(err and "not run" in err), True)
    check("...the handler never ran", client.calls, [])
    check("...and the refusal has a DENIED line",
          any(l.startswith("DENIED set_presence") and "privacy_survivor" in l for l in lines), True)


asyncio.run(run_checks())


section("slash commands: what tree.sync publishes is screened first")
from benham import bot  # noqa: E402 - wire.install is idempotent, the stubs stay
_orig_desc = bot.server_group.description
check("the shipped /server description names nobody",
      policy.screen_text(_orig_desc, TERMS)[1].rule, "")
bot.server_group.description = "Control Zebulon's servers"
check("a description naming him is rewritten in place, and the sync goes on",
      (bot._screen_command_tree(), bot.server_group.description),
      (False, "Control the owner's servers"))
bot.server_group.description = "Control Zebulonia"
check("a survivor stops the sync (logged)", bot._screen_command_tree(), True)
bot.server_group.description = _orig_desc
check("...and the real tree passes", bot._screen_command_tree(), False)

_watch = bot.WATCH
bot.WATCH = {"servers": {"a": {"name": "Zebulon's SMP"}, "b": {"name": "Zebulonia"},
                         "c": {"name": "Survival"}}}
_allowed = bot.allowed_servers
bot.allowed_servers = lambda gid: {"a", "b", "c"}
try:
    _choices = asyncio.run(bot.server_autocomplete(type("I", (), {"guild_id": 1})(), ""))
finally:
    bot.WATCH, bot.allowed_servers = _watch, _allowed
check("autocomplete choices: rewritten, or dropped if a term survives",
      sorted(c.name for c in _choices), ["Survival", "the owner's SMP"])

print()
if _fails:
    print(f"FAIL - {len(_fails)} check(s): {', '.join(_fails)}")
    sys.exit(1)
print("all green")
