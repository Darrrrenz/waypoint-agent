"""Explicit preferences and compact verified episodes; no inferred user facts."""

import asyncio
from typing import Literal, Protocol
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, Field

from waypoint_agent.schemas import (
    EarliestMeetingStart,
    MemorySelection,
    RescheduleContext,
    Schema,
    TrajectoryEvent,
    utc_now,
)


class Episode(Schema):
    task_id: UUID
    workflow: str
    outcome: str
    source_ids: list[str]
    decisions: list[dict[str, str]]
    operation_ids: list[UUID]
    selected_memory: list[MemorySelection]


class MemoryRecord(Schema):
    id: UUID = Field(default_factory=uuid4)
    namespace: str
    key: str
    kind: Literal["semantic", "episodic"]
    content: EarliestMeetingStart | Episode
    source: Literal["explicit_cli", "verified_runtime"]
    created_at: AwareDatetime = Field(default_factory=utc_now)
    revision: int
    supersedes: UUID | None = None


def replacement(records, namespace, key, content, source):
    previous = next((r for r in reversed(records) if r.key == key), None)
    if previous and (previous.content == content or previous.kind == "episodic"):
        return previous, False
    return MemoryRecord(
        namespace=namespace,
        key=key,
        content=content,
        source=source,
        kind="semantic" if isinstance(content, EarliestMeetingStart) else "episodic",
        revision=previous.revision + 1 if previous else 1,
        supersedes=previous.id if previous else None,
    ), True


class MemoryStore(Protocol):
    async def records(self, namespace: str) -> list[MemoryRecord]: ...
    async def put(self, namespace: str, key: str, content, source: str) -> MemoryRecord: ...


class InMemoryStore:
    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()

    async def records(self, namespace):
        return [r.model_copy(deep=True) for r in self.data.get(namespace, [])]

    async def put(self, namespace, key, content, source):
        async with self.lock:
            records = self.data.setdefault(namespace, [])
            record, new = replacement(records, namespace, key, content, source)
            if new:
                records.append(record.model_copy(deep=True))
            return record.model_copy(deep=True)


MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS waypoint_memory_namespaces (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS waypoint_agent_memory (
    namespace TEXT NOT NULL REFERENCES waypoint_memory_namespaces(name),
    key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    record JSONB NOT NULL,
    PRIMARY KEY (namespace, key, revision)
);
"""


class PostgresMemoryStore:
    def __init__(self, repository):
        self.repository = repository

    async def records(self, namespace):
        async with await self.repository.connect() as conn:
            rows = await (
                await conn.execute(
                    "SELECT record FROM waypoint_agent_memory WHERE namespace=%s ORDER BY key, revision",
                    (namespace,),
                )
            ).fetchall()
            return [MemoryRecord.model_validate(row[0]) for row in rows]

    async def put(self, namespace, key, content, source):
        async with await self.repository.connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO waypoint_memory_namespaces VALUES (%s) ON CONFLICT DO NOTHING",
                    (namespace,),
                )
                await conn.execute(
                    "SELECT name FROM waypoint_memory_namespaces WHERE name=%s FOR UPDATE",
                    (namespace,),
                )
                rows = await (
                    await conn.execute(
                        "SELECT record FROM waypoint_agent_memory WHERE namespace=%s AND key=%s "
                        "ORDER BY revision",
                        (namespace, key),
                    )
                ).fetchall()
                record, new = replacement(
                    [MemoryRecord.model_validate(r[0]) for r in rows],
                    namespace,
                    key,
                    content,
                    source,
                )
                if new:
                    await conn.execute(
                        "INSERT INTO waypoint_agent_memory VALUES (%s,%s,%s,%s)",
                        (namespace, key, record.revision, Jsonb(record.model_dump(mode="json"))),
                    )
                return record


class MemoryController:
    def __init__(self, store: MemoryStore):
        self.store = store

    async def set_preference(self, namespace, preference: EarliestMeetingStart):
        if not namespace.strip():
            raise ValueError("Memory namespace cannot be empty")
        return await self.store.put(namespace, "earliest_meeting_start", preference, "explicit_cli")

    async def retrieve(self, namespace, workflow):
        if workflow != "reschedule":
            return []
        preferences = [
            r for r in await self.store.records(namespace) if r.key == "earliest_meeting_start"
        ]
        if not preferences:
            return []
        record = max(preferences, key=lambda r: r.revision)
        return [
            MemorySelection(
                id=record.id,
                namespace=namespace,
                revision=record.revision,
                preference=record.content,
            )
        ]

    async def prepare(self, state, namespace, strategy="structured"):
        if state.task.status != "pending":
            raise ValueError("Only new tasks may retrieve memory")
        if strategy not in ("disabled", "structured"):
            raise ValueError("Unsupported memory strategy")
        state.memory_strategy, state.memory_namespace = strategy, namespace
        state.selected_memory = (
            await self.retrieve(namespace, state.task.workflow) if strategy == "structured" else []
        )
        if isinstance(state.task.context, RescheduleContext):
            state.task.context.earliest_meeting_start = (
                state.selected_memory[0].preference.model_copy(deep=True)
                if state.selected_memory
                else None
            )

    async def consider(self, state):
        if (
            state.task.status != "completed"
            or state.task.termination_reason != "verified"
            or state.findings is None
            or state.memory_strategy != "structured"
        ):
            return None
        # Recheck durable evidence; a completion flag or proposed update alone is insufficient.
        from waypoint_agent.evaluation.alice import MeetingEmailEvaluator
        from waypoint_agent.evaluation.reschedule import RescheduleEvaluator

        evaluator = (
            RescheduleEvaluator()
            if state.task.workflow == "reschedule"
            else MeetingEmailEvaluator()
        )
        if not evaluator.verify(state, state.findings).accepted:
            raise ValueError("Cannot project an unverified episode")
        episode = Episode(
            task_id=state.task.id,
            workflow=state.task.workflow,
            outcome=state.findings.outcome,
            source_ids=state.findings.meeting_ids + getattr(state.findings, "unread_email_ids", []),
            decisions=[
                {
                    "approval_id": str(a.id),
                    "decision": a.decision,
                    "outcome": a.outcome or "unresolved",
                }
                for a in state.approvals
            ],
            operation_ids=[e.operation_id for e in state.executions],
            selected_memory=state.selected_memory,
        )
        return await self.store.put(
            state.memory_namespace, f"task:{state.task.id}", episode, "verified_runtime"
        )

    async def project(self, repository, checkpoint):
        state = checkpoint.state
        if (
            state.memory_strategy != "structured"
            or state.task.status != "completed"
            or state.memory_projection == "stored"
        ):
            return checkpoint
        async with repository.task_lock(state.task.id):
            checkpoint, _ = await repository.load(state.task.id)
            state = checkpoint.state
            if state.memory_projection == "stored":
                return checkpoint
            try:
                async with asyncio.timeout(state.limits.get("storage_timeout", 5)):
                    record = await self.consider(state)
                if record is None:
                    return checkpoint
                state.memory_projection, state.memory_projection_error = "stored", None
                data = {"record_id": str(record.id), "revision": record.revision}
            except Exception as exc:
                state.memory_projection = "failed"
                state.memory_projection_error = type(exc).__name__
                data = {"error": type(exc).__name__, "retry": "resume the completed task"}
            checkpoint.last_sequence += 1
            async with asyncio.timeout(state.limits.get("storage_timeout", 5)):
                await repository.save(
                    checkpoint,
                    TrajectoryEvent(
                        task_id=state.task.id,
                        sequence=checkpoint.last_sequence,
                        timestamp=utc_now(),
                        kind=f"memory_projection_{state.memory_projection}",
                        data=data,
                    ),
                )
            return checkpoint
