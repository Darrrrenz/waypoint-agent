import asyncio

import pytest

from waypoint_agent.schemas import ModelReply
from waypoint_agent.tools.mock import mock_tools


@pytest.mark.parametrize(
    "action,kind",
    [
        ("not JSON", "invalid_action"),
        ({"kind": "pretend_success"}, "invalid_action"),
        ({"kind": "call_tool", "call": {"name": "delete_email", "arguments": {}}}, "tool_error"),
        ({"kind": "call_tool", "call": {"name": "read_email", "arguments": {}}}, "tool_error"),
        (
            {"kind": "call_tool", "call": {"name": "read_email", "arguments": {"email_id": 1}}},
            "tool_error",
        ),
        (
            {
                "kind": "call_tool",
                "call": {"name": "read_email", "arguments": {"email_id": "none"}},
            },
            "tool_error",
        ),
    ],
)
async def test_errors_are_bounded_and_inspectable(action, kind, state, make_runtime):
    runtime = make_runtime([action] * 10, max_errors=2)
    checkpoint = await runtime.run(state)
    assert checkpoint.state.task.termination_reason == "error_budget"
    assert checkpoint.state.model_calls == 2
    _, events = await runtime.repository.load(state.task.id)
    failures = [e for e in events if e.kind == kind]
    assert len(failures) == 2
    assert all("error" in e.data for e in failures)


@pytest.mark.parametrize(
    "limit,reason", [("max_steps", "max_steps"), ("max_model_calls", "max_model_calls")]
)
async def test_repeated_valid_actions_stop(limit, reason, script, state, make_runtime):
    checkpoint = await make_runtime([script[0]] * 20, **{limit: 2}).run(state)
    assert checkpoint.state.task.status == "limited"
    assert checkpoint.state.task.termination_reason == reason
    assert checkpoint.state.model_calls == 2


class SlowModel:
    async def next_action(self, state, tools):
        await asyncio.sleep(1)
        return ModelReply(action={})


@pytest.mark.parametrize("overall", [False, True])
async def test_model_timeout_and_overall_deadline(overall, state, make_runtime):
    runtime = make_runtime(
        model=SlowModel(),
        max_errors=2,
        model_timeout=1 if overall else 0.01,
        deadline_seconds=0.02 if overall else 5,
    )
    checkpoint = await runtime.run(state)
    assert checkpoint.state.task.termination_reason == ("deadline" if overall else "error_budget")
    _, events = await runtime.repository.load(state.task.id)
    assert any(e.kind == "model_error" and "TimeoutError" in e.data["error"] for e in events)


@pytest.mark.parametrize("failure", ["timeout", "invalid_output", "exception"])
async def test_tool_failures(failure, data, script, state, make_runtime):
    tools = mock_tools(data)

    async def broken(arguments):
        if failure == "timeout":
            await asyncio.sleep(1)
        if failure == "exception":
            raise RuntimeError("test failure")
        return {"unvalidated": True}

    tools.get("search_calendar").execute = broken
    runtime = make_runtime([script[0]] * 3, tools=tools, tool_timeout=0.01, max_errors=2)
    checkpoint = await runtime.run(state)
    assert checkpoint.state.task.termination_reason == "error_budget"
    assert not checkpoint.state.observations
    _, events = await runtime.repository.load(state.task.id)
    assert len([e for e in events if e.kind == "tool_error"]) == 2


async def test_model_can_correct_an_error(script, state, make_runtime):
    checkpoint = await make_runtime([{}, *script]).run(state)
    assert checkpoint.state.task.status == "completed"
    assert checkpoint.state.errors == 1


async def test_completion_request_without_observations_fails(script, state, make_runtime):
    checkpoint = await make_runtime([script[-1]], max_errors=1).run(state)
    assert checkpoint.state.task.status == "failed"


async def test_storage_failure_does_not_execute_model(state, make_runtime):
    class BrokenRepository:
        async def save(self, checkpoint, event):
            raise OSError("storage offline")

    class MustNotRun:
        async def next_action(self, state, tools):
            pytest.fail("Model called after failed persistence")

    with pytest.raises(OSError):
        await make_runtime(repository=BrokenRepository(), model=MustNotRun()).run(state)


async def test_slow_storage_is_bounded(state, make_runtime):
    class SlowRepository:
        async def save(self, checkpoint, event):
            await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        await make_runtime(repository=SlowRepository(), storage_timeout=0.01).run(state)


async def test_expired_deadline_does_not_execute_immediate_tool(script, state, make_runtime, data):
    from waypoint_agent.storage.memory import MemoryRepository

    class DelayedRepository(MemoryRepository):
        async def save(self, checkpoint, event):
            if event.kind == "tool_requested":
                await asyncio.sleep(0.03)
            await super().save(checkpoint, event)

    tools = mock_tools(data)

    async def must_not_run(arguments):
        pytest.fail("A tool ran after the deadline")

    tools.get("search_calendar").execute = must_not_run
    checkpoint = await make_runtime(
        script, tools=tools, repository=DelayedRepository(), deadline_seconds=0.02
    ).run(state)
    assert checkpoint.state.task.termination_reason == "deadline"
    assert not checkpoint.state.observations


async def test_evaluator_errors_are_inspectable(script, state, make_runtime):
    class BrokenEvaluator:
        def verify(self, state, findings):
            raise ValueError("broken evaluator")

    runtime = make_runtime(script, max_errors=1)
    runtime.evaluator = BrokenEvaluator()
    checkpoint = await runtime.run(state)
    assert checkpoint.state.task.status == "failed"
    _, events = await runtime.repository.load(state.task.id)
    assert events[-2].kind == "evaluation_error"
