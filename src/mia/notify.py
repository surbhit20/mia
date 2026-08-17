"""Ask the user whether mia should join a detected Meet call.

Uses AppleScript's `display dialog` rather than `terminal-notifier`, which
cannot do this on current macOS. terminal-notifier 2.0.0 is built on
NSUserNotification, an API Apple has since removed: it still *shows* a
notification, but its `-actions` flag is inert and it returns immediately
with empty output instead of waiting for a choice. Confirmed live -- the
call returned in ~1s with an empty stdout despite `-timeout 120`, so every
prompt was silently read as "not accepted" and no call could ever be
joined.

`display dialog` blocks for the full timeout, reports which button was
pressed, and is built into macOS. It is routed through System Events so
the dialog appears in front of whatever has focus -- the user is in a
meeting when this fires, so a prompt hidden behind Chrome is a prompt that
times out.
"""

import subprocess
from enum import StrEnum


class NotificationResult(StrEnum):
    JOIN = "join"
    SKIP = "skip"
    TIMEOUT = "timeout"


def _applescript_quote(text: str) -> str:
    """Wrap `text` as an AppleScript string literal.

    Meeting titles carry URLs and arbitrary calendar text, so backslashes
    and double quotes have to be escaped or they terminate the literal
    early and the script fails to compile.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_dialog_output(output: str) -> NotificationResult:
    """Map `display dialog`'s result line to a NotificationResult.

    On a click the line reads `button returned:Join, gave up:false`; on
    timeout, `button returned:, gave up:true`. Anything else -- an
    AppleScript error, or the user dismissing with Escape -- is treated as
    TIMEOUT, which the caller already handles as "don't join".
    """
    cleaned = output.strip()
    if "gave up:true" in cleaned:
        return NotificationResult.TIMEOUT
    if "button returned:Join" in cleaned:
        return NotificationResult.JOIN
    if "button returned:Skip" in cleaned:
        return NotificationResult.SKIP
    return NotificationResult.TIMEOUT


def prompt_join(title: str, timeout_seconds: int = 120) -> NotificationResult:
    message = _applescript_quote(f"Join {title}?")
    script = (
        f"tell application \"System Events\" to display dialog {message} "
        f'with title "mia" buttons {{"Skip", "Join"}} default button "Join" '
        f"giving up after {timeout_seconds}"
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 5,
    )
    return _parse_dialog_output(result.stdout)
