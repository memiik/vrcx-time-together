from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class FriendStat:
    user_id: str
    display_name: str
    sessions: int
    milliseconds: int
    average_milliseconds: int
    longest_milliseconds: int
    active_days: int
    first_seen: datetime | None
    last_seen: datetime | None


@dataclass(frozen=True, slots=True)
class FriendIdentity:
    user_id: str
    display_name: str
    milliseconds: int = 0


@dataclass(frozen=True, slots=True)
class FriendMapNode:
    user_id: str
    display_name: str
    milliseconds: int
    sessions: int


@dataclass(frozen=True, slots=True)
class FriendMapLink:
    source_user_id: str
    target_user_id: str
    milliseconds: int
    encounters: int
    likelihood: float


@dataclass(frozen=True, slots=True)
class FriendMapData:
    nodes: tuple[FriendMapNode, ...]
    links: tuple[FriendMapLink, ...]


@dataclass(frozen=True, slots=True)
class DashboardData:
    friends: tuple[FriendStat, ...]
    friend_options: tuple[FriendIdentity, ...]
    matching_count: int
    latest_activity: datetime | None
    current_friend_count: int
    person_daily: tuple[tuple[date, int], ...]
    social_daily: tuple[tuple[date, int], ...]

    @property
    def total_person_milliseconds(self) -> int:
        return sum(value for _day, value in self.person_daily)

    @property
    def total_social_milliseconds(self) -> int:
        return sum(value for _day, value in self.social_daily)

    @property
    def visible_sessions(self) -> int:
        return sum(friend.sessions for friend in self.friends)


@dataclass(slots=True)
class AppState:
    start_date: date
    end_date: date
    search_term: str = ""
    minimum_minutes: int = 0
    result_limit: int | None = 50
    selected_friend_ids: list[str] = field(default_factory=list)
    aggregation: str = "Daily"
    overview_metric: str = "Time with friends"


@dataclass(frozen=True, slots=True)
class ComparisonData:
    series_by_user: dict[str, tuple[tuple[date, int], ...]]


@dataclass(frozen=True, slots=True)
class CoPresenceStat:
    user_id: str
    display_name: str
    milliseconds: int
    encounters: int


@dataclass(frozen=True, slots=True)
class FriendInsightsData:
    friend: FriendIdentity
    daily: tuple[tuple[date, int], ...]
    weekday_hourly_milliseconds: tuple[tuple[int, ...], ...]
    weekday_occurrences: tuple[int, ...]
    sessions: int
    context_milliseconds: tuple[int, int, int]
    context_encounters: tuple[int, int, int]
    co_presence: tuple[CoPresenceStat, ...]

    @property
    def total_milliseconds(self) -> int:
        return sum(value for _day, value in self.daily)

    @property
    def active_days(self) -> int:
        return sum(1 for _day, value in self.daily if value > 0)
