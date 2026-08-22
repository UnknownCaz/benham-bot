"""
benham/paths.py - the one module that knows where everything lives.

Before this file, fourteen modules each computed BASE_DIR from their own
__file__ and resolved every config and state file against it. That worked only
because everything sat flat in the repo root; the moment a module moves, its
files silently move with it. Now every module asks this one, and relocating a
whole category of files is a one-line change here (PLAN-src-reorg.md, Stage 5).

The categories, by who writes the file:

  CONFIG_DIR   hand-edited by the owner, read by the bot: environ.env,
               control.json, webhooks.json, voices.json, exaroton_watch.json.
  STATE_DIR    written by the bot or its tools; never hand-edit, never commit:
               inbox.jsonl, the memories/usage/search logs, channels.json (the
               bot rewrites it every boot), voice_settings.json,
               personality_overrides.txt, outbox/, downloads/, guest_work/.
  PROMPTS_DIR  persona and guardrail text: persona.md, guest_persona.md,
               guest_guide.md.
  LOG_DIR      where live process logs are written (supervise.log, boot*.out).

This module imports nothing but os, so importing it can never be a cycle.
"""

import os

# Repo root: this file sits at <root>/benham/paths.py.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_DIR = os.path.join(ROOT, "config")
STATE_DIR = os.path.join(ROOT, "state")
PROMPTS_DIR = os.path.join(ROOT, "prompts")
LOG_DIR = os.path.join(ROOT, "logs")

# ---------------------------------------------------------------------------
# Faces (PLAN-second-face.md, commit 1).
#
# A face is one bot identity: its own token, persona and state. The DEFAULT
# face is Benham, and it resolves to exactly the flat directories above -
# byte-identical with every path this module handed out before faces existed,
# so an unmigrated deployment keeps working without a config edit. A named
# non-default face lives under faces/<name>/ beneath the same roots.
#
# Both functions read STATE_DIR/PROMPTS_DIR at call time, not at import, so
# the _testconfig redirect flows through them the same way it flows through
# every store that resolves its path late.
#
# The name check is the rooms rule (lowercase kebab, bounded): a face name
# reaches os.path.join, so "../../x" must be unrepresentable, not filtered.
# ---------------------------------------------------------------------------

DEFAULT_FACE = "benham"

_FACE_ALPHABET = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def check_face(face):
    """Raise ValueError unless `face` is a legal face name. Public on purpose:
    identity.py validates config-declared names against the same rule that
    guards the filesystem, so the two can never disagree about what a face is."""
    if (
        not isinstance(face, str)
        or not face
        or len(face) > 40
        or face[0] not in "abcdefghijklmnopqrstuvwxyz"
        or set(face) - _FACE_ALPHABET
    ):
        raise ValueError(
            f"invalid face name {face!r}: lowercase kebab, starts with a letter, max 40 chars"
        )


_check_face = check_face  # the functions below predate the public name


def state_for(face=DEFAULT_FACE):
    """State root for one face; the default face is today's STATE_DIR itself."""
    _check_face(face)
    if face == DEFAULT_FACE:
        return STATE_DIR
    return os.path.join(STATE_DIR, "faces", face)


def prompts_for(face=DEFAULT_FACE):
    """Prompts root for one face; the default face is today's PROMPTS_DIR itself."""
    _check_face(face)
    if face == DEFAULT_FACE:
        return PROMPTS_DIR
    return os.path.join(PROMPTS_DIR, "faces", face)
