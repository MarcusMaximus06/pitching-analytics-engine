# MLB Model Upgrade Plan

## Goal
Improve Hag Labs MLB predictions by adding recent-form weighting while keeping compute cost low.

## Current Model Inputs
- Team runs scored per game
- Team runs allowed per game
- Starting pitcher FIP
- Park factor
- Monte Carlo Poisson simulation
- Vegas implied probability comparison

## Upgrade 1: Recent Form
Add short-term team performance signals:
- last 7 games runs scored per game
- last 7 games runs allowed per game
- last 14 games runs scored per game
- last 14 games runs allowed per game

## Proposed Blend
Use a weighted blend:

- 70% season-long performance
- 30% recent-form performance

Example:
season_rs_per_g * 0.70 + recent_rs_per_g * 0.30

## Why This Helps
Recent form can capture:
- injuries
- lineup changes
- bullpen fatigue
- offensive hot/cold streaks
- schedule effects

## Cost Control
Do not run this constantly.
Use cached MLB API pulls and reuse existing schedule/results data.

## Implementation Plan
1. Add recent-form function.
2. Cache it.
3. Blend recent form into expected runs.
4. Display recent-form adjustment in manual simulator.
5. Track whether recent-form model improves accuracy over time.

## Future Additions
- handedness splits
- bullpen fatigue
- weather
- travel/rest
- lineup confirmation
- starting pitcher recent form
