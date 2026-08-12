"""Backfill CFBD data and train the HagLabs NCAA winner model.

This is intentionally a local, rerunnable process. API responses are cached by
season so interrupted runs resume without consuming the same quota again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ncaaf_model import (
    CFBDDataClient,
    build_current_states,
    build_historical_feature_rows,
    build_season_context,
    fit_final_model,
    regress_states_for_new_season,
    safe_float,
    walk_forward_backtest,
)

ENDPOINTS = {
    "games": ("/games", {"seasonType": "both", "classification": "fbs"}),
    "advanced": ("/stats/game/advanced", {"excludeGarbageTime": "true", "seasonType": "both"}),
    "havoc": ("/stats/game/havoc", {"seasonType": "both"}),
    "returning": ("/player/returning", {}),
    "talent": ("/talent", {}),
    "lines": ("/lines", {"seasonType": "both"}),
    "weather": ("/games/weather", {"seasonType": "both", "classification": "fbs"}),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the HagLabs NCAA football winner model")
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year - 1)
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "data" / "ncaaf" / "raw")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "ncaaf")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached season payloads")
    parser.add_argument(
        "--source",
        choices=("auto", "cfbd", "public"),
        default="auto",
        help="Use CFBD, official public SportsDataverse releases, or auto-select CFBD when configured",
    )
    return parser.parse_args()


def cache_path(cache_dir: Path, name: str, year: int | None = None) -> Path:
    suffix = str(year) if year is not None else "static"
    return cache_dir / f"{name}_{suffix}.json"


def load_or_fetch(
    client: CFBDDataClient,
    cache_dir: Path,
    name: str,
    path: str,
    params: dict[str, Any],
    year: int | None,
    refresh: bool,
) -> list[dict[str, Any]]:
    target = cache_path(cache_dir, name, year)
    if target.exists() and not refresh:
        return json.loads(target.read_text(encoding="utf-8"))
    payload = client.get(path, **params)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
    return payload


def load_optional(
    client: CFBDDataClient,
    cache_dir: Path,
    name: str,
    path: str,
    params: dict[str, Any],
    year: int,
    refresh: bool,
) -> list[dict[str, Any]]:
    try:
        return load_or_fetch(client, cache_dir, name, path, params, year, refresh)
    except (requests.RequestException, RuntimeError, TypeError, ValueError) as exc:
        print(f"[{year}] optional {name} data unavailable: {exc}", flush=True)
        return []


PUBLIC_RELEASES = {
    "schedule": ("espn_cfb_schedules", "cfb_schedule_{year}.parquet"),
    "team_box": ("espn_cfb_team_box", "team_box_{year}.parquet"),
    "betting": ("espn_cfb_betting", "betting_{year}.parquet"),
}


def download_public_release(cache_dir: Path, dataset: str, year: int, refresh: bool) -> Path:
    tag, filename_template = PUBLIC_RELEASES[dataset]
    filename = filename_template.format(year=year)
    target = cache_dir / "public" / filename
    if target.exists() and not refresh:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tag}/{filename}"
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(f"Public {dataset} release for {year} failed with HTTP {response.status_code}")
    target.write_bytes(response.content)
    return target


def _ratio(value: Any) -> float:
    try:
        made, attempts = str(value).split("-", 1)
        return float(made) / max(1.0, float(attempts))
    except (TypeError, ValueError):
        return 0.0


def _pass_attempts(value: Any) -> float:
    try:
        return float(str(value).split("/", 1)[1])
    except (IndexError, TypeError, ValueError):
        return 0.0


def public_training_payload(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], pd.DataFrame]:
    games: list[dict[str, Any]] = []
    advanced: list[dict[str, Any]] = []
    betting_frames = []
    for year in range(args.start_year, args.end_year + 1):
        print(f"[{year}] loading official public schedule, box, and betting releases", flush=True)
        schedule = pd.read_parquet(download_public_release(args.cache_dir, "schedule", year, args.refresh))
        team_box = pd.read_parquet(download_public_release(args.cache_dir, "team_box", year, args.refresh))
        betting_frames.append(pd.read_parquet(download_public_release(args.cache_dir, "betting", year, args.refresh)))
        schedule = schedule[
            schedule["home_score"].notna()
            & schedule["away_score"].notna()
            & (schedule["home_score"] != schedule["away_score"])
        ]
        for row in schedule.to_dict("records"):
            games.append(
                {
                    "id": int(row["game_id"]),
                    "season": int(row["season"]),
                    "week": int(row["week"]),
                    "season_type": str(row.get("season_type") or "regular"),
                    "start_date": row["game_date"],
                    "neutral_site": bool(row.get("neutral_site")),
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "home_points": float(row["home_score"]),
                    "away_points": float(row["away_score"]),
                }
            )
        boxes_by_game: dict[int, list[dict[str, Any]]] = {}
        for row in team_box.to_dict("records"):
            boxes_by_game.setdefault(int(row["game_id"]), []).append(row)
        for game_id, boxes in boxes_by_game.items():
            for box in boxes:
                opponent = next((item for item in boxes if item["team_name"] != box["team_name"]), {})
                rush_attempts = safe_float(box.get("rushingAttempts"))
                pass_attempts = _pass_attempts(box.get("completionAttempts"))
                plays = max(1.0, rush_attempts + pass_attempts)
                yards_per_play = safe_float(box.get("totalYards")) / plays
                opponent_rush_attempts = safe_float(opponent.get("rushingAttempts"))
                opponent_pass_attempts = _pass_attempts(opponent.get("completionAttempts"))
                opponent_plays = max(1.0, opponent_rush_attempts + opponent_pass_attempts)
                turnovers_forced = safe_float(opponent.get("turnovers"))
                advanced.append(
                    {
                        "game_id": game_id,
                        "season": year,
                        "team": box["team_name"],
                        "opponent": opponent.get("team_name", ""),
                        "offense": {
                            "ppa": (yards_per_play - 5.4) / 5.4,
                            "success_rate": _ratio(box.get("thirdDownEff")),
                            "explosiveness": (
                                0.55 * safe_float(box.get("yardsPerPass"))
                                + 0.45 * safe_float(box.get("yardsPerRushAttempt"))
                            )
                            / 6.0,
                            "plays": plays,
                        },
                        "defense": {
                            "success_rate": _ratio(opponent.get("thirdDownEff")),
                        },
                        "havoc": {"defense": {"total": turnovers_forced / opponent_plays}},
                    }
                )
    return games, advanced, pd.concat(betting_frames, ignore_index=True)


def train_from_public_releases(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any], Any]:
    games, advanced, betting = public_training_payload(args)
    havoc = [
        {
            "game_id": row["game_id"],
            "team": row["team"],
            "defense": row["havoc"]["defense"],
        }
        for row in advanced
    ]
    features = build_historical_feature_rows(games, advanced=advanced, havoc=havoc)
    spread_map = {
        int(row["game_id"]): safe_float(row.get("home_team_spread"), float("nan"))
        for row in betting.to_dict("records")
        if bool(row.get("game_spread_available"))
    }
    features["market_home_probability"] = features["game_id"].map(
        lambda game_id: float(1.0 / (1.0 + np.exp(spread_map[int(game_id)] / 6.5)))
        if int(game_id) in spread_map and np.isfinite(spread_map[int(game_id)])
        else np.nan
    )
    backtest = walk_forward_backtest(features)
    model = fit_final_model(features, backtest)
    current_states = build_current_states(games, advanced=advanced, havoc=havoc)
    regress_states_for_new_season(current_states)
    model.metadata.update(
        {
            "data_source": "SportsDataverse ESPN college-football releases",
            "public_bootstrap": True,
            "data_limitations": "Roster, coach, venue, and weather coefficients require the CFBD enrichment build.",
            "team_states": {
                team: vars(state)
                for team, state in current_states.items()
            },
            "team_state_season": args.end_year + 1,
        }
    )
    return features, backtest, model


def main() -> int:
    args = parse_args()
    if args.start_year > args.end_year:
        raise SystemExit("--start-year must not exceed --end-year")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("CFBD_API_KEY", "").strip()
    source = "cfbd" if args.source == "auto" and api_key else args.source
    if source in {"auto", "public"}:
        features, backtest, model = train_from_public_releases(args)
        return write_outputs(args, features, backtest, model)
    if not api_key:
        raise SystemExit("CFBD_API_KEY is required for --source cfbd. Configure it locally; never pass it on the command line.")
    client = CFBDDataClient(api_key)

    venues = load_or_fetch(client, args.cache_dir, "venues", "/venues", {}, None, args.refresh)
    teams = load_or_fetch(client, args.cache_dir, "teams", "/teams", {}, None, args.refresh)
    all_games: list[dict[str, Any]] = []
    all_advanced: list[dict[str, Any]] = []
    all_havoc: list[dict[str, Any]] = []
    all_weather: list[dict[str, Any]] = []
    all_lines: list[dict[str, Any]] = []
    contexts = {}

    for year in range(args.start_year, args.end_year + 1):
        print(f"[{year}] fetching or loading cached CFBD payloads", flush=True)
        payloads: dict[str, list[dict[str, Any]]] = {}
        for name, (path, fixed_params) in ENDPOINTS.items():
            params = dict(fixed_params)
            params["year"] = year
            loader = load_or_fetch if name == "games" else load_optional
            payloads[name] = loader(client, args.cache_dir, name, path, params, year, args.refresh)
        payloads["coaches"] = load_optional(
            client,
            args.cache_dir,
            "coaches",
            "/coaches",
            {"minYear": year, "maxYear": year},
            year,
            args.refresh,
        )
        payloads["teams"] = teams
        contexts[year] = build_season_context(
            year,
            returning=payloads["returning"],
            talent=payloads["talent"],
            coaches=payloads["coaches"],
            teams=payloads["teams"],
            venues=venues,
        )
        all_games.extend(payloads["games"])
        all_advanced.extend(payloads["advanced"])
        all_havoc.extend(payloads["havoc"])
        all_weather.extend(payloads["weather"])
        all_lines.extend(payloads["lines"])

    print("Building pregame feature snapshots", flush=True)
    features = build_historical_feature_rows(
        all_games,
        contexts=contexts,
        advanced=all_advanced,
        havoc=all_havoc,
        weather=all_weather,
        lines=all_lines,
    )
    if features.empty:
        raise SystemExit("CFBD returned no completed games; model was not trained")
    print(f"Running rolling-origin validation on {len(features):,} games", flush=True)
    backtest = walk_forward_backtest(features)
    model = fit_final_model(features, backtest)
    model.metadata["data_source"] = "CollegeFootballData API"
    model.metadata["public_bootstrap"] = False
    return write_outputs(args, features, backtest, model)


def write_outputs(args: argparse.Namespace, features: pd.DataFrame, backtest: dict[str, Any], model: Any) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    features_path = args.output_dir / "features.parquet"
    features.to_parquet(features_path, index=False)
    backtest_path = args.output_dir / "backtest.json"
    backtest_path.write_text(json.dumps(backtest, indent=2, default=str), encoding="utf-8")
    model.metadata["data_generated_at"] = datetime.now(timezone.utc).isoformat()
    model.metadata["feature_store"] = str(features_path.relative_to(PROJECT_ROOT))
    model_path = args.output_dir / "model.json"
    model.save(model_path)

    summary = {
        "model_path": str(model_path),
        "feature_rows": len(features),
        "training_seasons": model.metadata.get("training_seasons"),
        "walk_forward_games": backtest.get("games", 0),
        "model_metrics": backtest.get("model", {}),
        "elo_metrics": backtest.get("elo", {}),
        "market_metrics": backtest.get("market", {}),
        "market_blend_metrics": backtest.get("market_blend", {}),
        "market_deltas": backtest.get("market_deltas", {}),
        "market_weight": backtest.get("market_weight"),
        "validated": model.metadata.get("validated", False),
        "market_validated": model.metadata.get("market_validated", False),
    }
    (args.output_dir / "build_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
