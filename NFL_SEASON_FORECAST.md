# HagLabs NFL Season Record Forecast

## What it does

The NFL Simulation Engine now produces a forecast for every team's final
regular-season record and an expected cumulative record after each week.

- ESPN supplies the complete 18-week schedule and final scores.
- Completed games are locked; the simulator never re-predicts a known result.
- Final scores update team Elo strength before remaining games are projected.
- The Odds API supplies no-vig consensus probabilities when a future matchup is
  available. Those probabilities are blended with updated Elo in log-odds space.
- Twenty thousand simulations produce mean and median wins, an 80% win range,
  and an approximate playoff probability.
- The forecast refreshes every 15 minutes and therefore updates after completed
  games without a manual season reset.

## Interpretation

`Projected Wins` is the mean across simulations, not a promise or a rounded
record. `80% Win Range` is the 10th-to-90th percentile range. Weekly records are
expected values, so fractional wins and losses are intentional.

The playoff probability currently approximates a top-seven finish in each
conference. It does not yet apply division-winner seeding or the NFL's complete
tiebreaker hierarchy.

## Validation status

The remaining-season forecast is market-informed and suitable for scenario
planning. The independent static game model has not passed a multi-season
walk-forward validation, so live disagreements with sportsbooks are labeled
research-only and cannot become official picks. Historical accuracy must be
demonstrated before that gate is enabled.

## Focused verification

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_nfl_season_model.py tests\test_nfl_fantasy_model.py -q
.\.venv\Scripts\ruff.exe check --no-cache nfl_season_model.py tests\test_nfl_season_model.py
```
