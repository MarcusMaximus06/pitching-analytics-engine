from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from daily_ncaaf_auto import get_or_create_log_worksheet
from ncaaf_model import blend_with_market, normalize_team_name, safe_float


def _probability(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100.0 if abs(number) > 1.0 else number


def _winner(home: str, away: str, home_probability: float) -> str:
    return home if home_probability >= 0.5 else away


def _accuracy(rows: Sequence[Mapping[str, Any]], pick_key: str) -> float | None:
    if not rows:
        return None
    return sum(
        normalize_team_name(row[pick_key]) == normalize_team_name(row["actual"])
        for row in rows
    ) / len(rows)


def _brier(rows: Sequence[Mapping[str, Any]], probability_key: str) -> float | None:
    if not rows:
        return None
    return sum(
        (float(row[probability_key]) - float(row["home_won"])) ** 2
        for row in rows
    ) / len(rows)


def analyze_rows(values: Sequence[Sequence[Any]]) -> dict[str, Any]:
    if not values:
        return {"graded": 0, "with_vegas": 0}
    headers = [str(value) for value in values[0]]
    records: list[dict[str, Any]] = []
    for raw in values[1:]:
        row = dict(zip(headers, list(raw) + [""] * max(0, len(headers) - len(raw))))
        if str(row.get("Result", "")).upper() not in {"WIN", "LOSS"}:
            continue
        home = str(row.get("Home Team") or "")
        away = str(row.get("Away Team") or "")
        actual = str(row.get("Actual Winner") or "")
        model_home = _probability(row.get("Model Home %"))
        market_home = _probability(row.get("Vegas Home %"))
        if not home or not away or not actual or model_home is None:
            continue
        record = {
            "date": row.get("Date"),
            "away": away,
            "home": home,
            "actual": actual,
            "home_won": normalize_team_name(actual) == normalize_team_name(home),
            "model_home": model_home,
            "model_pick": _winner(home, away, model_home),
            "confidence": str(row.get("Confidence") or "Unclassified"),
            "feature_snapshot": str(row.get("Feature Snapshot") or ""),
        }
        if market_home is not None:
            record["market_home"] = market_home
            record["market_pick"] = _winner(home, away, market_home)
        records.append(record)

    market_rows = [row for row in records if "market_home" in row]
    disagreement = [row for row in market_rows if row["model_pick"] != row["market_pick"]]
    agreement = [row for row in market_rows if row["model_pick"] == row["market_pick"]]
    weights: dict[str, Any] = {}
    for weight in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
        blended = []
        for row in market_rows:
            probability = blend_with_market(row["model_home"], row["market_home"], weight)
            blended.append(
                {
                    **row,
                    "blend_home": probability,
                    "blend_pick": _winner(row["home"], row["away"], probability),
                }
            )
        weights[f"{weight:.2f}"] = {
            "accuracy": _accuracy(blended, "blend_pick"),
            "brier": _brier(blended, "blend_home"),
        }

    confidence: dict[str, dict[str, Any]] = {}
    for tier in sorted({row["confidence"] for row in records}):
        group = [row for row in records if row["confidence"] == tier]
        confidence[tier] = {
            "games": len(group),
            "model_accuracy": _accuracy(group, "model_pick"),
            "model_brier": _brier(group, "model_home"),
        }

    edge_bins: dict[str, dict[str, Any]] = {}
    boundaries = (("0-5pp", 0.0, 0.05), ("5-10pp", 0.05, 0.10), ("10-20pp", 0.10, 0.20), ("20pp+", 0.20, math.inf))
    for label, lower, upper in boundaries:
        group = [row for row in market_rows if lower <= abs(row["model_home"] - row["market_home"]) < upper]
        edge_bins[label] = {
            "games": len(group),
            "model_accuracy": _accuracy(group, "model_pick"),
            "vegas_accuracy": _accuracy(group, "market_pick"),
        }

    zero_features: defaultdict[str, int] = defaultdict(int)
    snapshots = 0
    for row in records:
        try:
            snapshot = json.loads(row["feature_snapshot"])
        except (TypeError, ValueError):
            continue
        snapshots += 1
        for feature, value in snapshot.items():
            if abs(safe_float(value)) < 1e-12:
                zero_features[str(feature)] += 1

    misses = sorted(
        (
            {
                "matchup": f"{row['away']} @ {row['home']}",
                "actual": row["actual"],
                "model_pick": row["model_pick"],
                "model_pick_probability": max(row["model_home"], 1.0 - row["model_home"]),
                "vegas_pick": row.get("market_pick"),
                "vegas_pick_probability": (
                    max(row["market_home"], 1.0 - row["market_home"])
                    if "market_home" in row
                    else None
                ),
            }
            for row in records
            if normalize_team_name(row["model_pick"]) != normalize_team_name(row["actual"])
        ),
        key=lambda row: row["model_pick_probability"],
        reverse=True,
    )

    return {
        "graded": len(records),
        "with_vegas": len(market_rows),
        "independent_model": {
            "accuracy": _accuracy(records, "model_pick"),
            "brier": _brier(records, "model_home"),
        },
        "vegas": {
            "accuracy": _accuracy(market_rows, "market_pick"),
            "brier": _brier(market_rows, "market_home"),
        },
        "agreement": {
            "games": len(agreement),
            "accuracy": _accuracy(agreement, "model_pick"),
        },
        "disagreement": {
            "games": len(disagreement),
            "model_accuracy": _accuracy(disagreement, "model_pick"),
            "vegas_accuracy": _accuracy(disagreement, "market_pick"),
            "details": [
                {
                    "matchup": f"{row['away']} @ {row['home']}",
                    "actual": row["actual"],
                    "model_pick": row["model_pick"],
                    "model_pick_probability": max(row["model_home"], 1.0 - row["model_home"]),
                    "vegas_pick": row["market_pick"],
                    "vegas_pick_probability": max(row["market_home"], 1.0 - row["market_home"]),
                }
                for row in disagreement
            ],
        },
        "blend_weights": weights,
        "confidence": confidence,
        "absolute_model_market_gap": edge_bins,
        "zero_feature_rates": {
            feature: count / snapshots
            for feature, count in sorted(zero_features.items())
            if snapshots and count / snapshots >= 0.5
        },
        "largest_model_misses": misses[:12],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze immutable NCAA prediction-log results.")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    worksheet = get_or_create_log_worksheet()
    report = analyze_rows(worksheet.get_all_values())
    print(json.dumps(report, indent=None if args.compact else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
