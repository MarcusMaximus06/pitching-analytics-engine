"""League-aware NFL fantasy lineup, waiver, and roster-strength analysis.

The module keeps public-data access separate from deterministic decision logic so
the recommendations can be tested without a live league or network connection.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd
import requests
from scipy.optimize import linear_sum_assignment

INSEASON_MODEL_VERSION = "1.0.0"

NON_STARTER_SLOTS = {"BN", "BENCH", "IR", "TAXI"}
SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF", "DST"},
    "DST": {"DEF", "DST"},
    "FLEX": {"RB", "WR", "TE"},
    "W/R/T": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}


def _normal_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def fetch_sleeper_league_state(
    league_id: str,
    *,
    session: Any = requests,
) -> dict[str, Any]:
    """Load a Sleeper league through public endpoints; no login is required."""
    league_id = str(league_id).strip()
    if not league_id:
        raise ValueError("A Sleeper league ID is required.")
    base = "https://api.sleeper.app/v1"

    def get_json(path: str) -> Any:
        response = session.get(
            f"{base}{path}",
            timeout=(8, 25),
            headers={"User-Agent": f"HagLabs-NFL-InSeason/{INSEASON_MODEL_VERSION}"},
        )
        response.raise_for_status()
        return response.json()

    league = get_json(f"/league/{league_id}") or {}
    rosters = get_json(f"/league/{league_id}/rosters") or []
    users = get_json(f"/league/{league_id}/users") or []
    drafts = get_json(f"/league/{league_id}/drafts") or []
    week = int((league.get("settings") or {}).get("leg") or 1)
    try:
        matchups = get_json(f"/league/{league_id}/matchups/{week}") or []
    except requests.RequestException:
        matchups = []
    return {
        "league": league,
        "rosters": rosters,
        "users": users,
        "drafts": drafts,
        "matchups": matchups,
        "week": week,
    }


def league_team_names(state: dict[str, Any]) -> dict[int, str]:
    """Map Sleeper roster IDs to user-facing fantasy team names."""
    users = {
        str(user.get("user_id")): user
        for user in state.get("users", [])
        if user.get("user_id") is not None
    }
    names: dict[int, str] = {}
    for roster in state.get("rosters", []):
        roster_id = int(roster.get("roster_id") or 0)
        user = users.get(str(roster.get("owner_id")), {})
        metadata = user.get("metadata") or {}
        names[roster_id] = str(
            metadata.get("team_name")
            or metadata.get("team_name_update")
            or user.get("display_name")
            or f"Roster {roster_id}"
        )
    return names


def attach_sleeper_rosters(
    board: pd.DataFrame,
    state: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach current Sleeper ownership and starter status to a model board."""
    enriched = board.copy()
    if enriched.empty:
        return enriched, {"matched": 0, "league_players": 0, "unmatched_ids": []}
    enriched["Sleeper ID"] = enriched["Sleeper ID"].fillna("").astype(str)
    owners: dict[str, int] = {}
    starters: set[tuple[int, str]] = set()
    league_player_ids: set[str] = set()
    for roster in state.get("rosters", []):
        roster_id = int(roster.get("roster_id") or 0)
        for player_id in roster.get("players") or []:
            key = str(player_id)
            league_player_ids.add(key)
            owners[key] = roster_id
        for player_id in roster.get("starters") or []:
            starters.add((roster_id, str(player_id)))
    names = league_team_names(state)
    enriched["Roster ID"] = enriched["Sleeper ID"].map(owners).astype("Int64")
    enriched["Fantasy Team"] = enriched["Roster ID"].map(names)
    enriched["League Starter"] = [
        (int(roster_id), player_id) in starters if pd.notna(roster_id) else False
        for roster_id, player_id in zip(
            enriched["Roster ID"], enriched["Sleeper ID"], strict=False
        )
    ]
    matched_ids = set(enriched.loc[enriched["Roster ID"].notna(), "Sleeper ID"])
    return enriched, {
        "matched": len(matched_ids),
        "league_players": len(league_player_ids),
        "unmatched_ids": sorted(league_player_ids - matched_ids),
    }


def attach_manual_roster(
    board: pd.DataFrame,
    player_names: Iterable[str],
    *,
    team_name: str = "My Team",
) -> tuple[pd.DataFrame, list[str]]:
    """Attach a manually entered roster by normalized player name."""
    enriched = board.copy()
    requested = [str(name).strip() for name in player_names if str(name).strip()]
    requested_keys = {_normal_name(name) for name in requested}
    board_keys = enriched["Player"].map(_normal_name)
    mine = board_keys.isin(requested_keys)
    enriched["Roster ID"] = pd.Series(pd.NA, index=enriched.index, dtype="Int64")
    enriched.loc[mine, "Roster ID"] = 1
    enriched["Fantasy Team"] = np.where(mine, team_name, pd.NA)
    enriched["League Starter"] = False
    matched_keys = set(board_keys[mine])
    unmatched = [name for name in requested if _normal_name(name) not in matched_keys]
    return enriched, unmatched


def starter_slots(roster_positions: Iterable[str] | None) -> list[str]:
    """Expand a platform roster template into uniquely labeled starter slots."""
    positions = [str(slot).upper() for slot in (roster_positions or [])]
    if not positions:
        positions = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    counts: dict[str, int] = {}
    labels: list[str] = []
    totals = {slot: positions.count(slot) for slot in positions}
    for slot in positions:
        if slot in NON_STARTER_SLOTS:
            continue
        counts[slot] = counts.get(slot, 0) + 1
        labels.append(f"{slot}{counts[slot]}" if totals[slot] > 1 else slot)
    return labels


def _slot_type(label: str) -> str:
    return re.sub(r"\d+$", "", str(label).upper())


def _eligible(slot_label: str, position: str) -> bool:
    slot = _slot_type(slot_label)
    return str(position).upper() in SLOT_ELIGIBILITY.get(slot, {slot})


def lineup_score(frame: pd.DataFrame, mode: str = "Balanced") -> pd.Series:
    """Create a transparent risk preference score from model intervals."""
    projected = pd.to_numeric(frame.get("Projected PPG"), errors="coerce").fillna(0.0)
    floor = pd.to_numeric(frame.get("Floor PPG", projected), errors="coerce").fillna(projected)
    ceiling = pd.to_numeric(frame.get("Ceiling PPG", projected), errors="coerce").fillna(projected)
    if mode == "Floor":
        score = projected * 0.55 + floor * 0.45
    elif mode == "Upside":
        score = projected * 0.65 + ceiling * 0.35
    else:
        score = projected
    availability = pd.to_numeric(
        frame.get("Availability", pd.Series(1.0, index=frame.index)), errors="coerce"
    ).fillna(1.0).clip(0.0, 1.0)
    score *= 0.90 + 0.10 * availability
    inactive = frame.get("NFL Status", pd.Series("", index=frame.index)).astype(str).str.upper()
    score = score.mask(inactive.isin({"IR", "OUT", "PUP", "SUS", "RES"}), -1000.0)
    return score


def optimize_lineup(
    roster: pd.DataFrame,
    roster_positions: Iterable[str] | None,
    *,
    mode: str = "Balanced",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Find the maximum-score valid lineup with exact assignment optimization."""
    players = roster.copy().reset_index(drop=True)
    slots = starter_slots(roster_positions)
    if players.empty or not slots:
        return pd.DataFrame(), players
    players["Lineup Score"] = lineup_score(players, mode)
    slot_count = len(slots)
    player_count = len(players)
    column_count = max(slot_count, player_count)
    costs = np.full((slot_count, column_count), 1_000_000.0)
    for slot_index, slot in enumerate(slots):
        for player_index, row in players.iterrows():
            if _eligible(slot, str(row.get("Position", ""))):
                costs[slot_index, player_index] = -float(row["Lineup Score"])
    rows, columns = linear_sum_assignment(costs)
    chosen: list[dict[str, Any]] = []
    used_players: set[int] = set()
    for slot_index, player_index in zip(rows, columns, strict=False):
        slot = slots[int(slot_index)]
        if player_index >= player_count or costs[slot_index, player_index] >= 999_999:
            chosen.append({"Lineup Slot": slot, "Player": "OPEN", "Lineup Score": 0.0})
            continue
        used_players.add(int(player_index))
        row = players.iloc[int(player_index)].to_dict()
        row["Lineup Slot"] = slot
        chosen.append(row)
    starters = pd.DataFrame(chosen)
    bench = players.loc[~players.index.isin(used_players)].copy()
    if not bench.empty:
        bench = bench.sort_values("Lineup Score", ascending=False).reset_index(drop=True)
    return starters, bench


def lineup_changes(
    recommended: pd.DataFrame,
    roster: pd.DataFrame,
) -> dict[str, Any]:
    """Compare the optimized lineup with the platform's saved starters."""
    suggested = set(recommended.loc[recommended["Player"].ne("OPEN"), "Sleeper ID"].astype(str))
    current = set(roster.loc[roster.get("League Starter", False).eq(True), "Sleeper ID"].astype(str))
    by_id = roster.set_index(roster["Sleeper ID"].astype(str))["Player"].to_dict()
    add_ids = suggested - current
    bench_ids = current - suggested
    current_points = pd.to_numeric(
        roster.loc[roster["Sleeper ID"].astype(str).isin(current), "Projected PPG"],
        errors="coerce",
    ).sum()
    recommended_points = pd.to_numeric(
        recommended.loc[recommended["Player"].ne("OPEN"), "Projected PPG"],
        errors="coerce",
    ).sum()
    return {
        "start": [by_id[player_id] for player_id in add_ids if player_id in by_id],
        "bench": [by_id[player_id] for player_id in bench_ids if player_id in by_id],
        "current_points": float(current_points),
        "recommended_points": float(recommended_points),
        "projected_gain": float(recommended_points - current_points),
    }


def waiver_recommendations(
    league_board: pd.DataFrame,
    my_roster_id: int,
    roster_positions: Iterable[str] | None,
    *,
    mode: str = "Balanced",
    top_n: int = 40,
) -> pd.DataFrame:
    """Rank unrostered players by estimated improvement over a practical drop."""
    if league_board.empty:
        return pd.DataFrame()
    mine = league_board[league_board["Roster ID"].eq(my_roster_id)].copy()
    available = league_board[league_board["Roster ID"].isna()].copy()
    if mine.empty or available.empty:
        return pd.DataFrame()
    _, bench = optimize_lineup(mine, roster_positions, mode=mode)
    mine["Lineup Score"] = lineup_score(mine, mode)
    if bench.empty:
        bench = mine.copy()
    available["Lineup Score"] = lineup_score(available, mode)
    rows: list[dict[str, Any]] = []
    for _, candidate in available.iterrows():
        position = str(candidate.get("Position", ""))
        comparable = mine[mine["Position"].eq(position)]
        if comparable.empty:
            comparable = bench
        if comparable.empty:
            comparable = mine
        drop = comparable.sort_values("Lineup Score", ascending=True).iloc[0]
        weekly_gain = float(candidate["Lineup Score"] - drop["Lineup Score"])
        ros_gain = float(candidate.get("Projected Season Points", 0.0)) - float(
            drop.get("Projected Season Points", 0.0)
        )
        upside_gain = float(candidate.get("Ceiling PPG", 0.0)) - float(
            drop.get("Ceiling PPG", 0.0)
        )
        priority = (
            weekly_gain * 4.0
            + max(-3.0, float(candidate.get("VORP", 0.0)))
            + upside_gain * 0.35
            + float(candidate.get("Confidence", 0.0)) / 20.0
            - float(candidate.get("Risk Score", 0.0)) / 18.0
        )
        row = candidate.to_dict()
        row.update(
            {
                "Drop Candidate": drop.get("Player", ""),
                "Weekly Gain": round(weekly_gain, 2),
                "ROS Gain": round(ros_gain, 1),
                "Upside Gain": round(upside_gain, 2),
                "Waiver Priority": round(priority, 2),
                "Action": "Add" if weekly_gain > 0.35 else "Watch",
            }
        )
        rows.append(row)
    result = pd.DataFrame(rows).sort_values(
        ["Waiver Priority", "Lineup Score"], ascending=False
    )
    result.insert(0, "Waiver Rank", np.arange(1, len(result) + 1))
    return result.head(top_n).reset_index(drop=True)


def league_power_rankings(
    league_board: pd.DataFrame,
    roster_positions: Iterable[str] | None,
    team_names: dict[int, str],
    *,
    mode: str = "Balanced",
) -> pd.DataFrame:
    """Compare optimized starters and usable bench depth across the league."""
    rows: list[dict[str, Any]] = []
    owned = league_board[league_board["Roster ID"].notna()].copy()
    for roster_id, roster in owned.groupby("Roster ID"):
        roster_id = int(roster_id)
        starters, bench = optimize_lineup(roster, roster_positions, mode=mode)
        real_starters = starters[starters["Player"].ne("OPEN")]
        starter_points = pd.to_numeric(real_starters.get("Projected PPG"), errors="coerce").sum()
        floor_points = pd.to_numeric(real_starters.get("Floor PPG"), errors="coerce").sum()
        ceiling_points = pd.to_numeric(real_starters.get("Ceiling PPG"), errors="coerce").sum()
        depth_points = pd.to_numeric(bench.head(3).get("Projected PPG"), errors="coerce").sum()
        power = starter_points * 0.72 + ceiling_points * 0.18 + depth_points * 0.10
        rows.append(
            {
                "Roster ID": roster_id,
                "Fantasy Team": team_names.get(roster_id, f"Roster {roster_id}"),
                "Starter PPG": round(float(starter_points), 2),
                "Floor PPG": round(float(floor_points), 2),
                "Ceiling PPG": round(float(ceiling_points), 2),
                "Bench Depth": round(float(depth_points), 2),
                "Matched Players": len(roster),
                "Power Score": round(float(power), 2),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values("Power Score", ascending=False).reset_index(drop=True)
    result.insert(0, "Power Rank", np.arange(1, len(result) + 1))
    return result


def current_matchup_opponent(state: dict[str, Any], roster_id: int) -> int | None:
    """Return the opponent roster ID sharing the user's current matchup."""
    matchup_rows = state.get("matchups", [])
    mine = next(
        (
            row
            for row in matchup_rows
            if int(row.get("roster_id") or 0) == int(roster_id)
        ),
        None,
    )
    if not mine or mine.get("matchup_id") is None:
        return None
    matchup_id = mine.get("matchup_id")
    opponent = next(
        (
            row
            for row in matchup_rows
            if row.get("matchup_id") == matchup_id
            and int(row.get("roster_id") or 0) != int(roster_id)
        ),
        None,
    )
    return int(opponent.get("roster_id")) if opponent else None


def matchup_outlook(
    league_board: pd.DataFrame,
    my_roster_id: int,
    opponent_roster_id: int,
    roster_positions: Iterable[str] | None,
    *,
    mode: str = "Balanced",
) -> dict[str, float]:
    """Estimate a baseline head-to-head edge from two optimized roster lineups."""
    outputs: dict[str, dict[str, float]] = {}
    for label, roster_id in (("my", my_roster_id), ("opponent", opponent_roster_id)):
        roster = league_board[league_board["Roster ID"].eq(roster_id)]
        starters, _ = optimize_lineup(roster, roster_positions, mode=mode)
        starters = starters[starters["Player"].ne("OPEN")]
        mean = float(pd.to_numeric(starters.get("Projected PPG"), errors="coerce").sum())
        floor = pd.to_numeric(starters.get("Floor PPG"), errors="coerce").fillna(0.0)
        ceiling = pd.to_numeric(starters.get("Ceiling PPG"), errors="coerce").fillna(0.0)
        player_sd = ((ceiling - floor).clip(lower=0.0) / 3.29).to_numpy(dtype=float)
        outputs[label] = {"mean": mean, "variance": float(np.square(player_sd).sum())}
    difference = outputs["my"]["mean"] - outputs["opponent"]["mean"]
    difference_sd = math.sqrt(
        max(1.0, outputs["my"]["variance"] + outputs["opponent"]["variance"])
    )
    probability = 0.5 * (1.0 + math.erf(difference / (difference_sd * math.sqrt(2.0))))
    return {
        "my_projected_ppg": round(outputs["my"]["mean"], 2),
        "opponent_projected_ppg": round(outputs["opponent"]["mean"], 2),
        "projected_margin": round(difference, 2),
        "baseline_win_probability": float(np.clip(probability, 0.02, 0.98)),
    }
