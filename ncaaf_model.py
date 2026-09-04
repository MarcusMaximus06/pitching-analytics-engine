"""Leakage-safe NCAA football winner modeling utilities.

The module deliberately keeps data acquisition, feature snapshots, model fitting,
market comparison, and presentation separate.  Every training row is created
before the corresponding game updates either team's state.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pandas as pd
import requests

MODEL_VERSION = "ncaaf-winner-v1.1.0"

FEATURE_NAMES = (
    "elo_diff_100",
    "margin_ema_diff_10",
    "offense_ppg_diff_10",
    "defense_ppg_adv_10",
    "win_ema_diff",
    "offense_ppa_diff",
    "defense_success_adv",
    "explosiveness_diff",
    "havoc_adv",
    "rest_diff_7",
    "talent_diff_100",
    "returning_diff",
    "coach_tenure_diff_5",
    "home_field",
    "travel_diff_1000",
    "elevation_1000",
    "wind_20",
    "precipitation",
    "temperature_extreme",
)

FEATURE_LABELS = {
    "elo_diff_100": "opponent-adjusted team strength",
    "margin_ema_diff_10": "recent scoring margin",
    "offense_ppg_diff_10": "recent offense",
    "defense_ppg_adv_10": "recent defense",
    "win_ema_diff": "recent results",
    "offense_ppa_diff": "offensive PPA",
    "defense_success_adv": "defensive success rate",
    "explosiveness_diff": "explosiveness",
    "havoc_adv": "havoc rate",
    "rest_diff_7": "rest advantage",
    "talent_diff_100": "roster talent",
    "returning_diff": "returning production",
    "coach_tenure_diff_5": "coaching continuity",
    "home_field": "home field",
    "travel_diff_1000": "travel burden",
    "elevation_1000": "venue elevation",
    "wind_20": "wind",
    "precipitation": "precipitation",
    "temperature_extreme": "extreme temperature",
}

TEAM_ALIASES = {
    "miami fl": "miami",
    "miami florida": "miami",
    "miami hurricanes": "miami",
    "nc state wolfpack": "north carolina state",
    "nc state": "north carolina state",
    "ole miss rebels": "mississippi",
    "ole miss": "mississippi",
    "usc trojans": "usc",
    "southern california": "usc",
    "ucf knights": "ucf",
    "central florida": "ucf",
    "lsu tigers": "lsu",
    "louisiana state": "lsu",
    "byu cougars": "byu",
    "brigham young": "byu",
    "utsa roadrunners": "utsa",
    "texas san antonio": "utsa",
    "uab blazers": "uab",
    "alabama birmingham": "uab",
    "smu mustangs": "smu",
    "southern methodist": "smu",
}


def as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return dict(vars(value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sigmoid(value: Any) -> Any:
    array = np.asarray(value, dtype=float)
    output = np.empty_like(array)
    positive = array >= 0
    output[positive] = 1.0 / (1.0 + np.exp(-array[positive]))
    exp_value = np.exp(array[~positive])
    output[~positive] = exp_value / (1.0 + exp_value)
    return output.item() if output.ndim == 0 else output


def normalize_team_name(name: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    text = re.sub(r"\b(university|college|football)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return TEAM_ALIASES.get(text, text)


def match_team_name(name: str, candidates: Iterable[str]) -> str | None:
    target = normalize_team_name(name)
    normalized = {normalize_team_name(candidate): candidate for candidate in candidates}
    if target in normalized:
        return normalized[target]
    contained = [
        candidate
        for key, candidate in normalized.items()
        if len(target) >= 4 and (target in key or key in target)
    ]
    return contained[0] if len(contained) == 1 else None


def american_implied_probability(odds: float | None) -> float | None:
    value = safe_float(odds, default=float("nan"))
    if not math.isfinite(value) or value == 0:
        return None
    if value > 0:
        return 100.0 / (value + 100.0)
    return -value / (-value + 100.0)


def fair_american_odds(probability: float) -> int:
    probability = clamp(float(probability), 0.001, 0.999)
    if probability >= 0.5:
        return round(-100.0 * probability / (1.0 - probability))
    return round(100.0 * (1.0 - probability) / probability)


def devig_two_way(home_odds: float, away_odds: float) -> tuple[float, float, float]:
    home_raw = american_implied_probability(home_odds)
    away_raw = american_implied_probability(away_odds)
    if home_raw is None or away_raw is None:
        raise ValueError("Both moneylines must be valid non-zero American odds")
    total = home_raw + away_raw
    return home_raw / total, away_raw / total, total - 1.0


def market_consensus(odds_game: Mapping[str, Any]) -> dict[str, Any] | None:
    home = str(odds_game.get("home_team") or "")
    away = str(odds_game.get("away_team") or "")
    if not home or not away:
        return None

    observations: list[dict[str, Any]] = []
    best_home: float | None = None
    best_away: float | None = None
    for bookmaker in odds_game.get("bookmakers") or []:
        book = as_mapping(bookmaker)
        market = next(
            (as_mapping(item) for item in book.get("markets") or [] if as_mapping(item).get("key") == "h2h"),
            None,
        )
        if not market:
            continue
        outcomes = {str(as_mapping(item).get("name")): as_mapping(item).get("price") for item in market.get("outcomes") or []}
        if home not in outcomes or away not in outcomes:
            continue
        home_odds = safe_float(outcomes[home], default=float("nan"))
        away_odds = safe_float(outcomes[away], default=float("nan"))
        if not math.isfinite(home_odds) or not math.isfinite(away_odds):
            continue
        try:
            home_prob, away_prob, vig = devig_two_way(home_odds, away_odds)
        except ValueError:
            continue
        observations.append(
            {
                "bookmaker": book.get("title") or book.get("key") or "Unknown",
                "home_probability": home_prob,
                "away_probability": away_prob,
                "vig": vig,
                "updated_at": market.get("last_update") or book.get("last_update"),
            }
        )
        best_home = home_odds if best_home is None else max(best_home, home_odds)
        best_away = away_odds if best_away is None else max(best_away, away_odds)

    if not observations:
        return None
    home_probability = float(median(item["home_probability"] for item in observations))
    away_probability = 1.0 - home_probability
    return {
        "home_team": home,
        "away_team": away,
        "home_probability": home_probability,
        "away_probability": away_probability,
        "home_odds": best_home,
        "away_odds": best_away,
        "book_count": len(observations),
        "median_vig": float(median(item["vig"] for item in observations)),
        "observed_at": max((str(item["updated_at"] or "") for item in observations), default=""),
        "books": [item["bookmaker"] for item in observations],
    }


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_espn_scoreboards(
    payloads: Sequence[Mapping[str, Any]],
    season: int,
) -> list[dict[str, Any]]:
    """Normalize current ESPN scoreboard payloads into stable game records."""
    events: dict[str, Mapping[str, Any]] = {}
    for payload in payloads:
        for raw_event in payload.get("events") or []:
            event = as_mapping(raw_event)
            event_season = as_mapping(event.get("season"))
            if safe_int(event_season.get("year")) != int(season):
                continue
            event_id = str(event.get("id") or "")
            if event_id:
                events[event_id] = event

    games: list[dict[str, Any]] = []
    for event_id, event in events.items():
        competition = as_mapping((event.get("competitions") or [{}])[0])
        competitors = [as_mapping(item) for item in competition.get("competitors") or []]
        sides = {str(item.get("homeAway") or ""): item for item in competitors}
        home_item = sides.get("home", {})
        away_item = sides.get("away", {})
        home_team_data = as_mapping(home_item.get("team"))
        away_team_data = as_mapping(away_item.get("team"))
        home_team = str(home_team_data.get("location") or home_team_data.get("displayName") or "")
        away_team = str(away_team_data.get("location") or away_team_data.get("displayName") or "")
        if not home_team or not away_team:
            continue
        status = as_mapping(as_mapping(event.get("status")).get("type"))
        completed = bool(status.get("completed")) or str(status.get("state")) == "post"
        games.append(
            {
                "id": safe_int(event_id),
                "game_id": safe_int(event_id),
                "season": int(season),
                "week": safe_int(as_mapping(event.get("week")).get("number")),
                "start_date": str(event.get("date") or ""),
                "away_team": away_team,
                "home_team": home_team,
                "neutral_site": bool(competition.get("neutralSite")),
                "away_points": safe_float(away_item.get("score"), float("nan")) if completed else None,
                "home_points": safe_float(home_item.get("score"), float("nan")) if completed else None,
                "completed": completed,
            }
        )
    return sorted(games, key=_game_sort_key)


def fetch_espn_current_season_games(
    season: int,
    session: Any = requests,
) -> list[dict[str, Any]]:
    """Fetch completed/current FBS weeks from ESPN without a credential."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
    common = {
        "dates": str(season),
        "seasontype": 2,
        "groups": 80,
        "limit": 100,
    }

    def get_payload(**extra: Any) -> dict[str, Any]:
        response = session.get(
            url,
            params={**common, **extra},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"ESPN college-football scoreboard returned HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("ESPN college-football scoreboard returned an unexpected payload")
        return payload

    current = get_payload()
    current_week = max(
        (
            safe_int(as_mapping(event).get("week", {}).get("number"))
            for event in current.get("events") or []
        ),
        default=0,
    )
    payloads = [current]
    for week in range(1, current_week + 1):
        payloads.append(get_payload(week=week))
    return parse_espn_scoreboards(payloads, season)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))


@dataclass
class TeamState:
    rating: float = 1500.0
    games: int = 0
    win_ema: float = 0.5
    margin_ema: float = 0.0
    points_for_ema: float = 27.0
    points_against_ema: float = 27.0
    offense_ppa_ema: float = 0.0
    defense_success_ema: float = 0.42
    explosiveness_ema: float = 1.0
    havoc_ema: float = 0.15
    last_game: str | None = None


@dataclass
class SeasonContext:
    talent: dict[str, float] = field(default_factory=dict)
    returning: dict[str, float] = field(default_factory=dict)
    coach_tenure: dict[str, float] = field(default_factory=dict)
    team_locations: dict[str, tuple[float, float]] = field(default_factory=dict)
    venues: dict[int, dict[str, Any]] = field(default_factory=dict)


def _days_since(last_game: str | None, kickoff: datetime | None) -> float:
    last = parse_datetime(last_game)
    if last is None or kickoff is None:
        return 7.0
    return clamp((kickoff - last).total_seconds() / 86400.0, 3.0, 30.0)


def _weather_features(weather: Mapping[str, Any] | None) -> tuple[float, float, float]:
    weather = as_mapping(weather)
    wind = safe_float(weather.get("wind_speed") or weather.get("windSpeed")) / 20.0
    precip = safe_float(weather.get("precipitation"))
    temperature = safe_float(weather.get("temperature"), 65.0)
    extreme = max(0.0, abs(temperature - 65.0) - 15.0) / 20.0
    return wind, precip, extreme


def _site_features(
    game: Mapping[str, Any],
    context: SeasonContext,
) -> tuple[float, float]:
    venue_id = safe_int(game.get("venue_id") or game.get("venueId"))
    venue = context.venues.get(venue_id, {})
    venue_lat = safe_float(venue.get("latitude"), float("nan"))
    venue_lon = safe_float(venue.get("longitude"), float("nan"))
    elevation = safe_float(venue.get("elevation")) / 1000.0
    if not math.isfinite(venue_lat) or not math.isfinite(venue_lon):
        return 0.0, elevation
    home = str(game.get("home_team") or game.get("homeTeam") or "")
    away = str(game.get("away_team") or game.get("awayTeam") or "")
    home_location = context.team_locations.get(home)
    away_location = context.team_locations.get(away)
    if not home_location or not away_location:
        return 0.0, elevation
    home_miles = haversine_miles(*home_location, venue_lat, venue_lon)
    away_miles = haversine_miles(*away_location, venue_lat, venue_lon)
    return (away_miles - home_miles) / 1000.0, elevation


def create_feature_snapshot(
    game: Mapping[str, Any],
    states: Mapping[str, TeamState],
    context: SeasonContext | None = None,
    weather: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    context = context or SeasonContext()
    home = str(game.get("home_team") or game.get("homeTeam") or "")
    away = str(game.get("away_team") or game.get("awayTeam") or "")
    home_state = states.get(home, TeamState())
    away_state = states.get(away, TeamState())
    kickoff = parse_datetime(game.get("start_date") or game.get("startDate") or game.get("commence_time"))
    neutral = bool(game.get("neutral_site") or game.get("neutralSite"))
    travel, elevation = _site_features(game, context)
    wind, precipitation, extreme = _weather_features(weather)
    return {
        "elo_diff_100": (home_state.rating - away_state.rating) / 100.0,
        "margin_ema_diff_10": (home_state.margin_ema - away_state.margin_ema) / 10.0,
        "offense_ppg_diff_10": (home_state.points_for_ema - away_state.points_for_ema) / 10.0,
        "defense_ppg_adv_10": (away_state.points_against_ema - home_state.points_against_ema) / 10.0,
        "win_ema_diff": home_state.win_ema - away_state.win_ema,
        "offense_ppa_diff": home_state.offense_ppa_ema - away_state.offense_ppa_ema,
        "defense_success_adv": away_state.defense_success_ema - home_state.defense_success_ema,
        "explosiveness_diff": home_state.explosiveness_ema - away_state.explosiveness_ema,
        "havoc_adv": home_state.havoc_ema - away_state.havoc_ema,
        "rest_diff_7": (_days_since(home_state.last_game, kickoff) - _days_since(away_state.last_game, kickoff)) / 7.0,
        "talent_diff_100": (context.talent.get(home, 0.0) - context.talent.get(away, 0.0)) / 100.0,
        "returning_diff": context.returning.get(home, 0.5) - context.returning.get(away, 0.5),
        "coach_tenure_diff_5": (context.coach_tenure.get(home, 0.0) - context.coach_tenure.get(away, 0.0)) / 5.0,
        "home_field": 0.0 if neutral else 1.0,
        "travel_diff_1000": travel,
        "elevation_1000": elevation,
        "wind_20": wind,
        "precipitation": precipitation,
        "temperature_extreme": extreme,
    }


def _nested_metric(payload: Mapping[str, Any] | None, side: str, metric: str, default: float) -> float:
    block = as_mapping(as_mapping(payload).get(side))
    value = block.get(metric)
    if metric == "ppa" and value is None:
        plays = max(1.0, safe_float(block.get("plays"), 1.0))
        value = safe_float(block.get("total_ppa") or block.get("totalPpa")) / plays
    return safe_float(value, default)


def update_states_after_game(
    game: Mapping[str, Any],
    states: dict[str, TeamState],
    advanced_by_team: Mapping[str, Mapping[str, Any]] | None = None,
    havoc_by_team: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    home = str(game.get("home_team") or game.get("homeTeam") or "")
    away = str(game.get("away_team") or game.get("awayTeam") or "")
    if not home or not away:
        return
    home_points = safe_float(game.get("home_points") if "home_points" in game else game.get("homePoints"), float("nan"))
    away_points = safe_float(game.get("away_points") if "away_points" in game else game.get("awayPoints"), float("nan"))
    if not math.isfinite(home_points) or not math.isfinite(away_points) or home_points == away_points:
        return
    home_state = states.setdefault(home, TeamState())
    away_state = states.setdefault(away, TeamState())
    neutral = bool(game.get("neutral_site") or game.get("neutralSite"))
    home_advantage = 0.0 if neutral else 55.0
    expected_home = 1.0 / (1.0 + 10 ** ((away_state.rating - home_state.rating - home_advantage) / 400.0))
    home_win = 1.0 if home_points > away_points else 0.0
    margin = home_points - away_points
    multiplier = math.log(abs(margin) + 1.0) * (2.2 / ((abs(home_state.rating - away_state.rating) * 0.001) + 2.2))
    change = 24.0 * multiplier * (home_win - expected_home)
    home_state.rating += change
    away_state.rating -= change

    advanced_by_team = advanced_by_team or {}
    havoc_by_team = havoc_by_team or {}
    alpha = 0.28
    kickoff = str(game.get("start_date") or game.get("startDate") or "") or None
    for team, opponent, state, scored, allowed, won in (
        (home, away, home_state, home_points, away_points, home_win),
        (away, home, away_state, away_points, home_points, 1.0 - home_win),
    ):
        advanced = advanced_by_team.get(team, {})
        havoc = havoc_by_team.get(team, {})
        state.games += 1
        state.win_ema = (1 - alpha) * state.win_ema + alpha * won
        state.margin_ema = (1 - alpha) * state.margin_ema + alpha * (scored - allowed)
        state.points_for_ema = (1 - alpha) * state.points_for_ema + alpha * scored
        state.points_against_ema = (1 - alpha) * state.points_against_ema + alpha * allowed
        state.offense_ppa_ema = (1 - alpha) * state.offense_ppa_ema + alpha * _nested_metric(advanced, "offense", "ppa", state.offense_ppa_ema)
        state.defense_success_ema = (1 - alpha) * state.defense_success_ema + alpha * _nested_metric(advanced, "defense", "success_rate", state.defense_success_ema)
        state.explosiveness_ema = (1 - alpha) * state.explosiveness_ema + alpha * _nested_metric(advanced, "offense", "explosiveness", state.explosiveness_ema)
        state.havoc_ema = (1 - alpha) * state.havoc_ema + alpha * _nested_metric(havoc, "defense", "total", state.havoc_ema)
        state.last_game = kickoff


def regress_states_for_new_season(states: dict[str, TeamState]) -> None:
    for state in states.values():
        state.rating = 1500.0 + 0.72 * (state.rating - 1500.0)
        state.games = 0
        state.win_ema = 0.5 + 0.35 * (state.win_ema - 0.5)
        state.margin_ema *= 0.35
        state.points_for_ema = 27.0 + 0.35 * (state.points_for_ema - 27.0)
        state.points_against_ema = 27.0 + 0.35 * (state.points_against_ema - 27.0)
        state.offense_ppa_ema *= 0.35
        state.defense_success_ema = 0.42 + 0.35 * (state.defense_success_ema - 0.42)
        state.explosiveness_ema = 1.0 + 0.35 * (state.explosiveness_ema - 1.0)
        state.havoc_ema = 0.15 + 0.35 * (state.havoc_ema - 0.15)


def _game_is_complete(game: Mapping[str, Any]) -> bool:
    home_points = game.get("home_points") if "home_points" in game else game.get("homePoints")
    away_points = game.get("away_points") if "away_points" in game else game.get("awayPoints")
    return home_points is not None and away_points is not None and safe_float(home_points) != safe_float(away_points)


def _game_sort_key(game: Mapping[str, Any]) -> tuple[int, datetime]:
    season = safe_int(game.get("season"))
    kickoff = parse_datetime(game.get("start_date") or game.get("startDate")) or datetime(season, 1, 1, tzinfo=timezone.utc)
    return season, kickoff


def build_current_states(
    games: Sequence[Mapping[str, Any]],
    advanced: Sequence[Mapping[str, Any]] | None = None,
    havoc: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, TeamState]:
    advanced_index = {(safe_int(item.get("game_id") or item.get("gameId")), str(item.get("team"))): item for item in (advanced or [])}
    havoc_index = {(safe_int(item.get("game_id") or item.get("gameId")), str(item.get("team"))): item for item in (havoc or [])}
    states: dict[str, TeamState] = {}
    prior_season: int | None = None
    for game in sorted((as_mapping(item) for item in games), key=_game_sort_key):
        season = safe_int(game.get("season"))
        if prior_season is not None and season != prior_season:
            regress_states_for_new_season(states)
        prior_season = season
        if not _game_is_complete(game):
            continue
        game_id = safe_int(game.get("id"))
        home = str(game.get("home_team") or game.get("homeTeam") or "")
        away = str(game.get("away_team") or game.get("awayTeam") or "")
        update_states_after_game(
            game,
            states,
            {home: advanced_index.get((game_id, home), {}), away: advanced_index.get((game_id, away), {})},
            {home: havoc_index.get((game_id, home), {}), away: havoc_index.get((game_id, away), {})},
        )
    return states


def update_states_with_current_games(
    states: Mapping[str, TeamState],
    games: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, TeamState], dict[str, Any]]:
    """Apply current-season final scores to a copied preseason state snapshot."""
    updated = {
        team: TeamState(**asdict(state))
        for team, state in states.items()
    }
    applied = 0
    latest_week = 0
    latest_start = ""
    updated_teams: set[str] = set()
    for raw_game in sorted((as_mapping(item) for item in games), key=_game_sort_key):
        if not _game_is_complete(raw_game):
            continue
        home_display = str(raw_game.get("home_team") or raw_game.get("homeTeam") or "")
        away_display = str(raw_game.get("away_team") or raw_game.get("awayTeam") or "")
        home = match_team_name(home_display, updated) or home_display
        away = match_team_name(away_display, updated) or away_display
        if not home or not away:
            continue
        game = {**raw_game, "home_team": home, "away_team": away}
        update_states_after_game(game, updated)
        applied += 1
        latest_week = max(latest_week, safe_int(game.get("week")))
        latest_start = max(latest_start, str(game.get("start_date") or ""))
        updated_teams.update((home, away))
    return updated, {
        "completed_games_applied": applied,
        "latest_week": latest_week,
        "latest_start": latest_start,
        "teams_updated": len(updated_teams),
    }


def build_season_context(
    season: int,
    returning: Sequence[Mapping[str, Any]] | None = None,
    talent: Sequence[Mapping[str, Any]] | None = None,
    coaches: Sequence[Mapping[str, Any]] | None = None,
    teams: Sequence[Mapping[str, Any]] | None = None,
    venues: Sequence[Mapping[str, Any]] | None = None,
) -> SeasonContext:
    context = SeasonContext()
    for row in returning or []:
        item = as_mapping(row)
        context.returning[str(item.get("team"))] = safe_float(item.get("percent_ppa") or item.get("percentPpa"), 0.5)
    for row in talent or []:
        item = as_mapping(row)
        context.talent[str(item.get("team"))] = safe_float(item.get("talent"))
    for row in coaches or []:
        item = as_mapping(row)
        seasons = [as_mapping(value) for value in item.get("seasons") or []]
        for coach_season in seasons:
            if safe_int(coach_season.get("year")) != season:
                continue
            school = str(coach_season.get("school") or coach_season.get("team") or "")
            first_year = min((safe_int(value.get("year"), season) for value in seasons if str(value.get("school") or value.get("team") or "") == school), default=season)
            context.coach_tenure[school] = float(max(0, season - first_year))
    for row in teams or []:
        item = as_mapping(row)
        location = as_mapping(item.get("location"))
        lat = safe_float(location.get("latitude"), float("nan"))
        lon = safe_float(location.get("longitude"), float("nan"))
        if math.isfinite(lat) and math.isfinite(lon):
            context.team_locations[str(item.get("school"))] = (lat, lon)
    for row in venues or []:
        item = as_mapping(row)
        context.venues[safe_int(item.get("id"))] = item
    return context


def lines_consensus_by_game(lines: Sequence[Mapping[str, Any]] | None) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for raw_game in lines or []:
        game = as_mapping(raw_game)
        home_probs: list[float] = []
        home_odds_values: list[float] = []
        away_odds_values: list[float] = []
        for raw_line in game.get("lines") or []:
            line = as_mapping(raw_line)
            home_odds = line.get("home_moneyline") if "home_moneyline" in line else line.get("homeMoneyline")
            away_odds = line.get("away_moneyline") if "away_moneyline" in line else line.get("awayMoneyline")
            if home_odds is None or away_odds is None:
                continue
            try:
                home_prob, _, _ = devig_two_way(home_odds, away_odds)
            except ValueError:
                continue
            home_probs.append(home_prob)
            home_odds_values.append(safe_float(home_odds))
            away_odds_values.append(safe_float(away_odds))
        if home_probs:
            output[safe_int(game.get("id"))] = {
                "home_probability": float(median(home_probs)),
                "home_odds": float(median(home_odds_values)),
                "away_odds": float(median(away_odds_values)),
                "book_count": len(home_probs),
            }
    return output


def build_historical_feature_rows(
    games: Sequence[Mapping[str, Any]],
    contexts: Mapping[int, SeasonContext] | None = None,
    advanced: Sequence[Mapping[str, Any]] | None = None,
    havoc: Sequence[Mapping[str, Any]] | None = None,
    weather: Sequence[Mapping[str, Any]] | None = None,
    lines: Sequence[Mapping[str, Any]] | None = None,
) -> pd.DataFrame:
    contexts = contexts or {}
    advanced_index = {(safe_int(item.get("game_id") or item.get("gameId")), str(item.get("team"))): as_mapping(item) for item in (advanced or [])}
    havoc_index = {(safe_int(item.get("game_id") or item.get("gameId")), str(item.get("team"))): as_mapping(item) for item in (havoc or [])}
    weather_index = {safe_int(item.get("game_id") or item.get("gameId")): as_mapping(item) for item in (weather or [])}
    market_index = lines_consensus_by_game(lines)
    states: dict[str, TeamState] = {}
    rows: list[dict[str, Any]] = []
    prior_season: int | None = None

    for game in sorted((as_mapping(item) for item in games), key=_game_sort_key):
        if not _game_is_complete(game):
            continue
        season = safe_int(game.get("season"))
        if prior_season is not None and season != prior_season:
            regress_states_for_new_season(states)
        prior_season = season
        game_id = safe_int(game.get("id"))
        home = str(game.get("home_team") or game.get("homeTeam") or "")
        away = str(game.get("away_team") or game.get("awayTeam") or "")
        if not home or not away:
            continue
        snapshot = create_feature_snapshot(game, states, contexts.get(season), weather_index.get(game_id))
        home_points = safe_float(game.get("home_points") if "home_points" in game else game.get("homePoints"))
        away_points = safe_float(game.get("away_points") if "away_points" in game else game.get("awayPoints"))
        market = market_index.get(game_id, {})
        row = {
            "game_id": game_id,
            "season": season,
            "week": safe_int(game.get("week")),
            "start_date": str(game.get("start_date") or game.get("startDate") or ""),
            "away_team": away,
            "home_team": home,
            "home_win": int(home_points > away_points),
            "home_points": home_points,
            "away_points": away_points,
            "market_home_probability": market.get("home_probability"),
            "home_moneyline": market.get("home_odds"),
            "away_moneyline": market.get("away_odds"),
            **snapshot,
        }
        rows.append(row)
        update_states_after_game(
            game,
            states,
            {home: advanced_index.get((game_id, home), {}), away: advanced_index.get((game_id, away), {})},
            {home: havoc_index.get((game_id, home), {}), away: havoc_index.get((game_id, away), {})},
        )
    return pd.DataFrame(rows)


@dataclass
class WinnerModel:
    feature_names: list[str]
    coefficients: list[float]
    intercept: float
    means: list[float]
    scales: list[float]
    calibration_intercept: float = 0.0
    calibration_slope: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def bootstrap(cls) -> WinnerModel:
        coefficients = [
            0.58, 0.34, 0.18, 0.20, 0.24, 0.18, 0.14, 0.10, 0.10,
            0.06, 0.10, 0.08, 0.05, 0.30, 0.05, 0.02, 0.0, 0.0, 0.0,
        ]
        return cls(
            feature_names=list(FEATURE_NAMES),
            coefficients=coefficients,
            intercept=0.0,
            means=[0.0] * len(FEATURE_NAMES),
            scales=[1.0] * len(FEATURE_NAMES),
            metadata={"model_version": MODEL_VERSION, "validated": False, "mode": "bootstrap"},
        )

    @classmethod
    def fit(
        cls,
        frame: pd.DataFrame,
        feature_names: Sequence[str] = FEATURE_NAMES,
        l2: float = 1.5,
        max_iterations: int = 100,
    ) -> WinnerModel:
        if len(frame) < 50:
            raise ValueError("At least 50 completed games are required to fit the NCAA model")
        features = frame.loc[:, list(feature_names)].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
        labels = frame["home_win"].to_numpy(dtype=float)
        means = features.mean(axis=0)
        scales = features.std(axis=0)
        scales[scales < 1e-8] = 1.0
        standardized = (features - means) / scales
        design = np.column_stack([np.ones(len(standardized)), standardized])
        beta = np.zeros(design.shape[1])
        penalty = np.eye(design.shape[1]) * l2
        penalty[0, 0] = 0.0
        for _ in range(max_iterations):
            probabilities = np.clip(sigmoid(design @ beta), 1e-6, 1 - 1e-6)
            weights = np.clip(probabilities * (1.0 - probabilities), 1e-6, None)
            gradient = design.T @ (labels - probabilities) - penalty @ beta
            hessian = design.T @ (weights[:, None] * design) + penalty
            try:
                step = np.linalg.solve(hessian, gradient)
            except np.linalg.LinAlgError:
                step = np.linalg.pinv(hessian) @ gradient
            beta += step
            if np.max(np.abs(step)) < 1e-7:
                break
        return cls(
            feature_names=list(feature_names),
            coefficients=beta[1:].tolist(),
            intercept=float(beta[0]),
            means=means.tolist(),
            scales=scales.tolist(),
            metadata={"model_version": MODEL_VERSION, "validated": False, "training_games": len(frame)},
        )

    def _matrix(self, rows: pd.DataFrame | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> np.ndarray:
        if isinstance(rows, Mapping):
            rows = [rows]
        frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
        matrix = frame.reindex(columns=self.feature_names, fill_value=0.0).apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
        return (matrix - np.asarray(self.means)) / np.asarray(self.scales)

    def raw_logit(self, rows: pd.DataFrame | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> np.ndarray:
        return self.intercept + self._matrix(rows) @ np.asarray(self.coefficients)

    def predict_home_probability(self, rows: pd.DataFrame | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> np.ndarray:
        calibrated = self.calibration_intercept + self.calibration_slope * self.raw_logit(rows)
        return np.clip(sigmoid(calibrated), 0.01, 0.99)

    def fit_calibration(self, logits: Sequence[float], labels: Sequence[float]) -> None:
        values = np.asarray(logits, dtype=float)
        targets = np.asarray(labels, dtype=float)
        design = np.column_stack([np.ones(len(values)), values])
        beta = np.array([0.0, 1.0])
        penalty = np.diag([0.01, 0.01])
        for _ in range(60):
            probability = np.clip(sigmoid(design @ beta), 1e-6, 1 - 1e-6)
            weights = np.clip(probability * (1 - probability), 1e-6, None)
            gradient = design.T @ (targets - probability) - penalty @ beta
            hessian = design.T @ (weights[:, None] * design) + penalty
            step = np.linalg.solve(hessian, gradient)
            beta += step
            if np.max(np.abs(step)) < 1e-7:
                break
        self.calibration_intercept = float(beta[0])
        self.calibration_slope = float(clamp(beta[1], 0.25, 2.5))

    def explain(self, row: Mapping[str, Any], limit: int = 3) -> list[dict[str, Any]]:
        standardized = self._matrix(row)[0]
        contributions = standardized * np.asarray(self.coefficients)
        order = np.argsort(np.abs(contributions))[::-1][:limit]
        return [
            {
                "feature": self.feature_names[index],
                "label": FEATURE_LABELS.get(self.feature_names[index], self.feature_names[index]),
                "direction": "home" if contributions[index] >= 0 else "away",
                "contribution": float(contributions[index]),
            }
            for index in order
            if abs(contributions[index]) > 1e-6
        ]

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> WinnerModel:
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


def probability_metrics(labels: Sequence[float], probabilities: Sequence[float]) -> dict[str, float]:
    y = np.asarray(labels, dtype=float)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    return {
        "games": len(y),
        "accuracy": float(np.mean((p >= 0.5) == y)),
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
    }


def paired_market_deltas(
    labels: Sequence[float],
    market_probabilities: Sequence[float],
    blend_probabilities: Sequence[float],
    samples: int = 2000,
) -> dict[str, Any]:
    y = np.asarray(labels, dtype=float)
    market = np.clip(np.asarray(market_probabilities, dtype=float), 1e-6, 1 - 1e-6)
    blend = np.clip(np.asarray(blend_probabilities, dtype=float), 1e-6, 1 - 1e-6)
    blend_log_loss = -(y * np.log(blend) + (1 - y) * np.log(1 - blend))
    market_log_loss = -(y * np.log(market) + (1 - y) * np.log(1 - market))
    log_loss_delta = blend_log_loss - market_log_loss
    brier_delta = (blend - y) ** 2 - (market - y) ** 2
    rng = np.random.default_rng(20260812)
    indexes = rng.integers(0, len(y), size=(samples, len(y)))
    log_loss_interval = np.quantile(log_loss_delta[indexes].mean(axis=1), [0.025, 0.975])
    brier_interval = np.quantile(brier_delta[indexes].mean(axis=1), [0.025, 0.975])
    return {
        "definition": "market_blend_minus_market; negative favors HagLabs",
        "log_loss_delta": float(log_loss_delta.mean()),
        "log_loss_95_ci": [float(value) for value in log_loss_interval],
        "brier_delta": float(brier_delta.mean()),
        "brier_95_ci": [float(value) for value in brier_interval],
    }


def elo_baseline_probability(frame: pd.DataFrame) -> np.ndarray:
    home_logit = frame["elo_diff_100"].to_numpy(dtype=float) * (100.0 * math.log(10.0) / 400.0)
    home_logit += frame["home_field"].to_numpy(dtype=float) * (55.0 * math.log(10.0) / 400.0)
    return sigmoid(home_logit)


def walk_forward_backtest(frame: pd.DataFrame, min_train_seasons: int = 3) -> dict[str, Any]:
    seasons = sorted(safe_int(value) for value in frame["season"].dropna().unique())
    predictions: list[dict[str, Any]] = []
    for index in range(min_train_seasons, len(seasons)):
        test_season = seasons[index]
        prior = seasons[:index]
        if len(prior) < 2:
            continue
        calibration_season = prior[-1]
        train = frame[frame["season"].isin(prior[:-1])]
        calibration = frame[frame["season"] == calibration_season]
        test = frame[frame["season"] == test_season]
        if len(train) < 50 or len(calibration) < 20 or test.empty:
            continue
        model = WinnerModel.fit(train)
        model.fit_calibration(model.raw_logit(calibration), calibration["home_win"])
        calibration_model = model.predict_home_probability(calibration)
        calibration_market = pd.to_numeric(calibration["market_home_probability"], errors="coerce").to_numpy(dtype=float)
        calibration_labels = calibration["home_win"].to_numpy(dtype=float)
        available = np.isfinite(calibration_market)
        market_weight = 0.75
        if available.sum() >= 50:
            model_logits = np.log(
                np.clip(calibration_model[available], 1e-6, 1 - 1e-6)
                / np.clip(1 - calibration_model[available], 1e-6, 1 - 1e-6)
            )
            market_logits = np.log(
                np.clip(calibration_market[available], 1e-6, 1 - 1e-6)
                / np.clip(1 - calibration_market[available], 1e-6, 1 - 1e-6)
            )
            best_loss = float("inf")
            for candidate in np.linspace(0.0, 1.0, 21):
                candidate_probability = np.clip(
                    sigmoid((1 - candidate) * model_logits + candidate * market_logits),
                    1e-6,
                    1 - 1e-6,
                )
                loss = probability_metrics(calibration_labels[available], candidate_probability)["log_loss"]
                if loss < best_loss:
                    best_loss = loss
                    market_weight = float(candidate)
        raw_model_logits = model.raw_logit(test)
        model_probabilities = model.predict_home_probability(test)
        elo_probabilities = elo_baseline_probability(test)
        for position, (_, row) in enumerate(test.iterrows()):
            market_value = row.get("market_home_probability")
            market_probability = float(market_value) if pd.notna(market_value) else None
            blend_probability = (
                blend_with_market(float(model_probabilities[position]), market_probability, market_weight)
                if market_probability is not None
                else None
            )
            predictions.append(
                {
                    "season": test_season,
                    "game_id": row.get("game_id"),
                    "home_win": int(row["home_win"]),
                    "raw_model_logit": float(raw_model_logits[position]),
                    "model_probability": float(model_probabilities[position]),
                    "elo_probability": float(elo_probabilities[position]),
                    "market_probability": market_probability,
                    "market_blend_probability": blend_probability,
                    "market_weight": market_weight if market_probability is not None else None,
                }
            )
    if not predictions:
        return {"games": 0, "by_season": {}, "model": {}, "elo": {}, "market": {}, "market_blend": {}}
    output = pd.DataFrame(predictions)
    result: dict[str, Any] = {
        "games": len(output),
        "by_season": {},
        "model": probability_metrics(output["home_win"], output["model_probability"]),
        "elo": probability_metrics(output["home_win"], output["elo_probability"]),
    }
    market = output.dropna(subset=["market_probability"])
    if not market.empty:
        result["market"] = probability_metrics(market["home_win"], market["market_probability"])
        result["market_blend"] = probability_metrics(market["home_win"], market["market_blend_probability"])
        result["market_weight"] = float(market["market_weight"].median())
        result["market_deltas"] = paired_market_deltas(
            market["home_win"],
            market["market_probability"],
            market["market_blend_probability"],
        )
    else:
        result["market"] = {}
        result["market_blend"] = {}
        result["market_weight"] = None
    for season, group in output.groupby("season"):
        result["by_season"][str(season)] = {
            "model": probability_metrics(group["home_win"], group["model_probability"]),
            "elo": probability_metrics(group["home_win"], group["elo_probability"]),
        }
        market_group = group.dropna(subset=["market_probability"])
        if not market_group.empty:
            result["by_season"][str(season)]["market"] = probability_metrics(
                market_group["home_win"], market_group["market_probability"]
            )
            result["by_season"][str(season)]["market_blend"] = probability_metrics(
                market_group["home_win"], market_group["market_blend_probability"]
            )
    result["predictions"] = predictions
    return result


def fit_final_model(frame: pd.DataFrame, backtest: Mapping[str, Any] | None = None) -> WinnerModel:
    model = WinnerModel.fit(frame)
    oof = list((backtest or {}).get("predictions") or [])
    if len(oof) >= 100:
        logits = []
        labels = []
        # Calibrate the final coefficient fit from genuinely out-of-sample raw logits.
        # Reversing already calibrated probabilities would fit a second calibration
        # layer and then apply it to an incompatible final-model logit scale.
        for row in oof:
            raw_logit = safe_float(row.get("raw_model_logit"), float("nan"))
            if math.isfinite(raw_logit):
                logits.append(raw_logit)
                labels.append(safe_float(row.get("home_win")))
        if len(logits) >= 100:
            model.fit_calibration(logits, labels)
    model_metrics = (backtest or {}).get("model") or {}
    elo_metrics = (backtest or {}).get("elo") or {}
    model_validated = (
        safe_int((backtest or {}).get("games")) >= 500
        and safe_float(model_metrics.get("log_loss"), 99.0) < safe_float(elo_metrics.get("log_loss"), 99.0)
        and safe_float(model_metrics.get("brier"), 99.0) < safe_float(elo_metrics.get("brier"), 99.0)
    )
    market_metrics = (backtest or {}).get("market") or {}
    blend_metrics = (backtest or {}).get("market_blend") or {}
    market_deltas = (backtest or {}).get("market_deltas") or {}
    log_loss_interval = market_deltas.get("log_loss_95_ci") or [99.0, 99.0]
    brier_interval = market_deltas.get("brier_95_ci") or [99.0, 99.0]
    market_validated = (
        safe_int(market_metrics.get("games")) >= 1000
        and safe_float(blend_metrics.get("log_loss"), 99.0) < safe_float(market_metrics.get("log_loss"), 99.0)
        and safe_float(blend_metrics.get("brier"), 99.0) < safe_float(market_metrics.get("brier"), 99.0)
        and safe_float(log_loss_interval[1], 99.0) < 0.0
        and safe_float(brier_interval[1], 99.0) < 0.0
    )
    model.metadata.update(
        {
            "model_version": MODEL_VERSION,
            "mode": "trained",
            "validated": model_validated,
            "market_validated": market_validated,
            "market_weight": safe_float((backtest or {}).get("market_weight"), 0.75),
            "training_games": len(frame),
            "training_seasons": sorted(safe_int(value) for value in frame["season"].unique()),
            "backtest": {key: value for key, value in (backtest or {}).items() if key != "predictions"},
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return model


def blend_with_market(model_probability: float, market_probability: float | None, weight: float = 0.18) -> float:
    if market_probability is None:
        return float(model_probability)
    model_probability = clamp(float(model_probability), 0.01, 0.99)
    market_probability = clamp(float(market_probability), 0.01, 0.99)
    model_logit = math.log(model_probability / (1.0 - model_probability))
    market_logit = math.log(market_probability / (1.0 - market_probability))
    return float(sigmoid((1.0 - weight) * model_logit + weight * market_logit))


def prediction_record(
    game: Mapping[str, Any],
    feature_row: Mapping[str, Any],
    model: WinnerModel,
    market: Mapping[str, Any] | None = None,
    prediction_time: datetime | None = None,
) -> dict[str, Any]:
    prediction_time = prediction_time or datetime.now(timezone.utc)
    home = str(game.get("home_team") or game.get("homeTeam") or "")
    away = str(game.get("away_team") or game.get("awayTeam") or "")
    independent_home = float(model.predict_home_probability(feature_row)[0])
    market_home = safe_float((market or {}).get("home_probability"), float("nan"))
    market_value = market_home if math.isfinite(market_home) else None
    market_aware_home = blend_with_market(
        independent_home,
        market_value,
        safe_float(model.metadata.get("market_weight"), 0.75),
    )
    home_edge = independent_home - market_value if market_value is not None else None
    independent_winner = home if independent_home >= 0.5 else away
    independent_winner_probability = max(independent_home, 1.0 - independent_home)
    market_winner = (
        (home if market_value >= 0.5 else away) if market_value is not None else None
    )
    market_winner_probability = (
        max(market_value, 1.0 - market_value) if market_value is not None else None
    )
    market_aware_winner = (
        (home if market_aware_home >= 0.5 else away) if market_value is not None else None
    )
    market_aware_winner_probability = (
        max(market_aware_home, 1.0 - market_aware_home)
        if market_value is not None
        else None
    )
    completeness_fields = ("talent_diff_100", "returning_diff", "offense_ppa_diff", "havoc_adv")
    completeness = sum(abs(safe_float(feature_row.get(name))) > 1e-9 for name in completeness_fields) / len(completeness_fields)
    uncertainty = 0.12 - 0.04 * completeness
    validated = bool(model.metadata.get("validated"))
    market_validated = bool(model.metadata.get("market_validated"))
    final_home = market_aware_home if market_validated else independent_home
    winner = home if final_home >= 0.5 else away
    winner_probability = final_home if winner == home else 1.0 - final_home
    actionable = validated and market_validated and home_edge is not None and abs(home_edge) >= 0.035
    return {
        "prediction_id": f"{game.get('id') or game.get('game_id') or ''}:{prediction_time.isoformat()}",
        "game_id": game.get("id") or game.get("game_id"),
        "season": game.get("season"),
        "week": game.get("week"),
        "start_date": game.get("start_date") or game.get("startDate") or game.get("commence_time"),
        "prediction_time": prediction_time.isoformat(),
        "model_version": model.metadata.get("model_version", MODEL_VERSION),
        "model_mode": model.metadata.get("mode", "bootstrap"),
        "away_team": away,
        "home_team": home,
        "neutral_site": bool(game.get("neutral_site") or game.get("neutralSite")),
        "predicted_winner": winner,
        "winner_probability": winner_probability,
        "decision_source": "validated market ensemble" if market_validated else "independent HagLabs model",
        "independent_predicted_winner": independent_winner,
        "independent_winner_probability": independent_winner_probability,
        "independent_home_probability": independent_home,
        "market_predicted_winner": market_winner,
        "market_winner_probability": market_winner_probability,
        "market_aware_predicted_winner": market_aware_winner,
        "market_aware_winner_probability": market_aware_winner_probability,
        "market_aware_home_probability": market_aware_home,
        "final_home_probability": final_home,
        "market_home_probability": market_value,
        "model_edge": home_edge,
        "fair_home_moneyline": fair_american_odds(final_home),
        "fair_away_moneyline": fair_american_odds(1.0 - final_home),
        "market_home_moneyline": (market or {}).get("home_odds"),
        "market_away_moneyline": (market or {}).get("away_odds"),
        "book_count": safe_int((market or {}).get("book_count")),
        "market_observed_at": (market or {}).get("observed_at"),
        "uncertainty_low": clamp(winner_probability - uncertainty, 0.5, 0.99),
        "uncertainty_high": clamp(winner_probability + uncertainty, 0.5, 0.99),
        "actionable_edge": actionable,
        "explanations": model.explain(feature_row),
        "feature_snapshot": {name: safe_float(feature_row.get(name)) for name in model.feature_names},
        "result": "PENDING",
    }


class CFBDDataClient:
    """Small official CFBD REST client with bounded timeouts and no secret logging."""

    base_url = "https://api.collegefootballdata.com"

    def __init__(self, api_key: str, timeout: float = 25.0, session: requests.Session | None = None):
        key = str(api_key or "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        if not key:
            raise ValueError("CFBD_API_KEY is required")
        self.api_key = key
        self.timeout = timeout
        self.session = session or requests.Session()

    def get(self, path: str, **params: Any) -> list[dict[str, Any]]:
        query = dict(params)
        if "seasonType" not in query and "season_type" in query:
            query["seasonType"] = query.pop("season_type")
        if "gameId" not in query and "game_id" in query:
            query["gameId"] = query.pop("game_id")
        response = self.session.get(
            f"{self.base_url}{path}",
            params={key: value for key, value in query.items() if value is not None},
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"CFBD request {path} failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError(f"CFBD request {path} returned an unexpected payload")
        return [as_mapping(item) for item in payload]

    def optional(self, path: str, **params: Any) -> list[dict[str, Any]]:
        try:
            return self.get(path, **params)
        except (requests.RequestException, RuntimeError, ValueError):
            return []


def find_matching_schedule_game(
    odds_game: Mapping[str, Any],
    schedule: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    odds_home = normalize_team_name(str(odds_game.get("home_team") or ""))
    odds_away = normalize_team_name(str(odds_game.get("away_team") or ""))
    for raw_game in schedule:
        game = as_mapping(raw_game)
        home = normalize_team_name(str(game.get("home_team") or game.get("homeTeam") or ""))
        away = normalize_team_name(str(game.get("away_team") or game.get("awayTeam") or ""))
        if home == odds_home and away == odds_away:
            return game
    return None
