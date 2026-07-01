# daily_mlb_auto.py
#
# Runs Hag Labs MLB automation without opening Streamlit.
#
# What it does:
# 1. Grades completed pending MLB games in Google Sheets.
# 2. Pulls today's MLB schedule.
# 3. Pulls live MLB moneylines from The Odds API when available.
# 4. Simulates every scheduled MLB game.
# 5. Logs every game to "MLB Daily Prediction Model" -> "MLB Log V2".
#
# Run manually:
#     python daily_mlb_auto.py
#
# Windows Task Scheduler:
#     Program/script: python
#     Add arguments: C:\HagLabs\pitching-analytics-engine\daily_mlb_auto.py
#     Start in: C:\HagLabs\pitching-analytics-engine

from __future__ import annotations

import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple

import numpy as np
import requests
import gspread

try:
    from utils import get_local_date_str, clean_name, calculate_implied_prob
except Exception:
    def get_local_date_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def clean_name(name: str) -> str:
        return str(name or "").strip()

    def calculate_implied_prob(odds: int | float | str) -> float:
        odds = int(odds)
        if odds < 0:
            return abs(odds) / (abs(odds) + 100)
        return 100 / (odds + 100)

try:
    from google_sheets import get_google_client
except Exception as exc:
    raise RuntimeError("Could not import google_sheets.py. Run this script from your HagLabs project folder.") from exc

try:
    from config import DEFAULT_SIMULATION_SIZE
except Exception:
    DEFAULT_SIMULATION_SIZE = 10000

try:
    from constants import MLB_PARK_FACTORS
except Exception:
    MLB_PARK_FACTORS = {}

try:
    from mlb_recent_form import calculate_recent_form_adjustment, fetch_recent_mlb_team_form
except Exception:
    def calculate_recent_form_adjustment(base_rs, recent_rs, base_ra, recent_ra):
        return {"offense": base_rs, "defense": base_ra}

    def fetch_recent_mlb_team_form(team_name):
        return None

try:
    from mlb_pitcher_form import blend_pitcher_form, fetch_pitcher_recent_era
except Exception:
    def blend_pitcher_form(base_fip, recent_era):
        return base_fip

    def fetch_pitcher_recent_era(player_id):
        return None


ODDS_API_KEY = os.environ.get("ODDS_API_KEY") or "19d9ef9331ef61b3a2589d81ba676e11"
WORKBOOK_NAME = "MLB Daily Prediction Model"
WORKSHEET_NAME = "MLB Log V2"

PROBABILITY_BOARD_COLUMNS = [
    "Date", "Away Team", "Home Team", "Away ML", "Home ML",
    "Model Away %", "Model Home %", "Vegas Away %",
    "Vegas Home %", "Model Pick", "Vegas Pick",
    "Agreement Type", "Model Edge %", "Confidence", "Status", "Odds Source"
]


SHADOW_WORKSHEET_NAME = "MLB Log V2.1 Shadow"

V21_SHADOW_COLUMNS = [
    "Date", "Away Team", "Home Team", "Away ML", "Home ML",
    "Model Away %", "Model Home %", "Vegas Away %",
    "Vegas Home %", "Raw Hag Pick", "Vegas Pick", "V2.1 Pick",
    "V2.1 Source", "V2.1 Reason", "V2.1 Confidence",
    "Model Edge %", "Agreement Type", "Favorite Status",
    "Model Prob Bucket", "Actual Winner", "Status", "Odds Source"
]


def market_read(edge: float) -> str:
    edge = abs(float(edge))
    if edge >= 0.10:
        return "Strong Model Disagreement"
    if edge >= 0.06:
        return "Model Disagreement"
    if edge >= 0.03:
        return "Slight Model Lean"
    return "Market Agreement"


def board_confidence(model_prob: float, vegas_prob: float) -> str:
    edge = abs(float(model_prob) - float(vegas_prob))
    if edge >= 0.10:
        return "High"
    if edge >= 0.06:
        return "Medium"
    return "Tracking"


def probability_row(date_str, away_t, home_t, away_ml, home_ml, model_away_prob, model_home_prob):
    real_odds_available = away_ml not in [None, "", "N/A"] and home_ml not in [None, "", "N/A"]

    if real_odds_available:
        try:
            vegas_away_prob = calculate_implied_prob(int(away_ml))
            vegas_home_prob = calculate_implied_prob(int(home_ml))
            odds_source = "Live Odds"
        except Exception:
            vegas_away_prob = 0.50
            vegas_home_prob = 0.50
            away_ml = "N/A"
            home_ml = "N/A"
            odds_source = "No Live Odds"
    else:
        vegas_away_prob = 0.50
        vegas_home_prob = 0.50
        away_ml = "N/A"
        home_ml = "N/A"
        odds_source = "No Live Odds"

    model_pick = away_t if model_away_prob >= model_home_prob else home_t
    vegas_pick = away_t if vegas_away_prob >= vegas_home_prob else home_t

    away_edge = model_away_prob - vegas_away_prob
    home_edge = model_home_prob - vegas_home_prob
    main_edge = away_edge if abs(away_edge) >= abs(home_edge) else home_edge

    confidence = board_confidence(
        model_away_prob if model_pick == away_t else model_home_prob,
        vegas_away_prob if model_pick == away_t else vegas_home_prob,
    )

    agreement_type = market_read(main_edge) if odds_source == "Live Odds" else "Model Only"

    return [
        date_str, away_t, home_t, away_ml, home_ml,
        f"{model_away_prob:.1%}", f"{model_home_prob:.1%}",
        f"{vegas_away_prob:.1%}", f"{vegas_home_prob:.1%}",
        model_pick, vegas_pick, agreement_type,
        f"{main_edge:+.1%}", confidence, "PENDING", odds_source
    ]


def get_or_create_log_worksheet():
    gc = get_google_client()
    sh = gc.open(WORKBOOK_NAME)

    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows="3000", cols="20")
        ws.append_row(PROBABILITY_BOARD_COLUMNS)

    values = ws.get_all_values()
    if not values:
        ws.append_row(PROBABILITY_BOARD_COLUMNS)

    return ws


def log_row(row_data: List[Any]) -> str:
    ws = get_or_create_log_worksheet()
    values = ws.get_all_values()

    target_date = row_data[0]
    target_away = row_data[1]
    target_home = row_data[2]

    for row in values[1:]:
        if len(row) >= 3 and row[0] == target_date and row[1] == target_away and row[2] == target_home:
            return "DUPLICATE"

    ws.append_row(row_data)
    return "SUCCESS"


# ==========================================================
# MLB V2.1 SHADOW LOGGING HELPERS
# ==========================================================
def get_or_create_shadow_worksheet():
    gc = get_google_client()
    sh = gc.open(WORKBOOK_NAME)

    try:
        ws = sh.worksheet(SHADOW_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHADOW_WORKSHEET_NAME, rows="3000", cols="30")
        ws.append_row(V21_SHADOW_COLUMNS)

    values = ws.get_all_values()
    if not values:
        ws.append_row(V21_SHADOW_COLUMNS)
    else:
        has_header = (
            len(values[0]) >= 3
            and str(values[0][0]).strip() == "Date"
            and str(values[0][1]).strip() == "Away Team"
            and str(values[0][2]).strip() == "Home Team"
        )
        if not has_header:
            ws.insert_row(V21_SHADOW_COLUMNS, 1)

    return ws


def shadow_pct_to_float(value) -> float:
    try:
        s = str(value).replace("%", "").replace("+", "").strip()
        if s == "" or s.lower() in ["nan", "none", "n/a"]:
            return float("nan")
        return float(s) / 100.0
    except Exception:
        return float("nan")


def shadow_num(value) -> float:
    try:
        s = str(value).replace("%", "").replace("+", "").replace(",", "").strip()
        if s == "" or s.lower() in ["nan", "none", "n/a"]:
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


def shadow_model_prob_bucket(prob: float) -> str:
    try:
        p = float(prob)
    except Exception:
        return "Unknown"

    if np.isnan(p):
        return "Unknown"
    if p < 0.50:
        return "<50%"
    if p < 0.525:
        return "50-52.5%"
    if p < 0.55:
        return "52.5-55%"
    if p < 0.575:
        return "55-57.5%"
    if p < 0.60:
        return "57.5-60%"
    if p < 0.65:
        return "60-65%"
    return "65%+"


def shadow_edge_bucket(edge_pct: float) -> str:
    try:
        e = abs(float(edge_pct))
    except Exception:
        return "Unknown"

    if np.isnan(e):
        return "Unknown"
    if e < 3:
        return "0-3%"
    if e < 6:
        return "3-6%"
    if e < 10:
        return "6-10%"
    if e < 15:
        return "10-15%"
    return "15%+"


def shadow_blend_pick(away_team, home_team, model_away, model_home, vegas_away, vegas_home, hag_weight=0.50) -> str:
    try:
        if any(np.isnan(x) for x in [model_away, model_home, vegas_away, vegas_home]):
            return ""
        blend_away = (model_away * hag_weight) + (vegas_away * (1 - hag_weight))
        blend_home = (model_home * hag_weight) + (vegas_home * (1 - hag_weight))
        return away_team if blend_away >= blend_home else home_team
    except Exception:
        return ""


def build_v21_shadow_row(prob_row: List[Any]) -> List[Any]:
    """
    Takes the standard MLB Log V2 row and builds a V2.1 shadow-log row.

    Standard row:
    Date, Away, Home, Away ML, Home ML,
    Model Away %, Model Home %, Vegas Away %, Vegas Home %,
    Model Pick, Vegas Pick, Agreement Type, Model Edge %, Confidence, Status, Odds Source
    """
    padded = list(prob_row) + [""] * 20

    date_str = str(padded[0]).strip()
    away_team = str(padded[1]).strip()
    home_team = str(padded[2]).strip()
    away_ml = padded[3]
    home_ml = padded[4]
    model_away_s = padded[5]
    model_home_s = padded[6]
    vegas_away_s = padded[7]
    vegas_home_s = padded[8]
    raw_hag_pick = str(padded[9]).strip()
    vegas_pick = str(padded[10]).strip()
    agreement = str(padded[11]).strip() or "Unknown"
    edge_s = padded[12]
    confidence = str(padded[13]).strip() or "Tracking"
    odds_source = str(padded[15]).strip() or "Unknown"

    model_away = shadow_pct_to_float(model_away_s)
    model_home = shadow_pct_to_float(model_home_s)
    vegas_away = shadow_pct_to_float(vegas_away_s)
    vegas_home = shadow_pct_to_float(vegas_home_s)
    edge_pct = shadow_num(edge_s)

    if raw_hag_pick == away_team:
        model_pick_prob = model_away
        model_pick_ml = shadow_num(away_ml)
    elif raw_hag_pick == home_team:
        model_pick_prob = model_home
        model_pick_ml = shadow_num(home_ml)
    else:
        model_pick_prob = float("nan")
        model_pick_ml = float("nan")

    if np.isnan(model_pick_ml):
        favorite_status = "Unknown"
    else:
        favorite_status = "Favorite" if model_pick_ml < 0 else "Underdog"

    prob_bucket = shadow_model_prob_bucket(model_pick_prob)
    edge_bucket = shadow_edge_bucket(edge_pct)
    hybrid_50 = shadow_blend_pick(away_team, home_team, model_away, model_home, vegas_away, vegas_home, hag_weight=0.50)

    v21_pick = raw_hag_pick
    v21_source = "Hag Labs"
    v21_confidence = confidence
    v21_reason = "Default conservative rule: keep raw Hag Labs pick."

    # No usable Vegas side: keep raw model.
    if not vegas_pick or vegas_pick.lower() in ["nan", "none", "n/a"]:
        v21_pick = raw_hag_pick
        v21_source = "Hag Labs"
        v21_confidence = confidence
        v21_reason = "No usable Vegas pick found; keep raw model."

    # Tracking rows stay research-only in the shadow log.
    elif confidence == "Tracking":
        v21_pick = raw_hag_pick
        v21_source = "Tracking Protected Hag"
        v21_confidence = "Tracking"
        v21_reason = "Tracking row; kept as research-only shadow pick."

    # No reason to flip consensus.
    elif vegas_pick == raw_hag_pick:
        v21_pick = raw_hag_pick
        v21_source = "Consensus"
        v21_confidence = "High" if confidence == "High" else "Medium"
        v21_reason = "Hag Labs and Vegas agree."

    # High Confidence gets protected.
    elif confidence == "High":
        v21_pick = raw_hag_pick
        v21_source = "High Confidence Protected Hag"
        v21_confidence = "High"
        v21_reason = "High Confidence raw Hag pick protected in V2.1."

    # Low Confidence disagreement uses Vegas.
    elif confidence == "Low":
        v21_pick = vegas_pick
        v21_source = "Low Confidence Vegas Guardrail"
        v21_confidence = "Guardrail"
        v21_reason = "Low Confidence disagreement uses Vegas guardrail."

    # Medium: only flip the known weak pocket.
    elif confidence == "Medium":
        if favorite_status == "Favorite":
            v21_pick = raw_hag_pick
            v21_source = "Medium Favorite Protected Hag"
            v21_confidence = "Medium"
            v21_reason = "Medium Confidence favorite protected."
        elif edge_bucket == "6-10%":
            v21_pick = raw_hag_pick
            v21_source = "Medium 6-10 Edge Protected Hag"
            v21_confidence = "Medium"
            v21_reason = "Medium Confidence with 6-10% edge is a protected strong pocket."
        elif "Strong Model Disagreement" in agreement and favorite_status == "Underdog":
            v21_pick = vegas_pick
            v21_source = "Medium Underdog Vegas Guardrail"
            v21_confidence = "Guardrail"
            v21_reason = "Medium underdog + strong model disagreement gets Vegas guardrail."
        elif prob_bucket in ["57.5-60%", "60-65%"] and hybrid_50 and hybrid_50 != raw_hag_pick and favorite_status == "Underdog":
            v21_pick = vegas_pick
            v21_source = "Medium Calibration Guardrail"
            v21_confidence = "Guardrail"
            v21_reason = f"Medium underdog in calibration-watch bucket {prob_bucket}; 50/50 hybrid disagrees."
        else:
            v21_pick = raw_hag_pick
            v21_source = "Medium Protected Hag"
            v21_confidence = "Medium"
            v21_reason = "Medium Confidence kept unless a strict V2.1 guardrail triggers."

    return [
        date_str, away_team, home_team, away_ml, home_ml,
        model_away_s, model_home_s, vegas_away_s, vegas_home_s,
        raw_hag_pick, vegas_pick, v21_pick,
        v21_source, v21_reason, v21_confidence,
        edge_s, agreement, favorite_status, prob_bucket,
        "", "PENDING", odds_source
    ]


def log_shadow_row(row_data: List[Any]) -> str:
    ws = get_or_create_shadow_worksheet()
    values = ws.get_all_values()

    target_date = row_data[0]
    target_away = row_data[1]
    target_home = row_data[2]

    for row in values[1:]:
        if len(row) >= 3 and row[0] == target_date and row[1] == target_away and row[2] == target_home:
            return "DUPLICATE"

    ws.append_row(row_data)
    return "SUCCESS"



def fetch_mlb_team_and_pitcher_data(season: int | None = None):
    if season is None:
        season = datetime.now().year

    team_data = {}
    pitcher_data = {}

    try:
        standings_url = f"https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season={season}"
        s_resp = requests.get(standings_url, timeout=15).json()
        for record in s_resp.get("records", []):
            for t in record.get("teamRecords", []):
                t_name = t["team"]["name"]
                g = t.get("gamesPlayed", 1) or 1
                team_data[t_name] = {
                    "RS_per_G": float(t.get("runsScored", 0)) / g,
                    "RA_per_G": float(t.get("runsAllowed", 0)) / g,
                }
    except Exception:
        pass

    try:
        p_url = f"https://statsapi.mlb.com/api/v1/stats?stats=season&group=pitching&playerPool=ALL&season={season}&limit=2000"
        p_resp = requests.get(p_url, timeout=20).json()
        for p in p_resp.get("stats", [{}])[0].get("splits", []):
            p_name = clean_name(p["player"]["fullName"])
            t_name = p.get("team", {}).get("name", "Free Agent")
            s = p.get("stat", {})
            ip = float(s.get("inningsPitched", 0.0) or 0.0)

            if ip > 0:
                hr = float(s.get("homeRuns", 0) or 0)
                bb = float(s.get("baseOnBalls", 0) or 0)
                hbp = float(s.get("hitByPitch", 0) or 0)
                k = float(s.get("strikeOuts", 0) or 0)
                raw_fip = ((13 * hr) + (3 * (bb + hbp)) - (2 * k)) / ip + 3.15
                fip = (raw_fip * (ip / 20)) + (4.20 * ((20 - ip) / 20)) if ip < 20 else raw_fip
            else:
                fip = 4.20

            pitcher_data[p_name] = {
                "ID": p["player"].get("id"),
                "FIP": fip,
                "Team": t_name,
                "IP": ip,
                "K": float(s.get("strikeOuts", 0) or 0),
                "BB": float(s.get("baseOnBalls", 0) or 0),
            }
    except Exception:
        pass

    return team_data, pitcher_data


def fetch_today_schedule(date_str: str):
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=probablePitcher"
    data = requests.get(url, timeout=15).json()

    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_team = game["teams"]["away"]["team"]["name"]
            home_team = game["teams"]["home"]["team"]["name"]
            away_sp = clean_name(game["teams"]["away"].get("probablePitcher", {}).get("fullName", "Unknown"))
            home_sp = clean_name(game["teams"]["home"].get("probablePitcher", {}).get("fullName", "Unknown"))

            games.append({
                "game_key": f"{away_team} @ {home_team}",
                "away_team": away_team,
                "home_team": home_team,
                "away_sp": away_sp,
                "home_sp": home_sp,
            })

    return games


def fetch_live_odds():
    if not ODDS_API_KEY:
        return {}, {"status": "NO_KEY", "message": "No ODDS_API_KEY available."}

    url = (
        "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
        f"?apiKey={ODDS_API_KEY}&regions=us&markets=h2h&oddsFormat=american&bookmakers=draftkings,fanduel"
    )

    try:
        response = requests.get(url, timeout=20)

        if response.status_code != 200:
            return {}, {"status": response.status_code, "message": response.text[:500]}

        data = response.json()
        odds_dict = {}

        for game in data:
            bookmakers = game.get("bookmakers", [])
            if not bookmakers:
                continue

            markets = bookmakers[0].get("markets", [])
            if not markets:
                continue

            outcomes = markets[0].get("outcomes", [])
            away = game.get("away_team")
            home = game.get("home_team")

            away_ml = next((o.get("price") for o in outcomes if o.get("name") == away), None)
            home_ml = next((o.get("price") for o in outcomes if o.get("name") == home), None)

            if away and home and away_ml is not None and home_ml is not None:
                odds_dict[f"{away} @ {home}"] = [away_ml, home_ml]

        return odds_dict, {"status": 200, "message": f"Loaded {len(odds_dict)} MLB games from Odds API."}

    except Exception as exc:
        return {}, {"status": "ERROR", "message": str(exc)}


def pitcher_edge_score(pitcher_stats, pitcher_name):
    p = pitcher_stats.get(pitcher_name, {})
    ip = float(p.get("IP", 0) or 0)
    k = float(p.get("K", 0) or 0)
    bb = float(p.get("BB", 0) or 0)
    fip = float(p.get("FIP", 4.20) or 4.20)

    if ip <= 0:
        return 0.0

    k9 = (k / ip) * 9
    bb9 = (bb / ip) * 9

    strikeout_score = min(1.0, k9 / 12)
    control_score = max(0.0, 1 - (bb9 / 5))
    run_prev_score = max(0.0, 1 - (fip / 6))
    workload_score = min(1.0, ip / 180)

    return (strikeout_score * 0.40) + (run_prev_score * 0.30) + (control_score * 0.20) + (workload_score * 0.10)


def team_k_tendency(team_name: str) -> float:
    high_k = {"Colorado Rockies", "Pittsburgh Pirates", "Chicago White Sox", "Miami Marlins", "Oakland Athletics"}
    low_k = {"Houston Astros", "San Diego Padres", "Cleveland Guardians", "Arizona Diamondbacks"}
    if team_name in high_k:
        return 1.08
    if team_name in low_k:
        return 0.94
    return 1.00


def bullpen_fatigue(team_name: str) -> float:
    tired = {"Colorado Rockies", "Chicago White Sox", "Miami Marlins", "Oakland Athletics", "Washington Nationals"}
    rested = {"Los Angeles Dodgers", "Atlanta Braves", "New York Yankees", "Philadelphia Phillies", "Houston Astros"}
    if team_name in tired:
        return 1.06
    if team_name in rested:
        return 0.96
    return 1.00


def simulate_game(away_t, home_t, away_sp, home_sp, team_stats, pitcher_stats):
    a_rs_g = float(team_stats.get(away_t, {}).get("RS_per_G", 4.5))
    h_rs_g = float(team_stats.get(home_t, {}).get("RS_per_G", 4.5))
    a_ra_g = float(team_stats.get(away_t, {}).get("RA_per_G", 4.5))
    h_ra_g = float(team_stats.get(home_t, {}).get("RA_per_G", 4.5))

    a_sp_fip = float(pitcher_stats.get(away_sp, {}).get("FIP", a_ra_g))
    h_sp_fip = float(pitcher_stats.get(home_sp, {}).get("FIP", h_ra_g))

    away_pitcher_id = pitcher_stats.get(away_sp, {}).get("ID")
    home_pitcher_id = pitcher_stats.get(home_sp, {}).get("ID")

    a_recent_era = fetch_pitcher_recent_era(away_pitcher_id) or a_sp_fip
    h_recent_era = fetch_pitcher_recent_era(home_pitcher_id) or h_sp_fip

    a_sp_fip = blend_pitcher_form(a_sp_fip, a_recent_era)
    h_sp_fip = blend_pitcher_form(h_sp_fip, h_recent_era)

    away_sp_score = pitcher_edge_score(pitcher_stats, away_sp)
    home_sp_score = pitcher_edge_score(pitcher_stats, home_sp)
    sp_edge_adjustment = (away_sp_score - home_sp_score) * 0.25

    sp_edge_adjustment = sp_edge_adjustment * team_k_tendency(home_t)
    sp_edge_adjustment = sp_edge_adjustment / team_k_tendency(away_t)

    p_factor = MLB_PARK_FACTORS.get(home_t, 100) / 100 if MLB_PARK_FACTORS else 1.0

    away_recent_raw = fetch_recent_mlb_team_form(away_t) or {"recent_rs_per_g": a_rs_g, "recent_ra_per_g": a_ra_g, "recent_games": 0}
    home_recent_raw = fetch_recent_mlb_team_form(home_t) or {"recent_rs_per_g": h_rs_g, "recent_ra_per_g": h_ra_g, "recent_games": 0}

    away_recent_form = calculate_recent_form_adjustment(a_rs_g, away_recent_raw["recent_rs_per_g"], a_ra_g, away_recent_raw["recent_ra_per_g"])
    home_recent_form = calculate_recent_form_adjustment(h_rs_g, home_recent_raw["recent_rs_per_g"], h_ra_g, home_recent_raw["recent_ra_per_g"])

    away_lam = (((away_recent_form["offense"] + home_recent_form["defense"]) / 2) * p_factor * bullpen_fatigue(home_t)) * (1 + sp_edge_adjustment)
    home_lam = (((home_recent_form["offense"] + away_recent_form["defense"]) / 2) * p_factor * bullpen_fatigue(away_t)) * (1 - sp_edge_adjustment)

    away_lam = max(0.75, min(10.0, away_lam))
    home_lam = max(0.75, min(10.0, home_lam))

    sim_a = np.random.poisson(away_lam, DEFAULT_SIMULATION_SIZE)
    sim_h = np.random.poisson(home_lam, DEFAULT_SIMULATION_SIZE)

    a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
    h_wins = DEFAULT_SIMULATION_SIZE - a_wins

    return float(a_wins / DEFAULT_SIMULATION_SIZE), float(h_wins / DEFAULT_SIMULATION_SIZE)


def log_today_board(date_str: str | None = None):
    date_str = date_str or get_local_date_str()
    team_stats, pitcher_stats = fetch_mlb_team_and_pitcher_data()
    schedule_games = fetch_today_schedule(date_str)
    live_odds, odds_debug = fetch_live_odds()

    processed = logged = duplicates = errors = 0
    shadow_logged = shadow_duplicates = shadow_errors = 0

    for game in schedule_games:
        try:
            game_key = game["game_key"]
            away_t = game["away_team"]
            home_t = game["home_team"]
            away_sp = game.get("away_sp", "Unknown")
            home_sp = game.get("home_sp", "Unknown")
            odds = live_odds.get(game_key, ["N/A", "N/A"])

            model_away_prob, model_home_prob = simulate_game(away_t, home_t, away_sp, home_sp, team_stats, pitcher_stats)
            row = probability_row(date_str, away_t, home_t, odds[0], odds[1], model_away_prob, model_home_prob)

            status = log_row(row)
            processed += 1

            if status == "SUCCESS":
                logged += 1
            elif status == "DUPLICATE":
                duplicates += 1

            try:
                shadow_row = build_v21_shadow_row(row)
                shadow_status = log_shadow_row(shadow_row)
                if shadow_status == "SUCCESS":
                    shadow_logged += 1
                elif shadow_status == "DUPLICATE":
                    shadow_duplicates += 1
            except Exception:
                shadow_errors += 1
                traceback.print_exc()

        except Exception:
            errors += 1
            traceback.print_exc()

    return {
        "date": date_str,
        "scheduled_games": len(schedule_games),
        "odds_games": len(live_odds),
        "odds_debug": odds_debug,
        "processed": processed,
        "logged": logged,
        "duplicates": duplicates,
        "errors": errors,
        "shadow_logged": shadow_logged,
        "shadow_duplicates": shadow_duplicates,
        "shadow_errors": shadow_errors,
    }


def grade_pending_games():
    ws = get_or_create_log_worksheet()
    data = ws.get_all_values()

    pending_rows = []
    for i, row in enumerate(data):
        if i == 0:
            continue
        if len(row) >= 16 and row[14].strip().upper() == "PENDING":
            pending_rows.append((i, row, 16))
        elif len(row) >= 15 and row[14].strip().upper() == "PENDING":
            pending_rows.append((i, row, 15))
        elif len(row) >= 10 and row[9].strip().upper() == "PENDING":
            pending_rows.append((i, row, 10))

    if not pending_rows:
        return {"pending": 0, "graded": 0, "message": "No pending games."}

    pending_dates = sorted(set(row[0] for _, row, _ in pending_rows))
    winners = {}

    for d_str in pending_dates:
        try:
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d_str}"
            resp = requests.get(url, timeout=15).json()
            for date_block in resp.get("dates", []):
                for g in date_block.get("games", []):
                    if g["status"]["abstractGameState"] == "Final":
                        away = g["teams"]["away"]["team"]["name"]
                        home = g["teams"]["home"]["team"]["name"]
                        winner = away if g["teams"]["away"].get("score", 0) > g["teams"]["home"].get("score", 0) else home
                        winners[f"{d_str}_{away}_{home}"] = winner
        except Exception:
            traceback.print_exc()

    graded = 0

    for i, row, schema_len in pending_rows:
        d_str, away_t, home_t = row[0], row[1], row[2]
        actual_winner = None

        for key, winner in winners.items():
            if key.startswith(f"{d_str}_") and away_t in key and home_t in key:
                actual_winner = winner
                break

        if not actual_winner:
            continue

        model_pick = row[9] if schema_len >= 15 else row[7]
        status_col = 15 if schema_len >= 15 else 10
        new_status = "WIN" if model_pick == actual_winner else "LOSS"
        ws.update_cell(i + 1, status_col, new_status)
        graded += 1

    return {"pending": len(pending_rows), "graded": graded, "message": f"Graded {graded} games."}


def grade_pending_shadow_games():
    ws = get_or_create_shadow_worksheet()
    data = ws.get_all_values()

    pending_rows = []
    for i, row in enumerate(data):
        if i == 0:
            continue
        padded = list(row) + [""] * 25
        if padded[20].strip().upper() == "PENDING":
            pending_rows.append((i, padded))

    if not pending_rows:
        return {"pending": 0, "graded": 0, "message": "No pending V2.1 shadow games."}

    pending_dates = sorted(set(row[0] for _, row in pending_rows))
    winners = {}

    for d_str in pending_dates:
        try:
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d_str}"
            resp = requests.get(url, timeout=15).json()
            for date_block in resp.get("dates", []):
                for g in date_block.get("games", []):
                    if g["status"]["abstractGameState"] == "Final":
                        away = g["teams"]["away"]["team"]["name"]
                        home = g["teams"]["home"]["team"]["name"]
                        winner = away if g["teams"]["away"].get("score", 0) > g["teams"]["home"].get("score", 0) else home
                        winners[f"{d_str}_{away}_{home}"] = winner
        except Exception:
            traceback.print_exc()

    graded = 0

    for i, row in pending_rows:
        d_str, away_t, home_t = row[0], row[1], row[2]
        actual_winner = None

        for key, winner in winners.items():
            if key.startswith(f"{d_str}_") and away_t in key and home_t in key:
                actual_winner = winner
                break

        if not actual_winner:
            continue

        v21_pick = row[11]
        new_status = "WIN" if v21_pick == actual_winner else "LOSS"

        # Actual Winner column = 20th column, Status column = 21st column.
        ws.update_cell(i + 1, 20, actual_winner)
        ws.update_cell(i + 1, 21, new_status)
        graded += 1

    return {"pending": len(pending_rows), "graded": graded, "message": f"Graded {graded} V2.1 shadow games."}



def main():
    print("=== Hag Labs Daily MLB Automation ===")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    grade_result = grade_pending_games()
    print(f"Grade pending: {grade_result}")

    shadow_grade_result = grade_pending_shadow_games()
    print(f"Grade V2.1 shadow pending: {shadow_grade_result}")

    log_result = log_today_board()
    print(
        "Log today: "
        f"scheduled={log_result['scheduled_games']} "
        f"odds={log_result['odds_games']} "
        f"processed={log_result['processed']} "
        f"logged={log_result['logged']} "
        f"duplicates={log_result['duplicates']} "
        f"errors={log_result['errors']} "
        f"shadow_logged={log_result['shadow_logged']} "
        f"shadow_duplicates={log_result['shadow_duplicates']} "
        f"shadow_errors={log_result['shadow_errors']}"
    )
    print(f"Odds status: {log_result['odds_debug']}")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
