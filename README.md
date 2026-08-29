# VRCX Friendship Analytics

This project is an enhanced, standalone version of
`%APPDATA%\VRCX\vrcx_top_friends.pyw`. The original file is not modified.

## Run

Double-click `vrc-time-together.pyw`, or run from this directory:

```powershell
py -3 .\vrc-time-together.pyw
```

The copy first looks for `VRCX.sqlite3` beside the script, then automatically
uses `%APPDATA%\VRCX\VRCX.sqlite3`. The database is always opened read-only.

To validate the database and queries without opening the UI:

```powershell
py -3 .\vrc-time-together.pyw --check
```

## Metrics

- **Total person-time**: sum of time spent with all current friends. Time with
  multiple friends at once is intentionally counted once per person.
- **Daily average**: total person-time divided by all calendar days in the range,
  including inactive days.
- **Peak day / quietest day**: highest and lowest UTC daily person-time.
- **Daily chart**: hover for exact values. Sessions crossing UTC midnight are
  split across their correct dates.

Rankings, encounter counts, and metrics only include people who are in the
active account's current-friends table. Former friends are not included, which
matches the behavior of the source script.
