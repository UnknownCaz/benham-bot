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

import _testconfig  # noqa: F401,E402 - must precede every benham import

from benham.core import rooms

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
