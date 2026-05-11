# Hag Labs Model Notes

## Current Priority
Improve fantasy prediction quality while keeping infrastructure cheap.

## Core Rule
Do not run expensive calculations on every page load. Prefer cached, precomputed, or manually triggered outputs.

## Current Engines
- MLB Poisson simulation
- NFL Elo + EPA style model
- NCAA Football power rating model
- NCAA Softball Log5 + SOS model

## Short-Term Goals
1. Track prediction history cleanly
2. Improve calibration
3. Compare Hag Labs projections vs ESPN/Sleeper-style baselines
4. Add range-of-outcomes projections
5. Keep Render costs low

## Model Improvement Ideas
- Add confidence tiers
- Add recent-form weighting
- Add injury/news adjustment fields
- Add player usage trends
- Add projection percentiles
- Add boom/bust probabilities
- Add historical accuracy dashboard

## Cost Philosophy
Use cheap CPU-based models first:
- Monte Carlo
- regression
- ensemble weighting
- calibration tracking

Avoid expensive live AI inference unless used only for summaries or explanations.
