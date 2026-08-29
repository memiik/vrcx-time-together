"""Create a tiny VRCX-compatible database for packaged executable checks."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def create_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE gamelog_join_leave (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                type TEXT NOT NULL,
                display_name TEXT,
                location TEXT,
                user_id TEXT,
                time INTEGER
            );
            CREATE TABLE usr_release_friend_log_current (
                user_id TEXT PRIMARY KEY,
                display_name TEXT,
                trust_level TEXT,
                friend_number INTEGER
            );
            INSERT INTO usr_release_friend_log_current VALUES
                ('usr_smoke', 'Release Smoke Friend', '', 1);
            """
        )
        event_end = datetime.now(timezone.utc).replace(microsecond=0)
        duration = timedelta(minutes=45)
        connection.execute(
            """
            INSERT INTO gamelog_join_leave
                (created_at, type, display_name, location, user_id, time)
            VALUES (?, 'OnPlayerLeft', 'Release Smoke Friend', '', 'usr_smoke', ?)
            """,
            (
                event_end.isoformat().replace("+00:00", "Z"),
                round(duration.total_seconds() * 1000),
            ),
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: create_smoke_database.py <output.sqlite3>")
    create_database(Path(sys.argv[1]))

