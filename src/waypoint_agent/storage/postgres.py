from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from waypoint_agent.schemas import Checkpoint, TrajectoryEvent

DDL = """
CREATE TABLE IF NOT EXISTS waypoint_tasks (
    id UUID PRIMARY KEY,
    last_sequence INTEGER NOT NULL,
    checkpoint JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS waypoint_events (
    task_id UUID NOT NULL REFERENCES waypoint_tasks(id),
    sequence INTEGER NOT NULL,
    event JSONB NOT NULL,
    PRIMARY KEY (task_id, sequence)
);
"""


class PostgresRepository:
    def __init__(self, url: str):
        self.url = url

    async def connect(self):
        return await psycopg.AsyncConnection.connect(
            self.url, connect_timeout=5, options="-c statement_timeout=5000 -c lock_timeout=5000"
        )

    async def initialize(self):
        async with await self.connect() as conn:
            await conn.execute(DDL)

    async def save(self, checkpoint: Checkpoint, event: TrajectoryEvent) -> None:
        task_id = checkpoint.state.task.id
        seq = checkpoint.last_sequence
        if event.task_id != task_id or event.sequence != seq:
            raise ValueError("Checkpoint/event mismatch")
        async with await self.connect() as conn:
            async with conn.transaction():
                if seq == 1:
                    await conn.execute(
                        "INSERT INTO waypoint_tasks VALUES (%s, %s, %s)",
                        (task_id, seq, Jsonb(checkpoint.model_dump(mode="json"))),
                    )
                else:
                    cursor = await conn.execute(
                        "UPDATE waypoint_tasks SET last_sequence=%s, checkpoint=%s "
                        "WHERE id=%s AND last_sequence=%s RETURNING id",
                        (seq, Jsonb(checkpoint.model_dump(mode="json")), task_id, seq - 1),
                    )
                    if await cursor.fetchone() is None:
                        raise ValueError("Conflicting checkpoint sequence")
                await conn.execute(
                    "INSERT INTO waypoint_events VALUES (%s, %s, %s)",
                    (task_id, seq, Jsonb(event.model_dump(mode="json"))),
                )

    async def load(self, task_id: UUID):
        async with await self.connect() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor = await conn.execute(
                    "SELECT checkpoint FROM waypoint_tasks WHERE id=%s", (task_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError(str(task_id))
                checkpoint = Checkpoint.model_validate(row[0])
                cursor = await conn.execute(
                    "SELECT event FROM waypoint_events WHERE task_id=%s ORDER BY sequence",
                    (task_id,),
                )
                events = [TrajectoryEvent.model_validate(r[0]) for r in await cursor.fetchall()]
                return checkpoint, events
