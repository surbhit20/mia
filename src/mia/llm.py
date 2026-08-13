from dataclasses import dataclass

from mia.tools.base import ToolRegistry

@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str | None
    confirmation: str

def dispatch_command(client, registry: ToolRegistry, command_text: str) -> ToolCallResult:
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
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
