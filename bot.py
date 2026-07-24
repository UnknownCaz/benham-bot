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
import difflib
import asyncio
import tempfile
import threading
import subprocess
import traceback
from datetime import datetime, timezone

from collections import deque

import discord
from discord.ext import tasks
from discord.ext import voice_recv
from dotenv import load_dotenv

import brain

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
TTS_SCRIPT = os.path.join(BASE_DIR, "tts.ps1")

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
client = discord.Client(intents=intents)


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


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
    with open(INBOX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
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
    with open(VOICE_TRANSCRIPT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tag = "WAKE " if contains_wake else ""
    log(f"{tag}voice <{speaker}>: {text!r}")
    return rec


def read_voice_settings():
    try:
        with open(VOICE_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"voice": "Microsoft David Desktop", "rate": 0, "volume": 100}


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
    """Clear all user personality tweaks, back to the base persona.md."""
    try:
        if os.path.exists(PERSONALITY_OVERRIDES_FILE):
            os.remove(PERSONALITY_OVERRIDES_FILE)
    except Exception:  # noqa: BLE001
        pass
    log("Personality overrides reset")


_FILLER = {"benham", "claude", "hey", "yo", "hi", "hello", "ok", "okay", "um", "uh", "so", "please"}


def local_shortcut(text):
    """Handle mechanical requests with ZERO API calls. Returns (kind, payload) or None.
    kind: 'sleep' | 'voice' (payload=(changes, confirmation)) | 'ping' (payload=reply) |
    'persona_reset' | 'voices_list'."""
    low = text.lower()
    words = re.findall(r"[a-z']+", low)
    wordset = set(words)
    non_wake = [w for w in words if w not in _FILLER]

    # Sleep / stop
    if any(p in low for p in ("go to sleep", "stop listening", "leave the call",
                              "you can go", "goodbye benham", "good night benham", "disconnect")):
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


def _open_convo(gid, speaker):
    """(Re)open the continuous-conversation window for this speaker."""
    if CONVO_WINDOW_SEC > 0:
        _convo_until[gid] = time.monotonic() + CONVO_WINDOW_SEC
        _convo_speaker[gid] = speaker


def convo_active(gid, speaker):
    """True if a continuous conversation is open for this speaker (so no wake word needed)."""
    return (
        CONVO_WINDOW_SEC > 0
        and _convo_speaker.get(gid) == speaker
        and _convo_until.get(gid, 0.0) > time.monotonic()
    )


async def handle_auto_reply(guild, voice_channel, speaker, text):
    """Respond to a wake/continuation utterance (local shortcut or one API call)."""
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
            _open_convo(gid, speaker)
            await speak_in_channel(voice_channel, confirm)
            return
        if kind == "persona_reset":
            reset_overrides()
            _open_convo(gid, speaker)
            await speak_in_channel(voice_channel, "Okay, back to my usual self.")
            return
        if kind == "voices_list":
            names = brain.voice_names()
            spoken = ", ".join(names[:-1]) + (", or " + names[-1] if len(names) > 1 else "")
            _open_convo(gid, speaker)
            await speak_in_channel(
                voice_channel, f"I can be {spoken}. Just say switch to one of them.")
            return
        if kind == "ping":
            _open_convo(gid, speaker)
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
    _open_convo(gid, speaker)  # keep the conversation open for follow-ups without the name

    changes = brain.parse_directive(reply)
    if changes:
        apply_voice_settings(changes)
    trait = brain.parse_persona_directive(reply)
    if trait:
        append_override(trait)  # takes effect on the next reply
    spoken = brain.strip_directive(reply)
    conv.append({"role": "assistant", "content": spoken or reply})

    if usage is not None:
        log(f"brain: in={getattr(usage,'input_tokens','?')} out={getattr(usage,'output_tokens','?')} "
            f"reply={spoken!r}")
    if spoken:
        await speak_in_channel(voice_channel, spoken)


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
            # Engage if the name was said, OR a conversation is already open for this speaker
            # (continuous mode) and the utterance isn't obvious silence-hallucination noise.
            engage = rec["contains_wake"] or (convo_active(gid, name) and not looks_like_noise(text))
            if engage:
                try:
                    await handle_auto_reply(vc_channel.guild, vc_channel, name, text)
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


@client.event
async def on_ready():
    log(f"Logged in as {client.user} (id {client.user.id})")
    log(f"AUTO_REPLY mode: {'ON (autonomous API brain, ' + brain.MODEL + ')' if AUTO_REPLY else 'OFF (live-Claude loop)'}")
    dump_channels()
    if not poll_outbox.is_running():
        poll_outbox.start()
    if not flush_utterances.is_running():
        flush_utterances.start()


@client.event
async def on_message(message):
    rec = record_message(message)
    if not rec["is_self"]:
        log(f"inbox #{rec['channel']} <{rec['author']}>: {rec['content']!r}")


@tasks.loop(seconds=2)
async def poll_outbox():
    try:
        files = sorted(f for f in os.listdir(OUTBOX) if f.endswith(".json"))
    except FileNotFoundError:
        return
    for fname in files:
        path = os.path.join(OUTBOX, fname)
        result = {"processed_at": datetime.now(timezone.utc).isoformat()}
        try:
            with open(path, "r", encoding="utf-8") as f:
                req = json.load(f)
            action = req.get("action", "send")
            channel_id = int(req["channel_id"])
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)

            if action == "listen":
                await start_listening(channel)
                result.update({"status": "listening", "request": req, "channel": str(channel)})
                _finish(path, fname, SENT, result)
                log(f"Listening in #{getattr(channel, 'name', channel_id)}")
            elif action == "stop_listen":
                await stop_listening(channel.guild)
                result.update({"status": "stopped_listening", "request": req, "channel": str(channel)})
                _finish(path, fname, SENT, result)
                log(f"Stopped listening in #{getattr(channel, 'name', channel_id)}")
            elif action == "speak":
                text = str(req["content"])
                await speak_in_channel(channel, text)
                result.update(
                    {"status": "spoke", "request": req, "channel": str(channel)}
                )
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
                _finish(path, fname, SENT, result)
                log(f"Edited message {message_id} in #{getattr(channel, 'name', channel_id)}")
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
                _finish(path, fname, SENT, result)
                log(f"Fetched {len(msgs)} msg(s) from #{getattr(channel, 'name', channel_id)}")
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
                _finish(path, fname, SENT, result)
                log(f"Sent to #{getattr(channel, 'name', channel_id)}: {content!r}")
        except Exception as e:  # noqa: BLE001 — record everything, keep the loop alive
            result.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
            log(f"FAILED {fname}: {result['error']}")
            log(traceback.format_exc())
            _finish(path, fname, FAILED, result)


def _finish(path, fname, dest_dir, result):
    """Move the request file to dest_dir and write a sibling _result.json."""
    try:
        base = os.path.splitext(fname)[0]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(dest_dir, f"{base}_{stamp}.json")
        shutil.move(path, dest)
        with open(os.path.splitext(dest)[0] + "_result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception:  # noqa: BLE001
        log("Could not archive request file:\n" + traceback.format_exc())


@poll_outbox.before_loop
async def before_poll():
    await client.wait_until_ready()


def main():
    token = os.environ.get("BOT_KEY")
    if not token:
        raise SystemExit("BOT_KEY not set — put it in environ.env as BOT_KEY=<token>")
    client.run(token)


if __name__ == "__main__":
    main()
