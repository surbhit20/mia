import sys

import logfire

from mia.config import Config

_LEVEL_NAMES = {"info": "info", "warning": "warn", "error": "error"}


def configure(config: Config) -> None:
    logfire.configure(token=config.logfire_token)


def safe_log(level: str, message: str, **fields) -> None:
    if level not in _LEVEL_NAMES:
        raise ValueError(f"unsupported log level: {level}")
    try:
        func = getattr(logfire, _LEVEL_NAMES[level])
        func(message, **fields)
    except Exception as exc:
        print(f"mia: logfire call failed ({exc}); continuing", file=sys.stderr)
