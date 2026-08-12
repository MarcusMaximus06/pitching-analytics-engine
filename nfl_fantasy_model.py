"""Evidence-based NFL fantasy projections for the HagLabs draft lab.

The model intentionally separates public-data loading from deterministic model
logic so rankings can be backtested and unit-tested without network access.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

MODEL_VERSION = "2.0.0"

SCORING_PRESETS: dict[str, dict[str, float]] = {
    "ESPN PPR": {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "reception": 1.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_point": 2.0,
    },
    "Sleeper PPR": {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "reception": 1.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_point": 2.0,
    },
    "PPR": {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "reception": 1.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_point": 2.0,
    },
    "Half PPR": {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "reception": 0.5,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_point": 2.0,
    },
    "Standard": {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "reception": 0.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_point": 2.0,
    },
    "PPR + 6 Pt Pass TD": {
        "pass_yd": 0.04,
        "pass_td": 6.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "reception": 1.0,
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_point": 2.0,
    },
}

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K")
RECENCY_WEIGHTS = {1: 0.56, 2: 0.28, 3: 0.11, 4: 0.05}


def _to_pandas(frame: Any) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    try:
        return frame.to_pandas()
    except Exception:  # noqa: BLE001 - accepts either pandas or Polars frames.
        try:
            return pd.DataFrame(frame.to_dicts())
        except Exception:  # noqa: BLE001 - invalid optional feed becomes empty.
            return pd.DataFrame()


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _weighted_average(values: list[tuple[float, float]], default: float = 0.0) -> float:
    valid = [
        (value, weight)
        for value, weight in values
        if math.isfinite(value) and weight > 0
    ]
    if not valid:
        return default
    total_weight = sum(weight for _, weight in valid)
    return sum(value * weight for value, weight in valid) / total_weight


def _normalized_scoring(
    scoring_name: str,
    scoring_settings: dict[str, float] | None = None,
) -> dict[str, float]:
    if not scoring_settings:
        return SCORING_PRESETS.get(scoring_name, SCORING_PRESETS["PPR"]).copy()
    source = {key: _safe_number(value) for key, value in scoring_settings.items()}
    return {
        "pass_yd": source.get("pass_yd", 0.04),
        "pass_td": source.get("pass_td", 4.0),
        "pass_int": source.get("pass_int", -2.0),
        "rush_yd": source.get("rush_yd", 0.1),
        "rush_td": source.get("rush_td", 6.0),
        "reception": source.get("rec", source.get("reception", 1.0)),
        "rec_yd": source.get("rec_yd", 0.1),
        "rec_td": source.get("rec_td", 6.0),
        "fumble_lost": source.get("fum_lost", source.get("fumble_lost", -2.0)),
        "two_point": source.get("pass_2pt", source.get("two_point", 2.0)),
        "rush_first_down": source.get("rush_fd", 0.0),
        "rec_first_down": source.get("rec_fd", 0.0),
        "pass_first_down": source.get("pass_fd", 0.0),
        "rb_reception_bonus": source.get("bonus_rec_rb", 0.0),
        "bonus_pass_300": source.get("bonus_pass_yd_300", 0.0),
        "bonus_pass_400": source.get("bonus_pass_yd_400", 0.0),
        "bonus_rush_100": source.get("bonus_rush_yd_100", 0.0),
        "bonus_rush_200": source.get("bonus_rush_yd_200", 0.0),
        "bonus_rec_100": source.get("bonus_rec_yd_100", 0.0),
        "bonus_rec_200": source.get("bonus_rec_yd_200", 0.0),
        "bonus_pass_td_40": source.get("pass_td_40p", 0.0),
        "bonus_pass_td_50": source.get("pass_td_50p", 0.0),
        "bonus_rush_td_40": source.get("rush_td_40p", 0.0),
        "bonus_rush_td_50": source.get("rush_td_50p", 0.0),
        "bonus_rec_td_40": source.get("rec_td_40p", 0.0),
        "bonus_rec_td_50": source.get("rec_td_50p", 0.0),
        "fgm_yds": source.get("fgm_yds", 0.0),
        "fgm_0_19": source.get("fgm_0_19", 3.0),
        "fgm_20_29": source.get("fgm_20_29", 3.0),
        "fgm_30_39": source.get("fgm_30_39", 3.0),
        "fgm_40_49": source.get("fgm_40_49", 4.0),
        "fgm_50_59": source.get("fgm_50_59", 5.0),
        "fgm_60p": source.get("fgm_60p", 5.0),
        "fgmiss": source.get("fgmiss", 0.0),
        "xpm": source.get("xpm", 1.0),
        "xpmiss": source.get("xpmiss", 0.0),
    }


def _estimated_threshold_games(
    yards: pd.Series, games: pd.Series, threshold: float
) -> pd.Series:
    per_game = np.where(games > 0, yards / games, 0.0)
    scale = max(12.0, threshold * 0.20)
    probability = 1 / (1 + np.exp(-((per_game - threshold * 0.78) / scale)))
    return pd.Series(probability * games, index=yards.index).clip(lower=0)


def score_player_stats(
    frame: pd.DataFrame,
    scoring_name: str = "PPR",
    scoring_settings: dict[str, float] | None = None,
) -> pd.Series:
    """Calculate fantasy points from raw nflverse player-stat columns."""
    scoring = _normalized_scoring(scoring_name, scoring_settings)
    fumbles_lost = _series(frame, "fumbles_lost_total")
    if not fumbles_lost.any():
        fumbles_lost = (
            _series(frame, "sack_fumbles_lost")
            + _series(frame, "rushing_fumbles_lost")
            + _series(frame, "receiving_fumbles_lost")
        )

    points = (
        _series(frame, "passing_yards") * scoring["pass_yd"]
        + _series(frame, "passing_tds") * scoring["pass_td"]
        + _series(frame, "passing_interceptions") * scoring["pass_int"]
        + _series(frame, "rushing_yards") * scoring["rush_yd"]
        + _series(frame, "rushing_tds") * scoring["rush_td"]
        + _series(frame, "receptions") * scoring["reception"]
        + _series(frame, "receiving_yards") * scoring["rec_yd"]
        + _series(frame, "receiving_tds") * scoring["rec_td"]
        + fumbles_lost * scoring["fumble_lost"]
        + (
            _series(frame, "passing_2pt_conversions")
            + _series(frame, "rushing_2pt_conversions")
            + _series(frame, "receiving_2pt_conversions")
        )
        * scoring["two_point"]
        + _series(frame, "rushing_first_downs") * scoring.get("rush_first_down", 0.0)
        + _series(frame, "receiving_first_downs") * scoring.get("rec_first_down", 0.0)
        + _series(frame, "passing_first_downs") * scoring.get("pass_first_down", 0.0)
    )
    if "position" in frame.columns:
        rb_mask = frame["position"].astype(str).eq("RB").astype(float)
        points += (
            _series(frame, "receptions")
            * rb_mask
            * scoring.get("rb_reception_bonus", 0.0)
        )

    games = _series(frame, "games").clip(lower=0)
    points += _estimated_threshold_games(
        _series(frame, "passing_yards"), games, 300
    ) * scoring.get("bonus_pass_300", 0.0)
    points += _estimated_threshold_games(
        _series(frame, "passing_yards"), games, 400
    ) * scoring.get("bonus_pass_400", 0.0)
    points += _estimated_threshold_games(
        _series(frame, "rushing_yards"), games, 100
    ) * scoring.get("bonus_rush_100", 0.0)
    points += _estimated_threshold_games(
        _series(frame, "rushing_yards"), games, 200
    ) * scoring.get("bonus_rush_200", 0.0)
    points += _estimated_threshold_games(
        _series(frame, "receiving_yards"), games, 100
    ) * scoring.get("bonus_rec_100", 0.0)
    points += _estimated_threshold_games(
        _series(frame, "receiving_yards"), games, 200
    ) * scoring.get("bonus_rec_200", 0.0)

    # Seasonal nflverse files expose 40+ yard plays, not exact long-TD counts.
    # Regress those plays into conservative expected long-touchdown bonuses.
    for play_column, td_column, bonus_40, bonus_50 in [
        ("passing_40", "passing_tds", "bonus_pass_td_40", "bonus_pass_td_50"),
        ("rushing_40", "rushing_tds", "bonus_rush_td_40", "bonus_rush_td_50"),
        ("receiving_40", "receiving_tds", "bonus_rec_td_40", "bonus_rec_td_50"),
    ]:
        expected_long_tds = np.minimum(
            _series(frame, play_column) * 0.38, _series(frame, td_column)
        )
        points += expected_long_tds * scoring.get(bonus_40, 0.0)
        points += expected_long_tds * 0.45 * scoring.get(bonus_50, 0.0)

    if scoring.get("fgm_yds", 0.0):
        # nflverse supplies the sum of exact made-FG distances for each season.
        points += _series(frame, "fg_made_distance") * scoring["fgm_yds"]
    else:
        points += (
            _series(frame, "fg_made_0_19") * scoring.get("fgm_0_19", 3.0)
            + _series(frame, "fg_made_20_29") * scoring.get("fgm_20_29", 3.0)
            + _series(frame, "fg_made_30_39") * scoring.get("fgm_30_39", 3.0)
            + _series(frame, "fg_made_40_49") * scoring.get("fgm_40_49", 4.0)
            + _series(frame, "fg_made_50_59") * scoring.get("fgm_50_59", 5.0)
            + _series(frame, "fg_made_60_") * scoring.get("fgm_60p", 5.0)
        )
    points += _series(frame, "fg_missed") * scoring.get("fgmiss", 0.0)
    points += _series(frame, "pat_made") * scoring.get("xpm", 1.0)
    points += _series(frame, "pat_missed") * scoring.get("xpmiss", 0.0)
    return points.round(3)


def load_nflverse_fantasy_data(
    projection_season: int | None = None,
    history_seasons: int = 4,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Load only public nflverse datasets needed by the draft model."""
    projection_season = projection_season or datetime.now(UTC).year
    completed = list(range(projection_season - history_seasons, projection_season))
    advanced = completed[-2:]
    errors: list[str] = []

    nflverse_base = "https://github.com/nflverse/nflverse-data/releases/download"
    opportunity_base = (
        "https://github.com/ffverse/ffopportunity/releases/download/latest-data"
    )

    configured_cache = os.getenv("HAGLABS_NFL_CACHE_DIR")
    if configured_cache:
        cache_root = Path(configured_cache)
    elif os.getenv("LOCALAPPDATA"):
        cache_root = Path(os.environ["LOCALAPPDATA"]) / "HagLabs" / "nfl_fantasy_cache"
    else:
        cache_root = Path.home() / ".cache" / "haglabs" / "nfl_fantasy_cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    def download(url: str) -> pd.DataFrame:
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_path = cache_root / f"{cache_key}.parquet"
        cache_age_hours = (
            (datetime.now(UTC).timestamp() - cache_path.stat().st_mtime) / 3600
            if cache_path.exists()
            else float("inf")
        )
        if cache_age_hours <= 12:
            return pd.read_parquet(cache_path)

        try:
            response = requests.get(
                url,
                timeout=(10, 60),
                headers={
                    "User-Agent": "HagLabs-NFL-Fantasy/1.0",
                    "Accept": "application/octet-stream, */*",
                },
            )
            response.raise_for_status()
            frame = pd.read_parquet(BytesIO(response.content))
            temporary = cache_path.with_suffix(".tmp.parquet")
            frame.to_parquet(temporary, index=False)
            temporary.replace(cache_path)
            return frame
        except (requests.RequestException, OSError, ValueError):
            if cache_path.exists():
                return pd.read_parquet(cache_path)
            raise

    def load(name: str, loader: Any) -> pd.DataFrame:
        try:
            return loader()
        except Exception as exc:  # noqa: BLE001 - optional public feeds degrade independently.
            errors.append(f"{name}: {type(exc).__name__}")
            return pd.DataFrame()

    def load_seasons(url_template: str, seasons: list[int]) -> pd.DataFrame:
        frames = [download(url_template.format(season=season)) for season in seasons]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    receiving = load(
        "nextgen receiving",
        lambda: download(f"{nflverse_base}/nextgen_stats/ngs_receiving.parquet"),
    )
    rushing = load(
        "nextgen rushing",
        lambda: download(f"{nflverse_base}/nextgen_stats/ngs_rushing.parquet"),
    )
    passing = load(
        "nextgen passing",
        lambda: download(f"{nflverse_base}/nextgen_stats/ngs_passing.parquet"),
    )
    for frame in [receiving, rushing, passing]:
        if not frame.empty and "season" in frame.columns:
            frame.drop(frame[~frame["season"].isin(advanced)].index, inplace=True)

    bundle = {
        "stats": load(
            "player stats",
            lambda: load_seasons(
                f"{nflverse_base}/stats_player/stats_player_reg_{{season}}.parquet",
                completed,
            ),
        ),
        "rosters": load(
            "current rosters",
            lambda: download(
                f"{nflverse_base}/rosters/roster_{projection_season}.parquet"
            ),
        ),
        "players": load(
            "player registry",
            lambda: download(f"{nflverse_base}/players/players.parquet"),
        ),
        "opportunity": load(
            "expected opportunity",
            lambda: load_seasons(
                f"{opportunity_base}/ep_weekly_{{season}}.parquet",
                advanced,
            ),
        ),
        "snaps": load(
            "snap counts",
            lambda: load_seasons(
                f"{nflverse_base}/snap_counts/snap_counts_{{season}}.parquet",
                advanced,
            ),
        ),
        "depth": load(
            "depth charts",
            lambda: download(
                f"{nflverse_base}/depth_charts/depth_charts_{projection_season}.parquet"
            ),
        ),
        "combine": load(
            "combine",
            lambda: download(f"{nflverse_base}/combine/combine.parquet"),
        ),
        "draft": load(
            "draft",
            lambda: download(f"{nflverse_base}/draft_picks/draft_picks.parquet"),
        ),
        "ngs_receiving": receiving,
        "ngs_rushing": rushing,
        "ngs_passing": passing,
    }
    recent_draft_seasons = set(range(projection_season - 2, projection_season + 1))
    if not bundle["combine"].empty:
        season_column = (
            "season" if "season" in bundle["combine"].columns else "draft_year"
        )
        bundle["combine"] = bundle["combine"][
            bundle["combine"][season_column].isin(recent_draft_seasons)
        ]
    if not bundle["draft"].empty:
        bundle["draft"] = bundle["draft"][
            bundle["draft"]["season"].isin(recent_draft_seasons)
        ]
    metadata = {
        "projection_season": projection_season,
        "history_seasons": completed,
        "advanced_seasons": advanced,
        "errors": errors,
        "loaded_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "cache_directory": str(cache_root),
    }
    return bundle, metadata


def _prepare_stats(
    stats: pd.DataFrame,
    scoring_name: str,
    scoring_settings: dict[str, float] | None = None,
) -> pd.DataFrame:
    if stats.empty:
        return stats.copy()
    frame = stats.copy()
    if "season_type" in frame.columns:
        frame = frame[frame["season_type"].astype(str).str.upper().eq("REG")]
    frame = frame[frame.get("position", "").isin(FANTASY_POSITIONS)].copy()
    frame["model_points"] = score_player_stats(frame, scoring_name, scoring_settings)
    frame["games"] = _series(frame, "games").clip(lower=0)
    frame["model_ppg"] = np.where(
        frame["games"] > 0, frame["model_points"] / frame["games"], 0.0
    )
    frame["player_id"] = frame["player_id"].astype(str)
    frame["season"] = (
        pd.to_numeric(frame["season"], errors="coerce").fillna(0).astype(int)
    )
    return frame


def _current_roster(
    bundle: dict[str, pd.DataFrame], projection_season: int
) -> pd.DataFrame:
    rosters = bundle.get("rosters", pd.DataFrame()).copy()
    players = bundle.get("players", pd.DataFrame()).copy()
    if rosters.empty:
        return pd.DataFrame()

    rosters = rosters[rosters.get("position", "").isin(FANTASY_POSITIONS)].copy()
    rosters = rosters[rosters["gsis_id"].notna()].copy()
    if "week" in rosters.columns:
        rosters["week"] = pd.to_numeric(rosters["week"], errors="coerce").fillna(0)
        rosters = rosters.sort_values("week")
    rosters = rosters.drop_duplicates("gsis_id", keep="last")
    if "status" in rosters.columns:
        rosters = rosters[~rosters["status"].astype(str).isin(["CUT", "RET"])].copy()
    rosters["gsis_id"] = rosters["gsis_id"].astype(str)

    if not players.empty and "gsis_id" in players.columns:
        player_columns = [
            "gsis_id",
            "birth_date",
            "draft_year",
            "draft_round",
            "draft_pick",
            "college_name",
            "headshot",
        ]
        available = [column for column in player_columns if column in players.columns]
        registry = players[available].dropna(subset=["gsis_id"]).copy()
        registry["gsis_id"] = registry["gsis_id"].astype(str)
        registry = registry.drop_duplicates("gsis_id", keep="last")
        rosters = rosters.merge(
            registry, on="gsis_id", how="left", suffixes=("", "_registry")
        )

    rosters["projection_season"] = projection_season
    return rosters


def _age_on_season_start(birth_date: Any, projection_season: int) -> float | None:
    try:
        born = pd.to_datetime(birth_date, errors="raise").date()
        season_start = date(projection_season, 9, 1)
        return round((season_start - born).days / 365.2425, 1)
    except Exception:  # noqa: BLE001 - malformed public dates become unknown ages.
        return None


def _age_multiplier(position: str, age: float | None) -> float:
    if age is None or age <= 0:
        return 1.0
    if position == "RB":
        if age <= 23:
            return 1.02
        if age <= 26:
            return 1.0
        return max(0.58, 1.0 - (age - 26) * 0.075)
    if position == "WR":
        if age <= 22:
            return 0.96
        if age <= 28:
            return 1.0
        return max(0.64, 1.0 - (age - 28) * 0.052)
    if position == "TE":
        if age <= 23:
            return 0.91
        if age <= 29:
            return 1.0
        return max(0.68, 1.0 - (age - 29) * 0.045)
    if position == "QB":
        if age <= 24:
            return 0.97
        if age <= 33:
            return 1.0
        return max(0.72, 1.0 - (age - 33) * 0.035)
    return 1.0


def _rookie_projection(position: str, draft_pick: float, scoring_name: str) -> float:
    reception_bonus = SCORING_PRESETS.get(scoring_name, SCORING_PRESETS["PPR"])[
        "reception"
    ]
    baseline = {
        "QB": 14.0,
        "RB": 8.4 + reception_bonus * 0.8,
        "WR": 7.0 + reception_bonus * 1.2,
        "TE": 4.7 + reception_bonus * 0.7,
        "K": 7.0,
    }.get(position, 4.0)
    pick = draft_pick if draft_pick > 0 else 260.0
    if pick <= 10:
        capital = 1.28
    elif pick <= 32:
        capital = 1.16
    elif pick <= 64:
        capital = 1.02
    elif pick <= 105:
        capital = 0.88
    elif pick <= 160:
        capital = 0.72
    else:
        capital = 0.58
    return baseline * capital


def _opportunity_features(opportunity: pd.DataFrame) -> pd.DataFrame:
    if opportunity.empty or "player_id" not in opportunity.columns:
        return pd.DataFrame()
    frame = opportunity.copy()
    frame["player_id"] = frame["player_id"].astype(str)
    frame["season"] = (
        pd.to_numeric(frame["season"], errors="coerce").fillna(0).astype(int)
    )
    frame["games"] = frame.groupby(["player_id", "season"])["week"].transform("nunique")
    grouped = frame.groupby(["player_id", "season", "position"], as_index=False).agg(
        expected_points=("total_fantasy_points_exp", "sum"),
        actual_points=("total_fantasy_points", "sum"),
        expected_tds=("total_touchdown_exp", "sum"),
        actual_tds=("total_touchdown", "sum"),
        games=("games", "max"),
    )
    grouped["expected_ppg"] = np.where(
        grouped["games"] > 0,
        grouped["expected_points"] / grouped["games"],
        0.0,
    )
    grouped["td_regression"] = grouped["expected_tds"] - grouped["actual_tds"]
    return grouped


def _snap_features(snaps: pd.DataFrame) -> dict[str, float]:
    if snaps.empty or "player" not in snaps.columns:
        return {}
    frame = snaps.copy()
    values = (
        frame.get("offense_pct", pd.Series(0, index=frame.index))
        .astype(str)
        .str.rstrip("%")
    )
    frame["snap_pct"] = pd.to_numeric(values, errors="coerce").fillna(0.0)
    frame.loc[frame["snap_pct"] > 1.5, "snap_pct"] /= 100.0
    frame["name_key"] = frame["player"].map(_normalize_name)
    latest = pd.to_numeric(frame.get("season"), errors="coerce").max()
    frame = frame[pd.to_numeric(frame.get("season"), errors="coerce").eq(latest)]
    return frame.groupby("name_key")["snap_pct"].mean().clip(0, 1).to_dict()


def _depth_features(depth: pd.DataFrame) -> tuple[dict[str, int], str | None]:
    if depth.empty or "gsis_id" not in depth.columns or "pos_rank" not in depth.columns:
        return {}, None
    frame = depth.dropna(subset=["gsis_id"]).copy()
    if "dt" in frame.columns:
        frame["dt"] = pd.to_datetime(frame["dt"], errors="coerce", utc=True)
        latest = frame["dt"].max()
        if pd.notna(latest):
            frame = frame[frame["dt"].eq(latest)]
            as_of = latest.isoformat()
        else:
            as_of = None
    else:
        as_of = None
    frame["pos_rank"] = pd.to_numeric(frame["pos_rank"], errors="coerce")
    frame = frame.dropna(subset=["pos_rank"]).sort_values("pos_rank")
    ranks = (
        frame.drop_duplicates("gsis_id").set_index("gsis_id")["pos_rank"].astype(int)
    )
    return {str(player_id): int(rank) for player_id, rank in ranks.items()}, as_of


def _ngs_features(bundle: dict[str, pd.DataFrame]) -> dict[str, float]:
    feature_map: dict[str, float] = {}
    specs = [
        ("ngs_receiving", "avg_yac_above_expectation", 0.65, "avg_separation", 0.35),
        (
            "ngs_rushing",
            "rush_yards_over_expected_per_att",
            0.75,
            "rush_pct_over_expected",
            0.25,
        ),
        (
            "ngs_passing",
            "completion_percentage_above_expectation",
            0.75,
            "avg_completed_air_yards",
            0.25,
        ),
    ]
    for dataset, primary, primary_weight, secondary, secondary_weight in specs:
        frame = bundle.get(dataset, pd.DataFrame()).copy()
        if (
            frame.empty
            or "player_gsis_id" not in frame.columns
            or primary not in frame.columns
        ):
            continue
        frame[primary] = pd.to_numeric(frame[primary], errors="coerce")
        frame[secondary] = pd.to_numeric(frame.get(secondary), errors="coerce")
        grouped = frame.groupby("player_gsis_id", as_index=False).agg(
            primary=(primary, "mean"),
            secondary=(secondary, "mean"),
        )
        for column in ["primary", "secondary"]:
            std = grouped[column].std()
            grouped[f"{column}_z"] = (
                (grouped[column] - grouped[column].mean()) / std
                if std and math.isfinite(std)
                else 0.0
            )
        grouped["advanced_z"] = (
            grouped["primary_z"].fillna(0) * primary_weight
            + grouped["secondary_z"].fillna(0) * secondary_weight
        ).clip(-2, 2)
        feature_map.update(
            {
                str(row.player_gsis_id): float(row.advanced_z)
                for row in grouped.itertuples()
            }
        )
    return feature_map


def _team_environment(stats: pd.DataFrame) -> dict[str, float]:
    if stats.empty or "recent_team" not in stats.columns:
        return {}
    latest = int(stats["season"].max())
    frame = stats[
        (stats["season"] == latest) & stats["position"].isin(["QB", "RB", "WR", "TE"])
    ]
    values = frame.groupby("recent_team")["model_points"].sum()
    std = values.std()
    z_scores = (
        (values - values.mean()) / std if std and math.isfinite(std) else values * 0
    )
    return (1 + z_scores.clip(-2.4, 2.4) * 0.025).clip(0.94, 1.06).to_dict()


def _replacement_ranks(league_size: int, lineup_mode: str) -> dict[str, int]:
    qb_multiplier = 2.0 if lineup_mode == "Superflex" else 1.0
    return {
        "QB": max(1, round(league_size * qb_multiplier)),
        "RB": max(1, round(league_size * 2.5)),
        "WR": max(1, round(league_size * 3.25)),
        "TE": max(1, league_size),
        "K": max(1, league_size),
    }


def _percentile(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.rank(method="average", pct=True).fillna(0.0) * 100


def build_fantasy_board(
    bundle: dict[str, pd.DataFrame],
    scoring_name: str = "PPR",
    league_size: int = 12,
    lineup_mode: str = "1QB",
    projection_season: int | None = None,
    strategy: str = "Redraft",
    scoring_settings: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build a current-season draft board with uncertainty and career outlook."""
    projection_season = projection_season or datetime.now(UTC).year
    stats = _prepare_stats(
        bundle.get("stats", pd.DataFrame()), scoring_name, scoring_settings
    )
    rosters = _current_roster(bundle, projection_season)
    if stats.empty or rosters.empty:
        return pd.DataFrame()

    opportunity = _opportunity_features(bundle.get("opportunity", pd.DataFrame()))
    snap_map = _snap_features(bundle.get("snaps", pd.DataFrame()))
    depth_map, depth_as_of = _depth_features(bundle.get("depth", pd.DataFrame()))
    ngs_map = _ngs_features(bundle)
    team_map = _team_environment(stats)
    latest_completed = int(stats["season"].max())

    if not opportunity.empty:
        latest_opp = int(opportunity["season"].max())
    else:
        latest_opp = 0

    rows: list[dict[str, Any]] = []
    for roster in rosters.itertuples(index=False):
        player_id = str(getattr(roster, "gsis_id", ""))
        position = str(getattr(roster, "position", ""))
        name = str(
            getattr(roster, "full_name", "") or getattr(roster, "football_name", "")
        )
        team = str(getattr(roster, "team", "FA") or "FA")
        history = stats[stats["player_id"].eq(player_id)].sort_values(
            "season", ascending=False
        )
        age = _age_on_season_start(
            getattr(roster, "birth_date", None), projection_season
        )
        years_exp = int(_safe_number(getattr(roster, "years_exp", 0), 0))
        draft_pick = _safe_number(
            getattr(roster, "draft_number", None),
            _safe_number(getattr(roster, "draft_pick", None), 0),
        )

        history_values: list[tuple[float, float]] = []
        availability_values: list[tuple[float, float]] = []
        target_share_values: list[tuple[float, float]] = []
        wopr_values: list[tuple[float, float]] = []
        season_ppg: list[float] = []
        for record in history.itertuples(index=False):
            distance = max(1, projection_season - record.season)
            weight = RECENCY_WEIGHTS.get(distance, 0.02)
            ppg = _safe_number(getattr(record, "model_ppg", 0))
            games = _safe_number(getattr(record, "games", 0))
            history_values.append((ppg, weight))
            availability_values.append((min(1.0, games / 17.0), weight))
            target_share_values.append(
                (_safe_number(getattr(record, "target_share", 0)), weight)
            )
            wopr_values.append((_safe_number(getattr(record, "wopr", 0)), weight))
            if ppg > 0:
                season_ppg.append(ppg)

        is_rookie = years_exp == 0 or history.empty
        if history_values:
            baseline_ppg = _weighted_average(history_values)
        else:
            baseline_ppg = _rookie_projection(position, draft_pick, scoring_name)
        availability = _weighted_average(
            availability_values, default=0.86 if not is_rookie else 0.94
        )
        target_share = _weighted_average(target_share_values)
        wopr = _weighted_average(wopr_values)

        xppg = 0.0
        td_regression = 0.0
        if not opportunity.empty:
            player_opp = opportunity[opportunity["player_id"].eq(player_id)].copy()
            values: list[tuple[float, float]] = []
            td_values: list[tuple[float, float]] = []
            for record in player_opp.itertuples(index=False):
                distance = max(1, projection_season - record.season)
                weight = RECENCY_WEIGHTS.get(distance, 0.04)
                values.append((_safe_number(record.expected_ppg), weight))
                td_values.append((_safe_number(record.td_regression), weight))
            xppg = _weighted_average(values)
            td_regression = _weighted_average(td_values)

        # Scale the expected-opportunity feed to the selected scoring environment.
        if xppg > 0:
            ppr_adjustment = {
                "ESPN PPR": 1.08,
                "Sleeper PPR": 1.08,
                "PPR": 1.08,
                "Half PPR": 1.0,
                "Standard": 0.92,
                "PPR + 6 Pt Pass TD": 1.11 if position == "QB" else 1.08,
            }.get(scoring_name, 1.0)
            xppg *= ppr_adjustment
            blended_ppg = baseline_ppg * 0.72 + xppg * 0.28
        else:
            blended_ppg = baseline_ppg

        latest_ppg = (
            _safe_number(history.iloc[0]["model_ppg"], baseline_ppg)
            if not history.empty
            else baseline_ppg
        )
        older_ppg = _weighted_average(history_values[1:], default=baseline_ppg)
        trend = np.clip(
            (latest_ppg - older_ppg) / max(abs(older_ppg), 5.0), -0.25, 0.25
        )
        trend_factor = 1 + float(trend) * 0.10
        team_factor = _safe_number(team_map.get(team, 1.0), 1.0)
        advanced_z = _safe_number(ngs_map.get(player_id, 0.0), 0.0)
        advanced_factor = 1 + float(np.clip(advanced_z, -2, 2)) * 0.022
        snap_pct = _safe_number(snap_map.get(_normalize_name(name), 0.0), 0.0)
        role_factor = (
            1.0
            if is_rookie or snap_pct <= 0
            else 0.94 + min(1.0, snap_pct / 0.72) * 0.08
        )
        depth_rank = int(depth_map.get(player_id, 0))
        if depth_rank == 1:
            role_factor *= 1.015
        elif depth_rank == 2:
            role_factor *= 0.985
        elif depth_rank >= 3:
            role_factor *= max(0.86, 0.96 - (depth_rank - 3) * 0.018)
        nfl_status = str(getattr(roster, "status", "ACT") or "ACT")
        status_factor = {"RES": 0.68, "E14": 0.90}.get(nfl_status, 1.0)
        age_factor = _age_multiplier(position, age)

        projected_ppg = max(
            0.0,
            blended_ppg
            * trend_factor
            * team_factor
            * advanced_factor
            * role_factor
            * status_factor
            * age_factor,
        )
        projected_games = (
            16.5 if is_rookie else float(np.clip(14.0 + availability * 3.0, 14.0, 17.0))
        )
        projected_points = projected_ppg * projected_games

        season_variation = (
            float(np.std(season_ppg) / max(np.mean(season_ppg), 1.0))
            if len(season_ppg) > 1
            else 0.20
        )
        age_risk = max(0.0, (1.0 - age_factor) * 70)
        sample_risk = max(0, 3 - len(season_ppg)) * 8
        availability_risk = (1.0 - availability) * 32
        role_risk = (
            0.0 if is_rookie or snap_pct <= 0 else max(0.0, 0.65 - snap_pct) * 24
        )
        rookie_risk = 24.0 if is_rookie else 0.0
        risk = float(
            np.clip(
                8
                + season_variation * 45
                + age_risk
                + sample_risk
                + availability_risk
                + role_risk
                + rookie_risk,
                8,
                92,
            )
        )
        confidence = round(100 - risk)
        uncertainty = 0.16 + risk / 100 * 0.30
        floor_ppg = max(0.0, projected_ppg * (1 - uncertainty))
        ceiling_ppg = projected_ppg * (1 + uncertainty * 1.15)

        career_ppg: list[float] = []
        base_age_factor = max(_age_multiplier(position, age), 0.01)
        for offset in range(5):
            future_age = age + offset if age is not None else None
            curve_ratio = _age_multiplier(position, future_age) / base_age_factor
            development = 1.0
            if years_exp == 0:
                development = [1.0, 1.08, 1.12, 1.10, 1.06][offset]
            elif years_exp == 1:
                development = [1.0, 1.04, 1.06, 1.04, 1.0][offset]
            career_ppg.append(
                projected_ppg * curve_ratio * development * (0.985**offset)
            )
        career_value = sum(
            value * 17 * (0.90**offset) for offset, value in enumerate(career_ppg)
        )

        signals: list[str] = []
        if is_rookie and draft_pick and draft_pick <= 105:
            signals.append("Rookie Upside")
        if xppg > baseline_ppg + 1.1:
            signals.append("Opportunity Buy")
        if (
            age is not None
            and age <= 25
            and (target_share >= 0.18 or wopr >= 0.42)
            and trend > 0
        ):
            signals.append("Breakout Profile")
        if td_regression >= 1.5:
            signals.append("TD Regression Up")
        elif td_regression <= -1.8:
            signals.append("TD Regression Down")
        if availability < 0.72:
            signals.append("Durability Risk")
        if age_factor < 0.86:
            signals.append("Age-Curve Risk")
        if snap_pct and snap_pct < 0.48:
            signals.append("Role Risk")
        if depth_rank >= 3:
            signals.append("Depth Chart Risk")
        if nfl_status != "ACT":
            signals.append(f"Roster Status {nfl_status}")
        if confidence >= 72 and projected_ppg >= baseline_ppg:
            signals.append("Stable Profile")
        if not signals:
            signals.append("Neutral")

        why_parts = [
            f"{baseline_ppg:.1f} recency-weighted PPG",
            f"{projected_ppg:.1f} adjusted PPG",
        ]
        if xppg > 0:
            why_parts.append(f"{xppg:.1f} expected-opportunity PPG")
        if target_share > 0:
            why_parts.append(f"{target_share:.0%} target share")
        if snap_pct > 0:
            why_parts.append(f"{snap_pct:.0%} offensive snaps")

        rows.append(
            {
                "Player ID": player_id,
                "Sleeper ID": str(getattr(roster, "sleeper_id", "") or ""),
                "Player": name,
                "Position": position,
                "Team": team,
                "Age": age,
                "Experience": years_exp,
                "Draft Pick": int(draft_pick) if draft_pick > 0 else None,
                "Projected PPG": round(projected_ppg, 2),
                "Projected Games": round(projected_games, 1),
                "Projected Season Points": round(projected_points, 1),
                "Floor PPG": round(floor_ppg, 2),
                "Ceiling PPG": round(ceiling_ppg, 2),
                "Expected Opportunity PPG": round(xppg, 2),
                "Target Share": round(target_share, 3),
                "WOPR": round(wopr, 3),
                "Snap Share": round(snap_pct, 3),
                "Depth Rank": depth_rank or None,
                "Depth Data As Of": depth_as_of,
                "NFL Status": nfl_status,
                "Team Environment": round(team_factor, 3),
                "Advanced Efficiency Z": round(advanced_z, 2),
                "Availability": round(availability, 3),
                "Risk Score": round(risk, 1),
                "Confidence": confidence,
                "Signal": " | ".join(signals[:3]),
                "Why": "; ".join(why_parts),
                f"{projection_season} PPG": round(career_ppg[0], 2),
                f"{projection_season + 1} PPG": round(career_ppg[1], 2),
                f"{projection_season + 2} PPG": round(career_ppg[2], 2),
                f"{projection_season + 3} PPG": round(career_ppg[3], 2),
                f"{projection_season + 4} PPG": round(career_ppg[4], 2),
                "3-Year Career Value": round(
                    sum(
                        value * 17 * (0.90**offset)
                        for offset, value in enumerate(career_ppg[:3])
                    ),
                    1,
                ),
                "5-Year Career Value": round(career_value, 1),
                "Career Direction": (
                    "Rising"
                    if career_ppg[2] > career_ppg[0] * 1.04
                    else "Declining"
                    if career_ppg[2] < career_ppg[0] * 0.90
                    else "Stable"
                ),
                "Latest Data Season": latest_completed,
                "Opportunity Data Season": latest_opp,
            }
        )

    board = pd.DataFrame(rows)
    board = board[board["Projected PPG"] > 0].copy()
    if board.empty:
        return board

    replacement_ranks = _replacement_ranks(league_size, lineup_mode)
    replacement_values: dict[str, float] = {}
    for position, rank in replacement_ranks.items():
        pool = board[board["Position"].eq(position)].sort_values(
            "Projected PPG", ascending=False
        )
        replacement_values[position] = (
            float(pool.iloc[min(rank - 1, len(pool) - 1)]["Projected PPG"])
            if not pool.empty
            else 0.0
        )
    board["Replacement PPG"] = board["Position"].map(replacement_values).fillna(0.0)
    board["VORP"] = (board["Projected PPG"] - board["Replacement PPG"]).round(2)
    position_weights = {
        "QB": 1.08 if lineup_mode == "Superflex" else 0.72,
        "RB": 1.0,
        "WR": 1.0,
        "TE": 0.90,
        "K": 0.08,
    }
    board["Position Value Weight"] = board["Position"].map(position_weights).fillna(0.5)
    board["Adjusted VORP"] = (board["VORP"] * board["Position Value Weight"]).round(2)
    board["Ceiling VORP"] = (
        (board["Ceiling PPG"] - board["Replacement PPG"])
        * board["Position Value Weight"]
    ).round(2)
    board["VORP Percentile"] = _percentile(board["Adjusted VORP"])
    board["Ceiling Percentile"] = _percentile(board["Ceiling VORP"])
    career_surplus = pd.Series(0.0, index=board.index)
    for offset in range(5):
        column = f"{projection_season + offset} PPG"
        career_surplus += (
            (board[column] - board["Replacement PPG"]).clip(lower=0)
            * 17
            * (0.90**offset)
            * board["Position Value Weight"]
        )
    board["Career Surplus Value"] = career_surplus.round(1)
    board["Career Percentile"] = _percentile(board["Career Surplus Value"])
    board["Win-Now Score"] = (
        board["VORP Percentile"] * 0.64
        + board["Ceiling Percentile"] * 0.22
        + board["Confidence"] * 0.14
    ).round(1)
    if strategy == "Dynasty":
        board["Draft Score"] = (
            board["Win-Now Score"] * 0.44 + board["Career Percentile"] * 0.56
        ).round(1)
    else:
        board["Draft Score"] = (
            board["Win-Now Score"] * 0.90 + board["Career Percentile"] * 0.10
        ).round(1)
    board["Tier"] = pd.cut(
        board["Draft Score"],
        bins=[-1, 45, 58, 70, 82, 92, 101],
        labels=["Depth", "Bench", "Flex", "Starter", "Elite", "League Winner"],
    ).astype(str)
    board = board.sort_values(
        ["Draft Score", "VORP", "Projected PPG"], ascending=False
    ).reset_index(drop=True)
    board.insert(0, "Overall Rank", np.arange(1, len(board) + 1))
    board["Position Rank"] = board.groupby("Position").cumcount() + 1
    board["Pos Rank"] = board["Position"] + board["Position Rank"].astype(str)
    return board


def walk_forward_backtest(
    stats: pd.DataFrame,
    scoring_name: str = "PPR",
    target_season: int | None = None,
    scoring_settings: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Compare a recency blend with a last-season PPG baseline."""
    frame = _prepare_stats(stats, scoring_name, scoring_settings)
    if frame.empty:
        return pd.DataFrame()
    target_season = target_season or int(frame["season"].max())
    target = frame[(frame["season"] == target_season) & (frame["games"] >= 6)].copy()
    history = frame[
        frame["season"].between(target_season - 2, target_season - 1)
    ].copy()
    rows: list[dict[str, Any]] = []
    for current in target.itertuples(index=False):
        prior = history[history["player_id"].eq(str(current.player_id))].sort_values(
            "season", ascending=False
        )
        if prior.empty:
            continue
        prior_ppg = [
            _safe_number(value)
            for value in prior["model_ppg"].tolist()
            if _safe_number(value) > 0
        ]
        if not prior_ppg:
            continue
        model_prediction = (
            prior_ppg[0]
            if len(prior_ppg) == 1
            else prior_ppg[0] * 0.68 + prior_ppg[1] * 0.32
        )
        baseline_prediction = prior_ppg[0]
        rows.append(
            {
                "Position": current.position,
                "Actual": _safe_number(current.model_ppg),
                "Model": model_prediction,
                "Baseline": baseline_prediction,
            }
        )
    predictions = pd.DataFrame(rows)
    if predictions.empty:
        return pd.DataFrame()

    metrics: list[dict[str, Any]] = []
    for position, group in [
        ("ALL", predictions),
        *list(predictions.groupby("Position")),
    ]:
        if len(group) < 4:
            continue
        model_mae = (group["Model"] - group["Actual"]).abs().mean()
        baseline_mae = (group["Baseline"] - group["Actual"]).abs().mean()
        metrics.append(
            {
                "Position": position,
                "Players": len(group),
                "Model MAE": round(model_mae, 3),
                "Prior-Year MAE": round(baseline_mae, 3),
                "MAE Edge": round(baseline_mae - model_mae, 3),
                "Model Rank Corr": round(
                    group["Model"].rank().corr(group["Actual"].rank()), 3
                ),
                "Prior-Year Rank Corr": round(
                    group["Baseline"].rank().corr(group["Actual"].rank()), 3
                ),
                "Target Season": target_season,
            }
        )
    return pd.DataFrame(metrics)
