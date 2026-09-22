from waypoint_agent.calendar import OperationResult, UpdateQuery, arguments_hash, preference_allows
from waypoint_agent.schemas import EvaluationResult
from waypoint_agent.tools.calendar import Availability
from waypoint_agent.tools.mock import CalendarQuery, CalendarResults, Meeting


class RescheduleEvaluator:
    """Completion requires trusted execution provenance and a subsequent matching read."""

    def verify(self, state, findings):
        def reject(reason):
            return EvaluationResult(accepted=False, reasons=[reason])

        refs = set(findings.evidence_ids)
        observations = state.observations
        if (
            not refs
            or not refs <= {o.id for o in observations}
            or len(refs) != len(findings.evidence_ids)
            or len(set(findings.meeting_ids)) != len(findings.meeting_ids)
        ):
            return reject("Cite unique recorded observations and meeting IDs")
        ctx = state.task.context
        if findings.outcome == "rescheduled":
            matches = [e for e in state.executions if str(e.operation_id) == findings.operation_id]
            if len(matches) != 1:
                return reject("Need a confirmed runtime execution for the exact operation")
            execution = matches[0]
            approval = next((a for a in state.approvals if a.id == execution.approval_id), None)
            if (
                approval is None
                or approval.decision != "approved"
                or approval.outcome != "applied"
                or approval.task_id != state.task.id
                or approval.calendar_world_id != state.task.calendar_world_id
                or approval.operation_id != execution.operation_id
                or approval.arguments_hash != execution.arguments_hash
                or approval.arguments_hash != arguments_hash(approval.call.arguments)
            ):
                return reject("Execution is not bound to the approved request")
            query = UpdateQuery.model_validate(approval.call.arguments)
            before = Meeting.model_validate(approval.before)
            after = Meeting.model_validate(approval.after)
            result = OperationResult.model_validate(execution.result)
            if (
                result.operation_id != execution.operation_id
                or result.arguments_hash != approval.arguments_hash
                or result.event != after
                or after.id != approval.event_id
                or query.event_id != after.id
                or before.revision != query.expected_revision
                or after.revision != before.revision + 1
                or after.start != query.start
                or after.end != query.end
                or after.end - after.start != before.end - before.start
                or after.model_dump(exclude={"start", "end", "revision"})
                != before.model_dump(exclude={"start", "end", "revision"})
                or not ctx.destination_start <= after.start < after.end <= ctx.destination_end
                or not preference_allows(after.start, ctx)
                or findings.meeting_ids != [after.id]
            ):
                return reject("Write outcome differs from approved event or preserved fields")
            index = next(
                (i for i, o in enumerate(observations) if o.id == execution.observation_id), None
            )
            if (
                index is None
                or execution.observation_id not in refs
                or observations[index].call != approval.call
                or observations[index].output != execution.result
            ):
                return reject("Need the exact confirmed write observation")
            reads = [
                o
                for o in observations[index + 1 :]
                if o.id in refs
                and o.call.name == "read_calendar_event"
                and o.call.arguments.get("event_id") == after.id
            ]
            if not reads or Meeting.model_validate(reads[-1].output) != after:
                return reject("Need a subsequent authoritative read matching the approved update")
            return EvaluationResult(
                accepted=True,
                reasons=[],
                answer=(
                    f"Rescheduled {after.title} [{after.id}] to {after.start.isoformat()}"
                    f"–{after.end.isoformat()}. Verified operation {execution.operation_id}."
                ),
            )
        if findings.operation_id is not None or state.executions:
            return reject("A no-write outcome cannot contain an execution")
        searches = []
        for observation in observations:
            if observation.id in refs and observation.call.name == "search_calendar":
                query = CalendarQuery.model_validate(observation.call.arguments)
                result = CalendarResults.model_validate(observation.output)
                if (
                    query.participant == ctx.participant
                    and query.start == ctx.start
                    and query.end == ctx.end
                    and result.exhaustive
                ):
                    searches.append(result)
        if not searches:
            return reject("Need an exhaustive search of the task search window")
        meetings = searches[-1].meetings
        if set(findings.meeting_ids) != {m.id for m in meetings}:
            return reject("Identify all matching meetings")
        if not meetings:
            expected, answer = "missing_meeting", "No matching meeting found."
        elif len(meetings) > 1:
            expected, answer = "ambiguous", "Multiple meetings match; clarify the meeting."
        else:
            available = [
                Availability.model_validate(o.output)
                for o in observations
                if o.id in refs
                and o.call.name == "check_availability"
                and o.call.arguments.get("event_id") == meetings[0].id
            ]
            if (
                not available
                or available[-1].event != meetings[0]
                or not available[-1].exhaustive
                or available[-1].slots
                or available[-1].start != ctx.destination_start
                or available[-1].end != ctx.destination_end
                or available[-1].slot_minutes != ctx.slot_minutes
                or available[-1].earliest_meeting_start != ctx.earliest_meeting_start
            ):
                return reject("Need exhaustive evidence of no available destination slots")
            expected, answer = "no_availability", "No Friday-afternoon slot is available."
        if findings.outcome != expected:
            return reject("Outcome contradicts recorded evidence")
        return EvaluationResult(accepted=True, reasons=[], answer=answer)
