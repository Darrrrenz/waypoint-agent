from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from waypoint_agent.schemas import RescheduleContext, WorkflowContext


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WAYPOINT_", env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("postgresql://waypoint:waypoint@localhost:5432/waypoint")
    model: str = "gpt-4o-mini"
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.openai.com/v1"
    timezone: str = "America/Toronto"
    model_timeout: float = Field(default=30, gt=0)
    tool_timeout: float = Field(default=5, gt=0)
    storage_timeout: float = Field(default=5, gt=0)
    deadline_seconds: float = Field(default=120, gt=0)
    max_steps: int = Field(default=12, ge=1)
    max_model_calls: int = Field(default=12, ge=1)
    max_errors: int = Field(default=3, ge=1)
    read_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff: float = Field(default=0.05, ge=0, le=5)


def next_week(now: datetime, timezone: str, participant: str) -> WorkflowContext:
    if now.tzinfo is None:
        raise ValueError("The clock must be timezone aware")
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    monday = local.date() + timedelta(days=7 - local.weekday())
    return WorkflowContext(
        participant=participant,
        start=datetime.combine(monday, time.min, zone),
        end=datetime.combine(monday + timedelta(days=7), time.min, zone),
        timezone=timezone,
    )


def reschedule_context(now, timezone, participant, target_date: date | None = None):
    search = next_week(now, timezone, participant)
    friday = target_date or (search.start.date() + timedelta(days=4))
    if friday.weekday() != 4:
        raise ValueError("The reschedule workflow requires a Friday target date")
    zone = ZoneInfo(timezone)
    return RescheduleContext(
        **search.model_dump(),
        destination_start=datetime.combine(friday, time(13), zone),
        destination_end=datetime.combine(friday, time(17), zone),
    )
