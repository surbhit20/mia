"""Local date/time helpers shared by the LLM system prompt and the calendar tool.

Both need the same notion of "now, where the user is": Claude has to resolve
relative expressions like "3 PM" or "tomorrow" against the real current date,
and Google Calendar rejects a timezone-naive `dateTime`.
"""

import zoneinfo
from datetime import datetime
from pathlib import Path

# `/etc/localtime` is a symlink into the zoneinfo database on macOS and Linux;
# everything after this marker in the resolved path is the IANA zone name.
_ZONEINFO_MARKER = "/zoneinfo/"


def local_iana_timezone() -> str | None:
    """Best-effort IANA zone name for this machine, e.g. "America/Los_Angeles".

    The stdlib has no portable way to ask for the local *IANA* name --
    `datetime.now().astimezone().tzname()` returns an abbreviation like "PDT",
    which Google Calendar will not accept as a `timeZone` -- and pulling in
    `tzlocal` for it would be a new dependency. Reading the `/etc/localtime`
    symlink covers macOS (the only supported platform) and Linux; anywhere it
    doesn't resolve, callers fall back to the UTC offset instead.
    """
    try:
        resolved = str(Path("/etc/localtime").resolve())
    except OSError:
        return None
    index = resolved.find(_ZONEINFO_MARKER)
    if index == -1:
        return None
    name = resolved[index + len(_ZONEINFO_MARKER) :]
    if not name:
        return None
    try:
        zoneinfo.ZoneInfo(name)
    except Exception:
        return None
    return name


def local_utc_offset() -> str:
    """The current local UTC offset as "UTC-07:00"."""
    offset = datetime.now().astimezone().strftime("%z")
    if not offset:
        return "UTC+00:00"
    return f"UTC{offset[:3]}:{offset[3:]}"


def local_timezone_label() -> str:
    """Human/LLM-readable timezone: the IANA name when known, else the offset."""
    return local_iana_timezone() or local_utc_offset()
