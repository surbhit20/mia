from dataclasses import dataclass
from datetime import datetime

from mia.timeutil import local_timezone_label
from mia.tools.base import ToolRegistry

@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str | None
    confirmation: str

def _system_prompt() -> str:
    """Ground Claude in the real current date/time.

    Without this the model has no idea what "today" is, so "block 3 PM" would
    be resolved against its training cutoff -- silently creating the event on
    the wrong day (or emitting a naive timestamp the Calendar API rejects).
    """
    now = datetime.now().astimezone()
    return (
        "You are a voice assistant taking spoken commands during a live meeting.\n"
        f"The current date and time is {now.isoformat()}.\n"
        f"The user's local timezone is {local_timezone_label()}.\n"
        "Use that as the reference point for every relative or bare time the "
        "user gives (\"3 PM\", \"tomorrow\", \"in an hour\"): interpret them in "
        "the user's local timezone, and always emit ISO 8601 datetimes that "
        "include the UTC offset -- never a timezone-naive timestamp."
    )

def dispatch_command(client, registry: ToolRegistry, command_text: str) -> ToolCallResult:
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
        system=_system_prompt(),
        tools=registry.anthropic_tool_specs(),
        messages=[{"role": "user", "content": command_text}],
    )

    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        return ToolCallResult(tool_name=None, confirmation="Sorry, I didn't catch a command I can act on.")

    tool = registry.get(tool_use_block.name)
    if tool is None:
        return ToolCallResult(tool_name=tool_use_block.name, confirmation="Sorry, that didn't work — try again?")

    try:
        confirmation = tool.handler(tool_use_block.input)
    except Exception:
        return ToolCallResult(tool_name=tool.name, confirmation="Sorry, that didn't work — try again?")

    return ToolCallResult(tool_name=tool.name, confirmation=confirmation)
