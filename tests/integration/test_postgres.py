import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from waypoint_agent.schemas import Checkpoint, TrajectoryEvent
from waypoint_agent.storage.postgres import PostgresRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repository():
    url = os.environ.get("WAYPOINT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WAYPOINT_TEST_DATABASE_URL to run real PostgreSQL integration tests")
    repo = PostgresRepository(url)
    await repo.initialize()
    return repo


async def test_saved_workflow_loads_in_new_repository(repository, state, script, make_runtime):
    checkpoint = await make_runtime(script, repository=repository).run(state)
    saved, events = await PostgresRepository(repository.url).load(state.task.id)
    assert saved == checkpoint
    assert saved.state.task.status == "completed"
    assert [e.sequence for e in events] == list(range(1, checkpoint.last_sequence + 1))
    assert events[-1].kind == "completion_verified"
    assert any(e.kind == "tool_result" and e.data["output"] for e in events)


async def test_stale_checkpoint_is_rejected(repository, state):
    checkpoint = Checkpoint(state=state, last_sequence=1)
    event = TrajectoryEvent(
        task_id=state.task.id, sequence=1, timestamp=state.task.created_at, kind="test", data={}
    )
    await repository.save(checkpoint, event)
    stale = checkpoint.model_copy(update={"last_sequence": 3})
    with pytest.raises(ValueError, match="Conflicting"):
        await repository.save(stale, event.model_copy(update={"sequence": 3}))
    saved, events = await repository.load(state.task.id)
    assert saved == checkpoint
    assert len(events) == 1


async def test_event_failure_rolls_back_checkpoint_update(repository, state):
    checkpoint = Checkpoint(state=state, last_sequence=1)
    event = TrajectoryEvent(
        task_id=state.task.id, sequence=1, timestamp=state.task.created_at, kind="test", data={}
    )
    await repository.save(checkpoint, event)
    # Deliberately corrupt the test task to force an INSERT failure after its UPDATE.
    async with await repository.connect() as conn:
        await conn.execute(
            "INSERT INTO waypoint_events VALUES (%s, 2, %s)",
            (
                state.task.id,
                Jsonb(event.model_copy(update={"sequence": 2}).model_dump(mode="json")),
            ),
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        await repository.save(
            checkpoint.model_copy(update={"last_sequence": 2}),
            event.model_copy(update={"sequence": 2}),
        )
    saved, _ = await repository.load(state.task.id)
    assert saved.last_sequence == 1
    async with await repository.connect() as conn:
        await conn.execute(
            "DELETE FROM waypoint_events WHERE task_id=%s AND sequence=2", (state.task.id,)
        )
