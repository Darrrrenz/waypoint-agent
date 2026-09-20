import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from time import monotonic

from pydantic import ValidationError

from waypoint_agent.config import Settings
from waypoint_agent.interfaces import Evaluator, Model, Repository
from waypoint_agent.schemas import (
    ACTION_ADAPTER,
    AgentState,
    CallTool,
    Checkpoint,
    ToolResult,
    TrajectoryEvent,
    utc_now,
)
from waypoint_agent.tools.registry import Registry

logger = logging.getLogger("waypoint_agent")


class Runtime:
    def __init__(
        self,
        model: Model,
        tools: Registry,
        repository: Repository,
        evaluator: Evaluator,
        settings: Settings,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.model, self.tools, self.repository = model, tools, repository
        self.evaluator, self.settings, self.clock = evaluator, settings, clock

    async def run(self, state: AgentState) -> Checkpoint:
        if state.task.status != "pending":
            raise ValueError("Only new tasks can run; crash recovery is not implemented")
        checkpoint = Checkpoint(state=state.model_copy(deep=True))
        state = checkpoint.state
        deadline = monotonic() + self.settings.deadline_seconds

        async def record(kind, data, latency_ms=None, usage=None):
            checkpoint.last_sequence += 1
            event = TrajectoryEvent(
                task_id=state.task.id,
                sequence=checkpoint.last_sequence,
                timestamp=self.clock(),
                kind=kind,
                data=data,
                latency_ms=latency_ms,
                usage=usage,
            )
            # Storage failure is fatal; never continue with an unrecorded observation.
            async with asyncio.timeout(self.settings.storage_timeout):
                await self.repository.save(checkpoint, event)
            logger.info(
                json.dumps(
                    {
                        "task_id": str(state.task.id),
                        "sequence": event.sequence,
                        "kind": kind,
                        "latency_ms": latency_ms,
                        "usage": usage,
                    }
                )
            )

        async def invoke(awaitable, limit):
            remaining = deadline - monotonic()
            if remaining <= 0:
                awaitable.close()
                raise TimeoutError("Execution deadline reached")
            call_deadline = monotonic() + min(limit, remaining)
            async with asyncio.timeout(min(limit, remaining)):
                result = await awaitable
            if monotonic() >= call_deadline:
                raise TimeoutError("Call exceeded its time budget")
            return result

        def error_details(exc):
            details = {"error": type(exc).__name__}
            if isinstance(exc, ValidationError):
                details["validation"] = [
                    {"location": list(e["loc"]), "type": e["type"], "message": e["msg"]}
                    for e in exc.errors(include_input=False, include_context=False)
                ]
            return details

        state.task.status = "running"
        await record(
            "task_started",
            {"goal": state.task.goal, "context": state.task.context.model_dump(mode="json")},
        )
        while True:
            reason = None
            if monotonic() >= deadline:
                reason = "deadline"
            elif state.errors >= self.settings.max_errors:
                reason = "error_budget"
            elif state.steps >= self.settings.max_steps:
                reason = "max_steps"
            elif state.model_calls >= self.settings.max_model_calls:
                reason = "max_model_calls"
            if reason:
                state.task.status = "failed" if reason == "error_budget" else "limited"
                state.task.termination_reason = reason
                await record("task_terminated", {"reason": reason})
                return checkpoint

            state.steps += 1
            state.model_calls += 1
            await record("model_requested", {"step": state.steps})
            started = monotonic()
            try:
                reply = await invoke(
                    self.model.next_action(state.model_copy(deep=True), self.tools.describe()),
                    self.settings.model_timeout,
                )
            except Exception as exc:
                state.errors += 1
                # Provider exceptions may contain credentials/headers. Record only their type.
                feedback = f"model_error:{type(exc).__name__}"
                state.feedback.append(feedback)
                await record("model_error", {"error": feedback}, (monotonic() - started) * 1000)
                continue
            await record(
                "model_response",
                {"action": reply.action},
                (monotonic() - started) * 1000,
                reply.usage,
            )
            try:
                action = (
                    ACTION_ADAPTER.validate_json(reply.action, strict=True)
                    if isinstance(reply.action, str)
                    else ACTION_ADAPTER.validate_python(reply.action, strict=True)
                )
            except Exception as exc:
                state.errors += 1
                state.feedback.append(
                    "Invalid action schema; return call_tool or propose_completion"
                )
                await record("invalid_action", error_details(exc))
                continue

            if isinstance(action, CallTool):
                await record("tool_requested", action.call.model_dump(mode="json"))
                started = monotonic()
                try:
                    tool = self.tools.get(action.call.name)
                    arguments = tool.input_schema.model_validate_json(
                        json.dumps(action.call.arguments), strict=True
                    )
                    output = await invoke(tool.execute(arguments), self.settings.tool_timeout)
                    validated = tool.output_schema.model_validate_json(
                        json.dumps(output), strict=True
                    )
                except Exception as exc:
                    state.errors += 1
                    feedback = f"tool_error:{action.call.name}:{type(exc).__name__}"
                    state.feedback.append(feedback)
                    await record(
                        "tool_error",
                        {
                            **error_details(exc),
                            "feedback": feedback,
                            "call": action.call.model_dump(mode="json"),
                        },
                        (monotonic() - started) * 1000,
                    )
                    continue
                observation = ToolResult(
                    id=f"obs-{len(state.observations) + 1}",
                    call=action.call,
                    output=validated.model_dump(mode="json"),
                )
                state.observations.append(observation)
                await record(
                    "tool_result",
                    observation.model_dump(mode="json"),
                    (monotonic() - started) * 1000,
                )
            else:
                started = monotonic()
                try:
                    result = self.evaluator.verify(state.model_copy(deep=True), action.findings)
                except Exception as exc:
                    state.errors += 1
                    state.feedback.append(f"evaluation_error:{type(exc).__name__}")
                    await record(
                        "evaluation_error", error_details(exc), (monotonic() - started) * 1000
                    )
                    continue
                if monotonic() >= deadline:
                    await record("completion_rejected", {"reason": "deadline"})
                    continue
                if result.accepted:
                    state.findings, state.answer = action.findings, result.answer
                    state.task.status, state.task.termination_reason = "completed", "verified"
                    await record("completion_verified", result.model_dump(mode="json"))
                    return checkpoint
                state.errors += 1
                state.feedback.extend(result.reasons)
                await record("completion_rejected", result.model_dump(mode="json"))
