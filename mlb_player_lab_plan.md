# MLB Player Lab Plan

## Goal
Create a searchable MLB player research page inspired by Baseball Savant, without changing the betting model.

## Phase 1: Basic Player Lab
- Add new MLB page option: MLB Player Lab
- Search/select MLB players from existing pitcher and hitter databases
- Show player type: Pitcher or Batter
- Show season stats
- Show premium player card

## Pitcher Profile Metrics
- Team
- FIP
- IP
- K
- K/9
- BB
- ERA proxy / recent ERA if available
- Form badge

## Batter Profile Metrics
- Games
- Hits
- Doubles
- Triples
- HR
- BB
- Runs
- RBI
- SB
- Strikeouts
- Fantasy-style production

## Phase 2: Savant-Inspired Visuals
- Percentile-style stat bars
- Pitch arsenal usage chart
- Year-over-year trend charts
- Movement/velocity chart if available
- Contact quality summary

## Phase 3: Advanced Data
- Explore Statcast/pybaseball integration
- Pull xwOBA, xBA, xSLG, hard-hit %, barrel %
- Add batter and pitcher advanced profile tabs

## Guardrails
- Do not alter MLB V2 betting model
- Keep Player Lab separate from predictions
- Cache data aggressively
- Add one data source at a time
- Do not scrape fragile pages blindly

## First Build Target
Add a new MLB page route called MLB Player Lab with a searchable player dropdown and basic pitcher/batter profile cards.
