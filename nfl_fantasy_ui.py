"""Streamlit UI for the HagLabs NFL Draft Intelligence Lab."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from nfl_draft_assistant import (
    attach_market_rankings,
    fetch_sleeper_draft_picks,
    load_market_rankings,
    recommend_draft_picks,
)
from nfl_fantasy_model import (
    FANTASY_POSITIONS,
    MODEL_VERSION,
    SCORING_PRESETS,
    build_fantasy_board,
    load_nflverse_fantasy_data,
    walk_forward_backtest,
)
from nfl_league_profiles import load_league_profiles


@st.cache_data(ttl=21600, show_spinner=False)
def _cached_draft_model(
    scoring_name: str,
    scoring_settings: dict[str, float] | None,
    league_size: int,
    lineup_mode: str,
    strategy: str,
    model_version: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    del model_version  # Included in the cache key to invalidate stale failed loads.
    bundle, metadata = load_nflverse_fantasy_data()
    board = build_fantasy_board(
        bundle,
        scoring_name=scoring_name,
        league_size=league_size,
        lineup_mode=lineup_mode,
        projection_season=metadata["projection_season"],
        strategy=strategy,
        scoring_settings=scoring_settings,
    )
    backtest = walk_forward_backtest(
        bundle.get("stats", pd.DataFrame()),
        scoring_name=scoring_name,
        scoring_settings=scoring_settings,
    )
    market_metadata: dict = {"source": "Unavailable", "as_of": "unknown", "matched": 0}
    try:
        market, market_metadata = load_market_rankings(strategy, lineup_mode)
        board = attach_market_rankings(board, market)
        market_metadata["matched"] = int(
            board.get("Market Match", pd.Series(dtype=bool)).sum()
        )
    except Exception as exc:  # noqa: BLE001 - market data must not take down projections.
        board = attach_market_rankings(board, pd.DataFrame())
        market_metadata["error"] = type(exc).__name__
    return board, backtest, metadata, market_metadata


def _format_percent_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ["Target Share", "Snap Share", "Availability"]:
        if column in out.columns:
            out[column] = out[column].map(lambda value: f"{float(value):.0%}")
    return out


def _draft_state(profile_key: str) -> dict:
    key = f"nfl_live_draft_{profile_key}"
    if key not in st.session_state:
        st.session_state[key] = {"picks": []}
    return st.session_state[key]


def _render_live_draft_room(
    board: pd.DataFrame,
    profile: dict,
    profile_key: str,
    league_size: int,
    lineup_mode: str,
    strategy: str,
) -> None:
    st.subheader("Live Draft Decision Engine")
    st.caption(
        "Record each selection and the recommendation recalculates immediately. "
        "The model separates player value, market draft cost, roster construction, and wait risk."
    )
    draft = profile.get("draft", {})
    default_slot = int(draft.get("draft_slot") or 1)
    default_rounds = int(
        draft.get("rounds") or max(16, len(profile.get("roster_positions", [])))
    )
    setup_a, setup_b = st.columns(2)
    with setup_a:
        draft_slot = int(
            st.number_input(
                "Your draft slot",
                min_value=1,
                max_value=league_size,
                value=min(default_slot, league_size),
                step=1,
                key=f"draft_slot_{profile_key}_{league_size}",
            )
        )
    with setup_b:
        rounds = int(
            st.number_input(
                "Draft rounds",
                min_value=5,
                max_value=40,
                value=min(max(default_rounds, 5), 40),
                step=1,
                key=f"draft_rounds_{profile_key}",
            )
        )

    state = _draft_state(profile_key)
    picks = state["picks"]
    draft_id = str(draft.get("id") or "")
    roster_id = int(profile.get("roster_id") or 0)
    if (
        profile.get("platform") == "Sleeper"
        and draft_id
        and st.button("Sync live Sleeper picks", key=f"sync_sleeper_{profile_key}")
    ):
        try:
            live = fetch_sleeper_draft_picks(draft_id)
            if not live.empty:
                id_to_player = board.set_index("Sleeper ID")["Player"].to_dict()
                synced: list[dict] = []
                for row in live.to_dict("records"):
                    player = id_to_player.get(
                        str(row.get("Sleeper ID", "")), row.get("Player", "")
                    )
                    synced.append(
                        {
                            "Pick": int(row["Pick"]),
                            "Player": player,
                            "Position": row.get("Position", ""),
                            "Team": row.get("Team", ""),
                            "My Team": int(row.get("Roster ID", 0)) == roster_id,
                        }
                    )
                state["picks"] = synced
                st.rerun()
            st.info("Sleeper has not posted any draft selections yet.")
        except Exception as exc:  # noqa: BLE001 - manual entry remains available.
            st.warning(
                f"Sleeper sync unavailable ({type(exc).__name__}); use manual pick entry."
            )

    drafted_players = [str(pick["Player"]) for pick in picks]
    my_roster = [str(pick["Player"]) for pick in picks if pick.get("My Team")]
    available_names = board[~board["Player"].isin(drafted_players)]["Player"].tolist()
    entry_a, entry_b, entry_c = st.columns([2, 1, 1])
    with entry_a:
        selected_pick = st.selectbox(
            "Player just drafted",
            available_names,
            key=f"drafted_player_{profile_key}",
            placeholder="Type a player name",
        )
    with entry_b:
        picked_for = st.selectbox(
            "Drafted by",
            ["Other team", "My team"],
            key=f"drafted_owner_{profile_key}",
        )
    with entry_c:
        st.write("")
        st.write("")
        if selected_pick and st.button(
            "Record pick", type="primary", key=f"record_pick_{profile_key}"
        ):
            player_row = board[board["Player"].eq(selected_pick)].iloc[0]
            picks.append(
                {
                    "Pick": len(picks) + 1,
                    "Player": selected_pick,
                    "Position": player_row["Position"],
                    "Team": player_row["Team"],
                    "My Team": picked_for == "My team",
                }
            )
            st.rerun()

    action_a, action_b, _ = st.columns([1, 1, 4])
    with action_a:
        if st.button(
            "Undo last pick", disabled=not picks, key=f"undo_pick_{profile_key}"
        ):
            picks.pop()
            st.rerun()
    with action_b:
        if st.button(
            "Reset draft", disabled=not picks, key=f"reset_draft_{profile_key}"
        ):
            state["picks"] = []
            st.rerun()

    recommendations, context = recommend_draft_picks(
        board,
        drafted_players,
        my_roster,
        league_size=league_size,
        draft_slot=draft_slot,
        rounds=rounds,
        lineup_mode=lineup_mode,
        strategy=strategy,
        roster_positions=profile.get("roster_positions"),
        top_n=20,
    )
    metric_a, metric_b, metric_c, metric_d = st.columns(4)
    metric_a.metric("Current overall pick", context["current_pick"])
    metric_b.metric("Current round", context["round"])
    metric_c.metric("Your next pick", context["next_user_pick"] or "Complete")
    metric_d.metric("Picks until your turn", context["picks_until_turn"] or 0)

    if not recommendations.empty:
        best = recommendations.iloc[0]
        turn_text = (
            "You are on the clock"
            if context["is_user_turn"]
            else f"Plan for pick {context['next_user_pick']}"
        )
        st.success(
            f"{turn_text}: **{best['Player']} ({best['Pos Rank']}, {best['Team']})** — "
            f"{best['Recommendation']}"
        )
        recommendation_columns = [
            "Recommendation Rank",
            "Player",
            "Pos Rank",
            "Team",
            "Selection Score",
            "Overall Rank",
            "Market Rank",
            "Market Edge",
            "Chance Available Next Turn",
            "Roster Fit",
            "Confidence",
            "Signal",
        ]
        st.dataframe(
            recommendations[recommendation_columns],
            width="stretch",
            hide_index=True,
        )

    if picks:
        with st.expander(f"Draft log ({len(picks)} picks)"):
            log = pd.DataFrame(picks)
            st.dataframe(log, width="stretch", hide_index=True)
            st.download_button(
                "Download draft state CSV",
                data=log.to_csv(index=False).encode("utf-8"),
                file_name=f"haglabs_draft_state_{profile_key}.csv",
                mime="text/csv",
            )


def render_nfl_draft_lab() -> None:
    st.title("NFL Draft Intelligence Lab")
    st.caption(
        "Evidence-based redraft and dynasty rankings using usage, expected opportunity, "
        "snap share, team environment, draft capital, age curves, durability, and NFL Next Gen Stats."
    )

    profiles = load_league_profiles()
    profile_by_label = {str(profile["label"]): profile for profile in profiles}
    selected_label = st.selectbox(
        "League profile",
        ["Manual"] + list(profile_by_label),
        help="Local league IDs and settings are stored outside Git and are not credentials.",
    )
    profile = profile_by_label.get(selected_label, {})
    profile_key = str(profile.get("key", "manual"))
    scoring_settings = profile.get("scoring_settings")
    preferred_scoring = str(profile.get("scoring_label", "ESPN PPR"))
    scoring_options = list(SCORING_PRESETS)
    if scoring_settings and preferred_scoring not in scoring_options:
        scoring_options.insert(0, preferred_scoring)

    if profile:
        league_name = (
            profile.get("league_name")
            or f"{profile.get('platform')} league {profile.get('league_id')}"
        )
        team_name = profile.get("team_name", "Unresolved team")
        draft = profile.get("draft", {})
        details = [str(league_name), str(team_name)]
        if profile.get("team_count"):
            details.append(f"{profile['team_count']} teams")
        details.append(str(profile.get("lineup_mode", "1QB")))
        if draft.get("type"):
            details.append(f"{draft['type']} draft")
        if draft.get("draft_slot"):
            details.append(f"draft slot {draft['draft_slot']}")
        st.success(" · ".join(details))
        if profile.get("import_status") == "private":
            st.warning(
                str(
                    profile.get(
                        "message",
                        "This private league could not be imported automatically.",
                    )
                )
            )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        scoring_name = st.selectbox(
            "Scoring",
            scoring_options,
            index=scoring_options.index(preferred_scoring)
            if preferred_scoring in scoring_options
            else 0,
            key=f"nfl_scoring_{profile_key}",
        )
        active_scoring_settings = (
            scoring_settings if scoring_name == preferred_scoring else None
        )
    with c2:
        league_sizes = [8, 10, 12, 14, 16]
        preferred_size = int(profile.get("team_count") or 12)
        league_size = st.selectbox(
            "League teams",
            league_sizes,
            index=league_sizes.index(preferred_size)
            if preferred_size in league_sizes
            else 2,
            key=f"nfl_size_{profile_key}",
        )
    with c3:
        lineup_options = ["1QB", "Superflex"]
        preferred_lineup = str(profile.get("lineup_mode", "1QB"))
        lineup_mode = st.selectbox(
            "QB format",
            lineup_options,
            index=lineup_options.index(preferred_lineup)
            if preferred_lineup in lineup_options
            else 0,
            key=f"nfl_lineup_{profile_key}",
        )
    with c4:
        strategy_options = ["Redraft", "Dynasty"]
        preferred_strategy = str(profile.get("strategy", "Redraft"))
        strategy = st.selectbox(
            "Draft strategy",
            strategy_options,
            index=strategy_options.index(preferred_strategy)
            if preferred_strategy in strategy_options
            else 0,
            key=f"nfl_strategy_{profile_key}",
        )

    if active_scoring_settings:
        with st.expander("Imported scoring details"):
            st.caption(
                "The player model applies offensive scoring supported by nflverse. "
                "Exact made-field-goal distance and missed-XP scoring apply to kickers; "
                "team-defense settings are retained for a future defense module."
            )
            st.json(active_scoring_settings)

    st.info(
        "HagLabs does not treat a projection as certainty. Draft Score combines value over replacement, "
        "ceiling, career value, and confidence for the selected league format."
    )

    try:
        with st.spinner(
            "Loading current nflverse data and calculating the draft board..."
        ):
            board, backtest, metadata, market_metadata = _cached_draft_model(
                scoring_name,
                active_scoring_settings,
                league_size,
                lineup_mode,
                strategy,
                MODEL_VERSION,
            )
    except Exception as exc:  # noqa: BLE001 - isolate public-feed failure to this page.
        st.error(f"NFL model data could not be loaded: {type(exc).__name__}: {exc}")
        st.caption(
            "Other HagLabs sports remain available. Retry later if the public data feed is temporarily unavailable."
        )
        return

    if board.empty:
        st.error(
            "No current NFL draft board was produced. The public roster or player-stat feed may be unavailable."
        )
        if metadata.get("errors"):
            st.write("Data-feed notes:", ", ".join(metadata["errors"]))
        return

    positive_pattern = "Opportunity Buy|Breakout Profile|Rookie Upside|TD Regression Up"
    positive_edges = int(
        board["Signal"].str.contains(positive_pattern, regex=True, na=False).sum()
    )
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Modeled Players", len(board))
    with m2:
        st.metric("Latest Results", max(metadata.get("history_seasons", [0])))
    with m3:
        st.metric("Potential Edges", positive_edges)
    with m4:
        overall = (
            backtest[backtest["Position"].eq("ALL")]
            if not backtest.empty
            else pd.DataFrame()
        )
        rank_corr = (
            float(overall.iloc[0]["Model Rank Corr"]) if not overall.empty else 0.0
        )
        st.metric("Backtest Rank Corr", f"{rank_corr:.3f}" if rank_corr else "N/A")

    if metadata.get("errors"):
        st.warning(
            "Some optional feeds degraded gracefully: " + ", ".join(metadata["errors"])
        )
    if market_metadata.get("error"):
        st.warning(
            "Current market rankings are unavailable; projections still work, but wait-risk estimates "
            "fall back to the HagLabs model order."
        )
    else:
        st.caption(
            f"Market anchor: {market_metadata['source']} · as of {market_metadata['as_of']} · "
            f"matched {market_metadata['matched']} players"
        )

    filter_a, filter_b = st.columns([1, 2])
    with filter_a:
        positions = st.multiselect(
            "Positions",
            list(FANTASY_POSITIONS),
            default=["QB", "RB", "WR", "TE"],
        )
    with filter_b:
        search = st.text_input(
            "Find player or team", placeholder="Player name or team abbreviation"
        )

    filtered = (
        board[board["Position"].isin(positions)].copy() if positions else board.copy()
    )
    if search:
        needle = search.strip().lower()
        filtered = filtered[
            filtered["Player"].str.lower().str.contains(needle, regex=False)
            | filtered["Team"].str.lower().str.contains(needle, regex=False)
        ]

    tab_live, tab_board, tab_edges, tab_career, tab_player, tab_accuracy, tab_method = (
        st.tabs(
            [
                "Live Draft Room",
                "Draft Board",
                "Hidden Signals",
                "Career Outlook",
                "Player Lab",
                "Validation",
                "Methodology",
            ]
        )
    )

    with tab_live:
        _render_live_draft_room(
            board,
            profile,
            profile_key,
            league_size,
            lineup_mode,
            strategy,
        )

    with tab_board:
        st.subheader(
            f"{strategy} Draft Board — {scoring_name}, {league_size}-Team {lineup_mode}"
        )
        columns = [
            "Overall Rank",
            "Pos Rank",
            "Player",
            "Team",
            "Age",
            "Projected PPG",
            "Projected Season Points",
            "Floor PPG",
            "Ceiling PPG",
            "VORP",
            "Adjusted VORP",
            "Draft Score",
            "Market Rank",
            "Market Edge",
            "Tier",
            "Confidence",
            "Signal",
        ]
        st.dataframe(filtered[columns].head(300), width="stretch", hide_index=True)
        st.download_button(
            "Download HagLabs Draft Board CSV",
            data=filtered.to_csv(index=False).encode("utf-8"),
            file_name=f"haglabs_nfl_{strategy.lower()}_{scoring_name.lower().replace(' ', '_')}.csv",
            mime="text/csv",
        )

    with tab_edges:
        st.subheader("Signals Common Ranking Lists Often Flatten")
        st.caption(
            "These are model flags, not guarantees. They highlight expected opportunity, touchdown regression, "
            "young breakouts, role uncertainty, durability, and age-curve risk."
        )
        positive = board[
            board["Signal"].str.contains(positive_pattern, regex=True, na=False)
        ].copy()
        risks = board[
            board["Signal"].str.contains(
                "TD Regression Down|Durability Risk|Age-Curve Risk|Role Risk",
                regex=True,
                na=False,
            )
        ].copy()
        left, right = st.columns(2)
        with left:
            st.markdown("#### Potential Values")
            st.dataframe(
                _format_percent_columns(
                    positive[
                        [
                            "Overall Rank",
                            "Player",
                            "Pos Rank",
                            "Team",
                            "Projected PPG",
                            "Expected Opportunity PPG",
                            "Target Share",
                            "Snap Share",
                            "Signal",
                        ]
                    ].head(80)
                ),
                width="stretch",
                hide_index=True,
            )
        with right:
            st.markdown("#### Price and Risk Warnings")
            st.dataframe(
                _format_percent_columns(
                    risks[
                        [
                            "Overall Rank",
                            "Player",
                            "Pos Rank",
                            "Team",
                            "Projected PPG",
                            "Risk Score",
                            "Availability",
                            "Signal",
                        ]
                    ].head(80)
                ),
                width="stretch",
                hide_index=True,
            )

    with tab_career:
        st.subheader("Five-Year Dynasty Outlook")
        projection_season = int(metadata["projection_season"])
        career_columns = [
            "Overall Rank",
            "Player",
            "Position",
            "Team",
            "Age",
            f"{projection_season} PPG",
            f"{projection_season + 1} PPG",
            f"{projection_season + 2} PPG",
            f"{projection_season + 3} PPG",
            f"{projection_season + 4} PPG",
            "3-Year Career Value",
            "5-Year Career Value",
            "Career Surplus Value",
            "Career Direction",
            "Career Percentile",
            "Risk Score",
        ]
        career = filtered.sort_values("Career Surplus Value", ascending=False)
        st.dataframe(career[career_columns].head(250), width="stretch", hide_index=True)

    with tab_player:
        st.subheader("Player Evidence Card")
        choices = (
            filtered["Player"].tolist()
            if not filtered.empty
            else board["Player"].tolist()
        )
        selected_player = st.selectbox("Player", choices)
        player = board[board["Player"].eq(selected_player)].iloc[0]
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric(
            "Overall / Position",
            f"#{int(player['Overall Rank'])} / {player['Pos Rank']}",
        )
        p2.metric("Projected PPG", f"{player['Projected PPG']:.2f}")
        p3.metric("VORP", f"{player['VORP']:+.2f}")
        p4.metric("Draft Score", f"{player['Draft Score']:.1f}")
        p5.metric("Confidence", f"{int(player['Confidence'])}/100")
        st.markdown(f"**Signal:** {player['Signal']}")
        st.write(player["Why"])
        evidence_columns = [
            "Projected PPG",
            "Floor PPG",
            "Ceiling PPG",
            "Expected Opportunity PPG",
            "Target Share",
            "WOPR",
            "Snap Share",
            "Team Environment",
            "Advanced Efficiency Z",
            "Availability",
            "Risk Score",
            "3-Year Career Value",
            "5-Year Career Value",
            "Career Surplus Value",
            "Career Direction",
        ]
        evidence = pd.DataFrame(
            {
                "Feature": evidence_columns,
                "Value": [str(player[column]) for column in evidence_columns],
            }
        )
        st.dataframe(evidence, width="stretch", hide_index=True)

    with tab_accuracy:
        st.subheader("Walk-Forward Validation")
        st.caption(
            "The backtest predicts the latest completed season using only earlier seasons and compares the "
            "recency blend with a simple prior-year PPG baseline. Lower MAE and higher rank correlation are better."
        )
        if backtest.empty:
            st.info("Backtest metrics are unavailable from the current data response.")
        else:
            st.dataframe(backtest, width="stretch", hide_index=True)
            overall = backtest[backtest["Position"].eq("ALL")]
            if not overall.empty:
                row = overall.iloc[0]
                if row["MAE Edge"] > 0:
                    st.success(
                        f"The recency model improved MAE by {row['MAE Edge']:.3f} PPG in this test."
                    )
                else:
                    st.warning(
                        f"The simple baseline beat the recency model by {abs(row['MAE Edge']):.3f} PPG. "
                        "HagLabs reports this honestly and will not claim an edge until validation earns it."
                    )

    with tab_method:
        st.subheader("What the Model Uses")
        st.markdown(
            """
            - Recency-weighted player production under the selected scoring rules
            - Expected fantasy opportunity and touchdown regression
            - Target share, air-yards opportunity (WOPR), and offensive snap share
            - Team scoring environment with deliberately capped influence
            - NFL Next Gen Stats efficiency, heavily regressed to avoid small-sample overreaction
            - Current roster, draft capital, experience, age curves, availability, and role uncertainty
            - Cross-position value over replacement tailored to league size and 1QB/Superflex format
            - Five-year age/development and replacement-adjusted surplus for dynasty decisions
            - Current market ECR/value as a draft-cost signal, not as a substitute for projection quality
            - Snake-draft turn math, roster construction, and probability a player reaches the next pick
            """
        )
        st.markdown(
            "Data: [nflverse](https://github.com/nflverse) public datasets and "
            "[ffopportunity](https://github.com/ffverse/ffopportunity) release files. "
            "Market context: [DynastyProcess open data](https://github.com/DynastyProcess/data). "
            "Live Sleeper picks use its public read-only API."
        )
        st.caption(
            f"Model {MODEL_VERSION} | Loaded {metadata['loaded_at']} | Projection season {metadata['projection_season']} | "
            f"Historical seasons {metadata['history_seasons']}"
        )
        st.warning(
            "Fantasy projections are estimates, not promises. Depth-chart changes, injuries, suspensions, "
            "trades, and coaching decisions can invalidate assumptions quickly."
        )
