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

from google_sheets import get_google_client
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
    market_consensus,
    match_team_name,
    prediction_record,
    safe_float,
    safe_int,
)

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_PATH = PROJECT_ROOT / "data" / "ncaaf" / "model.json"
SHEET_NAME = "NCAAF Prediction Model"
LOG_TAB = "Predictions v2"
LOG_HEADERS = [
    "Prediction ID",
    "Game ID",
    "Season",
    "Week",
    "Start Date",
    "Prediction Time",
    "Model Version",
    "Model Mode",
    "Away Team",
    "Home Team",
    "Neutral Site",
    "Predicted Winner",
    "Winner Probability",
    "Independent Home Probability",
    "Final Home Probability",
    "Market Home Probability",
    "Model Edge",
    "Fair Home ML",
    "Fair Away ML",
    "Market Home ML",
    "Market Away ML",
    "Book Count",
    "Market Observed At",
    "Uncertainty Low",
    "Uncertainty High",
    "Feature Snapshot",
    "Result",
    "Actual Winner",
]


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
    candidates = [
        str(game.get("home_team") or game.get("homeTeam") or ""),
        str(game.get("away_team") or game.get("awayTeam") or ""),
    ]
    for odds_game in odds_games:
        home = match_team_name(str(odds_game.get("home_team") or ""), candidates)
        away = match_team_name(str(odds_game.get("away_team") or ""), candidates)
        if home == candidates[0] and away == candidates[1]:
            return odds_game
    return None


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
        for field in ("home_team", "away_team"):
            display_name = str(game.get(field) or "")
            if display_name and display_name not in aligned_states:
                canonical = match_team_name(display_name, states)
                if canonical:
                    aligned_states[display_name] = states[canonical]
        odds_game = _find_odds_for_schedule(game, odds_games)
        market = market_consensus(odds_game) if odds_game else None
        game_id = safe_int(game.get("id"))
        feature_row = create_feature_snapshot(game, aligned_states, context, weather.get(game_id))
        predictions.append(prediction_record(game, feature_row, model, market, timestamp))
    return predictions


def _record_to_row(record: Mapping[str, Any]) -> list[Any]:
    return [
        record.get("prediction_id"),
        record.get("game_id"),
        record.get("season"),
        record.get("week"),
        record.get("start_date"),
        record.get("prediction_time"),
        record.get("model_version"),
        record.get("model_mode"),
        record.get("away_team"),
        record.get("home_team"),
        record.get("neutral_site"),
        record.get("predicted_winner"),
        record.get("winner_probability"),
        record.get("independent_home_probability"),
        record.get("final_home_probability"),
        record.get("market_home_probability"),
        record.get("model_edge"),
        record.get("fair_home_moneyline"),
        record.get("fair_away_moneyline"),
        record.get("market_home_moneyline"),
        record.get("market_away_moneyline"),
        record.get("book_count"),
        record.get("market_observed_at"),
        record.get("uncertainty_low"),
        record.get("uncertainty_high"),
        json.dumps(record.get("feature_snapshot") or {}, separators=(",", ":"), sort_keys=True),
        record.get("result", "PENDING"),
        record.get("actual_winner", ""),
    ]


def _worksheet():
    client = get_google_client()
    spreadsheet = client.open(SHEET_NAME)
    try:
        worksheet = spreadsheet.worksheet(LOG_TAB)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=LOG_TAB, rows="5000", cols=str(len(LOG_HEADERS)))
    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(LOG_HEADERS)
    elif values[0] != LOG_HEADERS:
        raise RuntimeError(f"{LOG_TAB} has an unexpected schema; no rows were changed")
    return worksheet


def _log_full_slate(records: list[dict[str, Any]]) -> tuple[int, int]:
    worksheet = _worksheet()
    values = worksheet.get_all_values()
    existing = {
        (str(row[1]), str(row[6]), str(row[22]))
        for row in values[1:]
        if len(row) >= 23
    }
    new_rows = []
    for record in records:
        key = (
            str(record.get("game_id")),
            str(record.get("model_version")),
            str(record.get("market_observed_at") or record.get("prediction_time")),
        )
        if key not in existing:
            new_rows.append(_record_to_row(record))
            existing.add(key)
    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="RAW")
    return len(new_rows), len(records) - len(new_rows)


def _grade_predictions(api_key: str) -> int:
    if not api_key:
        raise RuntimeError("CFBD_API_KEY is required for stable-ID grading")
    worksheet = _worksheet()
    rows = worksheet.get_all_values()
    pending = [(index, row) for index, row in enumerate(rows[1:], start=2) if len(row) >= 28 and row[26] == "PENDING"]
    if not pending:
        return 0
    client = CFBDDataClient(api_key)
    game_ids = sorted({safe_int(row[1]) for _, row in pending if safe_int(row[1])})
    results = {}
    for game_id in game_ids:
        games = client.optional("/games", gameId=game_id)
        if not games:
            continue
        game = games[0]
        home_points = game.get("home_points") if "home_points" in game else game.get("homePoints")
        away_points = game.get("away_points") if "away_points" in game else game.get("awayPoints")
        if home_points is None or away_points is None or safe_float(home_points) == safe_float(away_points):
            continue
        winner = (
            str(game.get("home_team") or game.get("homeTeam"))
            if safe_float(home_points) > safe_float(away_points)
            else str(game.get("away_team") or game.get("awayTeam"))
        )
        results[game_id] = winner
    updates = 0
    for sheet_row, row in pending:
        actual = results.get(safe_int(row[1]))
        if not actual:
            continue
        worksheet.update_cell(sheet_row, 27, "WIN" if row[11] == actual else "LOSS")
        worksheet.update_cell(sheet_row, 28, actual)
        updates += 1
    return updates


def _history_metrics() -> dict[str, Any]:
    try:
        rows = _worksheet().get_all_records()
    except (gspread.GSpreadException, OSError, RuntimeError, ValueError):
        return {"games": 0, "accuracy": None, "brier": None}
    graded = [row for row in rows if str(row.get("Result", "")).upper() in {"WIN", "LOSS"}]
    if not graded:
        return {"games": 0, "accuracy": None, "brier": None}
    labels = []
    probabilities = []
    correct = 0
    for row in graded:
        home_win = int(str(row.get("Actual Winner")) == str(row.get("Home Team")))
        probability = safe_float(row.get("Final Home Probability"), 0.5)
        labels.append(home_win)
        probabilities.append(probability)
        correct += int(str(row.get("Result")).upper() == "WIN")
    brier = sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / len(labels)
    return {"games": len(graded), "accuracy": correct / len(graded), "brier": brier}


def _display_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        market_home = record.get("market_home_probability")
        edge = record.get("model_edge")
        explanation = ", ".join(
            f"{item['label']} → {item['direction']}" for item in record.get("explanations") or []
        )
        rows.append(
            {
                "Kickoff": record.get("start_date"),
                "Matchup": f"{record.get('away_team')} @ {record.get('home_team')}",
                "Winner": record.get("predicted_winner"),
                "Win %": record.get("winner_probability"),
                "Range": f"{record.get('uncertainty_low', 0):.0%}–{record.get('uncertainty_high', 0):.0%}",
                "Fair ML": record.get("fair_home_moneyline") if record.get("predicted_winner") == record.get("home_team") else record.get("fair_away_moneyline"),
                "Market Home %": market_home,
                "Independent Edge": edge,
                "Books": record.get("book_count"),
                "Signal": "Validated edge" if record.get("actionable_edge") else "Prediction only",
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
        st.warning("ODDS_API_KEY is not configured. The manual matchup lab remains available, but the live slate and market comparison are unavailable.")

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
        st.warning(
            "CFBD_API_KEY is not configured. The board will use the shipped 2014–2025 public-data team states "
            "and current sportsbook schedule, without roster, coach, venue, weather, or CFBD-ID grading enrichment."
        )
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

    games = payload.get("games") or []
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
        context = SeasonContext()
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
    predictions = _build_predictions(schedule, odds_games, states, context, weather, model)

    source_columns = st.columns(4)
    source_columns[0].metric("Upcoming games", len(schedule))
    source_columns[1].metric("Teams with history", len(states))
    source_columns[2].metric("Games with market", sum(record.get("market_home_probability") is not None for record in predictions))
    source_columns[3].metric("Odds books", max((safe_int(record.get("book_count")) for record in predictions), default=0))

    tabs = st.tabs(["Winner Board", "Matchup Lab", "Validation", "Methodology"])
    with tabs[0]:
        if not predictions:
            st.info("No FBS games are scheduled in the next 21 days.")
        else:
            display = _display_frame(predictions)
            st.dataframe(
                display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Win %": st.column_config.ProgressColumn(format="percent", min_value=0.5, max_value=1.0),
                    "Market Home %": st.column_config.NumberColumn(format="percent"),
                    "Independent Edge": st.column_config.NumberColumn(format="percent"),
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
                    st.success(f"Logged {added} timestamped predictions; skipped {duplicates} exact snapshots.")
                except (gspread.GSpreadException, OSError, RuntimeError, ValueError) as exc:
                    st.error(f"Predictions were calculated but Google Sheets logging failed safely: {exc}")
        if st.button("Grade completed predictions by CFBD game ID"):
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
        historical = _history_metrics()
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
                st.dataframe(pd.DataFrame(comparison), use_container_width=True, hide_index=True)
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
            either team. Calibration and reported metrics use later-season rolling-origin folds.

            **Player scope:** player information is aggregated into team-level continuity, returning
            production, transfers, talent, and availability. The engine does not project individual stats.
            """
        )
