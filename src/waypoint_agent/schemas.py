"""Shared contracts. Provider data and tool data are untrusted until validated."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowContext(Schema):
    participant: str
    start: AwareDatetime
    end: AwareDatetime
    timezone: str


class RescheduleContext(WorkflowContext):
    destination_start: AwareDatetime
    destination_end: AwareDatetime
    slot_minutes: int = Field(default=15, ge=1, le=240)

    @model_validator(mode="after")
    def valid_windows(self):
        if self.end <= self.start or self.destination_end <= self.destination_start:
            raise ValueError("Invalid search or destination window")
        return self


class Task(Schema):
    id: UUID = Field(default_factory=uuid4)
    goal: str
    created_at: AwareDatetime
    context: RescheduleContext | WorkflowContext
    workflow: Literal["meeting_email", "reschedule"] = "meeting_email"
    calendar_world_id: UUID | None = None
    status: Literal[
        "pending",
        "running",
        "waiting_for_approval",
        "unresolved",
        "denied",
        "completed",
        "failed",
        "limited",
    ] = "pending"
    termination_reason: (
        Literal["verified", "max_steps", "max_model_calls", "error_budget", "deadline", "denied"]
        | None
    ) = None

    @model_validator(mode="after")
    def valid_workflow(self):
        if self.workflow == "reschedule" and (
            not isinstance(self.context, RescheduleContext) or self.calendar_world_id is None
        ):
            raise ValueError("Rescheduling requires destination context and calendar world")
        return self


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


class RescheduleFindings(Schema):
    outcome: Literal["rescheduled", "missing_meeting", "ambiguous", "no_availability"]
    meeting_ids: list[str]
    operation_id: str | None
    evidence_ids: list[str]


class ProposeCompletion(Schema):
    kind: Literal["propose_completion"]
    findings: Findings


NextAction = Annotated[CallTool | ProposeCompletion, Field(discriminator="kind")]
ACTION_ADAPTER = TypeAdapter(NextAction)


class ProposeRescheduleCompletion(Schema):
    kind: Literal["propose_completion"]
    findings: RescheduleFindings


RESCHEDULE_ACTION_ADAPTER = TypeAdapter(
    Annotated[CallTool | ProposeRescheduleCompletion, Field(discriminator="kind")]
)


def action_adapter(workflow):
    return RESCHEDULE_ACTION_ADAPTER if workflow == "reschedule" else ACTION_ADAPTER


def completion_schema(workflow):
    return RescheduleFindings if workflow == "reschedule" else Findings


class ApprovalRequest(Schema):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    calendar_world_id: UUID
    event_id: str
    call: ToolCall
    arguments_hash: str
    expected_revision: int
    operation_id: UUID = Field(default_factory=uuid4)
    before: dict[str, Any]
    after: dict[str, Any]
    decision: Literal["pending", "approved", "denied"] = "pending"
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    outcome: Literal["applied", "stale", "denied"] | None = None


class ExecutionRecord(Schema):
    approval_id: UUID
    operation_id: UUID
    arguments_hash: str
    observation_id: str
    result: dict[str, Any]


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
    findings: RescheduleFindings | Findings | None = None
    answer: str | None = None
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    pending_approval_id: UUID | None = None
    pending_action: Any = None
    executions: list[ExecutionRecord] = Field(default_factory=list)
    model_kind: Literal["scripted", "reschedule_demo", "live"] = "scripted"
    script: list[Any] | None = None
    dataset: dict[str, Any] | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    active_seconds: float = 0
    reserved_seconds: float = 0
    read_attempts: int = 0
    replan_after_observation: int = 0


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
