"""The short "command received" clip mia plays before she starts thinking.

A command takes 2-6 seconds to answer (Claude dispatch, sometimes with a
second internal Claude call for Gmail, then ElevenLabs synthesis, then the
output POST). Without a signal in that window the speaker has no way to tell
"heard you, working on it" from "never heard you" -- and the wake word is the
part most likely to have failed, so the ambiguity lands exactly where it hurts.

The clip is generated once and cached on disk. Synthesizing it at command
time would cost about a second from ElevenLabs, which is the very latency
this exists to mask, so a cache miss must never happen on the hot path --
warm it when the bot joins instead.
"""

import hashlib
from pathlib import Path

from mia.tts import synthesize

ACK_PHRASE = "On it."

# Matches tts.synthesize's default (ElevenLabs' premade "Rachel"), so the
# acknowledgment and the answer that follows it are the same voice.
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# Recall's output_audio endpoint takes mp3 only, never PCM.
_OUTPUT_FORMAT = "mp3_44100_128"

_DEFAULT_CACHE_DIR = Path("~/.mia").expanduser()


def acknowledgment_mp3(
    api_key: str,
    voice_id: str = _DEFAULT_VOICE_ID,
    cache_dir: Path | None = None,
) -> bytes:
    """MP3 bytes for the acknowledgment, generating and caching on first use.

    Keyed by voice and phrase: changing either regenerates rather than
    serving a stale clip in the previous voice.
    """
    directory = _DEFAULT_CACHE_DIR if cache_dir is None else cache_dir
    phrase_digest = hashlib.sha256(ACK_PHRASE.encode("utf-8")).hexdigest()[:8]
    cached = directory / f"ack-{voice_id}-{phrase_digest}.mp3"

    if cached.is_file():
        data = cached.read_bytes()
        # A truncated or empty file would otherwise be served as a silent
        # "clip" forever, which looks exactly like the bug this feature fixes.
        if data:
            return data

    audio = synthesize(
        api_key=api_key,
        text=ACK_PHRASE,
        voice_id=voice_id,
        output_format=_OUTPUT_FORMAT,
    )
    directory.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(audio)
    return audio
