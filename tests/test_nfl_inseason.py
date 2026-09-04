import pandas as pd

from nfl_inseason import (
    attach_manual_roster,
    attach_sleeper_rosters,
    current_matchup_opponent,
    fetch_sleeper_league_state,
    league_power_rankings,
    lineup_changes,
    matchup_outlook,
    optimize_lineup,
    starter_slots,
    waiver_recommendations,
)


def _board():
    rows = [
        ("1", "Alpha QB", "QB", 22, 16, 29, 90, 8, 360, 6),
        ("2", "Bravo RB", "RB", 18, 12, 25, 84, 12, 300, 5),
        ("3", "Charlie WR", "WR", 17, 11, 26, 82, 14, 285, 4),
        ("4", "Delta RB", "RB", 15, 9, 23, 76, 18, 250, 2),
        ("5", "Echo WR", "WR", 16, 10, 24, 80, 15, 265, 3),
        ("6", "Foxtrot TE", "TE", 12, 7, 19, 79, 16, 205, 1),
        ("7", "Golf RB", "RB", 17, 10, 27, 86, 11, 280, 4),
        ("8", "Hotel WR", "WR", 13, 8, 21, 74, 20, 220, 0),
    ]
    return pd.DataFrame(
        [
            {
                "Sleeper ID": pid,
                "Player": name,
                "Position": position,
                "Team": "AAA",
                "Projected PPG": ppg,
                "Floor PPG": floor,
                "Ceiling PPG": ceiling,
                "Confidence": confidence,
                "Risk Score": risk,
                "Projected Season Points": ros,
                "VORP": vorp,
                "Availability": 1.0,
                "NFL Status": "ACT",
                "Signal": "Stable Profile",
            }
            for pid, name, position, ppg, floor, ceiling, confidence, risk, ros, vorp in rows
        ]
    )


def _state():
    return {
        "users": [
            {"user_id": "u1", "display_name": "one", "metadata": {"team_name": "Team One"}},
            {"user_id": "u2", "display_name": "two", "metadata": {"team_name": "Team Two"}},
        ],
        "rosters": [
            {"roster_id": 1, "owner_id": "u1", "players": ["1", "2", "3", "4"], "starters": ["1", "2", "3"]},
            {"roster_id": 2, "owner_id": "u2", "players": ["5", "6", "8"], "starters": ["5", "6"]},
        ],
        "matchups": [
            {"roster_id": 1, "matchup_id": 4},
            {"roster_id": 2, "matchup_id": 4},
        ],
    }


def test_starter_slots_exclude_bench_and_label_duplicates():
    assert starter_slots(["QB", "RB", "RB", "FLEX", "BN", "IR"]) == [
        "QB",
        "RB1",
        "RB2",
        "FLEX",
    ]


def test_exact_lineup_optimizer_handles_flex_jointly():
    roster = _board().iloc[[1, 2, 3, 4]].copy()
    starters, bench = optimize_lineup(roster, ["RB", "WR", "FLEX", "BN"])
    assert set(starters["Player"]) == {"Bravo RB", "Charlie WR", "Echo WR"}
    assert bench.iloc[0]["Player"] == "Delta RB"


def test_roster_attachment_and_platform_lineup_comparison():
    league_board, metadata = attach_sleeper_rosters(_board(), _state())
    mine = league_board[league_board["Roster ID"].eq(1)]
    starters, _ = optimize_lineup(mine, ["QB", "RB", "WR", "FLEX"])
    changes = lineup_changes(starters, mine)
    assert metadata["matched"] == 7
    assert league_board.loc[league_board["Player"].eq("Alpha QB"), "Fantasy Team"].iloc[0] == "Team One"
    assert changes["start"] == ["Delta RB"]
    assert changes["projected_gain"] == 15.0


def test_waiver_wire_uses_actual_unrostered_pool_and_drop_candidate():
    league_board, _ = attach_sleeper_rosters(_board(), _state())
    waivers = waiver_recommendations(
        league_board,
        1,
        ["QB", "RB", "WR", "FLEX", "BN"],
        top_n=10,
    )
    golf = waivers[waivers["Player"].eq("Golf RB")].iloc[0]
    assert golf["Drop Candidate"] == "Delta RB"
    assert golf["Weekly Gain"] == 2.0
    assert golf["Action"] == "Add"
    assert "Echo WR" not in waivers["Player"].tolist()


def test_league_power_uses_optimized_starters_and_depth():
    league_board, _ = attach_sleeper_rosters(_board(), _state())
    power = league_power_rankings(
        league_board,
        ["QB", "RB", "WR", "FLEX"],
        {1: "Team One", 2: "Team Two"},
    )
    assert power.iloc[0]["Fantasy Team"] == "Team One"
    assert power.iloc[0]["Power Rank"] == 1


def test_current_matchup_outlook_compares_optimized_rosters():
    state = _state()
    league_board, _ = attach_sleeper_rosters(_board(), state)
    opponent_id = current_matchup_opponent(state, 1)
    outlook = matchup_outlook(
        league_board, 1, opponent_id, ["QB", "RB", "WR", "FLEX"]
    )
    assert opponent_id == 2
    assert outlook["my_projected_ppg"] > outlook["opponent_projected_ppg"]
    assert outlook["baseline_win_probability"] > 0.5


def test_private_espn_manual_roster_matches_normalized_names():
    board, unmatched = attach_manual_roster(
        _board(), ["Alpha QB", "Charlie-WR", "Missing Player"], team_name="Sherlock"
    )
    mine = board[board["Roster ID"].eq(1)]
    assert set(mine["Player"]) == {"Alpha QB", "Charlie WR"}
    assert unmatched == ["Missing Player"]


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def get(self, url, **kwargs):
        if "/league/abc/rosters" in url:
            return _Response([])
        if "/league/abc/users" in url:
            return _Response([])
        if "/league/abc/drafts" in url:
            return _Response([{"status": "complete"}])
        if "/league/abc/matchups/4" in url:
            return _Response([])
        return _Response({"settings": {"leg": 4}, "status": "in_season"})


def test_sleeper_state_loader_uses_current_week_without_authentication():
    state = fetch_sleeper_league_state("abc", session=_Session())
    assert state["week"] == 4
    assert state["drafts"][0]["status"] == "complete"
