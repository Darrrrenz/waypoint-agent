"""Integration-only process that dies after the calendar commit, before checkpoint save."""

import asyncio
import os
import sys
from uuid import UUID

from waypoint_agent.calendar import PostgresCalendar
from waypoint_agent.cli import runtime_for
from waypoint_agent.config import Settings
from waypoint_agent.storage.postgres import PostgresRepository


class CrashRepository(PostgresRepository):
    async def save(self, checkpoint, event):
        if event.kind == "tool_result" and event.data["call"]["name"] == "update_calendar_event":
            os._exit(73)
        await super().save(checkpoint, event)


async def run():
    settings = Settings()
    repository = CrashRepository(settings.database_url.get_secret_value())
    task = UUID(sys.argv[1])
    checkpoint, _ = await repository.load(task)
    runtime = runtime_for(checkpoint.state, repository, PostgresCalendar(repository), settings)
    await runtime.resume(task)


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(run())
