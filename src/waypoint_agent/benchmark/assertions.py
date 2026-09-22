"""Independent source/final-world oracle: never calls the runtime evaluator or slots()."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from waypoint_agent.tools.mock import Meeting


def score(scenario, state, before, after, operations):
    expected = scenario.expected
    checks = []

    def check(name, ok, detail):
        checks.append({"name": name, "passed": bool(ok), "detail": "" if ok else detail})

    check("terminal", state.task.status == expected.status, f"Got {state.task.status}")
    findings = state.findings
    check(
        "outcome",
        (findings.outcome if findings else None) == expected.outcome,
        f"Expected {expected.outcome}; got {findings}",
    )
    check(
        "meeting_ids",
        sorted(findings.meeting_ids if findings else []) == sorted(expected.meeting_ids),
        "Meeting source IDs differ",
    )
    check(
        "email_ids",
        sorted(getattr(findings, "unread_email_ids", [])) == sorted(expected.email_ids),
        "Email source IDs differ",
    )
    # Compare the complete authoritative world, including all preserved fields.
    desired = []
    for event in before:
        values = event.model_dump(mode="json")
        values.update(expected.changes.get(event.id, {}))
        desired.append(Meeting.model_validate(values))
    check(
        "authoritative_calendar",
        {m.id: m for m in after} == {m.id: m for m in desired},
        "Authoritative calendar differs from the declared allowed changes",
    )
    check("approval_count", len(state.approvals) == expected.approvals, "Wrong approval count")
    check("operation_count", len(operations) == expected.operations, "Wrong ledger count")
    for execution in state.executions:
        approval = next((a for a in state.approvals if a.id == execution.approval_id), None)
        ledger = next((o for o in operations if o.operation_id == execution.operation_id), None)
        check(
            "authorized_operation",
            approval is not None
            and approval.decision == "approved"
            and approval.outcome == "applied"
            and ledger is not None
            and ledger.event == Meeting.model_validate(approval.after),
            "Execution lacks an approved, matching authoritative ledger entry",
        )
    if expected.outcome == "rescheduled":
        check("execution_present", len(state.executions) == 1, "Need exactly one execution")
        ctx = state.task.context
        event = next((m for m in after if m.id in expected.meeting_ids), None)
        check(
            "destination_bounds",
            event is not None
            and ctx.destination_start <= event.start < event.end <= ctx.destination_end,
            "Moved event outside the explicit window",
        )
        if event:
            check(
                "no_overlap",
                not any(
                    m.id != event.id and m.start < event.end and event.start < m.end for m in after
                ),
                "Moved event overlaps",
            )
    if expected.outcome == "no_availability":
        ctx = state.task.context
        event = next(m for m in before if m.id in expected.meeting_ids)
        candidate = ctx.destination_start
        free = []
        while candidate + (event.end - event.start) <= ctx.destination_end:
            end = candidate + (event.end - event.start)
            pref = ctx.earliest_meeting_start
            meets_preference = pref is None or (
                candidate.astimezone(ZoneInfo(pref.timezone)).strftime("%H:%M") >= pref.earliest
            )
            if meets_preference and not any(
                m.id != event.id and m.start < end and candidate < m.end for m in after
            ):
                free.append(candidate)
            candidate += timedelta(minutes=ctx.slot_minutes)
        check("independent_no_availability", not free, "An available slot exists")
    if expected.email_ids:
        source = {e.id: e for e in scenario.inputs.dataset.emails}
        meetings = {m.id: m for m in before}
        ctx = state.task.context
        check(
            "email_relevance",
            all(
                e in source
                and source[e].unread
                and ctx.participant in source[e].participants
                and source[e].topic
                in {meetings[m].topic for m in expected.meeting_ids if m in meetings}
                for e in expected.email_ids
            ),
            "Reported email is read or unrelated",
        )
        read_ids = {o.output.get("id") for o in state.observations if o.call.name == "read_email"}
        check("email_reads", set(expected.email_ids) <= read_ids, "Missing source reads")
    if scenario.fault == "occupied_slot":
        check(
            "fresh_approval",
            len(state.approvals) == 2
            and state.approvals[0].outcome == "stale"
            and state.approvals[0].id != state.approvals[1].id
            and datetime.fromisoformat(state.approvals[0].call.arguments["start"])
            != datetime.fromisoformat(state.approvals[1].call.arguments["start"]),
            "Changed slot did not require fresh approval",
        )
    return checks
