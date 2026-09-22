"""Actual fresh CLI processes; in-memory reconstruction is not a substitute."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from waypoint_agent.calendar import PostgresCalendar
from waypoint_agent.memory import MemoryController, PostgresMemoryStore
from waypoint_agent.schemas import EarliestMeetingStart

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


async def cli(repository, *arguments):
    environment = {**os.environ, "WAYPOINT_DATABASE_URL": repository.url}
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", "from waypoint_agent.cli import main; main()", *map(str, arguments)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


async def test_cross_process_preference_snapshot_and_episode(repository, data):
    namespace, world = f"integration:{uuid4()}", uuid4()
    await PostgresCalendar(repository).seed(world, data.meetings)
    first = json.loads(
        await cli(
            repository,
            "memory",
            "set",
            "--namespace",
            namespace,
            "--earliest",
            "15:00",
            "--timezone",
            "America/Toronto",
        )
    )
    repeated = json.loads(
        await cli(repository, "memory", "set", "--namespace", namespace, "--earliest", "15:00")
    )
    assert first["id"] == repeated["id"]
    waiting = json.loads(
        await cli(
            repository,
            "run",
            "--workflow",
            "reschedule",
            "--world-id",
            world,
            "--memory-namespace",
            namespace,
            "--json",
        )
    )
    state = waiting["checkpoint"]["state"]
    assert state["selected_memory"][0]["id"] == first["id"]
    assert state["approvals"][0]["call"]["arguments"]["start"] == "2026-09-25T19:00:00Z"
    task, approval = state["task"]["id"], state["pending_approval_id"]
    await cli(repository, "approve", task, approval)
    await cli(repository, "memory", "set", "--namespace", namespace, "--earliest", "16:00")
    completed = json.loads(await cli(repository, "resume", task, "--json"))
    assert completed["checkpoint"]["state"]["memory_projection"] == "stored"
    assert completed["checkpoint"]["state"]["task"]["status"] == "completed"
    await cli(repository, "resume", task)
    episodes = json.loads(await cli(repository, "memory", "episodes", "--namespace", namespace))
    assert len(episodes) == 1 and episodes[0]["content"]["task_id"] == task
    after = await PostgresCalendar(repository).events(world)
    assert after[0].start.isoformat() == "2026-09-25T19:00:00+00:00"
    assert after[0].revision == 2
    assert after[1:] == data.meetings[1:]


async def test_concurrent_preference_dedup_and_additive_initialization(repository):
    namespace = f"integration:{uuid4()}"
    controller = MemoryController(PostgresMemoryStore(repository))
    preference = EarliestMeetingStart(earliest="15:00", timezone="America/Toronto")
    records = await asyncio.gather(
        *(controller.set_preference(namespace, preference) for _ in range(4))
    )
    assert len({r.id for r in records}) == 1
    await repository.initialize()
    assert len(await controller.store.records(namespace)) == 1
    changed = await controller.set_preference(
        namespace, preference.model_copy(update={"earliest": "16:00"})
    )
    assert changed.revision == 2 and changed.supersedes == records[0].id
    assert await controller.retrieve(f"other:{uuid4()}", "reschedule") == []
