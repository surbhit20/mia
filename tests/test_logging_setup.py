import pytest

from mia.logging_setup import safe_log


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
