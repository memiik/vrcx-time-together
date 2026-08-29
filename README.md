# VRCX Time Together

VRCX Time Together is a private, native Windows dashboard for exploring the
time you have shared with VRChat friends. It reads the local VRCX history in
strict read-only mode; it does not modify VRCX, use telemetry, or send data to
remote services.

## Run

Install the native UI and timezone dependencies once:

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
Use **Change database** in the sidebar to select a database stored elsewhere.
The validated path is remembered for future launches; **Use automatic path**
returns to the lookup order above.

## Use the prebuilt Windows artifact

No Python installation is needed for the packaged application.

1. Open the repository's **Actions** tab and select the completed
   **Windows build and release** run.
2. Under **Artifacts**, download `VRCX-Time-Together-windows-x64`.
3. Extract the downloaded artifact ZIP. It contains both a standalone `.exe`
   and a portable application ZIP, plus SHA-256 checksums for each.
4. Either run `VRCX-Time-Together-1.0.0-<commit>-windows-x64.exe` directly, or
   extract the portable ZIP to a permanent folder and run
   `VRCX Time Together.exe`. Keep the portable build's `_internal` directory
   beside its executable.

The app automatically reads `%APPDATA%\VRCX\VRCX.sqlite3`. If VRCX is installed
elsewhere or you keep a copied database, use **Change database** in the app and
select its `VRCX.sqlite3` file. The database is always opened read-only.

For tagged releases, download either the standalone `.exe` or portable ZIP
directly from the repository's **Releases** page. The standalone build is the
easiest option; the portable build starts faster after extraction.

## Features

- Local-time date ranges with Today, 7/30/90 days, this month, last month,
  this year, all-time, and custom presets in an English-only range picker.
- A polished native Qt dashboard with responsive metric cards, background
  loading states, persistent window geometry, and a compact navigation rail.
- An inline English local-date tray with quick presets and exact date controls.
- Period-aware KPIs for social time, friends, sessions, peak day, and top friend.
- Interactive social-time and person-time trends with daily, weekly, and
  monthly views.
- Searchable friend analytics with typed numeric sorting for total time,
  sessions, average session, longest session, active days, and last seen.
- Selectable friend insights with calendar and weekday/hour heatmaps, a
  known-friend company-size breakdown, and ranked co-presence percentages by
  overlapping time or encounters.
- Multi-friend comparison using stable dark-theme colors, shared hover details,
  daily/weekly/monthly aggregation, and period or cumulative totals.
- Mouse-wheel chart zoom, drag-to-pan, and one-click view reset.
- Background database loading, lightweight result caching, automatic cache
  invalidation when VRCX changes, and explicit manual refresh.
- Local calendar-day splitting with daylight-saving-aware timezone conversion.

Select a friend row and choose **Open friend insights** to inspect their
patterns. Double-click a friend row to start a comparison, or check several
people on the Compare page. `Ctrl+F` focuses the current search, `Escape` clears
contextual input, `F5` forces a database refresh, and `Ctrl+1` through `Ctrl+4`
switch pages.

## Metrics

- **Social time** is wall-clock time with at least one current friend. Overlaps
  are merged, so one hour with three friends is one hour of social time.
- **Person-time** sums time per friend, so that same hour contributes three
  person-hours.
- Session statistics use completed `OnPlayerLeft` records, clipped to the
  selected local-time date range.
- Friend Insights counts another current friend as co-present only where their
  reconstructed sessions overlap in time and have the same recorded location.
  Company-size labels describe **known current friends present**, not the total
  number of all users in the VRChat instance.

Only people in the active account's current-friends table are included, which
matches VRCX's original top-friends behavior.

## Architecture

The `.pyw` file remains the directly launchable native Windows entry point.
The interface uses PySide6 (Qt 6) and PyQtGraph; the data layer remains
framework-independent inside the `vrc_time_together` package:

- `qt_app.py` — native application shell, pages, models, and background workers
- `qt_chart.py` — accelerated interactive time-series visualization
- `qt_insights.py` — calendar, weekly rhythm, company-context, and co-presence
  visualizations
- `qt_theme.py` — Qt palette, component styling, and chart colors
- `repository.py` — cached read-only queries and session aggregation
- `models.py` — typed application state and result models
- `timezone_utils.py` — aware UTC/local conversions and range boundaries
- `formatting.py` — centralized duration and human-readable date formatting
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

## Build the Windows application

Windows releases are frozen with the committed PyInstaller specification and
the exact dependency versions in `requirements-build.txt`. The result contains
Python, Qt, PyQtGraph, and timezone data; users do not need Python installed.

Build both packages locally with Python 3.13.11:

```powershell
py -3.13 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r .\requirements-build.txt
.\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean .\packaging\windows\VRCX-Time-Together.spec
.\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean .\packaging\windows\VRCX-Time-Together-OneFile.spec
```

The portable executable is created at
`dist\VRCX Time Together\VRCX Time Together.exe`; its entire folder must be
distributed together. The standalone build is created at
`dist\VRCX Time Together Standalone.exe` and can be distributed by itself.

The `Windows build and release` GitHub Actions workflow can also be run
manually. Pushing a tag matching the application version builds and tests both
packages, creates SHA-256 checksums, and publishes them to a GitHub Release:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Before creating a later release, update both `vrc_time_together.__version__`
and `packaging/windows/version_info.txt` to the same version. The workflow
rejects mismatched release tags.
