# Voice — archived 2026-08-16

Benham could join a Discord voice channel, speak in neural TTS, listen, transcribe, recognise
wake words, hold a continuous conversation, and answer autonomously from an API brain. It was
built 2026-07-24 and it worked.

It was archived because **it stopped being used.** `voice_transcript.jsonl` was last written
2026-07-25; `speak_in_voice` never once appeared in an executed action across the whole outbox
history. Three weeks dormant, while being among the most complex code in the repo — a private-
symbol monkeypatch, a pinned three-library version triangle, a local Whisper model, and roughly
600 lines threaded through `bot.py`. That is a lot of standing cost for a feature nobody reached
for, and the [intent rundown](../../INTENT.md) is explicit that Benham is a text channel between
people working on projects.

**Nothing here is deleted.** It is out of the running process, not out of the repo.

## What is in here

| File | Was |
|---|---|
| `bot_voice.py` | The voice code lifted verbatim out of `bot.py` (two blocks, line numbers noted inside). **A record, not a module** — it will not import. |
| `brain.py` | The autonomous AUTO_REPLY brain: Haiku, wake-gated, sliding window, local zero-API shortcuts. |
| `speak.py` / `listen.py` / `stoplisten.py` | The CLI entry points. |
| `voices.json` | The 15-voice B-named roster (Tyler likes B-names). Bennett was the default; Bella the female default. |

## What was NOT archived, and why

- **`prompts/persona.md` stays.** It is the shared personality, read by `agent.py` and
  `codesession.py` too. Voice was one consumer of it, not its owner.
- **`brain.strip_directive` moved to `core/shared_tools.py`** rather than coming here. It removes
  `<<...>>` directives from model output, and **the guest lane calls it** (`guest.py:508`,
  `guest_agent.py:403`) — brain.py was never voice-only, which is exactly the sort of thing that
  turns an archive into an outage. Its regex came with it.
- **`state/personality_overrides.txt` is now orphaned.** `brain.py` was its only reader — despite
  a comment in `guest.py` claiming "every surface reads" it, which was simply wrong: `agent.py`
  and `codesession.py` read `persona.md` and never the overrides. So the runtime "be more
  sarcastic" mechanism is gone with voice. If it is ever wanted back for text, it needs a reader
  in `agent.py`, not a resurrection of this directory.

## The knowledge worth keeping

This is the real reason the archive exists. The code is recoverable from git; these facts cost
days.

### The DAVE monkeypatch (the hard one)

discord.py 2.7 talks to Discord's DAVE end-to-end encryption, and `discord-ext-voice-recv` does
not decrypt before handing frames to the opus decoder — so received audio is garbage. The fix
wraps a **private symbol**:

```
discord.ext.voice_recv.opus.PacketDecoder._decode_packet
```

and runs each frame through `davey.DaveSession.decrypt(user_id, davey.MediaType.audio, packet)`
first. The session comes off `vc._connection.dave_session`; ssrc maps to a user id via
`vc._get_id_from_ssrc`.

**Downgrading discord.py instead does NOT work.** Pre-DAVE libraries are rejected by the voice
gateway with error 4006. The version triangle in `requirements.txt` was pinned deliberately —
`discord-ext-voice-recv==0.5.2a179`, `davey==0.1.*` — because a minor bump to any of the three
can move or rename that private symbol and silently break listening with no error.

### TTS

`edge-tts` neural voices replaced Windows SAPI on 2026-07-24 — free, no key, far more natural,
at the cost of slight latency (Tyler accepted it). Synthesis shells `python -m edge_tts` to MP3
and plays it through FFmpeg. `voice_settings.json` stored an edge voice name plus rate (-10..10)
and volume (0..100), remapped to edge's percentage format; legacy `Microsoft ...Desktop` names
auto-migrated. `tts.ps1` (SAPI) was kept but unused.

### STT and wake words

`faster-whisper` `base`, local. "claude" and "benham" always woke it, fuzzily — Whisper `base`
mishears Benham as Ben/Bentham often enough that phonetic variants were required. The **currently
selected** voice's B-name was also a live wake word, exact whole-word, only while that voice was
active.

Two bugs worth not repeating: Whisper hallucinates text during silence, so `looks_like_noise`
had to exist or a hallucination would hold a conversation window open forever; and voice-keyword
matching had to be whole-word set membership, because substring matching fired "man" inside
"manipulate" and "male" inside "female".

### Prompt caching was measured and rejected

The voice prefix sat under the 4096-token minimum, so caching bought nothing. This is the
opposite of `agent.py`, whose ~7.4k static prefix caches ~8x cheaper. Measure before assuming.

### The per-guild gate

`auto_reply_guilds` existed because AUTO_REPLY answering by itself in a friend's server is very
different from doing it in Testing. Wake detection and transcription ran everywhere; only the
self-answering path was gated. If autonomous replies ever return in any form, that separation —
**hear everywhere, answer only where allowed** — is the shape to copy.

## Bringing it back

Restore the files, re-add the four voice dependencies, re-apply `bot_voice.py`'s two blocks, and
re-register `speak_in_voice` / `voice_members` in `capabilities.py`. Expect the version triangle
to have rotted; verify the private symbol still exists before assuming the patch applies.
