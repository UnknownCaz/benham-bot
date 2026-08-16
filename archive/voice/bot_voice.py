# Extracted verbatim from benham/bot.py when voice was archived (2026-08-16).
# NOT importable - this is a record, not a module. See archive/voice/README.md.

# ---------- lines 171-241: the DAVE receive-decryption patch ----------
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



# ---------- lines 327-858: TTS, STT, wake words, AUTO_REPLY, listening ----------
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


PERSONALITY_OVERRIDES_FILE = os.path.join(paths.STATE_DIR, "personality_overrides.txt")


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


async def say(voice_channel, text, speaker_id):
    """Speak in voice, through policy rather than around it.

    Every spoken line now carries an OWNER_VOICE context. Until this existed that
    origin was declared but had no production caller: handle_auto_reply called
    speak_in_channel directly, so voice output was the one outward action that
    never passed the chokepoint. Its owner gate was sound - the check at the top of
    handle_auto_reply - but "sound because this particular function remembers to
    check" is the exact arrangement the policy refactor exists to eliminate.

    A refusal is logged and swallowed. The alternative is an exception inside the
    utterance loop, which would take down transcription for the whole call over one
    unspeakable line.
    """
    guild = getattr(voice_channel, "guild", None)
    try:
        await capabilities.run(
            client, log, "speak_in_voice",
            {"channel_id": voice_channel.id, "content": text},
            actor_id=speaker_id,
            call_ctx=policy.CallContext.owner_voice(
                speaker_id, guild.id if guild else None, voice_channel.id))
    except capabilities.ActionError as e:
        log(f"voice: refused to speak - {e}")


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
            await say(voice_channel, "Alright, going quiet. Call me if you need me.",
                     speaker_id)
            await stop_listening(guild)
            return
        if kind == "voice":
            changes, confirm = payload
            apply_voice_settings(changes)
            _open_convo(gid, speaker_id)
            await say(voice_channel, confirm, speaker_id)
            return
        if kind == "persona_reset":
            cleared = reset_overrides()
            _open_convo(gid, speaker_id)
            await say(
                voice_channel,
                "Okay, back to my usual self." if cleared
                else "I couldn't clear my personality tweaks - they're still active.",
                speaker_id,
            )
            return
        if kind == "voices_list":
            names = brain.voice_names()
            spoken = ", ".join(names[:-1]) + (", or " + names[-1] if len(names) > 1 else "")
            _open_convo(gid, speaker_id)
            await say(
                voice_channel, f"I can be {spoken}. Just say switch to one of them.",
                speaker_id)
            return
        if kind == "ping":
            _open_convo(gid, speaker_id)
            await say(voice_channel, payload, speaker_id)
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
        await say(voice_channel, spoken, speaker_id)
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


