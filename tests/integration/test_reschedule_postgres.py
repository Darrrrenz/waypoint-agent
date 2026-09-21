import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from waypoint_agent.approval import decide
from waypoint_agent.calendar import PostgresCalendar, SlotConflict, UpdateQuery
from waypoint_agent.cli import runtime_for
from waypoint_agent.config import Settings, reschedule_context
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.postgres import PostgresRepository

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


async def make_task(repository, data):
    store, world = PostgresCalendar(repository), uuid4()
    await store.seed(world, data.meetings)
    ctx = reschedule_context(data.clock, "America/Toronto", "alice@example.com")
    state = AgentState(
        task=Task(
            goal="Move Alice to Friday afternoon",
            created_at=data.clock,
            context=ctx,
            workflow="reschedule",
            calendar_world_id=world,
        ),
        dataset=data.model_dump(mode="json"),
        model_kind="reschedule_demo",
    )
    settings = Settings(_env_file=None)
    return state, store, runtime_for(state, repository, store, settings)


async def test_postgres_calendar_deduplicates_concurrent_operations(repository, data):
    state, store, _ = await make_task(repository, data)
    world, ctx = state.task.calendar_world_id, state.task.context
    operation = uuid4()
    query = UpdateQuery(
        event_id=data.meetings[0].id,
        expected_revision=1,
        start=ctx.destination_start,
        end=ctx.destination_start + timedelta(minutes=30),
    )
    results = await asyncio.gather(*(store.update(world, operation, query, ctx) for _ in range(5)))
    assert all(r == results[0] for r in results)
    after = await PostgresCalendar(PostgresRepository(repository.url)).events(world)
    assert after[0].revision == 2
    assert after[1:] == data.meetings[1:]
    with pytest.raises(ValueError, match="different arguments"):
        await store.update(world, operation, query.model_copy(update={"expected_revision": 2}), ctx)
    async with await repository.connect() as conn:
        assert (
            await (
                await conn.execute(
                    "SELECT count(*) FROM waypoint_calendar_operations WHERE world_id=%s", (world,)
                )
            ).fetchone()
        )[0] == 1


async def test_postgres_conflict_check_and_update_share_transaction(repository, data):
    state, store, _ = await make_task(repository, data)
    ctx, world = state.task.context, state.task.calendar_world_id
    queries = [
        UpdateQuery(
            event_id=m.id,
            expected_revision=1,
            start=ctx.destination_start,
            end=ctx.destination_start + (m.end - m.start),
        )
        for m in data.meetings[:2]
    ]
    contexts = [ctx, ctx.model_copy(update={"participant": data.meetings[1].participants[0]})]
    results = await asyncio.gather(
        *(store.update(world, uuid4(), q, c) for q, c in zip(queries, contexts, strict=True)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, SlotConflict) for r in results) == 1
    assert sum(not isinstance(r, Exception) for r in results) == 1
    after = await store.events(world)
    assert sum(m.revision == 2 for m in after) == 1


async def test_ledger_insert_failure_rolls_back_calendar_mutation(repository, data, monkeypatch):
    state, store, _ = await make_task(repository, data)
    real_execute = psycopg.AsyncConnection.execute

    async def fail_insert(connection, query, *args, **kwargs):
        if isinstance(query, str) and query.startswith("INSERT INTO waypoint_calendar_operations"):
            raise RuntimeError("Injected ledger insert failure after calendar UPDATE")
        return await real_execute(connection, query, *args, **kwargs)

    ctx, world = state.task.context, state.task.calendar_world_id
    query = UpdateQuery(
        event_id=data.meetings[0].id,
        expected_revision=1,
        start=ctx.destination_start,
        end=ctx.destination_start + timedelta(minutes=30),
    )
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncConnection, "execute", fail_insert)
        with pytest.raises(RuntimeError, match="ledger insert"):
            await store.update(world, uuid4(), query, ctx)
    assert await store.events(world) == data.meetings


async def test_database_lock_and_concurrent_decisions(repository, data):
    state, store, runtime = await make_task(repository, data)
    waiting = await runtime.run(state)
    async with repository.task_lock(state.task.id):
        fresh_repo = PostgresRepository(repository.url)
        with pytest.raises(ValueError, match="busy"):
            await decide(fresh_repo, state.task.id, waiting.state.pending_approval_id, "approved")
    decisions = await asyncio.gather(
        *(
            decide(
                PostgresRepository(repository.url),
                state.task.id,
                waiting.state.pending_approval_id,
                decision,
            )
            for decision in ("approved", "denied")
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(d, Exception) for d in decisions) == 1
    saved, _ = await repository.load(state.task.id)
    chosen = saved.state.approvals[0].decision
    result = await runtime.resume(state.task.id)
    assert result.state.task.status == ("completed" if chosen == "approved" else "denied")
    assert (await store.events(state.task.calendar_world_id))[0].revision == (
        2 if chosen == "approved" else 1
    )


def cli(repository, *arguments, expected=(0,)):
    env = {**os.environ, "WAYPOINT_DATABASE_URL": repository.url}
    result = subprocess.run(
        [sys.executable, "-c", "from waypoint_agent.cli import main; main()", *map(str, arguments)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode in expected, result.stdout + result.stderr
    return json.loads(result.stdout)


async def test_cli_subprocess_run_inspect_approve_resume_inspect(repository, data, tmp_path):
    state, store, _ = await make_task(repository, data)
    world = state.task.calendar_world_id
    waiting = await asyncio.to_thread(
        cli, repository, "run", "--workflow", "reschedule", "--world-id", world, "--json"
    )
    task = waiting["checkpoint"]["state"]["task"]["id"]
    assert waiting["checkpoint"]["state"]["task"]["status"] == "waiting_for_approval"
    assert await store.events(world) == data.meetings
    requests = await asyncio.to_thread(cli, repository, "approvals", task, "--json")
    assert len(requests) == 1
    await asyncio.to_thread(cli, repository, "inspect", task)
    await asyncio.to_thread(cli, repository, "approve", task, requests[0]["id"], "--json")
    assert await store.events(world) == data.meetings
    export = tmp_path / "trajectory.json"
    done = await asyncio.to_thread(cli, repository, "resume", task, "--json", "--export", export)
    inspected = await asyncio.to_thread(cli, repository, "inspect", task)
    assert done == inspected == json.loads(export.read_text(encoding="utf-8"))
    assert done["checkpoint"]["state"]["task"]["status"] == "completed"
    after = await store.events(world)
    assert after[0].revision == 2
    assert after[1:] == data.meetings[1:]
    assert after[0].model_dump(exclude={"start", "end", "revision"}) == data.meetings[0].model_dump(
        exclude={"start", "end", "revision"}
    )
    assert await asyncio.to_thread(cli, repository, "resume", task, "--json") == done


async def test_actual_process_crash_after_write_then_concurrent_resume(repository, data):
    state, store, runtime = await make_task(repository, data)
    waiting = await runtime.run(state)
    await decide(repository, state.task.id, waiting.state.pending_approval_id, "approved")
    env = {**os.environ, "WAYPOINT_DATABASE_URL": repository.url}
    crashed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(ROOT / "tests/integration/crash_worker.py"), str(state.task.id)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=45,
    )
    assert crashed.returncode == 73, crashed.stderr.decode(errors="replace")
    assert (await store.events(state.task.calendar_world_id))[0].revision == 2
    saved, _ = await repository.load(state.task.id)
    assert not saved.state.executions

    # Each CLI is a separate OS process, connection, model, and runtime.
    def resume_process():
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from waypoint_agent.cli import main; main()",
                "resume",
                str(state.task.id),
                "--json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=45,
        )

    results = await asyncio.gather(*(asyncio.to_thread(resume_process) for _ in range(3)))
    assert any(r.returncode == 0 for r in results)
    assert all(r.returncode in (0, 1) for r in results)
    final, events = await repository.load(state.task.id)
    assert final.state.task.status == "completed"
    assert len(final.state.executions) == 1
    assert final.state.model_calls == 5
    assert [e.sequence for e in events] == list(range(1, final.last_sequence + 1))
    assert (await store.events(state.task.calendar_world_id))[0].revision == 2
    async with await repository.connect() as conn:
        assert (
            await (
                await conn.execute(
                    "SELECT count(*) FROM waypoint_calendar_operations WHERE world_id=%s",
                    (state.task.calendar_world_id,),
                )
            ).fetchone()
        )[0] == 1


async def test_initialization_preserves_existing_checkpoint_and_calendar(repository, data):
    state, store, runtime = await make_task(repository, data)
    checkpoint = await runtime.run(state)
    await repository.initialize()
    await repository.initialize()
    saved, _ = await repository.load(state.task.id)
    assert saved == checkpoint
    assert await store.events(state.task.calendar_world_id) == data.meetings
    with pytest.raises(psycopg.errors.UniqueViolation):
        await store.seed(state.task.calendar_world_id, [])
