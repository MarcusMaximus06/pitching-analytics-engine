import pandas as pd

from nfl_draft_assistant import (
    attach_market_rankings,
    draft_context,
    recommend_draft_picks,
    snake_pick_numbers,
)
from nfl_fantasy_model import (
    build_fantasy_board,
    score_player_stats,
    walk_forward_backtest,
)


def _stat_row(player_id, name, position, season, games, **stats):
    row = {
        "player_id": player_id,
        "player_display_name": name,
        "position": position,
        "season": season,
        "season_type": "REG",
        "recent_team": "AAA",
        "games": games,
    }
    row.update(stats)
    return row


def _bundle(stats, roster_rows):
    return {
        "stats": pd.DataFrame(stats),
        "rosters": pd.DataFrame(roster_rows),
        "players": pd.DataFrame(),
        "opportunity": pd.DataFrame(),
        "snaps": pd.DataFrame(),
        "depth": pd.DataFrame(),
        "combine": pd.DataFrame(),
        "draft": pd.DataFrame(),
        "ngs_receiving": pd.DataFrame(),
        "ngs_rushing": pd.DataFrame(),
        "ngs_passing": pd.DataFrame(),
    }


def test_ppr_scoring_rewards_receptions():
    stats = pd.DataFrame(
        [
            {
                "receptions": 80,
                "receiving_yards": 800,
                "receiving_tds": 5,
            }
        ]
    )
    ppr = score_player_stats(stats, "PPR").iloc[0]
    espn = score_player_stats(stats, "ESPN PPR").iloc[0]
    sleeper = score_player_stats(stats, "Sleeper PPR").iloc[0]
    standard = score_player_stats(stats, "Standard").iloc[0]
    assert ppr - standard == 80
    assert espn == ppr
    assert sleeper == ppr


def test_custom_sleeper_scoring_applies_league_specific_rules():
    stats = pd.DataFrame(
        [
            {
                "position": "RB",
                "games": 1,
                "passing_tds": 2,
                "passing_interceptions": 1,
                "rushing_first_downs": 10,
                "receptions": 4,
            }
        ]
    )
    custom = {
        "pass_td": 5.0,
        "pass_int": -1.0,
        "rush_fd": 0.1,
        "rec": 1.0,
        "bonus_rec_rb": 0.25,
    }
    assert score_player_stats(stats, "CGC PigSkin Custom", custom).iloc[0] == 15.0


def test_custom_sleeper_kicker_scoring_uses_exact_made_distance():
    stats = pd.DataFrame(
        [
            {
                "position": "K",
                "fg_made_distance": 95,
                "fg_missed": 1,
                "pat_made": 3,
                "pat_missed": 1,
            }
        ]
    )
    custom = {"fgm_yds": 0.1, "fgmiss": 0.0, "xpm": 1.0, "xpmiss": -1.0}
    assert score_player_stats(stats, "CGC PigSkin Custom", custom).iloc[0] == 11.5


def test_board_changes_with_scoring_and_builds_career_outlook():
    stats = [
        _stat_row(
            "p1",
            "Pass Catcher",
            "RB",
            2024,
            16,
            carries=170,
            rushing_yards=700,
            rushing_tds=5,
            receptions=70,
            receiving_yards=600,
            receiving_tds=3,
        ),
        _stat_row(
            "p1",
            "Pass Catcher",
            "RB",
            2025,
            17,
            carries=180,
            rushing_yards=760,
            rushing_tds=6,
            receptions=76,
            receiving_yards=640,
            receiving_tds=3,
        ),
        _stat_row(
            "p2",
            "Early Down",
            "RB",
            2024,
            17,
            carries=250,
            rushing_yards=1100,
            rushing_tds=10,
            receptions=12,
            receiving_yards=80,
        ),
        _stat_row(
            "p2",
            "Early Down",
            "RB",
            2025,
            17,
            carries=255,
            rushing_yards=1120,
            rushing_tds=10,
            receptions=10,
            receiving_yards=70,
        ),
    ]
    rosters = [
        {
            "gsis_id": "p1",
            "position": "RB",
            "full_name": "Pass Catcher",
            "team": "AAA",
            "birth_date": "2002-01-01",
            "years_exp": 3,
            "week": 1,
        },
        {
            "gsis_id": "p2",
            "position": "RB",
            "full_name": "Early Down",
            "team": "BBB",
            "birth_date": "1999-01-01",
            "years_exp": 5,
            "week": 1,
        },
    ]
    bundle = _bundle(stats, rosters)
    ppr = build_fantasy_board(bundle, "PPR", projection_season=2026)
    standard = build_fantasy_board(bundle, "Standard", projection_season=2026)

    ppr_gap = (
        ppr.set_index("Player").loc["Pass Catcher", "Projected PPG"]
        - ppr.set_index("Player").loc["Early Down", "Projected PPG"]
    )
    standard_gap = (
        standard.set_index("Player").loc["Pass Catcher", "Projected PPG"]
        - standard.set_index("Player").loc["Early Down", "Projected PPG"]
    )
    assert ppr_gap > standard_gap
    assert "2028 PPG" in ppr.columns
    assert ppr["Draft Score"].between(0, 100).all()


def test_superflex_changes_qb_replacement_value():
    stats = []
    rosters = []
    for index, ppg_yards in enumerate([5000, 4200, 3400], start=1):
        player_id = f"q{index}"
        stats.append(
            _stat_row(
                player_id,
                f"QB {index}",
                "QB",
                2025,
                17,
                passing_yards=ppg_yards,
                passing_tds=30 - index,
            )
        )
        rosters.append(
            {
                "gsis_id": player_id,
                "position": "QB",
                "full_name": f"QB {index}",
                "team": "AAA",
                "birth_date": "1998-01-01",
                "years_exp": 4,
                "week": 1,
            }
        )
    bundle = _bundle(stats, rosters)
    one_qb = build_fantasy_board(
        bundle, "PPR", league_size=1, lineup_mode="1QB", projection_season=2026
    )
    superflex = build_fantasy_board(
        bundle, "PPR", league_size=1, lineup_mode="Superflex", projection_season=2026
    )
    top_one_qb = one_qb.set_index("Player").loc["QB 1", "VORP"]
    top_superflex = superflex.set_index("Player").loc["QB 1", "VORP"]
    assert top_superflex > top_one_qb


def test_walk_forward_backtest_reports_baseline_comparison():
    rows = []
    for index in range(1, 7):
        for season, yards in [
            (2023, 700 + index * 50),
            (2024, 800 + index * 55),
            (2025, 850 + index * 60),
        ]:
            rows.append(
                _stat_row(
                    f"w{index}",
                    f"Receiver {index}",
                    "WR",
                    season,
                    17,
                    receptions=50 + index,
                    receiving_yards=yards,
                    receiving_tds=4 + index % 3,
                )
            )
    result = walk_forward_backtest(pd.DataFrame(rows), "PPR", target_season=2025)
    assert not result.empty
    assert "MAE Edge" in result.columns
    assert "ALL" in result["Position"].tolist()


def test_snake_turn_math_for_pick_three():
    assert snake_pick_numbers(10, 3, 4) == [3, 18, 23, 38]
    context = draft_context(10, 3, 4, drafted_count=2)
    assert context["is_user_turn"] is True
    assert context["next_user_pick"] == 3
    assert context["following_user_pick"] == 18


def test_market_matching_and_recommendations_recalculate_after_a_pick():
    board = pd.DataFrame(
        [
            {
                "Overall Rank": 1,
                "Player": "Alpha Runner",
                "Position": "RB",
                "Team": "A",
                "Pos Rank": "RB1",
                "Draft Score": 95,
                "Confidence": 85,
                "Signal": "Stable",
                "Sleeper ID": "1",
            },
            {
                "Overall Rank": 2,
                "Player": "Beta Receiver",
                "Position": "WR",
                "Team": "B",
                "Pos Rank": "WR1",
                "Draft Score": 93,
                "Confidence": 88,
                "Signal": "Breakout",
                "Sleeper ID": "2",
            },
            {
                "Overall Rank": 3,
                "Player": "Gamma Quarterback",
                "Position": "QB",
                "Team": "C",
                "Pos Rank": "QB1",
                "Draft Score": 88,
                "Confidence": 90,
                "Signal": "Stable",
                "Sleeper ID": "3",
            },
        ]
    )
    market = pd.DataFrame(
        [
            {
                "Name Key": "alpharunner",
                "Player": "Alpha Runner",
                "Position": "RB",
                "Market Rank": 2.0,
                "Market SD": 4.0,
                "Market Value": 9000,
                "Market Date": "2026-08-07",
            },
            {
                "Name Key": "betareceiver",
                "Player": "Beta Receiver",
                "Position": "WR",
                "Market Rank": 1.0,
                "Market SD": 4.0,
                "Market Value": 9200,
                "Market Date": "2026-08-07",
            },
        ]
    )
    enriched = attach_market_rankings(board, market)
    first, _ = recommend_draft_picks(
        enriched,
        [],
        [],
        league_size=10,
        draft_slot=1,
        rounds=16,
        lineup_mode="1QB",
        strategy="Dynasty",
    )
    assert first.iloc[0]["Player"] in {"Alpha Runner", "Beta Receiver"}
    after_pick, _ = recommend_draft_picks(
        enriched,
        [first.iloc[0]["Player"]],
        [],
        league_size=10,
        draft_slot=1,
        rounds=16,
        lineup_mode="1QB",
        strategy="Dynasty",
    )
    assert first.iloc[0]["Player"] not in after_pick["Player"].tolist()
    assert after_pick.iloc[0]["Player"] != first.iloc[0]["Player"]


def test_one_qb_dynasty_uses_cross_position_surplus_not_position_percentiles():
    stats = []
    rosters = []
    players = [
        ("q1", "Elite QB", "QB", 4300, 35, 0, 0),
        ("q2", "Replacement QB", "QB", 3900, 28, 0, 0),
        ("w1", "Elite WR", "WR", 0, 0, 105, 1550),
        ("w2", "WR Two", "WR", 0, 0, 85, 1250),
        ("w3", "WR Three", "WR", 0, 0, 75, 1050),
    ]
    for (
        player_id,
        name,
        position,
        pass_yards,
        pass_tds,
        receptions,
        rec_yards,
    ) in players:
        stats.append(
            _stat_row(
                player_id,
                name,
                position,
                2025,
                17,
                passing_yards=pass_yards,
                passing_tds=pass_tds,
                receptions=receptions,
                receiving_yards=rec_yards,
                receiving_tds=9 if position == "WR" else 0,
            )
        )
        rosters.append(
            {
                "gsis_id": player_id,
                "position": position,
                "full_name": name,
                "team": "AAA",
                "birth_date": "2001-01-01",
                "years_exp": 3,
                "week": 1,
                "status": "ACT",
            }
        )
    board = build_fantasy_board(
        _bundle(stats, rosters),
        "PPR",
        league_size=2,
        lineup_mode="1QB",
        projection_season=2026,
        strategy="Dynasty",
    )
    values = board.set_index("Player")
    assert (
        values.loc["Elite WR", "Adjusted VORP"]
        > values.loc["Elite QB", "Adjusted VORP"]
    )
    assert values.loc["Elite WR", "Draft Score"] > values.loc["Elite QB", "Draft Score"]
