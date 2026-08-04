"""
pathsafe.py - may this filename exist, and does this path stay inside its root?

Extracted verbatim from capabilities.py (Stage 0 of PLAN-guest-permissions.md).
Two call sites need the same answers - the attachment quarantine under downloads/,
and (Stage 4) the guest workspace under guest_work/ - and two copies of "is this
path safe" is exactly one more than the number that can be trusted to stay in
agreement. Nothing here imports discord, policy or identity: these are questions
about strings and the filesystem, answerable without knowing who is asking.

The threat model is the one the attachment path was built for: the filename is the
single field an attacker fully controls, and `..\\..\\Windows\\System32\\evil.dll`
is a legal thing to name a Discord upload. basename() alone is not enough on
Windows: `CON` and `NUL` are devices rather than files, a trailing dot or space
silently renames what you opened, and both slash directions separate paths.
"""

import os
import re

# Types worth returning as text. Judged by extension as well as content_type,
# because Discord reports plenty of real text files as application/octet-stream.
TEXTUAL_TYPES = ("text/", "application/json", "application/xml", "application/csv",
                 "application/javascript", "application/x-yaml")
TEXTUAL_SUFFIXES = {".txt", ".log", ".md", ".json", ".yml", ".yaml", ".toml", ".ini",
                    ".cfg", ".conf", ".csv", ".tsv", ".xml", ".html", ".css", ".py",
                    ".js", ".ts", ".ps1", ".sh", ".bat", ".java", ".c", ".h", ".cpp",
                    ".rs", ".go", ".sql", ".properties", ".env", ".gitignore", ".mcmeta"}
# Extensions Windows will execute on a double click. What a call site does about
# one is its own policy: downloads/ flags and never blocks (Tyler is handed .jar
# files legitimately); the guest workspace will refuse them outright.
RUNNABLE_SUFFIXES = {".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".msi", ".vbs",
                     ".jse", ".wsf", ".lnk", ".reg"}

UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
RESERVED_STEMS = ({"con", "prn", "aux", "nul"}
                  | {f"com{i}" for i in range(1, 10)}
                  | {f"lpt{i}" for i in range(1, 10)})


class ConfinementError(ValueError):
    """A path resolved outside the root it was supposed to stay inside.

    Raised instead of capabilities.ActionError so this module needs nothing from
    capabilities (which imports it). Call sites catch this and re-raise in their
    own vocabulary, naming their own root - "outside downloads/" is a sentence
    about one call site, not about path arithmetic.
    """


def safe_filename(raw, fallback):
    """Rewrite an uploader's filename into something safe to create.

    Moved unchanged from capabilities._safe_filename; see the module docstring for
    why each rule exists.
    """
    name = str(raw or "").replace("\\", "/").split("/")[-1]
    name = UNSAFE_NAME_RE.sub("_", name).strip(" .")
    if not name:
        return fallback
    root, ext = os.path.splitext(name)
    if root.lower() in RESERVED_STEMS:
        root = "_" + root
    # Long names are truncated from the stem so the extension survives - the
    # extension is what says whether this is a log or a video.
    if len(root) + len(ext) > 120:
        root = root[:max(1, 120 - len(ext))]
    return (root + ext) or fallback


def confined_path(root, filename):
    """Join, then verify the result is really inside root.

    The sanitiser above should make escape impossible, which is the point: a path
    check that only fires when the sanitiser has a bug is exactly the check worth
    having, and it costs one realpath.
    """
    dest = os.path.realpath(os.path.join(root, filename))
    if os.path.commonpath([dest, os.path.realpath(root)]) != os.path.realpath(root):
        raise ConfinementError(f"path escapes its root: {filename!r}")
    return dest


def is_textual(content_type, filename):
    ct = (content_type or "").lower()
    if any(ct.startswith(t) for t in TEXTUAL_TYPES):
        return True
    return os.path.splitext(filename.lower())[1] in TEXTUAL_SUFFIXES
