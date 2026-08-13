import sounddevice as sd


class BlackHoleCapture:
    def __init__(self, device_name: str = "BlackHole 2ch", sample_rate: int = 16000):
        self._device_name = device_name
        self._sample_rate = sample_rate
        self._stream: sd.RawInputStream | None = None

    def __enter__(self) -> "BlackHoleCapture":
        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            device=self._device_name,
        )
        self._stream.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def read_frame(self, frame_ms: int = 30) -> bytes:
        frame_samples = int(self._sample_rate * frame_ms / 1000)
        data, _overflowed = self._stream.read(frame_samples)
        return bytes(data)
