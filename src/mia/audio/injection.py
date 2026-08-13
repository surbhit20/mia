import numpy as np
import sounddevice as sd


def inject_into_virtual_mic(
    pcm_audio: bytes, device_name: str = "BlackHole 2ch", sample_rate: int = 16000
) -> None:
    """Play raw PCM 16-bit audio out through the named output device, blocking until done."""
    samples = np.frombuffer(pcm_audio, dtype="int16")
    sd.play(samples, samplerate=sample_rate, device=device_name, blocking=True)
