from mia.notify import NotificationResult, _parse_terminal_notifier_output

def test_parses_join():
    assert _parse_terminal_notifier_output("Join\n") == NotificationResult.JOIN

def test_parses_skip():
    assert _parse_terminal_notifier_output("Skip\n") == NotificationResult.SKIP

def test_parses_empty_as_timeout():
    assert _parse_terminal_notifier_output("") == NotificationResult.TIMEOUT

def test_parses_timeout_string_as_timeout():
    assert _parse_terminal_notifier_output("*Timeout*\n") == NotificationResult.TIMEOUT
