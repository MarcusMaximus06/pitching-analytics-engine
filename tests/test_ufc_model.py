from __future__ import annotations

import json

import pytest

from ufc_model import FEATURE_NAMES, MODEL_VERSION, load_model_artifact, predict_matchup


def _artifact():
    coefficients = [0.0] * len(FEATURE_NAMES)
    coefficients[0] = -0.7
    coefficients[1] = -0.5
    coefficients[8] = -0.1
    return {
        "model_version": MODEL_VERSION,
        "validated": True,
        "market_validated": False,
        "feature_names": list(FEATURE_NAMES),
        "feature_center": [0.0] * len(FEATURE_NAMES),
        "feature_scale": [100.0, 0.2] + [1.0] * (len(FEATURE_NAMES) - 2),
        "coefficients": coefficients,
        "fighter_states": {
            "Alpha": {"elo": 1650, "fights": 10, "wins": 8, "losses": 2, "win_streak": 3, "loss_streak": 0},
            "Beta": {"elo": 1450, "fights": 10, "wins": 5, "losses": 5, "win_streak": 0, "loss_streak": 1},
        },
    }


def _profile(record="8-2-0", age=29, reach=74, sample=8):
    return {
        "Record": record,
        "Age": age,
        "Height": 71,
        "Reach": reach,
        "SLpM": 4.5,
        "Str Acc %": 50,
        "SApM": 3.0,
        "Str Def %": 58,
        "TD Avg": 1.8,
        "TD Acc %": 42,
        "TD Def %": 75,
        "Sub Avg": 0.5,
        "UFC Stat Sample": sample,
        "KO %": 50,
        "Sub %": 20,
        "Durability": 82,
        "Cardio": 84,
        "Recent Form": 85,
        "Strength of Schedule": 82,
    }


def test_matchup_is_symmetric_and_method_probabilities_normalize():
    artifact = _artifact()
    alpha = _profile()
    beta = _profile(record="5-5-0", age=33, reach=71)
    forward = predict_matchup("Alpha", "Beta", alpha, beta, artifact)
    reverse = predict_matchup("Beta", "Alpha", beta, alpha, artifact)

    assert forward["A Win %"] == pytest.approx(reverse["B Win %"], abs=0.1)
    assert forward["B Win %"] == pytest.approx(reverse["A Win %"], abs=0.1)
    assert forward["Predicted Winner"] == "Alpha"
    total = sum(
        forward[key]
        for key in (
            "A KO/TKO %",
            "A Submission %",
            "A Decision %",
            "B KO/TKO %",
            "B Submission %",
            "B Decision %",
        )
    )
    assert total == pytest.approx(100.0, abs=0.1)


def test_low_sample_profiles_are_less_confident():
    artifact = _artifact()
    strong = _profile(sample=10)
    weak = _profile(record="5-5-0", age=33, reach=71, sample=10)
    high_quality = predict_matchup("Alpha", "Beta", strong, weak, artifact)
    low_quality = predict_matchup(
        "Alpha", "Beta", {**strong, "UFC Stat Sample": 0, "SLpM": 0, "TD Avg": 0}, {**weak, "UFC Stat Sample": 0, "SLpM": 0, "TD Avg": 0}, artifact
    )
    assert abs(low_quality["A Win %"] - 50.0) < abs(high_quality["A Win %"] - 50.0)


def test_odds_anchored_path_is_symmetric_and_identified():
    artifact = _artifact()
    artifact["market_validated"] = True
    artifact["market_coefficients"] = [0.0] * len(FEATURE_NAMES) + [1.0]
    alpha = _profile()
    beta = _profile(record="5-5-0", age=33, reach=71)
    forward = predict_matchup("Alpha", "Beta", alpha, beta, artifact, market_probability_a=0.70)
    reverse = predict_matchup("Beta", "Alpha", beta, alpha, artifact, market_probability_a=0.30)

    assert forward["Prediction Mode"] == "Odds-Anchored"
    assert reverse["Prediction Mode"] == "Odds-Anchored"
    assert forward["A Win %"] == pytest.approx(reverse["B Win %"], abs=0.1)
    assert forward["B Win %"] == pytest.approx(reverse["A Win %"], abs=0.1)


def test_model_artifact_loader_rejects_wrong_version(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"model_version": "old"}), encoding="utf-8")
    assert load_model_artifact(path) == {}
    path.write_text(json.dumps(_artifact()), encoding="utf-8")
    assert load_model_artifact(path)["model_version"] == MODEL_VERSION
