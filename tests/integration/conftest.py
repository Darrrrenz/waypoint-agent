import os

import pytest

from waypoint_agent.storage.postgres import PostgresRepository


@pytest.fixture
async def repository():
    url = os.environ.get("WAYPOINT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WAYPOINT_TEST_DATABASE_URL to run real PostgreSQL integration tests")
    repo = PostgresRepository(url)
    await repo.initialize()
    return repo
