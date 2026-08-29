# VRCX Time Together

This project is an enhanced, standalone version of
`%APPDATA%\VRCX\vrcx_top_friends.pyw`. The original file is not modified.

## Run

Double-click `vrc-time-together.pyw`, or run from this directory:

```powershell
py -3 .\vrc-time-together.pyw
```

The copy first looks for `VRCX.sqlite3` beside the script, then automatically
uses `%APPDATA%\VRCX\VRCX.sqlite3`. The database is always opened read-only.

Use **Find a friend** to narrow the list by display name. **Minimum time**
removes brief shared sessions from the list, and **Show** controls how many
matching friends are displayed. These list filters do not change the four
overview metrics or daily chart, which continue to summarize all current
friends in the selected date range.

Select a row in the friend list to focus the time-series chart on that
person. Ctrl-click additional rows to compare multiple friends as separate
colored lines with shared hover values. The chart can group time by day, week,
or month. In the all-friends view, switch between **Time with friends** and
**Person-time**; use **All friends** to clear the comparison.

To validate the database and queries without opening the UI:

```powershell
py -3 .\vrc-time-together.pyw --check
```

## Metrics

- **Total person-time**: sum of time spent with all current friends. Time with
  multiple friends at once is intentionally counted once per person.
- **Time with friends**: actual wall-clock time when at least one current friend
  was present. Overlapping sessions are merged, so an hour with three friends
  is one hour of social time rather than three person-hours.
- **Daily average**: total person-time divided by all calendar days in the range,
  including inactive days.
- **Peak day / quietest day**: highest and lowest UTC daily person-time.
- **Time-series chart**: compare social time or person-time using daily, weekly,
  and monthly totals, then hover for exact values. Date ranges, day boundaries,
  labels, and session splitting use the computer's local timezone, including
  daylight-saving changes.

The friend list, shared-session counts, and metrics only include people in the
active account's current-friends table. Former friends are not included, which
matches the behavior of the source script.
