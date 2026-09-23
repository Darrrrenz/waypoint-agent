"""Offline presentation of recorded single-task evidence; no runtime verification."""

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from waypoint_agent.schemas import Checkpoint, TrajectoryEvent

NOTICE = (
    "Recorded evidence only. Verification is not re-executed "
    "and export authenticity is not checked."
)
UNKNOWN = "unknown (not recorded)"


class TraceError(ValueError):
    """An unreadable, unsupported, or inconsistent export."""


@dataclass(frozen=True)
class TraceSummary:
    fields: list[tuple[str, str]]
    approvals: list[str]
    timeline: list[tuple[int, str, str]]


def load_export(path: Path) -> tuple[Checkpoint, list[TrajectoryEvent], dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise TraceError(f"Cannot read input: {exc}") from exc
    except ValueError as exc:
        raise TraceError(f"Invalid JSON: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("checkpoint"), dict)
        or not isinstance(payload.get("events"), list)
    ):
        raise TraceError("Expected a single-task export with checkpoint and events")
    try:
        checkpoint = Checkpoint.model_validate(payload["checkpoint"])
        events = [TrajectoryEvent.model_validate(event) for event in payload["events"]]
    except (ValidationError, ValueError, KeyError) as exc:
        # Do not echo raw input (which can contain scripts, credentials, or email bodies).
        if isinstance(exc, ValidationError):
            error = exc.errors(include_input=False, include_context=False)[0]
            location = ".".join(map(str, error["loc"]))
            detail = f"{location}: {error['type']}"
        else:
            detail = type(exc).__name__
        raise TraceError(f"Invalid checkpoint or event schema: {detail}") from exc
    if "id" not in checkpoint.state.task.model_fields_set:
        raise TraceError("Checkpoint task ID must be recorded")
    for expected, event in enumerate(events, start=1):
        if event.task_id != checkpoint.state.task.id:
            raise TraceError(f"Event {event.sequence} belongs to a different task")
        if event.sequence != expected:
            raise TraceError(
                f"Inconsistent sequence: expected {expected}, got {event.sequence}; "
                "events must be ordered and contiguous from 1"
            )
    if checkpoint.last_sequence != len(events):
        raise TraceError(
            f"Final sequence {len(events)} does not match "
            f"checkpoint.last_sequence {checkpoint.last_sequence}"
        )
    return checkpoint, events, payload


def scalar(value) -> str:
    if value is None or isinstance(value, (dict, list)):
        return UNKNOWN
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def selected(data: dict, keys: tuple[str, ...]) -> str:
    return "; ".join(f"{key}: {scalar(data[key])}" for key in keys if key in data)


def identifiers(values) -> str:
    if not isinstance(values, list):
        return UNKNOWN
    ids = [scalar(v.get("id") if isinstance(v, dict) else v) for v in values[:8]]
    suffix = f", ... (+{len(values) - 8})" if len(values) > 8 else ""
    return f"{len(values)} [{', '.join(ids)}{suffix}]"


def findings(data: dict) -> str:
    parts = [f"outcome: {scalar(data.get('outcome'))}"]
    for key in ("meeting_ids", "unread_email_ids", "evidence_ids"):
        if key in data:
            parts.append(f"{key}: {identifiers(data[key])}")
    if "operation_id" in data:
        parts.append(f"operation_id: {scalar(data['operation_id'])}")
    return "; ".join(parts)


def call_description(data: dict) -> str:
    details = selected(
        mapping(data.get("arguments")),
        ("event_id", "email_id", "participant", "start", "end", "expected_revision"),
    )
    return scalar(data.get("name")) + (f"; {details}" if details else "")


def approval_description(data: dict) -> str:
    before, after = mapping(data.get("before")), mapping(data.get("after"))
    return (
        f"approval: {scalar(data.get('id', data.get('approval_id')))}; "
        f"operation: {scalar(data.get('operation_id'))}; "
        f"target event: {scalar(data.get('event_id'))}; "
        f"before: {scalar(before.get('start'))} to {scalar(before.get('end'))}; "
        f"proposed after: {scalar(after.get('start'))} to {scalar(after.get('end'))}; "
        f"decision: {scalar(data.get('decision'))}; "
        f"recorded outcome: {scalar(data.get('outcome'))}"
    )


def result_description(data: dict) -> str:
    output = mapping(data.get("output"))
    parts = [
        f"Recorded tool result: {scalar(mapping(data.get('call')).get('name'))}",
        f"observation: {scalar(data.get('id'))}",
    ]
    for key in ("meetings", "emails"):
        if key in output:
            parts.append(f"{key}: {identifiers(output[key])}")
    if "slots" in output:
        slots = output["slots"]
        parts.append(f"slots: {len(slots) if isinstance(slots, list) else UNKNOWN}")
    for source in (output, mapping(output.get("event"))):
        detail = selected(source, ("id", "operation_id", "start", "end", "revision", "exhaustive"))
        if detail:
            parts.append(detail)
    if len(parts) == 2:
        parts.append("source IDs and counts not recorded in recognized fields")
    return "; ".join(parts)


def event_description(event: TrajectoryEvent) -> str:
    kind, data = event.kind, event.data
    if kind in ("task_started", "task_resumed"):
        return "Task started" if kind == "task_started" else "Task resumed"
    if kind == "model_requested":
        return f"Model call requested; step: {scalar(data.get('step'))}"
    if kind == "model_response":
        action = data.get("action")
        if isinstance(action, str):
            try:
                action = json.loads(action)
            except ValueError:
                return "Model response recorded; unparsed action (payload omitted)"
        action = mapping(action)
        if action.get("kind") == "call_tool":
            return "Model proposed tool: " + call_description(mapping(action.get("call")))
        if action.get("kind") == "propose_completion":
            return "Model proposed completion; " + findings(mapping(action.get("findings")))
        return "Model response recorded; unrecognized action (payload omitted)"
    if kind in ("tool_requested", "proposal_validation"):
        label = (
            "Tool requested (proposal only)" if kind == "tool_requested" else "Proposal validation"
        )
        return f"{label}: {call_description(data)}"
    if kind == "tool_result":
        return result_description(data)
    if kind == "memory_retrieved":
        records = data.get("records")
        description = (
            f"Memory retrieved; namespace: {scalar(data.get('namespace'))}; "
            f"records: {identifiers(records)}"
        )
        context = mapping(data.get("effective_context"))
        preference = mapping(context.get("earliest_meeting_start"))
        if preference:
            description += "; effective preference: " + selected(
                preference, ("earliest", "timezone")
            )
        return description
    if kind in ("approval_requested", "approval_decided"):
        label = (
            "Approval requested" if kind == "approval_requested" else "Approval decision recorded"
        )
        return f"{label} (not execution); {approval_description(data)}"
    labels = {
        "approval_denied": "Approval denied",
        "approval_invalidated": "Approval invalidated; proposal stale",
        "operation_reconcile": "Operation reconciliation lookup (not a new mutation or outcome)",
        "operation_requested": "Operation execution attempt (outcome not yet established)",
        "operation_unresolved": "Operation unresolved; execution outcome unknown",
        "read_attempt": "Read execution attempt",
        "read_retry": "Read retry scheduled",
        "model_error": "Model call failed",
        "invalid_action": "Model action rejected",
        "tool_error": "Tool failed or rejected",
        "evaluation_error": "Completion evaluation failed",
        "completion_verified": "Completion verification recorded",
        "completion_rejected": "Completion rejected",
        "task_terminated": "Task terminated",
        "memory_projection_stored": "Memory projection stored",
        "memory_projection_failed": "Memory projection failed (separate from task completion)",
    }
    if kind not in labels:
        return "Unrecognized event kind; recorded payload omitted"
    detail = selected(
        data,
        (
            "approval_id",
            "operation_id",
            "attempt",
            "accepted",
            "reason",
            "error",
            "operation_absent",
            "record_id",
            "revision",
            "retry",
        ),
    )
    reasons = data.get("reasons")
    if isinstance(reasons, list) and reasons:
        detail += "; reasons: " + "; ".join(scalar(reason) for reason in reasons)
    return labels[kind] + (f"; {detail}" if detail else "")


def summarize(
    checkpoint: Checkpoint, events: list[TrajectoryEvent], metadata: dict
) -> TraceSummary:
    state, task = checkpoint.state, checkpoint.state.task

    def recorded(model, name):
        return scalar(getattr(model, name)) if name in model.model_fields_set else UNKNOWN

    workflow = task.workflow
    if "workflow" not in task.model_fields_set:
        workflow += " (historical default)"
    fields = [
        ("Task ID", str(task.id)),
        ("Workflow", workflow),
        ("Goal", task.goal),
        ("Recorded status", recorded(task, "status")),
        ("Termination reason", recorded(task, "termination_reason")),
        ("Recorded answer", recorded(state, "answer")),
    ]
    if state.findings:
        fields.append(("Recorded findings", findings(state.findings.model_dump(mode="json"))))
    for label, name in (("Model calls", "model_calls"), ("Steps", "steps"), ("Errors", "errors")):
        fields.append((label, recorded(state, name)))
    for label, name in (
        ("Memory strategy", "memory_strategy"),
        ("Memory projection", "memory_projection"),
        ("Memory projection error", "memory_projection_error"),
    ):
        if name in state.model_fields_set and getattr(state, name) is not None:
            fields.append((label, recorded(state, name)))
    for name in ("environment", "decision_source", "backend"):
        if name in metadata:
            value = metadata[name]
            context = (
                selected(value, ("backend", "storage", "kind"))
                if isinstance(value, dict)
                else scalar(value)
            )
            fields.append((f"Export {name}", context or UNKNOWN))
    actions = metadata.get("harness_actions")
    if isinstance(actions, list) and actions:
        for action in actions:
            fields.append(
                (
                    "Harness action",
                    selected(mapping(action), ("actor", "decision", "approval_id", "action")),
                )
            )
    return TraceSummary(
        fields=fields,
        approvals=[
            approval_description(a.model_dump(mode="json", exclude_unset=True))
            for a in state.approvals
        ],
        timeline=[(e.sequence, e.kind, event_description(e)) for e in events],
    )


def markdown_escape(value: str) -> str:
    value = re.sub(r"([\\`*_{}\[\]()#+.!~\-])", r"\\\1", value)
    value = html.escape(value, quote=False).replace("|", "&#124;")
    return "<br>".join(value.splitlines())


def render_text(summary: TraceSummary) -> str:
    def line(value):
        return " / ".join(value.splitlines())

    lines = ["Task trace", NOTICE, ""]
    lines.extend(f"{label}: {line(value)}" for label, value in summary.fields)
    if summary.approvals:
        lines.extend(["", "Approvals (checkpoint state; after values are proposals)"])
        lines.extend(f"- {line(approval)}" for approval in summary.approvals)
    lines.extend(["", "Timeline (recorded sequence order)"])
    lines.extend(f"{seq}. [{line(kind)}] {line(detail)}" for seq, kind, detail in summary.timeline)
    if not summary.timeline:
        lines.append("No events recorded.")
    return "\n".join(lines) + "\n"


def render_markdown(summary: TraceSummary) -> str:
    escape = markdown_escape
    lines = ["# Task trace", "", NOTICE, ""]
    lines.extend(f"- **{label}:** {escape(value)}" for label, value in summary.fields)
    if summary.approvals:
        lines.extend(["", "## Approvals (checkpoint state; after values are proposals)", ""])
        lines.extend(f"- {escape(approval)}" for approval in summary.approvals)
    lines.extend(["", "## Timeline (recorded sequence order)", ""])
    lines.extend(
        f"- **{seq}. {escape(kind)}:** {escape(detail)}" for seq, kind, detail in summary.timeline
    )
    if not summary.timeline:
        lines.append("No events recorded.")
    return "\n".join(lines) + "\n"


def render_export(path: Path, format: str = "text") -> str:
    summary = summarize(*load_export(path))
    if format == "text":
        return render_text(summary)
    if format == "markdown":
        return render_markdown(summary)
    raise TraceError(f"Unsupported output format: {format}")
