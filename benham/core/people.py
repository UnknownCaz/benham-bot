"""
people.py - who Benham knows, as live state (INTENT decision 61, Stage 9 A).

THE PROBLEM THIS ENDS. Until 2026-11-03 a person was known only if their id sat in
control.json (`guest.ids` to chat, `outreach.people` to be asked), and control.json
is read once at import. Adding Biggie took four hand edits on two machines and
three restarts in one afternoon. Caz: "the allow list is archaic". Goal 9 of
docs/plans/PLAN-known-people.md: lists live in the bot's own state and take effect
without a restart.

So this store is READ ON EVERY CHECK. There is no cache, no import-time snapshot,
and that is the whole point: identity.is_guest() asks load() on every inbound
non-owner DM, and a person add_person wrote one second ago is a guest on the next
message. The file is tiny; a read per DM is nothing next to the API call it gates.

The config lists stay a SECOND source (union), so nothing written before this
store existed stops working. This file never reads or writes control.json -
identity.py owns that, and unions the two.

    state/people.json   {"<user_id>": {"name": ..., "display_name": ...,
                                       "since": "<ISO, Detroit clock + offset>",
                                       "by": "caz" | "cli", "note": ...}}

Per face, like the four guest stores: who may talk to an identity is a property
of that identity (identity.guest_enabled's reasoning). A face resolves through
paths.state_for() at CALL time, so the test redirect of STATE_DIR flows through.

Writes are serialised by one lock because jsonio.write_json stages through a
single `path + ".tmp"`, and two threads writing at once collide on it (the
guest-memory lesson, guest.py). Only the add/remove capabilities write, and they
run in the bot process; every CLI reaches them through the outbox.
"""

import os
import threading

from benham import paths
from benham.core import jsonio

FILE_NAME = "people.json"

_lock = threading.Lock()


def path(face=None):
    """Where this face's people live. None = the face this process runs as."""
    return os.path.join(paths.state_for(face or paths.PROCESS_FACE), FILE_NAME)


def _clean(raw):
    """{int id: record} from whatever is on disk. A damaged entry is dropped, never
    fatal: one bad row must not make every guest a stranger (the _quiet_map rule)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            uid = int(k)
        except (TypeError, ValueError):
            continue
        if uid <= 0 or not isinstance(v, dict):
            continue
        rec = {key: v.get(key) for key in ("name", "display_name", "since", "by", "note")}
        rec["name"] = str(rec["name"] or uid)
        out[uid] = rec
    return out


def load(face=None):
    """Every known person, {user_id: record}, read from disk NOW.

    An unreadable face name answers empty rather than raising: a predicate that
    asks "is this person known" must never be the thing that throws."""
    try:
        p = path(face)
    except ValueError:
        return {}
    return _clean(jsonio.read_json(p, default={}))


def ids(face=None):
    """The known user ids, as a frozenset of int."""
    return frozenset(load(face))


def get(user_id, face=None):
    """One person's record, or None."""
    try:
        return load(face).get(int(user_id))
    except (TypeError, ValueError):
        return None


def names(face=None):
    """{name: id} for the people-map consumers (outreach's reachable set).
    Keyed by the account name add_person stored; the id is the truth."""
    return {rec["name"]: uid for uid, rec in load(face).items()}


def add(user_id, record, face=None):
    """Store one person. Returns the record as written. Overwrites an existing
    entry for the same id - the caller (add_person) refuses that case first."""
    uid = int(user_id)
    rec = {key: record.get(key) for key in ("name", "display_name", "since", "by", "note")}
    rec["name"] = str(rec["name"] or uid)
    with _lock:
        p = path(face)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        current = _clean(jsonio.read_json(p, default={}))
        current[uid] = rec
        jsonio.write_json(p, {str(k): v for k, v in sorted(current.items())})
    return rec


def remove(user_id, face=None):
    """Drop one person. Returns the removed record, or None if they were not here."""
    uid = int(user_id)
    with _lock:
        p = path(face)
        current = _clean(jsonio.read_json(p, default={}))
        rec = current.pop(uid, None)
        if rec is not None:
            jsonio.write_json(p, {str(k): v for k, v in sorted(current.items())})
    return rec
