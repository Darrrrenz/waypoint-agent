from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from waypoint_agent.schemas import (
    AgentState,
    Checkpoint,
    EvaluationResult,
    Findings,
    ModelReply,
    TrajectoryEvent,
)


class Model(Protocol):
    async def next_action(self, state: AgentState, tools: list[dict[str, Any]]) -> ModelReply: ...


class Tool(Protocol):
    name: str
    description: str
    risk_level: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    async def execute(self, arguments: BaseModel) -> Any: ...


class Repository(Protocol):
    async def save(self, checkpoint: Checkpoint, event: TrajectoryEvent) -> None: ...
    async def load(self, task_id: UUID) -> tuple[Checkpoint, list[TrajectoryEvent]]: ...


class Evaluator(Protocol):
    def verify(self, state: AgentState, findings: Findings) -> EvaluationResult: ...
