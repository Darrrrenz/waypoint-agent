"""Local human decisions; models never receive a decision-making tool."""

from waypoint_agent.calendar import UpdateQuery, arguments_hash
from waypoint_agent.schemas import TrajectoryEvent, utc_now


def pending_request(state):
    request = next((a for a in state.approvals if a.id == state.pending_approval_id), None)
    if request is None:
        raise ValueError("No current approval request")
    query = UpdateQuery.model_validate(request.call.arguments)
    normalized = query.model_dump(mode="json")
    if (
        request.task_id != state.task.id
        or request.calendar_world_id != state.task.calendar_world_id
        or request.call.name != "update_calendar_event"
        or request.event_id != query.event_id
        or request.expected_revision != query.expected_revision
        or normalized != request.call.arguments
        or request.arguments_hash != arguments_hash(normalized)
        or state.pending_action
        != {"kind": "call_tool", "call": request.call.model_dump(mode="json")}
        or request.before.get("id") != query.event_id
        or request.before.get("revision") != query.expected_revision
        or request.after
        != {
            **request.before,
            "start": normalized["start"],
            "end": normalized["end"],
            "revision": query.expected_revision + 1,
        }
        or request.resolved_at is not None
    ):
        raise PermissionError("Approval binding is invalid; a fresh proposal is required")
    return request, query


async def decide(repository, task_id, approval_id, decision):
    if decision not in ("approved", "denied"):
        raise ValueError("Invalid decision")
    async with repository.task_lock(task_id):
        checkpoint, _ = await repository.load(task_id)
        state = checkpoint.state
        request, _ = pending_request(state)
        if request.id != approval_id or state.task.status != "waiting_for_approval":
            raise ValueError("Decision must target the current pending request")
        if request.decision == decision:
            return checkpoint
        if request.decision != "pending":
            raise ValueError("An approval decision cannot be changed")
        request.decision, request.decided_at = decision, utc_now()
        checkpoint.last_sequence += 1
        await repository.save(
            checkpoint,
            TrajectoryEvent(
                task_id=task_id,
                sequence=checkpoint.last_sequence,
                timestamp=utc_now(),
                kind="approval_decided",
                data=request.model_dump(mode="json"),
            ),
        )
        return checkpoint
