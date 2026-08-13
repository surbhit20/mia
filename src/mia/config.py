import os
from dataclasses import dataclass, field
from pathlib import Path

class MissingConfigError(ValueError):
    def __init__(self, key: str):
        super().__init__(f"missing required environment variable: {key}")
        self.key = key

_REQUIRED_KEYS = (
    "DEEPGRAM_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "LOGFIRE_TOKEN",
)

@dataclass(frozen=True)
class Config:
    deepgram_api_key: str
    anthropic_api_key: str
    elevenlabs_api_key: str
    google_client_id: str
    google_client_secret: str
    logfire_token: str
    wake_word: str = "hey mia"
    fuzzy_threshold: float = 0.75
    state_file: Path = field(default_factory=lambda: Path("~/.mia/state.json").expanduser())

    @classmethod
    def from_env(cls) -> "Config":
        values = {}
        for key in _REQUIRED_KEYS:
            value = os.environ.get(key)
            if not value:
                raise MissingConfigError(key)
            values[key] = value
        return cls(
            deepgram_api_key=values["DEEPGRAM_API_KEY"],
            anthropic_api_key=values["ANTHROPIC_API_KEY"],
            elevenlabs_api_key=values["ELEVENLABS_API_KEY"],
            google_client_id=values["GOOGLE_CLIENT_ID"],
            google_client_secret=values["GOOGLE_CLIENT_SECRET"],
            logfire_token=values["LOGFIRE_TOKEN"],
            wake_word=os.environ.get("WAKE_WORD", "hey mia"),
            fuzzy_threshold=float(os.environ.get("FUZZY_THRESHOLD", "0.75")),
        )
