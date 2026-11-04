"""
test_jobs_close.py - borrowed tools and the close of a job (INTENT decision 61,
Stage 9 C; decision 62).

Caz, 2026-11-03: "The purpose should expire the guest shouldnt. So say they need
access to websearch (or something) they use it, and then I get a response from
Benham on what was done and found with the tool, in the form of a friendly heads
up, then I get prompted to have the tool removed, or kept."

So, in this file:

  * a person holds a tool set in people.json; grant_tool / revoke_tool change it
    one tool at a time, each on a card; web_search is the only grantable tool,
    anything else refused in words;
  * the guest brain is handed exactly what the person holds, read live (a
    revoke one second ago is honored), narrowed to an open job's tools, and the
    job's note says what the turn really carries; open_job's tools are a subset
    of what the person holds;
  * a person may hold several open jobs (Caz's call, 2026-11-03 chat): one
    message touches every one, the note lists every open goal, and only the
    same goal twice is refused;

Fully offline: no Discord, no API.

    python test_jobs_close.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import json
import os
import sys

from benham.core import capabilities
from benham.core import identity
from benham.core import jobs
from benham.core import people
from benham.core import policy
from benham.core.policy import CallContext

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)
    return ok


def section(name):
    print(f"\n{name}")


OWNER = sorted(identity.OWNER_IDS)[0]
FRIEND = _testconfig.GUEST_ID          # invented; on the fixture's guest.ids
FRIEND_2 = _testconfig.GUEST_ID_2
KNOWN = 555000555000555021             # invented; made known through people.json


class _Sent:
    def __init__(self, mid):
        self.id = mid


class _DM:
    def __init__(self, owner):
        self.owner = owner
        self.id = 7000 + owner.id % 1000

    async def send(self, content=None, files=None, view=None, **kw):
        DMS.append((self.owner.id, content))
        if view is not None:
            VIEWS.append((self.owner.id, content, view))
        return _Sent(880000 + len(DMS))


class _U:
    def __init__(self, uid, name):
        self.id, self.name = uid, name
        self.dm_channel = _DM(self)

    def __str__(self):
        return self.name


class _Client:
    def __init__(self, users):
        self.users = {u.id: u for u in users}
        self.guilds = []

    def get_user(self, uid):
        return self.users.get(int(uid))

    async def fetch_user(self, uid):
        return self.get_user(uid)


DMS, VIEWS = [], []
CLIENT = _Client([_U(KNOWN, "biglike"), _U(FRIEND, "doomlike"), _U(FRIEND_2, "other"),
                  _U(OWNER, "caz")])


def go(name, params=None, force=False, owner_dm=False, system=False):
    if system:
        cctx = CallContext.system()
    elif owner_dm:
        cctx = CallContext.owner_dm(OWNER, channel_id=1)
    else:
        cctx = CallContext.local()
    return asyncio.run(capabilities.run(
        CLIENT, lambda *_: None, name, params or {},
        actor_id=OWNER if owner_dm else None, force=force, call_ctx=cctx))


def refusal(name, params=None, force=False):
    try:
        go(name, params, force=force)
        return None
    except capabilities.ActionError as e:
        return str(e)


def wipe_all():
    for p in (people.path(), jobs.path()):
        try:
            os.remove(p)
        except OSError:
            pass
    DMS.clear()
    VIEWS.clear()


def setup_people():
    wipe_all()
    people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})


# --------------------------------------------------------------------------
section("people.json carries a person's tools - default chat + web search")
setup_people()
check("a known person with no `tools` key holds the default", people.tools_for(KNOWN),
      ["web_search"])
check("a config-only guest (no row) holds the default too", people.tools_for(FRIEND),
      ["web_search"])
check("add_person's row has no tools key - five fields, as ever",
      sorted(people.get(KNOWN)), ["by", "display_name", "name", "note", "since"])
people.set_tools(KNOWN, [])
check("a row with tools [] holds nothing beyond chat", people.tools_for(KNOWN), [])
check("...and the key is on disk", people.get(KNOWN)["tools"], [])
people.set_tools(KNOWN, ["web_search", "pc_task"])
check("set_tools keeps only what is grantable", people.get(KNOWN)["tools"], ["web_search"])
with open(people.path(), encoding="utf-8") as f:
    raw = json.load(f)
raw[str(KNOWN)]["tools"] = ["pc_task", "web_search", "read_channel"]
with open(people.path(), "w", encoding="utf-8") as f:
    json.dump(raw, f)
check("a hand-edited row can never widen the lane", people.tools_for(KNOWN), ["web_search"])
check("set_tools for someone with no row and no new row writes nothing",
      (people.set_tools(FRIEND, []), people.get(FRIEND)), (None, None))

section("The registry rows - what the fingerprint pins")
for n in ("grant_tool", "revoke_tool"):
    a = capabilities.REGISTRY[n]
    check(f"{n}: tier 2, always confirmed, not outward, taints",
          (a.tier, a.always_confirm, a.outward, a.taints), (identity.MANAGE, True, False, True))
    check(f"{n} is owner-only - a guest cannot reach it",
          policy.authorize(a, CallContext.guest_dm(FRIEND)).allowed, False)
    check(f"{n} is not reachable by a timer (SYSTEM)",
          policy.authorize(a, CallContext.system()).allowed, False)

section("revoke_tool - a card, then web search is gone; grant_tool gives it back")
setup_people()
r, preview = go("revoke_tool", {"who": "Big Like", "tool": "web_search"})
check("unconfirmed: a card naming the person and the tool",
      (r, preview["summary"]),
      (None, f"Take web search away from **Big Like (@biglike, id {KNOWN})**"))
check("...saying what they'd hold", "They'd hold: chat only." in preview["detail"], True)
check("the card changed nothing", people.tools_for(KNOWN), ["web_search"])
r, _ = go("revoke_tool", {"who": str(KNOWN), "tool": "web search"}, force=True)
check("confirmed: revoked ('web search' with a space is web_search)",
      (r["status"], r["tools"], people.tools_for(KNOWN)), ("tool_revoked", [], []))
check("revoking what they don't hold is refused in words",
      refusal("revoke_tool", {"who": str(KNOWN), "tool": "web_search"}),
      f"Big Like (@biglike, id {KNOWN}) doesn't hold web search.")
r, preview = go("grant_tool", {"who": str(KNOWN), "tool": "web_search"})
check("grant card", preview["summary"], f"Give **Big Like (@biglike, id {KNOWN})** web search")
check("the card changed nothing", people.tools_for(KNOWN), [])
r, _ = go("grant_tool", {"who": str(KNOWN), "tool": "web_search"}, force=True, owner_dm=True)
check("confirmed: granted", (r["status"], people.tools_for(KNOWN)), ("tool_granted",
                                                                    ["web_search"]))
check("granting what they hold is refused",
      refusal("grant_tool", {"who": str(KNOWN), "tool": "web_search"}),
      f"Big Like (@biglike, id {KNOWN}) already holds web search.")

section("Only web_search - anything else refused by name, nothing written")
for t in ("pc_task", "read_channel", "dm_user"):
    check(f"grant {t}: refused",
          refusal("grant_tool", {"who": str(KNOWN), "tool": t}),
          f"The guest brain can't run {t}; web_search is the only tool it has.")
check("revoke of a non-tool: refused the same way",
      refusal("revoke_tool", {"who": str(KNOWN), "tool": "shell"}),
      "The guest brain can't run shell; web_search is the only tool it has.")
check("Caz never borrows a tool", "Caz doesn't borrow tools" in (refusal(
    "grant_tool", {"who": "caz", "tool": "web_search"}) or ""), True)
check("a stranger is refused - add_person first", "add_person makes someone known first"
      in (refusal("grant_tool", {"who": "999000999000999000", "tool": "web_search"}) or ""),
      True)

section("A config-only guest gets a row on their first change")
setup_people()
r, preview = go("revoke_tool", {"who": str(FRIEND), "tool": "web_search"})
check("the card says a row will be written",
      "no people.json row yet" in preview["detail"], True)
check("...and wrote none", people.get(FRIEND), None)
r, _ = go("revoke_tool", {"who": str(FRIEND), "tool": "web_search"}, force=True)
row = people.get(FRIEND)
check("confirmed: a row, holding nothing, by caz",
      (r["row_written"], row["tools"], row["by"]), (True, [], "caz"))
check("...named from control.json (the fixture lists ids only), still a guest",
      (row["name"], identity.is_guest(FRIEND)), (str(FRIEND), True))
r, _ = go("grant_tool", {"who": str(FRIEND), "tool": "web_search"}, force=True)
check("a second change finds the row, writes none new",
      (r["row_written"], people.tools_for(FRIEND)), (False, ["web_search"]))

section("list_people shows each person's tools")
setup_people()
people.set_tools(KNOWN, [])
r, _ = go("list_people")
rows = {x["user_id"]: x for x in r["people"]}
check("revoked: []", rows[KNOWN]["tools"], [])
check("default: web_search", rows[FRIEND]["tools"], ["web_search"])
wipe_all()

section("open_job - a job's tools are a subset of what the person holds")
setup_people()
people.set_tools(KNOWN, [])
check("asking for web search they don't hold: refused, naming the gap and the door",
      refusal("open_job", {"goal": "x", "with": str(KNOWN), "tools": ["web_search"]}),
      f"Big Like (@biglike, id {KNOWN}) doesn't hold web search - grant_tool gives it "
      "first, or open the job without it (tools=[]).")
r, preview = go("open_job", {"goal": "x", "with": str(KNOWN)})
check("no tools given: the default is what they hold - chat only here",
      "Tools: none beyond chat" in preview["detail"], True)
r, preview = go("open_job", {"goal": "x", "with": str(FRIEND)})
check("a default holder's default job gets web search", "Tools: web search" in
      preview["detail"], True)
r, preview = go("open_job", {"goal": "x", "with": "caz", "tools": ["web_search"]})
check("a job with Caz may carry web search", "Tools: web search" in preview["detail"], True)
wipe_all()


# --------------------------------------------------------------------------
# The guest brain, through the REAL bot.on_message (test_jobs' rig): what the
# brain is handed - the tools and the note - is the thing under test.

import tempfile  # noqa: E402

from benham import bot  # noqa: E402
from benham.core import agent  # noqa: E402
from benham.core import confirm  # noqa: E402
from benham.core import conversations  # noqa: E402
from benham.core import jsonio  # noqa: E402
from benham.guest import guest  # noqa: E402

BOT_ID = 752313060970201218


class _GUser:
    def __init__(self, uid, name):
        self.id, self.name, self.display_name = uid, name, name

    def __str__(self):
        return self.name


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _GChannel:
    def __init__(self):
        self.id, self.sent = 4242, []

    def typing(self):
        return _Typing()

    async def send(self, content=None, **kw):
        self.sent.append(content)


class _GMsg:
    def __init__(self, uid, content):
        self.author = _GUser(uid, "friend")
        self.content = content
        self.guild = None
        self.channel = _GChannel()
        self.mentions = []
        self.attachments, self.embeds, self.stickers, self.message_snapshots = [], [], [], []
        self.reference = None
        self.id = 999000222
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Model:
    """The cheap-judge Anthropic client, scripted per call."""

    def __init__(self, reply='{"answers": []}'):
        self.reply, self.calls, self.messages = reply, [], self

    def create(self, **kw):
        self.calls.append(kw)
        text = self.reply(kw) if callable(self.reply) else self.reply
        return _Blk(content=[_Blk(type="text", text=text)],
                    usage=_Blk(input_tokens=1, output_tokens=1))


LOG, OWNER_DMS, BRAIN = [], [], []
REPLY = ["guest reply"]
bot.record_message = lambda m: {"is_self": False, "channel": "dm",
                                "author": str(m.author), "content": m.content}
bot.log = LOG.append
bot.strip_mention = lambda m: m.content


async def _reply_in(channel, text, **kw):
    channel.sent.append(text)
bot.reply_in = _reply_in


async def _owner(text, kind=None):
    OWNER_DMS.append((text, kind))
bot.ask_owner_dm = _owner


QUERIES = []


def _respond(user_id, text, log=None, content=None, note=None, tools=None, info=None,
             **kw):
    BRAIN.append({"user": user_id, "text": text, "note": note, "tools": tools})
    if isinstance(info, dict):
        info["queries"] = list(QUERIES)
    return REPLY[0]
_REAL_RESPOND = guest.respond
guest.respond = _respond
agent.ENABLED = True
confirm.current = lambda: None
confirm.live = lambda: []
_tmp = tempfile.mkdtemp(prefix="benham-jobs-close-test-")
guest.MEMORY_FILE = os.path.join(_tmp, "guest_memory.json")
guest.USAGE_FILE = os.path.join(_tmp, "guest_usage.json")
guest.QUIET_FILE = os.path.join(_tmp, "guest_quiet.json")
guest._quiet = None
guest.COOLDOWN = 0
jsonio.write_json(guest.USAGE_FILE, {})


def greset():
    wipe_all()
    conversations.forget()
    guest.wake(FRIEND)
    guest._last_call.clear()
    LOG.clear()
    OWNER_DMS.clear()
    BRAIN.clear()
    QUERIES.clear()
    REPLY[0] = "guest reply"


def deliver(text, uid=FRIEND, model=None):
    guest._client = model or _Model()
    msg = _GMsg(uid, text)
    asyncio.run(bot.on_message(msg))
    return msg


GOAL = "help them find a half-remembered game"

section("The guest brain honors what the person holds")
greset()
deliver("hey")
check("no row, no job: the default - web search", BRAIN[-1]["tools"], ["web_search"])
people.set_tools(FRIEND, [], new_record={"name": "doom", "by": "caz"})
deliver("hey again")
check("revoked a second ago: the very next turn has no search", BRAIN[-1]["tools"], [])
people.set_tools(FRIEND, ["web_search"])
job = jobs.create({"goal": GOAL, "with": str(FRIEND), "tools": [], "opened": jobs.stamp()})
deliver("so about that game")
check("in a chat-only job, a holder still gets no search on the job's turns",
      BRAIN[-1]["tools"], [])
check("...and the note says so", "Tools you may use for it: none" in BRAIN[-1]["note"], True)
jobs.update(job["id"], tools=["web_search"])
people.set_tools(FRIEND, [])
deliver("any luck?")
check("a job with web search, after revoke_tool: no search, and the note says none",
      (BRAIN[-1]["tools"], "Tools you may use for it: none" in BRAIN[-1]["note"]),
      ([], True))
people.set_tools(FRIEND, ["web_search"])
deliver("ok")
check("granted back: search again, and the note says web search",
      (BRAIN[-1]["tools"], "Tools you may use for it: web search" in BRAIN[-1]["note"]),
      (["web_search"], True))
greset()

section("Several open jobs per person (Caz's call, 2026-11-03 chat)")
greset()
people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})
r1, _ = go("open_job", {"goal": GOAL, "with": str(KNOWN)}, force=True, owner_dm=True)
r, preview = go("open_job", {"goal": "pick a mod for it", "with": str(KNOWN)})
check("a second job's card names the one already open",
      f"Also open with them: {r1['job_id']} \"{GOAL}\"" in preview["detail"], True)
r2, _ = go("open_job", {"goal": "pick a mod for it", "with": str(KNOWN)}, force=True,
           owner_dm=True)
check("confirmed: two open jobs with one person, two openers sent",
      ([j["id"] for j in jobs.open_all_for(KNOWN)], [d[0] for d in DMS]),
      ([r1["job_id"], r2["job_id"]], [KNOWN, KNOWN]))
check("the same goal a third time is refused, naming the job",
      f"already has that job open, {r2['job_id']}" in (refusal(
          "open_job", {"goal": "Pick a mod  for it", "with": str(KNOWN)}) or ""), True)
r, _ = go("list_jobs", {"state": "open"})
check("list_jobs shows both, newest first, same person",
      [(x["id"], x["with"]) for x in r["jobs"]],
      [(r2["job_id"], str(KNOWN)), (r1["job_id"], str(KNOWN))])
check("open count 2", r["open"], 2)
deliver("ok so here's what i remember", uid=KNOWN)
both = [jobs.get(r1["job_id"]), jobs.get(r2["job_id"])]
check("one message touches EVERY open job of theirs",
      [j["turns"] for j in both], [1, 1])
check("...and their last_activity moves together", both[0]["last_activity"],
      both[1]["last_activity"])
want = ("From Benham's side, not from them: Caz asked you to help them with these:\n"
        f"1. {GOAL}\n2. pick a mod for it\nYou opened this chat about them. Tools you may "
        "use for them: web search. Keep helping until they say they're sorted; don't "
        "pass anything to Caz, don't say you recorded anything.")
check("the note lists every open goal, one line each, opened order", BRAIN[-1]["note"], want)
check("the log names both jobs", any(f"is in job {r1['job_id']}, {r2['job_id']}" in line
                                     for line in LOG), True)
jobs.update(r1["job_id"], state="closed")
deliver("still here", uid=KNOWN)
check("one closed: back to the single-job wording for the other",
      BRAIN[-1]["note"].startswith("From Benham's side, not from them: Caz asked you to "
                                   "help them with this: pick a mod for it."), True)
check("...and only the open one is touched",
      (jobs.get(r1["job_id"])["turns"], jobs.get(r2["job_id"])["turns"]), (1, 2))
greset()

section("A job counts the searches run for it")
greset()
a = jobs.create({"goal": GOAL, "with": str(FRIEND), "tools": ["web_search"]})
deliver("hi")
check("a turn with no search counts nothing", jobs.get(a["id"]).get("searches"), None)
QUERIES[:] = ["robot platformer ps2", "ps2 robot game 2003"]
deliver("it had a robot")
j = jobs.get(a["id"])
check("a searched turn adds its queries to the counter", j["searches"], 2)
check("...and logs them", (j["log"][-1]["event"], j["log"][-1]["queries"]),
      ("searched", ["robot platformer ps2", "ps2 robot game 2003"]))
b = jobs.create({"goal": "pick a mod", "with": str(FRIEND), "tools": ["web_search"]})
QUERIES[:] = ["mod"]
deliver("and a mod too")
check("with two open jobs a searched turn is credited to each (an honest overcount)",
      (jobs.get(a["id"])["searches"], jobs.get(b["id"])["searches"]), (3, 1))
QUERIES.clear()
jobs.update(a["id"], state="closed")
jobs.update(b["id"], state="closed")
QUERIES[:] = ["x"]
deliver("no jobs now")
check("no open job: nothing is counted anywhere",
      (jobs.get(a["id"])["searches"], jobs.get(b["id"])["searches"]), (3, 1))
greset()

bot.client = CLIENT     # advance_job_beat runs capabilities.run on the bot's client


def tick():
    """One pass of the real tick_jobs loop body."""
    asyncio.run(bot.tick_jobs.coro())


def to_owner():
    return [c for uid, c in DMS if uid == OWNER]


section("The heads-up to Caz - owed on close, delivered by the tick, once")
greset()
people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})
old = jobs.create({"goal": "stage B rehearsal", "with": "caz", "state": "closed",
                   "closed": jobs.stamp(), "outcome": "rehearsal done"})
tick()
check("a job closed before Stage C (no headsup flag) is never told about", DMS, [])
job = jobs.create({"goal": GOAL, "with": str(KNOWN), "tools": ["web_search"],
                   "opened": "2026-11-03T18:00:00-05:00", "opened_by": "caz",
                   "last_activity": jobs.stamp()})
r, preview = go("close_job", {"job_id": job["id"], "outcome": "found it on my own"})
check("close_job's card says Caz gets the heads-up",
      "Caz gets the heads-up in his DM within a minute" in preview["detail"], True)
r, _ = go("close_job", {"job_id": job["id"], "outcome": "found it on my own"}, force=True,
          owner_dm=True)
j = jobs.get(job["id"])
check("confirmed: closed by hand, heads-up OWED, nothing sent yet",
      (r["status"], j["state"], j["ended"], j["headsup"], DMS),
      ("job_closed", "closed", "hand", "owed", []))
check("jobs.due() names the tell beat", [(x["id"], b) for x, b in jobs.due()],
      [(job["id"], "tell")])
guest._client = _Model("never called")
tick()
want = ("Heads-up - I wrapped up **{id}** with **Big Like (@biglike, id {k})**.\n"
        "You asked: \"{g}\"\n"
        "How it ended: closed by hand (caz) - found it on my own.\n"
        "What I did: 0 messages from them, no searches.\n"
        "What I found: nothing searched.\n"
        "Tools it used: none beyond chat.").format(id=job["id"], k=KNOWN, g=GOAL)
check("the tick sends exactly one DM, to Caz, word for word", to_owner(), [want])
check("...and none to the person", [u for u, _ in DMS if u != OWNER], [])
check("no summary call when no search ran", guest._client.calls, [])
j = jobs.get(job["id"])
check("marked sent, with its message id, logged",
      (j["headsup"], bool(j["headsup_message_id"]), j["log"][-1]["event"]),
      ("sent", True, "told_caz"))
tick()
check("the next tick sends nothing - told once", len(to_owner()), 1)
r, _ = go("advance_job", {"job_id": job["id"]}, system=True)
check("advance_job by hand on a told job: waiting, nothing sent",
      (r["status"], len(to_owner())), ("waiting", 1))
check("advance_job is SYSTEM-reachable (the timer) and owner-reachable, never a guest's",
      (policy.authorize(capabilities.REGISTRY["advance_job"], CallContext.system()).allowed,
       policy.authorize(capabilities.REGISTRY["advance_job"],
                        CallContext.guest_dm(FRIEND)).allowed), (True, False))

section("A searched job's heads-up carries a cheap summary of what was found")
greset()
people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})
job = jobs.create({"goal": GOAL, "with": str(KNOWN), "tools": ["web_search"],
                   "opened": jobs.stamp(), "opened_by": "caz", "last_activity": jobs.stamp()})
guest._remember(guest._key(KNOWN), "it had a robot, ps2", "Could be Ratchet & Clank?")
jobs.update(job["id"], turns=3)
jobs.add_searches(job["id"], ["robot platformer ps2", "ps2 robot game 2003"])
jobs.close(job["id"], "hand", "done", "cli")
summ = _Model("- Ratchet & Clank (2002, PS2) was the match\n2. Also floated: Jak and Daxter"
              "\nThird line\nFourth line dropped")
guest._client = summ
tick()
want = ("Heads-up - I wrapped up **{id}** with **Big Like (@biglike, id {k})**.\n"
        "You asked: \"{g}\"\n"
        "How it ended: closed by hand (cli) - done.\n"
        "What I did: 3 messages from them, 2 web searches.\n"
        "What I found:\n"
        "> Ratchet & Clank (2002, PS2) was the match\n"
        "> Also floated: Jak and Daxter\n"
        "> Third line\n"
        "Tools it used: web search.").format(id=job["id"], k=KNOWN, g=GOAL)
check("the heads-up, word for word: three lines at most, bullets stripped",
      to_owner()[0], want)
prompt = summ.calls[0]["messages"][0]["content"]
check("the summary call is the cheap model, and sees the goal, the queries and the chat",
      (summ.calls[0]["model"], GOAL in prompt, "robot platformer ps2" in prompt,
       "it had a robot, ps2" in prompt), ("claude-haiku-4-5", True, True, True))
check("...with no tool", "tools" in summ.calls[0], False)

section("Under the heads-up: one keep/remove card per tool the job used")
want_card = (f"Keep web search for **Big Like (@biglike, id {KNOWN})**, or remove it?\n"
             "-# Remove takes it away from their next message; Keep leaves it. No answer "
             f"= kept. Any time: `benham.py do revoke_tool who={KNOWN} tool=web_search`")
check("a second DM to Caz, the card, word for word", to_owner()[1:], [want_card])
check("...carrying the Keep / Remove buttons",
      [(type(v).__name__, [c.label for c in v.children]) for _, _, v in VIEWS],
      [("KeepToolView", ["Keep", "Remove"])])
CARD = VIEWS[-1][2]


class _Resp:
    def __init__(self):
        self.edited, self.sent = [], []

    async def edit_message(self, content=None, view=None):
        self.edited.append(content)

    async def send_message(self, content=None, ephemeral=False):
        self.sent.append(content)

    async def defer(self):
        pass


class _Inter:
    def __init__(self, uid, content="card"):
        self.user = _GUser(uid, "clicker")
        self.response = _Resp()
        self.message = _Blk(content=content)


async def click(view, uid, keep):
    inter = _Inter(uid)
    await view._click(inter, keep)
    return inter

i = asyncio.run(click(CARD, FRIEND, False))
check("a click by anyone but Caz does nothing and is refused privately",
      (people.tools_for(KNOWN), bool(i.response.sent), CARD.decided), (["web_search"], True,
                                                                       False))
i = asyncio.run(click(CARD, OWNER, False))
check("Caz taps Remove: revoke_tool runs - web search gone from their next message",
      people.tools_for(KNOWN), [])
check("...the card says removed and goes dead", (i.response.edited, CARD.decided),
      (["card\n\n_🗑️ removed_"], True))
asyncio.run(click(CARD, OWNER, True))
check("a second tap on a dead card changes nothing", people.tools_for(KNOWN), [])

greset()
people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})
job = jobs.create({"goal": GOAL, "with": str(KNOWN), "tools": ["web_search"],
                   "opened": jobs.stamp()})
jobs.add_searches(job["id"], ["q"])
jobs.close(job["id"], "hand", "done", "cli")
guest._client = _Model("Found: X")
tick()
asyncio.run(click(VIEWS[-1][2], OWNER, True))
check("Caz taps Keep: nothing changes", people.tools_for(KNOWN), ["web_search"])
greset()
people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})
job = jobs.create({"goal": GOAL, "with": str(KNOWN), "tools": ["web_search"],
                   "opened": jobs.stamp()})
jobs.close(job["id"], "hand", "done", "cli")
tick()
check("a job that never searched: the heads-up, and no card", (len(to_owner()), VIEWS),
      (1, []))
greset()
people.add(KNOWN, {"name": "biglike", "display_name": "Big Like", "by": "caz"})
job = jobs.create({"goal": GOAL, "with": str(KNOWN), "tools": ["web_search"],
                   "opened": jobs.stamp()})
jobs.add_searches(job["id"], ["q"])
people.set_tools(KNOWN, [])
jobs.close(job["id"], "hand", "done", "cli")
guest._client = _Model("Found: X")
tick()
check("a tool already revoked by the close: no card for it", (len(to_owner()), VIEWS),
      (1, []))


class _Boom:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise RuntimeError("api down")


greset()
job = jobs.create({"goal": "x", "with": "caz", "tools": ["web_search"],
                   "opened": jobs.stamp()})
jobs.add_searches(job["id"], ["q"])
jobs.close(job["id"], "hand", "done", "caz")
guest._client = _Boom()
tick()
check("a summary that fails still sends the heads-up, saying so",
      "(I couldn't summarize it - RuntimeError" in (to_owner() or [""])[0], True)
check("...and a job with Caz says 'with you'", "with you." in (to_owner() or [""])[0], True)
greset()

section("guest.respond itself - the list can only take away")


class _Brain:
    def __init__(self):
        self.calls, self.messages = [], self

    def create(self, **kw):
        self.calls.append(kw)
        return _Blk(content=[_Blk(type="text", text="sure")], stop_reason="end_turn",
                    usage=_Blk(input_tokens=1, output_tokens=1))


def real_turn(**kw):
    brain = _Brain()
    guest._client = brain
    _REAL_RESPOND(FRIEND, "hi", None, **kw)
    return [t.get("name") for t in brain.calls[-1].get("tools") or []]


saved_ws = guest.WEB_SEARCH
guest.WEB_SEARCH = True
check("tools=None: the lane default, web search", real_turn(), ["web_search"])
check("tools=['web_search']: web search", real_turn(tools=["web_search"]), ["web_search"])
check("tools=[]: no tool at all", real_turn(tools=[]), [])
check("tools=['pc_task']: an unknown name adds nothing", real_turn(tools=["pc_task"]), [])
guest.WEB_SEARCH = False
check("guest.web_search off in config wins over a holder", real_turn(tools=["web_search"]), [])
guest.WEB_SEARCH = saved_ws


class _SearchBrain(_Brain):
    def create(self, **kw):
        self.calls.append(kw)
        return _Blk(content=[_Blk(type="server_tool_use", name="web_search",
                                  input={"query": "robot game"}),
                             _Blk(type="text", text="found it")],
                    stop_reason="end_turn", usage=_Blk(input_tokens=1, output_tokens=1))


guest._client = _SearchBrain()
_info = {}
_REAL_RESPOND(FRIEND, "hi", None, info=_info)
check("respond fills info['queries'] with the turn's searches", _info, {"queries": ["robot game"]})
guest._client = _Brain()
_info = {"stale": 1}
_REAL_RESPOND(FRIEND, "hi", None, info=_info)
check("...and an empty list on a turn with none", _info.get("queries"), [])
greset()


# --------------------------------------------------------------------------
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASS")
