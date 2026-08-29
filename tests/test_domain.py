from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from vrc_time_together.formatting import (
    format_duration,
    format_english_date,
    format_english_day,
)
from vrc_time_together.models import AppState
from vrc_time_together.repository import (
    VrcxRepository,
    aggregate_time_series,
    open_database,
)
from vrc_time_together.timezone_utils import (
    LOCAL_TIMEZONE,
    local_range_utc,
    sqlite_timestamp,
)


class FormattingTests(unittest.TestCase):
    def test_duration_scale(self) -> None:
        self.assertEqual(format_duration(42_000), "42s")
        self.assertEqual(format_duration(8 * 60_000), "8m")
        self.assertEqual(format_duration((84 * 60 + 24) * 60_000), "3d 12h")

    def test_calendar_labels_are_deterministically_english(self) -> None:
        value = date(2026, 8, 2)
        self.assertEqual(format_english_date(value), "02 August 2026")
        self.assertEqual(format_english_day(value, include_year=True), "Sunday, 02 Aug 2026")

    def test_weekly_aggregation_preserves_totals(self) -> None:
        daily = [
            (date(2026, 8, 24) + timedelta(days=index), (index + 1) * 1_000)
            for index in range(9)
        ]
        weekly = aggregate_time_series(daily, "Weekly")
        self.assertEqual(len(weekly), 2)
        self.assertEqual(sum(value for _day, value in weekly), 45_000)


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "VRCX.sqlite3"
        self.local_day = date(2026, 8, 29)
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            CREATE TABLE gamelog_join_leave (
                id INTEGER PRIMARY KEY,
                created_at TEXT,
                type TEXT,
                display_name TEXT,
                location TEXT,
                user_id TEXT,
                time INTEGER
            );
            CREATE TABLE usr_test_friend_log_current (
                user_id TEXT PRIMARY KEY,
                display_name TEXT,
                trust_level TEXT,
                friend_number INTEGER
            );
            INSERT INTO usr_test_friend_log_current VALUES
                ('usr_a', 'Alpha', '', 1),
                ('usr_b', 'Beta', '', 2);
            """
        )
        sessions = (
            ("usr_a", "Alpha", 10, 12),
            ("usr_b", "Beta", 11, 13),
        )
        for index, (user_id, name, start_hour, end_hour) in enumerate(sessions, 1):
            local_end = datetime.combine(
                self.local_day,
                datetime.min.time(),
                tzinfo=LOCAL_TIMEZONE,
            ).replace(hour=end_hour)
            duration = (end_hour - start_hour) * 3_600_000
            connection.execute(
                "INSERT INTO gamelog_join_leave VALUES (?, ?, 'OnPlayerLeft', ?, '', ?, ?)",
                (index, sqlite_timestamp(local_end), name, user_id, duration),
            )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_database_connection_is_read_only(self) -> None:
        with closing(open_database(self.database_path)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden(value INTEGER)")

    def test_social_time_merges_overlapping_friend_sessions(self) -> None:
        data = VrcxRepository(self.database_path).load_dashboard(
            AppState(self.local_day, self.local_day)
        )
        self.assertEqual(data.total_person_milliseconds, 4 * 3_600_000)
        self.assertEqual(data.total_social_milliseconds, 3 * 3_600_000)
        self.assertEqual(len(data.friends), 2)
        self.assertEqual(data.friends[0].longest_milliseconds, 2 * 3_600_000)

    def test_local_day_boundaries_cover_one_real_calendar_day(self) -> None:
        start, end = local_range_utc(self.local_day, self.local_day)
        self.assertEqual((end - start).total_seconds(), 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
