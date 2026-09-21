"""Generate a synthetic approved demo trajectory; no real integrations or accounts.

This explicit test/demo harness supplies the human decision for its own mock task.
The ordinary run command always exits before approval and never approves itself.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from waypoint_agent.approval import decide
from waypoint_agent.calendar import MemoryCalendar, PostgresCalendar
from waypoint_agent.cli import runtime_for
from waypoint_agent.config import Settings, reschedule_context
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.memory import MemoryRepository
from waypoint_agent.storage.postgres import PostgresRepository
from waypoint_agent.tools.mock import Dataset


async def export(args):
    settings = Settings()
    data = Dataset.load(Path("fixtures/environment.json"))
    repository = (
        MemoryRepository()
        if args.memory
        else PostgresRepository(settings.database_url.get_secret_value())
    )
    store = MemoryCalendar() if args.memory else PostgresCalendar(repository)
    world = uuid4()
    await store.seed(world, data.meetings)
    state = AgentState(
        task=Task(
            goal="Move my meeting with Alice to Friday afternoon.",
            created_at=data.clock,
            context=reschedule_context(data.clock, "America/Toronto", "alice@example.com"),
            workflow="reschedule",
            calendar_world_id=world,
        ),
        dataset=data.model_dump(mode="json"),
        model_kind="reschedule_demo",
    )
    before = await store.events(world)
    waiting = await runtime_for(state, repository, store, settings).run(state)
    assert waiting.state.task.status == "waiting_for_approval"
    assert await store.events(world) == before
    await decide(repository, state.task.id, waiting.state.pending_approval_id, "approved")
    done = await runtime_for(waiting.state, repository, store, settings).resume(state.task.id)
    assert done.state.task.status == "completed"
    after = await store.events(world)
    assert after[0].revision == 2 and after[1:] == before[1:]
    checkpoint, events = await repository.load(state.task.id)
    payload = {
        "environment": "in_memory" if args.memory else "postgresql",
        "decision_source": "explicit synthetic demo harness",
        "checkpoint": checkpoint.model_dump(mode="json"),
        "events": [e.model_dump(mode="json") for e in events],
        "calendar_before": [m.model_dump(mode="json") for m in before],
        "calendar_after": [m.model_dump(mode="json") for m in after],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Exported verified {payload['environment']} trajectory: {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("artifacts/reschedule-trajectory.json"))
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(export(parser.parse_args()))
