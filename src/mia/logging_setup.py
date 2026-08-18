import sys

import logfire

from mia.config import Config

_LEVEL_NAMES = {"info": "info", "warning": "warn", "error": "error"}


# Print each log line's fields to the console, not just its message.
# Without this, "audio stats" and "transcript" render as bare labels and every
# diagnostic they carry is invisible unless you open Logfire in a browser --
# which made a live meeting nearly impossible to debug from the terminal.
_CONSOLE = logfire.ConsoleOptions(verbose=True)


def configure(config: Config) -> None:
    if not config.logfire_token:
        # Logfire is never a hard dependency (spec). With no token, configure
        # it in local-only mode: safe_log() calls stay harmless no-ops instead
        # of emitting a "not configured" warning on every single log line.
        logfire.configure(send_to_logfire=False, console=_CONSOLE)
        print("mia: no LOGFIRE_TOKEN set; Logfire reporting disabled", file=sys.stderr)
        return
    logfire.configure(token=config.logfire_token, console=_CONSOLE)


def safe_log(level: str, message: str, **fields) -> None:
    if level not in _LEVEL_NAMES:
        raise ValueError(f"unsupported log level: {level}")
    try:
        func = getattr(logfire, _LEVEL_NAMES[level])
        func(message, **fields)
    except Exception as exc:
        print(f"mia: logfire call failed ({exc}); continuing", file=sys.stderr)
