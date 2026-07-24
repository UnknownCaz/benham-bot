"""
brain.py — the autonomous reply generator for Benham (Anthropic API).

Given the recent conversation (a list of {"role": "user"|"assistant", "content": str}),
returns a short spoken reply. The system prompt is composed of, in order:
  1. guardrails.md         — LOCKED safety + output-format rules (always win)
  2. persona.md            — EDITABLE base personality (tone/identity)
  3. personality_overrides — runtime traits the user added by voice ("be more sarcastic")
Guardrails come first and are stated as non-overridable, so personality tweaks can change HOW
Benham talks but never WHAT he's allowed to do.

Token/API-call frugality lives mostly in bot.py. This module keeps each call cheap: cheap model,
low max_tokens, no thinking.

Replies may end with directives the app applies + strips before TTS (one call, no round-trip):
  <<voice=Zira; rate=-2; volume=90>>   -> voice/rate/volume  (parse_directive)
  <<persona: be more dry and sarcastic>> -> lasting personality change (parse_persona_directive)
"""

import os
import re
import json

MODEL = os.environ.get("BENHAM_MODEL", "claude-haiku-4-5")  # $1/$5 per 1M — cheap voice tier
MAX_TOKENS = int(os.environ.get("BENHAM_MAX_TOKENS", "160"))  # spoken replies are short

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUARDRAILS_FILE = os.path.join(BASE_DIR, "guardrails.md")
PERSONA_FILE = os.path.join(BASE_DIR, "persona.md")
OVERRIDES_FILE = os.path.join(BASE_DIR, "personality_overrides.txt")
VOICES_FILE = os.path.join(BASE_DIR, "voices.json")

# --- Named B-voice roster (voices.json) — one source of truth, imported by bot.py. ---
def _load_voices():
    try:
        with open(VOICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"default_male": "", "default_female": "", "voices": {}}

VOICES = _load_voices()
NAME_TO_VOICE = {n.lower(): v["voice"] for n, v in VOICES.get("voices", {}).items()}
_dm = VOICES.get("voices", {}).get(VOICES.get("default_male", ""), {})
_df = VOICES.get("voices", {}).get(VOICES.get("default_female", ""), {})
VOICE_MALE = os.environ.get("BENHAM_VOICE_MALE", _dm.get("voice", "en-US-BrianNeural"))
VOICE_FEMALE = os.environ.get("BENHAM_VOICE_FEMALE", _df.get("voice", "en-US-AvaNeural"))

_FEMALE_WORDS = ("female", "woman", "girl", "she")
_MALE_WORDS = ("male", "man", "guy", "dude", "he")


def resolve_voice(token):
    """Resolve a voice token to an edge-tts voice id, or None. Accepts a roster B-name, the words
    male/female, or a raw edge voice id (en-...Neural)."""
    if not token:
        return None
    t = str(token).strip().lower()
    if t in NAME_TO_VOICE:
        return NAME_TO_VOICE[t]
    if any(w in t for w in _FEMALE_WORDS):
        return VOICE_FEMALE
    if any(w in t for w in _MALE_WORDS):
        return VOICE_MALE
    if t.startswith("en-") or "neural" in t:
        return str(token).strip()
    return None


def voice_names():
    """List of roster B-names (preserves file order)."""
    return list(VOICES.get("voices", {}).keys())


def voices_blurb():
    """One-line-per-voice roster for the system prompt / 'what voices' answers."""
    return ", ".join(f"{n} ({v['gender']}, {v['desc']})" for n, v in VOICES.get("voices", {}).items())

_client = None
_static_cache = None  # guardrails + persona (only re-read on restart)

_VOICE_RE = re.compile(r"<<\s*(?:voice|rate|volume)\b.*?>>", re.IGNORECASE | re.DOTALL)
_PERSONA_RE = re.compile(r"<<\s*persona\s*[:=]\s*(.*?)>>", re.IGNORECASE | re.DOTALL)
_ANY_DIRECTIVE_RE = re.compile(r"<<.*?>>", re.DOTALL)


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def _read(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return default


def _system_prompt():
    """Guardrails + persona (cached) + live personality overrides (read fresh each call so voice
    personality changes take effect on the very next reply without a restart)."""
    global _static_cache
    if _static_cache is None:
        guardrails = _read(GUARDRAILS_FILE, "Keep replies short, spoken, and safe.")
        persona = _read(PERSONA_FILE, "You are Benham, a friendly voice in a Discord call.")
        voices = ""
        if NAME_TO_VOICE:
            voices = (
                "\n\n## Available voices (the app switches these)\n"
                "You can switch to any of these named voices with a `<<voice=Name>>` directive, and "
                "if someone asks what voices you have, name them:\n" + voices_blurb()
            )
        _static_cache = guardrails + "\n\n" + persona + voices
    overrides = _read(OVERRIDES_FILE).strip()
    if overrides:
        return _static_cache + "\n\n## Active personality adjustments (user-requested)\n" + overrides
    return _static_cache


def respond(messages):
    """Call the API and return (reply_text_with_directives, usage)."""
    client = _get_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_system_prompt(),
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text, getattr(resp, "usage", None)


def parse_directive(text):
    """Return {voice?, rate?, volume?} from a trailing <<voice=..>> directive, or {} if none."""
    m = _VOICE_RE.search(text or "")
    if not m:
        return {}
    body = m.group(0).strip("<> ")
    out = {}
    for part in re.split(r"[;,]", body):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "voice":
            resolved = resolve_voice(v)
            if resolved:
                out["voice"] = resolved
        elif k in ("rate", "volume"):
            try:
                out[k] = int(re.sub(r"[^\-0-9]", "", v))
            except ValueError:
                pass
    return out


def parse_persona_directive(text):
    """Return the personality trait string from a trailing <<persona: ...>> directive, or None."""
    m = _PERSONA_RE.search(text or "")
    if not m:
        return None
    trait = m.group(1).strip().strip("<> ")
    return trait or None


def strip_directive(text):
    """Remove ALL <<...>> directives so none are spoken aloud."""
    return _ANY_DIRECTIVE_RE.sub("", text or "").strip()
