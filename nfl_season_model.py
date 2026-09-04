"""NFL remaining-season record forecasts for HagLabs.

Completed games are fixed before any simulation begins. Remaining games use an
updated Elo probability blended with a no-vig market probability when the live
board contains the matchup. The market is context, not evidence that HagLabs
has discovered a betting edge.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import requests

MODEL_VERSION = "nfl-season-record-v1.0.0"
REGULAR_SEASON_GAMES = 17

AFC_TEAMS = {
    "Baltimore Ravens", "Buffalo Bills", "Cincinnati Bengals", "Cleveland Browns",
    "Denver Broncos", "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars",
    "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers", "Miami Dolphins",
    "New England Patriots", "New York Jets", "Pittsburgh Steelers", "Tennessee Titans",
}


def normalize_team_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def current_nfl_season(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(str(value).replace("%", "").replace("+", "").strip())
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def parse_espn_schedule(payloads: Sequence[Mapping[str, Any]], season: int) -> pd.DataFrame:
    """Normalize ESPN scoreboard payloads into one regular-season schedule."""
    events: dict[str, Mapping[str, Any]] = {}
    for payload in payloads:
        for event in payload.get("events") or []:
            event_season = event.get("season") or {}
            if int(event_season.get("year") or 0) != season:
                continue
            if str(event_season.get("slug") or "") != "regular-season":
                continue
            events[str(event.get("id") or "")] = event

    rows: list[dict[str, Any]] = []
    for event in events.values():
        competition = ((event.get("competitions") or [{}])[0])
        competitors = competition.get("competitors") or []
        if len(competitors) != 2:
            continue
        sides: dict[str, Mapping[str, Any]] = {
            str(item.get("homeAway") or ""): item for item in competitors
        }
        home_item = sides.get("home") or {}
        away_item = sides.get("away") or {}
        home_team = str((home_item.get("team") or {}).get("displayName") or "")
        away_team = str((away_item.get("team") or {}).get("displayName") or "")
        if not home_team or not away_team:
            continue
        status = (event.get("status") or {}).get("type") or {}
        completed = bool(status.get("completed")) or status.get("state") == "post"
        home_score = _safe_float(home_item.get("score"), float("nan"))
        away_score = _safe_float(away_item.get("score"), float("nan"))
        rows.append(
            {
                "game_id": str(event.get("id") or ""),
                "season": season,
                "week": int((event.get("week") or {}).get("number") or 0),
                "start_date": str(event.get("date") or ""),
                "away_team": away_team,
                "home_team": home_team,
                "neutral_site": bool(competition.get("neutralSite")),
                "completed": completed and math.isfinite(home_score) and math.isfinite(away_score),
                "away_score": away_score if math.isfinite(away_score) else np.nan,
                "home_score": home_score if math.isfinite(home_score) else np.nan,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["week", "start_date", "game_id"]).reset_index(drop=True)


def fetch_espn_nfl_schedule(
    season: int,
    session: requests.Session | Any = requests,
) -> pd.DataFrame:
    """Fetch the full season, including January games in the next calendar year."""
    payloads = []
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    for calendar_year in (season, season + 1):
        response = session.get(url, params={"dates": str(calendar_year), "limit": 1000}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"ESPN NFL schedule returned HTTP {response.status_code}")
        payloads.append(response.json())
    return parse_espn_schedule(payloads, season)


def _initial_elos(ratings: Mapping[str, Any], teams: Sequence[str]) -> dict[str, float]:
    output: dict[str, float] = {}
    for team in teams:
        value = ratings.get(team, 1500.0)
        if isinstance(value, Mapping):
            value = value.get("elo", 1500.0)
        output[team] = _safe_float(value, 1500.0)
    return output


def _elo_probability(home_elo: float, away_elo: float, neutral: bool = False) -> float:
    home_advantage = 0.0 if neutral else 42.0
    return 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo - home_advantage) / 400.0))


def update_elos_from_results(schedule: pd.DataFrame, ratings: Mapping[str, Any]) -> dict[str, float]:
    teams = sorted(set(schedule.get("home_team", [])) | set(schedule.get("away_team", [])))
    elos = _initial_elos(ratings, teams)
    completed = schedule[schedule["completed"].astype(bool)].sort_values(["week", "start_date"])
    for row in completed.to_dict("records"):
        home, away = row["home_team"], row["away_team"]
        expected = _elo_probability(elos[home], elos[away], bool(row.get("neutral_site")))
        margin = float(row["home_score"]) - float(row["away_score"])
        outcome = 0.5 if margin == 0 else float(margin > 0)
        multiplier = max(1.0, math.log(abs(margin) + 1.0))
        change = 18.0 * multiplier * (outcome - expected)
        elos[home] += change
        elos[away] -= change
    return elos


def market_probability_index(odds_board: pd.DataFrame | None) -> dict[tuple[str, str], float]:
    if odds_board is None or odds_board.empty:
        return {}
    output: dict[tuple[str, str], float] = {}
    for row in odds_board.to_dict("records"):
        away = normalize_team_name(row.get("Away Team"))
        home = normalize_team_name(row.get("Home Team"))
        probability = _safe_float(row.get("Vegas Home %"), float("nan")) / 100.0
        if away and home and math.isfinite(probability) and 0.0 < probability < 1.0:
            output[(away, home)] = probability
    return output


def _logit_blend(model_probability: float, market_probability: float, market_weight: float) -> float:
    model_probability = float(np.clip(model_probability, 0.02, 0.98))
    market_probability = float(np.clip(market_probability, 0.02, 0.98))
    model_logit = math.log(model_probability / (1.0 - model_probability))
    market_logit = math.log(market_probability / (1.0 - market_probability))
    value = (1.0 - market_weight) * model_logit + market_weight * market_logit
    return 1.0 / (1.0 + math.exp(-value))


def build_game_probabilities(
    schedule: pd.DataFrame,
    ratings: Mapping[str, Any],
    odds_board: pd.DataFrame | None = None,
    market_weight: float = 0.70,
) -> tuple[pd.DataFrame, dict[str, float]]:
    elos = update_elos_from_results(schedule, ratings)
    markets = market_probability_index(odds_board)
    rows: list[dict[str, Any]] = []
    for row in schedule.to_dict("records"):
        home, away = row["home_team"], row["away_team"]
        model_home = _elo_probability(elos[home], elos[away], bool(row.get("neutral_site")))
        market_home = markets.get((normalize_team_name(away), normalize_team_name(home)))
        final_home = (
            _logit_blend(model_home, market_home, market_weight)
            if market_home is not None and not row["completed"]
            else model_home
        )
        rows.append(
            {
                **row,
                "elo_home_probability": model_home,
                "market_home_probability": market_home,
                "home_win_probability": final_home,
                "probability_source": "Market + updated Elo" if market_home is not None else "Updated Elo",
            }
        )
    return pd.DataFrame(rows), elos


def simulate_season_records(
    schedule: pd.DataFrame,
    ratings: Mapping[str, Any],
    odds_board: pd.DataFrame | None = None,
    simulations: int = 20_000,
    seed: int = 20260904,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return team summary, weekly expected records, and game probabilities."""
    if schedule.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    games, elos = build_game_probabilities(schedule, ratings, odds_board)
    teams = sorted(set(games["home_team"]) | set(games["away_team"]))
    team_index = {team: index for index, team in enumerate(teams)}
    actual_wins = np.zeros(len(teams), dtype=float)
    actual_losses = np.zeros(len(teams), dtype=float)
    actual_ties = np.zeros(len(teams), dtype=float)
    sim_wins = np.zeros((simulations, len(teams)), dtype=np.float32)
    rng = np.random.default_rng(seed)

    for row in games.to_dict("records"):
        home_index = team_index[row["home_team"]]
        away_index = team_index[row["away_team"]]
        if row["completed"]:
            margin = float(row["home_score"]) - float(row["away_score"])
            if margin > 0:
                actual_wins[home_index] += 1
                actual_losses[away_index] += 1
                sim_wins[:, home_index] += 1
            elif margin < 0:
                actual_wins[away_index] += 1
                actual_losses[home_index] += 1
                sim_wins[:, away_index] += 1
            else:
                actual_ties[[home_index, away_index]] += 1
                sim_wins[:, [home_index, away_index]] += 0.5
            continue
        home_wins = rng.random(simulations) < float(row["home_win_probability"])
        sim_wins[:, home_index] += home_wins
        sim_wins[:, away_index] += ~home_wins

    playoff = np.zeros((simulations, len(teams)), dtype=bool)
    for conference_teams in (AFC_TEAMS, set(teams) - AFC_TEAMS):
        indexes = [team_index[team] for team in teams if team in conference_teams]
        if not indexes:
            continue
        tie_break = rng.random((simulations, len(indexes))) * 1e-4
        order = np.argsort(-(sim_wins[:, indexes] + tie_break), axis=1)[:, :7]
        for position, team_column in enumerate(indexes):
            playoff[:, team_column] = np.any(order == position, axis=1)

    summary_rows = []
    for team, index in team_index.items():
        values = sim_wins[:, index]
        games_played = int(actual_wins[index] + actual_losses[index] + actual_ties[index])
        summary_rows.append(
            {
                "Team": team,
                "Current Record": f"{int(actual_wins[index])}-{int(actual_losses[index])}"
                + (f"-{int(actual_ties[index])}" if actual_ties[index] else ""),
                "Games Played": games_played,
                "Projected Wins": float(values.mean()),
                "Projected Losses": float(REGULAR_SEASON_GAMES - values.mean()),
                "Median Wins": round(float(np.median(values))),
                "80% Win Range": f"{int(np.quantile(values, 0.10))}-{int(np.quantile(values, 0.90))}",
                "Playoff Probability": float(playoff[:, index].mean()),
                "Updated Elo": round(elos.get(team, 1500.0)),
            }
        )

    weekly_rows = []
    for team in teams:
        cumulative_wins = 0.0
        cumulative_losses = 0.0
        cumulative_ties = 0.0
        team_games = games[(games["home_team"] == team) | (games["away_team"] == team)]
        for week in range(1, 19):
            week_games = team_games[team_games["week"] == week]
            for row in week_games.to_dict("records"):
                is_home = row["home_team"] == team
                if row["completed"]:
                    team_score = float(row["home_score"] if is_home else row["away_score"])
                    opponent_score = float(row["away_score"] if is_home else row["home_score"])
                    if team_score > opponent_score:
                        cumulative_wins += 1.0
                    elif team_score < opponent_score:
                        cumulative_losses += 1.0
                    else:
                        cumulative_ties += 1.0
                else:
                    win_probability = (
                        float(row["home_win_probability"])
                        if is_home
                        else 1.0 - float(row["home_win_probability"])
                    )
                    cumulative_wins += win_probability
                    cumulative_losses += 1.0 - win_probability
            weekly_rows.append(
                {
                    "Team": team,
                    "Week": week,
                    "Expected Wins": cumulative_wins,
                    "Expected Losses": cumulative_losses,
                    "Ties": cumulative_ties,
                }
            )

    summary = pd.DataFrame(summary_rows).sort_values(
        ["Projected Wins", "Playoff Probability"], ascending=False
    ).reset_index(drop=True)
    weekly = pd.DataFrame(weekly_rows)
    return summary, weekly, games
