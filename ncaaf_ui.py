"""Streamlit interface for the HagLabs NCAA football winner engine."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import gspread
import pandas as pd
import requests
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from daily_ncaaf_auto import (
    find_odds_for_game,
    get_log_stats,
    grade_pending_games,
    log_prediction_records,
    resolve_model_team,
)
from ncaaf_model import (
    MODEL_VERSION,
    CFBDDataClient,
    SeasonContext,
    TeamState,
    WinnerModel,
    as_mapping,
    build_current_states,
    build_season_context,
    create_feature_snapshot,
    fetch_espn_current_season_games,
    market_consensus,
    prediction_record,
    safe_float,
    safe_int,
    update_states_with_current_games,
)

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_PATH = PROJECT_ROOT / "data" / "ncaaf" / "model.json"


def _configured_secret(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
        try:
            value = str(st.secrets.get(name, "")).strip()
        except (AttributeError, KeyError, RuntimeError, StreamlitSecretNotFoundError, TypeError):
            value = ""
        if value:
            return value
    return ""


@st.cache_resource
def _load_model() -> WinnerModel:
    if MODEL_PATH.exists():
        try:
            return WinnerModel.load(MODEL_PATH)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return WinnerModel.bootstrap()


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_odds(api_key: str) -> list[dict[str, Any]]:
    if not api_key:
        return []
    response = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
            "bookmakers": "draftkings,fanduel,betmgm,caesars,espnbet,fanatics",
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Odds service returned HTTP {response.status_code}")
    payload = response.json()
    return [as_mapping(item) for item in payload] if isinstance(payload, list) else []


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_public_current_results(season: int) -> list[dict[str, Any]]:
    return fetch_espn_current_season_games(season)


@st.cache_data(ttl=43200, show_spinner=False)
def _fetch_cfbd_payload(api_key: str, season: int) -> dict[str, Any]:
    if not api_key:
        return {}
    client = CFBDDataClient(api_key)
    seasons = range(max(2014, season - 3), season + 1)
    games: list[dict[str, Any]] = []
    advanced: list[dict[str, Any]] = []
    havoc: list[dict[str, Any]] = []
    for year in seasons:
        games.extend(client.get("/games", year=year, seasonType="both", classification="fbs"))
        advanced.extend(
            client.optional(
                "/stats/game/advanced",
                year=year,
                seasonType="both",
                excludeGarbageTime="true",
            )
        )
        havoc.extend(client.optional("/stats/game/havoc", year=year, seasonType="both"))
    return {
        "games": games,
        "advanced": advanced,
        "havoc": havoc,
        "returning": client.optional("/player/returning", year=season),
        "talent": client.optional("/talent", year=season),
        "coaches": client.optional("/coaches", minYear=season, maxYear=season),
        "teams": client.optional("/teams"),
        "venues": client.optional("/venues"),
        "weather": client.optional(
            "/games/weather",
            year=season,
            seasonType="both",
            classification="fbs",
        ),
    }


def _upcoming_games(games: list[dict[str, Any]], season: int) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=21)
    output = []
    for game in games:
        if safe_int(game.get("season")) != season:
            continue
        if game.get("home_points") is not None or game.get("homePoints") is not None:
            continue
        raw_start = game.get("start_date") or game.get("startDate")
        try:
            start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - timedelta(hours=6) <= start <= horizon:
            output.append(game)
    return sorted(output, key=lambda item: str(item.get("start_date") or item.get("startDate") or ""))


def _weather_index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {safe_int(row.get("game_id") or row.get("gameId")): row for row in rows}


def _find_odds_for_schedule(game: Mapping[str, Any], odds_games: list[dict[str, Any]]) -> dict[str, Any] | None:
    return find_odds_for_game(game, odds_games)[0]


def _build_predictions(
    schedule: list[dict[str, Any]],
    odds_games: list[dict[str, Any]],
    states: dict[str, TeamState],
    context: SeasonContext,
    weather: dict[int, dict[str, Any]],
    model: WinnerModel,
) -> list[dict[str, Any]]:
    predictions = []
    timestamp = datetime.now(timezone.utc)
    for game in schedule:
        aligned_states = dict(states)
        matchup_is_modelable = True
        for side in ("home", "away"):
            field = f"{side}_team"
            display_name = str(game.get(field) or "")
            aliases = [
                display_name,
                str(game.get(f"{side}_team_display") or ""),
                str(game.get(f"{side}_team_short") or ""),
                str(game.get(f"{side}_team_abbreviation") or ""),
            ]
            canonical = resolve_model_team(aliases, states)
            if not canonical:
                matchup_is_modelable = False
                break
            aligned_states[display_name] = states[canonical]
        if not matchup_is_modelable:
            continue
        odds_game = _find_odds_for_schedule(game, odds_games)
        market = market_consensus(odds_game) if odds_game else None
        game_id = safe_int(game.get("id"))
        feature_row = create_feature_snapshot(game, aligned_states, context, weather.get(game_id))
        predictions.append(prediction_record(game, feature_row, model, market, timestamp))
    return predictions


def _log_full_slate(records: list[dict[str, Any]]) -> tuple[int, int]:
    result = log_prediction_records(records)
    if result["errors"]:
        raise RuntimeError("; ".join(result["errors"][:3]))
    return result["logged"], result["duplicates"] + result["skipped_started"]


def _grade_predictions(api_key: str) -> int:
    del api_key  # ESPN stable IDs make unattended grading credential-free.
    result = grade_pending_games()
    if result["errors"] and not result["graded"]:
        raise RuntimeError("; ".join(result["errors"][:3]))
    return result["graded"]


def _history_metrics() -> dict[str, Any]:
    try:
        stats = get_log_stats()
    except (gspread.GSpreadException, OSError, RuntimeError, ValueError):
        return {
            "games": 0,
            "accuracy": None,
            "independent_accuracy": None,
            "brier": None,
            "vegas_accuracy": None,
            "model_advantage": None,
            "wins": 0,
            "losses": 0,
            "pending": 0,
            "confidence": {},
            "disagreement_games": 0,
            "independent_disagreement_wins": 0,
            "vegas_disagreement_wins": 0,
        }
    return {
        "games": stats["graded"],
        "accuracy": stats["model_accuracy"],
        "independent_accuracy": stats["independent_accuracy"],
        "brier": stats["brier"],
        "vegas_accuracy": stats["vegas_accuracy"],
        "model_advantage": stats["model_advantage"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "pending": stats["pending"],
        "confidence": stats["confidence"],
        "disagreement_games": stats.get("disagreement_games", 0),
        "independent_disagreement_wins": stats.get("independent_disagreement_wins", 0),
        "vegas_disagreement_wins": stats.get("vegas_disagreement_wins", 0),
    }


def _display_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        official_winner = record.get("predicted_winner")
        official_probability = record.get("winner_probability")
        independent_winner = record.get("independent_predicted_winner") or record.get("predicted_winner")
        independent_probability = record.get("independent_winner_probability") or record.get("winner_probability")
        market_winner = record.get("market_predicted_winner")
        market_probability = record.get("market_winner_probability")
        market_aware_winner = record.get("market_aware_predicted_winner")
        market_aware_probability = record.get("market_aware_winner_probability")
        home_edge = record.get("model_edge")
        winner_edge = None
        if home_edge is not None:
            winner_edge = home_edge if independent_winner == record.get("home_team") else -home_edge
        explanation = ", ".join(
            f"{item['label']} → {item['direction']}" for item in record.get("explanations") or []
        )
        rows.append(
            {
                "Kickoff": record.get("start_date"),
                "Matchup": f"{record.get('away_team')} @ {record.get('home_team')}",
                "Official Pick": official_winner,
                "Official Win %": official_probability,
                "Independent Pick": independent_winner,
                "Independent Win %": independent_probability,
                "Confidence": (
                    "High"
                    if safe_float(independent_probability) >= 0.70
                    else ("Medium" if safe_float(independent_probability) >= 0.60 else "Tracking")
                ),
                "Market Pick": market_winner,
                "Market Win %": market_probability,
                "Market-Aware Lean": market_aware_winner,
                "Lean Win %": market_aware_probability,
                "Range": f"{record.get('uncertainty_low', 0):.0%}–{record.get('uncertainty_high', 0):.0%}",
                "Fair ML": record.get("fair_home_moneyline") if official_winner == record.get("home_team") else record.get("fair_away_moneyline"),
                "HagLabs Edge": winner_edge,
                "Agreement": (
                    "No market"
                    if market_winner is None
                    else ("Agree" if market_winner == independent_winner else "Disagree")
                ),
                "Books": record.get("book_count"),
                "Decision Policy": record.get("decision_source"),
                "Signal": (
                    "Validated edge"
                    if record.get("actionable_edge")
                    else (
                        "Market disagreement"
                        if market_winner is not None and market_winner != independent_winner
                        else ("Model-market agree" if market_winner is not None else "No market")
                    )
                ),
                "Why": explanation,
            }
        )
    return pd.DataFrame(rows)


def render_ncaaf_winner_lab() -> None:
    st.title("NCAA Football Winner Intelligence Lab")
    st.caption(
        "Winner-only forecasts from opponent-adjusted team strength, recent efficiency, roster continuity, "
        "coaching, site, rest, weather, and a separately displayed no-vig sportsbook consensus."
    )

    model = _load_model()
    metadata = model.metadata
    model_validated = bool(metadata.get("validated"))
    market_validated = bool(metadata.get("market_validated"))
    current_year = datetime.now(timezone.utc).year
    season = st.selectbox("Season", list(range(current_year, max(2014, current_year - 3) - 1, -1)), index=0)
    cfbd_key = _configured_secret("CFBD_API_KEY")
    odds_key = _configured_secret("ODDS_API_KEY", "THE_ODDS_API_KEY")
    if not odds_key:
        st.warning(
            "ODDS_API_KEY is not configured. ESPN schedule discovery and HagLabs predictions remain available, "
            "but market comparisons will be marked unavailable."
        )

    status_columns = st.columns(4)
    status_columns[0].metric("Model", metadata.get("model_version", MODEL_VERSION))
    status_columns[1].metric(
        "Mode",
        "Market-validated" if market_validated else ("Football-validated" if model_validated else "Bootstrap"),
    )
    backtest = metadata.get("backtest") or {}
    status_columns[2].metric("Walk-forward games", safe_int(backtest.get("games")))
    model_metrics = backtest.get("model") or {}
    status_columns[3].metric("Backtest Brier", f"{safe_float(model_metrics.get('brier')):.3f}" if model_metrics else "Not run")

    if not model_validated:
        st.warning(
            "The trained artifact has not passed the minimum rolling-origin gate on this deployment. "
            "Winner probabilities may be used for shadow evaluation, but no market edge is labeled actionable."
        )
    elif not market_validated:
        st.warning(
            "The football model beat its Elo baseline, but the market-aware layer has not beaten the historical market gate. "
            "Winner forecasts remain available while actionable edge labels stay disabled."
        )
    if not cfbd_key:
        with st.expander("Required one-time commands"):
            st.code(
                "$env:CFBD_API_KEY='<configured locally>'\n"
                ".\\.venv\\Scripts\\python.exe scripts\\build_ncaaf_model.py --start-year 2014 --end-year 2025",
                language="powershell",
            )
    try:
        if cfbd_key:
            with st.spinner("Loading CFBD history and the current schedule..."):
                payload = _fetch_cfbd_payload(cfbd_key, int(season))
        else:
            payload = {}
        odds_games = _fetch_odds(odds_key) if odds_key else []
    except (requests.RequestException, RuntimeError, TypeError, ValueError) as exc:
        st.error(f"The NCAA data refresh failed safely: {exc}")
        return

    public_results: list[dict[str, Any]] = []
    public_results_error = ""
    if not cfbd_key:
        try:
            public_results = _fetch_public_current_results(int(season))
        except (requests.RequestException, RuntimeError, TypeError, ValueError) as exc:
            public_results_error = type(exc).__name__

    games = payload.get("games") or []
    state_refresh = {
        "completed_games_applied": 0,
        "latest_week": 0,
        "latest_start": "",
        "teams_updated": 0,
    }
    if games:
        states = build_current_states(games, payload.get("advanced"), payload.get("havoc"))
        context = build_season_context(
            int(season),
            returning=payload.get("returning"),
            talent=payload.get("talent"),
            coaches=payload.get("coaches"),
            teams=payload.get("teams"),
            venues=payload.get("venues"),
        )
        schedule = _upcoming_games(games, int(season))
        weather = _weather_index(payload.get("weather") or [])
    else:
        states = {
            team: TeamState(**state)
            for team, state in (metadata.get("team_states") or {}).items()
        }
        if safe_int(metadata.get("team_state_season")) == int(season):
            states, state_refresh = update_states_with_current_games(states, public_results)
        context = SeasonContext()
        schedule = _upcoming_games(public_results, int(season))
        if not schedule:
            schedule = [
                {
                    "id": game.get("id"),
                    "season": season,
                    "week": None,
                    "start_date": game.get("commence_time"),
                    "home_team": game.get("home_team"),
                    "away_team": game.get("away_team"),
                    "neutral_site": False,
                }
                for game in odds_games
            ]
        weather = {}

    if not cfbd_key:
        if state_refresh["completed_games_applied"]:
            st.info(
                f"Credential-free current-state update: applied {state_refresh['completed_games_applied']} "
                f"completed {season} games through week {state_refresh['latest_week']} to the shipped preseason baseline. "
                "Roster talent, returning production, coach, venue, and weather enrichment still require CFBD."
            )
        else:
            suffix = f" ({public_results_error})" if public_results_error else ""
            st.warning(
                "CFBD_API_KEY is not configured and no current-season final scores were applied"
                f"{suffix}. The board is using the shipped preseason team states plus current sportsbook games."
            )
    predictions = _build_predictions(schedule, odds_games, states, context, weather, model)

    source_columns = st.columns(5)
    source_columns[0].metric("Upcoming games", len(schedule))
    source_columns[1].metric("Teams with history", len(states))
    source_columns[2].metric("Games with market", sum(record.get("market_home_probability") is not None for record in predictions))
    source_columns[3].metric("Odds books", max((safe_int(record.get("book_count")) for record in predictions), default=0))
    source_columns[4].metric("Current finals applied", state_refresh["completed_games_applied"])

    historical = _history_metrics()
    st.markdown("#### NCAA Command Center")
    command_columns = st.columns(5)
    command_columns[0].metric("Graded Games", historical["games"])
    command_columns[1].metric(
        "Official Accuracy",
        f"{historical['accuracy']:.1%}" if historical["accuracy"] is not None else "No grades",
    )
    command_columns[2].metric(
        "Independent Accuracy",
        f"{historical['independent_accuracy']:.1%}" if historical["independent_accuracy"] is not None else "No grades",
    )
    command_columns[3].metric(
        "Vegas Accuracy",
        f"{historical['vegas_accuracy']:.1%}" if historical["vegas_accuracy"] is not None else "No odds grades",
    )
    command_columns[4].metric(
        "Official Advantage",
        f"{historical['model_advantage']:+.1%}" if historical["model_advantage"] is not None else "No comparison",
    )
    record_columns = st.columns(4)
    record_columns[0].metric("Wins", historical["wins"])
    record_columns[1].metric("Losses", historical["losses"])
    record_columns[2].metric("Pending", historical["pending"])
    confidence_summary = historical.get("confidence") or {}
    confidence_text = " · ".join(
        f"{tier} {metrics['accuracy']:.0%} ({metrics['games']})"
        for tier in ("High", "Medium", "Tracking")
        if (metrics := confidence_summary.get(tier)) and metrics.get("accuracy") is not None
    )
    record_columns[3].metric("Confidence Accuracy", confidence_text or "No grades")

    disagreement_games = historical.get("disagreement_games", 0)
    if disagreement_games:
        st.info(
            "Market protection is active. In the latest graded model–market disagreements, "
            f"the independent model is {historical['independent_disagreement_wins']}–"
            f"{disagreement_games - historical['independent_disagreement_wins']} and Vegas is "
            f"{historical['vegas_disagreement_wins']}–"
            f"{disagreement_games - historical['vegas_disagreement_wins']}. Official picks now follow "
            "the no-vig consensus when odds are available; the independent model remains visible and logged in shadow mode."
        )

    tabs = st.tabs(["Winner Board", "Matchup Lab", "Validation", "Methodology"])
    with tabs[0]:
        if not predictions:
            st.info("No FBS games are scheduled in the next 21 days.")
        else:
            st.caption(
                "Official Pick uses the no-vig sportsbook consensus whenever odds are available. Independent Pick and the "
                "market-aware lean remain visible for research until a prospective disagreement sample proves an advantage."
            )
            display = _display_frame(predictions)
            st.dataframe(
                display,
                width="stretch",
                hide_index=True,
                column_config={
                    "Official Win %": st.column_config.ProgressColumn(format="percent", min_value=0.5, max_value=1.0),
                    "Independent Win %": st.column_config.NumberColumn(format="percent"),
                    "Market Win %": st.column_config.NumberColumn(format="percent"),
                    "Lean Win %": st.column_config.NumberColumn(format="percent"),
                    "HagLabs Edge": st.column_config.NumberColumn(format="percent"),
                },
            )
            st.download_button(
                "Download current winner board",
                display.to_csv(index=False).encode("utf-8"),
                file_name=f"haglabs_ncaaf_winners_{season}.csv",
                mime="text/csv",
            )
            if st.button("Run and log full active slate", type="primary"):
                try:
                    added, duplicates = _log_full_slate(predictions)
                    st.success(
                        f"Logged {added} immutable pregame predictions to NCAAF Log; "
                        f"skipped {duplicates} duplicates or games already underway."
                    )
                except (gspread.GSpreadException, OSError, RuntimeError, ValueError) as exc:
                    st.error(f"Predictions were calculated but Google Sheets logging failed safely: {exc}")
        if st.button("Grade completed predictions from ESPN finals"):
            try:
                updated = _grade_predictions(cfbd_key)
                st.success(f"Graded {updated} completed prediction snapshots.")
            except (gspread.GSpreadException, OSError, RuntimeError, ValueError) as exc:
                st.error(f"Grading failed safely: {exc}")

    with tabs[1]:
        teams = sorted(states)
        if len(teams) < 2:
            st.info("At least two teams are required for the matchup lab.")
        else:
            away_col, home_col = st.columns(2)
            away = away_col.selectbox("Away team", teams, index=0)
            home_options = [team for team in teams if team != away]
            home = home_col.selectbox("Home team", home_options, index=0)
            neutral = st.checkbox("Neutral site")
            manual_game = {
                "id": f"manual-{season}-{away}-{home}",
                "season": season,
                "week": None,
                "start_date": datetime.now(timezone.utc).isoformat(),
                "away_team": away,
                "home_team": home,
                "neutral_site": neutral,
            }
            feature_row = create_feature_snapshot(manual_game, states, context)
            manual = prediction_record(manual_game, feature_row, model)
            metric_columns = st.columns(3)
            metric_columns[0].metric("Predicted winner", manual["predicted_winner"])
            metric_columns[1].metric("Win probability", f"{manual['winner_probability']:.1%}")
            metric_columns[2].metric(
                "Fair moneyline",
                manual["fair_home_moneyline"] if manual["predicted_winner"] == home else manual["fair_away_moneyline"],
            )
            st.write("Primary model drivers")
            for explanation in manual.get("explanations") or []:
                favored = home if explanation["direction"] == "home" else away
                st.write(f"- {explanation['label']}: favors **{favored}**")

    with tabs[2]:
        columns = st.columns(3)
        columns[0].metric("Prospectively graded", historical["games"])
        columns[1].metric("Winner accuracy", f"{historical['accuracy']:.1%}" if historical["accuracy"] is not None else "No grades")
        columns[2].metric("Prospective Brier", f"{historical['brier']:.3f}" if historical["brier"] is not None else "No grades")
        if backtest:
            comparison = []
            for name in ("model", "elo", "market", "market_blend"):
                metrics = backtest.get(name) or {}
                if metrics:
                    comparison.append({"Benchmark": name.title(), **metrics})
            if comparison:
                st.dataframe(pd.DataFrame(comparison), width="stretch", hide_index=True)
        st.caption(
            "The validation gate requires at least 500 rolling-origin games and lower log loss and Brier score than the Elo baseline. "
            "Every active game is logged; headline accuracy never excludes no-edge games."
        )

    with tabs[3]:
        st.markdown(
            """
            **Independent model:** chronological Elo, scoring-margin and efficiency EMAs, offensive PPA,
            defensive success, explosiveness, havoc, rest, roster talent, returning production,
            coaching continuity, home/neutral site, travel, elevation, and weather.

            **Market comparison:** each sportsbook is de-vigged separately, then the median probability
            is taken across available books. The sportsbook number never defines model confidence.

            **Leakage controls:** each historical feature snapshot is created before that game updates
            either team. Calibration uses raw out-of-sample logits, and reported metrics use later-season
            rolling-origin folds.

            **Current season:** when CFBD is unavailable, completed ESPN scoreboard results update the
            shipped preseason states automatically. This updates Elo and scoring form without pretending
            that missing roster, coach, venue, weather, or advanced-play data is present.

            **Decision separation:** HagLabs Pick is always the independent model unless the market ensemble
            passes the stricter historical market gate. The market-aware lean remains diagnostic until then.

            **Player scope:** player information is aggregated into team-level continuity, returning
            production, transfers, talent, and availability. The engine does not project individual stats.
            """
        )
