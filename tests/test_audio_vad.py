import wave
from pathlib import Path

import pytest

from mia.audio.vad import FrameVAD

FIXTURES = Path(__file__).parent / "fixtures"

def _read_frames(wav_path: Path, frame_ms: int = 30):
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        assert wf.getnchannels() == 1
        frame_bytes = int(16000 * frame_ms / 1000) * 2
        data = wf.readframes(wf.getnframes())
    return [data[i : i + frame_bytes] for i in range(0, len(data) - frame_bytes + 1, frame_bytes)]

@pytest.mark.skipif(not (FIXTURES / "speech.wav").exists(), reason="fixture not recorded yet")
def test_detects_speech_frames():
    vad = FrameVAD()
    frames = _read_frames(FIXTURES / "speech.wav")
    assert any(vad.is_speech(f) for f in frames)

@pytest.mark.skipif(not (FIXTURES / "silence.wav").exists(), reason="fixture not recorded yet")
def test_silence_has_no_speech_frames():
    vad = FrameVAD()
    frames = _read_frames(FIXTURES / "silence.wav")
    assert not any(vad.is_speech(f) for f in frames)
