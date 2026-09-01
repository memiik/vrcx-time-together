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
from vrc_time_together.friend_groups import detect_friend_groups
from vrc_time_together.models import AppState, FriendMapLink, FriendMapNode
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


class FriendGroupTests(unittest.TestCase):
    def test_detects_two_strong_same_instance_communities(self) -> None:
        nodes = tuple(
            FriendMapNode(user_id, user_id.upper(), 3_600_000, 4)
            for user_id in ("a", "b", "c", "d")
        )
        links = (
            FriendMapLink("a", "b", 3_000_000, 5, 0.8),
            FriendMapLink("c", "d", 2_800_000, 5, 0.75),
            FriendMapLink("b", "c", 60_000, 1, 0.02),
        )

        groups = detect_friend_groups(nodes, links)

        self.assertEqual(groups["a"], groups["b"])
        self.assertEqual(groups["c"], groups["d"])
        self.assertNotEqual(groups["a"], groups["c"])

    def test_leaves_unmeasured_people_ungrouped(self) -> None:
        nodes = (
            FriendMapNode("a", "Alpha", 3_600_000, 2),
            FriendMapNode("b", "Beta", 1_800_000, 1),
        )

        self.assertEqual(detect_friend_groups(nodes, tuple()), {"a": 0, "b": 0})


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

    def test_friend_insights_split_time_and_measure_co_presence(self) -> None:
        local_end = datetime.combine(
            self.local_day,
            datetime.min.time(),
            tzinfo=LOCAL_TIMEZONE,
        ).replace(hour=12)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO usr_test_friend_log_current VALUES (?, ?, '', ?)",
                ("usr_c", "Gamma", 3),
            )
            connection.execute(
                "INSERT INTO gamelog_join_leave VALUES "
                "(3, ?, 'OnPlayerLeft', 'Gamma', 'wrld_other:1', 'usr_c', ?)",
                (sqlite_timestamp(local_end), 60 * 60_000),
            )
            connection.commit()
        data = VrcxRepository(self.database_path).load_friend_insights(
            AppState(self.local_day, self.local_day), "usr_a"
        )

        self.assertEqual(data.total_milliseconds, 2 * 3_600_000)
        self.assertEqual(data.sessions, 1)
        self.assertEqual(data.active_days, 1)
        self.assertEqual(
            data.weekday_hourly_milliseconds[self.local_day.weekday()][10],
            3_600_000,
        )
        self.assertEqual(
            data.weekday_hourly_milliseconds[self.local_day.weekday()][11],
            3_600_000,
        )
        self.assertEqual(
            data.context_milliseconds,
            (3_600_000, 3_600_000, 0),
        )
        self.assertEqual(data.context_encounters, (0, 1, 0))
        self.assertEqual(len(data.co_presence), 1)
        self.assertEqual(data.co_presence[0].user_id, "usr_b")
        self.assertEqual(data.co_presence[0].milliseconds, 3_600_000)
        self.assertEqual(data.co_presence[0].encounters, 1)

    def test_friend_picker_catalog_is_not_limited_by_activity_filters(self) -> None:
        beta_end = datetime.combine(
            self.local_day,
            datetime.min.time(),
            tzinfo=LOCAL_TIMEZONE,
        ).replace(hour=15)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO usr_test_friend_log_current VALUES (?, ?, '', ?)",
                ("usr_inactive", "Inactive", 3),
            )
            connection.execute(
                "INSERT INTO gamelog_join_leave VALUES "
                "(3, ?, 'OnPlayerLeft', 'Beta', '', 'usr_b', ?)",
                (sqlite_timestamp(beta_end), 60 * 60_000),
            )
            connection.commit()
        data = VrcxRepository(self.database_path).load_dashboard(
            AppState(
                self.local_day,
                self.local_day,
                search_term="Alpha",
                result_limit=1,
            )
        )
        self.assertEqual([friend.display_name for friend in data.friends], ["Alpha"])
        self.assertEqual(
            [friend.display_name for friend in data.friend_options],
            ["Beta", "Alpha", "Inactive"],
        )

    def test_friend_map_ranks_presence_and_measures_same_instance_overlap(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE gamelog_join_leave SET location = 'wrld_shared:1'"
            )
            connection.commit()
        data = VrcxRepository(self.database_path).load_friend_map(
            AppState(self.local_day, self.local_day)
        )

        self.assertEqual([node.display_name for node in data.nodes], ["Alpha", "Beta"])
        self.assertEqual(data.nodes[0].milliseconds, 2 * 3_600_000)
        self.assertEqual(len(data.links), 1)
        self.assertEqual(data.links[0].milliseconds, 3_600_000)
        self.assertEqual(data.links[0].encounters, 1)
        self.assertEqual(data.links[0].likelihood, 0.5)

    def test_friend_map_does_not_infer_connections_without_location(self) -> None:
        data = VrcxRepository(self.database_path).load_friend_map(
            AppState(self.local_day, self.local_day)
        )
        self.assertEqual(len(data.nodes), 2)
        self.assertEqual(data.links, tuple())

    def test_friend_map_can_load_every_active_friend_without_a_limit(self) -> None:
        local_end = datetime.combine(
            self.local_day,
            datetime.min.time(),
            tzinfo=LOCAL_TIMEZONE,
        ).replace(hour=16)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO usr_test_friend_log_current VALUES (?, ?, '', ?)",
                ("usr_c", "Gamma", 3),
            )
            connection.execute(
                "INSERT INTO gamelog_join_leave VALUES "
                "(3, ?, 'OnPlayerLeft', 'Gamma', '', 'usr_c', ?)",
                (sqlite_timestamp(local_end), 60 * 60_000),
            )
            connection.commit()

        repository = VrcxRepository(self.database_path)
        state = AppState(self.local_day, self.local_day)
        self.assertEqual(len(repository.load_friend_map(state, max_nodes=2).nodes), 2)
        self.assertEqual(len(repository.load_friend_map(state, max_nodes=None).nodes), 3)

    def test_local_day_boundaries_cover_one_real_calendar_day(self) -> None:
        start, end = local_range_utc(self.local_day, self.local_day)
        self.assertEqual((end - start).total_seconds(), 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
