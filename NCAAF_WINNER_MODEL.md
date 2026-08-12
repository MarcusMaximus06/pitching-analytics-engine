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

1. Configure both API keys as deployment secrets.
2. Open **NCAA Football → Winner Board**.
3. Review the full upcoming slate. Games without a market still receive an
   independent winner probability.
4. Select **Run and log full active slate** to preserve every game, prediction
   timestamp, model version, feature snapshot, no-vig market probability, and
   current odds—not only apparent edges.
5. Select **Grade completed predictions by CFBD game ID** after games finish.

The Validation tab reports prospective accuracy and Brier score alongside the
rolling model, Elo, and market benchmarks. Historical backtests are evidence,
not a guarantee that the engine will beat future markets.

## Focused verification

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_ncaaf_model.py
.\.venv\Scripts\ruff.exe check ncaaf_model.py ncaaf_ui.py scripts\build_ncaaf_model.py tests\test_ncaaf_model.py
```
