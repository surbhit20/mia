import json
from pathlib import Path

class StateStore:
    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))

    def status(self, meeting_url: str) -> str | None:
        return self._read().get(meeting_url)

    def set_status(self, meeting_url: str, status: str) -> None:
        data = self._read()
        data[meeting_url] = status
        self._write(data)

    def clear(self, meeting_url: str) -> None:
        data = self._read()
        data.pop(meeting_url, None)
        self._write(data)
