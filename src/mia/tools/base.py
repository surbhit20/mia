from collections.abc import Callable
from dataclasses import dataclass

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]
    # Whether running this tool changes something in the world. Read-only
    # lookups are not "actions completed" for the post-meeting summary.
    # Defaults True so a newly added tool shows up as checklist noise rather
    # than being silently omitted from what mia reports she did.
    mutates: bool = True

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def anthropic_tool_specs(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self._tools.values()
        ]
