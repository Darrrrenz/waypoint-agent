from typing import Any

from waypoint_agent.interfaces import Tool


class Registry:
    def __init__(self, tools: list[Tool]):
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Duplicate tool names")
        for tool in tools:
            self.permission(tool)

    @staticmethod
    def permission(tool):
        from waypoint_agent.tools.calendar import UpdateCalendarEvent

        if tool.risk_level == "read_only" and tool.name != "update_calendar_event":
            return "read_only"
        if type(tool) is UpdateCalendarEvent and tool.risk_level == "approval_required":
            return "approval_required"
        raise PermissionError("Unsupported tool permission")

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
        tool = self.tools[name]
        self.permission(tool)
        return tool
