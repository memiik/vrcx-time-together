# VRCX Time Together

VRCX Time Together is a private, native Windows dashboard for exploring the
time you have shared with VRChat friends. It reads the local VRCX history in
strict read-only mode; it does not modify VRCX, use telemetry, or send data to
remote services.

## Run

Install the small timezone dependency once:

```powershell
py -3 -m pip install -r .\requirements.txt
```

Then double-click `vrc-time-together.pyw`, or run:

```powershell
py -3 .\vrc-time-together.pyw
```

The app first looks for `VRCX.sqlite3` beside the script, then uses
`%APPDATA%\VRCX\VRCX.sqlite3`. Connections use SQLite URI read-only mode plus
`PRAGMA query_only`; writes, schema changes, and migrations are not permitted.

## Features

- Local-time date ranges with Today, 7/30/90 days, this month, last month,
  this year, all-time, and custom presets.
- Social-time, friends-in-range, most-social-day, and top-friend overview cards.
- Searchable friend analytics with numeric sorting for total time, sessions,
  average session, longest session, active days, and last seen.
- Multi-friend comparison using stable dark-theme colors, shared hover details,
  daily/weekly/monthly aggregation, and period or cumulative totals.
- Mouse-wheel chart zoom, drag-to-pan, double-click or **Reset zoom** to reset.
- Background database loading, lightweight result caching, automatic cache
  invalidation when VRCX changes, and explicit manual refresh.
- Local calendar-day splitting with daylight-saving-aware timezone conversion.

Ctrl-click friend rows to compare several people. `Ctrl+F` focuses search,
`Escape` clears list filters, `Enter` refreshes, and `F5` forces a database
refresh.

## Metrics

- **Social time** is wall-clock time with at least one current friend. Overlaps
  are merged, so one hour with three friends is one hour of social time.
- **Person-time** sums time per friend, so that same hour contributes three
  person-hours.
- Session statistics use completed `OnPlayerLeft` records, clipped to the
  selected local-time date range.

Only people in the active account's current-friends table are included, which
matches VRCX's original top-friends behavior.

## Architecture

The `.pyw` file remains the directly launchable native Tkinter UI entry point.
Domain logic is separated into the `vrc_time_together` package:

- `repository.py` — cached read-only queries and session aggregation
- `models.py` — typed application state and result models
- `timezone_utils.py` — aware UTC/local conversions and range boundaries
- `formatting.py` — centralized duration and human-readable date formatting
- `theme.py` — shared dark-theme colors and typography
- `logging_utils.py` — rotating local diagnostic log configuration

Technical logs are stored under `%LOCALAPPDATA%\VRCX Time Together` and do not
record friend-history data.

## Validate and test

Validate the live database without opening the UI:

```powershell
py -3 .\vrc-time-together.pyw --check
```

Run the focused domain tests:

```powershell
py -3 -m unittest discover -s tests -v
```
