from datetime import timedelta
from uuid import uuid4

import pytest

from waypoint_agent.calendar import (
    MemoryCalendar,
    RevisionConflict,
    SlotConflict,
    UpdateQuery,
    slots,
)
from waypoint_agent.config import reschedule_context


@pytest.fixture
def context(data):
    return reschedule_context(data.clock, "America/Toronto", "alice@example.com")


async def test_calendar_atomic_update_deduplication_and_preservation(data, context):
    store, world, operation = MemoryCalendar(), uuid4(), uuid4()
    data.meetings[0].__pydantic_extra__["location"] = "Room 7"
    await store.seed(world, data.meetings)
    before = await store.events(world)
    query = UpdateQuery(
        event_id=before[0].id,
        expected_revision=1,
        start=context.destination_start,
        end=context.destination_start + timedelta(minutes=30),
    )
    first = await store.update(world, operation, query, context)
    assert await store.update(world, operation, query, context) == first
    after = await store.events(world)
    assert after[1:] == before[1:]
    assert after[0].model_dump(exclude={"start", "end", "revision"}) == before[0].model_dump(
        exclude={"start", "end", "revision"}
    )
    assert after[0].revision == 2
    with pytest.raises(ValueError, match="different arguments"):
        await store.update(
            world, operation, query.model_copy(update={"expected_revision": 2}), context
        )
    with pytest.raises(RevisionConflict):
        await store.update(world, uuid4(), query, context)
    with pytest.raises(ValueError, match="already exists"):
        await store.seed(world, [])


async def test_conflicts_include_all_events_and_half_open_boundaries(data, context):
    store, world = MemoryCalendar(), uuid4()
    start = context.destination_start
    data.meetings[1].start = start
    data.meetings[1].end = start + timedelta(minutes=30)
    await store.seed(world, data.meetings)
    available = slots(data.meetings, data.meetings[0], context)
    assert available[0][0] == data.meetings[1].end
    assert available[-1][1] == context.destination_end
    query = UpdateQuery(
        event_id=data.meetings[0].id,
        expected_revision=1,
        start=start,
        end=start + timedelta(minutes=30),
    )
    with pytest.raises(SlotConflict):
        await store.update(world, uuid4(), query, context)
    assert await store.events(world) == data.meetings


@pytest.mark.parametrize("change", ["duration", "bounds", "increment"])
async def test_invalid_updates_never_mutate(data, context, change):
    store, world = MemoryCalendar(), uuid4()
    await store.seed(world, data.meetings)
    start, end = context.destination_start, context.destination_start + timedelta(minutes=30)
    if change == "duration":
        end += timedelta(minutes=15)
    elif change == "bounds":
        start -= timedelta(hours=1)
        end -= timedelta(hours=1)
    else:
        start += timedelta(minutes=1)
        end += timedelta(minutes=1)
    with pytest.raises(ValueError):
        await store.update(
            world,
            uuid4(),
            UpdateQuery(event_id=data.meetings[0].id, expected_revision=1, start=start, end=end),
            context,
        )
    assert await store.events(world) == data.meetings
