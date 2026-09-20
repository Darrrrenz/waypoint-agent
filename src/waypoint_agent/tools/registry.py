from typing import Any

from waypoint_agent.interfaces import Tool


class Registry:
    def __init__(self, tools: list[Tool]):
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Duplicate tool names")
        if any(tool.risk_level != "read_only" for tool in tools):
            raise ValueError("Day 1 only allows read-only tools")

    def describe(self) -> list[dict[str, Any]]:
        return [
            dict(
                name=t.name,
                description=t.description,
                risk_level=t.risk_level,
                input_schema=t.input_schema.model_json_schema(),
                output_schema=t.output_schema.model_json_schema(),
            )
            for t in self.tools.values()
        ]

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ValueError(f"Unknown tool: {name}")
        return self.tools[name]
