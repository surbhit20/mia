import re
import subprocess

_MEET_CALL_URL_RE = re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}")

_APPLESCRIPT = """
tell application "Google Chrome"
    if not running then return ""
    set urlList to {}
    repeat with w in windows
        repeat with t in tabs of w
            copy (URL of t) to end of urlList
        end repeat
    end repeat
    return urlList
end tell
"""

def find_active_meet_tab() -> str | None:
    try:
        result = subprocess.run(
            ["osascript", "-e", _APPLESCRIPT],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None
    for url in result.stdout.split(", "):
        match = _MEET_CALL_URL_RE.match(url.strip())
        if match:
            return match.group(0)
    return None
