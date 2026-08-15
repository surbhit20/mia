class CommandBuffer:
    def __init__(self):
        self._capturing = False
        self._fragments: list[str] = []

    def is_capturing(self) -> bool:
        return self._capturing

    def start(self) -> None:
        self._capturing = True
        self._fragments = []

    def append(self, text_fragment: str) -> None:
        if self._capturing:
            self._fragments.append(text_fragment)

    def on_silence(self) -> str | None:
        if not self._capturing:
            return None
        self._capturing = False
        text = "".join(self._fragments).strip()
        return text or None
