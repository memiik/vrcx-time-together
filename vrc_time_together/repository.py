from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from .friend_introductions import IntroductionSession, infer_introductions
from .models import (
    AppState,
    CoPresenceStat,
    ComparisonData,
    DashboardData,
    FriendIdentity,
    FriendInsightsData,
    FriendMapData,
    FriendMapLink,
    FriendMapNode,
    FriendStat,
)
from .timezone_utils import (
    LOCAL_TIMEZONE,
    local_range_utc,
    parse_utc,
    sqlite_timestamp,
    to_local,
)

LOGGER = logging.getLogger(__name__)
DB_NAME = "VRCX.sqlite3"


@dataclass(frozen=True, slots=True)
class _FriendInterval:
    user_id: str
    display_name: str
    start: datetime
    end: datetime
    location: str


class VrcxDataError(RuntimeError):
    """A user-facing VRCX database or schema error."""


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def open_database(database_path: Path) -> sqlite3.Connection:
    """Open VRCX in URI read-only mode. No writes or schema changes are possible."""
    uri = database_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def find_database(script_path: Path | None = None) -> Path:
    candidates: list[Path] = []
    if script_path is not None:
        candidates.append(script_path.resolve().with_name(DB_NAME))
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "VRCX" / DB_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Could not find {DB_NAME}.\n\nSearched:\n"
        + "\n".join(str(path) for path in candidates)
    )


def find_friend_table(connection: sqlite3.Connection) -> str:
    tables = [
        row[0]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'usr%_friend_log_current'
            """
        )
    ]
    if not tables:
        raise VrcxDataError(
            "No current-friends table was found. Open VRCX once and try again."
        )
    return max(
        tables,
        key=lambda table: connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0],
    )


def find_friend_history_table(
    connection: sqlite3.Connection, current_table: str
) -> str | None:
    expected = current_table.removesuffix("_current") + "_history"
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (expected,),
    ).fetchone()
    return row[0] if row is not None else None


def aggregate_time_series(
    daily: list[tuple[date, int]] | tuple[tuple[date, int], ...], granularity: str
) -> list[tuple[date, int]]:
    if granularity == "Daily":
        return list(daily)
    totals: dict[date, int] = {}
    for day, milliseconds in daily:
        bucket = (
            day - timedelta(days=day.weekday())
            if granularity == "Weekly"
            else day.replace(day=1)
        )
        totals[bucket] = totals.get(bucket, 0) + milliseconds
    return list(totals.items())


class VrcxRepository:
    """Read-only, cached access to the VRCX social-history tables."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self._cache: dict[tuple[Any, ...], Any] = {}
        self._cache_signature: tuple[int, int] | None = None
        self._lock = threading.RLock()

    def _database_signature(self) -> tuple[int, int]:
        stat = self.database_path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _prepare_cache(self) -> tuple[int, int]:
        signature = self._database_signature()
        with self._lock:
            if signature != self._cache_signature:
                self._cache.clear()
                self._cache_signature = signature
                LOGGER.info("VRCX database change detected; query cache cleared")
        return signature

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_signature = None

    def load_dashboard(self, state: AppState) -> DashboardData:
        signature = self._prepare_cache()
        key = (
            "dashboard",
            signature,
            state.start_date,
            state.end_date,
            state.search_term.casefold(),
            state.minimum_minutes,
            state.result_limit,
        )
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._query_dashboard(state)
        except sqlite3.Error as error:
            LOGGER.exception("Dashboard query failed")
            raise VrcxDataError(
                "VRCX data could not be read. VRCX may be updating the database, "
                "or its schema may have changed. Try Refresh in a moment."
            ) from error
        with self._lock:
            self._cache[key] = result
        return result

    def load_comparison(self, state: AppState) -> ComparisonData:
        signature = self._prepare_cache()
        user_ids = tuple(dict.fromkeys(state.selected_friend_ids))
        key = ("comparison", signature, state.start_date, state.end_date, user_ids)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            with closing(open_database(self.database_path)) as connection:
                per_friend, _social = self._load_local_daily_series(
                    connection, state.start_date, state.end_date, list(user_ids)
                )
        except sqlite3.Error as error:
            LOGGER.exception("Comparison query failed")
            raise VrcxDataError(
                "The selected comparison could not be read from VRCX. Try again shortly."
            ) from error
        result = ComparisonData(
            {user_id: tuple(series) for user_id, series in per_friend.items()}
        )
        with self._lock:
            self._cache[key] = result
        return result

    def load_friend_insights(
        self, state: AppState, user_id: str
    ) -> FriendInsightsData:
        signature = self._prepare_cache()
        key = (
            "friend-insights",
            signature,
            state.start_date,
            state.end_date,
            user_id,
        )
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._query_friend_insights(state, user_id)
        except sqlite3.Error as error:
            LOGGER.exception("Friend insights query failed")
            raise VrcxDataError(
                "The selected friend insights could not be read from VRCX. "
                "Try again shortly."
            ) from error
        with self._lock:
            self._cache[key] = result
        return result

    def load_friend_map(
        self, state: AppState, max_nodes: int | None = 40
    ) -> FriendMapData:
        signature = self._prepare_cache()
        max_nodes = max(2, max_nodes) if max_nodes is not None else None
        key = (
            "friend-map",
            signature,
            state.start_date,
            state.end_date,
            max_nodes,
        )
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._query_friend_map(state, max_nodes)
        except sqlite3.Error as error:
            LOGGER.exception("Friend map query failed")
            raise VrcxDataError(
                "The friend map could not be read from VRCX. Try again shortly."
            ) from error
        with self._lock:
            self._cache[key] = result
        return result

    def earliest_local_date(self) -> date:
        with closing(open_database(self.database_path)) as connection:
            value = connection.execute(
                "SELECT MIN(created_at) FROM gamelog_join_leave"
            ).fetchone()[0]
        parsed = to_local(parse_utc(value))
        if parsed is None:
            raise VrcxDataError("No activity was found in the database.")
        return parsed.date()

    def _query_dashboard(self, state: AppState) -> DashboardData:
        if state.end_date < state.start_date:
            raise ValueError("The end date must be on or after the start date.")
        range_start, range_end = local_range_utc(state.start_date, state.end_date)
        start_at = sqlite_timestamp(range_start)
        end_at = sqlite_timestamp(range_end)
        with closing(open_database(self.database_path)) as connection:
            friend_table = find_friend_table(connection)
            friend_count = connection.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(friend_table)}"
            ).fetchone()[0]
            latest_value = connection.execute(
                "SELECT MAX(created_at) FROM gamelog_join_leave"
            ).fetchone()[0]
            clipped = """
                MAX(0.0,
                    (MIN(julianday(j.created_at), julianday(:end_at)) -
                     MAX(julianday(j.created_at) - (j.time / 86400000.0),
                         julianday(:start_at))) * 86400000.0)
            """
            query = f"""
                WITH stats AS (
                    SELECT
                        f.user_id,
                        f.display_name,
                        COUNT(*) AS sessions,
                        CAST(ROUND(SUM({clipped})) AS INTEGER) AS milliseconds,
                        CAST(ROUND(AVG({clipped})) AS INTEGER) AS average_ms,
                        CAST(ROUND(MAX({clipped})) AS INTEGER) AS longest_ms,
                        MIN(j.created_at) AS first_seen,
                        MAX(j.created_at) AS last_seen
                    FROM gamelog_join_leave AS j
                    JOIN {quote_identifier(friend_table)} AS f
                      ON f.user_id = j.user_id
                    WHERE j.type = 'OnPlayerLeft'
                      AND j.time > 0
                      AND julianday(j.created_at) > julianday(:start_at)
                      AND julianday(j.created_at) - (j.time / 86400000.0)
                            < julianday(:end_at)
                    GROUP BY f.user_id, f.display_name
                )
                SELECT * FROM stats
                WHERE milliseconds > 0
                  AND milliseconds >= :minimum_ms
                  AND display_name LIKE :friend_search ESCAPE '\\'
                ORDER BY milliseconds DESC, display_name COLLATE NOCASE
            """
            search_pattern = (
                "%"
                + state.search_term.strip()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                + "%"
            )
            rows = list(
                connection.execute(
                    query,
                    {
                        "start_at": start_at,
                        "end_at": end_at,
                        "minimum_ms": max(0, state.minimum_minutes) * 60_000,
                        "friend_search": search_pattern,
                    },
                )
            )

            matching_count = len(rows)
            if state.result_limit is not None:
                rows = rows[: state.result_limit]
            per_friend, social = self._load_local_daily_series(
                connection, state.start_date, state.end_date
            )
            friend_options = tuple(
                sorted(
                    (
                        FriendIdentity(
                            row["user_id"],
                            row["display_name"],
                            sum(
                                value
                                for _day, value in per_friend.get(row["user_id"], ())
                            ),
                        )
                        for row in connection.execute(
                            f"SELECT user_id, display_name FROM "
                            f"{quote_identifier(friend_table)}"
                        )
                    ),
                    key=lambda friend: (
                        -friend.milliseconds,
                        friend.display_name.casefold(),
                    ),
                )
            )
            friends = tuple(
                FriendStat(
                    user_id=row["user_id"],
                    display_name=row["display_name"],
                    sessions=row["sessions"],
                    milliseconds=row["milliseconds"],
                    average_milliseconds=row["average_ms"],
                    longest_milliseconds=row["longest_ms"],
                    active_days=sum(
                        1
                        for _day, value in per_friend.get(row["user_id"], ())
                        if value > 0
                    ),
                    first_seen=to_local(parse_utc(row["first_seen"])),
                    last_seen=to_local(parse_utc(row["last_seen"])),
                )
                for row in rows
            )

        days = [
            state.start_date + timedelta(days=index)
            for index in range((state.end_date - state.start_date).days + 1)
        ]
        person_time = tuple(
            (day, sum(series[index][1] for series in per_friend.values()))
            for index, day in enumerate(days)
        )
        return DashboardData(
            friends=friends,
            friend_options=friend_options,
            matching_count=matching_count,
            latest_activity=to_local(parse_utc(latest_value)),
            current_friend_count=friend_count,
            person_daily=person_time,
            social_daily=tuple(social),
        )

    def _query_friend_insights(
        self, state: AppState, user_id: str
    ) -> FriendInsightsData:
        if state.end_date < state.start_date:
            raise ValueError("The end date must be on or after the start date.")
        days = [
            state.start_date + timedelta(days=index)
            for index in range((state.end_date - state.start_date).days + 1)
        ]
        range_start, range_end = local_range_utc(state.start_date, state.end_date)
        with closing(open_database(self.database_path)) as connection:
            friend_table = find_friend_table(connection)
            identity_row = connection.execute(
                f"SELECT user_id, display_name FROM {quote_identifier(friend_table)} "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if identity_row is None:
                raise VrcxDataError(
                    "The selected person is not in the active current-friends table."
                )
            parameters = {
                "start_at": sqlite_timestamp(range_start),
                "end_at": sqlite_timestamp(range_end),
            }
            rows = connection.execute(
                f"""
                SELECT
                    f.user_id,
                    f.display_name,
                    j.created_at,
                    j.time,
                    COALESCE(j.location, '') AS location
                FROM gamelog_join_leave AS j
                JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
                WHERE j.type = 'OnPlayerLeft'
                  AND j.time > 0
                  AND julianday(j.created_at) > julianday(:start_at)
                  AND julianday(j.created_at) - (j.time / 86400000.0)
                        < julianday(:end_at)
                ORDER BY j.created_at
                """,
                parameters,
            )
            intervals: list[_FriendInterval] = []
            for row in rows:
                event_end = parse_utc(row["created_at"])
                if event_end is None:
                    continue
                event_start = event_end - timedelta(milliseconds=row["time"])
                clipped_start = max(event_start, range_start)
                clipped_end = min(event_end, range_end)
                if clipped_start >= clipped_end:
                    continue
                intervals.append(
                    _FriendInterval(
                        user_id=row["user_id"],
                        display_name=row["display_name"],
                        start=clipped_start,
                        end=clipped_end,
                        location=row["location"],
                    )
                )

        target_intervals = [
            interval for interval in intervals if interval.user_id == user_id
        ]
        daily_totals = {day: 0 for day in days}
        weekday_hourly = [[0 for _hour in range(24)] for _weekday in range(7)]
        weekday_occurrences = [0 for _weekday in range(7)]
        for day in days:
            weekday_occurrences[day.weekday()] += 1
        for interval in target_intervals:
            cursor = interval.start
            while cursor < interval.end:
                local_cursor = cursor.astimezone(LOCAL_TIMEZONE)
                local_day = local_cursor.date()
                next_midnight = datetime.combine(
                    local_day + timedelta(days=1),
                    time.min,
                    tzinfo=LOCAL_TIMEZONE,
                ).astimezone(timezone.utc)
                seconds_into_hour = (
                    local_cursor.minute * 60
                    + local_cursor.second
                    + local_cursor.microsecond / 1_000_000
                )
                next_hour = cursor + timedelta(seconds=3600 - seconds_into_hour)
                segment_end = min(interval.end, next_midnight, next_hour)
                milliseconds = round(
                    (segment_end - cursor).total_seconds() * 1000
                )
                if local_day in daily_totals:
                    daily_totals[local_day] += milliseconds
                    weekday_hourly[local_day.weekday()][local_cursor.hour] += milliseconds
                cursor = segment_end

        by_location: dict[str, list[_FriendInterval]] = {}
        names: dict[str, str] = {}
        for interval in intervals:
            by_location.setdefault(interval.location, []).append(interval)
            names[interval.user_id] = interval.display_name

        context_milliseconds = [0, 0, 0]
        context_encounters = [0, 0, 0]
        co_milliseconds: dict[str, int] = {}
        co_encounters: dict[str, int] = {}
        for target in target_intervals:
            candidates: list[tuple[str, datetime, datetime]] = []
            boundaries = {target.start, target.end}
            for other in by_location.get(target.location, ()):
                if other.user_id == user_id:
                    continue
                overlap_start = max(target.start, other.start)
                overlap_end = min(target.end, other.end)
                if overlap_start >= overlap_end:
                    continue
                candidates.append((other.user_id, overlap_start, overlap_end))
                boundaries.add(overlap_start)
                boundaries.add(overlap_end)

            encounter_context = [0, 0, 0]
            encountered_users: set[str] = set()
            ordered_boundaries = sorted(boundaries)
            for index in range(len(ordered_boundaries) - 1):
                segment_start = ordered_boundaries[index]
                segment_end = ordered_boundaries[index + 1]
                if segment_start >= segment_end:
                    continue
                present = {
                    other_id
                    for other_id, other_start, other_end in candidates
                    if other_start < segment_end and other_end > segment_start
                }
                milliseconds = round(
                    (segment_end - segment_start).total_seconds() * 1000
                )
                category = 0 if not present else 1 if len(present) <= 3 else 2
                context_milliseconds[category] += milliseconds
                encounter_context[category] += milliseconds
                encountered_users.update(present)
                for other_id in present:
                    co_milliseconds[other_id] = (
                        co_milliseconds.get(other_id, 0) + milliseconds
                    )
            if encounter_context:
                dominant = max(
                    range(3), key=lambda category: (encounter_context[category], category)
                )
                context_encounters[dominant] += 1
            for other_id in encountered_users:
                co_encounters[other_id] = co_encounters.get(other_id, 0) + 1

        co_presence = tuple(
            sorted(
                (
                    CoPresenceStat(
                        user_id=other_id,
                        display_name=names.get(other_id, other_id),
                        milliseconds=milliseconds,
                        encounters=co_encounters.get(other_id, 0),
                    )
                    for other_id, milliseconds in co_milliseconds.items()
                ),
                key=lambda stat: (-stat.milliseconds, stat.display_name.casefold()),
            )
        )
        return FriendInsightsData(
            friend=FriendIdentity(
                identity_row["user_id"], identity_row["display_name"]
            ),
            daily=tuple((day, daily_totals[day]) for day in days),
            weekday_hourly_milliseconds=tuple(
                tuple(hours) for hours in weekday_hourly
            ),
            weekday_occurrences=tuple(weekday_occurrences),
            sessions=len(target_intervals),
            context_milliseconds=tuple(context_milliseconds),
            context_encounters=tuple(context_encounters),
            co_presence=co_presence,
        )

    def _query_friend_map(
        self, state: AppState, max_nodes: int | None
    ) -> FriendMapData:
        if state.end_date < state.start_date:
            raise ValueError("The end date must be on or after the start date.")
        range_start, range_end = local_range_utc(state.start_date, state.end_date)
        with closing(open_database(self.database_path)) as connection:
            friend_table = find_friend_table(connection)
            parameters: dict[str, object] = {
                "start_at": sqlite_timestamp(range_start),
                "end_at": sqlite_timestamp(range_end),
            }
            limit_clause = ""
            if max_nodes is not None:
                parameters["max_nodes"] = max_nodes
                limit_clause = "LIMIT :max_nodes"
            clipped = """
                MAX(0.0,
                    (MIN(julianday(j.created_at), julianday(:end_at)) -
                     MAX(julianday(j.created_at) - (j.time / 86400000.0),
                         julianday(:start_at))) * 86400000.0)
            """
            stats = list(
                connection.execute(
                    f"""
                    SELECT
                        f.user_id,
                        f.display_name,
                        COUNT(*) AS sessions,
                        CAST(ROUND(SUM({clipped})) AS INTEGER) AS milliseconds
                    FROM gamelog_join_leave AS j
                    JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
                    WHERE j.type = 'OnPlayerLeft'
                      AND j.time > 0
                      AND julianday(j.created_at) > julianday(:start_at)
                      AND julianday(j.created_at) - (j.time / 86400000.0)
                            < julianday(:end_at)
                    GROUP BY f.user_id, f.display_name
                    HAVING milliseconds > 0
                    ORDER BY milliseconds DESC, f.display_name COLLATE NOCASE
                    {limit_clause}
                    """,
                    parameters,
                )
            )
            if not stats:
                return FriendMapData(tuple(), tuple())
            selected_user_clause = ""
            if max_nodes is not None:
                placeholders: list[str] = []
                for index, stat in enumerate(stats):
                    key = f"map_user_{index}"
                    parameters[key] = stat["user_id"]
                    placeholders.append(f":{key}")
                selected_user_clause = (
                    f"AND f.user_id IN ({', '.join(placeholders)})"
                )
            rows = list(
                connection.execute(
                    f"""
                    SELECT
                        f.user_id,
                        f.display_name,
                        j.created_at,
                        j.time,
                        COALESCE(j.location, '') AS location
                    FROM gamelog_join_leave AS j
                    JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
                    WHERE j.type = 'OnPlayerLeft'
                      AND j.time > 0
                      AND julianday(j.created_at) > julianday(:start_at)
                      AND julianday(j.created_at) - (j.time / 86400000.0)
                            < julianday(:end_at)
                      {selected_user_clause}
                    ORDER BY j.created_at
                    """,
                    parameters,
                )
            )

            friendship_dates: dict[str, datetime] = {}
            introduction_rows: list[sqlite3.Row] = []
            history_table = find_friend_history_table(connection, friend_table)
            introduction_parameters: dict[str, object] = {}
            introduction_placeholders: list[str] = []
            for index, stat in enumerate(stats):
                key = f"intro_user_{index}"
                introduction_parameters[key] = stat["user_id"]
                introduction_placeholders.append(f":{key}")
            if history_table is not None:
                history_rows = connection.execute(
                    f"""
                    SELECT user_id, MAX(created_at) AS befriended_at
                    FROM {quote_identifier(history_table)}
                    WHERE type = 'Friend'
                      AND user_id IN ({', '.join(introduction_placeholders)})
                    GROUP BY user_id
                    """,
                    introduction_parameters,
                )
                friendship_dates = {
                    row["user_id"]: parsed
                    for row in history_rows
                    if (parsed := parse_utc(row["befriended_at"])) is not None
                }
            if friendship_dates:
                introduction_parameters["intro_start"] = sqlite_timestamp(
                    min(friendship_dates.values()) - timedelta(days=1)
                )
                introduction_parameters["intro_end"] = sqlite_timestamp(
                    max(friendship_dates.values()) + timedelta(days=1)
                )
                introduction_rows = list(
                    connection.execute(
                        f"""
                        SELECT j.user_id, j.created_at, j.time,
                               COALESCE(j.location, '') AS location
                        FROM gamelog_join_leave AS j
                        WHERE j.type = 'OnPlayerLeft'
                          AND j.time > 0
                          AND j.user_id IN ({', '.join(introduction_placeholders)})
                          AND julianday(j.created_at) > julianday(:intro_start)
                          AND julianday(j.created_at) - (j.time / 86400000.0)
                                < julianday(:intro_end)
                        ORDER BY j.created_at
                        """,
                        introduction_parameters,
                    )
                )

        intervals: list[_FriendInterval] = []
        names = {row["user_id"]: row["display_name"] for row in stats}
        for row in rows:
            event_end = parse_utc(row["created_at"])
            if event_end is None:
                continue
            event_start = event_end - timedelta(milliseconds=row["time"])
            clipped_start = max(event_start, range_start)
            clipped_end = min(event_end, range_end)
            if clipped_start >= clipped_end:
                continue
            intervals.append(
                _FriendInterval(
                    user_id=row["user_id"],
                    display_name=row["display_name"],
                    start=clipped_start,
                    end=clipped_end,
                    location=row["location"],
                )
            )

        nodes = tuple(
            FriendMapNode(
                user_id=row["user_id"],
                display_name=row["display_name"],
                milliseconds=row["milliseconds"],
                sessions=row["sessions"],
            )
            for row in stats
        )

        grouped: dict[str, dict[str, list[tuple[datetime, datetime]]]] = {}
        for interval in intervals:
            if not interval.location:
                continue
            grouped.setdefault(interval.location, {}).setdefault(
                interval.user_id, []
            ).append((interval.start, interval.end))

        overlap_totals: dict[tuple[str, str], int] = {}
        overlap_encounters: dict[tuple[str, str], int] = {}
        for users in grouped.values():
            merged_by_user = {
                user_id: self._merge_intervals(user_intervals)
                for user_id, user_intervals in users.items()
            }
            for first_id, second_id in combinations(sorted(merged_by_user), 2):
                milliseconds, encounters = self._measure_overlap(
                    merged_by_user[first_id], merged_by_user[second_id]
                )
                if milliseconds <= 0:
                    continue
                key = (first_id, second_id)
                overlap_totals[key] = overlap_totals.get(key, 0) + milliseconds
                overlap_encounters[key] = overlap_encounters.get(key, 0) + encounters

        node_milliseconds = {node.user_id: node.milliseconds for node in nodes}
        links = tuple(
            sorted(
                (
                    FriendMapLink(
                        source_user_id=source_id,
                        target_user_id=target_id,
                        milliseconds=milliseconds,
                        encounters=overlap_encounters[(source_id, target_id)],
                        likelihood=min(
                            1.0,
                            milliseconds
                            / min(
                                node_milliseconds[source_id],
                                node_milliseconds[target_id],
                            ),
                        ),
                    )
                    for (source_id, target_id), milliseconds in overlap_totals.items()
                ),
                key=lambda link: (
                    -link.milliseconds,
                    names[link.source_user_id].casefold(),
                    names[link.target_user_id].casefold(),
                ),
            )
        )
        introduction_sessions: list[IntroductionSession] = []
        for row in introduction_rows:
            session_end = parse_utc(row["created_at"])
            if session_end is None:
                continue
            introduction_sessions.append(
                IntroductionSession(
                    user_id=row["user_id"],
                    start=session_end - timedelta(milliseconds=row["time"]),
                    end=session_end,
                    location=row["location"],
                )
            )
        introductions = infer_introductions(
            tuple(node.user_id for node in nodes),
            friendship_dates,
            tuple(introduction_sessions),
        )
        return FriendMapData(
            nodes=nodes,
            links=links,
            introductions=introductions,
        )

    @staticmethod
    def _merge_intervals(
        intervals: list[tuple[datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        merged: list[tuple[datetime, datetime]] = []
        for start, end in sorted(intervals):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
                continue
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        return merged

    @staticmethod
    def _measure_overlap(
        first: list[tuple[datetime, datetime]],
        second: list[tuple[datetime, datetime]],
    ) -> tuple[int, int]:
        milliseconds = 0
        encounters = 0
        first_index = second_index = 0
        while first_index < len(first) and second_index < len(second):
            first_start, first_end = first[first_index]
            second_start, second_end = second[second_index]
            overlap_start = max(first_start, second_start)
            overlap_end = min(first_end, second_end)
            if overlap_start < overlap_end:
                milliseconds += round(
                    (overlap_end - overlap_start).total_seconds() * 1000
                )
                encounters += 1
            if first_end <= second_end:
                first_index += 1
            else:
                second_index += 1
        return milliseconds, encounters

    def _load_local_daily_series(
        self,
        connection: sqlite3.Connection,
        start_date: date,
        end_date: date,
        user_ids: list[str] | None = None,
    ) -> tuple[dict[str, list[tuple[date, int]]], list[tuple[date, int]]]:
        days = [
            start_date + timedelta(days=index)
            for index in range((end_date - start_date).days + 1)
        ]
        range_start, range_end = local_range_utc(start_date, end_date)
        requested_ids = list(dict.fromkeys(user_ids or []))
        totals = {
            user_id: {day: 0 for day in days} for user_id in requested_ids
        }
        social_intervals: dict[date, list[tuple[datetime, datetime]]] = {
            day: [] for day in days
        }
        friend_table = find_friend_table(connection)
        parameters: dict[str, object] = {
            "start_at": sqlite_timestamp(range_start),
            "end_at": sqlite_timestamp(range_end),
        }
        user_filter = ""
        if requested_ids:
            placeholders = []
            for index, user_id in enumerate(requested_ids):
                key = f"user_{index}"
                placeholders.append(f":{key}")
                parameters[key] = user_id
            user_filter = f"AND f.user_id IN ({', '.join(placeholders)})"
        query = f"""
            SELECT f.user_id, j.created_at, j.time
            FROM gamelog_join_leave AS j
            JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
            WHERE j.type = 'OnPlayerLeft'
              AND j.time > 0
              AND julianday(j.created_at) > julianday(:start_at)
              AND julianday(j.created_at) - (j.time / 86400000.0)
                    < julianday(:end_at)
              {user_filter}
            ORDER BY j.created_at
        """
        for row in connection.execute(query, parameters):
            user_id = row["user_id"]
            totals.setdefault(user_id, {day: 0 for day in days})
            event_end = parse_utc(row["created_at"])
            if event_end is None:
                continue
            event_start = event_end - timedelta(milliseconds=row["time"])
            cursor = max(event_start, range_start)
            clipped_end = min(event_end, range_end)
            while cursor < clipped_end:
                local_cursor = cursor.astimezone(LOCAL_TIMEZONE)
                day = local_cursor.date()
                next_midnight = datetime.combine(
                    day + timedelta(days=1), time.min, tzinfo=LOCAL_TIMEZONE
                ).astimezone(timezone.utc)
                segment_end = min(clipped_end, next_midnight)
                milliseconds = round((segment_end - cursor).total_seconds() * 1000)
                totals[user_id][day] += milliseconds
                social_intervals[day].append((cursor, segment_end))
                cursor = segment_end

        per_friend = {
            user_id: [(day, daily[day]) for day in days]
            for user_id, daily in totals.items()
        }
        social: list[tuple[date, int]] = []
        for day in days:
            merged = 0
            current_start: datetime | None = None
            current_end: datetime | None = None
            for interval_start, interval_end in sorted(social_intervals[day]):
                if current_end is None or interval_start > current_end:
                    if current_start is not None and current_end is not None:
                        merged += round(
                            (current_end - current_start).total_seconds() * 1000
                        )
                    current_start, current_end = interval_start, interval_end
                elif interval_end > current_end:
                    current_end = interval_end
            if current_start is not None and current_end is not None:
                merged += round((current_end - current_start).total_seconds() * 1000)
            social.append((day, merged))
        return per_friend, social
