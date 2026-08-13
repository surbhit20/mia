from elevenlabs import ElevenLabs


def synthesize(api_key: str, text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> bytes:
    """Synthesize speech via ElevenLabs and return raw PCM 16-bit 16kHz mono audio.

    Default voice_id is ElevenLabs' premade "Rachel" voice. The API takes an
    opaque voice ID, not a human-readable name (see the SDK's own docstring:
    "Use the Get voices endpoint to list all available voices") — passing
    the literal string "Rachel" 404s.
    """
    client = ElevenLabs(api_key=api_key)
    audio_chunks = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        output_format="pcm_16000",
    )
    return b"".join(audio_chunks)
