from pydantic import AwareDatetime, Field

from waypoint_agent.calendar import (
    CalendarStore,
    OperationResult,
    UpdateQuery,
    slots,
    validate_update,
)
from waypoint_agent.schemas import EarliestMeetingStart, RescheduleContext, Schema
from waypoint_agent.tools.mock import CalendarQuery, CalendarResults, Meeting


class EventQuery(Schema):
    event_id: str = Field(min_length=1)


class Slot(Schema):
    start: AwareDatetime
    end: AwareDatetime


class Availability(Schema):
    event: Meeting
    start: AwareDatetime
    end: AwareDatetime
    slot_minutes: int
    slots: list[Slot]
    exhaustive: bool
    earliest_meeting_start: EarliestMeetingStart | None = None


class CalendarTool:
    risk_level = "read_only"

    def __init__(self, store: CalendarStore, world, context: RescheduleContext):
        self.store, self.world, self.context = store, world, context


class SearchCalendar(CalendarTool):
    name = "search_calendar"
    description = "All meetings with exact participant, start inclusive/end exclusive."
    input_schema = CalendarQuery
    output_schema = CalendarResults

    async def execute(self, arguments):
        events = await self.store.events(self.world)
        return CalendarResults(
            meetings=[
                m
                for m in events
                if arguments.participant in m.participants
                and arguments.start <= m.start < arguments.end
            ],
            exhaustive=True,
        ).model_dump(mode="json")


class ReadCalendarEvent(CalendarTool):
    name = "read_calendar_event"
    description = "Read the authoritative calendar event, including revision."
    input_schema = EventQuery
    output_schema = Meeting

    async def execute(self, arguments):
        events = await self.store.events(self.world)
        event = next((m for m in events if m.id == arguments.event_id), None)
        if event is None:
            raise ValueError("Event not found")
        return event.model_dump(mode="json")


class CheckAvailability(CalendarTool):
    name = "check_availability"
    description = (
        "All free slots in the task destination window, earliest first; excludes this event."
    )
    input_schema = EventQuery
    output_schema = Availability

    async def execute(self, arguments):
        events = await self.store.events(self.world)
        event = next((m for m in events if m.id == arguments.event_id), None)
        if event is None:
            raise ValueError("Event not found")
        return Availability(
            event=event,
            start=self.context.destination_start,
            end=self.context.destination_end,
            slot_minutes=self.context.slot_minutes,
            slots=[Slot(start=s, end=e) for s, e in slots(events, event, self.context)],
            exhaustive=True,
            earliest_meeting_start=self.context.earliest_meeting_start,
        ).model_dump(mode="json")


class UpdateCalendarEvent(CalendarTool):
    name = "update_calendar_event"
    description = "Propose exact event timestamps and expected revision for human approval."
    risk_level = "approval_required"
    input_schema = UpdateQuery
    output_schema = OperationResult

    async def execute(self, arguments):
        raise PermissionError("Only the runtime may execute an approved operation")

    async def validate_proposal(self, query):
        events = await self.store.events(self.world)
        matches = [
            m
            for m in events
            if self.context.participant in m.participants
            and self.context.start <= m.start < self.context.end
        ]
        if len(matches) != 1 or matches[0].id != query.event_id:
            raise ValueError("Proposal must target the unique meeting in the search scope")
        before = validate_update(events, query, self.context)
        available = slots(events, before, self.context)
        if not available or available[0] != (query.start, query.end):
            raise ValueError("Proposal must use the earliest available destination slot")
        return before


def calendar_tools(store, world, context):
    from waypoint_agent.tools.registry import Registry

    return Registry(
        [
            cls(store, world, context)
            for cls in (SearchCalendar, ReadCalendarEvent, CheckAvailability, UpdateCalendarEvent)
        ]
    )
