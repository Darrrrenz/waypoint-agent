from datetime import timedelta
from uuid import uuid4

import pytest

from waypoint_agent.approval import decide
from waypoint_agent.calendar import MemoryCalendar, UpdateQuery, slots
from waypoint_agent.cli import runtime_for
from waypoint_agent.config import Settings, reschedule_context
from waypoint_agent.memory import InMemoryStore, MemoryController
from waypoint_agent.schemas import AgentState, EarliestMeetingStart, Task
from waypoint_agent.storage.memory import MemoryRepository


async def setup(data, earliest="15:00", strategy="structured", memory_store=None):
    memory = MemoryController(memory_store or InMemoryStore())
    preference = EarliestMeetingStart(earliest=earliest, timezone="America/Toronto")
    record = await memory.set_preference("user", preference)
    world = uuid4()
    store, repo = MemoryCalendar(), MemoryRepository()
    await store.seed(world, data.meetings)
    state = AgentState(
        task=Task(
            goal="Move Alice to Friday afternoon",
            created_at=data.clock,
            context=reschedule_context(data.clock, "America/Toronto", "alice@example.com"),
            workflow="reschedule",
            calendar_world_id=world,
        ),
        model_kind="reschedule_demo",
        dataset=data.model_dump(mode="json"),
    )
    await memory.prepare(state, "user", strategy)

    def runtime():
        return runtime_for(state, repo, store, Settings(_env_file=None), memory)

    return state, repo, store, memory, record, runtime


async def test_dedup_supersession_namespaces_and_relevant_retrieval():
    controller = MemoryController(InMemoryStore())
    first = await controller.set_preference(
        "a", EarliestMeetingStart(earliest="15:00", timezone="America/Toronto")
    )
    assert await controller.set_preference("a", first.content) == first
    changed = await controller.set_preference(
        "a", first.content.model_copy(update={"earliest": "16:00"})
    )
    assert changed.revision == 2 and changed.supersedes == first.id
    selected = await controller.retrieve("a", "reschedule")
    assert selected[0].id == changed.id and selected[0].source == "explicit_cli"
    assert await controller.retrieve("b", "reschedule") == []
    assert await controller.retrieve("a", "meeting_email") == []


async def test_snapshot_approval_atomic_validation_and_episode(data):
    state, repo, store, memory, record, fresh = await setup(data)
    waiting = await fresh().run(state)
    approval = waiting.state.approvals[0]
    assert approval.call.arguments["start"] == "2026-09-25T19:00:00Z"
    assert waiting.state.selected_memory[0].id == record.id
    await memory.set_preference("user", record.content.model_copy(update={"earliest": "16:00"}))
    query = UpdateQuery(
        event_id=data.meetings[0].id,
        expected_revision=1,
        start=state.task.context.destination_start,
        end=state.task.context.destination_start + timedelta(minutes=30),
    )
    with pytest.raises(ValueError, match="preference"):
        await store.update(state.task.calendar_world_id, uuid4(), query, state.task.context)
    with pytest.raises(ValueError, match="preference"):
        await fresh().tools.get("update_calendar_event").validate_proposal(query)
    await decide(repo, state.task.id, approval.id, "approved")
    completed = await fresh().resume(state.task.id)
    assert completed.state.task.status == "completed"
    assert completed.state.memory_projection == "stored"
    assert completed.state.task.context.earliest_meeting_start.earliest == "15:00"
    assert (await fresh().resume(state.task.id)) == completed
    episodes = [r for r in await memory.store.records("user") if r.kind == "episodic"]
    assert len(episodes) == 1
    assert episodes[0].content.source_ids == ["cal-alice-001"]
    assert episodes[0].content.selected_memory[0].revision == 1
    assert len(store.operations) == 1


@pytest.mark.parametrize(
    "earliest,strategy,hour",
    [("15:00", "disabled", 13), ("15:00", "structured", 15), ("17:00", "structured", None)],
)
async def test_preference_intersects_window(data, earliest, strategy, hour):
    state, _, _, _, _, fresh = await setup(data, earliest, strategy)
    available = slots(data.meetings, data.meetings[0], state.task.context)
    assert (available[0][0].hour if available else None) == hour
    result = await fresh().run(state)
    if hour is None:
        assert result.state.findings.outcome == "no_availability"
        assert result.state.memory_projection == "stored"


async def test_projection_failure_retry_without_reexecution(data):
    class FailOnce(InMemoryStore):
        failed = False

        async def put(self, namespace, key, content, source):
            record = await super().put(namespace, key, content, source)
            if source == "verified_runtime" and not self.failed:
                self.failed = True
                raise OSError("Lost reply after inserting episode")
            return record

    state, repo, store, memory, _, fresh = await setup(data, memory_store=FailOnce())
    waiting = await fresh().run(state)
    assert await memory.consider(waiting.state) is None
    await decide(repo, state.task.id, waiting.state.pending_approval_id, "approved")
    result = await fresh().resume(state.task.id)
    assert result.state.task.status == "completed"
    assert result.state.memory_projection == "failed"
    assert len(store.operations) == 1
    result = await fresh().resume(state.task.id)
    assert result.state.memory_projection == "stored"
    assert len(store.operations) == 1
    assert len([r for r in await memory.store.records("user") if r.kind == "episodic"]) == 1
    tampered = result.state.model_copy(deep=True)
    tampered.executions.clear()
    with pytest.raises(ValueError, match="unverified"):
        await memory.consider(tampered)


def test_historical_context_default(data):
    context = reschedule_context(data.clock, "America/Toronto", "alice@example.com")
    raw = context.model_dump()
    raw.pop("earliest_meeting_start")
    assert type(context).model_validate(raw).earliest_meeting_start is None
