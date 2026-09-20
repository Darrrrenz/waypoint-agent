from waypoint_agent.schemas import AgentState, EvaluationResult, Findings
from waypoint_agent.tools.mock import (
    CalendarQuery,
    CalendarResults,
    Email,
    EmailQuery,
    EmailResults,
)


class MeetingEmailEvaluator:
    """Verify this workflow against observations, never the source dataset or an answer key."""

    def verify(self, state: AgentState, findings: Findings) -> EvaluationResult:
        def reject(reason: str):
            return EvaluationResult(accepted=False, reasons=[reason])

        refs = set(findings.evidence_ids)
        available = {o.id: o for o in state.observations}
        if not refs or not refs <= available.keys():
            return reject("Evidence must reference recorded successful observations")
        if any(
            len(values) != len(set(values))
            for values in (findings.meeting_ids, findings.unread_email_ids, findings.evidence_ids)
        ):
            return reject("Duplicate findings or evidence IDs")
        observations = [o for o in state.observations if o.id in refs]
        ctx = state.task.context
        calendars = []
        for o in observations:
            if o.call.name == "search_calendar":
                q = CalendarQuery.model_validate(o.call.arguments)
                result = CalendarResults.model_validate(o.output)
                if (
                    q.participant == ctx.participant
                    and q.start == ctx.start
                    and q.end == ctx.end
                    and result.exhaustive
                ):
                    calendars.append(result)
        if not calendars:
            return reject("Need an exhaustive calendar search covering the exact task scope")
        meetings = calendars[-1].meetings
        if any(
            ctx.participant not in m.participants or not ctx.start <= m.start < ctx.end
            for m in meetings
        ):
            return reject("Calendar results contradict the task scope")
        if set(findings.meeting_ids) != {m.id for m in meetings}:
            return reject("Findings must identify every plausible meeting")
        if not meetings:
            outcome = "missing_meeting"
            answer = "No meeting found in the requested next-week period."
            expected_emails = set()
        elif len(meetings) > 1:
            outcome = "ambiguous"
            answer = "Ambiguous: multiple meetings match; clarification is required. " + "; ".join(
                f"{m.title} at {m.start.isoformat()} [{m.id}]" for m in meetings
            )
            expected_emails = set()
        else:
            meeting = meetings[0]
            searches = []
            for o in observations:
                if o.call.name == "search_email":
                    q = EmailQuery.model_validate(o.call.arguments)
                    result = EmailResults.model_validate(o.output)
                    if (
                        q.participant == ctx.participant
                        and q.topic == meeting.topic
                        and result.exhaustive
                    ):
                        searches.append(result)
            if not searches:
                return reject(
                    "Need an exhaustive email search for the participant and meeting topic"
                )
            emails = [
                e
                for e in searches[-1].emails
                if e.unread and ctx.participant in e.participants and e.topic == meeting.topic
            ]
            expected_emails = {e.id for e in emails}
            reads = {
                o.output.get("id"): Email.model_validate(o.output)
                for o in observations
                if o.call.name == "read_email"
                and o.call.arguments.get("email_id") == o.output.get("id")
            }
            if any(reads.get(e.id) != e for e in emails):
                return reject("Each unread finding needs a matching read_email observation")
            outcome = "found" if emails else "no_unread"
            answer = f"Meeting: {meeting.title}, {meeting.start.isoformat()} [{meeting.id}]. "
            answer += (
                (
                    "Related unread emails: "
                    + "; ".join(f"{e.subject} [{e.id}]" for e in emails)
                    + "."
                )
                if emails
                else "No related unread emails."
            )
        if findings.outcome != outcome or set(findings.unread_email_ids) != expected_emails:
            return reject("Proposed outcome or email IDs do not match the observed evidence")
        return EvaluationResult(
            accepted=True, reasons=[], answer=answer + " Evidence: " + ", ".join(sorted(refs))
        )
