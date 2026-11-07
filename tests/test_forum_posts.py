"""
test_forum_posts.py - forum post tags and pinning, offline (INTENT 60, Stage 8 follow-up).

Two pieces give a forum's post screens to a prompt: create_thread's `tags=` (a
new post goes up already tagged) and edit_forum_post (an existing post's tags
replaced or cleared, the post pinned to the top of its forum or unpinned). What
is pinned:

  THE POSTURE IS PIN_MESSAGE'S. edit_forum_post is tier 2 (MANAGE), not outward,
  not always confirmed, not posts - it adds no words and changes nobody's access
  - and it taints, because its result echoes tag and post names other people
  wrote. A guest reaches it from nowhere.

  A NAME IS THE FORUM'S TAG OR IT IS REFUSED. Names match case-insensitively
  after NFKC; a numeric tag id is accepted; an unknown name is refused with the
  forum's own tag list, an ambiguous one with both ids, never a guess; repeats
  collapse; a sixth tag is refused before Discord is asked. Every refusal is an
  ActionError (one plain sentence), never a ValueError.

  ONE PINNED POST PER FORUM, CHECKED HERE. Whether Discord refuses a second pin
  or silently moves it is not in discord.py, so the verb looks at the forum's
  cached posts and names the one already pinned.

  AN ARCHIVED POST IS UNARCHIVED IN THE SAME EDIT. Discord refuses any edit to an
  archived thread, and forum posts auto-archive; the fake below refuses the same
  way, so a missing archived=False would fail here.

The forum and its posts are subclasses of discord.ForumChannel and
discord.Thread (create_thread and edit_forum_post branch on isinstance), with
only the properties the verbs read overridden; the tags are real
discord.ForumTag objects. Everything runs through capabilities.run as the owner
in a DM.

    python test_forum_posts.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - must precede every benham import

import asyncio
import sys
import types

import discord

from benham.core import capabilities, identity, policy

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!a}\n        want {want!a}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# ======================================================================= stubs

OWNER = 273967061619965952
GUILD = 736988645562646619          # the Testing Server (on the fixture's lists)
FORUM, TEXT = 5100, 5200
POST_A, POST_B, POST_C, TEXT_THREAD = 5101, 5102, 5103, 5201
TAG_MOD, TAG_TOOL, TAG_LIB, TAG_ART, TAG_QOL, TAG_FUN = 61, 62, 63, 64, 65, 66


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "fake"


def tag(tid, name):
    t = discord.ForumTag(name=name)
    t.id = tid
    return t


class FakeForum(discord.ForumChannel):
    """A ForumChannel with only what the verbs read. Its posts are its cache."""

    def __init__(self, cid, name, tags, guild, require_tag=False):
        self.id = cid
        self.name = name
        self.guild = guild
        self._tags = list(tags)
        self._require = require_tag
        self._posts = []
        self.created = []           # every create_thread call's kwargs

    def __repr__(self):
        return f"<FakeForum {self.name}>"

    @property
    def available_tags(self):
        return list(self._tags)

    def get_tag(self, tid):
        return next((t for t in self._tags if t.id == tid), None)

    @property
    def flags(self):
        return types.SimpleNamespace(require_tag=self._require)

    @property
    def threads(self):
        return list(self._posts)

    async def create_thread(self, **kw):
        self.created.append(kw)
        post = FakePost(9000 + len(self.created), kw["name"], self,
                        tags=kw.get("applied_tags", ()))
        return types.SimpleNamespace(thread=post, message=None)


class FakePost(discord.Thread):
    """A Thread. edit() behaves like Discord: refuses an archived thread unless the
    same call unarchives it, returns a NEW thread, and the cache catches up."""

    def __init__(self, tid, name, parent, tags=(), pinned=False, archived=False,
                 cached_parent=True):
        self.id = tid
        self.name = name
        self.guild = parent.guild
        self.parent_id = parent.id
        self.archived = archived
        self._parent = parent
        self._cached_parent = cached_parent
        self._tags = list(tags)
        self._pinned = pinned
        self.edits = []

    def __repr__(self):
        return f"<FakePost {self.name}>"

    @property
    def parent(self):
        return self._parent if self._cached_parent else None

    @property
    def applied_tags(self):
        return list(self._tags)

    @property
    def flags(self):
        return types.SimpleNamespace(pinned=self._pinned)

    async def edit(self, **kw):
        self.edits.append(kw)
        if self.archived and kw.get("archived") is not False:
            raise discord.HTTPException(_Resp(400), "Thread is archived")
        for t in kw.get("applied_tags", []):
            assert t.id != 0, "a hand-built ForumTag reached Discord"
        if "applied_tags" in kw:
            self._tags = list(kw["applied_tags"])
        if "pinned" in kw:
            self._pinned = bool(kw["pinned"])
        if "archived" in kw:
            self.archived = kw["archived"]
        return FakePost(self.id, self.name, self._parent, tags=self._tags,
                        pinned=self._pinned, archived=self.archived)


class _Client:
    def __init__(self, *channels, hidden=()):
        self.channels = {c.id: c for c in channels}
        self.hidden = {c.id: c for c in hidden}     # resolvable only by fetch

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    def get_guild(self, gid):
        return GUILD_OBJ if int(gid) == GUILD else None

    def get_user(self, uid):
        return None

    async def fetch_channel(self, cid):
        ch = self.channels.get(int(cid)) or self.hidden.get(int(cid))
        if ch is None:
            raise discord.NotFound(_Resp(404), "Unknown Channel")
        return ch


GUILD_OBJ = types.SimpleNamespace(id=GUILD, name="Testing")


def world(require_tag=False, pinned_b=False, archived_a=False, cached_parent=True):
    """(client, forum, post A, post B) - a forum with six tags and two posts."""
    forum = FakeForum(FORUM, "v2-mod-votes", [
        tag(TAG_MOD, "Mod"), tag(TAG_TOOL, "Tool"), tag(TAG_LIB, "Library"),
        tag(TAG_ART, "Art"), tag(TAG_QOL, "QoL"), tag(TAG_FUN, "Ｆｕｎ")],   # fullwidth: NFKC
        GUILD_OBJ, require_tag=require_tag)
    a = FakePost(POST_A, "Create: Aeronautics", forum, tags=[forum.get_tag(TAG_MOD)],
                 archived=archived_a, cached_parent=cached_parent)
    b = FakePost(POST_B, "How voting works", forum, pinned=pinned_b)
    forum._posts = [a, b]
    text = types.SimpleNamespace(id=TEXT, name="general", guild=GUILD_OBJ, created=[])

    async def _text_create(**kw):
        text.created.append(kw)
        return FakePost(TEXT_THREAD, kw["name"], forum)
    text.create_thread = _text_create
    tthread = types.SimpleNamespace(id=TEXT_THREAD, name="a chat thread", guild=GUILD_OBJ)
    hidden = (forum,) if not cached_parent else ()
    chans = (a, b, text, tthread) + (() if not cached_parent else (forum,))
    return _Client(*chans, hidden=hidden), forum, a, b


def go(client, name, params):
    return asyncio.run(capabilities.run(
        client, lambda *_: None, name, params, actor_id=OWNER,
        call_ctx=policy.CallContext.owner_dm(OWNER, channel_id=999)))


def refusal(client, name, params):
    """The refusal's text, or None. A ValueError is NOT caught: it fails the file."""
    try:
        go(client, name, params)
        return None
    except capabilities.ActionError as e:
        return str(e)


def one_sentence(msg):
    return bool(msg) and "\n" not in msg and "Traceback" not in msg


# ================================================================ registration
section("edit_forum_post - registered with pin_message's posture")
a = capabilities.REGISTRY["edit_forum_post"]
check("tier 2 (MANAGE)", a.tier, identity.MANAGE)
check("not outward, not always confirmed, not posts, no dry-run",
      (a.outward, a.always_confirm, a.posts, a.needs_confirm), (False, False, False, False))
check("taints (tag and post names are other people's text)", a.taints, True)
check("no guild param needed (it takes thread_id, like archive_thread)", a.needs_guild, False)
check("params: thread_id required, tags and pinned optional",
      ({k: v.get("type") for k, v in a.params.items()},
       [k for k, v in a.params.items() if v.get("required")]),
      ({"thread_id": "int", "tags": "str", "pinned": "bool"}, ["thread_id"]))
check("never a guest capability, and a guest is refused",
      (a.guest, policy.authorize(a, policy.CallContext.guest_dm(111, 5)).allowed),
      (False, False))
check("create_thread keeps its row (MANAGE, posts, not outward) and gains tags=",
      (capabilities.REGISTRY["create_thread"].tier, capabilities.REGISTRY["create_thread"].posts,
       capabilities.REGISTRY["create_thread"].outward,
       capabilities.REGISTRY["create_thread"].params["tags"]["type"]),
      (identity.MANAGE, True, False, "str"))

# =============================================================== the resolver
section("_resolve_forum_tags - names, ids, case, NFKC, dupes, the cap")
_, forum, _, _ = world()
R = capabilities._resolve_forum_tags
check("case-insensitive names, spaces trimmed, in the order given",
      [t.id for t in R(forum, "  mod ,TOOL,library ")], [TAG_MOD, TAG_TOOL, TAG_LIB])
check("NFKC: plain 'fun' finds the fullwidth tag", [t.id for t in R(forum, "fun")], [TAG_FUN])
check("a numeric id is accepted", [t.id for t in R(forum, str(TAG_ART))], [TAG_ART])
check("a name and its own id collapse to one tag",
      [t.id for t in R(forum, f"Art, {TAG_ART}, art")], [TAG_ART])
check("blank or only commas -> no tags", (R(forum, ""), R(forum, " , ,")), ([], []))
check("five is allowed", len(R(forum, "mod,tool,library,art,qol")), 5)
for raw, want_bits, label in [
        ("mod,tool,library,art,qol,fun", ["at most 5", "got 6"], "a sixth tag"),
        ("Mod, Bug", ["has no tag 'Bug'", "Mod, Tool, Library, Art, QoL"], "an unknown name lists the forum's tags"),
        ("424242", ["has no tag '424242'"], "an id from no tag here")]:
    try:
        R(forum, raw)
        m = None
    except capabilities.ActionError as e:
        m = str(e)
    check(f"refused: {label}", m is not None and all(b in m for b in want_bits) and one_sentence(m), True)
dup = FakeForum(FORUM, "dupes", [tag(71, "Mod"), tag(72, "MOD")], GUILD_OBJ)
try:
    R(dup, "mod")
    m = None
except capabilities.ActionError as e:
    m = str(e)
check("two tags that fold to one name: ambiguous, both ids named, never a guess",
      m is not None and "ambiguous" in m and "(71)" in m and "(72)" in m, True)
check("...and the id picks the one meant", [t.id for t in R(dup, "72")], [72])

# ================================================================ create_thread
section("create_thread tags= - the new post goes up tagged")
cl, forum, _, _ = world()
result, preview = go(cl, "create_thread", {"channel_id": FORUM, "name": "Create: Steam n Rails",
                                           "content": "Kind: Mod", "tags": "mod, qol"})
check("created, no preview (not always confirmed)", (result["status"], preview), ("created", None))
check("the forum's own ForumTag objects reach Discord as applied_tags",
      [t.id for t in forum.created[-1]["applied_tags"]], [TAG_MOD, TAG_QOL])
check("the result names the tags", result["tags"], ["Mod", "QoL"])
result, _ = go(cl, "create_thread", {"channel_id": FORUM, "name": "Untagged", "content": "x"})
check("no tags= -> applied_tags not sent at all, and tags: [] in the result",
      ("applied_tags" in forum.created[-1], result["tags"]), (False, []))
result, _ = go(cl, "create_thread", {"channel_id": FORUM, "name": "Blank", "content": "x", "tags": ""})
check("tags='' is the same as none", ("applied_tags" in forum.created[-1], result["tags"]), (False, []))
n = len(forum.created)
m = refusal(cl, "create_thread", {"channel_id": FORUM, "name": "Bad", "content": "x", "tags": "Bug"})
check("an unknown tag: refused, one sentence, nothing created",
      (m is not None and "has no tag 'Bug'" in m and one_sentence(m), len(forum.created)), (True, n))
m = refusal(cl, "create_thread", {"channel_id": TEXT, "name": "chat", "tags": "Mod"})
check("tags= outside a forum: refused (ActionError), nothing created",
      (m is not None and "forum-only" in m, cl.get_channel(TEXT).created), (True, []))
result, _ = go(cl, "create_thread", {"channel_id": TEXT, "name": "chat"})
check("a normal thread without tags= is untouched (no tags key)",
      ("tags" in result, "applied_tags" in cl.get_channel(TEXT).created[-1]), (False, False))

cl, forum, _, _ = world(require_tag=True)
m = refusal(cl, "create_thread", {"channel_id": FORUM, "name": "Needs one", "content": "x"})
check("a forum that requires a tag, none given: refused, names the tags, nothing created",
      (m is not None and "requires a tag" in m and "Mod" in m and one_sentence(m), forum.created),
      (True, []))
result, _ = go(cl, "create_thread", {"channel_id": FORUM, "name": "Has one", "content": "x", "tags": "Art"})
check("...and with one it goes up", result["tags"], ["Art"])

# ============================================================== edit_forum_post
section("edit_forum_post - tags replaced, cleared; before and after")
cl, forum, pa, pb = world()
result, preview = go(cl, "edit_forum_post", {"thread_id": POST_A, "tags": "tool, library"})
check("edited, no preview", (result["status"], preview), ("edited", None))
check("tags REPLACE the set (Mod gone), before and after reported",
      (result["before"], result["after"]),
      ({"tags": ["Mod"], "pinned": False}, {"tags": ["Tool", "Library"], "pinned": False}))
check("only applied_tags sent (pinned untouched, a live post not unarchived)",
      sorted(k for k in pa.edits[-1] if k != "reason"), ["applied_tags"])
check("the result names the post and its forum, unarchived false",
      (result["thread_id"], result["name"], result["forum"], result["unarchived"]),
      (POST_A, "Create: Aeronautics", "v2-mod-votes", False))
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_A, "tags": ""})
check("tags='' clears them", (result["after"]["tags"], pa.edits[-1]["applied_tags"]), ([], []))

section("edit_forum_post - pinning, and the one-pinned-post check")
cl, forum, pa, pb = world()
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_B, "pinned": True})
check("pinned=true pins the post", (result["before"]["pinned"], result["after"]["pinned"],
                                    pb.edits[-1]["pinned"]), (False, True, True))
check("only pinned sent (its tags untouched)",
      sorted(k for k in pb.edits[-1] if k != "reason"), ["pinned"])
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_B, "pinned": "true"})
check("pinning the post that is already pinned is fine (string 'true' coerced)",
      result["after"]["pinned"], True)
n = len(pa.edits)
m = refusal(cl, "edit_forum_post", {"thread_id": POST_A, "pinned": True})
check("a second pin in the same forum: refused, names the pinned post, nothing sent",
      (m is not None and "already has a pinned post" in m and "How voting works" in m
       and str(POST_B) in m and "pinned=false" in m and one_sentence(m), len(pa.edits)),
      (True, n))
m = refusal(cl, "edit_forum_post", {"thread_id": POST_A, "tags": "Art", "pinned": True})
check("...and with tags in the same call, the tags are not half-applied",
      (m is not None, len(pa.edits), [t.name for t in pa.applied_tags]), (True, n, ["Mod"]))
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_A, "pinned": False})
check("pinned=false on another post is never refused", result["after"]["pinned"], False)
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_B, "pinned": False})
check("unpin the pinned one...", result["after"]["pinned"], False)
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_A, "pinned": True, "tags": "mod, fun"})
check("...then the other can be pinned, tags in the same edit",
      (result["after"], sorted(k for k in pa.edits[-1] if k != "reason")),
      ({"tags": ["Mod", "Ｆｕｎ"], "pinned": True}, ["applied_tags", "pinned"]))

section("edit_forum_post - an archived post is unarchived in the same edit")
cl, forum, pa, pb = world(archived_a=True)
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_A, "tags": "Art"})
check("archived=False rides along, the edit lands, unarchived reported",
      (pa.edits[-1].get("archived"), result["after"]["tags"], result["unarchived"]),
      (False, ["Art"], True))

section("edit_forum_post - a post whose forum is not cached is still found")
cl, forum, pa, pb = world(cached_parent=False)
result, _ = go(cl, "edit_forum_post", {"thread_id": POST_A, "tags": "QoL"})
check("the forum is fetched by parent_id and the tag resolves", result["after"]["tags"], ["QoL"])

section("edit_forum_post - refused at once, one sentence each, nothing sent")
cl, forum, pa, pb = world()
for params, bits, label in [
        ({"thread_id": POST_A}, ["nothing to change"], "neither tags nor pinned"),
        ({"thread_id": TEXT_THREAD, "pinned": True}, ["is not a forum post", "pin_message"],
         "a thread outside a forum"),
        ({"thread_id": TEXT, "tags": "Mod"}, ["is not a forum post"], "a plain channel"),
        ({"thread_id": FORUM, "tags": "Mod"}, ["is not a forum post"], "the forum itself"),
        ({"thread_id": 404404, "tags": "Mod"}, ["no channel with id 404404"], "an id that is gone"),
        ({"thread_id": POST_A, "tags": "mod,tool,library,art,qol,fun"}, ["at most 5"], "six tags"),
        ({"thread_id": POST_A, "tags": "Bug"}, ["has no tag 'Bug'"], "an unknown tag")]:
    m = refusal(cl, "edit_forum_post", params)
    check(f"refused: {label}", m is not None and all(b in m for b in bits) and one_sentence(m), True)
check("...and not one edit reached Discord", (pa.edits, pb.edits), ([], []))
m = refusal(cl, "edit_forum_post", {"thread_id": POST_A, "tags": "Mod", "colour": "red"})
check("an unknown parameter is refused by validate", m is not None and "unknown parameter" in m, True)


# ===================================================================== the end
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
