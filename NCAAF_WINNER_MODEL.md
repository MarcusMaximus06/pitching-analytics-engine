# HagLabs NCAA Football Winner Engine

The NCAA Football Winner Intelligence Lab predicts game winners and calibrated
win probabilities. It does not project individual player statistics and it does
not describe a sportsbook disagreement as model confidence.

## Data sources

The historical builder uses the official CollegeFootballData REST API for:

- games, scores, sites, and stable game/team identifiers;
- garbage-time-filtered advanced game statistics and havoc;
- returning production, roster talent, and coach history;
- venue coordinates/elevation and game weather;
- historical moneylines for a market benchmark.

The live board uses The Odds API only for current multi-book moneylines. Each
book is de-vigged independently before the engine takes the median consensus.
Keys must be supplied through `CFBD_API_KEY` and `ODDS_API_KEY` (or
`THE_ODDS_API_KEY`) environment/deployment secrets. Never commit them.

## Model

Every historical row is a feature snapshot created before the game updates team
state. The independent model includes chronological Elo, exponentially weighted
scoring and efficiency form, PPA, defensive success, explosiveness, havoc, rest,
talent, returning production, coaching continuity, neutral/home site, travel,
elevation, and weather.

The model is an L2-regularized logistic winner model with held-out probability
calibration. A market-aware probability is displayed separately, using a
validation-selected blend anchored primarily to the consensus market. Market
odds are never used to define the independent model's confidence.

Version 1.1 corrects final-model calibration by learning the mapping from raw
rolling-origin logits rather than applying a second calibration to probabilities
that were already calibrated. It also keeps the independent **HagLabs Pick** as
the official prediction while the market ensemble remains unvalidated. The
market-aware lean is displayed as a diagnostic comparison, not disguised as the
proprietary forecast.

## Current-season state bridge

When `CFBD_API_KEY` is unavailable, the live engine no longer remains frozen at
the shipped preseason state. It reads completed games from ESPN's public FBS
scoreboard, de-duplicates stable game IDs, aligns team aliases, and applies each
result chronologically to a fresh copy of the preseason ratings. This updates
Elo, scoring margin, offense, defense, recent results, and rest before producing
the current slate.

The credential-free bridge does not invent unavailable inputs. Roster talent,
returning production, coaching, venue, weather, and advanced play efficiency
still require CFBD and are explicitly reported as missing on the live page.

The artifact receives a football `validated` flag only when at least 500
rolling-origin predictions beat the chronological Elo baseline on both Brier
score and log loss. A separate `market_validated` flag requires at least 1,000
held-out market games and an ensemble that beats the historical market on both
metrics. Actionable edge badges require both gates.

## Build the historical model

From PowerShell:

```powershell
cd C:\HagLabs\pitching-analytics-engine
$env:CFBD_API_KEY='<configured locally>'
.\.venv\Scripts\python.exe scripts\build_ncaaf_model.py --start-year 2014 --end-year 2025
```

Without a CFBD key, use the credential-free official SportsDataverse release
bootstrap. This is also the automatic fallback:

```powershell
.\.venv\Scripts\python.exe scripts\build_ncaaf_model.py --source public --start-year 2014 --end-year 2025
```

The process caches API responses under `data/ncaaf/raw`, writes leakage-safe
features to `data/ncaaf/features.parquet`, rolling validation to
`data/ncaaf/backtest.json`, and the deployable artifact to
`data/ncaaf/model.json`. Raw data and local evaluation outputs are ignored by
Git; the small model artifact may be intentionally committed after review.

For long local runs, redirect output and let the process continue locally:

```powershell
$job = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "scripts\build_ncaaf_model.py --start-year 2014 --end-year 2025" `
  -RedirectStandardOutput "data\ncaaf\build.log" `
  -RedirectStandardError "data\ncaaf\build-error.log" `
  -WindowStyle Hidden -PassThru
$job.Id
```

## Live workflow

`daily_ncaaf_auto.py` is the primary logger and runs without Streamlit. It first
grades pending rows from official ESPN finals, then discovers the local day's
FBS schedule from ESPN, attaches optional multi-book odds, runs every safely
matched pregame through the HagLabs model, and appends every new prediction to
`NCAA Football Prediction Model` → `NCAAF Log`. Missing odds remain blank and are noted;
they never become invented lines or fake edges.

```powershell
cd C:\HagLabs\pitching-analytics-engine
.\.venv\Scripts\python.exe daily_ncaaf_auto.py
```

Use `--dry-run` for schedule and model verification without reading or writing
Google Sheets. A date override is available for bounded diagnostics:

```powershell
.\.venv\Scripts\python.exe daily_ncaaf_auto.py --date 2026-09-12 --dry-run
```

The first nine `NCAAF Log` columns retain the legacy contract. Stable ESPN IDs,
kickoff/prediction timestamps, confidence, edge, model version, market metadata,
actual winner, and the immutable feature snapshot are appended. Duplicate
protection uses both the stable game ID and date/away/home fallback. Once a row
exists, later runs never overwrite its pregame probabilities, pick, odds, edge,
or confidence. Games at or after kickoff are excluded from new logging.

The Streamlit Winner Board remains a review/admin fallback and uses the same
sheet logger and ESPN grader. Its slate now comes from ESPN even when no odds
key is available, so modelable games without a moneyline remain visible.

### Windows Task Scheduler

- Program/script: `C:\HagLabs\pitching-analytics-engine\run_daily_ncaaf_auto.bat`
- Start in: `C:\HagLabs\pitching-analytics-engine`
- Trigger: daily at 6:00 AM America/Chicago, repeat every 2 hours for 18 hours.
- Settings: run as soon as possible after a missed start; require network;
  stop after 20 minutes; if already running, do not start a parallel instance.

Repeated runs grade finals and discover newly listed/rescheduled games. They do
not replace an earlier official pregame snapshot merely because odds moved.

The Validation tab reports prospective accuracy and Brier score alongside the
rolling model, Elo, and market benchmarks. Historical backtests are evidence,
not a guarantee that the engine will beat future markets.

## Focused verification

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_ncaaf_model.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_daily_ncaaf_auto.py
.\.venv\Scripts\ruff.exe check daily_ncaaf_auto.py ncaaf_model.py ncaaf_ui.py scripts\build_ncaaf_model.py tests\test_ncaaf_model.py tests\test_daily_ncaaf_auto.py
```
