from __future__ import annotations

from datetime import date, datetime


ENGLISH_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
ENGLISH_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def format_english_date(value: date, *, short: bool = False) -> str:
    """Format dates deterministically in English, independent of the OS locale."""
    month = ENGLISH_MONTHS[value.month]
    if short:
        month = month[:3]
    return f"{value.day:02d} {month} {value.year}"


def format_english_day(value: date, *, include_year: bool = False) -> str:
    month = ENGLISH_MONTHS[value.month][:3]
    suffix = f" {value.year}" if include_year else ""
    return f"{ENGLISH_WEEKDAYS[value.weekday()]}, {value.day:02d} {month}{suffix}"


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
    return format_english_date(value.date(), short=True)


def format_local_datetime(value: datetime | None) -> str:
    if value is None:
        return "No activity"
    today = datetime.now(value.tzinfo).date()
    if value.date() == today:
        return f"Today, {value:%H:%M}"
    if value.date() == date.fromordinal(today.toordinal() - 1):
        return f"Yesterday, {value:%H:%M}"
    return f"{format_english_date(value.date(), short=True)}, {value:%H:%M}"
