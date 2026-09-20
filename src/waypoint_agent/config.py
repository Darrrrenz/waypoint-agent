from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from waypoint_agent.schemas import WorkflowContext


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
