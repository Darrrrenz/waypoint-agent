import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from waypoint_agent.approval import decide
from waypoint_agent.calendar import MemoryCalendar, PostgresCalendar
from waypoint_agent.config import Settings, next_week, reschedule_context
from waypoint_agent.evaluation.alice import MeetingEmailEvaluator
from waypoint_agent.models.openai_compatible import OpenAICompatibleModel
from waypoint_agent.models.reschedule import RescheduleDemoModel
from waypoint_agent.models.scripted import ScriptedModel
from waypoint_agent.runtime import Runtime
from waypoint_agent.schemas import AgentState, Task
from waypoint_agent.storage.memory import MemoryRepository
from waypoint_agent.storage.postgres import PostgresRepository
from waypoint_agent.tools.calendar import calendar_tools
from waypoint_agent.tools.mock import Dataset, mock_tools

DEFAULT_GOAL = (
    "Find my meeting with Alice next week and tell me whether I have any related unread emails."
)


def runtime_for(state, repository, store, settings):
    settings = settings.model_copy(update={**state.limits, **state.model_config_values})
    dataset = Dataset.model_validate(state.dataset)
    if state.model_kind == "live":
        model = OpenAICompatibleModel(settings)
    elif state.model_kind == "reschedule_demo":
        model = RescheduleDemoModel()
    else:
        model = ScriptedModel(state.script or [])
    tools = (
        calendar_tools(store, state.task.calendar_world_id, state.task.context)
        if state.task.workflow == "reschedule"
        else mock_tools(dataset)
    )
    return Runtime(
        model, tools, repository, MeetingEmailEvaluator(), settings, clock=lambda: dataset.clock
    )


def show_approvals(state):
    for request in state.approvals:
        print(f"Approval: {request.id} ({request.decision}; {request.outcome or 'unresolved'})")
        print(f"World: {request.calendar_world_id}; event: {request.event_id}")
        print(f"Before: {request.before['start']} – {request.before['end']}")
        print(f"After:  {request.after['start']} – {request.after['end']}")
        print(f"Revision: {request.expected_revision}; operation: {request.operation_id}")
        print(f"Arguments SHA-256: {request.arguments_hash}")
        print(json.dumps(request.call.arguments, sort_keys=True))


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
    store = MemoryCalendar() if getattr(args, "ephemeral", False) else PostgresCalendar(repository)
    if args.command == "seed-calendar":
        world = args.world_id or uuid4()
        await store.seed(world, Dataset.load(args.fixture).meetings)
        print(f"Seeded calendar world: {world}")
        return 0
    if args.command in ("approvals", "approve", "deny"):
        if args.command != "approvals":
            await decide(
                repository,
                args.task_id,
                args.approval_id,
                "approved" if args.command == "approve" else "denied",
            )
        checkpoint, _ = await repository.load(args.task_id)
        if args.json:
            print(
                json.dumps(
                    [a.model_dump(mode="json") for a in checkpoint.state.approvals], indent=2
                )
            )
        else:
            show_approvals(checkpoint.state)
        return 0
    if args.command == "inspect":
        checkpoint, events = await repository.load(args.task_id)
    elif args.command == "resume":
        saved, _ = await repository.load(args.task_id)
        runtime = runtime_for(saved.state, repository, store, settings)
        checkpoint = await runtime.resume(args.task_id)
        _, events = await repository.load(args.task_id)
    else:
        dataset = Dataset.load(args.fixture)
        reschedule = args.workflow == "reschedule"
        world = args.world_id
        if reschedule:
            if args.ephemeral:
                world = world or uuid4()
                await store.seed(world, dataset.meetings)
            elif world is None:
                raise ValueError("Rescheduling requires --world-id from seed-calendar")
            await store.events(world)
        context = (
            reschedule_context(dataset.clock, settings.timezone, args.participant, args.target_date)
            if reschedule
            else next_week(dataset.clock, settings.timezone, args.participant)
        )
        if reschedule:
            context.slot_minutes = args.slot_minutes
        script_path = args.script or (None if reschedule else Path("fixtures/script.json"))
        state = AgentState(
            task=Task(
                goal=args.goal
                or (
                    "Move my meeting with Alice to Friday afternoon."
                    if reschedule
                    else DEFAULT_GOAL
                ),
                created_at=dataset.clock,
                context=context,
                workflow=args.workflow,
                calendar_world_id=world,
            ),
            dataset=dataset.model_dump(mode="json"),
            model_config_values={"model": settings.model, "base_url": settings.base_url},
            model_kind=(
                "live"
                if args.model == "live"
                else "reschedule_demo"
                if reschedule and script_path is None
                else "scripted"
            ),
            script=json.loads(script_path.read_text(encoding="utf-8")) if script_path else None,
        )
        runtime = runtime_for(state, repository, store, settings)
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
        if state.pending_approval_id:
            show_approvals(state)
        if getattr(args, "ephemeral", False):
            print("Ephemeral run: use --export to retain the trajectory.")
    return (
        0
        if checkpoint.state.task.status in ("completed", "waiting_for_approval", "denied")
        or args.command == "inspect"
        else 2
    )


def main():
    parser = argparse.ArgumentParser(prog="waypoint-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    seed = sub.add_parser("seed-calendar")
    seed.add_argument("--world-id", type=UUID, default=None)
    seed.add_argument("--fixture", type=Path, default=Path("fixtures/environment.json"))
    inspect = sub.add_parser("inspect")
    inspect.add_argument("task_id", type=UUID)
    for command in ("approvals", "approve", "deny", "resume"):
        child = sub.add_parser(command)
        child.add_argument("task_id", type=UUID)
        child.add_argument("--json", action="store_true")
        if command in ("approve", "deny"):
            child.add_argument("approval_id", type=UUID)
        if command == "resume":
            child.add_argument("--export", type=Path)
    run = sub.add_parser("run")
    run.add_argument("goal", nargs="?")
    run.add_argument("--workflow", choices=["meeting_email", "reschedule"], default="meeting_email")
    run.add_argument("--world-id", type=UUID)
    run.add_argument("--target-date", type=date.fromisoformat)
    run.add_argument("--slot-minutes", type=int, choices=range(1, 241), default=15)
    run.add_argument("--participant", default="alice@example.com")
    run.add_argument("--model", choices=["scripted", "live"], default="scripted")
    run.add_argument("--fixture", type=Path, default=Path("fixtures/environment.json"))
    run.add_argument("--script", type=Path)
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
