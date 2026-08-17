from unittest.mock import MagicMock, patch

from mia.notify import (
    NotificationResult,
    _applescript_quote,
    _parse_dialog_output,
    prompt_join,
)


def test_parses_join():
    assert (
        _parse_dialog_output("button returned:Join, gave up:false\n")
        == NotificationResult.JOIN
    )


def test_parses_skip():
    assert (
        _parse_dialog_output("button returned:Skip, gave up:false\n")
        == NotificationResult.SKIP
    )


def test_parses_gave_up_as_timeout():
    assert (
        _parse_dialog_output("button returned:, gave up:true\n")
        == NotificationResult.TIMEOUT
    )


def test_parses_empty_as_timeout():
    assert _parse_dialog_output("") == NotificationResult.TIMEOUT


def test_parses_applescript_error_as_timeout():
    # Escape-dismissing the dialog makes osascript fail rather than report a
    # button; that must not read as a join.
    assert (
        _parse_dialog_output("execution error: User canceled. (-128)")
        == NotificationResult.TIMEOUT
    )


def test_gave_up_wins_over_a_stale_button_name():
    # Guards the parse order: a timeout line that still mentions Join must
    # not be read as a join.
    assert (
        _parse_dialog_output("button returned:Join, gave up:true")
        == NotificationResult.TIMEOUT
    )


def test_applescript_quote_escapes_double_quotes_and_backslashes():
    # Calendar titles are arbitrary text; an unescaped quote would end the
    # AppleScript string literal early and break the script.
    assert _applescript_quote('a "b" \\ c') == '"a \\"b\\" \\\\ c"'


@patch("mia.notify.subprocess.run")
def test_prompt_join_passes_timeout_to_applescript_and_subprocess(mock_run):
    mock_run.return_value = MagicMock(stdout="button returned:Join, gave up:false")

    assert prompt_join("standup", timeout_seconds=30) == NotificationResult.JOIN

    args, kwargs = mock_run.call_args
    script = args[0][2]
    assert "giving up after 30" in script
    assert '"Join standup?"' in script
    # The subprocess guard must outlive the dialog's own timeout, or it
    # would kill a prompt the user is still looking at.
    assert kwargs["timeout"] == 35
