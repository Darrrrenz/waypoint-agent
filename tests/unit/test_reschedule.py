import asyncio
from datetime import timedelta
from pathlib import Path
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

ROOT = Path(__file__).resolve().parents[2]


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


class CrashAfterCommit(MemoryRepository):
    crash = True

    async def save(self, checkpoint, event):
        if (
            self.crash
            and event.kind == "tool_result"
            and event.data["call"]["name"] == "update_calendar_event"
        ):
            self.crash = False
            raise OSError("Process died before observation checkpoint")
        await super().save(checkpoint, event)


async def test_crash_after_commit_reconciles_without_another_write(data):
    state, repo, store, fresh = await setup(data, repository=CrashAfterCommit())
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    with pytest.raises(OSError):
        await fresh().resume(state.task.id)
    assert len(store.operations) == 1
    durable, _ = await repo.load(state.task.id)
    assert not durable.state.executions
    assert durable.state.task.status == "running"

    async def must_not_write(*args):
        pytest.fail("Committed operation was executed twice")

    store.update = must_not_write
    result = await fresh().resume(state.task.id)
    assert result.state.task.status == "completed"
    assert (
        result.state.active_seconds >= durable.state.active_seconds + durable.state.reserved_seconds
    )
    assert len(result.state.executions) == 1
    assert (await store.events(state.task.calendar_world_id))[0].revision == 2


@pytest.mark.parametrize("committed", [False, True])
async def test_uncertain_write_is_unresolved_then_reconciles(data, committed):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    real = store.update

    async def uncertain(*args):
        if committed:
            await real(*args)
        raise OSError("Connection lost")

    store.update = uncertain
    result = await fresh().resume(state.task.id)
    assert result.state.task.status == "unresolved"
    assert result.state.answer is None
    assert not result.state.executions
    assert len(store.operations) == int(committed)
    store.update = real
    result = await fresh().resume(state.task.id)
    assert result.state.task.status == "completed"
    assert len(store.operations) == 1


async def test_ledger_unavailable_never_retries_write(data):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    await approve(repo, waiting)

    async def unavailable(*args):
        raise OSError("Ledger offline")

    store.lookup = unavailable
    for _ in range(2):
        assert (await fresh().resume(state.task.id)).state.task.status == "unresolved"
    assert not store.operations


async def test_script_and_pending_action_survive_restart(data):
    from waypoint_agent.models.scripted import ScriptedModel

    class InterruptRead(MemoryRepository):
        crash = True

        async def save(self, checkpoint, event):
            await super().save(checkpoint, event)
            if self.crash and event.kind == "model_response":
                self.crash = False
                raise OSError("Process interrupted after saving response")

    state, repo, _, fresh = await setup(data, repository=InterruptRead())
    # Capture a demo action sequence; use a finite script across fresh runtimes.
    demo_state, demo_repo, _, demo_fresh = await setup(data)
    await demo_fresh().run(demo_state)
    _, events = await demo_repo.load(demo_state.task.id)
    script = [e.data["action"] for e in events if e.kind == "model_response"]
    runtime = fresh()
    runtime.model = ScriptedModel(script)
    with pytest.raises(OSError):
        await runtime.run(state)
    saved, _ = await repo.load(state.task.id)
    assert saved.state.model_calls == 1
    assert saved.state.pending_action == script[0]
    result = await fresh().resume(state.task.id)
    assert result.state.task.status == "waiting_for_approval"
    assert result.state.model_calls == 3
    _, events = await repo.load(state.task.id)
    assert len([e for e in events if e.kind == "model_requested"]) == 3


async def test_budgets_are_persisted_and_cannot_be_reset_on_resume(data):
    state, repo, store, fresh = await setup(data, max_model_calls=3)
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    runtime = fresh()
    runtime.settings.max_model_calls = 100
    result = await runtime.resume(state.task.id)
    assert result.state.task.status == "limited"
    assert result.state.task.termination_reason == "max_model_calls"
    assert result.state.model_calls == 3
    assert result.state.answer is None  # Applied but not verified; no success claim.
    assert len(store.operations) == 1
    assert await fresh().resume(state.task.id) == result


async def test_expired_approved_task_cannot_write(data):
    state, repo, store, fresh = await setup(data)
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    repo.records[state.task.id][0].state.active_seconds = 120
    result = await fresh().resume(state.task.id)
    assert result.state.task.termination_reason == "deadline"
    assert not store.operations


async def test_wait_time_does_not_consume_execution_budget(data):
    state, repo, _, fresh = await setup(data)
    waiting = await fresh().run(state)
    await asyncio.sleep(0.02)
    assert (
        await fresh().resume(state.task.id)
    ).state.active_seconds == waiting.state.active_seconds
    await approve(repo, waiting)
    assert (await fresh().resume(state.task.id)).state.task.status == "completed"


@pytest.mark.parametrize("tamper", ["read", "operation", "fields", "provenance", "order"])
async def test_completion_requires_exact_execution_and_subsequent_read(data, tamper):
    state, repo, _, fresh = await setup(data)
    waiting = await fresh().run(state)
    await approve(repo, waiting)
    result = (await fresh().resume(state.task.id)).state
    if tamper == "read":
        result.observations[-1].output["start"] = "2026-09-25T17:15:00Z"
    elif tamper == "operation":
        result.findings.operation_id = str(uuid4())
    elif tamper == "fields":
        result.executions[0].result["event"]["title"] = "Changed without approval"
    elif tamper == "provenance":
        result.executions = []
    else:
        result.observations[-1], result.observations[-2] = (
            result.observations[-2],
            result.observations[-1],
        )
    assert not RescheduleEvaluator().verify(result, result.findings).accepted


async def test_failed_approval_checkpoint_is_fatal_and_never_mutates(data):
    class Broken(MemoryRepository):
        async def save(self, checkpoint, event):
            if event.kind == "approval_requested":
                raise OSError("Storage offline")
            await super().save(checkpoint, event)

    state, repo, store, fresh = await setup(data, repository=Broken())
    with pytest.raises(OSError):
        await fresh().run(state)
    saved, _ = await repo.load(state.task.id)
    assert not saved.state.approvals
    assert not store.operations


async def test_permission_policy_fails_closed(data):
    from waypoint_agent.tools.registry import Registry

    state, _, _, fresh = await setup(data)
    tool = fresh().tools.get("search_calendar")
    tool.risk_level = "arbitrary_write"
    with pytest.raises(PermissionError):
        Registry([tool])


async def test_http_reschedule_schema_and_full_flow(data):
    import json

    import httpx
    from pydantic import SecretStr

    from waypoint_agent.models.openai_compatible import OpenAICompatibleModel
    from waypoint_agent.schemas import RescheduleFindings

    state, repo, _, fresh = await setup(data)
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["tools"][-1]["function"]["parameters"] == RescheduleFindings.model_json_schema()
        supplied = json.loads(body["messages"][1]["content"])
        persisted = AgentState.model_validate(supplied["state"])
        action = (await RescheduleDemoModel().next_action(persisted, [])).action
        function = (
            {"name": action["call"]["name"], "arguments": json.dumps(action["call"]["arguments"])}
            if action["kind"] == "call_tool"
            else {"name": "propose_completion", "arguments": json.dumps(action["findings"])}
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"tool_calls": [{"function": function}]}}]}
        )

    def live():
        runtime = fresh()
        runtime.model = OpenAICompatibleModel(
            Settings(_env_file=None, api_key=SecretStr("test")),
            transport=httpx.MockTransport(handler),
        )
        return runtime

    waiting = await live().run(state)
    await approve(repo, waiting)
    assert (await live().resume(state.task.id)).state.task.status == "completed"
    assert calls == 5


async def test_finite_script_resumes_approved_action_and_completes(data):
    import json

    from waypoint_agent.models.scripted import ScriptedModel

    state, repo, store, fresh = await setup(data)
    runtime = fresh()
    script = ROOT / "fixtures/reschedule-script.json"
    runtime.model = ScriptedModel(json.loads(script.read_text(encoding="utf-8")))
    waiting = await runtime.run(state)
    assert waiting.state.model_calls == 3
    await approve(repo, waiting)
    result = await fresh().resume(state.task.id)
    assert result.state.task.status == "completed"
    assert result.state.model_calls == 5
    assert len(store.operations) == 1


async def test_transient_read_exhaustion_is_bounded(data):
    state, _, _, fresh = await setup(data, max_errors=1, retry_backoff=0)
    runtime = fresh()
    attempts = 0

    async def unavailable(arguments):
        nonlocal attempts
        attempts += 1
        raise TransientReadError("Unavailable")

    runtime.tools.get("search_calendar").execute = unavailable
    result = await runtime.run(state)
    assert result.state.task.termination_reason == "error_budget"
    assert result.state.model_calls == 1
    assert attempts == 3


@pytest.mark.parametrize("field", ["operation_id", "approval_id"])
async def test_model_cannot_supply_authorization_metadata(data, field):
    from waypoint_agent.models.scripted import ScriptedModel

    state, _, store, fresh = await setup(data, max_errors=1)
    ctx = state.task.context
    runtime = fresh()
    runtime.model = ScriptedModel(
        [
            {
                "kind": "call_tool",
                "call": {
                    "name": "update_calendar_event",
                    "arguments": {
                        "event_id": data.meetings[0].id,
                        "expected_revision": 1,
                        "start": ctx.destination_start.isoformat(),
                        "end": (ctx.destination_start + timedelta(minutes=30)).isoformat(),
                        field: str(uuid4()),
                    },
                },
            }
        ]
    )
    result = await runtime.run(state)
    assert result.state.task.status == "failed"
    assert not result.state.approvals
    assert not store.operations
