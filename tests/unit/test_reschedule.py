import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from waypoint_agent.approval import decide
from waypoint_agent.calendar import MemoryCalendar, TransientReadError
from waypoint_agent.config import Settings, reschedule_context
from waypoint_agent.evaluation.reschedule import RescheduleEvaluator
from waypoint_agent.models.reschedule import RescheduleDemoModel
from waypoint_agent.runtime import Runtime
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.memory import MemoryRepository
from waypoint_agent.tools.calendar import calendar_tools


async def setup(data, repository=None, store=None, **limits):
    repository, store = repository or MemoryRepository(), store or MemoryCalendar()
    world = uuid4()
    await store.seed(world, data.meetings)
    context = reschedule_context(data.clock, "America/Toronto", "alice@example.com")
    state = AgentState(
        task=Task(
            goal="Move Alice to Friday afternoon",
            created_at=data.clock,
            context=context,
            workflow="reschedule",
            calendar_world_id=world,
        ),
        model_kind="reschedule_demo",
        dataset=data.model_dump(mode="json"),
    )

    def fresh():
        return Runtime(
            RescheduleDemoModel(),
            calendar_tools(store, world, context),
            repository,
            RescheduleEvaluator(),
            Settings(_env_file=None, **limits),
        )

    return state, repository, store, fresh


async def approve(repository, checkpoint):
    return await decide(
        repository, checkpoint.state.task.id, checkpoint.state.pending_approval_id, "approved"
    )


async def test_pause_approve_fresh_runtime_and_verified_preservation(data):
    state, repo, store, fresh = await setup(data)
    before = await store.events(state.task.calendar_world_id)
    waiting = await fresh().run(state)
    assert waiting.state.task.status == "waiting_for_approval"
    assert waiting.state.model_calls == 3
    assert await store.events(state.task.calendar_world_id) == before
    assert await fresh().resume(state.task.id) == waiting
    approved = await approve(repo, waiting)
    assert await approve(repo, waiting) == approved
    assert await store.events(state.task.calendar_world_id) == before
    done = await fresh().resume(state.task.id)
    assert done.state.task.status == "completed"
    assert done.state.model_calls == 5
    after = await store.events(state.task.calendar_world_id)
    assert after[1:] == before[1:]
    assert after[0].start == state.task.context.destination_start
    assert after[0].end - after[0].start == before[0].end - before[0].start
    assert after[0].revision == 2
    assert after[0].model_dump(exclude={"start", "end", "revision"}) == before[0].model_dump(
        exclude={"start", "end", "revision"}
    )
    assert await fresh().resume(state.task.id) == done
    _, events = await repo.load(state.task.id)
    assert [e.sequence for e in events] == list(range(1, done.last_sequence + 1))


async def test_denial_and_conflicting_decisions(data):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    request = waiting.state.pending_approval_id
    await decide(repo, state.task.id, request, "denied")
    with pytest.raises(ValueError, match="cannot be changed"):
        await decide(repo, state.task.id, request, "approved")
    assert (await fresh().resume(state.task.id)).state.task.status == "denied"
    assert await store.events(state.task.calendar_world_id) == data.meetings
    assert not store.operations


@pytest.mark.parametrize("variant", ["missing", "ambiguous", "full"])
async def test_no_mutation_for_unschedulable_cases(data, variant):
    if variant == "missing":
        data.meetings = data.meetings[1:]
    elif variant == "ambiguous":
        data.meetings.append(data.meetings[0].model_copy(update={"id": "another"}))
    else:
        ctx = reschedule_context(data.clock, "America/Toronto", "alice@example.com")
        data.meetings[1].start, data.meetings[1].end = ctx.destination_start, ctx.destination_end
    state, _, store, fresh = await setup(data)
    result = await fresh().run(state)
    assert result.state.task.status == "completed"
    assert (
        result.state.findings.outcome
        == {
            "missing": "missing_meeting",
            "ambiguous": "ambiguous",
            "full": "no_availability",
        }[variant]
    )
    assert await store.events(state.task.calendar_world_id) == data.meetings
    assert not store.operations


@pytest.mark.parametrize("change", ["revision", "conflict"])
async def test_changed_calendar_requires_new_approval(data, change):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    events = store.worlds[state.task.calendar_world_id]
    if change == "revision":
        events[0].revision += 1
    else:
        events[1].start = state.task.context.destination_start
        events[1].end = events[1].start + timedelta(minutes=30)
    before = await store.events(state.task.calendar_world_id)
    next_request = await fresh().resume(state.task.id)
    assert next_request.state.task.status == "waiting_for_approval"
    assert next_request.state.pending_approval_id != waiting.state.pending_approval_id
    assert next_request.state.approvals[0].outcome == "stale"
    assert await store.events(state.task.calendar_world_id) == before
    assert not store.operations
    with pytest.raises(ValueError):
        await approve(repo, waiting)
    await approve(repo, next_request)
    assert (await fresh().resume(state.task.id)).state.task.status == "completed"


@pytest.mark.parametrize("alter", ["arguments", "task", "world", "pending", "hash", "before"])
async def test_altered_approval_cannot_authorize_write(data, alter):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    with pytest.raises(ValueError):
        await decide(repo, state.task.id, uuid4(), "approved")
    await approve(repo, waiting)
    # Simulate corrupted storage; no model or CLI exposes these edits.
    saved = repo.records[state.task.id][0].state
    request = saved.approvals[0]
    if alter == "arguments":
        request.call.arguments["expected_revision"] = 5
    elif alter == "task":
        request.task_id = uuid4()
    elif alter == "world":
        request.calendar_world_id = uuid4()
    elif alter == "pending":
        saved.pending_action["call"]["arguments"]["event_id"] = "other"
    elif alter == "hash":
        request.arguments_hash = "wrong"
    else:
        request.before["id"] = "wrong"
    with pytest.raises(PermissionError):
        await fresh().resume(state.task.id)
    assert not store.operations


async def test_transient_read_retries_within_budget(data):
    state, _, store, fresh = await setup(data, retry_backoff=0)
    runtime = fresh()
    tool = runtime.tools.get("search_calendar")
    real = tool.execute
    attempts = 0

    async def flaky(arguments):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientReadError("temporarily unavailable")
        return await real(arguments)

    tool.execute = flaky
    result = await runtime.run(state)
    assert result.state.task.status == "waiting_for_approval"
    assert attempts == 3
    assert result.state.model_calls == 3
    assert not store.operations


async def test_concurrent_resumes_apply_once(data):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    results = await asyncio.gather(
        *(fresh().resume(state.task.id) for _ in range(4)), return_exceptions=True
    )
    assert any(not isinstance(r, Exception) and r.state.task.status == "completed" for r in results)
    assert all(not isinstance(r, Exception) or "busy" in str(r) for r in results)
    assert len(store.operations) == 1
    assert (await store.events(state.task.calendar_world_id))[0].revision == 2
