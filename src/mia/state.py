import json
import os
import time
from collections.abc import Callable
from pathlib import Path

# Meet URLs are stable across a recurring calendar event, so a permanent record
# would blacklist tomorrow's standup because today's was skipped (or failed to
# join once). Entries are therefore only "already handled" for a bounded
# window. Four hours comfortably outlives any single meeting -- so no call ever
# re-prompts itself mid-session -- while still being clear well before the same
# link comes round the next day.
_DEFAULT_TTL_SECONDS = 4 * 60 * 60

class StateStore:
    def __init__(
        self,
        path: Path,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        self._path = path
        self._ttl_seconds = ttl_seconds
        # Wall clock, not monotonic: entries have to stay comparable across
        # restarts of this long-running process.
        self._clock = clock

    def _read(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            # A truncated/corrupt file (e.g. killed mid-write by an older
            # version) would otherwise raise at the top of every poll, forever.
            # Treat it as empty; the next write replaces it.
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: os.replace is atomic on POSIX, so a process killed
        # mid-write leaves the previous file intact rather than a half-written
        # one. The temp file must share the target's directory to stay on the
        # same filesystem.
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        tmp_path.write_text(json.dumps(data))
        os.replace(tmp_path, self._path)

    def status(self, meeting_url: str) -> str | None:
        entry = self._read().get(meeting_url)
        if not isinstance(entry, dict):
            # Absent, or a bare-string entry written before entries carried a
            # timestamp -- no way to age it, so treat it as expired.
            return None
        if self._clock() - entry.get("updated_at", 0) >= self._ttl_seconds:
            return None
        return entry.get("status")

    def set_status(self, meeting_url: str, status: str) -> None:
        data = self._read()
        data[meeting_url] = {"status": status, "updated_at": self._clock()}
        self._write(data)

    def clear(self, meeting_url: str) -> None:
        data = self._read()
        data.pop(meeting_url, None)
        self._write(data)
