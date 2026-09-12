"""Standalone HagLabs NCAA football prediction logging automation.

The job is intentionally independent of Streamlit. ESPN supplies the schedule
and final scores, the validated HagLabs artifact supplies winner probabilities,
and The Odds API is an optional market comparison rather than a schedule source.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import gspread
import requests
import tomllib

from ncaaf_model import (
    SeasonContext,
    TeamState,
    WinnerModel,
    american_implied_probability,
    as_mapping,
    create_feature_snapshot,
    fetch_espn_current_season_games,
    market_consensus,
    normalize_team_name,
    parse_datetime,
    parse_espn_scoreboards,
    prediction_record,
    safe_float,
    safe_int,
    update_states_with_current_games,
)

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_PATH = PROJECT_ROOT / "data" / "ncaaf" / "model.json"
WORKBOOK_NAME = "NCAA Football Prediction Model"
WORKSHEET_NAME = "NCAAF Log"
LOCAL_TIMEZONE = ZoneInfo("America/Chicago")
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/"
    "college-football/scoreboard"
)
ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf/odds"
GOOGLE_CREDS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    or ("/etc/secrets/google_credentials.json" if Path("/etc/secrets/google_credentials.json").exists() else PROJECT_ROOT / "google_credentials.json")
)

# The first nine columns are the exact legacy contract used by app.py. New
# metadata is appended so historical rows and fixed legacy indexes remain valid.
LEGACY_HEADERS = [
    "Date",
    "Away Team",
    "Home Team",
    "Away Odds",
    "Home Odds",
    "Model Away %",
    "Model Home %",
    "Predicted Winner",
    "Result",
]
ADDITIONAL_HEADERS = [
    "Confidence",
    "Edge",
    "Notes",
    "Game ID",
    "ESPN Away Team ID",
    "ESPN Home Team ID",
    "Start Time",
    "Prediction Time",
    "Model Version",
    "Model Mode",
    "Winner Probability",
    "Vegas Pick",
    "Vegas Away %",
    "Vegas Home %",
    "Actual Winner",
    "Book Count",
    "Odds Source",
    "Neutral Site",
    "Season",
    "Week",
    "Feature Snapshot",
]
LOG_HEADERS = LEGACY_HEADERS + ADDITIONAL_HEADERS


def configured_secret(*names: str) -> str:
    """Read an existing environment/Streamlit secret without displaying it."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return ""
    try:
        payload = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    for name in names:
        value = str(payload.get(name, "")).strip()
        if value:
            return value
    return ""


def get_google_client() -> Any:
    """Create the existing service-account client without importing Streamlit."""
    return gspread.service_account(filename=str(GOOGLE_CREDS_PATH))


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TIMEZONE)


def season_for_date(target: date) -> int:
    """January bowls and the championship belong to the prior fall season."""
    return target.year if target.month >= 7 else target.year - 1


def _column_letter(number: int) -> str:
    output = ""
    value = int(number)
    while value:
        value, remainder = divmod(value - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _header_map(headers: Sequence[str]) -> dict[str, int]:
    return {str(name).strip(): index for index, name in enumerate(headers)}


def _row_value(row: Sequence[Any], headers: Sequence[str]) -> dict[str, str]:
    padded = list(row) + [""] * max(0, len(headers) - len(row))
    return {header: str(padded[index]).strip() for index, header in enumerate(headers)}


def ensure_log_headers(worksheet: Any) -> tuple[list[str], list[list[str]]]:
    """Preserve the legacy schema and append missing metadata headers only."""
    values = worksheet.get_all_values()
    if not values or not any(str(value).strip() for value in values[0]):
        worksheet.update(
            range_name=f"A1:{_column_letter(len(LOG_HEADERS))}1",
            values=[LOG_HEADERS],
            value_input_option="RAW",
        )
        return list(LOG_HEADERS), [list(LOG_HEADERS)]

    existing = [str(value).strip() for value in values[0]]
    if existing[: len(LEGACY_HEADERS)] != LEGACY_HEADERS:
        raise RuntimeError(
            f"{WORKSHEET_NAME} has an unexpected legacy schema; no prediction rows were changed"
        )
    merged = list(existing)
    for header in LOG_HEADERS:
        if header not in merged:
            merged.append(header)
    if merged != existing:
        if getattr(worksheet, "col_count", len(merged)) < len(merged):
            worksheet.resize(cols=len(merged))
        worksheet.update(
            range_name=f"A1:{_column_letter(len(merged))}1",
            values=[merged],
            value_input_option="RAW",
        )
        values[0] = merged
    return merged, values


def get_or_create_log_worksheet(client: Any | None = None) -> Any:
    client = client or get_google_client()
    try:
        spreadsheet = client.open(WORKBOOK_NAME)
    except gspread.exceptions.SpreadsheetNotFound:
        spreadsheet = client.create(WORKBOOK_NAME)
    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET_NAME,
            rows="5000",
            cols=str(len(LOG_HEADERS)),
        )
    ensure_log_headers(worksheet)
    return worksheet


def fetch_scoreboard_date(
    date_value: str | date,
    session: Any = requests,
) -> list[dict[str, Any]]:
    target = date.fromisoformat(str(date_value)) if not isinstance(date_value, date) else date_value
    response = session.get(
        ESPN_SCOREBOARD_URL,
        params={"dates": target.strftime("%Y%m%d"), "groups": 80, "limit": 500},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"ESPN NCAA scoreboard returned HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("ESPN NCAA scoreboard returned an unexpected payload")
    games = parse_espn_scoreboards([payload], season_for_date(target))
    return [
        game
        for game in games
        if (
            (parse_datetime(game.get("start_date")) or datetime.min.replace(tzinfo=timezone.utc))
            .astimezone(LOCAL_TIMEZONE)
            .date()
            == target
        )
    ]


def fetch_live_odds(
    api_key: str,
    session: Any = requests,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not api_key:
        return [], {"status": "NO_KEY", "message": "No Odds API key configured; model-only rows remain enabled."}
    try:
        response = session.get(
            ODDS_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings,fanduel,betmgm,caesars,espnbet,fanatics",
            },
            timeout=25,
        )
        if response.status_code != 200:
            return [], {
                "status": response.status_code,
                "message": f"Odds service returned HTTP {response.status_code}; model-only rows remain enabled.",
            }
        payload = response.json()
        games = [as_mapping(item) for item in payload] if isinstance(payload, list) else []
        return games, {"status": 200, "message": f"Loaded {len(games)} NCAA odds events."}
    except (requests.RequestException, TypeError, ValueError) as exc:
        return [], {
            "status": "ERROR",
            "message": f"Odds unavailable ({type(exc).__name__}); model-only rows remain enabled.",
        }


def _game_aliases(game: Mapping[str, Any], side: str) -> list[str]:
    return [
        str(game.get(f"{side}_team") or ""),
        str(game.get(f"{side}_team_display") or ""),
        str(game.get(f"{side}_team_short") or ""),
        str(game.get(f"{side}_team_abbreviation") or ""),
    ]


def _names_match(name: str, aliases: Iterable[str]) -> bool:
    target = normalize_team_name(name)
    if not target:
        return False
    normalized_aliases = {normalize_team_name(alias) for alias in aliases if str(alias).strip()}
    if target in normalized_aliases:
        return True
    target_tokens = set(target.split())
    for alias in normalized_aliases:
        alias_tokens = set(alias.split())
        # Containment is allowed only for multi-token location names. This keeps
        # Miami and Miami (OH), for example, from ever being paired loosely.
        if min(len(target_tokens), len(alias_tokens)) >= 2 and (
            target_tokens <= alias_tokens or alias_tokens <= target_tokens
        ):
            return True
    return False


def find_odds_for_game(
    game: Mapping[str, Any],
    odds_games: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    matches: list[dict[str, Any]] = []
    kickoff = parse_datetime(game.get("start_date"))
    for raw_odds in odds_games:
        odds = as_mapping(raw_odds)
        if not _names_match(str(odds.get("home_team") or ""), _game_aliases(game, "home")):
            continue
        if not _names_match(str(odds.get("away_team") or ""), _game_aliases(game, "away")):
            continue
        odds_kickoff = parse_datetime(odds.get("commence_time"))
        if kickoff and odds_kickoff and abs((kickoff - odds_kickoff).total_seconds()) > 18 * 3600:
            continue
        matches.append(odds)
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "multiple odds events matched; odds were withheld"
    return None, None


def resolve_model_team(names: str | Iterable[str], states: Mapping[str, TeamState]) -> str | None:
    aliases = [names] if isinstance(names, str) else list(names)
    targets = {normalize_team_name(name) for name in aliases if str(name).strip()}
    exact = [candidate for candidate in states if normalize_team_name(candidate) in targets]
    if exact:
        # Historical providers occasionally created both location-only and full
        # display-name states. They are the same team when both match an exact
        # ESPN alias; prefer the state updated most recently this season.
        return max(
            exact,
            key=lambda candidate: (
                parse_datetime(states[candidate].last_game)
                or datetime.min.replace(tzinfo=timezone.utc),
                states[candidate].games,
            ),
        )
    contained: list[str] = []
    for candidate in states:
        candidate_tokens = set(normalize_team_name(candidate).split())
        for target in targets:
            target_tokens = set(target.split())
            if min(len(target_tokens), len(candidate_tokens)) >= 2 and (
                target_tokens <= candidate_tokens or candidate_tokens <= target_tokens
            ):
                contained.append(candidate)
                break
    return contained[0] if len(contained) == 1 else None


def load_model_runtime(
    season: int,
    session: Any = requests,
) -> tuple[WinnerModel, dict[str, TeamState], SeasonContext, dict[str, Any]]:
    if not MODEL_PATH.exists():
        raise RuntimeError(f"NCAA model artifact is missing: {MODEL_PATH}")
    model = WinnerModel.load(MODEL_PATH)
    state_season = safe_int(model.metadata.get("team_state_season"))
    if state_season != int(season):
        raise RuntimeError(
            f"NCAA model states are for {state_season or 'an unknown season'}, not {season}; rebuild the artifact before logging"
        )
    states = {
        team: TeamState(**state)
        for team, state in (model.metadata.get("team_states") or {}).items()
    }
    if not states:
        raise RuntimeError("NCAA model artifact contains no team states")
    current_games = fetch_espn_current_season_games(season, session=session)
    states, refresh = update_states_with_current_games(states, current_games)
    return model, states, SeasonContext(), refresh


def _confidence(probability: float) -> str:
    if probability >= 0.70:
        return "High"
    if probability >= 0.60:
        return "Medium"
    return "Tracking"


def _kickoff_has_passed(game: Mapping[str, Any], now: datetime) -> bool:
    kickoff = parse_datetime(game.get("start_date"))
    if kickoff is None:
        return True
    return kickoff <= now.astimezone(timezone.utc)


def build_prediction_records(
    schedule: Sequence[Mapping[str, Any]],
    odds_games: Sequence[Mapping[str, Any]],
    states: Mapping[str, TeamState],
    model: WinnerModel,
    context: SeasonContext | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    timestamp = (now or local_now()).astimezone(timezone.utc)
    predictions: list[dict[str, Any]] = []
    warnings: list[str] = []
    context = context or SeasonContext()
    for raw_game in schedule:
        game = as_mapping(raw_game)
        label = f"{game.get('away_team')} @ {game.get('home_team')}"
        try:
            status = str(game.get("status_state") or "pre").lower()
            detail = str(game.get("status_detail") or "").lower()
            if game.get("completed") or status == "post":
                warnings.append(f"SKIP: {label} is already final.")
                continue
            if status == "in" or _kickoff_has_passed(game, timestamp):
                warnings.append(f"SKIP: {label} has already started; no retroactive prediction was logged.")
                continue
            if any(word in detail for word in ("canceled", "cancelled", "postponed")):
                warnings.append(f"SKIP: {label} is {game.get('status_detail') or 'not active'}.")
                continue

            away_display = str(game.get("away_team") or "")
            home_display = str(game.get("home_team") or "")
            away_model = resolve_model_team(_game_aliases(game, "away"), states)
            home_model = resolve_model_team(_game_aliases(game, "home"), states)
            if not away_model or not home_model or away_model == home_model:
                missing = away_display if not away_model else home_display
                warnings.append(f'WARNING: Could not safely map "{missing}" to the NCAA model; skipped {label}.')
                continue

            aligned_states = dict(states)
            aligned_states[away_display] = states[away_model]
            aligned_states[home_display] = states[home_model]
            feature_row = create_feature_snapshot(game, aligned_states, context)
            odds_game, odds_warning = find_odds_for_game(game, odds_games)
            market = market_consensus(odds_game) if odds_game else None
            record = prediction_record(game, feature_row, model, market, timestamp)
            record["away_team_id"] = game.get("away_team_id")
            record["home_team_id"] = game.get("home_team_id")
            record["status_state"] = game.get("status_state")
            record["status_detail"] = game.get("status_detail")
            record["confidence"] = _confidence(
                safe_float(record.get("independent_winner_probability"), 0.5)
            )
            if odds_warning:
                warnings.append(f"WARNING: {label}: {odds_warning}.")
            if market is None:
                warnings.append(f"WARNING: Odds unavailable for {label}; model prediction remains eligible.")
            predictions.append(record)
        except Exception as exc:  # noqa: BLE001 - per-game isolation is required for unattended runs
            warnings.append(f"ERROR: {label}: {type(exc).__name__}: {exc}")
    return predictions, warnings


def _format_probability(value: Any) -> str:
    probability = safe_float(value, float("nan"))
    return f"{probability:.2%}" if math.isfinite(probability) else ""


def prediction_to_sheet_values(record: Mapping[str, Any]) -> dict[str, Any]:
    home_probability = safe_float(record.get("independent_home_probability"), 0.5)
    winner = str(record.get("independent_predicted_winner") or record.get("predicted_winner") or "")
    home_team = str(record.get("home_team") or "")
    market_home = safe_float(record.get("market_home_probability"), float("nan"))
    has_market = math.isfinite(market_home)
    market_pick = ""
    edge = ""
    if has_market:
        market_pick = home_team if market_home >= 0.5 else str(record.get("away_team") or "")
        home_edge = home_probability - market_home
        winner_edge = home_edge if winner == home_team else -home_edge
        edge = f"{winner_edge:+.2%}"
    kickoff = parse_datetime(record.get("start_date"))
    if kickoff is None:
        raise ValueError("prediction has no valid kickoff")
    notes = "No Vegas Odds"
    if has_market:
        agreement = "Market agreement" if winner == market_pick else "Market disagreement"
        notes = f"{agreement}; consensus from {safe_int(record.get('book_count'))} books"
    return {
        "Date": kickoff.astimezone(LOCAL_TIMEZONE).date().isoformat(),
        "Away Team": record.get("away_team"),
        "Home Team": home_team,
        "Away Odds": record.get("market_away_moneyline") if has_market else "",
        "Home Odds": record.get("market_home_moneyline") if has_market else "",
        "Model Away %": _format_probability(1.0 - home_probability),
        "Model Home %": _format_probability(home_probability),
        "Predicted Winner": winner,
        "Result": "PENDING",
        "Confidence": record.get("confidence") or _confidence(max(home_probability, 1.0 - home_probability)),
        "Edge": edge,
        "Notes": notes,
        "Game ID": record.get("game_id"),
        "ESPN Away Team ID": record.get("away_team_id"),
        "ESPN Home Team ID": record.get("home_team_id"),
        "Start Time": kickoff.isoformat(),
        "Prediction Time": record.get("prediction_time"),
        "Model Version": record.get("model_version"),
        "Model Mode": record.get("model_mode"),
        "Winner Probability": _format_probability(record.get("independent_winner_probability")),
        "Vegas Pick": market_pick,
        "Vegas Away %": _format_probability(1.0 - market_home) if has_market else "",
        "Vegas Home %": _format_probability(market_home) if has_market else "",
        "Actual Winner": "",
        "Book Count": record.get("book_count") if has_market else 0,
        "Odds Source": "Live consensus" if has_market else "No Vegas Odds",
        "Neutral Site": bool(record.get("neutral_site")),
        "Season": record.get("season"),
        "Week": record.get("week"),
        "Feature Snapshot": json.dumps(
            record.get("feature_snapshot") or {}, separators=(",", ":"), sort_keys=True
        ),
    }


def _matchup_key(date_value: str, away: str, home: str) -> tuple[str, str, str]:
    return date_value.strip(), normalize_team_name(away), normalize_team_name(home)


def log_prediction_records(
    records: Sequence[Mapping[str, Any]],
    worksheet: Any | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    worksheet = worksheet or get_or_create_log_worksheet(client)
    headers, values = ensure_log_headers(worksheet)
    existing_ids: set[str] = set()
    existing_matchups: set[tuple[str, str, str]] = set()
    for raw_row in values[1:]:
        row = _row_value(raw_row, headers)
        game_id = row.get("Game ID", "")
        if game_id:
            existing_ids.add(game_id)
        existing_matchups.add(
            _matchup_key(row.get("Date", ""), row.get("Away Team", ""), row.get("Home Team", ""))
        )

    current = (now or local_now()).astimezone(timezone.utc)
    rows_to_append: list[list[Any]] = []
    duplicates = 0
    skipped_started = 0
    errors: list[str] = []
    for record in records:
        try:
            kickoff = parse_datetime(record.get("start_date"))
            if kickoff is None or kickoff <= current:
                skipped_started += 1
                continue
            mapped = prediction_to_sheet_values(record)
            game_id = str(mapped.get("Game ID") or "")
            matchup = _matchup_key(
                str(mapped.get("Date") or ""),
                str(mapped.get("Away Team") or ""),
                str(mapped.get("Home Team") or ""),
            )
            if (game_id and game_id in existing_ids) or matchup in existing_matchups:
                duplicates += 1
                continue
            rows_to_append.append([mapped.get(header, "") for header in headers])
            if game_id:
                existing_ids.add(game_id)
            existing_matchups.add(matchup)
        except Exception as exc:  # noqa: BLE001 - one malformed record must not stop the slate
            errors.append(f"{record.get('away_team')} @ {record.get('home_team')}: {type(exc).__name__}: {exc}")
    if rows_to_append:
        worksheet.append_rows(rows_to_append, value_input_option="RAW")
    return {
        "logged": len(rows_to_append),
        "duplicates": duplicates,
        "skipped_started": skipped_started,
        "errors": errors,
    }


def _row_matches_game(row: Mapping[str, str], game: Mapping[str, Any]) -> bool:
    return _names_match(row.get("Away Team", ""), _game_aliases(game, "away")) and _names_match(
        row.get("Home Team", ""), _game_aliases(game, "home")
    )


def grade_pending_games(
    worksheet: Any | None = None,
    client: Any | None = None,
    session: Any = requests,
) -> dict[str, Any]:
    worksheet = worksheet or get_or_create_log_worksheet(client)
    headers, values = ensure_log_headers(worksheet)
    indexes = _header_map(headers)
    pending = [
        (sheet_row, _row_value(raw_row, headers))
        for sheet_row, raw_row in enumerate(values[1:], start=2)
        if _row_value(raw_row, headers).get("Result", "").upper() == "PENDING"
    ]
    if not pending:
        return {"pending": 0, "graded": 0, "unresolved": 0, "errors": []}

    schedules: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for date_value in sorted({row.get("Date", "") for _, row in pending}):
        try:
            schedules[date_value] = fetch_scoreboard_date(date_value, session=session)
        except (ValueError, requests.RequestException, RuntimeError, TypeError) as exc:
            schedules[date_value] = []
            errors.append(f"{date_value}: {type(exc).__name__}: {exc}")

    updates: list[dict[str, Any]] = []
    graded = 0
    unresolved = 0
    result_column = _column_letter(indexes["Result"] + 1)
    actual_column = _column_letter(indexes["Actual Winner"] + 1)
    for sheet_row, row in pending:
        candidates = schedules.get(row.get("Date", ""), [])
        game_id = row.get("Game ID", "")
        match = next((game for game in candidates if game_id and str(game.get("game_id")) == game_id), None)
        if match is None:
            strict_matches = [game for game in candidates if _row_matches_game(row, game)]
            match = strict_matches[0] if len(strict_matches) == 1 else None
        if not match or not match.get("completed"):
            unresolved += 1
            continue
        home_points = safe_float(match.get("home_points"), float("nan"))
        away_points = safe_float(match.get("away_points"), float("nan"))
        if not math.isfinite(home_points) or not math.isfinite(away_points) or home_points == away_points:
            unresolved += 1
            continue
        actual = row.get("Home Team", "") if home_points > away_points else row.get("Away Team", "")
        result = "WIN" if normalize_team_name(row.get("Predicted Winner", "")) == normalize_team_name(actual) else "LOSS"
        updates.extend(
            [
                {"range": f"{result_column}{sheet_row}", "values": [[result]]},
                {"range": f"{actual_column}{sheet_row}", "values": [[actual]]},
            ]
        )
        graded += 1
    if updates:
        worksheet.batch_update(updates, value_input_option="RAW")
    return {
        "pending": len(pending),
        "graded": graded,
        "unresolved": unresolved,
        "errors": errors,
    }


def _parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "").replace("+", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100.0 if abs(number) > 1.0 else number


def get_log_stats(
    worksheet: Any | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    worksheet = worksheet or get_or_create_log_worksheet(client)
    headers, values = ensure_log_headers(worksheet)
    rows = [_row_value(row, headers) for row in values[1:] if any(str(value).strip() for value in row)]
    graded = [row for row in rows if row.get("Result", "").upper() in {"WIN", "LOSS"}]
    wins = sum(row.get("Result", "").upper() == "WIN" for row in graded)
    losses = len(graded) - wins
    pending = sum(row.get("Result", "").upper() == "PENDING" for row in rows)
    vegas_wins = 0
    model_vegas_wins = 0
    vegas_games = 0
    brier_values: list[float] = []
    confidence = defaultdict(lambda: {"games": 0, "wins": 0, "accuracy": None})
    for row in graded:
        tier = row.get("Confidence", "").strip() or "Unclassified"
        confidence[tier]["games"] += 1
        confidence[tier]["wins"] += int(row.get("Result", "").upper() == "WIN")
        home_probability = _parse_percent(row.get("Model Home %"))
        actual = row.get("Actual Winner", "")
        if not actual:
            predicted = row.get("Predicted Winner", "")
            actual = predicted if row.get("Result", "").upper() == "WIN" else (
                row.get("Away Team", "") if normalize_team_name(predicted) == normalize_team_name(row.get("Home Team", "")) else row.get("Home Team", "")
            )
        if home_probability is not None:
            home_won = normalize_team_name(actual) == normalize_team_name(row.get("Home Team", ""))
            brier_values.append((home_probability - float(home_won)) ** 2)

        away_odds = safe_float(row.get("Away Odds"), float("nan"))
        home_odds = safe_float(row.get("Home Odds"), float("nan"))
        away_implied = american_implied_probability(away_odds)
        home_implied = american_implied_probability(home_odds)
        if away_implied is None or home_implied is None:
            continue
        vegas_games += 1
        vegas_pick = row.get("Vegas Pick", "").strip()
        if not vegas_pick:
            vegas_pick = row.get("Away Team", "") if away_implied >= home_implied else row.get("Home Team", "")
        vegas_wins += int(normalize_team_name(vegas_pick) == normalize_team_name(actual))
        model_vegas_wins += int(row.get("Result", "").upper() == "WIN")
    for metrics in confidence.values():
        metrics["accuracy"] = metrics["wins"] / metrics["games"] if metrics["games"] else None
    model_accuracy = wins / len(graded) if graded else None
    vegas_accuracy = vegas_wins / vegas_games if vegas_games else None
    model_vegas_accuracy = model_vegas_wins / vegas_games if vegas_games else None
    return {
        "rows": len(rows),
        "graded": len(graded),
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "model_accuracy": model_accuracy,
        "vegas_games": vegas_games,
        "vegas_accuracy": vegas_accuracy,
        "model_accuracy_on_vegas": model_vegas_accuracy,
        "model_advantage": (
            model_vegas_accuracy - vegas_accuracy
            if model_vegas_accuracy is not None and vegas_accuracy is not None
            else None
        ),
        "brier": sum(brier_values) / len(brier_values) if brier_values else None,
        "confidence": dict(confidence),
    }


def run_daily(
    date_value: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    session: Any = requests,
    client: Any | None = None,
) -> dict[str, Any]:
    current = now or local_now()
    target_date = date.fromisoformat(date_value) if date_value else current.date()
    summary: dict[str, Any] = {
        "date": target_date.isoformat(),
        "graded": 0,
        "games_found": 0,
        "modelable": 0,
        "with_odds": 0,
        "without_odds": 0,
        "duplicates": 0,
        "logged": 0,
        "skipped": 0,
        "errors": [],
        "warnings": [],
        "dry_run": dry_run,
    }
    worksheet = None
    if not dry_run:
        worksheet = get_or_create_log_worksheet(client)
        grade_result = grade_pending_games(worksheet=worksheet, session=session)
        summary["graded"] = grade_result["graded"]
        summary["errors"].extend(grade_result["errors"])

    schedule = fetch_scoreboard_date(target_date, session=session)
    summary["games_found"] = len(schedule)
    model, states, context, refresh = load_model_runtime(season_for_date(target_date), session=session)
    summary["state_refresh"] = refresh
    odds_games, odds_status = fetch_live_odds(
        configured_secret("ODDS_API_KEY", "THE_ODDS_API_KEY"), session=session
    )
    summary["odds_status"] = odds_status
    records, warnings = build_prediction_records(
        schedule,
        odds_games,
        states,
        model,
        context=context,
        now=current,
    )
    summary["warnings"].extend(warnings)
    summary["modelable"] = len(records)
    summary["with_odds"] = sum(record.get("market_home_probability") is not None for record in records)
    summary["without_odds"] = len(records) - summary["with_odds"]
    summary["skipped"] = len(schedule) - len(records)
    if not dry_run:
        log_result = log_prediction_records(records, worksheet=worksheet, now=current)
        summary["logged"] = log_result["logged"]
        summary["duplicates"] = log_result["duplicates"]
        summary["skipped"] += log_result["skipped_started"]
        summary["errors"].extend(log_result["errors"])
        summary["stats"] = get_log_stats(worksheet=worksheet)
    return summary


def print_summary(summary: Mapping[str, Any]) -> None:
    print("NCAA FOOTBALL DAILY AUTOMATION")
    print(f"Date: {summary['date']}")
    print(f"Pending games graded: {summary['graded']}")
    print(f"Games found today: {summary['games_found']}")
    print(f"Games modelable: {summary['modelable']}")
    print(f"Games with Vegas odds: {summary['with_odds']}")
    print(f"Games without Vegas odds: {summary['without_odds']}")
    print(f"Previously logged: {summary['duplicates']}")
    print(f"New games logged: {summary['logged']}")
    print(f"Skipped/unmatched: {summary['skipped']}")
    print(f"Errors: {len(summary['errors'])}")
    if summary.get("dry_run"):
        print("Mode: DRY RUN (Google Sheets was not read or written)")
    for warning in summary.get("warnings", []):
        print(warning)
    for error in summary.get("errors", []):
        print(f"ERROR: {error}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the standalone HagLabs NCAA football logger.")
    parser.add_argument("--date", help="Local slate date in YYYY-MM-DD format; defaults to America/Chicago today.")
    parser.add_argument("--dry-run", action="store_true", help="Discover and model without reading or writing Google Sheets.")
    args = parser.parse_args(argv)
    try:
        summary = run_daily(date_value=args.date, dry_run=args.dry_run)
        print_summary(summary)
        return 1 if summary["errors"] else 0
    except Exception as exc:  # noqa: BLE001 - CLI must fail loudly with a concise terminal error
        print("NCAA FOOTBALL DAILY AUTOMATION FAILED", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
