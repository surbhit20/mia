import numpy as np
import torch
from silero_vad import load_silero_vad

# The installed silero-vad model (6.x) only accepts fixed-size windows: exactly
# 512 samples at 16kHz or 256 samples at 8kHz (see silero_vad.utils_vad.OnnxWrapper
# / the JIT model's forward pass, which raises ValueError on any other length).
# This is stricter than the brief's original code, which fed the model whatever
# length `frame_ms` produced (e.g. 480 samples for a 30ms frame at 16kHz) and
# raised "Input audio chunk is too short" / a shape-mismatch ValueError at
# runtime. To keep the public interface's frame_ms=30 default intact (Task 19's
# main loop depends on that framing), is_speech() pads/truncates each incoming
# frame to the model's required window size before inference.
_WINDOW_SAMPLES = {8000: 256, 16000: 512}


class FrameVAD:
    def __init__(self, sample_rate: int = 16000, frame_ms: int = 30):
        if sample_rate not in _WINDOW_SAMPLES:
            raise ValueError(f"Unsupported sample_rate {sample_rate}; silero-vad supports 8000 or 16000")
        self._model = load_silero_vad()
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._window_samples = _WINDOW_SAMPLES[sample_rate]

    def is_speech(self, frame: bytes) -> bool:
        audio = np.frombuffer(frame, dtype="int16").astype("float32") / 32768.0
        if len(audio) < self._window_samples:
            audio = np.pad(audio, (0, self._window_samples - len(audio)))
        elif len(audio) > self._window_samples:
            audio = audio[: self._window_samples]
        tensor = torch.from_numpy(audio)
        prob = self._model(tensor, self._sample_rate).item()
        return prob > 0.5
