"""Declarative inputs and private answer keys. Only Inputs reach task construction."""

from typing import Any, Literal

from pydantic import Field, model_validator

from waypoint_agent.schemas import RescheduleContext, Schema, WorkflowContext
from waypoint_agent.tools.mock import Dataset


class Inputs(Schema):
    dataset: Dataset
    workflow: Literal["meeting_email", "reschedule"]
    goal: str
    context: RescheduleContext | WorkflowContext
    model: Literal["scripted", "reschedule_demo"]
    responses: list[Any] | None = None


class Expected(Schema):
    status: Literal["completed", "denied", "failed", "limited"]
    outcome: str | None = None
    meeting_ids: list[str] = Field(default_factory=list)
    email_ids: list[str] = Field(default_factory=list)
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    approvals: int = 0
    operations: int = 0
    allowed_tools: list[str] | None = None
    argument_constraints: dict[str, dict[str, Any]] | None = None
    max_tool_proposals: int | None = None


class Scenario(Schema):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    description: str
    inputs: Inputs
    expected: Expected
    approval: Literal["approve", "deny", "none"] = "none"
    fault: Literal["transient_read", "commit_before_checkpoint", "occupied_slot"] | None = None


class Suite(Schema):
    schema_version: Literal[1] = 1
    scenarios: list[Scenario] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({s.id for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("Scenario IDs must be unique")
        return self
