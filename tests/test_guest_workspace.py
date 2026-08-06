"""
test_guest_workspace.py - the fence around guest files. (Stage 4)

What is proven, in order: names that should die, die (the pathsafe corpus,
applied to workspace operations rather than to the sanitiser in isolation);
guests are structurally isolated from each other and from Tyler's disk; commons
is readable and never writable; the three quotas hold including under a racing
pair of writers; runnable suffixes are refused on BOTH inbound paths; ws_import
takes attachments only from the guest's own messages in their own DM; and the
outbound re-verification refuses everything that is not, right now, a real file
in that guest's own folder.

    python test_guest_workspace.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package and bot.py live.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio
import os
import shutil
import sys
import tempfile
import threading

from benham.core import capabilities
from benham.core import identity
from benham.core import policy
from benham.core.policy import Origin

TYLER = 273967061619965952
DOOM = 777000777000777000
OTHER = 888000888000888000

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


from benham.guest import guest_workspace as ws  # noqa: E402

# Point the whole module at a scratch tree - tests never touch the real one.
_tmp = tempfile.mkdtemp(prefix="benham-ws-test-")
ws.ROOT = os.path.join(_tmp, "guest_work")
ws.COMMONS = os.path.join(ws.ROOT, "commons")
os.makedirs(ws.COMMONS, exist_ok=True)
with open(os.path.join(ws.COMMONS, "welcome.txt"), "w", encoding="utf-8") as f:
    f.write("hello guests")
with open(os.path.join(ws.COMMONS, "mods.jar"), "wb") as f:
    f.write(b"\x00" * 64)


def wipe(actor):
    shutil.rmtree(ws.guest_root(actor), ignore_errors=True)


def err(fn, *args, **kw):
    try:
        fn(*args, **kw)
        return None
    except ws.WorkspaceError as e:
        return str(e)


# --------------------------------------------------------------------------
section("Names that should die, die")

wipe(DOOM)
ws.write_file(DOOM, "notes.txt", "hi")
CORPUS = ["..", "..\\evil.txt", "../evil.txt", "a/b.txt", "a\\b.txt",
          "C:evil.txt", "C:\\abs\\evil.txt", "//unc/share/x.txt",
          "CON", "con.txt", "NUL.log", "com1.csv",
          "trailing.", "trailing ", " . ", "", "nul\x00byte.txt"]
bad = [n for n in CORPUS if err(ws.write_file, DOOM, n, "x") is None]
check("every corpus name is refused for write", bad, [])
bad = [n for n in CORPUS if err(ws.read_file, DOOM, n) is None]
check("...and for read", bad, [])
bad = [n for n in CORPUS if err(ws.delete_file, DOOM, n) is None]
check("...and for delete", bad, [])
bad = [n for n in CORPUS if err(ws.attach_path, DOOM, n) is None]
check("...and for attach", bad, [])
check("a mismatch-refusal suggests the safe spelling",
      "Did you mean" in err(ws.write_file, DOOM, "my file?.txt", "x"), True)

# Imports sanitise instead (uploader-controlled name), and say so.
rec = ws.import_bytes(DOOM, "..\\..\\Windows\\evil.dll.txt", b"x")
check("import sanitises a hostile name", rec["name"], "evil.dll.txt")
check("...and reports what it changed",
      rec["sanitised_from"], "..\\..\\Windows\\evil.dll.txt")


# --------------------------------------------------------------------------
section("Isolation is structural")

wipe(DOOM); wipe(OTHER)
ws.write_file(DOOM, "mine.txt", "doom's")
ws.write_file(OTHER, "theirs.txt", "other's")
check("A cannot read B's file by name",
      err(ws.read_file, DOOM, "theirs.txt") is not None, True)
check("A's listing shows only A's files",
      [f["name"] for f in ws.list_files(DOOM)["mine"]], ["mine.txt"])
check("roots differ per guest",
      ws.guest_root(DOOM) == ws.guest_root(OTHER), False)
check("a non-numeric actor id gets no root at all",
      err(ws.guest_root, "..") is not None, True)


# --------------------------------------------------------------------------
section("Commons: readable, never writable")

check("commons file is readable",
      ws.read_file(DOOM, "welcome.txt", area="commons")["text"], "hello guests")
check("commons binary reports metadata, offers ws_attach... no wait - it is not attachable either",
      "note" in ws.read_file(DOOM, "mods.jar", area="commons"), True)
check("an unknown area is refused",
      err(ws.read_file, DOOM, "welcome.txt", area="everyone") is not None, True)
check("commons is not deletable (delete resolves against OWN root only)",
      err(ws.delete_file, DOOM, "welcome.txt") is not None, True)
check("commons is not attachable (attach resolves against OWN root only)",
      err(ws.attach_path, DOOM, "welcome.txt") is not None, True)


# --------------------------------------------------------------------------
section("Quotas: per-file, per-folder, file-count, and under a race")

wipe(DOOM)
_pf, _pg, _mf = ws.PER_FILE_BYTES, ws.PER_GUEST_BYTES, ws.MAX_FILES
ws.PER_FILE_BYTES, ws.PER_GUEST_BYTES, ws.MAX_FILES = 100, 250, 3
try:
    check("per-file cap refuses", "per-file cap" in err(ws.write_file, DOOM, "big.txt", "x" * 101), True)
    ws.write_file(DOOM, "a.txt", "x" * 100)
    ws.write_file(DOOM, "b.txt", "x" * 100)
    check("folder cap refuses the write that would pass it",
          "workspace cap" in err(ws.write_file, DOOM, "c.txt", "x" * 60), True)
    ws.write_file(DOOM, "c.txt", "x" * 40)
    check("file-count cap refuses a fourth file",
          "at most" in err(ws.write_file, DOOM, "d.txt", "x"), True)
    check("overwriting does not double-count",
          ws.write_file(DOOM, "c.txt", "x" * 45)["replaced"], True)

    wipe(DOOM)
    ws.MAX_FILES = 1
    hits, misses = [], []

    def racer(i):
        e = err(ws.write_file, DOOM, f"slot{i}.txt", "x")
        (misses if e else hits).append(i)

    ts = [threading.Thread(target=racer, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("eight racers, one file slot: exactly one wins", len(hits), 1)
    check("...and the folder really holds one file",
          len(ws.list_files(DOOM)["mine"]), 1)
finally:
    ws.PER_FILE_BYTES, ws.PER_GUEST_BYTES, ws.MAX_FILES = _pf, _pg, _mf


# --------------------------------------------------------------------------
section("No runnable file, however it arrives")

wipe(DOOM)
for name in ("run.exe", "go.bat", "x.cmd", "s.ps1", "i.msi", "v.vbs"):
    check(f"write {name} refused", "runnable" in (err(ws.write_file, DOOM, name, "x") or ""), True)
    check(f"import {name} refused", "runnable" in (err(ws.import_bytes, DOOM, name, b"x") or ""), True)
check("write of .txt still fine", ws.write_file(DOOM, "fine.txt", "ok")["status"], "written")


# --------------------------------------------------------------------------
section("ws_import: only YOUR OWN messages, in THIS DM, no URL anywhere")

check("ws_import declares no channel parameter",
      "channel_id" in capabilities.REGISTRY["ws_import"].params, False)
check("...and no url parameter",
      any("url" in k for k in capabilities.REGISTRY["ws_import"].params), False)


class _Att:
    def __init__(self, filename, data, size=None):
        self.filename, self._data = filename, data
        self.size = len(data) if size is None else size
        self.content_type = "text/plain"
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self._data


class _Author:
    def __init__(self, uid):
        self.id = uid


class _Msg:
    def __init__(self, author_id, atts):
        self.author = _Author(author_id)
        self.attachments = atts


class _Chan:
    def __init__(self, msgs):
        self.id = 111
        self._msgs = msgs

    async def fetch_message(self, mid):
        import discord
        if mid in self._msgs:
            return self._msgs[mid]
        raise discord.NotFound(None, None) if hasattr(discord.NotFound, "__init__") else None


class _Client:
    def __init__(self, chan):
        self._chan = chan

    def get_channel(self, cid):
        return self._chan if int(cid) == 111 else None


def run_import(msgs, params, actor=DOOM, channel_id=111, message_id=901):
    chan = _Chan(msgs)
    ctx = capabilities.Ctx(_Client(chan), lambda *_: None, actor_id=actor,
                           source_channel_id=channel_id,
                           source_message_id=message_id)
    handler = capabilities.REGISTRY["ws_import"].handler
    try:
        return asyncio.run(handler(ctx, params)), None
    except capabilities.ActionError as e:
        return None, str(e)


wipe(DOOM)
own = _Msg(DOOM, [_Att("notes from doom.txt", b"hello"),
                  _Att("huge.bin", b"", size=10**9)])
tylers = _Msg(TYLER, [_Att("secrets.txt", b"nope")])

out, e = run_import({901: own}, {})
check("defaults to the current message and imports", e is None, True)
check("the file landed, sanitised name intact",
      [f["name"] for f in ws.list_files(DOOM)["mine"]], ["notes from doom.txt"])
check("the oversize attachment was skipped from METADATA",
      "skipped" in out["imported"][1], True)
check("...without spending its bandwidth", own.attachments[1].reads, 0)

out, e = run_import({901: own, 902: tylers}, {"message_id": 902})
check("someone else's message is refused", "YOUR OWN" in (e or ""), True)

out, e = run_import({901: own}, {"index": 5})
check("an out-of-range index is refused", "out of range" in (e or ""), True)

ctx = capabilities.Ctx(None, lambda *_: None, actor_id=DOOM,
                       source_channel_id=None, source_message_id=None)
try:
    asyncio.run(capabilities.REGISTRY["ws_import"].handler(ctx, {}))
    check("no source message on this surface -> refused", "ran", "refused")
except capabilities.ActionError:
    check("no source message on this surface -> refused", "refused", "refused")


# --------------------------------------------------------------------------
section("Outbound: verify_outgoing trusts nothing")

wipe(DOOM)
ws.write_file(DOOM, "give.txt", "here")
good = ws.attach_path(DOOM, "give.txt")
check("a real own file verifies to its realpath",
      ws.verify_outgoing(DOOM, good), os.path.realpath(good))
check("another guest's file does not verify",
      ws.verify_outgoing(OTHER, good), None)
check("a commons path does not verify",
      ws.verify_outgoing(DOOM, os.path.join(ws.COMMONS, "welcome.txt")), None)
check("an absolute path elsewhere does not verify",
      ws.verify_outgoing(DOOM, os.path.abspath(__file__)), None)
ws.delete_file(DOOM, "give.txt")
check("a deleted file stops verifying", ws.verify_outgoing(DOOM, good), None)


# --------------------------------------------------------------------------
section("Registry: the six, exactly, with exactly the guest shape")

WS = {"ws_list", "ws_read", "ws_write", "ws_delete", "ws_import", "ws_attach",
      "read_shared_channel"}
flagged = {n for n, a in capabilities.REGISTRY.items() if a.guest}
check("the guest-flagged capabilities are exactly the six", flagged, WS)
for n in sorted(WS):
    a = capabilities.REGISTRY[n]
    check(f"{n}: guest DM origin only", a.origins, frozenset({Origin.GUEST_DM}))
    check(f"{n}: taints, not outward, no confirm, no posts",
          (a.taints, a.outward, a.needs_confirm, a.posts),
          (True, False, False, False))

identity.GUEST = {"enabled": True, "mode": "workspace", "ids": [DOOM],
                  "capabilities": sorted(WS)}
identity.GUEST_IDS = {DOOM}
check("granting them all in config grants exactly them",
      set(capabilities.guest_grants()), WS)
gctx = policy.CallContext.guest_dm(DOOM, 111)
check("caller rules allow a granted ws capability",
      policy.authorize(capabilities.REGISTRY["ws_write"], gctx).allowed, True)
check("target rules allow it too",
      policy.authorize_target(capabilities.REGISTRY["ws_write"], gctx).allowed, True)
check("the owner still cannot reach it",
      policy.authorize(capabilities.REGISTRY["ws_write"],
                       policy.CallContext.owner_dm(TYLER, 111)).rule,
      "origin_allowed")
check("pc_task is still unreachable beside them",
      policy.authorize(capabilities.REGISTRY["pc_task"], gctx).allowed, False)


# --------------------------------------------------------------------------
section("read_shared_channel: two decisions, and the refusal names nothing")

SHARED = 1531594114099970121      # Testing #benham-beta
# A channel Benham CAN see and a guest must never read: Chillbar's rules
# channel. Deliberately a real id from a real friend server - the case this
# capability exists to refuse is not a made-up number.
SECRET = 1491485791661330576


class _RCMsg:
    def __init__(self, text):
        import datetime
        self.id = 5
        self.created_at = datetime.datetime(2026, 8, 6)
        self.author = type("A", (), {"id": 1, "__str__": lambda s: "someone"})()
        self.content = text
        self.channel = type("C", (), {"id": SHARED})()
        self.attachments, self.embeds, self.reactions = [], [], []
        self.pinned, self.reference = False, None


class _RCChan:
    id = SHARED
    name = "benham-beta"

    def history(self, limit=None):
        class _It:
            def __aiter__(s):
                return s

            def __anext__(s):
                if not hasattr(s, "done"):
                    s.done = True
                    async def _v():
                        return _RCMsg("hello from the beta channel")
                    return _v()
                raise StopAsyncIteration
        # history() must be an async iterator of messages
        class _Real:
            def __aiter__(s):
                s.sent = False
                return s

            async def __anext__(s):
                if s.sent:
                    raise StopAsyncIteration
                s.sent = True
                return _RCMsg("hello from the beta channel")
        return _Real()

    def __str__(self):
        return "benham-beta"


class _RCClient:
    def get_channel(self, cid):
        return _RCChan() if int(cid) == SHARED else None


def run_rsc(params, shared=(SHARED,)):
    identity.GUEST = {"enabled": True, "mode": "workspace", "ids": [DOOM],
                      "capabilities": ["read_shared_channel"],
                      "read_channels": list(shared)}
    identity.GUEST_IDS = {DOOM}
    ctx = capabilities.Ctx(_RCClient(), lambda *_: None, actor_id=DOOM)
    h = capabilities.REGISTRY["read_shared_channel"].handler
    try:
        return asyncio.run(h(ctx, params)), None
    except capabilities.ActionError as e:
        return None, str(e)


out, e = run_rsc({}, shared=())
check("no shared channels -> nothing to read", "no channels are shared" in (e or ""), True)

out, e = run_rsc({})
check("discovery lists only shared channels",
      [c["channel_id"] for c in out["shared_channels"]], [SHARED])

out, e = run_rsc({"channel_id": SHARED, "limit": 5})
check("a shared channel reads", out["count"], 1)
check("...and returns the message text",
      out["messages"][0]["content"], "hello from the beta channel")

out, e = run_rsc({"channel_id": SECRET})
check("a NON-shared channel is refused", e is not None, True)
check("...and the refusal names no name, no contents, nothing but the id asked for",
      e, f"channel {SECRET} is not shared with guests")

check("read_channel (the owner's) is still unreachable by a guest",
      policy.authorize(capabilities.REGISTRY["read_channel"],
                       policy.CallContext.guest_dm(DOOM, 111)).allowed, False)

identity.GUEST = {"enabled": True, "mode": "workspace", "ids": [DOOM],
                  "capabilities": sorted(WS)}
identity.GUEST_IDS = {DOOM}
check("granted but with an empty channel list, it reaches nothing",
      run_rsc({"channel_id": SHARED}, shared=())[1] is not None, True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
