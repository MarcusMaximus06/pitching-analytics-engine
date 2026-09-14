"""Standalone NFL prediction logging and ESPN-final grading.

The job is safe for unattended execution: it grades existing pending rows first,
discovers the current NFL slate independently from the odds feed, preserves the
first pregame snapshot, and never invents unavailable market data.
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import requests

from daily_ncaaf_auto import configured_secret, get_google_client
from nfl_prediction_config import NFL_LOG_COLUMNS, NFL_TEAM_RATINGS
from nfl_season_model import (
    build_game_probabilities,
    current_nfl_season,
    fetch_espn_nfl_schedule,
    normalize_team_name,
    parse_espn_schedule,
)

WORKBOOK_NAME = "NFL Prediction Model"
WORKSHEET_NAME = "NFL Log V2"
LOCAL_TIMEZONE = ZoneInfo("America/Chicago")
EASTERN_TIMEZONE = ZoneInfo("America/New_York")
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/"


def _column_letter(number: int) -> str:
    output = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _row_map(headers: Sequence[str], raw: Sequence[Any]) -> dict[str, str]:
    padded = list(raw) + [""] * max(0, len(headers) - len(raw))
    return {header: str(padded[index]) for index, header in enumerate(headers)}


def ensure_headers(worksheet: Any) -> tuple[list[str], list[list[str]]]:
    values = worksheet.get_all_values()
    if not values or not any(str(value).strip() for value in values[0]):
        worksheet.resize(cols=len(NFL_LOG_COLUMNS))
        worksheet.update(range_name=f"A1:{_column_letter(len(NFL_LOG_COLUMNS))}1", values=[NFL_LOG_COLUMNS])
        return list(NFL_LOG_COLUMNS), [list(NFL_LOG_COLUMNS)]
    headers = [str(value) for value in values[0]]
    missing = [header for header in NFL_LOG_COLUMNS if header not in headers]
    if missing:
        headers.extend(missing)
        worksheet.resize(cols=len(headers))
        worksheet.update(range_name=f"A1:{_column_letter(len(headers))}1", values=[headers])
        values[0] = headers
    return headers, values


def get_or_create_worksheet(client: Any | None = None) -> Any:
    client = client or get_google_client()
    spreadsheet = client.open(WORKBOOK_NAME)
    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET_NAME,
            rows=3000,
            cols=len(NFL_LOG_COLUMNS),
        )
    ensure_headers(worksheet)
    return worksheet


def _canonical_team(value: Any) -> str | None:
    key = normalize_team_name(value)
    matches = [team for team in NFL_TEAM_RATINGS if normalize_team_name(team) == key]
    if len(matches) == 1:
        return matches[0]
    aliases = {
        "washingtonfootballteam": "Washington Commanders",
        "lachargers": "Los Angeles Chargers",
        "larams": "Los Angeles Rams",
    }
    return aliases.get(key)


def _american_implied(value: Any) -> float | None:
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(odds) or odds == 0:
        return None
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def _average(values: Sequence[Any]) -> float | None:
    numbers = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numbers.append(number)
    return sum(numbers) / len(numbers) if numbers else None


def fetch_live_odds(session: Any = requests) -> tuple[list[dict[str, Any]], str | None]:
    api_key = configured_secret("ODDS_API_KEY", "THE_ODDS_API_KEY")
    if not api_key:
        return [], "ODDS_API_KEY is not configured"
    try:
        response = session.get(
            ODDS_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "bookmakers": "draftkings,fanduel,betmgm,caesars,espnbet,betrivers",
            },
            timeout=25,
        )
        if response.status_code != 200:
            return [], f"The Odds API returned HTTP {response.status_code}"
        return list(response.json() or []), None
    except (OSError, requests.RequestException, ValueError) as exc:
        return [], f"The Odds API failed: {type(exc).__name__}"


def parse_odds_board(games: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for game in games:
        away = _canonical_team(game.get("away_team"))
        home = _canonical_team(game.get("home_team"))
        if not away or not home or away == home:
            continue
        away_prices: list[Any] = []
        home_prices: list[Any] = []
        home_probabilities: list[float] = []
        home_spreads: list[Any] = []
        totals: list[Any] = []
        books = 0
        for bookmaker in game.get("bookmakers") or []:
            found_h2h = False
            for market in bookmaker.get("markets") or []:
                outcomes = market.get("outcomes") or []
                if market.get("key") == "h2h":
                    prices = {
                        _canonical_team(item.get("name")): item.get("price")
                        for item in outcomes
                    }
                    away_price = prices.get(away)
                    home_price = prices.get(home)
                    away_implied = _american_implied(away_price)
                    home_implied = _american_implied(home_price)
                    if away_implied is not None and home_implied is not None:
                        total = away_implied + home_implied
                        away_prices.append(away_price)
                        home_prices.append(home_price)
                        home_probabilities.append(home_implied / total)
                        found_h2h = True
                elif market.get("key") == "spreads":
                    for item in outcomes:
                        if _canonical_team(item.get("name")) == home:
                            home_spreads.append(item.get("point"))
                elif market.get("key") == "totals":
                    for item in outcomes:
                        if str(item.get("name") or "").lower() == "over":
                            totals.append(item.get("point"))
            books += int(found_h2h)
        if not home_probabilities:
            continue
        home_probability = statistics.median(home_probabilities)
        rows.append(
            {
                "Event ID": str(game.get("id") or ""),
                "Away Team": away,
                "Home Team": home,
                "Away ML": _average(away_prices),
                "Home ML": _average(home_prices),
                "Vegas Away %": (1.0 - home_probability) * 100.0,
                "Vegas Home %": home_probability * 100.0,
                "Spread": _average(home_spreads),
                "Total": _average(totals),
                "Books": books,
            }
        )
    return pd.DataFrame(rows)


def _date_for_kickoff(value: Any) -> str:
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return ""
    return timestamp.tz_convert(EASTERN_TIMEZONE).date().isoformat()


def _log_id(date_value: str, away: str, home: str) -> str:
    return f"{date_value.strip()}|{normalize_team_name(away)}|{normalize_team_name(home)}"


def _format_probability(value: Any) -> str:
    try:
        return f"{float(value) * 100.0:.1f}"
    except (TypeError, ValueError):
        return ""


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    return str(round(number)) if abs(number - round(number)) < 0.05 else f"{number:.1f}"


def build_today_rows(
    season_schedule: pd.DataFrame,
    odds_board: pd.DataFrame,
    target_date: date,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    if season_schedule.empty:
        return [], []
    games, _ = build_game_probabilities(
        season_schedule,
        NFL_TEAM_RATINGS,
        odds_board,
        market_weight=0.70,
    )
    odds_index = {
        (normalize_team_name(row["Away Team"]), normalize_team_name(row["Home Team"])): row
        for row in odds_board.to_dict("records")
    }
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for game in games.to_dict("records"):
        if _date_for_kickoff(game.get("start_date")) != target_date.isoformat():
            continue
        label = f"{game.get('away_team')} @ {game.get('home_team')}"
        kickoff = pd.to_datetime(game.get("start_date"), utc=True, errors="coerce")
        if bool(game.get("completed")) or pd.isna(kickoff) or kickoff.to_pydatetime() <= now.astimezone(timezone.utc):
            warnings.append(f"SKIP: {label} has started or is final; no retroactive snapshot was logged.")
            continue
        away = str(game.get("away_team") or "")
        home = str(game.get("home_team") or "")
        market = odds_index.get((normalize_team_name(away), normalize_team_name(home)))
        model_home = float(game.get("elo_home_probability"))
        final_home = float(game.get("home_win_probability"))
        model_pick = home if final_home >= 0.5 else away
        vegas_home = float(game["market_home_probability"]) if pd.notna(game.get("market_home_probability")) else None
        vegas_pick = (home if vegas_home >= 0.5 else away) if vegas_home is not None else ""
        selected_model = model_home if model_pick == home else 1.0 - model_home
        selected_market = (
            (vegas_home if model_pick == home else 1.0 - vegas_home)
            if vegas_home is not None
            else None
        )
        edge = (selected_model - selected_market) * 100.0 if selected_market is not None else None
        confidence = "High" if max(final_home, 1.0 - final_home) >= 0.70 else (
            "Medium" if max(final_home, 1.0 - final_home) >= 0.60 else "Tracking"
        )
        if market is None:
            warnings.append(f"WARNING: Odds unavailable for {label}; updated-Elo prediction remains eligible.")
        rows.append(
            {
                "Log ID": _log_id(target_date.isoformat(), away, home),
                "Date": target_date.isoformat(),
                "Start Time": kickoff.isoformat(),
                "Away Team": away,
                "Home Team": home,
                "Away ML": _format_number((market or {}).get("Away ML")),
                "Home ML": _format_number((market or {}).get("Home ML")),
                "Vegas Away %": _format_probability(1.0 - vegas_home) if vegas_home is not None else "",
                "Vegas Home %": _format_probability(vegas_home) if vegas_home is not None else "",
                "Spread": _format_number((market or {}).get("Spread")),
                "Total": _format_number((market or {}).get("Total")),
                "Model Away %": _format_probability(1.0 - model_home),
                "Model Home %": _format_probability(model_home),
                "Model Pick": model_pick,
                "Vegas Pick": vegas_pick,
                "Model Edge %": f"{edge:+.1f}" if edge is not None else "",
                "Confidence": confidence,
                "Official Pick": "FALSE",
                "Status": "PENDING",
                "Odds Source": "Live multi-book consensus" if market is not None else "No Vegas Odds",
                "Opening Snapshot Time": now.astimezone(LOCAL_TIMEZONE).isoformat(),
                "Actual Winner": "",
                "Model Result": "",
                "Early Vegas Result": "",
                "Closing Vegas Result": "",
                "Event ID": str(game.get("game_id") or ""),
                "Decision Source": str(game.get("probability_source") or "Updated Elo"),
            }
        )
    return rows, warnings


def log_rows(rows: Sequence[Mapping[str, Any]], worksheet: Any) -> dict[str, int]:
    headers, values = ensure_headers(worksheet)
    existing_ids = set()
    existing_events = set()
    for raw in values[1:]:
        row = _row_map(headers, raw)
        existing_ids.add(row.get("Log ID") or _log_id(row.get("Date", ""), row.get("Away Team", ""), row.get("Home Team", "")))
        if row.get("Event ID"):
            existing_events.add(row["Event ID"])
    new_rows: list[list[Any]] = []
    duplicates = 0
    for row in rows:
        if str(row.get("Log ID")) in existing_ids or (row.get("Event ID") and str(row.get("Event ID")) in existing_events):
            duplicates += 1
            continue
        new_rows.append([row.get(header, "") for header in headers])
        existing_ids.add(str(row.get("Log ID")))
        if row.get("Event ID"):
            existing_events.add(str(row.get("Event ID")))
    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="RAW")
    return {"logged": len(new_rows), "duplicates": duplicates}


def _fetch_scoreboard_date(date_value: str, session: Any = requests) -> pd.DataFrame:
    parsed = date.fromisoformat(date_value)
    season = parsed.year if parsed.month >= 7 else parsed.year - 1
    response = session.get(
        ESPN_SCOREBOARD_URL,
        params={"dates": parsed.strftime("%Y%m%d"), "limit": 100},
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"ESPN NFL scoreboard returned HTTP {response.status_code}")
    return parse_espn_schedule([response.json()], season)


def grade_pending(worksheet: Any, session: Any = requests) -> dict[str, Any]:
    headers, values = ensure_headers(worksheet)
    pending = [
        (index, _row_map(headers, raw))
        for index, raw in enumerate(values[1:], start=2)
        if _row_map(headers, raw).get("Status", "").upper() == "PENDING"
    ]
    if not pending:
        return {"graded": 0, "errors": []}
    finals_by_event: dict[str, dict[str, Any]] = {}
    finals_by_matchup: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for date_value in sorted({row.get("Date", "") for _, row in pending if row.get("Date")}):
        try:
            games = _fetch_scoreboard_date(date_value, session=session)
        except Exception as exc:  # noqa: BLE001 - one date must not block other dates
            errors.append(f"{date_value}: {type(exc).__name__}: {exc}")
            continue
        for game in games.to_dict("records"):
            if not bool(game.get("completed")):
                continue
            home_score = float(game.get("home_score"))
            away_score = float(game.get("away_score"))
            if home_score == away_score:
                continue
            winner = game["home_team"] if home_score > away_score else game["away_team"]
            final = {**game, "winner": winner}
            finals_by_event[str(game.get("game_id") or "")] = final
            finals_by_matchup[_log_id(date_value, game["away_team"], game["home_team"])] = final

    updates: list[dict[str, Any]] = []
    graded = 0
    column = {header: index + 1 for index, header in enumerate(headers)}
    for row_number, row in pending:
        final = finals_by_event.get(row.get("Event ID", "")) if row.get("Event ID") else None
        if final is None:
            final = finals_by_matchup.get(
                _log_id(row.get("Date", ""), row.get("Away Team", ""), row.get("Home Team", ""))
            )
        if final is None:
            continue
        actual = str(final["winner"])
        model_pick = str(row.get("Model Pick") or "")
        vegas_pick = str(row.get("Vegas Pick") or "")
        closing_pick = str(row.get("Closing Vegas Pick") or "") or vegas_pick
        model_result = "WIN" if normalize_team_name(model_pick) == normalize_team_name(actual) else "LOSS"
        early_result = (
            "WIN" if normalize_team_name(vegas_pick) == normalize_team_name(actual) else "LOSS"
        ) if vegas_pick else ""
        closing_result = (
            "WIN" if normalize_team_name(closing_pick) == normalize_team_name(actual) else "LOSS"
        ) if closing_pick else ""
        values_by_header = {
            "Actual Winner": actual,
            "Model Result": model_result,
            "Early Vegas Result": early_result,
            "Closing Vegas Result": closing_result,
            "Status": model_result,
        }
        for header, value in values_by_header.items():
            cell = f"{_column_letter(column[header])}{row_number}"
            updates.append({"range": cell, "values": [[value]]})
        graded += 1
    if updates:
        worksheet.batch_update(updates, value_input_option="RAW")
    return {"graded": graded, "errors": errors}


def run_daily(
    date_value: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    session: Any = requests,
    client: Any | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    target = date.fromisoformat(date_value) if date_value else now.astimezone(LOCAL_TIMEZONE).date()
    worksheet = None if dry_run else get_or_create_worksheet(client)
    grading = {"graded": 0, "errors": []} if dry_run else grade_pending(worksheet, session=session)
    season = current_nfl_season(now)
    schedule = fetch_espn_nfl_schedule(season, session=session)
    todays_schedule = schedule[schedule["start_date"].map(_date_for_kickoff).eq(target.isoformat())] if not schedule.empty else schedule
    odds_games, odds_error = fetch_live_odds(session=session)
    odds_board = parse_odds_board(odds_games)
    rows, warnings = build_today_rows(schedule, odds_board, target, now)
    if odds_error:
        warnings.insert(0, f"WARNING: {odds_error}; model-only rows remain eligible.")
    logging = {"logged": 0, "duplicates": 0} if dry_run else log_rows(rows, worksheet)
    return {
        "date": target.isoformat(),
        "pending_graded": grading["graded"],
        "games_found": len(todays_schedule),
        "games_modelable": len(rows),
        "games_with_odds": sum(bool(row.get("Vegas Pick")) for row in rows),
        "games_without_odds": sum(not bool(row.get("Vegas Pick")) for row in rows),
        "previously_logged": logging["duplicates"],
        "new_logged": logging["logged"],
        "skipped": max(0, len(todays_schedule) - len(rows)),
        "errors": grading["errors"],
        "warnings": warnings,
    }


def print_summary(summary: Mapping[str, Any]) -> None:
    print("NFL DAILY AUTOMATION")
    print(f"Date: {summary['date']}")
    print(f"Pending games graded: {summary['pending_graded']}")
    print(f"Games found today: {summary['games_found']}")
    print(f"Games modelable: {summary['games_modelable']}")
    print(f"Games with Vegas odds: {summary['games_with_odds']}")
    print(f"Games without Vegas odds: {summary['games_without_odds']}")
    print(f"Previously logged: {summary['previously_logged']}")
    print(f"New games logged: {summary['new_logged']}")
    print(f"Skipped/started: {summary['skipped']}")
    print(f"Errors: {len(summary['errors'])}")
    for warning in summary["warnings"]:
        print(warning)
    for error in summary["errors"]:
        print(f"ERROR: {error}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade and log NFL predictions without Streamlit.")
    parser.add_argument("--date", help="Local date override in YYYY-MM-DD format")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and model without Google Sheets writes")
    args = parser.parse_args(argv)
    try:
        summary = run_daily(date_value=args.date, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - catastrophic failures must be loud
        print(f"NFL DAILY AUTOMATION FAILED: {type(exc).__name__}: {exc}")
        return 1
    print_summary(summary)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
