"""Isolated, bounded runtime runs with explicitly synthetic harness decisions."""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from waypoint_agent.approval import decide
from waypoint_agent.benchmark.assertions import score
from waypoint_agent.benchmark.scenarios import Suite
from waypoint_agent.calendar import (
    MemoryCalendar,
    PostgresCalendar,
    TransientReadError,
    UpdateQuery,
)
from waypoint_agent.cli import runtime_for
from waypoint_agent.config import Settings
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.memory import MemoryRepository
from waypoint_agent.storage.postgres import PostgresRepository


class InjectedCrash(Exception):
    """An in-process interruption; not evidence of process/database durability."""


class FaultRepository:
    def __init__(self, repository, enabled):
        self.repository, self.enabled = repository, enabled
        self.triggered = False

    def __getattr__(self, name):
        return getattr(self.repository, name)

    async def save(self, checkpoint, event):
        if (
            self.enabled
            and not self.triggered
            and event.kind == "tool_result"
            and event.data["call"]["name"] == "update_calendar_event"
        ):
            self.triggered = True
            raise InjectedCrash("After commit, before observation checkpoint")
        await self.repository.save(checkpoint, event)


async def ledger(store, world):
    from waypoint_agent.calendar import OperationResult

    if isinstance(store, MemoryCalendar):
        return [
            OperationResult.model_validate(v)
            for (w, _), v in store.operations.items()
            if w == world
        ]
    async with await store.repository.connect() as conn:
        rows = await (
            await conn.execute(
                "SELECT result FROM waypoint_calendar_operations WHERE world_id=%s", (world,)
            )
        ).fetchall()
        return [OperationResult.model_validate(row[0]) for row in rows]


async def run_scenario(scenario, backend="memory", settings=None, max_resumes=6):
    settings = settings or Settings(_env_file=None)
    repo = (
        MemoryRepository()
        if backend == "memory"
        else PostgresRepository(settings.database_url.get_secret_value())
    )
    if backend == "postgres":
        await repo.initialize()
    store = MemoryCalendar() if backend == "memory" else PostgresCalendar(repo)
    world = uuid4()
    inputs = scenario.inputs.model_copy(deep=True)
    await store.seed(world, inputs.dataset.meetings)
    before = await store.events(world)
    state = AgentState(
        task=Task(
            goal=inputs.goal,
            created_at=inputs.dataset.clock,
            context=inputs.context,
            workflow=inputs.workflow,
            calendar_world_id=world if inputs.workflow == "reschedule" else None,
        ),
        dataset=inputs.dataset.model_dump(mode="json"),
        model_kind=inputs.model,
        script=inputs.responses,
    )
    wrapped = FaultRepository(repo, scenario.fault == "commit_before_checkpoint")
    fault_triggered = False
    harness_actions = []

    def fresh(saved):
        runtime = runtime_for(saved, wrapped, store, settings)
        if scenario.fault == "transient_read" and not fault_triggered:
            tool = runtime.tools.get("search_calendar")
            original = tool.execute

            async def read(arguments):
                nonlocal fault_triggered
                if not fault_triggered:
                    fault_triggered = True
                    raise TransientReadError("Synthetic transient read")
                return await original(arguments)

            tool.execute = read
        return runtime

    checkpoint = await fresh(state).run(state)
    for _ in range(max_resumes):
        state = checkpoint.state
        if state.task.status not in ("waiting_for_approval", "unresolved", "running"):
            break
        if state.task.status == "waiting_for_approval":
            if scenario.approval == "none":
                break
            if scenario.fault == "occupied_slot" and not fault_triggered:
                # A separate synthetic actor occupies the proposed slot through the real store.
                bob = next(m for m in before if m.id == "cal-bob-001")
                ctx = inputs.context.model_copy(update={"participant": "bob@example.com"})
                from datetime import timedelta

                query = UpdateQuery(
                    event_id=bob.id,
                    expected_revision=bob.revision,
                    start=ctx.destination_start,
                    end=ctx.destination_start + timedelta(minutes=30),
                )
                await store.update(world, uuid4(), query, ctx)
                fault_triggered = True
                harness_actions.append({"actor": "test-harness", "action": "occupy_slot"})
            decision = "approved" if scenario.approval == "approve" else "denied"
            await decide(repo, state.task.id, state.pending_approval_id, decision)
            harness_actions.append(
                {
                    "actor": "test-harness",
                    "decision": decision,
                    "approval_id": str(state.pending_approval_id),
                }
            )
        try:
            checkpoint = await fresh(state).resume(state.task.id)
        except InjectedCrash:
            checkpoint, _ = await repo.load(state.task.id)
    checkpoint, events = await repo.load(state.task.id)
    after = await store.events(world)
    operations = await ledger(store, world)
    assertions = score(scenario, checkpoint.state, before, after, operations)
    triggered = fault_triggered or wrapped.triggered
    if scenario.fault:
        assertions.append(
            {
                "name": "fault_exercised",
                "passed": triggered,
                "detail": "" if triggered else "Fault was not reached",
            }
        )
    return {
        "checkpoint": checkpoint,
        "events": events,
        "before": before,
        "after": after,
        "operations": operations,
        "assertions": assertions,
        "harness_actions": harness_actions,
        "fault_triggered": triggered,
    }


async def run_suite(suite_path, output, backend="memory", repeats=1, settings=None):
    from waypoint_agent.benchmark.reporting import metadata, summarize, write_reports

    raw = await asyncio.to_thread(Path(suite_path).read_text, encoding="utf-8")
    suite = Suite.model_validate_json(raw)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for repeat in range(repeats):
        for scenario in suite.scenarios:
            started = perf_counter()
            row = {
                "id": scenario.id,
                "repeat": repeat,
                "workflow": scenario.inputs.workflow,
                "model": scenario.inputs.model,
                "backend": backend,
                "metadata": metadata(scenario),
            }
            try:
                async with asyncio.timeout(180):
                    run = await run_scenario(scenario, backend, settings)
                row.update(summarize(scenario, run))
                artifact = f"{scenario.id}-{repeat}.json"
                evidence = {
                    k: (
                        [v.model_dump(mode="json") for v in value]
                        if k in ("events", "before", "after", "operations")
                        else value.model_dump(mode="json")
                        if k == "checkpoint"
                        else value
                    )
                    for k, value in run.items()
                }
                (output / artifact).write_text(
                    json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
                )
                row["trajectory"] = artifact
            except Exception as exc:
                row.update(
                    status="infrastructure_error",
                    assertions=[],
                    metrics=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            row["harness_wall_seconds"] = perf_counter() - started
            results.append(row)
    report = write_reports(output, suite, results, backend, repeats)
    return report
