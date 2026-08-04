"""
guest_workspace.py - the files guests may have, and the fence around them.
(Guest-refactor Stage 4.)

Layout, fixed:

    guest_work/
      commons/       Tyler-curated; every guest may read, no guest may write
      <user_id>/     one per guest, created on first write, reachable only by
                     that guest - the folder name comes from the CallContext's
                     actor id and is never a parameter anything can pass

This module is pure file logic: no discord import, no policy import, no model
anywhere near it. WHO may call these functions is policy.py's decision, made
before any of this runs; this file only answers WHERE bytes may land and how
many. Keeping the two questions in different files is the same split as
identity/policy - authority in the rules file, arithmetic here.

The namespace is FLAT, deliberately. A guest names files, not paths: every name
goes through pathsafe.safe_filename and anything that does not survive the trip
unchanged is refused rather than silently rewritten - for a WRITE, the caller
typed the name and should hear that it was unusable, where an IMPORT keeps the
sanitise-and-say-so behaviour because an uploader's filename is the one field
an attacker fully controls. Subfolders, drive letters, dots-and-slashes of any
flavour: all of it dies in the same two pathsafe functions the downloads
quarantine has trusted since before this file existed.

One rule has no exception anywhere in this file: the guest workspace never
contains a runnable-suffixed file, however it arrives. The downloads folder
flags and allows (Tyler gets handed .jar files legitimately); a guest has no
legitimate reason to author .bat onto Tyler's disk, and a friend who truly
needs to hand over a script sends it as .txt - Tyler moving it out of
quarantine is the deliberate human step that makes it live.
"""

import os
import threading

import identity
import pathsafe

from benham import paths
ROOT = os.path.join(paths.STATE_DIR, "guest_work")
COMMONS = os.path.join(ROOT, "commons")

_cfg = (identity.guest_config().get("workspace") or {})
PER_FILE_BYTES = int(float(_cfg.get("per_file_mb", 5)) * 1024 * 1024)
PER_GUEST_BYTES = int(float(_cfg.get("per_guest_mb", 20)) * 1024 * 1024)
MAX_FILES = int(_cfg.get("max_files", 100))
MAX_TEXT_CHARS = 20000          # same inline-return bound as read_attachments

# One lock around every check-then-write, for the same reason guest.py's
# _quota_lock exists: two tool calls from two guests really can race, and a cap
# that leaks under exactly the concurrent case is not much of a cap.
_lock = threading.Lock()


class WorkspaceError(Exception):
    """A user-facing workspace refusal. capabilities.py re-raises as ActionError."""


def guest_root(actor_id):
    """This guest's folder. int() is the isolation: the path component is a
    number derived from the verified CallContext actor, never a string anything
    upstream chose, so guest A's root cannot name guest B's whatever the model
    or the message said."""
    try:
        return os.path.join(ROOT, str(int(actor_id)))
    except (TypeError, ValueError):
        raise WorkspaceError("no workspace without a numeric guest id")


def _clean_name(raw, strict):
    """One flat filename, made safe - or refused.

    strict=True (writes, deletes, attaches, reads): the name must survive
    sanitisation UNCHANGED, so what the caller said is exactly what happens.
    strict=False (imports): the uploader's name is sanitised and the new name
    reported, read_attachments-style.
    """
    name = pathsafe.safe_filename(raw, "")
    if not name:
        raise WorkspaceError(f"{raw!r} is not a usable filename")
    if strict and name != str(raw):
        raise WorkspaceError(
            f"{raw!r} is not a plain filename here - the workspace is flat, so "
            f"no folders, no drive letters, no path tricks. Did you mean {name!r}?")
    return name


def _no_runnable(name):
    if os.path.splitext(name.lower())[1] in pathsafe.RUNNABLE_SUFFIXES:
        raise WorkspaceError(
            f"{name!r} has a runnable extension, and the guest workspace never "
            "holds one - send it as .txt if it is really needed.")


def _resolve(root, name):
    """Join and confine. The sanitiser makes escape impossible; this is the
    check that fires only if the sanitiser has a bug, which is exactly the
    check worth having."""
    try:
        return pathsafe.confined_path(root, name)
    except pathsafe.ConfinementError:
        raise WorkspaceError(f"{name!r} escapes the workspace - refused")


def _entries(folder):
    out = []
    if os.path.isdir(folder):
        for n in sorted(os.listdir(folder)):
            p = os.path.join(folder, n)
            if os.path.isfile(p):
                st = os.stat(p)
                out.append({"name": n, "bytes": st.st_size,
                            "modified": int(st.st_mtime)})
    return out


def _folder_usage(folder):
    files = _entries(folder)
    return len(files), sum(f["bytes"] for f in files)


def list_files(actor_id):
    """This guest's files plus the commons - names, sizes, and the caps."""
    mine = _entries(guest_root(actor_id))
    count, used = len(mine), sum(f["bytes"] for f in mine)
    return {
        "mine": mine,
        "commons": _entries(COMMONS),
        "used_bytes": used, "quota_bytes": PER_GUEST_BYTES,
        "files": count, "max_files": MAX_FILES,
    }


def read_file(actor_id, name, area="mine"):
    """One file's contents (text, truncated) or its metadata (binary).

    Reads reach two places only: the guest's own folder and commons/. There is
    no third value of `area`, and other guests' folders are not an area.
    """
    name = _clean_name(name, strict=True)
    if area not in ("mine", "commons"):
        raise WorkspaceError("area must be 'mine' or 'commons'")
    folder = COMMONS if area == "commons" else guest_root(actor_id)
    path = _resolve(folder, name)
    if not os.path.isfile(path):
        raise WorkspaceError(f"no file called {name!r} in {area}")
    size = os.path.getsize(path)
    rec = {"name": name, "area": area, "bytes": size}
    if pathsafe.is_textual(None, name):
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
        rec["text"] = text[:MAX_TEXT_CHARS]
        rec["text_truncated"] = len(text) > MAX_TEXT_CHARS
    else:
        rec["note"] = "binary file - ask for it as an attachment (ws_attach)"
    return rec


def _admit(root, name, nbytes, replacing):
    """The quota gate, called with the lock held: may `nbytes` more land here?"""
    count, used = _folder_usage(root)
    if replacing:
        count -= 1
        used -= os.path.getsize(os.path.join(root, name))
    if nbytes > PER_FILE_BYTES:
        raise WorkspaceError(
            f"{nbytes / 1048576:.1f}MB is over the {PER_FILE_BYTES // 1048576}MB "
            "per-file cap")
    if count + 1 > MAX_FILES:
        raise WorkspaceError(
            f"the workspace holds at most {MAX_FILES} files - delete something first")
    if used + nbytes > PER_GUEST_BYTES:
        raise WorkspaceError(
            f"that would pass the {PER_GUEST_BYTES // 1048576}MB workspace cap "
            f"({(used) / 1048576:.1f}MB used) - delete something first")


def write_file(actor_id, name, text):
    """Create or overwrite one text file in the guest's own folder."""
    name = _clean_name(name, strict=True)
    _no_runnable(name)
    root = guest_root(actor_id)
    data = str(text).encode("utf-8")
    with _lock:
        os.makedirs(root, exist_ok=True)
        path = _resolve(root, name)
        replacing = os.path.isfile(path)
        _admit(root, name, len(data), replacing)
        with open(path, "wb") as f:
            f.write(data)
    return {"status": "written", "name": name, "bytes": len(data),
            "replaced": replacing}


def import_bytes(actor_id, filename, data):
    """Land downloaded attachment bytes in the guest's folder. Sanitises the
    name (uploader-controlled) rather than refusing a mismatch, and says so."""
    safe = pathsafe.safe_filename(filename, "imported_file")
    _no_runnable(safe)
    root = guest_root(actor_id)
    with _lock:
        os.makedirs(root, exist_ok=True)
        path = _resolve(root, safe)
        replacing = os.path.isfile(path)
        _admit(root, safe, len(data), replacing)
        with open(path, "wb") as f:
            f.write(data)
    rec = {"status": "imported", "name": safe, "bytes": len(data),
           "replaced": replacing}
    if safe != str(filename):
        rec["sanitised_from"] = str(filename)
    return rec


def delete_file(actor_id, name):
    """Delete one of the guest's OWN files. Commons is not deletable from here,
    structurally: the root this resolves against is theirs and nothing else."""
    name = _clean_name(name, strict=True)
    root = guest_root(actor_id)
    path = _resolve(root, name)
    with _lock:
        if not os.path.isfile(path):
            raise WorkspaceError(f"no file called {name!r} in your workspace")
        os.remove(path)
    return {"status": "deleted", "name": name}


def attach_path(actor_id, name):
    """The full path of one of the guest's own files, for attaching to the
    reply. Own folder only - commons files are readable but travel by Tyler's
    hand, and other guests' files are not reachable in any spelling."""
    name = _clean_name(name, strict=True)
    path = _resolve(guest_root(actor_id), name)
    if not os.path.isfile(path):
        raise WorkspaceError(f"no file called {name!r} in your workspace")
    return path


def verify_outgoing(actor_id, path):
    """Second look before a file leaves in a reply: is this REALLY inside this
    guest's own folder right now? bot.py calls this on every path the loop
    hands back, so a bug anywhere upstream fails the attach rather than
    shipping a file. Check twice, as always."""
    root = os.path.realpath(guest_root(actor_id))
    real = os.path.realpath(str(path))
    try:
        inside = os.path.commonpath([real, root]) == root
    except ValueError:
        inside = False
    return real if (inside and os.path.isfile(real)) else None
