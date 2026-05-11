# Hag Labs Accuracy Tracking Plan

## Goal
Track every prediction in a consistent format so Hag Labs can measure accuracy, calibration, and model edge over time.

## Core Fields To Track

### Game Info
- date
- sport
- league
- away_team
- home_team

### Model Output
- model_away_prob
- model_home_prob
- model_pick
- confidence_tier
- model_edge

### Market / Baseline Comparison
- away_odds
- home_odds
- implied_away_prob
- implied_home_prob
- market_pick

### Result
- actual_winner
- result
- model_correct
- market_correct

### Future Fantasy Additions
- player_name
- projected_points
- actual_points
- projection_error
- boom_probability
- bust_probability

## Short-Term Implementation
1. Keep using Google Sheets.
2. Standardize prediction logs.
3. Add confidence tiers.
4. Add accuracy dashboard.
5. Later move logs to PostgreSQL when Sheets becomes too slow.

## Confidence Tiers
- Low: model edge under 3%
- Medium: model edge 3% to 7%
- High: model edge over 7%

## Long-Term Goal
Use historical accuracy to improve model weights automatically.
