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
               guardrails.md, guest_guide.md.
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
