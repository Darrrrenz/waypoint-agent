import asyncio
import json
import sys
from pathlib import Path

import pytest

from waypoint_agent.config import Settings, next_week
from waypoint_agent.evaluation.alice import MeetingEmailEvaluator
from waypoint_agent.models.scripted import ScriptedModel
from waypoint_agent.runtime import Runtime
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.memory import MemoryRepository
from waypoint_agent.tools.mock import Dataset, mock_tools

ROOT = Path(__file__).resolve().parents[1]


def pytest_asyncio_loop_factories():
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    return {"default": factory}


@pytest.fixture
def data():
    return Dataset.load(ROOT / "fixtures/environment.json")


@pytest.fixture
def script():
    return json.loads((ROOT / "fixtures/script.json").read_text())


@pytest.fixture
def state(data):
    return AgentState(
        task=Task(
            goal="Find my meeting with Alice next week and related unread emails",
            created_at=data.clock,
            context=next_week(data.clock, "America/Toronto", "alice@example.com"),
        )
    )


@pytest.fixture
def make_runtime(data):
    def make(actions=(), repository=None, model=None, tools=None, **overrides):
        return Runtime(
            model or ScriptedModel(actions),
            tools or mock_tools(data),
            repository or MemoryRepository(),
            MeetingEmailEvaluator(),
            Settings(_env_file=None, **overrides),
            clock=lambda: data.clock,
        )

    return make
