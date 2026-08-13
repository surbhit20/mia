from elevenlabs import ElevenLabs


def synthesize(api_key: str, text: str, voice_id: str = "Rachel") -> bytes:
    """Synthesize speech via ElevenLabs and return raw PCM 16-bit 16kHz mono audio."""
    client = ElevenLabs(api_key=api_key)
    audio_chunks = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        output_format="pcm_16000",
    )
    return b"".join(audio_chunks)
