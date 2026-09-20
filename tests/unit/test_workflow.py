from datetime import datetime

import pytest

from waypoint_agent.config import next_week
from waypoint_agent.tools.mock import mock_tools


async def test_alice_workflow(make_runtime, script, state):
    runtime = make_runtime(script)
    checkpoint = await runtime.run(state)
    result = checkpoint.state
    assert result.task.status == "completed"
    assert result.findings.meeting_ids == ["cal-alice-001"]
    assert result.findings.unread_email_ids == ["mail-alice-unread"]
    assert "2026-09-22T10:00:00-04:00" in result.answer
    assert "mail-alice-unread" in result.answer
    assert "mail-alice-read" not in result.answer
    assert "mail-alice-unrelated" not in result.answer
    assert "mail-bob-unread" not in result.answer
    saved, events = await runtime.repository.load(result.task.id)
    assert saved == checkpoint
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert events[-1].kind == "completion_verified"
    assert all(e.usage is None for e in events)
    assert all(e.latency_ms is not None for e in events if e.kind == "tool_result")


@pytest.mark.parametrize("scenario", ["missing_meeting", "no_unread", "ambiguous"])
async def test_valid_alternative_outcomes(scenario, data, state, script, make_runtime):
    findings = script[-1]["findings"]
    findings["outcome"] = scenario
    findings["unread_email_ids"] = []
    if scenario == "missing_meeting":
        data.meetings = []
        findings["meeting_ids"] = []
        findings["evidence_ids"] = ["obs-1"]
        actions = [script[0], script[-1]]
    elif scenario == "ambiguous":
        data.meetings.append(data.meetings[0].model_copy(update={"id": "cal-second"}))
        findings["meeting_ids"].append("cal-second")
        findings["evidence_ids"] = ["obs-1"]
        actions = [script[0], script[-1]]
    else:
        for email in data.emails:
            email.unread = False
        findings["evidence_ids"] = ["obs-1", "obs-2"]
        actions = [script[0], script[1], script[-1]]
    checkpoint = await make_runtime(actions, tools=mock_tools(data)).run(state)
    assert checkpoint.state.task.status == "completed"
    assert checkpoint.state.findings.outcome == scenario
    if scenario == "ambiguous":
        assert "clarification" in checkpoint.state.answer


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_evidence",
        "wrong_email",
        "missing_read",
        "wrong_scope",
        "false_absence",
        "omit_meeting",
        "duplicate",
    ],
)
async def test_unsupported_findings_rejected(mutation, script, state, make_runtime):
    findings = script[-1]["findings"]
    if mutation == "unknown_evidence":
        findings["evidence_ids"] = ["invented"]
    elif mutation == "wrong_email":
        findings["unread_email_ids"] = ["mail-alice-unrelated"]
    elif mutation == "missing_read":
        script.pop(2)
        findings["evidence_ids"] = ["obs-1", "obs-2"]
    elif mutation == "wrong_scope":
        script[0]["call"]["arguments"]["participant"] = "bob@example.com"
    elif mutation == "false_absence":
        findings.update(outcome="no_unread", unread_email_ids=[])
    elif mutation == "omit_meeting":
        findings["meeting_ids"] = []
    else:
        findings["meeting_ids"] *= 2
    runtime = make_runtime(script, max_errors=1)
    result = await runtime.run(state)
    assert result.state.task.status == "failed"
    assert result.state.answer is None
    _, events = await runtime.repository.load(state.task.id)
    assert any(e.kind == "completion_rejected" for e in events)


def test_next_week_is_next_monday_even_on_monday_and_handles_dst():
    ctx = next_week(
        datetime.fromisoformat("2026-10-26T12:00:00-04:00"), "America/Toronto", "alice@example.com"
    )
    assert ctx.start.isoformat() == "2026-11-02T00:00:00-05:00"
    assert ctx.end.isoformat() == "2026-11-09T00:00:00-05:00"
    ctx = next_week(
        datetime.fromisoformat("2026-10-25T12:00:00-04:00"), "America/Toronto", "alice@example.com"
    )
    assert ctx.start.utcoffset() != ctx.end.utcoffset()


def test_naive_clock_rejected():
    with pytest.raises(ValueError, match="timezone aware"):
        next_week(datetime(2026, 9, 20), "America/Toronto", "alice@example.com")


async def test_partial_search_cannot_establish_absence(data, script, state, make_runtime):
    tools = mock_tools(data)

    async def partial(arguments):
        return {"emails": [], "exhaustive": False}

    tools.get("search_email").execute = partial
    script[-1]["findings"].update(
        outcome="no_unread", unread_email_ids=[], evidence_ids=["obs-1", "obs-2"]
    )
    checkpoint = await make_runtime(
        [script[0], script[1], script[-1]], tools=tools, max_errors=1
    ).run(state)
    assert checkpoint.state.task.status == "failed"


async def test_unfiltered_search_still_excludes_read_emails(script, state, make_runtime):
    script[1]["call"]["arguments"]["unread_only"] = False
    checkpoint = await make_runtime(script).run(state)
    assert checkpoint.state.task.status == "completed"
    assert checkpoint.state.findings.unread_email_ids == ["mail-alice-unread"]
