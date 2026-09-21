"""Credential-free demo policy derived solely from durable observations."""

from waypoint_agent.schemas import ModelReply


class RescheduleDemoModel:
    async def next_action(self, state, tools):
        def call(name, **arguments):
            return ModelReply(
                action={
                    "kind": "call_tool",
                    "call": {
                        "name": name,
                        "arguments": arguments,
                    },
                }
            )

        def complete(outcome, meetings, operation=None):
            return ModelReply(
                action={
                    "kind": "propose_completion",
                    "findings": {
                        "outcome": outcome,
                        "meeting_ids": meetings,
                        "operation_id": operation,
                        "evidence_ids": [o.id for o in state.observations],
                    },
                }
            )

        ctx = state.task.context
        if state.executions:
            execution = state.executions[-1]
            event_id = execution.result["event"]["id"]
            if state.observations[-1].call.name != "read_calendar_event":
                return call("read_calendar_event", event_id=event_id)
            return complete("rescheduled", [event_id], str(execution.operation_id))
        last = state.observations[-1] if state.observations else None
        if (
            last is None
            or len(state.observations) <= state.replan_after_observation
            or last.call.name not in ("search_calendar", "check_availability")
        ):
            return call(
                "search_calendar",
                participant=ctx.participant,
                start=ctx.start.isoformat(),
                end=ctx.end.isoformat(),
            )
        if last.call.name == "search_calendar":
            meetings = last.output["meetings"]
            if len(meetings) != 1:
                return complete(
                    "missing_meeting" if not meetings else "ambiguous", [m["id"] for m in meetings]
                )
            return call("check_availability", event_id=meetings[0]["id"])
        if not last.output["slots"]:
            return complete("no_availability", [last.output["event"]["id"]])
        first = last.output["slots"][0]
        return call(
            "update_calendar_event",
            event_id=last.output["event"]["id"],
            expected_revision=last.output["event"]["revision"],
            **first,
        )
