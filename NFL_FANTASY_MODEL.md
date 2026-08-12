# HagLabs NFL Fantasy Model

## Use it

1. Start HagLabs normally.
2. Select **NFL Football**.
3. Select **NFL Draft Intelligence Lab**.
4. Select a saved league profile, or choose scoring, league size, 1QB/Superflex, and Redraft/Dynasty manually.
5. Open **Live Draft Room**, enter your draft slot, and record each selection as it happens.
6. Use the recalculated recommendation, next-turn availability estimate, roster fit, and market edge together.

The draft board can be downloaded as CSV.

## What version 2 models

- Exact fantasy scoring for PPR, half-PPR, standard, and six-point passing-TD PPR.
- Saved local league profiles with exact offensive scoring, roster format, team count, and known draft position.
- Separate ESPN PPR and Sleeper PPR defaults for leagues whose private settings are not available.
- Four completed seasons of recency-weighted player production.
- Expected fantasy opportunity and touchdown regression.
- Target share, WOPR, and offensive snap share.
- Capped team-environment effects.
- NFL Next Gen Stats efficiency with strong regression toward the mean.
- Current roster, experience, draft capital, age curves, availability, and role uncertainty.
- Cross-position value over replacement based on league size and 1QB/Superflex demand.
- Five-year replacement-adjusted dynasty surplus, age/development curves, and current NFL roster/depth status.
- Current DynastyProcess market ECR/value, kept separate from HagLabs' independent player projection.
- Snake-draft turn order, roster needs, market uncertainty, and the probability a target survives to the next turn.
- Live Sleeper draft-pick synchronization through its public read-only API; one-click manual pick entry for ESPN.
- Explicit floor, ceiling, risk, confidence, evidence, and hidden-signal labels.

Public performance data comes from nflverse and ffopportunity release files. Market context comes from the GPL-3.0 DynastyProcess open-data repository and is used to estimate draft cost, not to overwrite the HagLabs model. The Streamlit cache refreshes the model every six hours, while a persistent local cache keeps the board usable during upstream HTTP failures. Optional feeds fail independently so one unavailable advanced dataset does not take down the rest of HagLabs.

League profiles live in the Git-ignored `haglabs_data/nfl_league_profiles.json` file. League IDs are not passwords, but private ESPN cookies and account credentials must never be placed there. Sleeper exposes public league settings by league ID; private ESPN leagues require a settings screenshot or an authenticated integration approved by the user.

## Validation baseline

The 2025 walk-forward test predicts 2025 from earlier seasons and compares the model with reusing the previous season's PPG.

| Metric | HagLabs | Prior-year baseline |
|---|---:|---:|
| Players | 380 | 380 |
| Mean absolute error | 2.482 PPG | 2.598 PPG |
| Rank correlation | 0.822 | 0.814 |

The measured advantage is useful but modest. Do not describe the model as the most accurate available until repeated out-of-sample seasons and external projection comparisons support that claim.

## Draft-day workflow

1. Select the correct league profile and confirm scoring, teams, QB format, and redraft/dynasty mode.
2. Enter the assigned draft slot and number of rounds.
3. Sleeper: press **Sync live Sleeper picks**. ESPN: select the drafted player, choose **Other team** or **My team**, and press **Record pick**.
4. When a target is taken, record the pick; the available pool and recommendation immediately recalculate.
5. Read `Selection Score` as a decision priority, not a projection. It combines independent model value, market cost, roster fit, confidence, and the risk of waiting.
6. Download the draft-state CSV periodically as a recovery copy.

## Honest limitations

- No model can guarantee it will outperform ESPN or Sleeper. That claim requires repeated, timestamped, out-of-sample comparison against their archived preseason projections.
- ESPN private-league picks cannot be read without authenticated session data, which HagLabs does not request or store. Manual entry is deliberately supported.
- Sleeper dynasty market data does not publish expert-level standard deviations, so the next-turn probability uses a documented rank-dependent uncertainty estimate.
- Current injuries, suspensions, final depth charts, offensive-line changes, and coaching changes remain time-sensitive. Roster/depth feeds are shown with their data date and stale data should be discounted.
- Rookie forecasts still rely heavily on draft capital and position baselines; college production and declare-age calibration remain the largest model gap.
- Team defense is not yet projected. Kicker field-goal distance scoring is supported where nflverse exposes exact made-distance totals.

## Efficient Codex task prompt

Use this compact format for future NFL fantasy work:

```text
Goal: <one concrete outcome>
League: <teams, scoring, lineup, keeper/dynasty, draft slot>
Current behavior: <what HagLabs does now>
Expected behavior: <what should change>
Acceptance check: <specific result or example>
Deployment: local verification only | prepare GitHub update | deploy now
```

Codex should inspect `nfl_fantasy_model.py`, `nfl_fantasy_ui.py`, and the focused test first. It should not scan the full `app.py` unless routing or shared UI behavior requires it.

## Verification

```powershell
rtk pytest tests\test_nfl_fantasy_model.py -q
rtk ruff check nfl_fantasy_model.py nfl_fantasy_ui.py nfl_draft_assistant.py nfl_league_profiles.py tests\test_nfl_fantasy_model.py
.\.venv\Scripts\python.exe -m py_compile app.py nfl_fantasy_model.py nfl_fantasy_ui.py nfl_draft_assistant.py nfl_league_profiles.py
```

Fantasy projections are estimates. Injuries, roster moves, coaching decisions, and depth-chart changes can invalidate assumptions quickly.
