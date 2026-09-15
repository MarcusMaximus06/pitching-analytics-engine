# NFL game automation

`daily_nfl_auto.py` is the primary unattended NFL logger and grader. It runs
without Streamlit and follows this order:

1. Open `NFL Prediction Model` → `NFL Log V2`.
2. Grade only officially final ESPN games currently marked `PENDING`.
3. Load the current regular-season ESPN schedule and result-updated Elo state.
4. Attach optional no-vig, multi-book odds.
5. Log every modelable pregame matchup in the next seven days once without overwriting its opening snapshot.

Run it directly with the repository environment:

```powershell
cd C:\HagLabs\pitching-analytics-engine
.\.venv\Scripts\python.exe daily_nfl_auto.py
```

Use a dry run for bounded schedule/model diagnostics:

```powershell
.\.venv\Scripts\python.exe daily_nfl_auto.py --date 2026-09-14 --dry-run
```

Use `--lookahead-days 0` for a single date or another non-negative value for a
different inclusive future window. Probability values are stored with explicit
percent signs. On the website, completed games also have a separately labeled
walk-forward Elo baseline; reconstructed rows are never counted as official
tracked picks.

The scheduled launcher is `run_daily_nfl_auto.bat`; output is appended to
`haglabs_data\daily_nfl_auto.log`. Existing pregame model and market columns
are immutable. Grading updates only the actual winner, model/market results,
and status. Stable ESPN event IDs are preferred, with an exact date/away/home
fallback for legacy rows.
