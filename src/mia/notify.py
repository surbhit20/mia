import subprocess
from enum import StrEnum

class NotificationResult(StrEnum):
    JOIN = "join"
    SKIP = "skip"
    TIMEOUT = "timeout"

def _parse_terminal_notifier_output(output: str) -> NotificationResult:
    cleaned = output.strip()
    if cleaned == "Join":
        return NotificationResult.JOIN
    if cleaned == "Skip":
        return NotificationResult.SKIP
    return NotificationResult.TIMEOUT

def prompt_join(title: str, timeout_seconds: int = 120) -> NotificationResult:
    result = subprocess.run(
        [
            "terminal-notifier",
            "-title", "mia",
            "-message", f"Join {title}?",
            "-actions", "Join,Skip",
            "-timeout", str(timeout_seconds),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 5,
    )
    return _parse_terminal_notifier_output(result.stdout)
