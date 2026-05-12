# MLB Pitcher Recent Form Plan

## Goal
Improve MLB prediction quality by adjusting starting pitcher strength using recent performance.

## Current State
The model uses season-long FIP for starting pitchers.

## New Target
Blend season FIP with recent pitcher performance.

## First Version
Use last 3 starts when available.

Track:
- recent innings pitched
- earned runs allowed
- recent ERA
- games found

## Blend
- 65% season FIP
- 35% recent ERA

## Fallback
If recent start data is unavailable:
- use season FIP only

## Why This Helps
Pitcher performance can change quickly due to:
- fatigue
- injury
- command issues
- pitch mix changes
- velocity changes

## Cost Control
Use MLB Stats API only.
Cache future pitcher-form calls if needed.
Avoid expensive AI or external paid data.
