"""Streamlit UI for the HagLabs NFL in-season fantasy command center."""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from nfl_fantasy_model import MODEL_VERSION, SCORING_PRESETS
from nfl_fantasy_ui import _cached_draft_model
from nfl_inseason import (
    INSEASON_MODEL_VERSION,
    attach_manual_roster,
    attach_sleeper_rosters,
    current_matchup_opponent,
    fetch_sleeper_league_state,
    league_power_rankings,
    league_team_names,
    lineup_changes,
    matchup_outlook,
    optimize_lineup,
    waiver_recommendations,
)
from nfl_league_profiles import load_league_profiles


@st.cache_data(ttl=300, show_spinner=False)
def _cached_sleeper_state(league_id: str, refresh_token: int) -> dict:
    del refresh_token
    return fetch_sleeper_league_state(league_id)


def _parse_names(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n\r]+", text) if item.strip()]


def _lineup_columns(frame: pd.DataFrame) -> list[str]:
    desired = [
        "Lineup Slot",
        "Player",
        "Position",
        "Team",
        "Projected PPG",
        "Floor PPG",
        "Ceiling PPG",
        "Confidence",
        "Risk Score",
        "NFL Status",
        "Signal",
    ]
    return [column for column in desired if column in frame.columns]


def _resolve_roster_id(profile: dict, names: dict[int, str]) -> int:
    configured = int(profile.get("roster_id") or 0)
    if configured in names:
        return configured
    target = str(profile.get("team_name") or "").casefold()
    for roster_id, name in names.items():
        if str(name).casefold() == target:
            return roster_id
    return configured


def render_nfl_inseason_command_center() -> None:
    st.title("NFL Fantasy Command Center")
    st.caption(
        "League-aware start/sit, waiver, roster-strength, and rest-of-season decisions "
        "powered by the HagLabs projection model—not ESPN or Sleeper projections."
    )

    profiles = load_league_profiles()
    profile_by_label = {str(profile["label"]): profile for profile in profiles}
    if not profile_by_label:
        st.error("No local NFL league profiles are configured.")
        return
    selected_label = st.selectbox(
        "League profile",
        list(profile_by_label),
        help="Sleeper rosters sync from public league data. Private ESPN rosters can be pasted without a login.",
    )
    profile = profile_by_label[selected_label]
    platform = str(profile.get("platform") or "")
    profile_key = str(profile.get("key") or platform.lower())
    strategy = str(profile.get("strategy") or "Redraft")
    scoring_name = str(profile.get("scoring_label") or "PPR")
    scoring_settings = profile.get("scoring_settings")
    if not scoring_settings and scoring_name not in SCORING_PRESETS:
        scoring_name = "PPR"
    league_size = int(profile.get("team_count") or 10)
    lineup_mode = str(profile.get("lineup_mode") or "1QB")
    risk_mode = st.segmented_control(
        "Decision posture",
        ["Floor", "Balanced", "Upside"],
        default="Balanced",
        key=f"inseason_risk_{profile_key}",
        help="Floor leans toward safer outcomes; Upside leans toward model ceiling. Availability and inactive status still apply.",
    ) or "Balanced"

    try:
        with st.spinner("Updating the HagLabs rest-of-season player model..."):
            board, backtest, metadata, market_metadata = _cached_draft_model(
                scoring_name,
                scoring_settings,
                league_size,
                lineup_mode,
                strategy,
                MODEL_VERSION,
            )
    except Exception as exc:  # noqa: BLE001 - isolate public-feed failures.
        st.error(f"The player model could not load: {type(exc).__name__}: {exc}")
        return
    if board.empty:
        st.error("The current public feeds did not produce a fantasy player board.")
        return

    state: dict = {}
    match_metadata: dict = {}
    team_names: dict[int, str] = {}
    roster_id = 1
    roster_positions = profile.get("roster_positions") or []
    league_board = pd.DataFrame()
    manual_mode = platform.casefold() != "sleeper"

    if not manual_mode:
        refresh_key = f"sleeper_refresh_{profile_key}"
        if refresh_key not in st.session_state:
            st.session_state[refresh_key] = 0
        refresh_col, status_col = st.columns([1, 4])
        with refresh_col:
            if st.button("Refresh Sleeper now", key=f"refresh_button_{profile_key}"):
                st.session_state[refresh_key] += 1
                st.rerun()
        try:
            state = _cached_sleeper_state(
                str(profile.get("league_id") or ""),
                int(st.session_state[refresh_key]),
            )
        except Exception as exc:  # noqa: BLE001 - manual fallback remains clear.
            st.error(f"Sleeper roster sync failed: {type(exc).__name__}: {exc}")
            return
        league = state.get("league", {})
        roster_positions = league.get("roster_positions") or roster_positions
        team_names = league_team_names(state)
        roster_id = _resolve_roster_id(profile, team_names)
        league_board, match_metadata = attach_sleeper_rosters(board, state)
        with status_col:
            st.success(
                f"Live sync · {league.get('name') or 'Sleeper league'} · "
                f"{league.get('status', 'unknown').replace('_', ' ')} · week {state.get('week', 1)} · "
                f"{len(state.get('rosters', []))} teams"
            )
    else:
        st.warning(
            "ESPN returned 401 for this private league. Paste your drafted roster below; "
            "HagLabs will not request, store, or imitate account cookies."
        )
        roster_text = st.text_area(
            "Your ESPN roster (one player per line, or comma-separated)",
            key=f"manual_roster_{profile_key}",
            placeholder="Patrick Mahomes\nAmon-Ra St. Brown\n...",
            height=150,
        )
        player_names = _parse_names(roster_text)
        league_board, unmatched = attach_manual_roster(
            board, player_names, team_name=str(profile.get("team_name") or "My Team")
        )
        if unmatched:
            st.warning("Names not matched: " + ", ".join(unmatched[:12]))
        if not player_names:
            st.info("Paste the completed ESPN roster to activate lineup and roster-gap analysis.")
            return

    my_roster = league_board[league_board["Roster ID"].eq(roster_id)].copy()
    if my_roster.empty:
        st.error("Your roster could not be matched to modeled players. Refresh or verify the saved team profile.")
        return
    recommended, bench = optimize_lineup(
        my_roster, roster_positions, mode=risk_mode
    )
    starter_points = float(
        pd.to_numeric(
            recommended.loc[recommended["Player"].ne("OPEN"), "Projected PPG"],
            errors="coerce",
        ).sum()
    )
    floor_points = float(
        pd.to_numeric(
            recommended.loc[recommended["Player"].ne("OPEN"), "Floor PPG"],
            errors="coerce",
        ).sum()
    )
    ceiling_points = float(
        pd.to_numeric(
            recommended.loc[recommended["Player"].ne("OPEN"), "Ceiling PPG"],
            errors="coerce",
        ).sum()
    )
    injuries = int(
        my_roster.get("NFL Status", pd.Series("", index=my_roster.index))
        .astype(str)
        .str.upper()
        .isin({"IR", "OUT", "PUP", "SUS", "RES"})
        .sum()
    )
    opponent_id = (
        current_matchup_opponent(state, roster_id) if not manual_mode else None
    )
    head_to_head = (
        matchup_outlook(
            league_board,
            roster_id,
            opponent_id,
            roster_positions,
            mode=risk_mode,
        )
        if opponent_id is not None
        else None
    )
    metric_a, metric_b, metric_c, metric_d, metric_e = st.columns(5)
    metric_a.metric("Optimized Starter PPG", f"{starter_points:.1f}")
    metric_b.metric("Lineup Range", f"{floor_points:.1f}–{ceiling_points:.1f}")
    metric_c.metric("Modeled Roster", f"{len(my_roster)} players")
    metric_d.metric("Inactive Flags", injuries)
    metric_e.metric(
        f"Week {state.get('week', 1)} Baseline",
        f"{head_to_head['baseline_win_probability']:.0%}" if head_to_head else "N/A",
        help=(
            f"Versus {team_names.get(opponent_id, f'Roster {opponent_id}')}; "
            f"baseline margin {head_to_head['projected_margin']:+.1f} PPG. "
            "This uses rest-of-season player ranges and is not yet opponent-defense adjusted."
            if head_to_head and opponent_id is not None
            else "The current matchup is not posted or ESPN ownership is private."
        ),
    )

    tab_lineup, tab_waivers, tab_power, tab_roster, tab_method = st.tabs(
        ["Start / Sit", "Waiver Wire", "League Power", "My Roster", "Methodology"]
    )

    with tab_lineup:
        st.subheader(f"Recommended {risk_mode} Lineup")
        st.caption(
            "This is an exact slot-eligible optimization of HagLabs floor, baseline, or ceiling estimates. "
            "Confirm late injury news before kickoff."
        )
        if head_to_head and opponent_id is not None:
            opponent_name = team_names.get(opponent_id, f"Roster {opponent_id}")
            st.info(
                f"Week {state.get('week', 1)} baseline vs **{opponent_name}**: "
                f"{head_to_head['my_projected_ppg']:.1f}–{head_to_head['opponent_projected_ppg']:.1f}, "
                f"{head_to_head['baseline_win_probability']:.0%} win probability. "
                "This is a roster-strength baseline; opponent-defense and late-news adjustments remain separate."
            )
        if not manual_mode:
            changes = lineup_changes(recommended, my_roster)
            if changes["start"] or changes["bench"]:
                start_text = ", ".join(changes["start"]) or "no additions"
                bench_text = ", ".join(changes["bench"]) or "no removals"
                st.info(
                    f"Suggested change: start **{start_text}**; bench **{bench_text}**. "
                    f"Baseline difference: {changes['projected_gain']:+.1f} PPG."
                )
            else:
                st.success("Your current Sleeper starters match the optimized modeled lineup.")
        st.dataframe(
            recommended[_lineup_columns(recommended)], width="stretch", hide_index=True
        )
        if recommended["Player"].eq("OPEN").any():
            st.caption(
                "An OPEN defense slot is expected because the current player model does not yet project team defenses."
            )
        st.markdown("#### Best bench alternatives")
        bench_columns = [
            column
            for column in [
                "Player",
                "Position",
                "Team",
                "Projected PPG",
                "Floor PPG",
                "Ceiling PPG",
                "Confidence",
                "NFL Status",
                "Signal",
            ]
            if column in bench.columns
        ]
        st.dataframe(bench[bench_columns], width="stretch", hide_index=True)

    with tab_waivers:
        st.subheader("Model-Driven Waiver Priorities")
        waivers = waiver_recommendations(
            league_board,
            roster_id,
            roster_positions,
            mode=risk_mode,
            top_n=50,
        )
        if manual_mode:
            st.warning(
                "ESPN availability is not public for this league. These are unverified targets; "
                "confirm each player is a free agent before acting."
            )
        else:
            st.caption(
                "Only players not rostered in the synced Sleeper league appear here. Weekly Gain compares "
                "the candidate with the most practical modeled drop at the same position when possible."
            )
        if waivers.empty:
            st.info("No waiver comparison is available for the current roster state.")
        else:
            waiver_columns = [
                "Waiver Rank",
                "Action",
                "Player",
                "Position",
                "Team",
                "Drop Candidate",
                "Weekly Gain",
                "ROS Gain",
                "Upside Gain",
                "Waiver Priority",
                "Projected PPG",
                "Confidence",
                "Risk Score",
                "Signal",
            ]
            st.dataframe(
                waivers[[column for column in waiver_columns if column in waivers.columns]],
                width="stretch",
                hide_index=True,
            )

    with tab_power:
        st.subheader("League Roster Power")
        if manual_mode:
            st.info("League-wide power rankings require public ownership data; private ESPN ownership is unavailable.")
        else:
            power = league_power_rankings(
                league_board, roster_positions, team_names, mode=risk_mode
            )
            if not power.empty:
                my_rank = power[power["Roster ID"].eq(roster_id)]
                if not my_rank.empty:
                    row = my_rank.iloc[0]
                    st.info(
                        f"**{row['Fantasy Team']}** ranks #{int(row['Power Rank'])} of {len(power)} "
                        f"with {row['Starter PPG']:.1f} optimized starter PPG."
                    )
                st.dataframe(power, width="stretch", hide_index=True)
            if match_metadata.get("unmatched_ids"):
                st.caption(
                    f"Matched {match_metadata['matched']} of {match_metadata['league_players']} rostered assets. "
                    "Team defenses and a small number of unmapped players are excluded consistently."
                )

    with tab_roster:
        st.subheader(str(profile.get("team_name") or "My Team"))
        roster_columns = [
            "Player",
            "Position",
            "Team",
            "Pos Rank",
            "Projected PPG",
            "Projected Season Points",
            "Floor PPG",
            "Ceiling PPG",
            "VORP",
            "3-Year Career Value",
            "5-Year Career Value",
            "Confidence",
            "Risk Score",
            "NFL Status",
            "Signal",
        ]
        roster_view = my_roster.sort_values("Projected PPG", ascending=False)
        st.dataframe(
            roster_view[[column for column in roster_columns if column in roster_view.columns]],
            width="stretch",
            hide_index=True,
        )

    with tab_method:
        st.subheader("How Decisions Are Made")
        st.markdown(
            """
            - HagLabs projections supply the expected points, floor, ceiling, confidence, durability, role, and rest-of-season value.
            - Sleeper supplies league settings, ownership, and the currently saved starters; it does not supply the ranking answer.
            - Lineups are solved as an exact slot-eligibility assignment, so flex choices are evaluated jointly rather than greedily.
            - Waivers compare each available player with a realistic drop candidate and combine weekly gain, rest-of-season gain, upside, confidence, and risk.
            - League power uses optimized starters plus limited bench depth; it is a roster-strength estimate, not a promised finish.
            - Private ESPN data remains manual unless the league is made publicly readable. No account login or cookie is requested or stored.
            """
        )
        st.caption(
            f"In-season engine {INSEASON_MODEL_VERSION} · player model {MODEL_VERSION} · "
            f"history through {max(metadata.get('history_seasons', [0]))} · "
            f"market anchor {market_metadata.get('source', 'unavailable')}"
        )
        if not backtest.empty:
            overall = backtest[backtest["Position"].eq("ALL")]
            if not overall.empty:
                row = overall.iloc[0]
                st.write(
                    f"Latest walk-forward validation: model MAE {row['Model MAE']:.3f} PPG; "
                    f"rank correlation {row['Model Rank Corr']:.3f}."
                )
