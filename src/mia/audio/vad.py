import numpy as np
import torch
from silero_vad import load_silero_vad

# The installed silero-vad model (6.x) only accepts fixed-size windows: exactly
# 512 samples at 16kHz or 256 samples at 8kHz (see silero_vad.utils_vad.OnnxWrapper
# / the JIT model's forward pass, which raises ValueError on any other length).
# This is stricter than the brief's original code, which fed the model whatever
# length `frame_ms` produced (e.g. 480 samples for a 30ms frame at 16kHz) and
# raised "Input audio chunk is too short" / a shape-mismatch ValueError at
# runtime.
#
# `frame_ms` therefore isn't free: it must be the duration that yields exactly
# this window (32ms at 16kHz, 32ms at 8kHz), and __init__ rejects anything else
# rather than silently degrading every inference. is_speech() still pads or
# truncates as a last-resort guard for a short/partial read from the device.
_WINDOW_SAMPLES = {8000: 256, 16000: 512}


class FrameVAD:
    def __init__(self, sample_rate: int = 16000, frame_ms: int = 32):
        if sample_rate not in _WINDOW_SAMPLES:
            raise ValueError(f"Unsupported sample_rate {sample_rate}; silero-vad supports 8000 or 16000")
        window_samples = _WINDOW_SAMPLES[sample_rate]
        frame_samples = int(sample_rate * frame_ms / 1000)
        if frame_samples != window_samples:
            raise ValueError(
                f"frame_ms={frame_ms} at {sample_rate}Hz is {frame_samples} samples, "
                f"but silero-vad requires exactly {window_samples}; "
                f"use frame_ms={int(window_samples * 1000 / sample_rate)}"
            )
        self._model = load_silero_vad()
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._window_samples = window_samples

    def is_speech(self, frame: bytes) -> bool:
        audio = np.frombuffer(frame, dtype="int16").astype("float32") / 32768.0
        if len(audio) < self._window_samples:
            audio = np.pad(audio, (0, self._window_samples - len(audio)))
        elif len(audio) > self._window_samples:
            audio = audio[: self._window_samples]
        tensor = torch.from_numpy(audio)
        prob = self._model(tensor, self._sample_rate).item()
        return prob > 0.5
