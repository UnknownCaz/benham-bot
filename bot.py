"""
benham-bot — persistent Discord bot repurposed as a Claude-controlled send tool.

What it does:
  * Connects with the bot token (BOT_KEY in environ.env) and stays online.
  * On login, writes channels.json listing every guild + text channel it can see,
    so we can look up channel IDs to send to.
  * Every ~2s, polls ./outbox for *.json request files of the form
        { "channel_id": <int>, "content": "<text>" }
    sends each one to that channel, then moves the file to outbox/sent/
    (on success) or outbox/failed/ (on error), recording the result.

Reading:
  * Every message the bot can see is appended to inbox.jsonl (one JSON object per line):
        {ts, guild, channel, channel_id, author, author_id, is_self, content, message_id}
    Reading message text requires the privileged Message Content intent (enable it in the
    Discord Developer Portal -> Bot -> Message Content Intent).
  * On-demand backlog: drop a request with "action": "history" (see fetch.py) to pull the
    last N messages of a channel into the result file.

Send a message by dropping a request into ./outbox — use send.py for that.
Read recent messages by tailing inbox.jsonl, or pull backlog with fetch.py.
"""

import os
import re
import sys
import json
import time
import shutil
import logging
import difflib
import asyncio
import tempfile
import threading
import subprocess
import traceback
from datetime import datetime, timezone, timedelta

from collections import deque

import discord
from discord import app_commands
from discord.ext import tasks
from discord.ext import voice_recv
from dotenv import load_dotenv

import agent
import brain
import capabilities
import codesession
import confirm
import exaroton_ops as exa
import identity
import jsonio

try:
    import audioop  # stdlib in 3.12 (removed in 3.13)
except Exception:  # noqa: BLE001
    audioop = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE_DIR, "outbox")
SENT = os.path.join(OUTBOX, "sent")
FAILED = os.path.join(OUTBOX, "failed")
CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")
INBOX_FILE = os.path.join(BASE_DIR, "inbox.jsonl")
VOICE_TRANSCRIPT = os.path.join(BASE_DIR, "voice_transcript.jsonl")

# Canonical wake names. Detection is FUZZY (see is_wake): each word / adjacent word-pair in an
# utterance is compared to these, so Whisper mishears of "Benham" (Bentham, Ben ham, Benum, Bnham,
# Benham) still trigger without maintaining a spelling list. "claude" kept as an alias.
WAKE_WORDS = ["claude", "benham"]
WAKE_FUZZY_THRESHOLD = 0.8  # 0..1 similarity; lower = more lenient (more false positives)
SILENCE_FLUSH_SEC = 0.6   # end an utterance after this much silence from a speaker
MIN_UTTERANCE_SEC = 0.35  # ignore blips shorter than this

# Discord voice receive delivers 48kHz, 16-bit, stereo PCM.
DISCORD_RATE = 48000
DISCORD_WIDTH = 2
DISCORD_CHANNELS = 2
WHISPER_RATE = 16000

_whisper_model = None
_whisper_lock = threading.Lock()
LISTEN_SESSIONS = {}  # guild_id -> {"channel_id": int, "sink": SpeechSink}

load_dotenv(os.path.join(BASE_DIR, "environ.env"))  # must precede env reads below

# --- Autonomous "AUTO_REPLY" mode: the bot answers wake utterances itself via the API brain. ---
# Off by default (set BENHAM_AUTO_REPLY=1 in environ.env) so the free live-Claude loop stays default.
AUTO_REPLY = os.environ.get("BENHAM_AUTO_REPLY", "0") == "1"
CONV_TURNS = 12                 # sliding window of turns kept per guild (bounds input tokens)
REPLY_COOLDOWN_SEC = 1.5        # minimum gap between API calls per guild
# Continuous-conversation window: after a wake word, Benham keeps answering follow-ups WITHOUT the
# name until this many seconds pass with no speech from the conversation partner. 0 = disable
# (wake word required every time). Each engaged utterance re-extends the window.
CONVO_WINDOW_SEC = float(os.environ.get("BENHAM_CONVO_WINDOW_SEC", "25"))
VOICE_SETTINGS_FILE = os.path.join(BASE_DIR, "voice_settings.json")
CONVERSATIONS = {}              # guild_id -> deque of {"role", "content"}
_last_reply_at = {}             # guild_id -> monotonic time of last API reply
_last_wake_text = {}            # guild_id -> last handled text (dedup)
_convo_until = {}               # guild_id -> monotonic deadline the conversation stays open
_convo_speaker = {}             # guild_id -> speaker name the open conversation belongs to

# Whisper 'base' hallucinates these on silence; don't let them keep a conversation window alive.
_NOISE_TEXT = {
    "", "you", "thank you", "thanks", "thanks for watching", "thank you.", "you.",
    "bye", "bye.", "please subscribe", "subscribe", ".", "uh", "um", "hmm",
}


def looks_like_noise(text):
    t = re.sub(r"[^a-z ]", "", (text or "").lower()).strip()
    return t in _NOISE_TEXT or len(t) < 2

_PING_REPLIES = [
    "Yeah, I'm here.", "Still here — what's up?", "Right here. Go ahead.",
    "Yep, listening.", "I'm around. What do you need?",
]
_ping_idx = [0]

for d in (OUTBOX, SENT, FAILED):
    os.makedirs(d, exist_ok=True)

# --- exaroton /server commands + watchdog config (public IDs only; no secrets here) ---
with open(os.path.join(BASE_DIR, "exaroton_watch.json"), encoding="utf-8") as _wf:
    WATCH = json.load(_wf)
GUILD_ID = int(WATCH["guild_id"])
ALERT_CHAN = int(WATCH["alert_channel_id"])
OWNER_IDS = set(WATCH.get("owner_ids", []))
# Guilds where the autonomous voice brain (AUTO_REPLY) may engage. Defaults to the Testing Server
# only, so even with AUTO_REPLY=1 Benham never auto-replies in friend servers (e.g. Chillbar)
# unless a guild id is explicitly added here. Wake detection/transcription still work everywhere;
# this only gates the self-answering path.
AUTO_REPLY_GUILDS = set(int(g) for g in WATCH.get("auto_reply_guilds", [GUILD_ID]))

# --- Per-guild /server command config: a SERVER whitelist per Discord guild (not per user) ---
# command_guilds maps guild_id -> {"servers": "*" | [exaroton ids], "require_operator": bool}.
# From a given Discord guild you can only see/control the listed exaroton servers; require_operator
# decides whether start/stop/restart still need an operator there (owner_ids or a guild admin).
# The /server commands are registered to exactly these guilds. Default: Testing only, all servers,
# operators required.
COMMAND_GUILDS = {
    int(gid): cfg for gid, cfg in
    WATCH.get("command_guilds", {str(GUILD_ID): {"servers": "*", "require_operator": True}}).items()
}


def allowed_servers(guild_id):
    """Set of exaroton server IDs controllable from this Discord guild ("*" => all configured)."""
    cfg = COMMAND_GUILDS.get(guild_id)
    if not cfg:
        return set()
    servers = cfg.get("servers", "*")
    known = set(WATCH["servers"].keys())
    return known if servers == "*" else (set(servers) & known)


def guild_requires_operator(guild_id):
    """Whether start/stop/restart need an operator in this guild (default: yes)."""
    cfg = COMMAND_GUILDS.get(guild_id)
    return bool(cfg.get("require_operator", True)) if cfg else True

# --- DAVE receive-decryption patch (this is what makes voice listening work) ---
# discord.py 2.7 negotiates DAVE (E2E voice encryption) and requires the `davey` lib, but
# discord-ext-voice-recv only does transport decryption — so received frames are still
# DAVE-encrypted and opus fails with "corrupted stream". Older pre-DAVE discord.py can't
# connect anymore (voice gateway rejects: 4006), so downgrading isn't an option either.
# Fix: davey.DaveSession exposes decrypt(user_id, media_type, packet); we wrap voice_recv's
# PacketDecoder._decode_packet to run each frame through the active DAVE session before opus
# decode. Verified working on discord.py 2.7.1 + discord-ext-voice-recv 0.5.2a179 + davey 0.1.6.
import davey as _davey
from discord.ext.voice_recv import opus as _vr_opus

_orig_decode_packet = _vr_opus.PacketDecoder._decode_packet
_SILENCE_FRAME = b"\x00" * 3840  # 20ms of 48kHz stereo 16-bit silence
_dave_err_count = [0]


def _note_dave_err(where, e):
    # Log the first, then every 500th, so persistent failures are visible but not spammy.
    _dave_err_count[0] += 1
    n = _dave_err_count[0]
    if n == 1 or n % 500 == 0:
        print(f"[dave-patch] {where} failed (#{n}): {type(e).__name__}: {e}", flush=True)


def _dave_decode_packet(self, packet):
    # Step 1: DAVE-decrypt the frame in place if a session is active.
    try:
        data = getattr(packet, "decrypted_data", None)
        if packet and data:
            vc = self.router.sink.voice_client
            if vc is not None:
                sess = getattr(vc._connection, "dave_session", None)
                if sess is not None and sess.ready:
                    uid = vc._get_id_from_ssrc(self.ssrc)
                    if uid is not None:
                        packet.decrypted_data = sess.decrypt(
                            uid, _davey.MediaType.audio, data
                        )
    except Exception as e:  # noqa: BLE001
        _note_dave_err("decrypt", e)
    # Step 2: opus-decode, but NEVER raise — a single bad frame (e.g. during a DAVE epoch
    # transition around TTS playback) must not kill voice_recv's packet-router thread, which
    # would silently end all receiving. On failure, return a silent frame and keep going.
    try:
        return _orig_decode_packet(self, packet)
    except Exception as e:  # noqa: BLE001
        _note_dave_err("opus-decode", e)
        return packet, _SILENCE_FRAME


_vr_opus.PacketDecoder._decode_packet = _dave_decode_packet

# Read + write proxy: message_content is privileged — enable it in the Dev Portal
# (Bot -> Privileged Gateway Intents -> Message Content Intent) or on_message text is blank.
intents = discord.Intents.default()
intents.message_content = True

# members/presences are ALSO privileged, and unlike message_content they default OFF
# here on purpose: discord.py refuses to log in at all (PrivilegedIntentsRequired) if
# an intent is requested that the Dev Portal has not granted. Defaulting them on would
# mean this commit bricks the bot on Tyler's next restart until he clicks two toggles.
# Off by default costs only list_members / who_is_online / member-aware role lookups,
# which report a clear "enable the intent" error rather than failing mysteriously.
_INTENT_CFG = identity.CONTROL.get("intents", {}) or {}
intents.members = bool(_INTENT_CFG.get("members", False))
intents.presences = bool(_INTENT_CFG.get("presences", False))

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{stamp}] {msg}"
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001 — logging must never raise, for any reason
        # Discord content and channel names carry emoji, and console_utf8() cannot
        # help if stdout was replaced after startup. Beyond UnicodeEncodeError this
        # also swallows a bogus .encoding codec name (LookupError) and a closed or
        # broken redirect target (ValueError/OSError). A log() that raises inside
        # poll_outbox's try would corrupt that loop's success/failure accounting.
        try:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(line.encode(enc, "backslashreplace").decode(enc, "replace"), flush=True)
        except Exception:  # noqa: BLE001 — give up silently rather than propagate
            pass


def dump_channels():
    """Write channels.json listing all guilds + text channels the bot can see."""
    data = []
    for guild in client.guilds:
        channels = []
        for ch in guild.text_channels:
            channels.append(
                {
                    "name": ch.name,
                    "id": ch.id,
                    "can_send": ch.permissions_for(guild.me).send_messages,
                }
            )
        voice = []
        for vc in guild.voice_channels:
            perms = vc.permissions_for(guild.me)
            voice.append(
                {
                    "name": vc.name,
                    "id": vc.id,
                    "can_join": perms.connect and perms.speak,
                }
            )
        data.append(
            {
                "guild": guild.name,
                "guild_id": guild.id,
                "text_channels": channels,
                "voice_channels": voice,
            }
        )
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log(f"Wrote {CHANNELS_FILE} ({len(data)} guild(s))")
    for g in data:
        log(f"  guild: {g['guild']} ({g['guild_id']}) — {len(g['text_channels'])} text channel(s)")


def record_message(message):
    """Append one message to inbox.jsonl."""
    rec = {
        "ts": message.created_at.isoformat(),
        "guild": message.guild.name if message.guild else None,
        "guild_id": message.guild.id if message.guild else None,
        "channel": getattr(message.channel, "name", str(message.channel)),
        "channel_id": message.channel.id,
        "author": str(message.author),
        "author_id": message.author.id,
        "is_self": message.author.id == (client.user.id if client.user else None),
        "content": message.content,
        "message_id": message.id,
    }
    jsonio.append_jsonl(INBOX_FILE, rec)
    return rec


def synth_tts(text):
    """Render text to an MP3 via edge-tts (natural neural voice). Returns the audio path.
    Voice/rate/volume come from voice_settings.json — voice is an edge-tts voice name."""
    cfg = read_voice_settings()
    voice = cfg.get("voice") or brain.VOICE_MALE
    if voice.startswith("Microsoft"):  # migrate legacy SAPI names on the fly
        voice = brain.VOICE_FEMALE if "Zira" in voice else brain.VOICE_MALE
    rate = f"{int(cfg.get('rate', 0)) * 10:+d}%"          # SAPI -10..10  -> edge -100%..+100%
    volume = f"{int(cfg.get('volume', 100)) - 100:+d}%"   # SAPI 0..100   -> edge -100%..+0%
    tmpdir = tempfile.mkdtemp(prefix="benham_tts_")
    txt_path = os.path.join(tmpdir, "text.txt")
    mp3_path = os.path.join(tmpdir, "speech.mp3")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    subprocess.run(
        [
            sys.executable, "-m", "edge_tts", "--voice", voice,
            "--rate", rate, "--volume", volume,
            "--file", txt_path, "--write-media", mp3_path,
        ],
        check=True,
        capture_output=True,
    )
    return mp3_path


async def speak_in_channel(voice_channel, text):
    """Speak text via SAPI in the voice channel. If a listen session is active there,
    play through the existing connection and stay; otherwise join, speak, and disconnect."""
    wav_path = await asyncio.to_thread(synth_tts, text)
    guild = voice_channel.guild
    listening = guild.id in LISTEN_SESSIONS
    vc = guild.voice_client
    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel.id != voice_channel.id:
        await vc.move_to(voice_channel)
    if vc.is_playing():
        vc.stop()
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    source = discord.FFmpegPCMAudio(wav_path)
    vc.play(source, after=lambda err: loop.call_soon_threadsafe(done.set))
    await done.wait()
    if not listening:
        await vc.disconnect()
    try:
        shutil.rmtree(os.path.dirname(wav_path), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def get_whisper():
    """Lazily load the faster-whisper base model (downloads ~150MB on first use)."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            log("Loading faster-whisper 'base' model (first run downloads it)...")
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
            log("Whisper model ready.")
    return _whisper_model


def pcm_to_whisper_array(pcm_bytes):
    """48kHz stereo 16-bit PCM bytes -> 16kHz mono float32 numpy array for whisper."""
    import numpy as np

    if audioop is not None:
        mono = audioop.tomono(pcm_bytes, DISCORD_WIDTH, 0.5, 0.5)
        mono16k, _ = audioop.ratecv(mono, DISCORD_WIDTH, 1, DISCORD_RATE, WHISPER_RATE, None)
        samples = np.frombuffer(mono16k, dtype=np.int16).astype(np.float32) / 32768.0
        return samples
    # Fallback (no audioop): decimate stereo->mono and 48k->16k crudely.
    stereo = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    stereo = stereo.reshape(-1, 2).mean(axis=1)
    mono16k = stereo[::3]  # 48k -> 16k
    return mono16k / 32768.0


def transcribe_pcm(pcm_bytes):
    """Blocking: convert + transcribe a PCM buffer, return stripped text."""
    audio = pcm_to_whisper_array(pcm_bytes)
    model = get_whisper()
    # beam_size=1 + no prev-text conditioning = lower latency (fine for short commands).
    segments, _info = model.transcribe(
        audio, language="en", vad_filter=True, beam_size=1, condition_on_previous_text=False
    )
    return " ".join(seg.text for seg in segments).strip()


class SpeechSink(voice_recv.AudioSink):
    """Buffers decoded PCM per speaker so the flush loop can transcribe utterances."""

    def __init__(self, guild_id, channel_id):
        super().__init__()
        self.guild_id = guild_id
        self.channel_id = channel_id
        self._buffers = {}  # user_id -> {"buf": bytearray, "last": monotonic, "name": str}
        self._lock = threading.Lock()

    def wants_opus(self):
        return False  # give us decoded PCM

    def write(self, user, data):
        if user is None or not data.pcm:
            return
        with self._lock:
            slot = self._buffers.get(user.id)
            if slot is None:
                slot = {"buf": bytearray(), "last": time.monotonic(), "name": str(user)}
                self._buffers[user.id] = slot
            slot["buf"].extend(data.pcm)
            slot["last"] = time.monotonic()
            slot["name"] = str(user)

    def pop_ready(self, now):
        """Return [(user_id, name, bytes)] for speakers who've gone quiet."""
        ready = []
        bytes_per_sec = DISCORD_RATE * DISCORD_WIDTH * DISCORD_CHANNELS
        with self._lock:
            for uid, slot in list(self._buffers.items()):
                if slot["buf"] and (now - slot["last"]) >= SILENCE_FLUSH_SEC:
                    data = bytes(slot["buf"])
                    slot["buf"] = bytearray()
                    if len(data) >= MIN_UTTERANCE_SEC * bytes_per_sec:
                        ready.append((uid, slot["name"], data))
        return ready

    def cleanup(self):
        with self._lock:
            self._buffers.clear()


def current_voice_name():
    """The B-name of the currently-selected voice (e.g. 'Bruce'), or None if not a roster voice."""
    return brain.VOICE_TO_NAME.get(read_voice_settings().get("voice", ""))


def is_wake(text):
    """Wake detection. Always-active names (WAKE_WORDS, e.g. 'benham') match fuzzily to survive
    Whisper mishears. The currently-selected voice's own name ALSO wakes it (exact whole word),
    so calling 'Bruce' works while Bruce is the active voice, but not otherwise."""
    low = text.lower()
    words = re.findall(r"[a-z']+", low)
    wordset = set(words)

    # The active voice's name is a live trigger — exact whole-word only (precise, no false wakes).
    vn = current_voice_name()
    if vn and vn.lower() in wordset:
        return True

    if any(w in low for w in WAKE_WORDS):
        return True
    # single words plus adjacent pairs joined (so "ben ham" -> "benham")
    candidates = list(words) + ["".join(pair) for pair in zip(words, words[1:])]
    for tok in candidates:
        if len(tok) < 4:  # too short to judge — avoids matching bare "ben", "ben", etc.
            continue
        for w in WAKE_WORDS:
            if difflib.SequenceMatcher(None, tok, w).ratio() >= WAKE_FUZZY_THRESHOLD:
                return True
    return False


def write_voice_transcript(channel_id, speaker, speaker_id, text):
    contains_wake = is_wake(text)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "speaker": speaker,
        "speaker_id": speaker_id,
        "text": text,
        "contains_wake": contains_wake,
    }
    jsonio.append_jsonl(VOICE_TRANSCRIPT, rec)
    tag = "WAKE " if contains_wake else ""
    log(f"{tag}voice <{speaker}>: {text!r}")
    return rec


def read_voice_settings():
    # The fallback voice is an edge-tts id; the old SAPI default ("Microsoft David
    # Desktop") lingered here long after synth_tts moved to edge-tts, and synth_tts
    # had to special-case Microsoft* names to migrate it at call time.
    return jsonio.read_json(VOICE_SETTINGS_FILE,
                            default={"voice": brain.VOICE_MALE, "rate": 0, "volume": 100})


def apply_voice_settings(changes):
    """Merge changes into voice_settings.json (clamped). changes: {voice?, rate?, volume?}."""
    cfg = read_voice_settings()
    if "voice" in changes:
        cfg["voice"] = changes["voice"]
    if "rate" in changes:
        cfg["rate"] = max(-10, min(10, int(changes["rate"])))
    if "volume" in changes:
        cfg["volume"] = max(0, min(100, int(changes["volume"])))
    with open(VOICE_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    log(f"Applied voice settings: {changes}")
    return cfg


PERSONALITY_OVERRIDES_FILE = os.path.join(BASE_DIR, "personality_overrides.txt")


def append_override(trait):
    """Persist a user-requested personality trait (one per line). Takes effect next reply."""
    trait = trait.strip().rstrip(".")
    if not trait:
        return
    with open(PERSONALITY_OVERRIDES_FILE, "a", encoding="utf-8") as f:
        f.write("- " + trait + "\n")
    log(f"Personality override added: {trait!r}")


def reset_overrides():
    """Clear all user personality tweaks, back to the base persona.md.

    Returns True only if the overrides are actually gone. The caller announces the
    reset out loud, so claiming success after a failed remove would tell Tyler the
    personality had been cleared while the old traits were still live.
    """
    try:
        if os.path.exists(PERSONALITY_OVERRIDES_FILE):
            os.remove(PERSONALITY_OVERRIDES_FILE)
    except OSError as e:
        log(f"Personality override reset FAILED (old traits still active): {e}")
        return False
    log("Personality overrides reset")
    return True


_FILLER = {"benham", "claude", "hey", "yo", "hi", "hello", "ok", "okay", "um", "uh", "so", "please"}


def local_shortcut(text):
    """Handle mechanical requests with ZERO API calls. Returns (kind, payload) or None.
    kind: 'sleep' | 'voice' (payload=(changes, confirmation)) | 'ping' (payload=reply) |
    'persona_reset' | 'voices_list'."""
    low = text.lower()
    words = re.findall(r"[a-z']+", low)
    wordset = set(words)
    non_wake = [w for w in words if w not in _FILLER]

    # Sleep / stop / leave — broad, since people phrase this many ways
    if any(p in low for p in (
        "go to sleep", "go back to sleep", "sleep now", "sleep mode", "run your sleep",
        "go to bed", "time for bed", "time to go to bed", "bedtime", "head to bed", "off to bed",
        "night night", "good night", "goodnight", "goodbye benham", "bye benham",
        "stop listening", "stop responding", "quiet down", "be quiet",
        "leave the call", "leave the vc", "leave voice", "leave the channel", "leave the chat",
        "get out of the call", "you can go", "you can leave", "you can head out",
        "disconnect", "log off", "sign off", "power down", "shut down", "dismissed",
    )):
        return ("sleep", None)

    # Reset personality (local, zero API)
    if any(p in low for p in ("reset your personality", "reset personality", "default personality",
                              "normal personality", "be your normal self", "be yourself again",
                              "back to normal", "personality reset")):
        return ("persona_reset", None)

    # "What voices do you have?"
    if any(p in low for p in ("what voices", "which voices", "list voices", "list your voices",
                              "who can you be", "voices can you", "voice options", "what voice can")):
        return ("voices_list", None)

    # Voice / rate / volume — build changes
    cfg = read_voice_settings()
    changes = {}
    confirm = "Okay, how's this?"
    # Switch intent guards voice changes so names/genders in normal chatter don't fire.
    switch_intent = any(p in low for p in ("switch", "change to", "change your voice", "use the",
                                           "use your", "talk as", "talk like", "sound like",
                                           "become", "voice", "accent"))
    name_hit = next((n for n in brain.voice_names() if n.lower() in wordset), None)
    cur_name = current_voice_name()
    # Switch on a switch-intent, or when the utterance is basically just the name — but NOT when it
    # names the current voice (that's the active wake word being used, not a switch request).
    if name_hit and name_hit != cur_name and (switch_intent or len(non_wake) <= 1):
        changes["voice"] = brain.NAME_TO_VOICE[name_hit.lower()]
        confirm = f"Alright, I'm {name_hit} now. How's this?"
    elif switch_intent and (wordset & {"female", "woman", "girl"}):
        changes["voice"] = brain.VOICE_FEMALE
    elif switch_intent and (wordset & {"male", "man", "guy", "dude"}):
        changes["voice"] = brain.VOICE_MALE
    if any(p in low for p in ("slower", "slow down", "too fast")):
        changes["rate"] = int(cfg.get("rate", 0)) - 3
    elif any(p in low for p in ("faster", "speed up", "too slow")):
        changes["rate"] = int(cfg.get("rate", 0)) + 3
    if any(p in low for p in ("louder", "volume up", "speak up", "too quiet", "too soft")):
        changes["volume"] = int(cfg.get("volume", 100)) + 15
    elif any(p in low for p in ("quieter", "softer", "volume down", "too loud", "lower your volume")):
        changes["volume"] = int(cfg.get("volume", 100)) - 15
    if changes:
        return ("voice", (changes, confirm))

    # Trivial ping — short greeting / presence check
    if len(non_wake) <= 2 or any(p in low for p in ("you there", "are you there", "you up",
                                                    "still there", "you awake", "can you hear")):
        r = _PING_REPLIES[_ping_idx[0] % len(_PING_REPLIES)]
        _ping_idx[0] += 1
        return ("ping", r)

    return None


def _open_convo(gid, speaker_id):
    """(Re)open the continuous-conversation window for this speaker.

    Keyed on the Discord user id, not the display name. A display name is
    self-chosen and changeable by anyone, so keying a "no wake word needed" window
    on it meant another member could rename themselves to match and inherit an open
    window. The owner check below makes that moot, but an id is the correct key
    regardless.
    """
    if CONVO_WINDOW_SEC > 0:
        _convo_until[gid] = time.monotonic() + CONVO_WINDOW_SEC
        _convo_speaker[gid] = speaker_id


def convo_active(gid, speaker_id):
    """True if a continuous conversation is open for this speaker (so no wake word needed)."""
    return (
        CONVO_WINDOW_SEC > 0
        and _convo_speaker.get(gid) == speaker_id
        and _convo_until.get(gid, 0.0) > time.monotonic()
    )


async def handle_auto_reply(guild, voice_channel, speaker, speaker_id, text):
    """Respond to a wake/continuation utterance (local shortcut or one API call).

    The owner check is the first statement in this function, deliberately and
    unconditionally. Voice used to be the weak half of Benham: the text path
    checked identity.is_owner in code, while voice relied on a line in
    guardrails.md telling the model that only Tyler was trusted. A prompt is not a
    gate - anyone who could join a voice channel was talking to the brain, and the
    only thing between them and it was the model choosing to behave.

    It sits above the dedup and the local shortcuts, not just above the API call,
    because the shortcuts have real effects too: "go to sleep" disconnects Benham
    and "reset your personality" rewrites his tuning. Those being free of API cost
    never made them free of consequence.

    Note what is NOT gated: transcription. Everything said in the channel still
    reaches voice_transcript.jsonl, so Benham can still tell Tyler what the room
    said. Same shape as the text side - hears everyone, answers one person.
    """
    if not identity.is_owner(speaker_id):
        log(f"voice: ignoring wake from non-owner {speaker} ({speaker_id})")
        return

    gid = guild.id
    if text == _last_wake_text.get(gid):
        return  # dedup Whisper repeats
    _last_wake_text[gid] = text

    shortcut = local_shortcut(text)
    if shortcut is not None:
        kind, payload = shortcut
        if kind == "sleep":
            _convo_until.pop(gid, None)  # close the conversation window
            _convo_speaker.pop(gid, None)
            await speak_in_channel(voice_channel, "Alright, going quiet. Call me if you need me.")
            await stop_listening(guild)
            return
        if kind == "voice":
            changes, confirm = payload
            apply_voice_settings(changes)
            _open_convo(gid, speaker_id)
            await speak_in_channel(voice_channel, confirm)
            return
        if kind == "persona_reset":
            cleared = reset_overrides()
            _open_convo(gid, speaker_id)
            await speak_in_channel(
                voice_channel,
                "Okay, back to my usual self." if cleared
                else "I couldn't clear my personality tweaks - they're still active.",
            )
            return
        if kind == "voices_list":
            names = brain.voice_names()
            spoken = ", ".join(names[:-1]) + (", or " + names[-1] if len(names) > 1 else "")
            _open_convo(gid, speaker_id)
            await speak_in_channel(
                voice_channel, f"I can be {spoken}. Just say switch to one of them.")
            return
        if kind == "ping":
            _open_convo(gid, speaker_id)
            await speak_in_channel(voice_channel, payload)
            return

    # API path — cooldown guard (drop rapid fragments; dedup already ran)
    now = time.monotonic()
    if now - _last_reply_at.get(gid, 0.0) < REPLY_COOLDOWN_SEC:
        return

    conv = CONVERSATIONS.setdefault(gid, deque(maxlen=CONV_TURNS * 2))
    conv.append({"role": "user", "content": f"{speaker}: {text}"})
    try:
        reply, usage = await asyncio.to_thread(brain.respond, list(conv))
    except Exception:  # noqa: BLE001
        log("Brain error:\n" + traceback.format_exc())
        conv.pop()  # don't keep a user turn we never answered
        return
    _last_reply_at[gid] = time.monotonic()
    _open_convo(gid, speaker_id)  # keep the conversation open for follow-ups without the name

    changes = brain.parse_directive(reply)
    if changes:
        apply_voice_settings(changes)
    trait = brain.parse_persona_directive(reply)
    if trait:
        append_override(trait)  # takes effect on the next reply
    leaving = brain.wants_sleep(reply)
    spoken = brain.strip_directive(reply)
    conv.append({"role": "assistant", "content": spoken or reply})

    if usage is not None:
        log(f"brain: in={getattr(usage,'input_tokens','?')} out={getattr(usage,'output_tokens','?')} "
            f"reply={spoken!r}")
    if spoken:
        await speak_in_channel(voice_channel, spoken)
    if leaving:  # model chose to leave via <<sleep>>
        _convo_until.pop(gid, None)
        _convo_speaker.pop(gid, None)
        await stop_listening(guild)


@tasks.loop(seconds=0.4)
async def flush_utterances():
    if not LISTEN_SESSIONS:
        return
    now = time.monotonic()
    for guild_id, session in list(LISTEN_SESSIONS.items()):
        sink = session["sink"]
        for uid, name, pcm in sink.pop_ready(now):
            try:
                text = await asyncio.to_thread(transcribe_pcm, pcm)
            except Exception:  # noqa: BLE001
                log("Transcription error:\n" + traceback.format_exc())
                continue
            if not text:
                continue
            rec = write_voice_transcript(session["channel_id"], name, uid, text)
            if not AUTO_REPLY:
                continue
            vc_channel = client.get_channel(session["channel_id"])
            if vc_channel is None:
                continue
            gid = vc_channel.guild.id
            if gid not in AUTO_REPLY_GUILDS:
                continue  # autonomous replies only in allowlisted guilds (default: Testing only)
            # Engage if the name was said, OR a conversation is already open for this speaker
            # (continuous mode) and the utterance isn't obvious silence-hallucination noise.
            engage = rec["contains_wake"] or (convo_active(gid, uid) and not looks_like_noise(text))
            if engage:
                try:
                    await handle_auto_reply(vc_channel.guild, vc_channel, name, uid, text)
                except Exception:  # noqa: BLE001
                    log("Auto-reply error:\n" + traceback.format_exc())


async def start_listening(voice_channel):
    """Join the voice channel with a receiving client and begin transcribing speech."""
    get_whisper()  # load model up front so the first utterance isn't delayed
    guild = voice_channel.guild
    vc = guild.voice_client
    # Need a VoiceRecvClient; if a plain client is connected, reconnect as a receiver.
    if vc is not None and not isinstance(vc, voice_recv.VoiceRecvClient):
        await vc.disconnect()
        vc = None
    if vc is None:
        vc = await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
    elif vc.channel.id != voice_channel.id:
        await vc.move_to(voice_channel)
    if vc.is_listening():
        vc.stop_listening()
    sink = SpeechSink(guild.id, voice_channel.id)
    vc.listen(sink)
    LISTEN_SESSIONS[guild.id] = {"channel_id": voice_channel.id, "sink": sink}


async def stop_listening(guild):
    """Stop transcribing and leave the voice channel in this guild."""
    LISTEN_SESSIONS.pop(guild.id, None)
    vc = guild.voice_client
    if vc is not None:
        if isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_listening():
            vc.stop_listening()
        await vc.disconnect()


@flush_utterances.before_loop
async def before_flush():
    await client.wait_until_ready()


# ===================== exaroton: /server slash commands + watchdog =====================
# Pure ops layer over the exaroton skill (imported as `exa`). A /server command group
# (status = anyone; start/stop/restart = operators) plus a background watchdog loop that
# alerts on crash / unexpected-offline / back-online for servers flagged watch:true in
# exaroton_watch.json. No MOTD / world / whitelist / naming changes — Tyler keeps those.

# Emoji per status NAME, not per numeric code. The exaroton skill already owns the
# code -> name table; spelling the 11 codes out a third time here meant three copies
# that had to be kept in step by hand.
_STATUS_EMOJI_BY_NAME = {
    "OFFLINE": "⚪", "ONLINE": "🟢", "STARTING": "🟡", "STOPPING": "🟠",
    "RESTARTING": "🔵", "SAVING": "💾", "LOADING": "⏳", "CRASHED": "🔴",
    "PENDING": "⏳", "TRANSFERRING": "🔀", "PREPARING": "⏳",
}


def status_badge(code):
    """'🟢 online' for a status code, or a readable fallback for an unknown one."""
    name = exa.status_label(code)          # single source of truth, from the skill
    emoji = _STATUS_EMOJI_BY_NAME.get(name, "❔")
    # CRASHED stays shouty; everything else reads better lowercase.
    return f"{emoji} {name if name == 'CRASHED' else name.lower()}"

# --- watchdog state (module-level) ---
_wd_last_status = {}    # sid -> int last-seen status code (absent until primed)
_wd_last_alert = {}     # (sid, event) -> monotonic ts, for per-event cooldown
_wd_empty_since = {}    # sid -> monotonic ts first seen empty while online
_wd_expected_stop = {}  # sid -> monotonic ts of an operator/auto stop (suppresses "down" alert)
_wd_primed = {"done": False}
_wd_poll_count = {"n": 0}


def _server_label(sid):
    return WATCH["servers"].get(sid, {}).get("name", sid)


def is_operator(interaction):
    """Operator = explicit owner_ids allowlist OR Discord admin/manage_guild in this guild.
    (exaroton has no notion of Discord roles, so guild-admin is the practical stand-in.)"""
    if interaction.user.id in OWNER_IDS:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_guild))


async def server_autocomplete(interaction: discord.Interaction, current: str):
    cur = (current or "").lower()
    allow = allowed_servers(interaction.guild_id)  # only servers this Discord guild may control
    out = [
        app_commands.Choice(name=cfg["name"], value=sid)
        for sid, cfg in WATCH["servers"].items()
        if sid in allow and cur in cfg["name"].lower()
    ]
    return out[:25]


server_group = app_commands.Group(
    name="server", description="Control Tyler's exaroton Minecraft servers")


@server_group.command(name="status", description="Show a server's current status")
@app_commands.describe(server="Which server")
@app_commands.autocomplete(server=server_autocomplete)
async def server_status(interaction: discord.Interaction, server: str):
    if server not in allowed_servers(interaction.guild_id):
        await interaction.response.send_message(
            "That server can't be controlled from this Discord server.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        d = await exa.one_server(server)
    except Exception as e:  # noqa: BLE001
        await interaction.followup.send(f"⚠️ exaroton error: {e}")
        return
    st = status_badge(d.get("status"))
    players = d.get("players") or {}
    who = ""
    if d.get("status") == 1:
        who = f" — {players.get('count', 0)}/{players.get('max', '?')} online"
    await interaction.followup.send(f"**{d.get('name', _server_label(server))}**: {st}{who}")


async def _power_command(interaction, server, verb, fn):
    gid = interaction.guild_id
    # Primary gate is the per-guild SERVER whitelist: you can only control servers this Discord
    # guild is allowed to (also blocks anything not in exaroton_watch.json).
    if server not in allowed_servers(gid):
        await interaction.response.send_message(
            f"**{_server_label(server)}** can't be controlled from this Discord server.",
            ephemeral=True)
        return
    # Then, only where the guild requires it, also require an operator (owner_ids or a guild admin).
    if guild_requires_operator(gid) and not is_operator(interaction):
        await interaction.response.send_message(
            "⛔ Only Tyler or a server admin can do that here.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    if verb in ("stop", "restart"):
        _wd_expected_stop[server] = time.monotonic()  # suppress the watchdog "down" alert
    try:
        await fn(server)
    except Exception as e:  # noqa: BLE001
        _wd_expected_stop.pop(server, None)
        await interaction.followup.send(f"⚠️ exaroton error: {e}")
        return
    await interaction.followup.send(f"✅ **{verb}** sent to **{_server_label(server)}**.")
    log(f"/server {verb} {server} by {interaction.user} ({interaction.user.id})")


@server_group.command(name="start", description="Start a server (operators only)")
@app_commands.describe(server="Which server")
@app_commands.autocomplete(server=server_autocomplete)
async def server_start_cmd(interaction: discord.Interaction, server: str):
    await _power_command(interaction, server, "start", exa.start)


@server_group.command(name="stop", description="Stop a server (operators only)")
@app_commands.describe(server="Which server")
@app_commands.autocomplete(server=server_autocomplete)
async def server_stop_cmd(interaction: discord.Interaction, server: str):
    await _power_command(interaction, server, "stop", exa.stop)


@server_group.command(name="restart", description="Restart a server (operators only)")
@app_commands.describe(server="Which server")
@app_commands.autocomplete(server=server_autocomplete)
async def server_restart_cmd(interaction: discord.Interaction, server: str):
    await _power_command(interaction, server, "restart", exa.restart)


tree.add_command(server_group)


async def send_alert(text):
    """Post a watchdog alert to the configured channel (same resolution poll_outbox uses)."""
    ch = client.get_channel(ALERT_CHAN)
    if ch is None:
        ch = await client.fetch_channel(ALERT_CHAN)
    await ch.send(text)


async def _wd_emit(sid, event, text):
    """Send an alert, gated by a per-(server,event) cooldown to absorb flapping."""
    now = time.monotonic()
    if now - _wd_last_alert.get((sid, event), 0.0) < WATCH.get("alert_cooldown_seconds", 300):
        return
    _wd_last_alert[(sid, event)] = now
    try:
        await send_alert(text)
    except Exception:  # noqa: BLE001
        log("[watchdog] alert send failed:\n" + traceback.format_exc())


@tasks.loop(seconds=WATCH.get("poll_seconds", 30))
async def exaroton_watchdog():
    now = time.monotonic()
    try:
        servers = await exa.all_servers()          # ONE call covers every watched server
    except Exception as e:  # noqa: BLE001 — transient blip: skip cycle, don't alert-storm
        log(f"[watchdog] poll failed: {e}")
        return
    by_id = {s.get("id"): s for s in servers}
    for sid, cfg in WATCH["servers"].items():
        if not cfg.get("watch"):
            continue
        s = by_id.get(sid)
        if not s:
            continue
        new = s.get("status")
        prev = _wd_last_status.get(sid)
        players = (s.get("players") or {}).get("count", 0)

        # Alert only after priming (first poll just seeds state -> no boot-time storm).
        if _wd_primed["done"] and prev is not None and new != prev:
            expected_fresh = (now - _wd_expected_stop.get(sid, 0.0)) < 120
            if new == 7 and prev != 7:
                await _wd_emit(sid, "crashed", f"🔴 **{cfg['name']}** CRASHED")
            elif new == 0 and prev in (1, 2, 6):
                if expected_fresh:
                    _wd_expected_stop.pop(sid, None)   # clean/operator stop -> silent
                else:
                    await _wd_emit(sid, "down", f"🟠 **{cfg['name']}** went offline unexpectedly")
            elif new == 1 and prev != 1:
                await _wd_emit(sid, "up", f"🟢 **{cfg['name']}** is online")

        # optional auto-stop-when-empty (off by default in config)
        if cfg.get("auto_stop_empty") and new == 1:
            if players == 0:
                _wd_empty_since.setdefault(sid, now)
                if now - _wd_empty_since[sid] >= cfg.get("empty_grace_seconds", 900):
                    _wd_expected_stop[sid] = now
                    try:
                        await exa.stop(sid)
                        await send_alert(f"ℹ️ **{cfg['name']}** auto-stopped (empty, saving credits)")
                    except Exception as e:  # noqa: BLE001
                        log(f"[watchdog] auto-stop failed: {e}")
                    _wd_empty_since.pop(sid, None)
            else:
                _wd_empty_since.pop(sid, None)

        _wd_last_status[sid] = new

    _wd_poll_count["n"] += 1
    if _wd_poll_count["n"] % WATCH.get("credit_check_every", 20) == 0:
        try:
            c = await exa.credits()
            if c < WATCH.get("credit_floor", 50):
                await _wd_emit("__acct__", "lowcredit", f"⚠️ exaroton credits low: {c:.1f}")
        except Exception as e:  # noqa: BLE001 — a blip must not kill the watchdog loop
            # Silently swallowing this meant a broken credits endpoint disabled the
            # low-credit alarm entirely, with nothing to show the alarm had stopped
            # working. Log it; the next cycle retries anyway.
            log(f"[watchdog] credit check failed (low-credit alarm not evaluated): {e}")

    _wd_primed["done"] = True


@exaroton_watchdog.before_loop
async def before_watchdog():
    await client.wait_until_ready()

# =================== end exaroton /server + watchdog block ===================


@client.event
async def on_ready():
    log(f"Logged in as {client.user} (id {client.user.id})")
    log(f"AUTO_REPLY mode: {'ON (autonomous API brain, ' + brain.MODEL + ')' if AUTO_REPLY else 'OFF (live-Claude loop)'}")
    if AUTO_REPLY:
        log(f"AUTO_REPLY allowed guilds: {sorted(AUTO_REPLY_GUILDS)} (autonomous replies gated to these)")

    # --- control plane (identity.py / control.json) ---
    log(f"Owner(s): {sorted(identity.OWNER_IDS)} — Benham takes direction from these only")
    log(f"Text agent: {'ON (' + agent.MODEL + ')' if agent.ENABLED else 'OFF (relay only)'}"
        f", agent guilds {sorted(identity.AGENT_GUILDS)} (+ owner DMs always)")
    log(f"Destructive actions allowed in guilds: {sorted(identity.DESTRUCTIVE_GUILDS) or 'NONE'}")
    log(f"Capabilities registered: {len(capabilities.REGISTRY)} "
        f"({', '.join(f'{identity.TIER_NAMES[t]}={sum(1 for a in capabilities.REGISTRY.values() if a.tier == t)}' for t in (0, 1, 2, 3))})")
    if not intents.members:
        log("Note: Server Members intent OFF — list_members/who_is_online will report "
            "that it needs enabling (Dev Portal → Bot → Privileged Gateway Intents)")

    # Wire the PC session's permission gate to a DM. A Claude Code tool call that
    # needs approval suspends until this round-trips, so it has to reach Tyler
    # wherever he is - hence a DM rather than a reply in whatever channel started it.
    codesession.configure(log, ask_owner_dm)
    log(f"PC access: {'ON — workdir ' + codesession.WORKDIR if codesession.ENABLED else 'OFF'}"
        + (f", writes/commands ask (timeout {codesession.PERMISSION_TIMEOUT}s)"
           if codesession.ENABLED else ""))

    pres = identity.CONTROL.get("presence", {}) or {}
    if pres:
        try:
            await capabilities.run(client, log, "set_presence", pres, force=True)
        except Exception:  # noqa: BLE001 — cosmetic; never block startup
            log(f"presence setup failed:\n{traceback.format_exc()}")

    dump_channels()
    if not poll_outbox.is_running():
        poll_outbox.start()
    if not flush_utterances.is_running():
        flush_utterances.start()
    # Register /server to each command guild. The group is added GLOBALLY (tree.add_command), so we
    # copy globals into each guild and sync that guild — guild-scoped syncs appear instantly, and
    # (unlike the old code, which synced the empty guild scope) this actually registers the commands.
    for gid in COMMAND_GUILDS:
        try:
            gobj = discord.Object(id=gid)
            tree.clear_commands(guild=gobj)       # idempotent across reconnects
            tree.copy_global_to(guild=gobj)       # copy the /server group into this guild
            synced = await tree.sync(guild=gobj)
            log(f"Synced {len(synced)} command(s) to guild {gid}: {[c.name for c in synced]}")
        except Exception:  # noqa: BLE001
            log(f"Slash command sync failed for guild {gid}:\n" + traceback.format_exc())
    if not exaroton_watchdog.is_running():
        exaroton_watchdog.start()


DISCORD_MSG_LIMIT = 2000


def split_for_discord(text, limit=DISCORD_MSG_LIMIT):
    """Split a reply into Discord-sized chunks, preferring paragraph then line breaks.

    A reply that exceeds the limit raises HTTPException and is lost entirely, which
    for the agent path means the API call was paid for and produced nothing.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks, rest = [], text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def reply_in(channel, text):
    """Send a possibly-long reply, in order."""
    for chunk in split_for_discord(text):
        await channel.send(chunk)


async def ask_owner_dm(text):
    """DM the owner. Used by the PC session's permission gate.

    Raises rather than swallowing a failure: codesession treats an unreachable
    owner as a denial, and it can only do that if it finds out.
    """
    owner_id = sorted(identity.OWNER_IDS)[0]
    user = client.get_user(owner_id) or await client.fetch_user(owner_id)
    channel = user.dm_channel or await user.create_dm()
    await reply_in(channel, text)


async def fire_confirmed(pending, channel):
    """Run a confirmed destructive action. Never reached from inside the agent loop.

    This is deliberately a plain function called straight from on_message: Tyler's
    "yes" is read by code, matched to a parked token, and executed here. The model
    never sees the confirmation and never gets to produce one, so no message content
    anywhere - his, someone else's, or a channel Benham read - can talk its way into
    firing a delete.
    """
    try:
        result, _ = await capabilities.run(
            client, log, pending.action, pending.params,
            actor_id=pending.requested_by, force=True)
        log(f"CONFIRMED {pending.action} (token {pending.token}) by {pending.requested_by}: {result}")
        await reply_in(channel, f"Done — `{pending.action}`: {json.dumps(result, default=str)}")
    except capabilities.ActionError as e:
        await reply_in(channel, f"Couldn't do it: {e}")
    except Exception as e:  # noqa: BLE001
        log(f"CONFIRMED action {pending.action} crashed:\n{traceback.format_exc()}")
        await reply_in(channel, f"That failed: {type(e).__name__}: {e}")


def strip_mention(message):
    """The message text with Benham's own mention removed."""
    text = message.content or ""
    if client.user:
        for form in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
            text = text.replace(form, " ")
    return text.strip()


@client.event
async def on_message(message):
    rec = record_message(message)
    if rec["is_self"]:
        return
    log(f"inbox #{rec['channel']} <{rec['author']}>: {rec['content']!r}")

    is_dm = message.guild is None
    mentioned = bool(client.user and client.user in message.mentions)
    if not (is_dm or mentioned):
        return  # Benham reads everything, but only engages when addressed.

    # The owner gate. Everyone else can talk TO Benham; nobody else directs it.
    if not identity.is_owner(message.author.id):
        log(f"ignoring direction from non-owner {message.author} ({message.author.id})")
        if is_dm:
            await reply_in(message.channel, identity.refusal(message.author.id))
        return

    text = strip_mention(message)
    if not text:
        return

    # A blocked PC permission request outranks everything: a Claude Code session is
    # suspended mid-tool waiting on this exact reply, and routing it to the agent
    # instead would leave that session hanging until it timed out.
    rid = codesession.pending_request()
    if rid:
        verdict, _ = confirm.read_reply(text)
        if verdict in ("yes", "no"):
            codesession.answer(rid, verdict == "yes")
            await reply_in(message.channel,
                           "Running it now." if verdict == "yes" else "Skipping that.")
            return

    # Confirmation is checked BEFORE the agent, and resolved without it. A pending
    # action only exists in the seconds after a preview, so an "ok" in ordinary
    # conversation falls through to the agent as normal.
    pending = confirm.current()
    if pending is not None:
        verdict, token = confirm.read_reply(text)
        target = confirm.get(token) if token else pending
        if verdict == "yes" and target is not None:
            confirm.consume(target.token)
            await fire_confirmed(target, message.channel)
            return
        if verdict == "no":
            log(f"DECLINED {pending.action} (token {pending.token}) by {message.author.id}")
            confirm.cancel()
            await reply_in(message.channel, "Cancelled — nothing was touched.")
            return

    if not agent.ENABLED:
        return

    where = "a DM" if is_dm else f"#{message.channel} in {message.guild.name}"
    key = f"dm:{message.author.id}" if is_dm else f"ch:{message.channel.id}"
    try:
        async with message.channel.typing():
            reply, parked = await agent.respond(
                client, log, text,
                actor_id=message.author.id, actor_name=str(message.author),
                channel_id=message.channel.id,
                guild_id=message.guild.id if message.guild else None,
                where=where, conversation_key=key)
    except Exception as e:  # noqa: BLE001 — a brain failure must not kill the bot
        log(f"agent failed:\n{traceback.format_exc()}")
        await reply_in(message.channel, f"My brain threw an error: {type(e).__name__}: {e}")
        return

    if reply:
        await reply_in(message.channel, reply)
    if parked is not None:
        await reply_in(message.channel, confirm.describe(parked))


@tasks.loop(seconds=2)
async def poll_outbox():
    try:
        files = sorted(f for f in os.listdir(OUTBOX) if f.endswith(".json"))
    except FileNotFoundError:
        return
    for fname in files:
        path = os.path.join(OUTBOX, fname)
        result = {"processed_at": datetime.now(timezone.utc).isoformat()}
        # Set the moment an action's irreversible side effect has fired (the message
        # is sent, the purge has run). Everything after that point -- archiving, and
        # especially the success log() -- is bookkeeping that must never be able to
        # turn a completed action into a FAILED one.
        action_done = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                req = json.load(f)
            action = req.get("action", "send")

            # --- capability-registry actions (capabilities.py) ---
            # The legacy names below (send/dm/speak/edit/delete/history/purge) predate
            # the registry and keep their own handling; everything added since is
            # declared once in capabilities.py and dispatched here. The two name sets
            # are disjoint (send vs send_message, purge vs purge_messages), so an old
            # request file still routes exactly where it always did.
            if action in capabilities.REGISTRY:
                act = capabilities.REGISTRY[action]
                params = {k: v for k, v in req.items()
                          if k not in ("action", "queued_at", "confirm_token", "actor_id")}
                token = req.get("confirm_token")

                if act.destructive and not token:
                    # Step one of two. Nothing is touched: this runs the dry-run,
                    # parks the real parameters, and hands back a token. The caller
                    # re-submits with that token to actually fire it. Same "no inline
                    # shortcut" rule the DM path follows, in the machine channel.
                    _, preview = await capabilities.run(
                        client, log, action, params,
                        actor_id=req.get("actor_id"), force=False)
                    parked = confirm.park(action, params, preview,
                                          req.get("actor_id"), "outbox")
                    result.update({"status": "confirmation_required", "request": req,
                                   "action": action, "confirm_token": parked.token,
                                   "preview": preview,
                                   "expires_in_seconds": parked.seconds_left})
                    # No side effect fired, but this request file is resolved — the
                    # flag exists to stop the handler below from _finish-ing twice.
                    action_done = True
                    _finish(path, fname, SENT, result)
                    log(f"{action}: dry-run only, confirm with token {parked.token}")
                    continue

                if act.destructive:
                    parked = confirm.consume(token)
                    if parked is None:
                        raise ValueError(
                            f"confirm_token {token!r} is unknown or expired. Expiry means "
                            "cancelled — re-submit without a token for a fresh dry-run.")
                    if parked.action != action:
                        raise ValueError(
                            f"confirm_token {token!r} was issued for `{parked.action}`, "
                            f"not `{action}`")
                    params = parked.params  # fire exactly what was previewed, not a re-read

                res, _ = await capabilities.run(
                    client, log, action, params,
                    actor_id=req.get("actor_id"), force=True)
                result.update({"status": "ok", "action": action,
                               "request": req, "result": res})
                action_done = True
                _finish(path, fname, SENT, result)
                continue

            if action == "dm":
                # A DM request carries user_id, not channel_id: resolve the user's
                # private channel and create it if this is the first message.
                # Discord only permits this for a user who shares a guild with the
                # bot and has not blocked DMs from server members.
                user_id = int(req["user_id"])
                user = client.get_user(user_id) or await client.fetch_user(user_id)
                channel = user.dm_channel or await user.create_dm()
                channel_id = channel.id
                # Falls through to the same send path as a channel message below.
            else:
                channel_id = int(req["channel_id"])
                channel = client.get_channel(channel_id)
                if channel is None:
                    channel = await client.fetch_channel(channel_id)

            if action == "listen":
                await start_listening(channel)
                result.update({"status": "listening", "request": req, "channel": str(channel)})
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Listening in #{getattr(channel, 'name', channel_id)}")
            elif action == "stop_listen":
                await stop_listening(channel.guild)
                result.update({"status": "stopped_listening", "request": req, "channel": str(channel)})
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Stopped listening in #{getattr(channel, 'name', channel_id)}")
            elif action == "speak":
                text = str(req["content"])
                await speak_in_channel(channel, text)
                result.update(
                    {"status": "spoke", "request": req, "channel": str(channel)}
                )
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Spoke in #{getattr(channel, 'name', channel_id)}: {text!r}")
            elif action == "edit":
                message_id = int(req["message_id"])
                content = str(req["content"])
                msg = await channel.fetch_message(message_id)
                await msg.edit(content=content)
                result.update(
                    {
                        "status": "edited",
                        "request": req,
                        "message_id": message_id,
                        "channel": str(channel),
                    }
                )
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Edited message {message_id} in #{getattr(channel, 'name', channel_id)}")
            elif action == "delete":
                message_id = int(req["message_id"])
                msg = await channel.fetch_message(message_id)
                await msg.delete()
                result.update(
                    {
                        "status": "deleted",
                        "request": req,
                        "message_id": message_id,
                        "channel": str(channel),
                    }
                )
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Deleted message {message_id} in #{getattr(channel, 'name', channel_id)}")
            elif action == "history":
                limit = int(req.get("limit", 20))
                msgs = []
                async for m in channel.history(limit=limit):
                    msgs.append(
                        {
                            "ts": m.created_at.isoformat(),
                            "author": str(m.author),
                            "author_id": m.author.id,
                            "content": m.content,
                            "message_id": m.id,
                        }
                    )
                msgs.reverse()  # oldest -> newest
                result.update(
                    {"status": "fetched", "request": req, "channel": str(channel), "messages": msgs}
                )
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Fetched {len(msgs)} msg(s) from #{getattr(channel, 'name', channel_id)}")
            elif action == "purge":
                # Delete messages older than `older_than_days` (default 7).
                # scope: "channel" (default) purges just this channel;
                #        "guild" sweeps every text channel in the guild.
                days = int(req.get("older_than_days", 7))
                scope = req.get("scope", "channel")
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                if scope == "guild" and getattr(channel, "guild", None) is not None:
                    targets = list(channel.guild.text_channels)
                else:
                    targets = [channel]
                per_channel = {}
                total = 0
                errors = {}
                for ch in targets:
                    try:
                        # discord.py bulk-deletes messages < 14 days old and
                        # falls back to individual deletes for older ones.
                        deleted = await ch.purge(limit=None, before=cutoff, bulk=True)
                        per_channel[f"#{ch.name}"] = len(deleted)
                        total += len(deleted)
                        log(f"Purged {len(deleted)} msg(s) older than {days}d from #{ch.name}")
                    except discord.Forbidden:
                        errors[f"#{ch.name}"] = "missing Manage Messages permission"
                        log(f"PURGE forbidden in #{getattr(ch,'name','?')} (need Manage Messages)")
                    except Exception as e:  # noqa: BLE001
                        errors[f"#{ch.name}"] = str(e)
                        log(f"PURGE error in #{getattr(ch,'name','?')}: {e}")
                result.update({
                    "status": "purged",
                    "request": req,
                    "scope": scope,
                    "older_than_days": days,
                    "cutoff": cutoff.isoformat(),
                    "deleted_total": total,
                    "deleted_by_channel": per_channel,
                    "errors": errors,
                })
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Purge complete: {total} msg(s) deleted, errors={errors}")
            else:
                content = str(req["content"])
                sent = await channel.send(content)
                result.update(
                    {
                        "status": "sent",
                        "request": req,
                        "message_id": sent.id,
                        "channel": str(channel),
                    }
                )
                action_done = True
                _finish(path, fname, SENT, result)
                log(f"Sent to #{getattr(channel, 'name', channel_id)}: {content!r}")
        except Exception as e:  # noqa: BLE001 — record everything, keep the loop alive
            if action_done:
                # The action already happened on Discord's side. Re-filing it as
                # FAILED would lie to the caller, and calling _finish again would
                # either double-archive or clobber the result. Deliberately keyed
                # on the side effect, not on whether the request file still exists:
                # _finish can fail to move it, which would leave the file in place
                # after a genuinely completed action.
                log(f"Post-completion error on {fname} (action already done): "
                    f"{type(e).__name__}: {e}")
                log(traceback.format_exc())
                continue
            result.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
            log(f"FAILED {fname}: {result['error']}")
            log(traceback.format_exc())
            _finish(path, fname, FAILED, result)


def _finish(path, fname, dest_dir, result):
    """Move the request file to dest_dir and write a sibling _result.json.

    Returns True only when BOTH halves succeeded. The two failures are reported
    separately on purpose: a failed move leaves the request sitting in the outbox
    to be picked up again, while a failed result-write leaves the caller polling
    for a result that will never appear. Collapsing them into one silent handler
    made a half-archived request indistinguishable from a clean one.
    """
    base = os.path.splitext(fname)[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_dir, f"{base}_{stamp}.json")
    try:
        shutil.move(path, dest)
    except Exception:  # noqa: BLE001
        log(f"ARCHIVE FAILED {fname}:\n" + traceback.format_exc())
        # Quarantine it. The poller globs "*.json", so leaving a request whose
        # action already fired sitting in the outbox means the next cycle -- two
        # seconds later -- re-sends the message or re-runs the purge, forever.
        # Renaming out of the glob costs one syscall and bounds the damage at one
        # duplicate; the payload is preserved for a human to inspect.
        try:
            os.replace(path, path + ".stuck")
            log(f"Quarantined {fname} -> {fname}.stuck (its action already ran)")
        except Exception:  # noqa: BLE001
            log(f"COULD NOT QUARANTINE {fname} -- it WILL be reprocessed:\n"
                + traceback.format_exc())
        return False
    try:
        with open(os.path.splitext(dest)[0] + "_result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception:  # noqa: BLE001
        log(f"ARCHIVED BUT NO RESULT FILE {fname} -> {dest}:\n" + traceback.format_exc())
        return False
    return True


@poll_outbox.before_loop
async def before_poll():
    await client.wait_until_ready()


def console_utf8():
    """Force UTF-8 on the console streams, with a replacing fallback.

    supervise_bot.bat redirects stdout to a file, and on Windows that makes
    Python pick cp1252 -- which cannot encode the emoji that routinely appear in
    Discord channel names and message content. Without this, printing them raises
    UnicodeEncodeError deep inside the outbox loop.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def configure_logging():
    """Route library logging through one UTC format matching log()'s own lines."""
    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%SZ",
    )
    formatter.converter = time.gmtime          # log() stamps UTC; match it
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def main():
    console_utf8()
    token = os.environ.get("BOT_KEY")
    if not token:
        raise SystemExit("BOT_KEY not set — put it in environ.env as BOT_KEY=<token>")
    configure_logging()
    # log_handler=None stops discord.py installing a handler of its own. Its records
    # still propagate to the root handler set up above, so the gateway diagnostics
    # (connects, RESUMEs, voice 4006s) are kept -- they matter for a process that
    # runs for days -- but now share one timestamp format with log() instead of
    # interleaving a second one in the same file.
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
