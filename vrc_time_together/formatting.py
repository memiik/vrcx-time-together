from __future__ import annotations

from datetime import date, datetime


def format_duration(milliseconds: int, *, compact: bool = False) -> str:
    """Format a numeric duration without losing its raw sortable value."""
    total_seconds = max(0, round(milliseconds / 1000))
    if total_seconds < 60:
        return f"{total_seconds}s"
    total_minutes = round(milliseconds / 60_000)
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    if compact or hours < 48:
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes:02d}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d {remaining_hours}h" if remaining_hours else f"{days}d"


def format_local_date(value: datetime | None) -> str:
    if value is None:
        return "No activity"
    return value.strftime("%d %b %Y")


def format_local_datetime(value: datetime | None) -> str:
    if value is None:
        return "No activity"
    today = datetime.now(value.tzinfo).date()
    if value.date() == today:
        return f"Today, {value:%H:%M}"
    if value.date() == date.fromordinal(today.toordinal() - 1):
        return f"Yesterday, {value:%H:%M}"
    return value.strftime("%d %b %Y, %H:%M")
