import pytest
from mia.config import Config, MissingConfigError

REQUIRED_ENV = {
    "DEEPGRAM_API_KEY": "dg-key",
    "ANTHROPIC_API_KEY": "an-key",
    "ELEVENLABS_API_KEY": "el-key",
    "GOOGLE_CLIENT_ID": "gc-id",
    "GOOGLE_CLIENT_SECRET": "gc-secret",
    "LOGFIRE_TOKEN": "lf-token",
}

def test_from_env_reads_all_required_keys(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    config = Config.from_env()
    assert config.deepgram_api_key == "dg-key"
    assert config.anthropic_api_key == "an-key"
    assert config.elevenlabs_api_key == "el-key"
    assert config.google_client_id == "gc-id"
    assert config.google_client_secret == "gc-secret"
    assert config.logfire_token == "lf-token"
    assert config.wake_word == "hey mia"
    assert config.fuzzy_threshold == 0.75

def test_from_env_does_not_require_logfire_token(monkeypatch):
    # Spec: Logfire must never be a hard dependency for correctness, so a user
    # with no Logfire account can still start the bot.
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LOGFIRE_TOKEN")
    config = Config.from_env()
    assert config.logfire_token == ""
    assert config.deepgram_api_key == "dg-key"

def test_from_env_raises_on_missing_key(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    with pytest.raises(MissingConfigError, match="DEEPGRAM_API_KEY"):
        Config.from_env()

def test_from_env_respects_wake_word_override(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("WAKE_WORD", "hey robot")
    assert Config.from_env().wake_word == "hey robot"

def test_from_env_defaults_attendee_settings_when_unset(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ATTENDEE_API_KEY", raising=False)
    monkeypatch.delenv("ATTENDEE_BASE_URL", raising=False)
    monkeypatch.delenv("ATTENDEE_WEBSOCKET_PORT", raising=False)
    monkeypatch.delenv("ATTENDEE_BOT_NAME", raising=False)

    config = Config.from_env()

    assert config.attendee_api_key == ""
    assert config.attendee_base_url == "http://localhost:8000"
    assert config.attendee_websocket_port == 8765
    assert config.attendee_bot_name == "Mia"


def test_from_env_respects_attendee_overrides(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ATTENDEE_API_KEY", "att-key")
    monkeypatch.setenv("ATTENDEE_BASE_URL", "http://example.com:9000")
    monkeypatch.setenv("ATTENDEE_WEBSOCKET_PORT", "9999")
    monkeypatch.setenv("ATTENDEE_BOT_NAME", "Custom Bot")

    config = Config.from_env()

    assert config.attendee_api_key == "att-key"
    assert config.attendee_base_url == "http://example.com:9000"
    assert config.attendee_websocket_port == 9999
    assert config.attendee_bot_name == "Custom Bot"
