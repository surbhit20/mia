import numpy as np
import sounddevice as sd


def start_playback(
    pcm_audio: bytes, device_name: str = "BlackHole 2ch", sample_rate: int = 16000
) -> None:
    """Start playing raw PCM 16-bit audio out through the named output
    device. Does not block -- sounddevice plays asynchronously under the
    hood, which is what lets the caller keep running its own loop (reading
    mic frames, watching for a barge-in wake word) while audio plays."""
    samples = np.frombuffer(pcm_audio, dtype="int16")
    sd.play(samples, samplerate=sample_rate, device=device_name, blocking=False)


def is_playback_active() -> bool:
    """True while audio started by start_playback() is still playing."""
    try:
        stream = sd.get_stream()
    except RuntimeError:
        return False
    return stream.active


def stop_playback() -> None:
    """Immediately stop whatever start_playback() is currently playing.
    A harmless no-op if nothing is playing."""
    sd.stop()
