"""Leakage-aware UFC winner model and live matchup inference.

The training rows are ordered chronologically. Elo, streak, and experience state
are captured before each result is applied, then a symmetric ridge-logistic model
is fitted so swapping fighter order always produces the complementary probability.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

MODEL_VERSION = "ufc-winner-v3.0.0"
DECISION_POLICY_VERSION = "ufc-shadow-validation-v1.0.0"
PINNED_DATASET_COMMIT = "f4d1b35ca09d8c0d875451635daef9179adfc9e0"
DEFAULT_DATASET_URL = (
    "https://raw.githubusercontent.com/rfordatascience/tidytuesday/"
    f"{PINNED_DATASET_COMMIT}/data/2026/2026-07-07/ultimate_ufc_dataset.csv"
)

FEATURE_NAMES = (
    "elo_diff",
    "win_rate_diff",
    "experience_log_diff",
    "win_streak_diff",
    "loss_streak_diff",
    "title_bout_diff",
    "age_diff",
    "height_cm_diff",
    "reach_cm_diff",
    "sig_str_landed_diff",
    "sig_str_accuracy_diff",
    "td_landed_diff",
    "td_accuracy_diff",
    "submission_attempt_diff",
    "ko_rate_diff",
    "submission_rate_diff",
)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def sigmoid(value: float | np.ndarray) -> float | np.ndarray:
    clipped = np.clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _record(profile: Mapping[str, Any]) -> tuple[int, int, int]:
    values = re.findall(r"\d+", str(profile.get("Record") or ""))
    if len(values) >= 2:
        return int(values[0]), int(values[1]), int(values[2]) if len(values) > 2 else 0
    return 0, 0, 0


def _rate(wins: float, losses: float) -> float:
    return (wins + 1.0) / (wins + losses + 2.0)


def _state_for(states: dict[str, dict[str, Any]], fighter: str) -> dict[str, Any]:
    return states.setdefault(
        fighter,
        {
            "elo": 1500.0,
            "fights": 0,
            "wins": 0,
            "losses": 0,
            "win_streak": 0,
            "loss_streak": 0,
            "last_date": "",
        },
    )


def _row_feature_vector(row: Mapping[str, Any], states: dict[str, dict[str, Any]]) -> list[float]:
    red = str(row.get("r_fighter") or "").strip()
    blue = str(row.get("b_fighter") or "").strip()
    red_state = _state_for(states, red)
    blue_state = _state_for(states, blue)

    red_wins = safe_float(row.get("r_wins"), red_state["wins"])
    red_losses = safe_float(row.get("r_losses"), red_state["losses"])
    blue_wins = safe_float(row.get("b_wins"), blue_state["wins"])
    blue_losses = safe_float(row.get("b_losses"), blue_state["losses"])
    red_ko = safe_float(row.get("r_win_by_ko_tko"))
    blue_ko = safe_float(row.get("b_win_by_ko_tko"))
    red_sub = safe_float(row.get("r_win_by_submission"))
    blue_sub = safe_float(row.get("b_win_by_submission"))

    return [
        blue_state["elo"] - red_state["elo"],
        _rate(blue_wins, blue_losses) - _rate(red_wins, red_losses),
        math.log1p(blue_wins + blue_losses) - math.log1p(red_wins + red_losses),
        safe_float(row.get("b_current_win_streak")) - safe_float(row.get("r_current_win_streak")),
        safe_float(row.get("b_current_lose_streak")) - safe_float(row.get("r_current_lose_streak")),
        safe_float(row.get("b_total_title_bouts")) - safe_float(row.get("r_total_title_bouts")),
        safe_float(row.get("b_age"), 30.0) - safe_float(row.get("r_age"), 30.0),
        safe_float(row.get("b_height_cms"), 175.0) - safe_float(row.get("r_height_cms"), 175.0),
        safe_float(row.get("b_reach_cms"), 180.0) - safe_float(row.get("r_reach_cms"), 180.0),
        safe_float(row.get("b_avg_sig_str_landed"), 4.0) - safe_float(row.get("r_avg_sig_str_landed"), 4.0),
        safe_float(row.get("b_avg_sig_str_pct"), 0.45) - safe_float(row.get("r_avg_sig_str_pct"), 0.45),
        safe_float(row.get("b_avg_td_landed"), 1.2) - safe_float(row.get("r_avg_td_landed"), 1.2),
        safe_float(row.get("b_avg_td_pct"), 0.35) - safe_float(row.get("r_avg_td_pct"), 0.35),
        safe_float(row.get("b_avg_sub_att"), 0.4) - safe_float(row.get("r_avg_sub_att"), 0.4),
        (blue_ko / max(1.0, blue_wins)) - (red_ko / max(1.0, red_wins)),
        (blue_sub / max(1.0, blue_wins)) - (red_sub / max(1.0, red_wins)),
    ]


def _update_states(row: Mapping[str, Any], states: dict[str, dict[str, Any]]) -> None:
    red = str(row.get("r_fighter") or "").strip()
    blue = str(row.get("b_fighter") or "").strip()
    winner = str(row.get("winner") or "")
    red_state = _state_for(states, red)
    blue_state = _state_for(states, blue)
    expected_red = 1.0 / (1.0 + 10.0 ** ((blue_state["elo"] - red_state["elo"]) / 400.0))
    actual_red = 1.0 if winner == "Red" else 0.0
    finish = str(row.get("finish") or "").upper()
    multiplier = 1.12 if ("KO" in finish or "SUB" in finish) else 1.0
    experience = min(red_state["fights"], blue_state["fights"])
    k_factor = max(18.0, 38.0 - min(20.0, experience * 1.25)) * multiplier
    change = k_factor * (actual_red - expected_red)
    red_state["elo"] += change
    blue_state["elo"] -= change
    red_state["fights"] += 1
    blue_state["fights"] += 1
    if winner == "Red":
        red_state["wins"] += 1
        blue_state["losses"] += 1
        red_state["win_streak"] += 1
        red_state["loss_streak"] = 0
        blue_state["loss_streak"] += 1
        blue_state["win_streak"] = 0
    else:
        blue_state["wins"] += 1
        red_state["losses"] += 1
        blue_state["win_streak"] += 1
        blue_state["loss_streak"] = 0
        red_state["loss_streak"] += 1
        red_state["win_streak"] = 0
    fight_date = str(row.get("date") or "")[:10]
    red_state["last_date"] = fight_date
    blue_state["last_date"] = fight_date


def historical_examples(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    ordered = frame.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce")
    ordered = ordered[
        ordered["winner"].isin(["Red", "Blue"])
        & ordered["r_fighter"].notna()
        & ordered["b_fighter"].notna()
        & ordered["date"].notna()
    ].sort_values(["date", "r_fighter", "b_fighter"])
    states: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for source in ordered.to_dict("records"):
        vector = _row_feature_vector(source, states)
        result = {
            "date": pd.Timestamp(source["date"]).date().isoformat(),
            "target": 1.0 if source["winner"] == "Red" else 0.0,
            "r_odds": safe_float(source.get("r_odds"), float("nan")),
            "b_odds": safe_float(source.get("b_odds"), float("nan")),
        }
        result.update(dict(zip(FEATURE_NAMES, vector)))
        rows.append(result)
        _update_states(source, states)
    return pd.DataFrame(rows), states


def _fit_ridge_logistic(matrix: np.ndarray, target: np.ndarray, penalty: float = 0.7) -> np.ndarray:
    # Symmetric augmentation removes red/blue corner-order bias by construction.
    x = np.vstack([matrix, -matrix])
    y = np.concatenate([target, 1.0 - target])
    weights = np.zeros(x.shape[1], dtype=float)
    identity = np.eye(x.shape[1])
    for _ in range(80):
        probability = sigmoid(x @ weights)
        variance = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = (x.T @ (probability - y)) / len(y) + penalty * weights / len(y)
        hessian = (x.T @ (x * variance[:, None])) / len(y) + penalty * identity / len(y)
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return weights


def _probability_metrics(target: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    probability = np.clip(probability.astype(float), 0.01, 0.99)
    return {
        "games": len(target),
        "accuracy": float(np.mean((probability >= 0.5) == (target >= 0.5))),
        "brier": float(np.mean((probability - target) ** 2)),
        "log_loss": float(-np.mean(target * np.log(probability) + (1.0 - target) * np.log(1.0 - probability))),
    }


def _implied_probability(odds: float) -> float | None:
    if not math.isfinite(odds) or odds == 0:
        return None
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def _market_red_probability(red_odds: float, blue_odds: float) -> float | None:
    red = _implied_probability(red_odds)
    blue = _implied_probability(blue_odds)
    if red is None or blue is None or red + blue <= 0:
        return None
    return red / (red + blue)


def _latest_fighter_profiles(frame: pd.DataFrame, states: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    ordered = frame.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce")
    ordered = ordered[ordered["date"].notna()].sort_values("date")
    latest: dict[str, dict[str, Any]] = {}
    methods: dict[str, dict[str, int]] = {}
    for row in ordered.to_dict("records"):
        for side in ("r", "b"):
            name = str(row.get(f"{side}_fighter") or "").strip()
            if not name:
                continue
            latest[name] = {
                "Division": str(row.get("weight_class") or "Unknown"),
                "Age": safe_float(row.get(f"{side}_age"), 30.0),
                "Height": safe_float(row.get(f"{side}_height_cms"), 175.3) / 2.54,
                "Reach": safe_float(row.get(f"{side}_reach_cms"), 182.9) / 2.54,
                "Stance": str(row.get(f"{side}_stance") or ""),
                "SLpM": safe_float(row.get(f"{side}_avg_sig_str_landed"), 4.0),
                "Str Acc %": safe_float(row.get(f"{side}_avg_sig_str_pct"), 0.45) * 100.0,
                "TD Avg": safe_float(row.get(f"{side}_avg_td_landed"), 1.2),
                "TD Acc %": safe_float(row.get(f"{side}_avg_td_pct"), 0.35) * 100.0,
                "Sub Avg": safe_float(row.get(f"{side}_avg_sub_att"), 0.4),
                "Last Historical Fight": pd.Timestamp(row["date"]).date().isoformat(),
            }
        winner_side = "r" if row.get("winner") == "Red" else "b" if row.get("winner") == "Blue" else ""
        if winner_side:
            winner = str(row.get(f"{winner_side}_fighter") or "").strip()
            bucket = methods.setdefault(winner, {"ko": 0, "submission": 0, "decision": 0})
            finish = str(row.get("finish") or "").lower()
            if "ko" in finish:
                bucket["ko"] += 1
            elif "sub" in finish:
                bucket["submission"] += 1
            else:
                bucket["decision"] += 1

    profiles: dict[str, dict[str, Any]] = {}
    for name, raw in latest.items():
        state = states.get(name, {})
        wins = int(safe_float(state.get("wins")))
        losses = int(safe_float(state.get("losses")))
        draws = max(0, int(safe_float(state.get("fights"))) - wins - losses)
        method = methods.get(name, {})
        denominator = max(1, sum(method.values()))
        profiles[name] = {
            **raw,
            "Status": "Historical Model Roster",
            "Record": f"{wins}-{losses}-{draws}",
            "Style": "Historical statistical profile",
            "Data Quality": "Historical pre-fight statistics",
            "Data Source": "Pinned UFCStats-derived TidyTuesday snapshot",
            "SApM": 0,
            "Str Def %": 0,
            "TD Def %": 0,
            "KD Avg": 0,
            "Control Score": 0,
            "UFC Stat Sample": int(safe_float(state.get("fights"))),
            "Striking": 75,
            "Grappling": 75,
            "Wrestling": 75,
            "Submission": 75,
            "Durability": 75,
            "Cardio": 75,
            "Power": 75,
            "Speed": 75,
            "Fight IQ": 75,
            "Experience": min(100, 45 + int(safe_float(state.get("fights"))) * 3),
            "KO %": round(100.0 * safe_float(method.get("ko")) / denominator, 1),
            "Sub %": round(100.0 * safe_float(method.get("submission")) / denominator, 1),
            "Decision %": round(100.0 * safe_float(method.get("decision")) / denominator, 1),
            "Recent Form": 75,
            "Strength of Schedule": 75,
            "Notes": "Generated from the latest leakage-safe historical row; current manual/live profiles take precedence.",
        }
    return profiles


def build_model_artifact(frame: pd.DataFrame, validation_start: str = "2024-01-01") -> dict[str, Any]:
    examples, states = historical_examples(frame)
    cutoff = pd.Timestamp(validation_start)
    dates = pd.to_datetime(examples["date"])
    train = examples[dates < cutoff]
    validation = examples[dates >= cutoff]
    if len(train) < 1000 or len(validation) < 300:
        split = max(1000, int(len(examples) * 0.78))
        train = examples.iloc[:split]
        validation = examples.iloc[split:]

    train_x = train[list(FEATURE_NAMES)].to_numpy(dtype=float)
    # Every feature is an opponent-minus-fighter difference. Centering at zero
    # preserves exact A/B symmetry; a nonzero empirical median would make
    # predict(A, B) differ from 1 - predict(B, A).
    center = np.zeros(len(FEATURE_NAMES), dtype=float)
    train_x = np.where(np.isfinite(train_x), train_x, center)
    scale = np.nanstd(np.vstack([train_x, -train_x]), axis=0)
    scale = np.where(scale < 1e-6, 1.0, scale)
    train_z = (train_x - center) / scale
    train_y = train["target"].to_numpy(dtype=float)
    validation_x = validation[list(FEATURE_NAMES)].to_numpy(dtype=float)
    validation_x = np.where(np.isfinite(validation_x), validation_x, center)
    validation_z = (validation_x - center) / scale
    validation_y = validation["target"].to_numpy(dtype=float)
    validation_weights = _fit_ridge_logistic(train_z, train_y)
    model_probability = sigmoid(validation_z @ validation_weights)
    model_metrics = _probability_metrics(validation_y, model_probability)

    market_mask: list[bool] = []
    market_probabilities: list[float] = []
    for row in validation.to_dict("records"):
        probability = _market_red_probability(row["r_odds"], row["b_odds"])
        market_mask.append(probability is not None)
        market_probabilities.append(probability if probability is not None else 0.5)
    mask = np.asarray(market_mask, dtype=bool)
    market_probability = np.asarray(market_probabilities, dtype=float)[mask]
    market_target = validation_y[mask]
    market_metrics = _probability_metrics(market_target, market_probability) if mask.any() else {}

    # The live market model treats no-vig odds as a prior and learns whether the
    # independent fighter features contain residual information. Training is
    # confined to the pre-cutoff sample; the later fights remain untouched.
    train_market_rows = []
    train_market_targets = []
    for index, row in enumerate(train.to_dict("records")):
        probability = _market_red_probability(row["r_odds"], row["b_odds"])
        if probability is None:
            continue
        market_logit = math.log(clamp(probability, 0.01, 0.99) / (1.0 - clamp(probability, 0.01, 0.99)))
        train_market_rows.append(np.append(train_z[index], market_logit))
        train_market_targets.append(train_y[index])
    market_coefficients = _fit_ridge_logistic(
        np.asarray(train_market_rows, dtype=float),
        np.asarray(train_market_targets, dtype=float),
        penalty=3.0,
    )
    validation_market_x = np.column_stack(
        [
            validation_z[mask],
            np.log(np.clip(market_probability, 0.01, 0.99) / (1.0 - np.clip(market_probability, 0.01, 0.99))),
        ]
    )
    stacked_probability = sigmoid(validation_market_x @ market_coefficients)
    stacked_metrics = _probability_metrics(market_target, stacked_probability)
    blend_results: dict[str, dict[str, float | int]] = {}
    best_weight = 1.0
    best_loss = safe_float(market_metrics.get("log_loss"), 99.0)
    for weight in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
        blended = (1.0 - weight) * model_probability[mask] + weight * market_probability
        metrics = _probability_metrics(market_target, blended)
        blend_results[f"{weight:.2f}"] = metrics
        if safe_float(metrics["log_loss"], 99.0) < best_loss:
            best_loss = safe_float(metrics["log_loss"], 99.0)
            best_weight = weight

    all_x = examples[list(FEATURE_NAMES)].to_numpy(dtype=float)
    final_center = np.zeros(len(FEATURE_NAMES), dtype=float)
    all_x = np.where(np.isfinite(all_x), all_x, final_center)
    final_scale = np.nanstd(np.vstack([all_x, -all_x]), axis=0)
    final_scale = np.where(final_scale < 1e-6, 1.0, final_scale)
    final_weights = _fit_ridge_logistic((all_x - final_center) / final_scale, examples["target"].to_numpy(dtype=float))
    validated = (
        safe_float(model_metrics.get("games")) >= 500
        and safe_float(model_metrics.get("accuracy")) > 0.55
        and safe_float(model_metrics.get("brier"), 1.0) < 0.245
    )
    market_validated = bool(
        market_metrics
        and safe_float(stacked_metrics.get("accuracy")) >= safe_float(market_metrics.get("accuracy"))
        and safe_float(stacked_metrics.get("brier"), 1.0) < safe_float(market_metrics.get("brier"), 1.0)
        and safe_float(stacked_metrics.get("log_loss"), 99.0) < safe_float(market_metrics.get("log_loss"), 99.0)
    )

    all_market_rows = []
    all_market_targets = []
    all_z = (all_x - final_center) / final_scale
    for index, row in enumerate(examples.to_dict("records")):
        probability = _market_red_probability(row["r_odds"], row["b_odds"])
        if probability is None:
            continue
        market_logit = math.log(clamp(probability, 0.01, 0.99) / (1.0 - clamp(probability, 0.01, 0.99)))
        all_market_rows.append(np.append(all_z[index], market_logit))
        all_market_targets.append(row["target"])
    final_market_coefficients = _fit_ridge_logistic(
        np.asarray(all_market_rows, dtype=float),
        np.asarray(all_market_targets, dtype=float),
        penalty=3.0,
    )

    return {
        "model_version": MODEL_VERSION,
        "decision_policy_version": DECISION_POLICY_VERSION,
        "source_url": DEFAULT_DATASET_URL,
        "source_commit": PINNED_DATASET_COMMIT,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "trained_through": str(examples["date"].max()),
        "training_games": len(examples),
        "validation_start": str(validation["date"].min()),
        "validated": validated,
        "market_validated": market_validated,
        "market_weight": best_weight,
        "feature_names": list(FEATURE_NAMES),
        "feature_center": [float(value) for value in final_center],
        "feature_scale": [float(value) for value in final_scale],
        "coefficients": [float(value) for value in final_weights],
        "market_coefficients": [float(value) for value in final_market_coefficients],
        "validation": {
            "model": model_metrics,
            "market": market_metrics,
            "odds_anchored": stacked_metrics,
            "blends": blend_results,
        },
        "fighter_states": {
            name: {
                key: (round(value, 4) if isinstance(value, float) else value)
                for key, value in state.items()
            }
            for name, state in states.items()
        },
        "fighter_profiles": _latest_fighter_profiles(frame, states),
    }


def load_model_artifact(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        return {}
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    if payload.get("model_version") != MODEL_VERSION:
        return {}
    return payload


def _lookup_state(artifact: Mapping[str, Any], fighter: str) -> Mapping[str, Any]:
    states = artifact.get("fighter_states") or {}
    if fighter in states:
        return states[fighter]
    target = _normalize_name(fighter)
    for name, state in states.items():
        if _normalize_name(name) == target:
            return state
    return {}


def _profile_feature_vector(
    profile_a: Mapping[str, Any],
    profile_b: Mapping[str, Any],
    state_a: Mapping[str, Any],
    state_b: Mapping[str, Any],
) -> list[float]:
    wins_a, losses_a, _ = _record(profile_a)
    wins_b, losses_b, _ = _record(profile_b)
    wins_a = int(safe_float(state_a.get("wins"), wins_a))
    losses_a = int(safe_float(state_a.get("losses"), losses_a))
    wins_b = int(safe_float(state_b.get("wins"), wins_b))
    losses_b = int(safe_float(state_b.get("losses"), losses_b))
    ko_a = wins_a * safe_float(profile_a.get("KO %"), 33.0) / 100.0
    ko_b = wins_b * safe_float(profile_b.get("KO %"), 33.0) / 100.0
    sub_a = wins_a * safe_float(profile_a.get("Sub %"), 20.0) / 100.0
    sub_b = wins_b * safe_float(profile_b.get("Sub %"), 20.0) / 100.0

    def difference(field: str, default: float, divisor: float = 1.0) -> float:
        a_value = safe_float(profile_a.get(field), default) / divisor
        b_value = safe_float(profile_b.get(field), default) / divisor
        return b_value - a_value

    return [
        safe_float(state_b.get("elo"), 1500.0) - safe_float(state_a.get("elo"), 1500.0),
        _rate(wins_b, losses_b) - _rate(wins_a, losses_a),
        math.log1p(wins_b + losses_b) - math.log1p(wins_a + losses_a),
        safe_float(state_b.get("win_streak")) - safe_float(state_a.get("win_streak")),
        safe_float(state_b.get("loss_streak")) - safe_float(state_a.get("loss_streak")),
        0.0,
        difference("Age", 30.0),
        difference("Height", 69.0) * 2.54,
        difference("Reach", 72.0) * 2.54,
        difference("SLpM", 4.0),
        difference("Str Acc %", 45.0, 100.0),
        difference("TD Avg", 1.2),
        difference("TD Acc %", 35.0, 100.0),
        difference("Sub Avg", 0.4),
        (ko_b / max(1.0, wins_b)) - (ko_a / max(1.0, wins_a)),
        (sub_b / max(1.0, wins_b)) - (sub_a / max(1.0, wins_a)),
    ]


def _profile_quality(profile: Mapping[str, Any], state: Mapping[str, Any]) -> float:
    fields = ("SLpM", "Str Acc %", "SApM", "Str Def %", "TD Avg", "TD Acc %", "TD Def %", "Sub Avg")
    coverage = sum(safe_float(profile.get(field)) > 0 for field in fields) / len(fields)
    sample = max(safe_float(profile.get("UFC Stat Sample")), safe_float(state.get("fights")))
    return clamp(0.20 + 0.50 * coverage + 0.30 * min(1.0, sample / 8.0), 0.20, 1.0)


def predict_matchup(
    fighter_a: str,
    fighter_b: str,
    profile_a: Mapping[str, Any],
    profile_b: Mapping[str, Any],
    artifact: Mapping[str, Any] | None = None,
    boost_a: float = 0.0,
    boost_b: float = 0.0,
    scheduled_rounds: int = 3,
    market_probability_a: float | None = None,
) -> dict[str, Any]:
    artifact = artifact or {}
    state_a = _lookup_state(artifact, fighter_a)
    state_b = _lookup_state(artifact, fighter_b)
    vector = np.asarray(_profile_feature_vector(profile_a, profile_b, state_a, state_b), dtype=float)
    coefficients = np.asarray(artifact.get("coefficients") or np.zeros(len(FEATURE_NAMES)), dtype=float)
    center = np.asarray(artifact.get("feature_center") or np.zeros(len(FEATURE_NAMES)), dtype=float)
    scale = np.asarray(artifact.get("feature_scale") or np.ones(len(FEATURE_NAMES)), dtype=float)
    if len(coefficients) != len(FEATURE_NAMES):
        coefficients = np.zeros(len(FEATURE_NAMES), dtype=float)
    scale = np.where(np.abs(scale) < 1e-6, 1.0, scale)
    standardized = (vector - center) / scale
    trained_logit = float(standardized @ coefficients)
    prediction_mode = "Independent"
    market_coefficients = np.asarray(artifact.get("market_coefficients") or [], dtype=float)
    if (
        market_probability_a is not None
        and bool(artifact.get("market_validated"))
        and len(market_coefficients) == len(FEATURE_NAMES) + 1
    ):
        market_probability_a = clamp(safe_float(market_probability_a, 0.5), 0.01, 0.99)
        market_logit = math.log(market_probability_a / (1.0 - market_probability_a))
        trained_logit = float(np.append(standardized, market_logit) @ market_coefficients)
        prediction_mode = "Odds-Anchored"

    # Small bounded context layer uses current information absent from the frozen
    # training snapshot. It cannot overpower the historical, leakage-safe model.
    form_delta = safe_float(profile_a.get("Recent Form"), 75.0) - safe_float(profile_b.get("Recent Form"), 75.0)
    sos_delta = safe_float(profile_a.get("Strength of Schedule"), 70.0) - safe_float(profile_b.get("Strength of Schedule"), 70.0)
    cardio_delta = safe_float(profile_a.get("Cardio"), 75.0) - safe_float(profile_b.get("Cardio"), 75.0)
    five_round_multiplier = 1.45 if int(scheduled_rounds) >= 5 else 1.0
    context_logit = clamp(
        form_delta * 0.008 + sos_delta * 0.004 + cardio_delta * 0.003 * five_round_multiplier,
        -0.35,
        0.35,
    )
    manual_logit = clamp((safe_float(boost_a) - safe_float(boost_b)) * 0.045, -0.45, 0.45)
    quality_a = _profile_quality(profile_a, state_a)
    quality_b = _profile_quality(profile_b, state_b)
    quality = (quality_a + quality_b) / 2.0
    raw_probability_a = float(sigmoid(trained_logit + context_logit + manual_logit))
    shrink = 0.58 + 0.42 * quality
    probability_a = clamp(0.5 + (raw_probability_a - 0.5) * shrink, 0.08, 0.92)
    probability_b = 1.0 - probability_a

    wins_a, _, _ = _record(profile_a)
    wins_b, _, _ = _record(profile_b)
    finish_rate_a = clamp((safe_float(profile_a.get("KO %"), 33.0) + safe_float(profile_a.get("Sub %"), 20.0)) / 100.0, 0.15, 0.90)
    finish_rate_b = clamp((safe_float(profile_b.get("KO %"), 33.0) + safe_float(profile_b.get("Sub %"), 20.0)) / 100.0, 0.15, 0.90)
    durability_a = safe_float(profile_a.get("Durability"), 75.0) / 100.0
    durability_b = safe_float(profile_b.get("Durability"), 75.0) / 100.0
    five_round_finish = 0.06 if int(scheduled_rounds) >= 5 else 0.0
    conditional_finish_a = clamp(0.25 + 0.45 * finish_rate_a + 0.18 * (1.0 - durability_b) + five_round_finish, 0.18, 0.82)
    conditional_finish_b = clamp(0.25 + 0.45 * finish_rate_b + 0.18 * (1.0 - durability_a) + five_round_finish, 0.18, 0.82)
    ko_share_a = safe_float(profile_a.get("KO %"), 33.0) / max(1.0, safe_float(profile_a.get("KO %"), 33.0) + safe_float(profile_a.get("Sub %"), 20.0))
    ko_share_b = safe_float(profile_b.get("KO %"), 33.0) / max(1.0, safe_float(profile_b.get("KO %"), 33.0) + safe_float(profile_b.get("Sub %"), 20.0))
    outcomes = [
        probability_a * conditional_finish_a * ko_share_a,
        probability_a * conditional_finish_a * (1.0 - ko_share_a),
        probability_a * (1.0 - conditional_finish_a),
        probability_b * conditional_finish_b * ko_share_b,
        probability_b * conditional_finish_b * (1.0 - ko_share_b),
        probability_b * (1.0 - conditional_finish_b),
    ]
    rounded = [round(value * 100.0, 1) for value in outcomes[:5]]
    rounded.append(round(100.0 - sum(rounded), 1))
    uncertainty = 0.07 + (1.0 - quality) * 0.09
    pick_probability = max(probability_a, probability_b)
    if quality >= 0.75 and pick_probability >= 0.66:
        confidence = "High"
    elif quality >= 0.55 and pick_probability >= 0.59:
        confidence = "Medium"
    elif pick_probability >= 0.55:
        confidence = "Low"
    else:
        confidence = "Tracking"

    contributions = standardized * coefficients
    driver_rows = []
    for index in np.argsort(np.abs(contributions))[::-1][:5]:
        # The model target is Fighter A; positive contribution favors A.
        contribution = float(contributions[index])
        driver_rows.append(
            {
                "feature": FEATURE_NAMES[index],
                "favors": fighter_a if contribution >= 0 else fighter_b,
                "impact": round(abs(contribution), 3),
            }
        )

    return {
        "Fighter A": fighter_a,
        "Fighter B": fighter_b,
        "A Grade": round(50.0 + (probability_a - 0.5) * 100.0, 1),
        "B Grade": round(50.0 + (probability_b - 0.5) * 100.0, 1),
        "A Win %": round(probability_a * 100.0, 1),
        "B Win %": round(probability_b * 100.0, 1),
        "A KO/TKO %": rounded[0],
        "A Submission %": rounded[1],
        "A Decision %": rounded[2],
        "B KO/TKO %": rounded[3],
        "B Submission %": rounded[4],
        "B Decision %": rounded[5],
        "Confidence": confidence,
        "Predicted Winner": fighter_a if probability_a >= 0.5 else fighter_b,
        "Model Quality": round(quality * 100.0),
        "Model Version": artifact.get("model_version", MODEL_VERSION),
        "Decision Policy": artifact.get("decision_policy_version", DECISION_POLICY_VERSION),
        "Validated": bool(artifact.get("validated")),
        "Market Validated": bool(artifact.get("market_validated")),
        "Prediction Mode": prediction_mode,
        "A Striking Path": round(-contributions[9] - contributions[10], 2),
        "B Striking Path": round(contributions[9] + contributions[10], 2),
        "A Grappling Path": round(-contributions[11] - contributions[12] - contributions[13], 2),
        "B Grappling Path": round(contributions[11] + contributions[12] + contributions[13], 2),
        "Style Score Diff": round(trained_logit + context_logit + manual_logit, 3),
        "Uncertainty Low %": round(max(50.0, (pick_probability - uncertainty) * 100.0), 1),
        "Uncertainty High %": round(min(92.0, (pick_probability + uncertainty) * 100.0), 1),
        "Top Signals": driver_rows,
        "Historical Fights A": int(safe_float(state_a.get("fights"), wins_a)),
        "Historical Fights B": int(safe_float(state_b.get("fights"), wins_b)),
    }
