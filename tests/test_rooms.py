"""
test_rooms.py - the room primitive: explicit creation, provenance, cursors,
the no-free-text listing, and the worker/handoff arithmetic.

What these checks pin, and why each is worth pinning (INTENT items 20/22):

  CREATION IS EXPLICIT. A ghost or archived room refuses posts and names what
  it knows - a typo must fail loud, never mint a room (item 20.4).

  THE LISTING CARRIES NO FREE TEXT. names + unread counts, nothing else. That
  is the property that lets it sit in a prompt without tainting (item 22), so
  a test asserts the ABSENCE of purpose/messages in listing output - the kind
  of claim that otherwise rots silently.

  CURSORS ARE PER-READER AND MONOTONIC. Reader A's read must not move reader
  B's count, and a re-read never rewinds.

  THE HANDOFF BOUND IS ARITHMETIC. resumable() returns the worker id until
  runs hit the bound, then None - the transcript is the cost, the room is the
  memory, and the rule lives in one function.

Everything runs against a temp directory, never the real state/.

    python tests/test_rooms.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import shutil
import tempfile
from datetime import datetime, timezone

import _testconfig  # noqa: F401,E402 - must precede every benham import

from benham.core import rooms


def iso_now():
    return datetime.now(timezone.utc).isoformat()

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}, want {want!r}")
        _fails.append(label)


def section(title):
    print(f"\n{title}")


def main():
    tmp = tempfile.mkdtemp(prefix="benham-rooms-")
    rooms.INDEX_FILE = os.path.join(tmp, "rooms.json")
    rooms.ROOMS_DIR = os.path.join(tmp, "rooms")
    rooms.CURSOR_DIR = os.path.join(tmp, "rooms", "cursors")
    try:
        section("names are charset-limited - the listing-safety property")
        check("kebab passes", rooms.valid_name("storyizier-bugs"), True)
        check("digits pass", rooms.valid_name("room2"), True)
        check("uppercase fails", rooms.valid_name("Scratch"), False)
        check("spaces fail", rooms.valid_name("my room"), False)
        check("leading hyphen fails", rooms.valid_name("-x"), False)
        check("41 chars fails", rooms.valid_name("a" * 41), False)
        check("empty fails", rooms.valid_name(""), False)

        section("creation is explicit, collision is loud")
        entry = rooms.create("scratch", "default room", "system")
        check("created with seq 0", entry["seq"], 0)
        try:
            rooms.create("scratch", "again", "system")
            check("duplicate create refused", "no error", "ValueError")
        except ValueError as e:
            check("duplicate create refused", "already exists" in str(e), True)
        try:
            rooms.create("Bad Name", "x", "system")
            check("bad name refused", "no error", "ValueError")
        except ValueError:
            check("bad name refused", True, True)
        check("ensure() returns existing without error",
              rooms.ensure("scratch", "ignored", "system")["purpose"],
              "default room")

        section("posting: ghosts fail naming the known, provenance rides every line")
        try:
            rooms.post("nope", "tyler", "hello?")
            check("ghost post refused", "no error", "ValueError")
        except ValueError as e:
            check("ghost post refused", "scratch" in str(e), True)
        m1 = rooms.post("scratch", "tyler", "first")
        m2 = rooms.post("scratch", "worker (abc123)", "second")
        check("seq increments", (m1["seq"], m2["seq"]), (1, 2))
        check("author on the line", m2["author"], "worker (abc123)")
        check("ts on the line", bool(m1["ts"]), True)
        try:
            rooms.post("scratch", "tyler", "   ")
            check("empty post refused", "no error", "ValueError")
        except ValueError:
            check("empty post refused", True, True)

        section("a torn final line is skipped, not fatal")
        with open(rooms._room_file("scratch"), "a", encoding="utf-8") as fh:
            fh.write('{"seq": 3, "truncated')
        check("messages read past the tear",
              [m["seq"] for m in rooms.messages("scratch")], [1, 2])

        section("cursors are per-reader and monotonic")
        entry, msgs = rooms.read_and_mark("tyler", "scratch")
        check("first read sees both", [m["seq"] for m in msgs], [1, 2])
        check("tyler's cursor advanced", rooms.cursor("tyler")["scratch"], 2)
        check("worker's cursor untouched",
              rooms.cursor("worker-x").get("scratch"), None)
        _, again = rooms.read_and_mark("tyler", "scratch")
        check("re-read shows the tail rather than nothing", len(again) > 0, True)
        rooms.mark_read("tyler", "scratch", 1)
        check("cursor never rewinds", rooms.cursor("tyler")["scratch"], 2)

        section("the listing: names + counts ONLY, archived rooms leave it")
        rooms.create("storyizier", "Doom's story bot work", "tyler")
        rooms.post("storyizier", "tyler", "note")
        lst = rooms.listing("tyler")
        check("both rooms listed", [r["name"] for r in lst],
              ["scratch", "storyizier"])
        check("unread math per reader",
              [(r["name"], r["unread"]) for r in rooms.listing("worker-x")],
              [("scratch", 2), ("storyizier", 1)])
        check("NO free text in a listing row - the no-taint property",
              sorted(lst[0]), ["has_worker", "name", "unread"])
        rooms.archive("storyizier")
        check("archived room leaves the listing",
              [r["name"] for r in rooms.listing("tyler")], ["scratch"])
        try:
            rooms.post("storyizier", "tyler", "into the void")
            check("archived post refused", "no error", "ValueError")
        except ValueError as e:
            check("archived post refused", "archived" in str(e), True)

        section("worker record: the handoff bound is arithmetic")
        check("no worker yet -> fresh", rooms.resumable("scratch"), None)
        rooms.record_run("scratch", "sess-aaa", resumed=False)
        check("fresh run recorded at 1", rooms.worker("scratch")["runs"], 1)
        check("now resumable", rooms.resumable("scratch"), "sess-aaa")
        for _ in range(rooms.handoff_after() - 1):
            rooms.record_run("scratch", "sess-aaa", resumed=True)
        check("runs at the bound", rooms.worker("scratch")["runs"],
              rooms.handoff_after())
        check("at the bound -> fresh again (room is the memory)",
              rooms.resumable("scratch"), None)
        rooms.record_run("scratch", None, resumed=True)
        check("a run with no id never clobbers a good record",
              rooms.worker("scratch")["session_id"], "sess-aaa")
        rooms.record_run("scratch", "sess-bbb", resumed=False)
        check("fresh id restarts the count", rooms.worker("scratch")["runs"], 1)

        section("registry: the taint split item 22 settled")
        from benham.core import capabilities, policy
        for name in ("list_rooms", "read_room", "create_room", "post_room"):
            check(f"{name} is registered", name in capabilities.REGISTRY, True)
        check("the LISTING does not taint (metadata only)",
              capabilities.REGISTRY["list_rooms"].taints, False)
        check("CONTENT taints (20.7 as settled)",
              capabilities.REGISTRY["read_room"].taints, True)
        for name in ("list_rooms", "read_room", "create_room", "post_room"):
            act = capabilities.REGISTRY[name]
            check(f"{name} is not a guest capability", act.guest, False)
            check(f"{name} is unreachable by SYSTEM (pull-only has no timer)",
                  policy.Origin.SYSTEM in (act.origins or policy.DEFAULT_ORIGINS),
                  False)
            check(f"{name} is not outward (a room is a file, not a surface)",
                  act.outward, False)

        section("the agent prompt carries names+counts and never room content")
        from benham.core import agent
        rooms.post("scratch", "tyler", "SECRET-SAUCE-DO-NOT-LEAK")
        _static, vol = agent._system_blocks("a DM", "Tyler")
        check("## Rooms section present when rooms exist", "## Rooms" in vol, True)
        check("the scratch count is shown", "`scratch`" in vol, True)
        check("message text NEVER reaches the prompt",
              "SECRET-SAUCE" in (vol + _static), False)
        check("purpose text NEVER reaches the prompt",
              "default room" in vol, False)

        section("spawn_in_room: pointer not content, resume split, auto-post")
        import asyncio
        from benham.core import policy as _pol
        import benham.core.codesession as codesession

        act = capabilities.REGISTRY["spawn_in_room"]
        check("pc_task's exact profile: blocked_when_tainted",
              act.blocked_when_tainted, True)
        check("...and taints", act.taints, True)
        check("...and DM/CLI origins only",
              act.origins, {_pol.Origin.OWNER_DM, _pol.Origin.LOCAL_CLI})

        rooms.post("scratch", "tyler", "PLANTED-ROOM-CONTENT")
        calls = []

        async def fake_run_task(prompt, on_progress=None, read_only=False,
                                resume=None):
            calls.append({"prompt": prompt, "resume": resume})
            return {"text": "did the thing", "session_id": f"sess-{len(calls)}",
                    "cost_usd": 0.1, "is_error": False, "tools_used": [],
                    "asks": 0, "started": iso_now(), "ended": iso_now()}

        async def fast_sleep(_):
            return None

        old = (codesession.run_task, codesession.ENABLED,
               capabilities.asyncio.sleep)
        codesession.run_task, codesession.ENABLED = fake_run_task, True
        capabilities.asyncio.sleep = fast_sleep

        def spawn(**params):
            r, _pv = asyncio.run(capabilities.run(
                None, lambda m: None, "spawn_in_room", params,
                actor_id=None, force=True, call_ctx=_pol.CallContext.local()))
            return r

        try:
            try:
                spawn(room="ghost", task="x")
                check("ghost room refused", "no error", "ActionError")
            except capabilities.ActionError as e:
                check("ghost room refused", "ghost" in str(e) or "no room" in str(e),
                      True)

            r1 = spawn(room="scratch", task="do a one-off")
            check("scratch spawns FRESH by default", calls[-1]["resume"], None)
            check("the task leads the prompt",
                  calls[-1]["prompt"].startswith("do a one-off"), True)
            check("the pointer names the room",
                  "room read scratch" in calls[-1]["prompt"], True)
            check("ROOM CONTENT never rides the prompt (item 22c)",
                  "PLANTED-ROOM-CONTENT" in calls[-1]["prompt"], False)
            check("the report was auto-posted",
                  rooms.messages("scratch")[-1]["text"], "did the thing")
            check("...attributed to the worker",
                  rooms.messages("scratch")[-1]["author"].startswith("worker ("),
                  True)
            check("...and the result says where",
                  r1["posted_seq"], rooms.messages("scratch")[-1]["seq"])

            spawn(room="scratch", task="another one-off")
            check("scratch stays fresh on the second spawn too",
                  calls[-1]["resume"], None)
            prior = rooms.worker("scratch")["session_id"]
            spawn(**{"room": "scratch", "task": "same thread", "continue": True})
            check("continue=true resumes even scratch (the id on record BEFORE)",
                  calls[-1]["resume"], prior)

            rooms.create("proj", "a named project room", "tyler")
            spawn(room="proj", task="start the thread")
            check("a named room's first spawn is fresh (nothing to resume)",
                  calls[-1]["resume"], None)
            first_id = rooms.worker("proj")["session_id"]
            r2 = spawn(room="proj", task="continue the thread")
            check("a named room RESUMES by default", calls[-1]["resume"], first_id)
            check("...and says so", r2["resumed"], True)
            spawn(room="proj", task="unrelated", fresh=True)
            check("fresh=true forces a new worker", calls[-1]["resume"], None)

            for _ in range(rooms.handoff_after() + 1):
                rooms.record_run("proj", rooms.worker("proj")["session_id"],
                                 resumed=True)
            r3 = spawn(room="proj", task="past the bound")
            check("past the handoff bound: fresh, and the result says rolled over",
                  (calls[-1]["resume"], r3["rolled_over"]), (None, True))
            check("...and the prompt tells the session it replaces the worker",
                  "replace this room's previous worker" in calls[-1]["prompt"],
                  True)
        finally:
            (codesession.run_task, codesession.ENABLED,
             capabilities.asyncio.sleep) = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
