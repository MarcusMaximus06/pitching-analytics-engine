# HagLabs UFC Winner Engine

## Production model

`ufc-winner-v3.0.0` predicts fight winners at two levels:

- **Independent mode** works without a betting line. It uses pre-fight Elo, record, experience, streaks, title-fight context, age, height, reach, striking, takedown, submission, and finish-rate differences.
- **Odds-anchored mode** is used by the live Vegas board. It starts from the no-vig market probability and applies a regularized correction learned from the independent fighter features. The market is an input, not the answer.

All training examples are processed chronologically. Elo, record, and streak state are captured before each fight is graded. Difference features are centered at zero and augmented in reverse order so swapping Fighter A and Fighter B produces complementary probabilities.

Sparse profiles are shrunk toward 50/50. Current form, strength of schedule, cardio, manual adjustments, and five-round context are bounded so they cannot overpower the fitted historical model. The UI displays a probability range rather than presenting a point estimate as certainty.

## Data and reproducibility

The production artifact is built from the TidyTuesday UFC snapshot pinned to commit `f4d1b35ca09d8c0d875451635daef9179adfc9e0`. The snapshot contains UFCStats-derived pre-fight statistics and historical moneylines through 2026-03-28. Live prices come from The Odds API and are converted to no-vig probabilities.

Rebuild from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\build_ufc_model.py
```

The command writes `data/ufc/model.json`, including source/version metadata, coefficients, fighter state, and holdout metrics.
It also generates fallback profiles for the full historical roster. Existing CSV/manual profiles always take precedence, so expanding model coverage never overwrites a current curated fighter entry.

## Frozen holdout results

The model trains before 2024-01-01 and evaluates on later fights:

| Path | Fights | Accuracy | Brier | Log loss |
|---|---:|---:|---:|---:|
| Independent HagLabs | 1,141 | 65.9% | 0.218 | 0.626 |
| No-vig market | 996 | 70.0% | 0.199 | 0.582 |
| Odds-anchored HagLabs | 996 | 70.3% | 0.195 | 0.573 |

The odds-anchored path improved all three recorded metrics on the frozen market-covered holdout, but the advantage is small and is not a guarantee of future profit. Closing-line value, calibration, and official-pick performance must continue to be tracked prospectively.

## Decision policy

Only High and Medium confidence predictions from a validated artifact count as official picks. Low and Tracking rows remain research observations. Every new log row stores the model version, decision-policy version, and uncertainty range so later evaluations do not mix model generations.
