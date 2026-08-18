import pytest

from mia.config import Config
from mia.logging_setup import configure, safe_log


def _config(logfire_token: str) -> Config:
    return Config(
        deepgram_api_key="dg",
        anthropic_api_key="an",
        elevenlabs_api_key="el",
        google_client_id="id",
        google_client_secret="secret",
        logfire_token=logfire_token,
    )


def test_configure_sends_to_logfire_when_a_token_is_set(mocker):
    mock_configure = mocker.patch("logfire.configure")
    configure(_config("lf-token"))
    kwargs = mock_configure.call_args.kwargs
    assert kwargs["token"] == "lf-token"
    # Fields must reach the console, not only Logfire -- a bare "audio stats"
    # label with its counters hidden made a live meeting undebuggable from the
    # terminal.
    assert kwargs["console"].verbose is True


def test_configure_disables_reporting_without_a_token(mocker):
    # Logfire is never a hard dependency: no token must not stop the bot.
    mock_configure = mocker.patch("logfire.configure")
    configure(_config(""))
    kwargs = mock_configure.call_args.kwargs
    assert kwargs["send_to_logfire"] is False
    assert kwargs["console"].verbose is True


def test_safe_log_is_a_no_op_when_reporting_is_disabled(recwarn):
    # Real (unmocked) configure: without a token, logging must be genuinely
    # harmless -- no exception and no "logfire is not configured" warning.
    configure(_config(""))
    safe_log("info", "meeting joined", meeting_id="abc")
    assert not [w for w in recwarn if "not been called" in str(w.message)]



def test_safe_log_calls_logfire(mocker):
    mock_info = mocker.patch("logfire.info")
    safe_log("info", "meeting joined", meeting_id="abc")
    mock_info.assert_called_once_with("meeting joined", meeting_id="abc")


def test_safe_log_swallows_logfire_exception(mocker, capsys):
    mocker.patch("logfire.error", side_effect=RuntimeError("network down"))
    safe_log("error", "tool failed")  # must not raise
    assert "network down" in capsys.readouterr().err


def test_safe_log_rejects_invalid_level():
    with pytest.raises(ValueError, match="debug"):
        safe_log("debug", "not a supported level")
