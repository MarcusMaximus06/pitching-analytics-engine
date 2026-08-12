"""Market-aware, league-aware live NFL draft recommendations."""

from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

DYNASTY_MARKET_URL = "https://raw.githubusercontent.com/DynastyProcess/data/master/files/values-players.csv"
REDRAFT_MARKET_URL = "https://raw.githubusercontent.com/DynastyProcess/data/master/files/db_fpecr_latest.csv"


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _cache_root() -> Path:
    configured = os.getenv("HAGLABS_NFL_CACHE_DIR")
    if configured:
        return Path(configured)
    if os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "HagLabs" / "nfl_fantasy_cache"
    return Path.home() / ".cache" / "haglabs" / "nfl_fantasy_cache"


def _download_csv(url: str, max_age_hours: float = 12) -> pd.DataFrame:
    cache_root = _cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_path = cache_root / f"market_{key}.csv"
    age_hours = (
        (datetime.now(UTC).timestamp() - cache_path.stat().st_mtime) / 3600
        if cache_path.exists()
        else float("inf")
    )
    if age_hours <= max_age_hours:
        return pd.read_csv(cache_path)
    try:
        response = requests.get(
            url,
            timeout=(10, 45),
            headers={"User-Agent": "HagLabs-NFL-Draft/2.0", "Accept": "text/csv, */*"},
        )
        response.raise_for_status()
        frame = pd.read_csv(BytesIO(response.content))
        temporary = cache_path.with_suffix(".tmp.csv")
        temporary.write_bytes(response.content)
        temporary.replace(cache_path)
        return frame
    except (requests.RequestException, OSError, ValueError):
        if cache_path.exists():
            return pd.read_csv(cache_path)
        raise


def load_market_rankings(
    strategy: str,
    lineup_mode: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a current market anchor without treating consensus as truth."""
    if strategy == "Dynasty":
        raw = _download_csv(DYNASTY_MARKET_URL)
        rank_column = "ecr_2qb" if lineup_mode == "Superflex" else "ecr_1qb"
        value_column = "value_2qb" if lineup_mode == "Superflex" else "value_1qb"
        market = raw.rename(
            columns={
                "player": "Player",
                "pos": "Position",
                rank_column: "Market Rank",
                value_column: "Market Value",
                "scrape_date": "Market Date",
            }
        )
        market["Market SD"] = (
            8.0
            + pd.to_numeric(market["Market Rank"], errors="coerce").fillna(100) * 0.055
        )
        source = "DynastyProcess weekly 1QB/2QB values"
    else:
        raw = _download_csv(REDRAFT_MARKET_URL)
        market = raw[raw["page_type"].eq("redraft-overall")].copy()
        market = market.rename(
            columns={
                "player": "Player",
                "pos": "Position",
                "ecr": "Market Rank",
                "sd": "Market SD",
                "scrape_date": "Market Date",
            }
        )
        market["Market Value"] = np.nan
        source = "DynastyProcess current redraft ECR archive"

    columns = [
        "Player",
        "Position",
        "Market Rank",
        "Market SD",
        "Market Value",
        "Market Date",
    ]
    market = market[[column for column in columns if column in market.columns]].copy()
    market["Position"] = (
        market["Position"].astype(str).str.upper().replace({"DST": "DEF"})
    )
    market["Market Rank"] = pd.to_numeric(market["Market Rank"], errors="coerce")
    market["Market SD"] = (
        pd.to_numeric(market["Market SD"], errors="coerce").fillna(10.0).clip(3, 40)
    )
    market["Name Key"] = market["Player"].map(_normalize_name)
    market = market.dropna(subset=["Market Rank"]).sort_values("Market Rank")
    market = market.drop_duplicates(["Name Key", "Position"], keep="first")
    dates = market.get("Market Date", pd.Series(dtype=str)).dropna().astype(str)
    return market, {
        "source": source,
        "source_url": "https://github.com/DynastyProcess/data",
        "as_of": dates.max() if not dates.empty else "unknown",
        "matched": 0,
    }


def attach_market_rankings(
    board: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    if board.empty:
        return board.copy()
    out = board.copy()
    out["Name Key"] = out["Player"].map(_normalize_name)
    if market.empty:
        out["Market Rank"] = np.nan
        out["Market SD"] = np.nan
        out["Market Value"] = np.nan
        out["Market Date"] = None
    else:
        fields = [
            "Name Key",
            "Position",
            "Market Rank",
            "Market SD",
            "Market Value",
            "Market Date",
        ]
        available = [column for column in fields if column in market.columns]
        out = out.merge(market[available], on=["Name Key", "Position"], how="left")
    out["Market Edge"] = (out["Market Rank"] - out["Overall Rank"]).round(1)
    out["Market Match"] = out["Market Rank"].notna()
    return out.drop(columns=["Name Key"])


def snake_pick_numbers(league_size: int, draft_slot: int, rounds: int) -> list[int]:
    if league_size < 1 or not 1 <= draft_slot <= league_size or rounds < 1:
        return []
    picks: list[int] = []
    for round_number in range(1, rounds + 1):
        if round_number % 2:
            picks.append((round_number - 1) * league_size + draft_slot)
        else:
            picks.append(round_number * league_size - draft_slot + 1)
    return picks


def draft_context(
    league_size: int,
    draft_slot: int,
    rounds: int,
    drafted_count: int,
) -> dict[str, Any]:
    current_pick = max(1, drafted_count + 1)
    user_picks = snake_pick_numbers(league_size, draft_slot, rounds)
    remaining = [pick for pick in user_picks if pick >= current_pick]
    next_pick = remaining[0] if remaining else None
    following_pick = remaining[1] if len(remaining) > 1 else None
    return {
        "current_pick": current_pick,
        "round": math.ceil(current_pick / league_size),
        "is_user_turn": next_pick == current_pick,
        "next_user_pick": next_pick,
        "following_user_pick": following_pick,
        "picks_until_turn": max(0, next_pick - current_pick) if next_pick else None,
        "user_picks": user_picks,
    }


def infer_roster_requirements(roster_positions: list[str] | None) -> dict[str, int]:
    positions = [str(position).upper() for position in (roster_positions or [])]
    if not positions:
        positions = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    return {
        "QB": positions.count("QB") + positions.count("SUPER_FLEX"),
        "RB": positions.count("RB"),
        "WR": positions.count("WR"),
        "TE": positions.count("TE"),
        "FLEX": positions.count("FLEX") + positions.count("W/R/T"),
        "K": positions.count("K"),
        "DEF": positions.count("DEF") + positions.count("DST"),
        "BENCH": positions.count("BN") + positions.count("BENCH"),
    }


def _availability_probability(
    market_rank: float, market_sd: float, future_pick: int | None
) -> float:
    if future_pick is None or not math.isfinite(market_rank):
        return 0.0
    sd = max(3.0, market_sd if math.isfinite(market_sd) else 10.0)
    z_score = (future_pick - market_rank) / sd
    cdf = 0.5 * (1 + math.erf(z_score / math.sqrt(2)))
    return float(np.clip(1 - cdf, 0, 1))


def _need_adjustment(
    position: str,
    roster_counts: dict[str, int],
    requirements: dict[str, int],
    lineup_mode: str,
    strategy: str,
    round_number: int,
    rounds: int,
) -> tuple[float, str]:
    required = requirements.get(position, 0)
    current = roster_counts.get(position, 0)
    if current < required:
        return 9.0, f"open {position} starter"
    if position in {"RB", "WR", "TE"}:
        flex_players = sum(roster_counts.get(item, 0) for item in ("RB", "WR", "TE"))
        flex_required = sum(requirements.get(item, 0) for item in ("RB", "WR", "TE"))
        flex_required += requirements.get("FLEX", 0)
        if flex_players < flex_required:
            return 6.0, "open FLEX starter"
    if position == "QB" and lineup_mode == "1QB" and current >= 1:
        return (-10.0 if current == 1 else -18.0), "1QB depth can wait"
    if position == "TE" and current >= 1:
        return -4.0, "TE starter already filled"
    if position == "K":
        if round_number < max(1, rounds - 2):
            return -34.0, "kicker should wait"
        return 2.0, "late-round kicker window"
    if strategy == "Dynasty" and position in {"RB", "WR", "TE", "QB"}:
        return 2.5, "dynasty bench value"
    return 0.0, "best available depth"


def recommend_draft_picks(
    board: pd.DataFrame,
    drafted_players: list[str],
    my_roster: list[str],
    league_size: int,
    draft_slot: int,
    rounds: int,
    lineup_mode: str,
    strategy: str,
    roster_positions: list[str] | None = None,
    top_n: int = 15,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Rank available players for the user's next turn and explain wait risk."""
    context = draft_context(league_size, draft_slot, rounds, len(drafted_players))
    if board.empty:
        return board.copy(), context
    drafted_keys = {_normalize_name(player) for player in drafted_players}
    roster_keys = {_normalize_name(player) for player in my_roster}
    available = board[~board["Player"].map(_normalize_name).isin(drafted_keys)].copy()
    roster_rows = board[board["Player"].map(_normalize_name).isin(roster_keys)]
    roster_counts = roster_rows["Position"].value_counts().to_dict()
    requirements = infer_roster_requirements(roster_positions)
    future_pick = (
        context["following_user_pick"]
        if context["is_user_turn"]
        else context["next_user_pick"]
    )
    max_rank = max(float(board["Overall Rank"].max()), 2.0)
    round_number = int(context["round"])

    selection_scores: list[float] = []
    survival_values: list[float] = []
    need_values: list[str] = []
    recommendation_reasons: list[str] = []
    for _, row in available.iterrows():
        model_rank = float(row["Overall Rank"])
        draft_score = float(row["Draft Score"])
        confidence = float(row["Confidence"])
        market_rank_raw = row.get("Market Rank", np.nan)
        market_sd_raw = row.get("Market SD", np.nan)
        market_rank = (
            float(market_rank_raw) if pd.notna(market_rank_raw) else model_rank
        )
        market_sd = float(market_sd_raw) if pd.notna(market_sd_raw) else 12.0
        survival = _availability_probability(market_rank, market_sd, future_pick)
        need_bonus, need_reason = _need_adjustment(
            str(row["Position"]),
            roster_counts,
            requirements,
            lineup_mode,
            strategy,
            round_number,
            rounds,
        )
        market_quality = 100 * (1 - min(market_rank, max_rank) / max_rank)
        edge = float(np.clip(market_rank - model_rank, -35, 35))
        urgency = 1 - survival
        selection_score = (
            draft_score * 0.62
            + market_quality * 0.14
            + edge * 0.28
            + confidence * 0.06
            + urgency * 12.0
            + need_bonus
        )
        selection_scores.append(round(selection_score, 2))
        survival_values.append(round(survival * 100, 1))
        need_values.append(need_reason)
        recommendation_reasons.append(
            f"model #{int(model_rank)}; market ~{market_rank:.0f}; "
            f"{survival * 100:.0f}% chance to reach pick {future_pick or 'N/A'}; {need_reason}"
        )

    available["Selection Score"] = selection_scores
    available["Chance Available Next Turn"] = survival_values
    available["Roster Fit"] = need_values
    available["Recommendation"] = recommendation_reasons
    available = available.sort_values(
        ["Selection Score", "Draft Score", "Confidence"], ascending=False
    ).reset_index(drop=True)
    available.insert(0, "Recommendation Rank", np.arange(1, len(available) + 1))
    return available.head(top_n), context


def fetch_sleeper_draft_picks(draft_id: str) -> pd.DataFrame:
    """Read the public Sleeper draft feed; no token or login is used."""
    response = requests.get(
        f"https://api.sleeper.app/v1/draft/{draft_id}/picks",
        timeout=(8, 20),
        headers={"User-Agent": "HagLabs-NFL-Draft/2.0"},
    )
    response.raise_for_status()
    rows: list[dict[str, Any]] = []
    for pick in response.json():
        metadata = pick.get("metadata") or {}
        name = metadata.get("first_name", "") + " " + metadata.get("last_name", "")
        rows.append(
            {
                "Pick": int(pick.get("pick_no", len(rows) + 1)),
                "Round": int(pick.get("round", 0)),
                "Roster ID": int(pick.get("roster_id", 0)),
                "Sleeper ID": str(pick.get("player_id", "")),
                "Player": name.strip() or str(metadata.get("player_name", "")),
                "Position": str(metadata.get("position", "")),
                "Team": str(metadata.get("team", "")),
            }
        )
    return pd.DataFrame(rows).sort_values("Pick") if rows else pd.DataFrame()
