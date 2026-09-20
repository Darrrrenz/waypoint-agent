"""Shared contracts. Provider data and tool data are untrusted until validated."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowContext(Schema):
    participant: str
    start: AwareDatetime
    end: AwareDatetime
    timezone: str


class Task(Schema):
    id: UUID = Field(default_factory=uuid4)
    goal: str
    created_at: AwareDatetime
    context: WorkflowContext
    status: Literal["pending", "running", "completed", "failed", "limited"] = "pending"
    termination_reason: (
        Literal["verified", "max_steps", "max_model_calls", "error_budget", "deadline"] | None
    ) = None


class ToolCall(Schema):
    name: str
    arguments: dict[str, Any]


class CallTool(Schema):
    kind: Literal["call_tool"]
    call: ToolCall


class Findings(Schema):
    outcome: Literal["found", "missing_meeting", "no_unread", "ambiguous"]
    meeting_ids: list[str]
    unread_email_ids: list[str]
    evidence_ids: list[str]


class ProposeCompletion(Schema):
    kind: Literal["propose_completion"]
    findings: Findings


NextAction = Annotated[CallTool | ProposeCompletion, Field(discriminator="kind")]
ACTION_ADAPTER = TypeAdapter(NextAction)


class ToolResult(Schema):
    id: str
    call: ToolCall
    output: dict[str, Any]


class EvaluationResult(Schema):
    accepted: bool
    reasons: list[str]
    answer: str | None = None


class AgentState(Schema):
    task: Task
    steps: int = 0
    model_calls: int = 0
    errors: int = 0
    observations: list[ToolResult] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    findings: Findings | None = None
    answer: str | None = None


class TrajectoryEvent(Schema):
    task_id: UUID
    sequence: int
    timestamp: AwareDatetime
    kind: str
    data: dict[str, Any]
    latency_ms: float | None = None
    usage: dict[str, int] | None = None


class Checkpoint(Schema):
    state: AgentState
    last_sequence: int = 0


class ModelReply(Schema):
    action: Any
    usage: dict[str, int] | None = None


def utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
