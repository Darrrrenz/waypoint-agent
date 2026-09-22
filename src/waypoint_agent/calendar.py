"""Transactional mock calendar; every mutation serializes on its calendar world."""

import asyncio
import hashlib
import json
from datetime import UTC, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, Field, model_validator

from waypoint_agent.schemas import RescheduleContext, Schema
from waypoint_agent.tools.mock import Meeting


def arguments_hash(arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RevisionConflict(ValueError):
    pass


class SlotConflict(ValueError):
    pass


class TransientReadError(OSError):
    """Only explicitly transient reads are retried automatically."""


class UpdateQuery(Schema):
    event_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def normalize(self):
        self.start = self.start.astimezone(UTC)
        self.end = self.end.astimezone(UTC)
        if self.end <= self.start:
            raise ValueError("Invalid update interval")
        return self


class OperationResult(Schema):
    operation_id: UUID
    arguments_hash: str
    event: Meeting


def preference_allows(start, context: RescheduleContext):
    preference = context.earliest_meeting_start
    return preference is None or (
        start.astimezone(ZoneInfo(preference.timezone)).strftime("%H:%M") >= preference.earliest
    )


def slots(events: list[Meeting], event: Meeting, context: RescheduleContext):
    duration = event.end - event.start
    start = context.destination_start
    available = []
    while start + duration <= context.destination_end:
        end = start + duration
        if preference_allows(start, context) and not any(
            m.id != event.id and m.start < end and start < m.end for m in events
        ):
            available.append((start, end))
        start += timedelta(minutes=context.slot_minutes)
    return available


def validate_update(events, query: UpdateQuery, context: RescheduleContext):
    event = next((m for m in events if m.id == query.event_id), None)
    if event is None:
        raise RevisionConflict("Event no longer exists")
    if event.revision != query.expected_revision:
        raise RevisionConflict("Event revision changed; fresh approval required")
    if context.participant not in event.participants:
        raise ValueError("Event does not match participant")
    if query.end - query.start != event.end - event.start:
        raise ValueError("Duration must be preserved")
    if not context.destination_start <= query.start < query.end <= context.destination_end:
        raise ValueError("Update outside destination bounds")
    if not preference_allows(query.start, context):
        raise ValueError("Update violates the saved earliest meeting preference")
    if (query.start - context.destination_start).total_seconds() % (context.slot_minutes * 60):
        raise ValueError("Update does not use the slot increment")
    if any(m.id != event.id and m.start < query.end and query.start < m.end for m in events):
        raise SlotConflict("Destination now conflicts; fresh approval required")
    return event


class CalendarStore(Protocol):
    async def seed(self, world: UUID, meetings: list[Meeting]) -> None: ...
    async def events(self, world: UUID) -> list[Meeting]: ...
    async def lookup(
        self, world: UUID, operation: UUID, arguments: dict
    ) -> OperationResult | None: ...
    async def update(
        self, world: UUID, operation: UUID, query: UpdateQuery, context: RescheduleContext
    ) -> OperationResult: ...


def existing_result(row, arguments):
    if row is None:
        return None
    result = OperationResult.model_validate(row)
    if result.arguments_hash != arguments_hash(arguments):
        raise ValueError("Operation ID reused with different arguments")
    return result


class MemoryCalendar:
    def __init__(self):
        self.worlds = {}
        self.operations = {}
        self.lock = asyncio.Lock()

    async def seed(self, world, meetings):
        async with self.lock:
            if world in self.worlds:
                raise ValueError("Calendar world already exists; seeding never overwrites")
            self.worlds[world] = [m.model_copy(deep=True) for m in meetings]

    async def events(self, world):
        return [m.model_copy(deep=True) for m in self.worlds[world]]

    async def lookup(self, world, operation, arguments):
        return existing_result(self.operations.get((world, operation)), arguments)

    async def update(self, world, operation, query, context):
        async with self.lock:
            existing = await self.lookup(world, operation, query.model_dump(mode="json"))
            if existing:
                return existing
            events = self.worlds[world]
            before = validate_update(events, query, context)
            after = before.model_copy(
                update={"start": query.start, "end": query.end, "revision": before.revision + 1},
                deep=True,
            )
            result = OperationResult(
                operation_id=operation,
                arguments_hash=arguments_hash(query.model_dump(mode="json")),
                event=after,
            )
            self.worlds[world] = [after if m.id == after.id else m for m in events]
            self.operations[world, operation] = result.model_dump(mode="json")
            return result.model_copy(deep=True)


CALENDAR_DDL = """
CREATE TABLE IF NOT EXISTS waypoint_calendar_worlds (
    id UUID PRIMARY KEY,
    events JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS waypoint_calendar_operations (
    world_id UUID NOT NULL REFERENCES waypoint_calendar_worlds(id),
    operation_id UUID NOT NULL,
    result JSONB NOT NULL,
    PRIMARY KEY (world_id, operation_id)
);
"""


class PostgresCalendar:
    def __init__(self, repository):
        self.repository = repository

    async def seed(self, world, meetings):
        async with await self.repository.connect() as conn:
            await conn.execute(
                "INSERT INTO waypoint_calendar_worlds VALUES (%s, %s)",
                (world, Jsonb([m.model_dump(mode="json") for m in meetings])),
            )

    async def events(self, world):
        async with await self.repository.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT events FROM waypoint_calendar_worlds WHERE id=%s", (world,)
                )
            ).fetchone()
            if row is None:
                raise KeyError(str(world))
            return [Meeting.model_validate(m) for m in row[0]]

    async def lookup(self, world, operation, arguments):
        async with await self.repository.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT result FROM waypoint_calendar_operations "
                    "WHERE world_id=%s AND operation_id=%s",
                    (world, operation),
                )
            ).fetchone()
            return existing_result(row[0] if row else None, arguments)

    async def update(self, world, operation, query, context):
        async with await self.repository.connect() as conn:
            async with conn.transaction():
                # World row lock serializes all writers, including distinct events/operations.
                row = await (
                    await conn.execute(
                        "SELECT events FROM waypoint_calendar_worlds WHERE id=%s FOR UPDATE",
                        (world,),
                    )
                ).fetchone()
                if row is None:
                    raise KeyError(str(world))
                old = await (
                    await conn.execute(
                        "SELECT result FROM waypoint_calendar_operations "
                        "WHERE world_id=%s AND operation_id=%s",
                        (world, operation),
                    )
                ).fetchone()
                existing = existing_result(old[0] if old else None, query.model_dump(mode="json"))
                if existing:
                    return existing
                events = [Meeting.model_validate(m) for m in row[0]]
                before = validate_update(events, query, context)
                after = before.model_copy(
                    update={
                        "start": query.start,
                        "end": query.end,
                        "revision": before.revision + 1,
                    },
                    deep=True,
                )
                result = OperationResult(
                    operation_id=operation,
                    arguments_hash=arguments_hash(query.model_dump(mode="json")),
                    event=after,
                )
                await conn.execute(
                    "UPDATE waypoint_calendar_worlds SET events=%s WHERE id=%s",
                    (
                        Jsonb(
                            [
                                (after if m.id == after.id else m).model_dump(mode="json")
                                for m in events
                            ]
                        ),
                        world,
                    ),
                )
                await conn.execute(
                    "INSERT INTO waypoint_calendar_operations VALUES (%s, %s, %s)",
                    (world, operation, Jsonb(result.model_dump(mode="json"))),
                )
                return result
