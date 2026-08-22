"""
test_conv_faces.py - two independent queues, one shared store, no cross-face binds.

Commit 8 of PLAN-second-face.md, and the regression test Kestra asked for. The
conversation STORE stays shared (one question owed is one question owed,
whoever carried it - the spike's classification), but three things are per
face, each guarding its own failure:

  * the record's `face`, stamped at open - without it both faces' ticks see
    the same overdue conversation in the shared store and BOTH nudge it;
  * the queue a face renders and numbers - Tyler's decision 3, two numbering
    schemes, one per DM thread;
  * ask_batches.json - it stores Discord message ids, which belong to one
    face's DM channel, so a batch id recorded by face A must never bind a
    reply arriving at face B.

Also holds the two fields the nudge-cap worker shipped (asker_session,
nudge_cap) unchanged through the face stamp - their semantics are theirs.

    python test_conv_faces.py
"""

# Runnable from anywhere: tests/ is sys.path[0] when run directly, so put the
# repo root there too - that is where the benham package lives.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _testconfig  # noqa: F401,E402 - redirects CONFIG/STATE dirs first

import os
import shutil
import sys
import tempfile

from benham import paths
from benham.core import conversations as C

_fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"      got:  {got!r}\n      want: {want!r}")
        _fails.append(label)


TYLER = 273967061619965952

_tmp = tempfile.mkdtemp(prefix="benham-conv-faces-")
_real_store, _real_batches, _real_face = C.STORE, C.BATCHES, paths.PROCESS_FACE
C.STORE = os.path.join(_tmp, "conversations.json")


def as_face(face):
    """Run the module as one face's process: the face and ITS batches file.
    This is exactly what differs between two real processes - everything else
    (the store) is deliberately shared."""
    paths.PROCESS_FACE = face
    C.BATCHES = os.path.join(_tmp, f"ask_batches-{face}.json")


try:
    # --- the stamp -----------------------------------------------------------
    as_face("benham")
    b1 = C.open_conversation(TYLER, "test", "benham's question?")
    as_face("codex")
    c1 = C.open_conversation(TYLER, "test", "codex's question?",
                             asker_session="local_abc123", nudge_cap=1)
    check("a conversation carries its opening face", C.face_of(C.get(b1["id"])),
          "benham")
    check("...and codex's carries codex", C.face_of(C.get(c1["id"])), "codex")
    check("a record with no face field is the primary's (pre-faces records)",
          C.face_of({"id": "x"}), "benham")
    check("the shipped asker_session field rides through unchanged",
          C.get(c1["id"])["asker_session"], "local_abc123")
    check("the shipped nudge_cap field rides through unchanged",
          C.get(c1["id"])["nudge_cap"], 1)

    # --- two independent queues ---------------------------------------------
    as_face("benham")
    check("benham's queue holds only benham's ask",
          [c["id"] for c in C.queue_for(TYLER)], [b1["id"]])
    as_face("codex")
    check("codex's queue holds only codex's ask",
          [c["id"] for c in C.queue_for(TYLER)], [c1["id"]])
    check("slot 1 in codex's numbering is codex's question",
          C.by_slot(TYLER, 1)["id"], c1["id"])
    as_face("benham")
    check("slot 1 in benham's numbering is benham's question - same person, "
          "same number, different question per face",
          C.by_slot(TYLER, 1)["id"], b1["id"])

    # --- only the carrying face's tick advances ------------------------------
    # Both are past due; each process's due() must hand over exactly its own.
    from datetime import timedelta
    later = C._now() + C.NUDGE_AFTER + timedelta(minutes=1)
    as_face("benham")
    check("benham's tick sees only benham's conversation",
          [c["id"] for c, _ in C.due(now=later)], [b1["id"]])
    as_face("codex")
    check("codex's tick sees only codex's conversation - the cross-face "
          "double-nudge is unrepresentable",
          [c["id"] for c, _ in C.due(now=later)], [c1["id"]])
    check("...and the shipped nudge_cap decides its verdict (cap 1: nudge)",
          [what for _, what in C.due(now=later)], ["nudge"])

    # --- the cross-face bind regression (Kestra's ask) -----------------------
    # Face A renders its queue as message 111. A reply referencing 111 can only
    # arrive at face A's process; but even a confused caller in face B's
    # process must not resolve A's numbering - B's batches file simply does
    # not contain it.
    as_face("benham")
    C.set_batch_message(TYLER, 111, shown=[b1["id"]])
    check("face A's batch message is on record for face A",
          C.batch_message(TYLER), 111)
    check("...and face A's screen numbering resolves face A's question",
          C.shown_queue(TYLER)[0]["id"], b1["id"])
    as_face("codex")
    check("a batch id from face A does not exist for face B",
          C.batch_message(TYLER), None)
    check("face B's slot 1 still resolves B's OWN question, never A's numbering",
          C.by_slot(TYLER, 1)["id"], c1["id"])
    C.set_batch_message(TYLER, 222, shown=[c1["id"]])
    as_face("benham")
    check("...and B's batch message is invisible to A in return",
          C.batch_message(TYLER), 111)

    # --- an explicit face argument wins over the process face ----------------
    as_face("benham")
    x = C.open_conversation(TYLER, "test", "opened for the other face?",
                            face="codex")
    check("open_conversation(face=...) stamps the named face",
          C.face_of(C.get(x["id"])), "codex")
finally:
    C.STORE, C.BATCHES, paths.PROCESS_FACE = _real_store, _real_batches, _real_face
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
sys.exit(1 if _fails else 0)
