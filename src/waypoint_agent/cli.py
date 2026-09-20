import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import UUID

from waypoint_agent.config import Settings, next_week
from waypoint_agent.evaluation.alice import MeetingEmailEvaluator
from waypoint_agent.models.openai_compatible import OpenAICompatibleModel
from waypoint_agent.models.scripted import ScriptedModel
from waypoint_agent.runtime import Runtime
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.memory import MemoryRepository
from waypoint_agent.storage.postgres import PostgresRepository
from waypoint_agent.tools.mock import Dataset, mock_tools

DEFAULT_GOAL = (
    "Find my meeting with Alice next week and tell me whether I have any related unread emails."
)


async def dispatch(args):
    settings = Settings()
    repository = (
        MemoryRepository()
        if getattr(args, "ephemeral", False)
        else PostgresRepository(settings.database_url.get_secret_value())
    )
    if args.command == "init-db":
        await repository.initialize()
        print("Database initialized.")
        return 0
    if args.command == "inspect":
        checkpoint, events = await repository.load(args.task_id)
    else:
        dataset = Dataset.load(args.fixture)
        model = (
            ScriptedModel(json.loads(args.script.read_text(encoding="utf-8")))
            if args.model == "scripted"
            else OpenAICompatibleModel(settings)
        )
        state = AgentState(
            task=Task(
                goal=args.goal,
                created_at=dataset.clock,
                context=next_week(dataset.clock, settings.timezone, args.participant),
            )
        )
        runtime = Runtime(
            model,
            mock_tools(dataset),
            repository,
            MeetingEmailEvaluator(),
            settings,
            clock=lambda: dataset.clock,
        )
        checkpoint = await runtime.run(state)
        _, events = await repository.load(checkpoint.state.task.id)
    payload = {
        "checkpoint": checkpoint.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in events],
    }
    if getattr(args, "export", None):
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.command == "inspect" or args.json:
        print(json.dumps(payload, indent=2))
    else:
        state = checkpoint.state
        print(
            f"Task: {state.task.id}\nStatus: {state.task.status} ({state.task.termination_reason})"
        )
        print(state.answer or "No verified answer.")
        print(f"Trajectory: {len(events)} events; model calls: {state.model_calls}")
        if args.ephemeral:
            print("Ephemeral run: use --export to retain the trajectory.")
    return 0 if checkpoint.state.task.status == "completed" or args.command == "inspect" else 2


def main():
    parser = argparse.ArgumentParser(prog="waypoint-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("task_id", type=UUID)
    run = sub.add_parser("run")
    run.add_argument("goal", nargs="?", default=DEFAULT_GOAL)
    run.add_argument("--participant", default="alice@example.com")
    run.add_argument("--model", choices=["scripted", "live"], default="scripted")
    run.add_argument("--fixture", type=Path, default=Path("fixtures/environment.json"))
    run.add_argument("--script", type=Path, default=Path("fixtures/script.json"))
    run.add_argument("--ephemeral", action="store_true")
    run.add_argument("--json", action="store_true")
    run.add_argument("--export", type=Path)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # psycopg async connections require selector support on Windows.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            code = runner.run(dispatch(parser.parse_args()))
    except Exception as exc:
        print(
            f"Command failed ({type(exc).__name__}). Check configuration and infrastructure.",
            file=sys.stderr,
        )
        code = 1
    raise SystemExit(code)
