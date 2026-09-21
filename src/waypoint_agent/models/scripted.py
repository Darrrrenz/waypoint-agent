from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from waypoint_agent.schemas import AgentState, ModelReply


class ScriptedModel:
    """Finite deterministic responses; exhaustion is a normal bounded model failure."""

    def __init__(self, actions: Iterable[Any]):
        self.actions = list(actions)

    async def next_action(self, state: AgentState, tools: list[dict[str, Any]]) -> ModelReply:
        try:
            action = deepcopy(self.actions[state.model_calls - 1])
            # This evidence reference is resolved from a trusted completed execution;
            # it cannot supply approval metadata or authorize an operation.
            if (
                state.task.workflow == "reschedule"
                and isinstance(action, dict)
                and action.get("kind") == "propose_completion"
                and action.get("findings", {}).get("operation_id") == "$last_operation_id"
            ):
                if not state.executions:
                    raise ValueError("No confirmed operation is available")
                action["findings"]["operation_id"] = str(state.executions[-1].operation_id)
            return ModelReply(action=action)
        except IndexError as exc:
            raise ValueError("Script exhausted") from exc
