from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import (
    FEATURE_NAMES,
    CFBDDataClient,
    SeasonContext,
    TeamState,
    WinnerModel,
    build_historical_feature_rows,
    create_feature_snapshot,
    devig_two_way,
    find_matching_schedule_game,
    market_consensus,
    paired_market_deltas,
    parse_espn_scoreboards,
    prediction_record,
    probability_metrics,
    update_states_with_current_games,
    walk_forward_backtest,
)


def _game(game_id, season, week, away, home, away_points, home_points, neutral=False):
    return {
        "id": game_id,
        "season": season,
        "week": week,
        "start_date": datetime(season, 8, 20, tzinfo=timezone.utc) + timedelta(days=7 * week),
        "away_team": away,
        "home_team": home,
        "away_points": away_points,
        "home_points": home_points,
        "neutral_site": neutral,
    }


def test_devig_removes_overround_and_consensus_uses_all_books():
    home, away, vig = devig_two_way(-130, 115)
    assert home + away == pytest.approx(1.0)
    assert vig > 0

    game = {
        "home_team": "Georgia",
        "away_team": "Clemson",
        "bookmakers": [
            {
                "key": "book_a",
                "title": "Book A",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-08-12T12:00:00Z",
                        "outcomes": [
                            {"name": "Georgia", "price": -130},
                            {"name": "Clemson", "price": 115},
                        ],
                    }
                ],
            },
            {
                "key": "book_b",
                "title": "Book B",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-08-12T12:05:00Z",
                        "outcomes": [
                            {"name": "Georgia", "price": -125},
                            {"name": "Clemson", "price": 110},
                        ],
                    }
                ],
            },
        ],
    }
    consensus = market_consensus(game)
    assert consensus is not None
    assert consensus["book_count"] == 2
    assert consensus["home_probability"] + consensus["away_probability"] == pytest.approx(1.0)
    assert consensus["observed_at"] == "2026-08-12T12:05:00Z"


def test_feature_snapshot_respects_neutral_site_and_rest():
    states = {
        "Home": TeamState(rating=1600, last_game="2026-08-20T00:00:00Z"),
        "Away": TeamState(rating=1500, last_game="2026-08-24T00:00:00Z"),
    }
    game = {
        "home_team": "Home",
        "away_team": "Away",
        "neutral_site": True,
        "start_date": "2026-08-31T00:00:00Z",
    }
    features = create_feature_snapshot(game, states, SeasonContext())
    assert features["home_field"] == 0
    assert features["elo_diff_100"] == pytest.approx(1.0)
    assert features["rest_diff_7"] > 0


def test_historical_rows_are_created_before_game_state_updates():
    games = [
        _game(1, 2022, 1, "Away", "Home", 10, 35),
        _game(2, 2022, 2, "Away", "Home", 24, 21),
    ]
    rows = build_historical_feature_rows(games)
    assert len(rows) == 2
    assert rows.iloc[0]["elo_diff_100"] == pytest.approx(0.0)
    assert rows.iloc[0]["margin_ema_diff_10"] == pytest.approx(0.0)
    assert rows.iloc[1]["elo_diff_100"] > 0
    assert rows.iloc[1]["margin_ema_diff_10"] > 0


def test_regularized_model_learns_signal_and_serializes(tmp_path):
    rng = np.random.default_rng(42)
    size = 700
    frame = pd.DataFrame({name: rng.normal(size=size) for name in FEATURE_NAMES})
    logits = 1.4 * frame["elo_diff_100"] + 0.8 * frame["home_field"] + 0.5 * frame["margin_ema_diff_10"]
    frame["home_win"] = rng.binomial(1, 1 / (1 + np.exp(-logits)))
    model = WinnerModel.fit(frame)
    probabilities = model.predict_home_probability(frame)
    assert probability_metrics(frame["home_win"], probabilities)["accuracy"] > 0.65
    assert model.coefficients[FEATURE_NAMES.index("elo_diff_100")] > 0

    target = tmp_path / "model.json"
    model.save(target)
    restored = WinnerModel.load(target)
    assert restored.predict_home_probability(frame.iloc[[0]])[0] == pytest.approx(probabilities[0])


def test_walk_forward_uses_only_prior_seasons():
    rng = np.random.default_rng(7)
    rows = []
    for season in range(2018, 2025):
        for game_id in range(90):
            values = {name: float(rng.normal()) for name in FEATURE_NAMES}
            probability = 1 / (1 + np.exp(-(1.2 * values["elo_diff_100"] + 0.4 * values["home_field"])))
            rows.append(
                {
                    "season": season,
                    "game_id": season * 1000 + game_id,
                    "home_win": int(rng.random() < probability),
                    "market_home_probability": None,
                    **values,
                }
            )
    result = walk_forward_backtest(pd.DataFrame(rows), min_train_seasons=3)
    assert result["games"] == 4 * 90
    assert set(result["by_season"]) == {"2021", "2022", "2023", "2024"}
    assert result["model"]["log_loss"] < 0.7


def test_bootstrap_predictions_never_claim_actionable_edge():
    model = WinnerModel.bootstrap()
    game = {
        "id": 123,
        "season": 2026,
        "away_team": "Away",
        "home_team": "Home",
        "neutral_site": False,
    }
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["elo_diff_100"] = 2.0
    record = prediction_record(
        game,
        features,
        model,
        {"home_probability": 0.40, "home_odds": 150, "away_odds": -170, "book_count": 4},
    )
    assert record["model_edge"] > 0.03
    assert record["actionable_edge"] is False
    assert record["model_mode"] == "bootstrap"
    assert record["predicted_winner"] == "Home"
    assert record["market_aware_predicted_winner"] == "Away"
    assert record["decision_source"] == "independent HagLabs model"

    model.metadata["market_validated"] = True
    validated_record = prediction_record(
        game,
        features,
        model,
        {"home_probability": 0.40, "home_odds": 150, "away_odds": -170, "book_count": 4},
    )
    assert validated_record["predicted_winner"] == "Away"
    assert validated_record["decision_source"] == "validated market ensemble"


def test_public_scoreboard_results_update_preseason_states_once():
    payload = {
        "events": [
            {
                "id": "401000001",
                "season": {"year": 2026, "type": 2},
                "week": {"number": 1},
                "date": "2026-08-29T16:00:00Z",
                "status": {"type": {"state": "post", "completed": True}},
                "competitions": [
                    {
                        "neutralSite": False,
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "35",
                                "team": {"location": "Home"},
                            },
                            {
                                "homeAway": "away",
                                "score": "17",
                                "team": {"location": "Away"},
                            },
                        ],
                    }
                ],
            }
        ]
    }
    games = parse_espn_scoreboards([payload, payload], 2026)
    states, summary = update_states_with_current_games(
        {"Home": TeamState(), "Away": TeamState()}, games
    )
    assert len(games) == 1
    assert summary["completed_games_applied"] == 1
    assert summary["latest_week"] == 1
    assert states["Home"].rating > states["Away"].rating
    assert states["Home"].games == 1


def test_schedule_matching_uses_canonical_names():
    odds = {"away_team": "Ole Miss Rebels", "home_team": "Miami Hurricanes"}
    schedule = [{"id": 9, "away_team": "Mississippi", "home_team": "Miami"}]
    assert find_matching_schedule_game(odds, schedule)["id"] == 9


def test_market_validation_interval_requires_repeatable_improvement():
    labels = np.array([0, 1] * 250)
    market = np.where(labels == 1, 0.60, 0.40)
    improved = np.where(labels == 1, 0.72, 0.28)
    deltas = paired_market_deltas(labels, market, improved, samples=300)
    assert deltas["log_loss_95_ci"][1] < 0
    assert deltas["brier_95_ci"][1] < 0


def test_cfbd_errors_do_not_echo_api_key():
    class Response:
        status_code = 401

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    client = CFBDDataClient("super-secret-key", session=Session())
    with pytest.raises(RuntimeError) as error:
        client.get("/games", year=2025)
    assert "super-secret-key" not in str(error.value)


def test_cfbd_client_normalizes_documented_query_names():
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return []

    class Session:
        def __init__(self):
            self.kwargs = None

        def get(self, *args, **kwargs):
            self.kwargs = kwargs
            return Response()

    session = Session()
    client = CFBDDataClient("key", session=session)
    client.get("/games", game_id=321, season_type="regular")
    assert session.kwargs["params"] == {"gameId": 321, "seasonType": "regular"}
