# MLB Pitcher Lookup Plan

## Goal
Map pitcher names to MLB player IDs so Hag Labs can fetch recent starts.

## Why This Is Needed
MLB Stats API game logs usually require a player ID, not just a name.

## Implementation Steps
1. Use existing `pitcher_data` from `fetch_mlb_api_data`.
2. Store pitcher MLB ID in `pitcher_data`.
3. Pass pitcher ID into recent-form function.
4. Fetch last 3 game logs.
5. Calculate recent ERA.
6. Blend recent ERA with season FIP.

## Safety Rule
Do not alter automated slate predictions until manual simulator confirms the pitcher lookup works.

## Fallback
If pitcher ID or recent logs are unavailable:
- use season FIP only
