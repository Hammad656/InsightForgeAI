"""Schedule business-local executive reports and publish them through M5."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00]+$")
    cadence: Literal["daily", "weekly", "monthly"]
    timezone: str = Field(default="Asia/Karachi", max_length=80)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekday: int = Field(default=0, ge=0, le=6)
    month_day: int = Field(default=1, ge=1, le=28)
    period: Literal["previous_day", "previous_week", "previous_month"] = "previous_month"

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Select an IANA business timezone") from exc
        return value


def next_occurrence(config: ScheduleConfig, after: datetime) -> datetime:
    zone = ZoneInfo(config.timezone)
    day = after.astimezone(zone).date()
    for offset in range(32):
        candidate = day + timedelta(days=offset)
        if config.cadence == "weekly" and candidate.weekday() != config.weekday:
            continue
        if config.cadence == "monthly" and candidate.day != config.month_day:
            continue
        # fold=0 selects the first repeated wall time. In a spring gap the
        # pre-transition offset shifts the chosen wall time forward by the gap.
        instant = datetime.combine(candidate, time(config.hour, config.minute), zone).astimezone(timezone.utc)
        if instant > after:
            return instant
    raise ValueError("No schedule occurrence is inside the supported interval")


def report_period(config: ScheduleConfig, due_at: datetime) -> tuple[date, date]:
    today = due_at.astimezone(ZoneInfo(config.timezone)).date()
    if config.period == "previous_day":
        return today - timedelta(days=1), today
    if config.period == "previous_week":
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday
    month = today.replace(day=1)
    return (month - timedelta(days=1)).replace(day=1), month
