from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from nfl_season_model import (
    build_game_probabilities,
    current_nfl_season,
    parse_espn_schedule,
    simulate_season_records,
)


def _event(game_id, week, away, home, away_score=None, home_score=None):
    completed = away_score is not None and home_score is not None
    return {
        "id": str(game_id),
        "date": f"2026-09-{week + 2:02d}T00:00Z",
        "season": {"year": 2026, "slug": "regular-season"},
        "week": {"number": week},
        "status": {"type": {"state": "post" if completed else "pre", "completed": completed}},
        "competitions": [{
            "neutralSite": False,
            "competitors": [
                {"homeAway": "home", "score": home_score, "team": {"displayName": home}},
                {"homeAway": "away", "score": away_score, "team": {"displayName": away}},
            ],
        }],
    }


def test_current_season_rolls_over_in_july():
    assert current_nfl_season(datetime(2026, 2, 1, tzinfo=timezone.utc)) == 2025
    assert current_nfl_season(datetime(2026, 9, 1, tzinfo=timezone.utc)) == 2026


def test_schedule_parser_filters_and_locks_completed_results():
    payload = {"events": [_event(1, 1, "Away", "Home", 17, 24)]}
    frame = parse_espn_schedule([payload], 2026)
    assert len(frame) == 1
    assert bool(frame.iloc[0]["completed"])
    assert frame.iloc[0]["home_score"] == 24


def test_market_probability_is_blended_for_unplayed_game():
    schedule = parse_espn_schedule([{"events": [_event(2, 2, "Away", "Home")]}], 2026)
    ratings = {"Away": {"elo": 1500}, "Home": {"elo": 1500}}
    odds = pd.DataFrame([{"Away Team": "Away", "Home Team": "Home", "Vegas Home %": "70.0"}])
    games, _ = build_game_probabilities(schedule, ratings, odds)
    probability = games.iloc[0]["home_win_probability"]
    assert probability > games.iloc[0]["elo_home_probability"]
    assert probability < 0.70
    assert games.iloc[0]["probability_source"] == "Market + updated Elo"


def test_simulation_preserves_completed_wins_and_builds_weekly_path():
    events = [
        _event(1, 1, "Away", "Home", 10, 24),
        _event(2, 2, "Home", "Away"),
    ]
    schedule = parse_espn_schedule([{"events": events}], 2026)
    ratings = {"Away": {"elo": 1500}, "Home": {"elo": 1550}}
    summary, weekly, games = simulate_season_records(schedule, ratings, simulations=2000)
    home = summary[summary["Team"] == "Home"].iloc[0]
    assert home["Current Record"] == "1-0"
    assert home["Projected Wins"] >= 1.0
    assert len(weekly[weekly["Team"] == "Home"]) == 18
    assert len(games) == 2
    assert summary["Playoff Probability"].between(0, 1).all()


def test_probability_rows_sum_to_one():
    schedule = parse_espn_schedule([{"events": [_event(3, 1, "Away", "Home")]}], 2026)
    games, _ = build_game_probabilities(schedule, {"Away": 1600, "Home": 1400})
    home = games.iloc[0]["home_win_probability"]
    assert home + (1.0 - home) == pytest.approx(1.0)
