from pathlib import Path

from pydantic import AwareDatetime, Field, model_validator

from waypoint_agent.schemas import Schema


class Meeting(Schema):
    id: str
    title: str
    start: AwareDatetime
    end: AwareDatetime
    participants: list[str]
    topic: str


class Email(Schema):
    id: str
    subject: str
    participants: list[str]
    topic: str
    unread: bool
    sent_at: AwareDatetime
    body: str


class Dataset(Schema):
    clock: AwareDatetime
    meetings: list[Meeting]
    emails: list[Email]

    @model_validator(mode="after")
    def unique_ids(self):
        for records in (self.meetings, self.emails):
            if len({record.id for record in records}) != len(records):
                raise ValueError("Duplicate source IDs")
        return self

    @classmethod
    def load(cls, path: Path) -> "Dataset":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class CalendarQuery(Schema):
    participant: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def valid_window(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class CalendarResults(Schema):
    meetings: list[Meeting]
    exhaustive: bool


class EmailQuery(Schema):
    participant: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    unread_only: bool


class EmailResults(Schema):
    emails: list[Email]
    exhaustive: bool


class ReadQuery(Schema):
    email_id: str = Field(min_length=1)


class SearchCalendar:
    name = "search_calendar"
    description = "All meetings with exact participant identity, start inclusive/end exclusive."
    risk_level = "read_only"
    input_schema = CalendarQuery
    output_schema = CalendarResults

    def __init__(self, data: Dataset):
        self.data = data

    async def execute(self, arguments: CalendarQuery):
        return CalendarResults(
            meetings=[
                m
                for m in self.data.meetings
                if arguments.participant in m.participants
                and arguments.start <= m.start < arguments.end
            ],
            exhaustive=True,
        ).model_dump(mode="json")


class SearchEmail:
    name = "search_email"
    description = (
        "All emails matching BOTH exact participant identity and topic; optional unread filter."
    )
    risk_level = "read_only"
    input_schema = EmailQuery
    output_schema = EmailResults

    def __init__(self, data: Dataset):
        self.data = data

    async def execute(self, arguments: EmailQuery):
        return EmailResults(
            emails=[
                e
                for e in self.data.emails
                if arguments.participant in e.participants
                and e.topic == arguments.topic
                and (e.unread or not arguments.unread_only)
            ],
            exhaustive=True,
        ).model_dump(mode="json")


class ReadEmail:
    name = "read_email"
    description = "Read an email by stable ID without marking it read."
    risk_level = "read_only"
    input_schema = ReadQuery
    output_schema = Email

    def __init__(self, data: Dataset):
        self.data = data

    async def execute(self, arguments: ReadQuery):
        for email in self.data.emails:
            if email.id == arguments.email_id:
                return email.model_dump(mode="json")
        raise ValueError("Email ID not found")


def mock_tools(data: Dataset):
    from waypoint_agent.tools.registry import Registry

    return Registry([SearchCalendar(data), SearchEmail(data), ReadEmail(data)])
