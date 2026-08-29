from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

try:
    from tzlocal import get_localzone
except ImportError:  # The app remains usable without optional DST metadata.
    get_localzone = None


LOCAL_TIMEZONE: tzinfo = (
    get_localzone() if get_localzone is not None else datetime.now().astimezone().tzinfo
)


def local_timezone_label() -> str:
    now = datetime.now(LOCAL_TIMEZONE)
    offset = now.strftime("%z")
    offset = offset[:3] + ":" + offset[3:]
    return f"{now.tzname()} · UTC{offset}"


LOCAL_TIMEZONE_NAME = local_timezone_label()


def local_range_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(start_date, time.min, tzinfo=LOCAL_TIMEZONE)
    end = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=LOCAL_TIMEZONE
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def to_local(value: datetime | None) -> datetime | None:
    return value.astimezone(LOCAL_TIMEZONE) if value is not None else None


def sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
