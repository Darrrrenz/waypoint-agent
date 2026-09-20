from collections.abc import Iterable
from typing import Any

from waypoint_agent.schemas import AgentState, ModelReply


class ScriptedModel:
    """Finite deterministic responses; exhaustion is a normal bounded model failure."""

    def __init__(self, actions: Iterable[Any]):
        self.actions = iter(actions)

    async def next_action(self, state: AgentState, tools: list[dict[str, Any]]) -> ModelReply:
        try:
            return ModelReply(action=next(self.actions))
        except StopIteration as exc:
            raise ValueError("Script exhausted") from exc
