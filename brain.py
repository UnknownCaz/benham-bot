"""
brain.py — the autonomous reply generator for Benham (Anthropic API).

Given the recent conversation (a list of {"role": "user"|"assistant", "content": str}),
returns a short spoken reply in the Benham persona (persona.md as the system prompt).

Token/API-call frugality lives mostly in bot.py (wake-gating, local shortcuts, debounce,
sliding window). This module keeps each call cheap: cheap model, low max_tokens, no thinking.

A reply may end with an inline voice directive like:  <<voice=Zira; rate=-2; volume=90>>
parse_directive() extracts it and strip_directive() removes it before TTS — so a nuanced
voice change costs ONE call, never a second round-trip.
"""

import os
import re

MODEL = os.environ.get("BENHAM_MODEL", "claude-haiku-4-5")  # $1/$5 per 1M — cheap voice tier
MAX_TOKENS = int(os.environ.get("BENHAM_MAX_TOKENS", "160"))  # spoken replies are short

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONA_FILE = os.path.join(BASE_DIR, "persona.md")

_client = None
_system_cache = None

_DIRECTIVE_RE = re.compile(r"<<\s*(voice|rate|volume)\b.*?>>", re.IGNORECASE | re.DOTALL)


def _get_client():
    global _client
    if _client is None:
        import anthropic

        # Reads ANTHROPIC_API_KEY from env (loaded from environ.env by bot.py).
        _client = anthropic.Anthropic()
    return _client


def _system_prompt():
    global _system_cache
    if _system_cache is None:
        try:
            with open(PERSONA_FILE, "r", encoding="utf-8") as f:
                _system_cache = f.read()
        except Exception:  # noqa: BLE001
            _system_cache = "You are Benham, a friendly voice in a Discord call. Keep replies short."
    return _system_cache


def respond(messages):
    """Call the API and return the assistant's reply text (directive still attached)."""
    client = _get_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_system_prompt(),
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    usage = getattr(resp, "usage", None)
    return text, usage


def parse_directive(text):
    """Return a dict of voice settings from a trailing <<...>> directive, or {} if none."""
    m = _DIRECTIVE_RE.search(text or "")
    if not m:
        return {}
    body = m.group(0).strip("<> ")
    out = {}
    for part in re.split(r"[;,]", body):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if k == "voice":
            low = v.lower()
            if "zira" in low or "female" in low or "woman" in low:
                out["voice"] = "Microsoft Zira Desktop"
            elif "david" in low or "male" in low or "man" in low:
                out["voice"] = "Microsoft David Desktop"
            else:
                out["voice"] = v
        elif k in ("rate", "volume"):
            try:
                out[k] = int(re.sub(r"[^\-0-9]", "", v))
            except ValueError:
                pass
    return out


def strip_directive(text):
    """Remove the inline voice directive so it isn't spoken aloud."""
    return _DIRECTIVE_RE.sub("", text or "").strip()
