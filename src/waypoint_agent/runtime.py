import asyncio
import json
import logging
from time import monotonic

from pydantic import ValidationError

from waypoint_agent.approval import pending_request
from waypoint_agent.calendar import (
    RevisionConflict,
    SlotConflict,
    TransientReadError,
    arguments_hash,
)
from waypoint_agent.schemas import (
    ApprovalRequest,
    CallTool,
    Checkpoint,
    ExecutionRecord,
    ToolCall,
    ToolResult,
    TrajectoryEvent,
    action_adapter,
    utc_now,
)

logger = logging.getLogger("waypoint_agent")
LIMIT_FIELDS = (
    "max_steps",
    "max_model_calls",
    "max_errors",
    "deadline_seconds",
    "model_timeout",
    "tool_timeout",
    "storage_timeout",
    "read_retries",
    "retry_backoff",
)
TERMINAL = {"completed", "failed", "limited", "denied"}


class Runtime:
    def __init__(self, model, tools, repository, evaluator, settings, clock=utc_now):
        self.model, self.tools, self.repository = model, tools, repository
        self.evaluator, self.settings, self.clock = evaluator, settings, clock

    async def run(self, state):
        if state.task.status != "pending":
            raise ValueError("Use resume for an existing task")
        state = state.model_copy(deep=True)
        state.limits = {key: getattr(self.settings, key) for key in LIMIT_FIELDS}
        if hasattr(self.model, "actions"):
            state.script = self.model.actions
        return await self._continue(Checkpoint(state=state), resumed=False)

    async def resume(self, task_id):
        async with self.repository.task_lock(task_id):
            checkpoint, _ = await self.repository.load(task_id)
            state = checkpoint.state
            if state.task.status in TERMINAL:
                return checkpoint
            if state.pending_approval_id:
                request, _ = pending_request(state)
                if request.decision == "pending":
                    return checkpoint
            self.settings = self.settings.model_copy(update=state.limits)
            if state.script is not None:
                from waypoint_agent.models.scripted import ScriptedModel

                self.model = ScriptedModel(state.script)
            return await self._continue(checkpoint, resumed=True)

    async def _continue(self, checkpoint, resumed):
        state, settings = checkpoint.state, self.settings
        if state.task.workflow == "reschedule":
            from waypoint_agent.evaluation.reschedule import RescheduleEvaluator

            evaluator = RescheduleEvaluator()
            update_tool = self.tools.get("update_calendar_event")
            if (
                update_tool.world != state.task.calendar_world_id
                or update_tool.context != state.task.context
            ):
                raise PermissionError("Calendar tools do not match the persisted task")
        else:
            evaluator = self.evaluator
        # A crashed in-flight call is charged its full reservation on restart.
        state.active_seconds += state.reserved_seconds
        state.reserved_seconds = 0
        tick = monotonic()
        deadline = tick + max(0, settings.deadline_seconds - state.active_seconds)

        async def record(kind, data, latency_ms=None, usage=None):
            nonlocal tick
            now = monotonic()
            state.active_seconds += now - tick
            tick = now
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
            async with asyncio.timeout(settings.storage_timeout):
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
                state.reserved_seconds = 0
                raise TimeoutError("Execution deadline reached")
            call_deadline = monotonic() + min(limit, remaining)
            try:
                async with asyncio.timeout(min(limit, remaining)):
                    result = await awaitable
                if monotonic() >= call_deadline:
                    raise TimeoutError("Call exceeded its time budget")
                return result
            finally:
                state.reserved_seconds = 0

        def reserve(limit):
            state.reserved_seconds = max(0, min(limit, deadline - monotonic()))

        def details(exc):
            result = {"error": type(exc).__name__}
            if isinstance(exc, ValidationError):
                result["validation"] = [
                    {"location": list(e["loc"]), "type": e["type"], "message": e["msg"]}
                    for e in exc.errors(include_input=False, include_context=False)
                ]
            return result

        async def failure(kind, exc, call=None):
            state.errors += 1
            state.pending_action, state.read_attempts = None, 0
            feedback = f"{kind}:{call.name + ':' if call else ''}{type(exc).__name__}"
            state.feedback.append(feedback)
            await record(
                kind,
                {
                    **details(exc),
                    "feedback": feedback,
                    **({"call": call.model_dump(mode="json")} if call else {}),
                    **({"error": feedback} if kind == "model_error" else {}),
                },
            )

        async def observe(call, output, execution=None, latency_ms=None):
            observation = ToolResult(
                id=f"obs-{len(state.observations) + 1}", call=call, output=output
            )
            state.observations.append(observation)
            state.pending_action, state.read_attempts = None, 0
            if execution:
                execution.observation_id = observation.id
                state.executions.append(execution)
            await record("tool_result", observation.model_dump(mode="json"), latency_ms)

        async def approved_write():
            started = monotonic()
            request, query = pending_request(state)
            if request.decision == "denied":
                request.outcome, request.resolved_at = "denied", utc_now()
                state.pending_action, state.pending_approval_id = None, None
                state.task.status, state.task.termination_reason = "denied", "denied"
                await record("approval_denied", {"approval_id": str(request.id)})
                return False
            if request.decision != "approved":
                raise PermissionError("Human approval required")
            tool = self.tools.get(request.call.name)
            reserve(settings.tool_timeout)
            await record("operation_reconcile", {"operation_id": str(request.operation_id)})
            try:
                result = await invoke(
                    tool.store.lookup(tool.world, request.operation_id, request.call.arguments),
                    settings.tool_timeout,
                )
            except Exception as exc:
                state.task.status = "unresolved"
                await record("operation_unresolved", details(exc))
                return False
            if result is None:
                reserve(settings.tool_timeout)
                await record("operation_requested", {"operation_id": str(request.operation_id)})
                try:
                    result = await invoke(
                        tool.store.update(tool.world, request.operation_id, query, tool.context),
                        settings.tool_timeout,
                    )
                except (RevisionConflict, SlotConflict) as exc:
                    request.outcome, request.resolved_at = "stale", utc_now()
                    state.pending_approval_id, state.pending_action = None, None
                    state.replan_after_observation = len(state.observations)
                    state.feedback.append(
                        "Calendar changed: search and propose again for fresh approval"
                    )
                    state.errors += 1
                    await record(
                        "approval_invalidated", {**details(exc), "approval_id": str(request.id)}
                    )
                    return True
                except (ValueError, PermissionError) as exc:
                    request.outcome, request.resolved_at = "stale", utc_now()
                    state.pending_approval_id = None
                    await failure("tool_error", exc, request.call)
                    return True
                except Exception as exc:
                    # No blind retry: a later resume reconciles the same operation first.
                    state.task.status = "unresolved"
                    await record("operation_unresolved", details(exc))
                    return False
            request.outcome, request.resolved_at = "applied", utc_now()
            state.pending_approval_id = None
            await observe(
                request.call,
                result.model_dump(mode="json"),
                ExecutionRecord(
                    approval_id=request.id,
                    operation_id=request.operation_id,
                    arguments_hash=request.arguments_hash,
                    observation_id="",
                    result=result.model_dump(mode="json"),
                ),
                latency_ms=(monotonic() - started) * 1000,
            )
            return True

        state.task.status = "running"
        await record(
            "task_resumed" if resumed else "task_started",
            {"goal": state.task.goal, "context": state.task.context.model_dump(mode="json")},
        )
        if state.pending_approval_id and not await approved_write():
            return checkpoint
        while True:
            reason = None
            if monotonic() >= deadline:
                reason = "deadline"
            elif state.errors >= settings.max_errors:
                reason = "error_budget"
            elif state.pending_action is None:
                if state.steps >= settings.max_steps:
                    reason = "max_steps"
                elif state.model_calls >= settings.max_model_calls:
                    reason = "max_model_calls"
            if reason:
                state.task.status = "failed" if reason == "error_budget" else "limited"
                state.task.termination_reason = reason
                await record("task_terminated", {"reason": reason})
                return checkpoint
            if state.pending_action is None:
                state.steps += 1
                state.model_calls += 1
                reserve(settings.model_timeout)
                await record("model_requested", {"step": state.steps})
                started = monotonic()
                try:
                    reply = await invoke(
                        self.model.next_action(state.model_copy(deep=True), self.tools.describe()),
                        settings.model_timeout,
                    )
                except Exception as exc:
                    await failure("model_error", exc)
                    continue
                state.pending_action = reply.action
                await record(
                    "model_response",
                    {"action": reply.action},
                    (monotonic() - started) * 1000,
                    reply.usage,
                )
            try:
                adapter = action_adapter(state.task.workflow)
                raw = state.pending_action
                action = (
                    adapter.validate_json(raw, strict=True)
                    if isinstance(raw, str)
                    else adapter.validate_python(raw, strict=True)
                )
            except Exception as exc:
                await failure("invalid_action", exc)
                continue
            if isinstance(action, CallTool):
                await record("tool_requested", action.call.model_dump(mode="json"))
                try:
                    tool = self.tools.get(action.call.name)
                    arguments = tool.input_schema.model_validate_json(
                        json.dumps(action.call.arguments), strict=True
                    )
                except Exception as exc:
                    await failure("tool_error", exc, action.call)
                    continue
                if self.tools.permission(tool) == "approval_required":
                    reserve(settings.tool_timeout)
                    await record("proposal_validation", action.call.model_dump(mode="json"))
                    try:
                        if state.task.workflow != "reschedule":
                            raise PermissionError("This workflow cannot mutate calendars")
                        before = await invoke(
                            tool.validate_proposal(arguments), settings.tool_timeout
                        )
                    except Exception as exc:
                        await failure("tool_error", exc, action.call)
                        continue
                    normalized = arguments.model_dump(mode="json")
                    call = ToolCall(name=tool.name, arguments=normalized)
                    before_json = before.model_dump(mode="json")
                    request = ApprovalRequest(
                        task_id=state.task.id,
                        calendar_world_id=state.task.calendar_world_id,
                        event_id=arguments.event_id,
                        call=call,
                        arguments_hash=arguments_hash(normalized),
                        expected_revision=arguments.expected_revision,
                        before=before_json,
                        after={
                            **before_json,
                            "start": normalized["start"],
                            "end": normalized["end"],
                            "revision": before.revision + 1,
                        },
                        created_at=utc_now(),
                    )
                    state.approvals.append(request)
                    state.pending_action = {
                        "kind": "call_tool",
                        "call": call.model_dump(mode="json"),
                    }
                    state.pending_approval_id = request.id
                    state.task.status = "waiting_for_approval"
                    await record("approval_requested", request.model_dump(mode="json"))
                    return checkpoint
                while True:
                    if state.read_attempts >= 1 + settings.read_retries:
                        await failure(
                            "tool_error", TransientReadError("Retry budget exhausted"), action.call
                        )
                        break
                    state.read_attempts += 1
                    reserve(settings.tool_timeout)
                    await record("read_attempt", {"attempt": state.read_attempts})
                    started = monotonic()
                    try:
                        output = await invoke(tool.execute(arguments), settings.tool_timeout)
                        validated = tool.output_schema.model_validate_json(
                            json.dumps(output), strict=True
                        )
                    except TransientReadError as exc:
                        if state.read_attempts >= 1 + settings.read_retries:
                            await failure("tool_error", exc, action.call)
                            break
                        await record("read_retry", details(exc))
                        await asyncio.sleep(
                            min(
                                settings.retry_backoff * 2 ** (state.read_attempts - 1),
                                max(0, deadline - monotonic()),
                            )
                        )
                        continue
                    except Exception as exc:
                        await failure("tool_error", exc, action.call)
                        break
                    await observe(
                        action.call,
                        validated.model_dump(mode="json"),
                        latency_ms=(monotonic() - started) * 1000,
                    )
                    break
            else:
                try:
                    result = evaluator.verify(state.model_copy(deep=True), action.findings)
                except Exception as exc:
                    await failure("evaluation_error", exc)
                    continue
                state.pending_action = None
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
