from dataclasses import dataclass
from datetime import datetime

from mia.logging_setup import safe_log
from mia.timeutil import local_timezone_label
from mia.tools.base import ToolRegistry


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str | None
    confirmation: str
    succeeded: bool = True
    mutated: bool = True


class ConversationHistory:
    """Bounded, in-memory record of prior dispatch_command turns for one
    joined call. Never persisted to disk. Each remembered exchange is
    stored in the Anthropic Messages API's own multi-turn tool-use format,
    so it can be prepended directly to the next call's `messages` list."""

    def __init__(self, max_exchanges: int = 3):
        self._max_exchanges = max_exchanges
        self._exchanges: list[list[dict]] = []

    def as_messages(self) -> list[dict]:
        messages: list[dict] = []
        for exchange in self._exchanges:
            messages.extend(exchange)
        return messages

    def record(
        self,
        user_message: dict,
        assistant_message: dict,
        tool_result_message: dict | None,
    ) -> None:
        exchange = [user_message, assistant_message]
        if tool_result_message is not None:
            exchange.append(tool_result_message)
        self._exchanges.append(exchange)
        if len(self._exchanges) > self._max_exchanges:
            self._exchanges.pop(0)


def _system_prompt() -> str:
    """Ground Claude in the real current date/time.

    Without this the model has no idea what "today" is, so "block 3 PM" would
    be resolved against its training cutoff -- silently creating the event on
    the wrong day (or emitting a naive timestamp the Calendar API rejects).
    """
    now = datetime.now().astimezone()
    return (
        "You are Mia, a voice assistant taking spoken commands during a live "
        "meeting with other people present.\n"
        f"The current date and time is {now.isoformat()}.\n"
        f"The user's local timezone is {local_timezone_label()}.\n"
        "Use that as the reference point for every relative or bare time the "
        "user gives (\"3 PM\", \"tomorrow\", \"in an hour\"): interpret them in "
        "the user's local timezone, and always emit ISO 8601 datetimes that "
        "include the UTC offset -- never a timezone-naive timestamp.\n"
        "\n"
        "Everything you say is spoken aloud into the meeting, so reply in one "
        "or two short sentences of plain spoken English. Never use lists, "
        "markdown, bullet points, or headings, and do not read out URLs or "
        "raw timestamps -- say \"3 PM tomorrow\", not an ISO string.\n"
        "\n"
        "You can create, find, move, rename, and cancel events on the user's "
        "Google Calendar, and search their Gmail. If you are asked what you "
        "can do, say that briefly and naturally.\n"
        "\n"
        "If a command is ambiguous -- an unclear time, or an event that could "
        "match several on the calendar -- ask one short clarifying question "
        "instead of guessing. A wrong calendar change is worse than a "
        "follow-up question.\n"
        "\n"
        "If the user asks for something you have no tool for, say in one "
        "sentence what you can help with instead. Do not refuse flatly, and "
        "do not answer general trivia or drift into unrelated conversation -- "
        "you are speaking into someone's meeting."
    )


def dispatch_command(
    client, registry: ToolRegistry, command_text: str, history: ConversationHistory
) -> ToolCallResult:
    user_message = {"role": "user", "content": command_text}
    messages = history.as_messages() + [user_message]

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
        system=_system_prompt(),
        tools=registry.anthropic_tool_specs(),
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        thinking={"type": "disabled"},
        messages=messages,
    )

    assistant_message = {"role": "assistant", "content": response.content}
    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)

    if tool_use_block is None:
        # Speak Claude's actual reply. This branch used to discard
        # response.content and substitute a fixed "I didn't catch a command"
        # line, which silently threw away every clarifying question,
        # capability answer, and conversational follow-up the model produced.
        # The canned line survives only for a genuinely empty response.
        spoken = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        history.record(user_message, assistant_message, None)
        return ToolCallResult(
            tool_name=None,
            confirmation=spoken or "Sorry, I didn't catch a command I can act on.",
            succeeded=True,
            mutated=False,
        )

    tool = registry.get(tool_use_block.name)
    succeeded = True
    if tool is None:
        confirmation = "Sorry, that didn't work — try again?"
        safe_log("error", "tool not found in registry", tool_name=tool_use_block.name)
        succeeded = False
    else:
        try:
            confirmation = tool.handler(tool_use_block.input)
        except Exception as exc:
            confirmation = "Sorry, that didn't work — try again?"
            safe_log("error", "tool handler failed", tool_name=tool.name, error=str(exc))
            succeeded = False

    tool_result_message = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_block.id,
                "content": confirmation,
            }
        ],
    }
    history.record(user_message, assistant_message, tool_result_message)
    return ToolCallResult(
        tool_name=tool.name if tool is not None else tool_use_block.name,
        confirmation=confirmation,
        succeeded=succeeded,
        mutated=(tool.mutates if tool is not None else False),
    )
