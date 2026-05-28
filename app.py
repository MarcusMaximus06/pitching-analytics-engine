import plotly.graph_objects as go
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback
import requests
from curl_cffi import requests as cffi_requests
import gspread
import os
import plotly.graph_objects as go
import nfl_data_py as nfl
import re
import requests
from utils import get_local_date_str, clean_name, calculate_implied_prob, get_confidence_tier
from google_sheets import get_google_client, get_google_worksheet
from config import APP_TITLE, APP_PAGE_TITLE, CACHE_TTL_SHORT, CACHE_TTL_ODDS, CACHE_TTL_STATS, CACHE_TTL_DAILY, DEFAULT_SIMULATION_SIZE, MIN_ACTIONABLE_EDGE
from constants import MLB_PARK_FACTORS
from mlb_recent_form import calculate_recent_form_adjustment, fetch_recent_mlb_team_form
from mlb_pitcher_form import blend_pitcher_form, fetch_pitcher_recent_era
from pybaseball import statcast_pitcher, statcast_batter

TEAM_LOGOS = {
    "Arizona Diamondbacks": "https://www.mlbstatic.com/team-logos/109.svg",
    "Atlanta Braves": "https://www.mlbstatic.com/team-logos/144.svg",
    "Baltimore Orioles": "https://www.mlbstatic.com/team-logos/110.svg",
    "Boston Red Sox": "https://www.mlbstatic.com/team-logos/111.svg",
    "Chicago Cubs": "https://www.mlbstatic.com/team-logos/112.svg",
    "Chicago White Sox": "https://www.mlbstatic.com/team-logos/145.svg",
    "Cincinnati Reds": "https://www.mlbstatic.com/team-logos/113.svg",
    "Cleveland Guardians": "https://www.mlbstatic.com/team-logos/114.svg",
    "Colorado Rockies": "https://www.mlbstatic.com/team-logos/115.svg",
    "Detroit Tigers": "https://www.mlbstatic.com/team-logos/116.svg",
    "Houston Astros": "https://www.mlbstatic.com/team-logos/117.svg",
    "Kansas City Royals": "https://www.mlbstatic.com/team-logos/118.svg",
    "Los Angeles Angels": "https://www.mlbstatic.com/team-logos/108.svg",
    "Los Angeles Dodgers": "https://www.mlbstatic.com/team-logos/119.svg",
    "Miami Marlins": "https://www.mlbstatic.com/team-logos/146.svg",
    "Milwaukee Brewers": "https://www.mlbstatic.com/team-logos/158.svg",
    "Minnesota Twins": "https://www.mlbstatic.com/team-logos/142.svg",
    "New York Mets": "https://www.mlbstatic.com/team-logos/121.svg",
    "New York Yankees": "https://www.mlbstatic.com/team-logos/147.svg",
    "Oakland Athletics": "https://www.mlbstatic.com/team-logos/133.svg",
    "Philadelphia Phillies": "https://www.mlbstatic.com/team-logos/143.svg",
    "Pittsburgh Pirates": "https://www.mlbstatic.com/team-logos/134.svg",
    "San Diego Padres": "https://www.mlbstatic.com/team-logos/135.svg",
    "San Francisco Giants": "https://www.mlbstatic.com/team-logos/137.svg",
    "Seattle Mariners": "https://www.mlbstatic.com/team-logos/136.svg",
    "St. Louis Cardinals": "https://www.mlbstatic.com/team-logos/138.svg",
    "Tampa Bay Rays": "https://www.mlbstatic.com/team-logos/139.svg",
    "Texas Rangers": "https://www.mlbstatic.com/team-logos/140.svg",
    "Toronto Blue Jays": "https://www.mlbstatic.com/team-logos/141.svg",
    "Washington Nationals": "https://www.mlbstatic.com/team-logos/120.svg"
}

PITCH_COLORS = {
    "Fastball": "#d73027",
    "4-Seam": "#d73027",
    "Sinker": "#fc8d59",
    "Cutter": "#fdae61",
    "Slider": "#fee08b",
    "Curveball": "#66bd63",
    "Changeup": "#1a9850",
    "Splitter": "#3288bd",
    "Knuckleball": "#5e4fa2"
}

PITCH_ARSENALS = {
    "Paul Skenes": [
        {"pitch": "4-Seam", "usage": 39, "velo": 98.1},
        {"pitch": "Splitter", "usage": 24, "velo": 91.4},
        {"pitch": "Slider", "usage": 22, "velo": 87.3},
        {"pitch": "Curveball", "usage": 15, "velo": 82.0},
    ],

    "Chris Sale": [
        {"pitch": "4-Seam", "usage": 41, "velo": 94.8},
        {"pitch": "Slider", "usage": 36, "velo": 79.5},
        {"pitch": "Changeup", "usage": 17, "velo": 85.1},
        {"pitch": "Sinker", "usage": 6, "velo": 93.2},
    ],

    "Shota Imanaga": [
        {"pitch": "4-Seam", "usage": 48, "velo": 92.7},
        {"pitch": "Splitter", "usage": 28, "velo": 84.3},
        {"pitch": "Slider", "usage": 15, "velo": 81.4},
        {"pitch": "Curveball", "usage": 9, "velo": 76.2},
    ],
}

# --- CLOUDFLARE BYPASS V9: THE SMART TLS SPOOFER ---
original_get = requests.get
def custom_get(url, **kwargs):
    if "googleapis.com" in str(url) or "googleusercontent.com" in str(url):
        return original_get(url, **kwargs)
    try:
        return cffi_requests.get(url, impersonate="chrome120", **kwargs)
    except Exception:
        return original_get(url, **kwargs)
requests.get = custom_get

original_post = requests.post
def custom_post(url, **kwargs):
    if "googleapis.com" in str(url) or "googleusercontent.com" in str(url):
        return original_post(url, **kwargs)
    try:
        return cffi_requests.post(url, impersonate="chrome120", **kwargs)
    except Exception:
        return original_post(url, **kwargs)
requests.post = custom_post

original_request = requests.Session.request
def custom_request(self, method, url, **kwargs):
    if "googleapis.com" in str(url) or "googleusercontent.com" in str(url):
        return original_request(self, method, url, **kwargs)
    try:
        return cffi_requests.request(method, url, impersonate="chrome120", **kwargs)
    except Exception:
        return original_request(self, method, url, **kwargs)
requests.Session.request = custom_request
# ---------------------------------------

st.set_page_config(page_title=APP_PAGE_TITLE, layout="wide")


# ==========================================================
# NFL UI / ANALYTICS HELPERS
# ==========================================================
NFL_FANTASY_RELEVANT_NAMES = {
    "Josh Allen","Lamar Jackson","Patrick Mahomes","Jalen Hurts","Joe Burrow","C.J. Stroud",
    "Dak Prescott","Anthony Richardson","Justin Herbert","Brock Purdy","Jordan Love","Kyler Murray",
    "Caleb Williams","Jayden Daniels","Drake Maye","Bo Nix","Trevor Lawrence","Tua Tagovailoa",
    "Bijan Robinson","Christian McCaffrey","Saquon Barkley","Breece Hall","Jahmyr Gibbs","Jonathan Taylor",
    "De'Von Achane","Derrick Henry","Kyren Williams","Josh Jacobs","Kenneth Walker","James Cook",
    "Alvin Kamara","Bucky Irving","Ashton Jeanty","Omarion Hampton","Cam Skattebo","RJ Harvey",
    "Justin Jefferson","Ja'Marr Chase","CeeDee Lamb","Amon-Ra St. Brown","Puka Nacua","Malik Nabers",
    "Nico Collins","A.J. Brown","Garrett Wilson","Brian Thomas","Drake London","Marvin Harrison",
    "Mike Evans","Davante Adams","Tee Higgins","Rashee Rice","Ladd McConkey","Xavier Worthy",
    "Brock Bowers","Trey McBride","George Kittle","Sam LaPorta","Travis Kelce","Mark Andrews",
    "T.J. Hockenson","David Njoku","Evan Engram","Dalton Kincaid","Taysom Hill",
    "Brandon Aubrey","Justin Tucker","Jake Elliott","Harrison Butker","Cameron Dicker","Ka'imi Fairbairn",
    "Eddy Pineiro","Younghoe Koo","Tyler Bass","Jake Moody"
}

def nfl_clean_display_df(df, max_rows=80):
    if df is None or not isinstance(df, pd.DataFrame):
        return df

    out = df.copy()

    for bad_col in ["Headshot URL", "headshot_url", "HeadshotURL"]:
        if bad_col in out.columns:
            out = out.drop(columns=[bad_col])

    if "Team" in out.columns:
        out["Team"] = out["Team"].fillna("FA").replace({None: "FA", "None": "FA", "nan": "FA"})

    if "Player" in out.columns and "Position" in out.columns:
        relevant_positions = ["QB", "RB", "WR", "TE", "K"]
        out = out[out["Position"].isin(relevant_positions)]

        if "Status" in out.columns:
            out = out[out["Status"].fillna("Active").astype(str).isin(["Active", "Questionable", "Doubtful", "Out", "IR", "PUP"])]

        # Keep obvious fantasy players, top values, and all rostered/synced guys.
        if "Trade Value" in out.columns:
            out = out.sort_values("Trade Value", ascending=False)
        elif "Dynasty Value" in out.columns:
            out = out.sort_values("Dynasty Value", ascending=False)
        elif "Redraft Value" in out.columns:
            out = out.sort_values("Redraft Value", ascending=False)

        out = out.head(max_rows)

    return out


def nfl_percentile_bar(label, value, max_value=100):
    pct = int(max(1, min(99, (float(value) / max_value) * 100 if max_value else 1)))

    if pct >= 85:
        color = "#22c55e"
    elif pct >= 70:
        color = "#84cc16"
    elif pct >= 50:
        color = "#facc15"
    else:
        color = "#ef4444"

    st.markdown(f"**{label}: {pct}th percentile**")
    st.markdown(
        f"""
        <div style="background-color:#1f2937;border-radius:8px;height:14px;width:100%;margin-bottom:14px;">
            <div style="background-color:{color};width:{pct}%;height:14px;border-radius:8px;"></div>
        </div>
        """,
        unsafe_allow_html=True
    )


def render_nfl_player_card(player, info):
    name = player or "Unknown"
    info = info or {}

    player_id = str(info.get("player_id") or info.get("Player ID") or info.get("id") or "")
    headshot = info.get("headshot_url") or info.get("Headshot URL") or (f"https://sleepercdn.com/content/nfl/players/{player_id}.jpg" if player_id else "")
    team = info.get("team") or info.get("Team") or "FA"
    pos = info.get("position") or info.get("Position") or "N/A"
    age = info.get("age") or info.get("Age") or "N/A"
    value = info.get("Trade Value") or info.get("trade_value") or info.get("Dynasty Value") or info.get("Redraft Value") or "N/A"
    status = info.get("status") or info.get("Status") or "Active"

    c1, c2 = st.columns([0.45, 2.2])
    with c1:
        if headshot:
            st.image(headshot, width=72)
    with c2:
        st.markdown(f"**{name}**")
        st.caption(f"{team} • {pos} • Age {age} • {status}")
        st.caption(f"Value: {value}")


def build_exact_trade_suggestions(power_df, player_df):
    if power_df is None or player_df is None or power_df.empty or player_df.empty:
        return pd.DataFrame()

    pdf = nfl_clean_display_df(player_df, max_rows=300)
    rows = []

    if "Team/User" not in power_df.columns or "Weakest Position" not in power_df.columns:
        return pd.DataFrame()

    for _, team_row in power_df.iterrows():
        need_team = team_row.get("Team/User")
        need_pos = team_row.get("Weakest Position")

        targets = pdf[pdf.get("Position", pd.Series(dtype=str)).eq(need_pos)].head(8)

        for _, target in targets.iterrows():
            target_name = target.get("Player")
            target_value = float(target.get("Trade Value", target.get("Dynasty Value", target.get("Redraft Value", 0))) or 0)

            offer_pool = pdf[
                (pdf.get("Position", pd.Series(dtype=str)) != need_pos)
                & (pdf.get("Trade Value", pdf.get("Dynasty Value", pdf.get("Redraft Value", 0))).astype(float).between(max(0, target_value - 3), target_value + 3))
            ].head(5)

            for _, offer in offer_pool.iterrows():
                offer_name = offer.get("Player")
                offer_value = float(offer.get("Trade Value", offer.get("Dynasty Value", offer.get("Redraft Value", 0))) or 0)
                fairness = max(0, 100 - abs(target_value - offer_value) * 10)

                rows.append({
                    "Team Needing Help": need_team,
                    "Need": need_pos,
                    "Offer": offer_name,
                    "Target": target_name,
                    "Offer Value": round(offer_value, 1),
                    "Target Value": round(target_value, 1),
                    "Fairness Score": round(fairness, 0),
                    "Impact": f"Adds {need_pos} help while keeping value within {abs(target_value - offer_value):.1f} pts."
                })

    return pd.DataFrame(rows).drop_duplicates().head(25)

# ==========================================================
# MASTER SPORT ROUTER
# ==========================================================
st.sidebar.title(APP_TITLE)
sport = st.sidebar.selectbox(
    "Select Sport Engine:",
    [
        "🏠 Home",
        "⚾ MLB Baseball",
        "🏈 NFL Football",
        "🎓 NCAA Football",
        "🥎 NCAA Softball"
    ]
)
st.sidebar.markdown("---")

if sport == "🏠 Home":
    st.title("🏠 Hag Labs Sports Analytics Hub")
    st.markdown("### Pick a sport engine from the sidebar.")

    st.markdown("""
    #### Active Engines
    - ⚾ MLB Baseball: betting model, player lab, fantasy projections
    - 🏈 NFL Football: simulation engine and fantasy projections in progress

    #### Coming Soon
    - 🎓 NCAA Football
    - 🥎 NCAA Softball
    """)

    st.stop()

# ==========================================================
# SPORT BRANCH 1: MLB BASEBALL
# ==========================================================
if sport == "⚾ MLB Baseball":
    page = st.sidebar.radio(
    "Select Engine:",
    [
        "🎲 Monte Carlo Simulation Engine",
        "🔎 MLB Player Lab",
        "🏆 Fantasy Sports Predictor"
    ]
)
    st.sidebar.markdown("---")

    PARK_FACTORS = MLB_PARK_FACTORS

    @st.cache_data(ttl=CACHE_TTL_ODDS)
    def get_live_odds():
        api_key = os.environ.get('ODDS_API_KEY')
        if not api_key: return {}
        url = f'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=american&bookmakers=draftkings,fanduel'
        try:
            response = requests.get(url, timeout=15).json()
            odds_dict = {}
            for game in response:
                if 'bookmakers' in game and len(game['bookmakers']) > 0:
                    outcomes = game['bookmakers'][0]['markets'][0]['outcomes']
                    away = game['away_team']
                    home = game['home_team']
                    away_ml = next((o['price'] for o in outcomes if o['name'] == away), 100)
                    home_ml = next((o['price'] for o in outcomes if o['name'] == home), -110)
                    odds_dict[f"{away} @ {home}"] = [away_ml, home_ml]
            return odds_dict
        except Exception: return {}

    @st.cache_data(ttl=CACHE_TTL_STATS)
    def fetch_mlb_api_data():
        team_data = {}
        pitcher_data = {}
        hitter_data = {}
        
        try:
            standings_url = "https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026"
            s_resp = requests.get(standings_url, timeout=10).json()
            for record in s_resp.get('records', []):
                for t in record.get('teamRecords', []):
                    t_name = t['team']['name']
                    g = t.get('gamesPlayed', 1) or 1
                    rs_g = t.get('runsScored', 0) / g
                    ra_g = t.get('runsAllowed', 0) / g
                    team_data[t_name] = {'RS_per_G': rs_g, 'RA_per_G': ra_g}

            p_url = "https://statsapi.mlb.com/api/v1/stats?stats=season&group=pitching&playerPool=ALL&season=2026&limit=1500"
            p_resp = requests.get(p_url, timeout=15).json()
            for p in p_resp.get('stats', [{}])[0].get('splits', []):
                p_name = clean_name(p['player']['fullName'])
                t_name = p.get('team', {}).get('name', 'Free Agent')
                s = p['stat']
                
                ip = float(s.get('inningsPitched', 0.0))
                if ip > 0:
                    hr = s.get('homeRuns', 0)
                    bb = s.get('baseOnBalls', 0)
                    hbp = s.get('hitByPitch', 0)
                    k = s.get('strikeOuts', 0)
                    
                    raw_fip = ((13 * hr) + (3 * (bb + hbp)) - (2 * k)) / ip + 3.15
                    if ip < 20: 
                        fip = (raw_fip * (ip/20)) + (4.20 * ((20-ip)/20))
                    else:
                        fip = raw_fip
                else:
                    fip = 4.20 
                
                pitcher_data[p_name] = {
                    'ID': p['player'].get('id'),
                    'Throw Side': p.get('player', {}).get('pitchHand', {}).get('code', 'U'),
                    'FIP': fip,
                    'Team': t_name,
                    'IP': ip,
                    'K': s.get('strikeOuts', 0),
                    'W': s.get('wins', 0),
                    'SV': s.get('saves', 0),
                    'L': s.get('losses', 0),
                    'ER': s.get('earnedRuns', 0),
                    'H': s.get('hits', 0),
                    'BB': s.get('baseOnBalls', 0),
                    'G': s.get('gamesPlayed', 1) or 1
                }

            position_lookup = {}

            try:
                pos_url = "https://statsapi.mlb.com/api/v1/sports/1/players?season=2026"
                pos_resp = requests.get(pos_url, timeout=15).json()
            
                for person in pos_resp.get("people", []):
                    pid = person.get("id")
                    pos = person.get("primaryPosition", {}).get("abbreviation", "UTIL")
                    position_lookup[pid] = pos
            
            except Exception:
                position_lookup = {}
            h_url = "https://statsapi.mlb.com/api/v1/stats?stats=season&group=hitting&playerPool=ALL&season=2026&limit=1500"
            h_resp = requests.get(h_url, timeout=15).json()
            for h in h_resp.get('stats', [{}])[0].get('splits', []):
                h_name = clean_name(h['player']['fullName'])
                s = h['stat']
                hitter_data[h_name] = {
                    'ID': h['player'].get('id'),
                    'Team': h.get('team', {}).get('name', 'Free Agent'),
                    'Position': position_lookup.get(h['player'].get('id'), 'UTIL'),
                    'Bat Side': h.get('player', {}).get('batSide', {}).get('code', 'U'),
                    'H': s.get('hits',0), '2B': s.get('doubles',0), '3B': s.get('triples',0), 'HR': s.get('homeRuns',0),
                    'BB': s.get('baseOnBalls',0), 'R': s.get('runs',0), 'RBI': s.get('rbi',0), 'SB': s.get('stolenBases',0),
                    'SO': s.get('strikeOuts',0), 'G': s.get('gamesPlayed', 1) or 1
                }
                
        except Exception as e:
            st.error(f"API Sync Warning: {e}")
            
        return team_data, pitcher_data, hitter_data

    @st.cache_data(ttl=CACHE_TTL_DAILY)
    def fetch_pitch_arsenal(player_id):
        try:
            if not player_id:
                return []
    
            end_date = datetime.today().strftime("%Y-%m-%d")
            start_date = (datetime.today() - timedelta(days=45)).strftime("%Y-%m-%d")
    
            df = statcast_pitcher(start_date, end_date, int(player_id))
    
            if df is None or df.empty or "pitch_name" not in df.columns:
                return []
    
            arsenal = (
                df.groupby("pitch_name")
                .agg(
                    count=("pitch_name", "count"),
                    velo=("release_speed", "mean"),
                    h_break=("pfx_x", "mean"),
                    v_break=("pfx_z", "mean")
                )
                .reset_index()
            )
    
            total = arsenal["count"].sum()
            arsenal["usage"] = arsenal["count"] / total * 100
    
            arsenal = arsenal.sort_values("usage", ascending=False).head(6)
    
            arsenal_list = []

            for _, row in arsenal.iterrows():
                pitch_name = row["pitch_name"]
            
                pitch_df = df[df["pitch_name"] == pitch_name]
            
                location_sample = pitch_df[
                    pitch_df["plate_x"].notna() & pitch_df["plate_z"].notna()
                ].tail(75)
            
                locations = [
                    {
                        "x": round(r["plate_x"], 2),
                        "z": round(r["plate_z"], 2)
                    }
                    for _, r in location_sample.iterrows()
                ]
            
                arsenal_list.append({
                    "pitch": pitch_name,
                    "usage": round(row["usage"], 1),
                    "velo": round(row["velo"], 1) if pd.notna(row["velo"]) else 0,
                    "h_break": round(row["h_break"] * 12, 1) if pd.notna(row["h_break"]) else 0,
                    "v_break": round(row["v_break"] * 12, 1) if pd.notna(row["v_break"]) else 0,
                    "locations": locations
                })
            
            return arsenal_list
    
        except Exception:
            return []

    @st.cache_data(ttl=CACHE_TTL_DAILY)
    def fetch_batter_statcast(player_id):
        try:
            if not player_id:
                return None
    
            end_date = datetime.today().strftime("%Y-%m-%d")
            start_date = (datetime.today() - timedelta(days=45)).strftime("%Y-%m-%d")
    
            df = statcast_batter(start_date, end_date, int(player_id))
    
            if df is None or df.empty:
                return None
    
            avg_ev = round(df["launch_speed"].dropna().mean(), 1) if "launch_speed" in df.columns else 0
    
            hard_hit = (
                (df["launch_speed"] >= 95).mean() * 100
                if "launch_speed" in df.columns else 0
            )
    
            k_rate = (
                (df["events"] == "strikeout").mean() * 100
                if "events" in df.columns else 0
            )
    
            return {
                "avg_ev": round(avg_ev, 1),
                "hard_hit": round(hard_hit, 1),
                "k_rate": round(k_rate, 1)
            }
    
        except Exception:
            return None

    @st.cache_data(ttl=CACHE_TTL_DAILY)
    def fetch_recent_batter_fantasy_form(player_id):
        try:
            if not player_id:
                return None

            url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=gameLog&group=hitting&season=2026"
            data = requests.get(url, timeout=10).json()
    
            splits = data.get("stats", [{}])[0].get("splits", [])
    
            if not splits:
                return None
    
            recent_games = splits[-7:]
    
            total_points = 0
            games = 0
    
            for g in recent_games:
                s = g.get("stat", {})
    
                points = (
                    (int(s.get("hits", 0)) * 1)
                    + (int(s.get("doubles", 0)) * 2)
                    + (int(s.get("triples", 0)) * 3)
                    + (int(s.get("homeRuns", 0)) * 6)
                    + (int(s.get("runs", 0)) * 2)
                    + (int(s.get("rbi", 0)) * 2)
                    + (int(s.get("baseOnBalls", 0)) * 1)
                    + (int(s.get("stolenBases", 0)) * 5)
                    - (int(s.get("strikeOuts", 0)) * 0.5)
                )
    
                total_points += points
                games += 1
    
            if games == 0:
                return None
    
            return round(total_points / games, 2)
    
        except Exception:
            return None

    @st.cache_data(ttl=CACHE_TTL_SHORT)
    def fetch_today_mlb_lineups():
        try:
            date_str = get_local_date_str()
    
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=lineups"
    
            data = requests.get(url, timeout=10).json()
    
            lineup_map = {}
    
            for date_block in data.get("dates", []):
                for game in date_block.get("games", []):
    
                    teams = game.get("teams", {})
    
                    for side in ["away", "home"]:
    
                        team_name = teams.get(side, {}).get("team", {}).get("name", "Unknown")
    
                        lineup = teams.get(side, {}).get("lineup", [])
    
                        for idx, player in enumerate(lineup):
    
                            player_name = clean_name(player.get("fullName", ""))
    
                            if player_name:
    
                                lineup_map[player_name] = {
                                    "Team": team_name,
                                    "Batting Order": idx + 1,
                                    "Confirmed Starter": True
                                }
    
            return lineup_map
    
        except Exception:
            return {}
    
    def log_to_google_sheets(row_data):
        try:
            gc = get_google_client()
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("MLB Log V2")
            values = worksheet.get_all_values()
            
            if not values or len(values) == 0:
                worksheet.append_row(["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Confidence", "Result"])
                values = [["Date", "Away Team", "Home Team"]]
            
            target_date = row_data[0]
            target_away = row_data[1]
            target_home = row_data[2]
            
            for row in values[1:]:
                if len(row) >= 3 and row[0] == target_date and row[1] == target_away and row[2] == target_home:
                    return "DUPLICATE"
                    
            worksheet.append_row(row_data)
            return "SUCCESS"
        except Exception as e:
            st.error(f"Google Sheets Log Error: {e}")
            return "ERROR"
    @st.cache_data(ttl=CACHE_TTL_SHORT)
    def get_master_log_stats():
        try:
            worksheet = get_google_worksheet("MLB Daily Prediction Model", "MLB Log V2")
            data = worksheet.get_all_values()
            last_updated = datetime.now().strftime("%Y-%m-%d %I:%M %p")
            if len(data) <= 1: return 0, 0.0, 0.0
            total_games, model_wins, vegas_wins = 0, 0, 0
            for row in data[1:]:
                if len(row) >= 9:
                    result, model_pick = row[8].strip().upper(), row[7].strip()
                    away_ml = int(row[3]) if row[3].replace('-','').isdigit() else 0
                    home_ml = int(row[4]) if row[4].replace('-','').isdigit() else 0
                    away_t, home_t = row[1], row[2]
                    vegas_pick = away_t if away_ml < home_ml else home_t
                    if result in ["WIN", "LOSS"]:
                        total_games += 1
                        if result == "WIN": model_wins += 1
                        actual_winner = model_pick if result == "WIN" else (away_t if model_pick == home_t else home_t)
                        if actual_winner == vegas_pick: vegas_wins += 1
            mod_acc = (model_wins / total_games * 100) if total_games > 0 else 0.0
            veg_acc = (vegas_wins / total_games * 100) if total_games > 0 else 0.0
            return total_games, mod_acc, veg_acc, last_updated
        except Exception:
            return 0, 0.0, 0.0, "Unavailable"

    def calculate_sp_edge_score(pitcher_stats, pitcher_name):
        p = pitcher_stats.get(pitcher_name, {})
    
        ip = p.get("IP", 0)
        k = p.get("K", 0)
        bb = p.get("BB", 0)
        fip = p.get("FIP", 4.20)
    
        if ip <= 0:
            return 0
    
        k9 = (k / ip) * 9
        bb9 = (bb / ip) * 9
    
        strikeout_score = min(1.0, k9 / 12)
        control_score = max(0.0, 1 - (bb9 / 5))
        run_prev_score = max(0.0, 1 - (fip / 6))
        workload_score = min(1.0, ip / 180)
    
        sp_score = (
            strikeout_score * 0.40
            + run_prev_score * 0.30
            + control_score * 0.20
            + workload_score * 0.10
        )
    
        return sp_score

    def calculate_team_k_tendency(team_stats, team_name):
        # Temporary safe version until we add real team batting strikeout rates
        high_k_teams = [
            "Colorado Rockies",
            "Pittsburgh Pirates",
            "Chicago White Sox",
            "Miami Marlins",
            "Oakland Athletics"
        ]
    
        low_k_teams = [
            "Houston Astros",
            "San Diego Padres",
            "Cleveland Guardians",
            "Arizona Diamondbacks"
        ]
    
        if team_name in high_k_teams:
            return 1.08
    
        if team_name in low_k_teams:
            return 0.94
    
        return 1.00

    def calculate_bullpen_fatigue(team_name):
        # Temporary safe version until we add real bullpen usage data
        tired_bullpen_teams = [
            "Colorado Rockies",
            "Chicago White Sox",
            "Miami Marlins",
            "Oakland Athletics",
            "Washington Nationals"
        ]
    
        rested_bullpen_teams = [
            "Los Angeles Dodgers",
            "Atlanta Braves",
            "New York Yankees",
            "Philadelphia Phillies",
            "Houston Astros"
        ]
    
        if team_name in tired_bullpen_teams:
            return 1.06
    
        if team_name in rested_bullpen_teams:
            return 0.96
    
        return 1.00
    
    def auto_grade_pending_bets():
        try:
            worksheet = get_google_worksheet("MLB Daily Prediction Model", "MLB Log V2")
            data = worksheet.get_all_values()
            pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 10 and row[9] == "PENDING"]
            if not pending_rows: return 0
            
            pending_dates = list(set([row[0] for i, row in pending_rows]))
            score_dict = {}
            for d_str in pending_dates:
                url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d_str}"
                resp = requests.get(url, timeout=10).json()
                if 'dates' in resp and len(resp['dates']) > 0:
                    for g in resp['dates'][0]['games']:
                        if g['status']['abstractGameState'] == 'Final':
                            away, home = g['teams']['away']['team']['name'], g['teams']['home']['team']['name']
                            winner = away if g['teams']['away'].get('score', 0) > g['teams']['home'].get('score', 0) else home
                            score_dict[f"{d_str}_{away}"] = winner
                            score_dict[f"{d_str}_{home}"] = winner
            
            updates = 0
            for i, row in pending_rows:
                d_str, away_t, model_pick = row[0], row[1], row[7]
                lookup_key = f"{d_str}_{away_t}"
                if lookup_key in score_dict:
                    actual_winner = score_dict[lookup_key]
                    new_status = "WIN" if model_pick == actual_winner else "LOSS"
                    worksheet.update_cell(i + 1, 10, new_status)
                    updates += 1
            return updates
        except Exception as e:
            st.error(f"Auto-Grader Error: {e}")
            return -1

    if page == "🎲 Monte Carlo Simulation Engine":
        st.title("🎲 Monte Carlo Simulation Engine")
        st.markdown("### 📊 Live Model Log & Automation")
        st.subheader("📈 MLB V2 Performance Dashboard")

        v2_total = 0
        v2_wins = 0
        v2_losses = 0
        vegas_wins = 0
        vegas_losses = 0
        
        try:
            worksheet = get_google_worksheet("MLB Daily Prediction Model", "MLB Log V2")
            data = worksheet.get_all_values()
        
            for row in data[1:]:
                if len(row) >= 10:
                    result = row[9].strip().upper()
        
                    if result in ["WIN", "LOSS"]:
                        v2_total += 1
        
                        if result == "WIN":
                            v2_wins += 1
                        else:
                            v2_losses += 1
        
                        away_team = row[1]
                        home_team = row[2]
                        away_ml = int(row[3])
                        home_ml = int(row[4])
                        model_pick = row[7]
        
                        vegas_pick = away_team if away_ml < home_ml else home_team
        
                        actual_winner = (
                            model_pick
                            if result == "WIN"
                            else (away_team if model_pick == home_team else home_team)
                        )
        
                        if vegas_pick == actual_winner:
                            vegas_wins += 1
                        else:
                            vegas_losses += 1
        
            v2_acc = (v2_wins / v2_total * 100) if v2_total > 0 else 0
            vegas_total = vegas_wins + vegas_losses
            vegas_acc = (vegas_wins / vegas_total * 100) if vegas_total > 0 else 0
            hag_advantage = v2_acc - vegas_acc
        
            d1, d2, d3, d4 = st.columns(4)

            with d1:
                st.metric("Graded Games", v2_total)
            
            with d2:
                st.metric("Hag Labs Accuracy", f"{v2_acc:.1f}%")
            
            with d3:
                st.metric("Vegas Accuracy", f"{vegas_acc:.1f}%")
            
            with d4:
                st.metric("Hag Labs Advantage", f"{hag_advantage:+.1f}%")

            st.markdown("#### Accuracy by Confidence Tier")
    
            tier_stats = {
                "High": {"wins": 0, "losses": 0},
                "Medium": {"wins": 0, "losses": 0},
                "Low": {"wins": 0, "losses": 0}
            }
            
            for row in data[1:]:
                if len(row) >= 10:
                    confidence = row[8].strip()
                    result = row[9].strip().upper()
            
                    if confidence in tier_stats and result in ["WIN", "LOSS"]:
                        if result == "WIN":
                            tier_stats[confidence]["wins"] += 1
                        else:
                            tier_stats[confidence]["losses"] += 1
            
            tier_cols = st.columns(3)
            
            for idx, tier in enumerate(["High", "Medium", "Low"]):
                wins = tier_stats[tier]["wins"]
                losses = tier_stats[tier]["losses"]
                total = wins + losses
                acc = (wins / total * 100) if total > 0 else 0
            
                with tier_cols[idx]:
                    st.metric(
                        f"{tier} Confidence",
                        f"{acc:.1f}%",
                        f"{wins}-{losses}"
                    )
        
        except Exception as e:
            st.warning(f"Dashboard unavailable: {e}")
        tot_games, mod_acc, veg_acc, last_updated = get_master_log_stats()
        st.caption(f"Last Updated: {last_updated}")
        if st.button("🔄 Refresh MLB Cached Data"):
            st.cache_data.clear()
            st.success("MLB cached data cleared. Refresh the page to reload fresh data.")
        
        if st.button("🔄 Auto-Grade Yesterday's Bets"):
            with st.spinner("Pinging MLB Stats API..."):
                updates = auto_grade_pending_bets()
                if updates > 0:
                    st.success(f"✅ Successfully graded {updates} games! Refresh.")
                elif updates == 0:
                    st.info("No games ready to be graded.")
                with st.spinner("Pinging MLB Stats API..."):
                    updates = auto_grade_pending_bets()
                    if updates > 0: st.success(f"✅ Successfully graded {updates} games! Refresh.")
                    elif updates == 0: st.info("No games ready to be graded.")
        st.markdown("---")
        
        with st.spinner('Syncing native MLB API data and live odds...'):
            team_stats, pitcher_stats, _ = fetch_mlb_api_data()
            live_odds = get_live_odds()
            
            if not team_stats:
                st.warning("⚠️ Could not establish connection to MLB Stats API.")
            else:
                st.subheader("⚡ Automated Daily Slate Runner")
                if st.button("▶ Auto-Run & Log Entire Daily Slate"):
                    with st.spinner("Simulating full MLB Slate using API Metrics..."):
                        slate_logs = []
                        new_logs_count = 0
                        date_str = get_local_date_str()
                        
                        sched_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=probablePitcher"
                        sched_resp = requests.get(sched_url).json()
                        probables = {}
                        if 'dates' in sched_resp and len(sched_resp['dates']) > 0:
                            for g in sched_resp['dates'][0].get('games', []):
                                a_team = g['teams']['away']['team']['name']
                                h_team = g['teams']['home']['team']['name']
                                a_sp = clean_name(g['teams']['away'].get('probablePitcher', {}).get('fullName', 'Unknown'))
                                h_sp = clean_name(g['teams']['home'].get('probablePitcher', {}).get('fullName', 'Unknown'))
                                probables[a_team] = a_sp
                                probables[h_team] = h_sp

                        for game_key, odds in live_odds.items():
                            try:
                                away_t, home_t = game_key.split(" @ ")
                                a_ml, h_ml = odds
                                
                                a_rs_g = team_stats.get(away_t, {}).get('RS_per_G', 4.5)
                                h_rs_g = team_stats.get(home_t, {}).get('RS_per_G', 4.5)
                                a_ra_g = team_stats.get(away_t, {}).get('RA_per_G', 4.5)
                                h_ra_g = team_stats.get(home_t, {}).get('RA_per_G', 4.5)
                                
                                away_sp = probables.get(away_t, 'Unknown')
                                home_sp = probables.get(home_t, 'Unknown')
                                a_sp_fip = pitcher_stats.get(away_sp, {}).get('FIP', a_ra_g)
                                h_sp_fip = pitcher_stats.get(home_sp, {}).get('FIP', h_ra_g)

                                away_pitcher_id = pitcher_stats.get(away_sp, {}).get("ID")
                                home_pitcher_id = pitcher_stats.get(home_sp, {}).get("ID")
                                
                                a_recent_era = fetch_pitcher_recent_era(away_pitcher_id) or a_sp_fip
                                h_recent_era = fetch_pitcher_recent_era(home_pitcher_id) or h_sp_fip
                                
                                a_sp_fip = blend_pitcher_form(a_sp_fip, a_recent_era)
                                h_sp_fip = blend_pitcher_form(h_sp_fip, h_recent_era)

                                away_sp_score = calculate_sp_edge_score(pitcher_stats, away_sp)
                                home_sp_score = calculate_sp_edge_score(pitcher_stats, home_sp)
                                
                                sp_edge_adjustment = (away_sp_score - home_sp_score) * 0.25

                                away_k_factor = calculate_team_k_tendency(team_stats, home_t)
                                home_k_factor = calculate_team_k_tendency(team_stats, away_t)
                                
                                sp_edge_adjustment = sp_edge_adjustment * away_k_factor
                                sp_edge_adjustment = sp_edge_adjustment / home_k_factor
                                
                                a_run_prevention = (a_sp_fip * 0.60) + (a_ra_g * 0.40)
                                h_run_prevention = (h_sp_fip * 0.60) + (h_ra_g * 0.40)
                                p_factor = PARK_FACTORS.get(home_t, 100) / 100

                                away_bullpen_factor = calculate_bullpen_fatigue(away_t)
                                home_bullpen_factor = calculate_bullpen_fatigue(home_t)
                                
                                away_recent_raw = fetch_recent_mlb_team_form(away_t) or {
                                    "recent_rs_per_g": a_rs_g,
                                    "recent_ra_per_g": a_ra_g,
                                    "recent_games": 0
                                }
                                
                                home_recent_raw = fetch_recent_mlb_team_form(home_t) or {
                                    "recent_rs_per_g": h_rs_g,
                                    "recent_ra_per_g": h_ra_g,
                                    "recent_games": 0
                                }
                                
                                away_recent_form = calculate_recent_form_adjustment(
                                    a_rs_g,
                                    away_recent_raw["recent_rs_per_g"],
                                    a_ra_g,
                                    away_recent_raw["recent_ra_per_g"]
                                )
                                
                                home_recent_form = calculate_recent_form_adjustment(
                                    h_rs_g,
                                    home_recent_raw["recent_rs_per_g"],
                                    h_ra_g,
                                    home_recent_raw["recent_ra_per_g"]
                                )
                                
                                away_lam = (((away_recent_form["offense"] + home_recent_form["defense"]) / 2) * p_factor * home_bullpen_factor) * (1 + sp_edge_adjustment)
                                home_lam = (((home_recent_form["offense"] + away_recent_form["defense"]) / 2) * p_factor * away_bullpen_factor) * (1 - sp_edge_adjustment)
                                
                                sim_a = np.random.poisson(away_lam, DEFAULT_SIMULATION_SIZE)
                                sim_h = np.random.poisson(home_lam, DEFAULT_SIMULATION_SIZE)
                                a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                                h_wins = DEFAULT_SIMULATION_SIZE - a_wins
                                model_away_prob = a_wins / DEFAULT_SIMULATION_SIZE
                                model_home_prob = h_wins / DEFAULT_SIMULATION_SIZE
                                v_a_prob = calculate_implied_prob(a_ml)
                                v_h_prob = calculate_implied_prob(h_ml)
                                
                                action_taken = "No Edge"

                                away_edge = model_away_prob - v_a_prob
                                home_edge = model_home_prob - v_h_prob
                                
                                if away_edge > MIN_ACTIONABLE_EDGE and away_edge > home_edge:
                                    action_taken = away_t
                                
                                elif home_edge > MIN_ACTIONABLE_EDGE and home_edge > away_edge:
                                    action_taken = home_t
                                
                                confidence_tier = "Low"

                                if action_taken == away_t:
                                    confidence_tier = get_confidence_tier(model_away_prob, v_a_prob)
                                
                                elif action_taken == home_t:
                                    confidence_tier = get_confidence_tier(model_home_prob, v_h_prob)
                                
                                if action_taken != "No Edge" and confidence_tier != "Low":
                                    row_data = [date_str, away_t, home_t, a_ml, h_ml, f"{model_away_prob:.1%}", f"{model_home_prob:.1%}", action_taken, confidence_tier, "PENDING"]
                                    log_status = log_to_google_sheets(row_data)
                                    if log_status in ["SUCCESS", "DUPLICATE"]:
                                        slate_logs.append(row_data)
                                        if log_status == "SUCCESS":
                                            new_logs_count += 1
                            except: continue
                            
                        if slate_logs:
                            st.success(f"✅ Successfully processed {len(slate_logs)} actionable edges! ({new_logs_count} new entries logged to Sheets)")
                            st.markdown("#### 📅 Today's MLB Actionable Edges")
                            df_display = pd.DataFrame(slate_logs, columns=["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Confidence", "Status"])
                            st.dataframe(df_display, use_container_width=True, hide_index=True)
                        else:
                            st.info("No actionable edges found on today's MLB slate.")

        st.markdown("---")
        st.subheader("Manual Matchup Override")
        st.caption("Standalone Engine: Calculates probability edges using native MLB API logic and visualizes Poisson distributions.")
        st.caption(f"Simulation Size: {DEFAULT_SIMULATION_SIZE:,} runs per team")
        st.caption(f"Minimum Actionable Edge: {MIN_ACTIONABLE_EDGE:.1%}")
        if st.checkbox("Show MLB Team Stats Debug"):
            st.write(team_stats)

        if st.checkbox("Show Recent Form Debug"):
            debug_recent = fetch_recent_mlb_team_form("Los Angeles Dodgers")

            st.write(debug_recent)

        if st.checkbox("Show Pitcher ID Debug"):
            st.write(pitcher_stats.get("Shohei Ohtani"))
            
        MLB_TEAMS = sorted(list(PARK_FACTORS.keys()))
        
        col_a, col_b = st.columns(2)
        with col_a:
            away_t = st.selectbox("Away Team:", MLB_TEAMS, index=0)
            away_pitchers = sorted([p for p, data in pitcher_stats.items() if data.get('Team', 'FA') == away_t]) if 'pitcher_stats' in locals() else []
            away_sp = st.selectbox(f"{away_t} SP Override:", ["League Average SP"] + away_pitchers)
        
        with col_b:
            home_t = st.selectbox("Home Team:", MLB_TEAMS, index=1)
            home_pitchers = sorted([p for p, data in pitcher_stats.items() if data.get('Team', 'FA') == home_t]) if 'pitcher_stats' in locals() else []
            home_sp = st.selectbox(f"{home_t} SP Override:", ["League Average SP"] + home_pitchers)
            
        location = st.selectbox("Location:", list(PARK_FACTORS.keys()), index=list(PARK_FACTORS.keys()).index(home_t) if home_t in PARK_FACTORS else 0)
        
        if st.button("▶ Run Manual Simulation"):
            if 'team_stats' in locals() and team_stats:
                p_factor = PARK_FACTORS.get(location, 100) / 100
                
                a_rs_g = team_stats.get(away_t, {}).get('RS_per_G', 4.5)
                h_rs_g = team_stats.get(home_t, {}).get('RS_per_G', 4.5)
                a_ra_g = team_stats.get(away_t, {}).get('RA_per_G', 4.5)
                h_ra_g = team_stats.get(home_t, {}).get('RA_per_G', 4.5)

                a_sp_fip = pitcher_stats.get(away_sp, {}).get('FIP', a_ra_g) if away_sp != "League Average SP" else a_ra_g
                h_sp_fip = pitcher_stats.get(home_sp, {}).get('FIP', h_ra_g) if home_sp != "League Average SP" else h_ra_g

                away_pitcher_id = pitcher_stats.get(away_sp, {}).get("ID")
                home_pitcher_id = pitcher_stats.get(home_sp, {}).get("ID")
                
                a_recent_era = fetch_pitcher_recent_era(away_pitcher_id) or a_sp_fip
                h_recent_era = fetch_pitcher_recent_era(home_pitcher_id) or h_sp_fip
                
                a_sp_fip = blend_pitcher_form(a_sp_fip, a_recent_era)
                h_sp_fip = blend_pitcher_form(h_sp_fip, h_recent_era)

                away_sp_score = calculate_sp_edge_score(pitcher_stats, away_sp)
                home_sp_score = calculate_sp_edge_score(pitcher_stats, home_sp)
                
                sp_edge_adjustment = (away_sp_score - home_sp_score) * 0.25

                away_k_factor = calculate_team_k_tendency(team_stats, home_t)
                home_k_factor = calculate_team_k_tendency(team_stats, away_t)
                
                sp_edge_adjustment = sp_edge_adjustment * away_k_factor
                sp_edge_adjustment = sp_edge_adjustment / home_k_factor
                
                st.caption(
                    f"Pitcher Form Blend | "
                    f"{away_sp}: Recent ERA {a_recent_era:.2f} | "
                    f"{home_sp}: Recent ERA {h_recent_era:.2f}"
                )
                
                a_run_prevention = (a_sp_fip * 0.60) + (a_ra_g * 0.40)
                h_run_prevention = (h_sp_fip * 0.60) + (h_ra_g * 0.40)

                away_bullpen_factor = calculate_bullpen_fatigue(away_t)
                home_bullpen_factor = calculate_bullpen_fatigue(home_t)
                
                away_recent_raw = fetch_recent_mlb_team_form(away_t) or {
                    "recent_rs_per_g": a_rs_g,
                    "recent_ra_per_g": a_ra_g,
                    "recent_games": 0
                }
                
                home_recent_raw = fetch_recent_mlb_team_form(home_t) or {
                    "recent_rs_per_g": h_rs_g,
                    "recent_ra_per_g": h_ra_g,
                    "recent_games": 0
                }
                
                away_recent_form = calculate_recent_form_adjustment(
                    a_rs_g,
                    away_recent_raw["recent_rs_per_g"],
                    a_ra_g,
                    away_recent_raw["recent_ra_per_g"]
                )
                
                home_recent_form = calculate_recent_form_adjustment(
                    h_rs_g,
                    home_recent_raw["recent_rs_per_g"],
                    h_ra_g,
                    home_recent_raw["recent_ra_per_g"]
                )
                
                away_lam = (((away_recent_form["offense"] + home_recent_form["defense"]) / 2) * p_factor * home_bullpen_factor) * (1 + sp_edge_adjustment)
                home_lam = (((home_recent_form["offense"] + away_recent_form["defense"]) / 2) * p_factor * away_bullpen_factor) * (1 - sp_edge_adjustment)
                
                sim_a = np.random.poisson(away_lam, DEFAULT_SIMULATION_SIZE)
                sim_h = np.random.poisson(home_lam, DEFAULT_SIMULATION_SIZE)
                a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                h_wins = DEFAULT_SIMULATION_SIZE - a_wins
                model_away_prob = a_wins / DEFAULT_SIMULATION_SIZE
                model_home_prob = h_wins / DEFAULT_SIMULATION_SIZE
                
                st.write(f"Final Expected Runs: {away_t} **{away_lam:.2f}** | {home_t} **{home_lam:.2f}**")

                away_recent_raw = fetch_recent_mlb_team_form(away_t) or {
                    "recent_rs_per_g": a_rs_g,
                    "recent_ra_per_g": a_ra_g,
                    "recent_games": 0
                }
                
                home_recent_raw = fetch_recent_mlb_team_form(home_t) or {
                    "recent_rs_per_g": h_rs_g,
                    "recent_ra_per_g": h_ra_g,
                    "recent_games": 0
                }
                
                away_recent_form = calculate_recent_form_adjustment(
                    a_rs_g,
                    away_recent_raw["recent_rs_per_g"],
                    a_ra_g,
                    away_recent_raw["recent_ra_per_g"]
                )
                
                home_recent_form = calculate_recent_form_adjustment(
                    h_rs_g,
                    home_recent_raw["recent_rs_per_g"],
                    h_ra_g,
                    home_recent_raw["recent_ra_per_g"]
                )
                
                away_lam = ((away_recent_form["offense"] + home_recent_form["defense"]) / 2) * p_factor
                home_lam = ((home_recent_form["offense"] + away_recent_form["defense"]) / 2) * p_factor
                
                st.caption(
                    f"Recent Form Blend Active | "
                    f"{away_t}: Off {away_recent_form['offense']:.2f}, Def {away_recent_form['defense']:.2f} | "
                    f"{home_t}: Off {home_recent_form['offense']:.2f}, Def {home_recent_form['defense']:.2f}"
                )
                    
                away_lam = ((away_recent_form["offense"] + home_recent_form["defense"]) / 2) * p_factor
                home_lam = ((home_recent_form["offense"] + away_recent_form["defense"]) / 2) * p_factor

                away_wp = model_away_prob * 100
                home_wp = model_home_prob * 100

                st.markdown("### 📊 Team Momentum Snapshot")

                tm1, tm2 = st.columns(2)
                
                away_recent_games = away_recent_raw.get("recent_games", 0)
                home_recent_games = home_recent_raw.get("recent_games", 0)
                
                away_recent_rs = away_recent_raw.get("recent_rs_per_g", a_rs_g)
                away_recent_ra = away_recent_raw.get("recent_ra_per_g", a_ra_g)
                
                home_recent_rs = home_recent_raw.get("recent_rs_per_g", h_rs_g)
                home_recent_ra = home_recent_raw.get("recent_ra_per_g", h_ra_g)
                
                def momentum_label(rs, ra):
                    diff = rs - ra
                
                    if diff >= 1.0:
                        return "🔥 Strong Positive"
                    elif diff >= 0.25:
                        return "↗ Improving"
                    elif diff > -0.25:
                        return "➖ Neutral"
                    elif diff > -1.0:
                        return "↘ Slipping"
                    else:
                        return "❄️ Cold"
                
                with tm1:
                    away_diff = away_recent_rs - away_recent_ra
                    away_momentum_score = max(0, min(1, (away_diff + 2) / 4))
                
                    st.markdown(f"#### {away_t}")
                    st.metric("Recent Runs/Game", f"{away_recent_rs:.2f}")
                    st.metric("Recent Runs Allowed/Game", f"{away_recent_ra:.2f}")
                    st.metric("Recent Run Diff", f"{away_diff:+.2f}")
                    st.progress(away_momentum_score)
                    st.metric("Recent Games Sample", away_recent_games)
                    st.caption(momentum_label(away_recent_rs, away_recent_ra))
                
                with tm2:
                    home_diff = home_recent_rs - home_recent_ra
                    home_momentum_score = max(0, min(1, (home_diff + 2) / 4))
                
                    st.markdown(f"#### {home_t}")
                    st.metric("Recent Runs/Game", f"{home_recent_rs:.2f}")
                    st.metric("Recent Runs Allowed/Game", f"{home_recent_ra:.2f}")
                    st.metric("Recent Run Diff", f"{home_diff:+.2f}")
                    st.progress(home_momentum_score)
                    st.metric("Recent Games Sample", home_recent_games)
                    st.caption(momentum_label(home_recent_rs, home_recent_ra))
                
                st.markdown("### ⚾ Premium Pitcher Matchup")

                st.markdown(
                    """
                    <style>
                    .pitcher-card {
                        background: linear-gradient(145deg, #111827, #0b1220);
                        border: 1px solid rgba(255,255,255,0.12);
                        border-radius: 18px;
                        padding: 24px;
                        box-shadow: 0 0 18px rgba(255,255,255,0.05);
                        margin-bottom: 18px;
                    }
                    .pitcher-card h2 {
                        margin-top: 0;
                    }
                    </style>
                    """,
                    unsafe_allow_html=True
                )
                
                card1, card2 = st.columns(2)
                
                away_pitcher_stats = pitcher_stats.get(away_sp, {})
                home_pitcher_stats = pitcher_stats.get(home_sp, {})
                
                away_k9 = (
                    (away_pitcher_stats.get("K", 0) / away_pitcher_stats.get("IP", 1)) * 9
                    if away_pitcher_stats.get("IP", 0) > 0 else 0
                )
                
                home_k9 = (
                    (home_pitcher_stats.get("K", 0) / home_pitcher_stats.get("IP", 1)) * 9
                    if home_pitcher_stats.get("IP", 0) > 0 else 0
                )
                
                def pitcher_form_label(era):
                    if era <= 2.75:
                        return "🔥 HOT"
                    elif era <= 4.00:
                        return "✅ STABLE"
                    else:
                        return "❄️ COLD"
                
                with card1:

                    st.markdown("<div class='pitcher-card'>", unsafe_allow_html=True)
                    
                    away_fip_color = (
                        "green" if a_sp_fip <= 3.25
                        else "orange" if a_sp_fip <= 4.25
                        else "red"
                    )
                
                    away_era_color = (
                        "green" if a_recent_era <= 3.00
                        else "orange" if a_recent_era <= 4.25
                        else "red"
                    )
                
                    away_k9_color = (
                        "green" if away_k9 >= 9
                        else "orange" if away_k9 >= 7
                        else "red"
                    )
                
                    st.markdown(f"## {away_sp}")
                
                    st.markdown(
                        f"<span style='color:{away_fip_color}; font-size:20px;'>FIP: {a_sp_fip:.2f}</span>",
                        unsafe_allow_html=True
                    )
                
                    st.markdown(
                        f"<span style='color:{away_era_color}; font-size:20px;'>Recent ERA: {a_recent_era:.2f}</span>",
                        unsafe_allow_html=True
                    )
                
                    st.markdown(
                        f"<span style='color:{away_k9_color}; font-size:20px;'>K/9: {away_k9:.1f}</span>",
                        unsafe_allow_html=True
                    )
                
                    st.markdown(f"IP: {away_pitcher_stats.get('IP', 0)}")
                
                    st.markdown(
                        f"### {pitcher_form_label(a_recent_era)}"
                    )
                
                    st.progress(away_wp / 100)
                
                    st.caption(f"{away_t} Win Probability: {away_wp:.1f}%")

                    st.markdown("</div>", unsafe_allow_html=True)
                
                with card2:

                    st.markdown("<div class='pitcher-card'>", unsafe_allow_html=True)
                    
                    home_fip_color = (
                        "green" if h_sp_fip <= 3.25
                        else "orange" if h_sp_fip <= 4.25
                        else "red"
                    )
                
                    home_era_color = (
                        "green" if h_recent_era <= 3.00
                        else "orange" if h_recent_era <= 4.25
                        else "red"
                    )
                
                    home_k9_color = (
                        "green" if home_k9 >= 9
                        else "orange" if home_k9 >= 7
                        else "red"
                    )
                
                    st.markdown(f"## {home_sp}")
                
                    st.markdown(
                        f"<span style='color:{home_fip_color}; font-size:20px;'>FIP: {h_sp_fip:.2f}</span>",
                        unsafe_allow_html=True
                    )
                
                    st.markdown(
                        f"<span style='color:{home_era_color}; font-size:20px;'>Recent ERA: {h_recent_era:.2f}</span>",
                        unsafe_allow_html=True
                    )
                
                    st.markdown(
                        f"<span style='color:{home_k9_color}; font-size:20px;'>K/9: {home_k9:.1f}</span>",
                        unsafe_allow_html=True
                    )
                
                    st.markdown(f"IP: {home_pitcher_stats.get('IP', 0)}")
                
                    st.markdown(
                        f"### {pitcher_form_label(h_recent_era)}"
                    )
                
                    st.progress(home_wp / 100)
                
                    st.caption(f"{home_t} Win Probability: {home_wp:.1f}%")

                    st.markdown("</div>", unsafe_allow_html=True)
                
                res_c1, res_c2 = st.columns(2)

                st.markdown("### 🎯 Matchup Edge Meter")

                edge_col1, edge_col2, edge_col3 = st.columns(3)
                
                model_edge = abs(model_away_prob - model_home_prob)
                fav_team = away_t if model_away_prob > model_home_prob else home_t
                fav_prob = max(model_away_prob, model_home_prob)
                
                if model_edge >= 0.12:
                    edge_label = "Strong Edge"
                elif model_edge >= 0.06:
                    edge_label = "Moderate Edge"
                else:
                    edge_label = "Tight Matchup"
                
                with edge_col1:

                    away_logo = TEAM_LOGOS.get(away_t)
                
                    if away_logo:
                        st.image(away_logo, width=55)
                
                    st.metric(f"{away_t} Win Prob", f"{model_away_prob:.1%}")
                
                with edge_col2:
                    st.metric("Model Lean", fav_team)
                    st.progress(float(fav_prob))
                
                confidence = "Low"

                if model_edge >= 0.12:
                    confidence = "High"
                elif model_edge >= 0.06:
                    confidence = "Medium"
                
                with edge_col3:

                    home_logo = TEAM_LOGOS.get(home_t)
                
                    if home_logo:
                        st.image(home_logo, width=55)
                
                    st.metric(f"{home_t} Win Prob", f"{model_home_prob:.1%}")
                
                    if confidence == "High":
                        confidence_html = """
                        <div style='
                            background-color:#14532d;
                            color:#86efac;
                            padding:10px;
                            border-radius:12px;
                            text-align:center;
                            font-weight:700;
                            font-size:20px;
                            margin-top:10px;
                        '>
                            🔥 HIGH CONFIDENCE
                        </div>
                        """
                
                    elif confidence == "Medium":
                        confidence_html = """
                        <div style='
                            background-color:#78350f;
                            color:#fde68a;
                            padding:10px;
                            border-radius:12px;
                            text-align:center;
                            font-weight:700;
                            font-size:20px;
                            margin-top:10px;
                        '>
                            ⚡ MEDIUM CONFIDENCE
                        </div>
                        """
                
                    else:
                        confidence_html = """
                        <div style='
                            background-color:#7f1d1d;
                            color:#fca5a5;
                            padding:10px;
                            border-radius:12px;
                            text-align:center;
                            font-weight:700;
                            font-size:20px;
                            margin-top:10px;
                        '>
                            ❄️ LOW CONFIDENCE
                        </div>
                        """
                
                    st.markdown(confidence_html, unsafe_allow_html=True)

                # --- PLOTLY VISUALIZATION BLOCK ---
                st.markdown("#### Simulation Distribution Analysis")
                fig = go.Figure()
                
                fig.add_trace(go.Histogram(
                    x=sim_a, 
                    name=away_t, 
                    opacity=0.85, 
                    histnorm='probability',
                    marker_color='#1f77b4'
                ))
                fig.add_trace(go.Histogram(
                    x=sim_h, 
                    name=home_t, 
                    opacity=0.85, 
                    histnorm='probability',
                    marker_color='#d62728'
                ))
                
                fig.update_layout(
                    barmode='group',
                    title=f'10,000 Poisson Simulations: {away_t} vs {home_t}',
                    xaxis_title='Simulated Runs Scored',
                    yaxis_title='Probability of Occurrence',
                    yaxis=dict(tickformat='.1%'),
                    legend_title="Team",
                    xaxis=dict(tickmode='linear', tick0=0, dtick=1, range=[0, 15]),
                    hovermode='x unified'
                )
                
                st.plotly_chart(fig, use_container_width=True)
                # -----------------------------------

            else:
                st.error("Engine failure connecting to MLB Stats API.")

    elif page == "🔎 MLB Player Lab":
        st.title("🔎 MLB Player Lab")
        st.caption("Savant-inspired MLB player research center")
    
        with st.spinner("Loading MLB player database..."):
            _, pitcher_stats, hitter_stats = fetch_mlb_api_data()
    
        player_type = st.radio("Player Type:", ["Pitcher", "Batter"], horizontal=True)
    
        if player_type == "Pitcher":
            all_pitchers = sorted(list(pitcher_stats.keys()))
            selected_player = st.selectbox("Search Pitcher", all_pitchers)
    
            p_data = pitcher_stats.get(selected_player, {})
    
            if p_data:
                
                player_id = p_data.get("ID")
                headshot_url = f"https://midfield.mlbstatic.com/v1/people/{player_id}/spots/180" if player_id else None
            
                if headshot_url:
                    st.image(headshot_url, width=140)
                
                team_name = p_data.get("Team", "N/A")
                team_logo = TEAM_LOGOS.get(team_name)
                
                logo_col, text_col = st.columns([0.4, 5])
                
                with logo_col:
                    if team_logo:
                        st.image(team_logo, width=55)
                
                with text_col:
                    st.markdown(f"### {selected_player}")
                    st.caption(team_name)
                
                p_fip = p_data.get("FIP", 0)
                p_ip = p_data.get("IP", 0)
                p_k = p_data.get("K", 0)
                p_bb = p_data.get("BB", 0)
    
                p_k9 = (p_k / p_ip * 9) if p_ip > 0 else 0
                p_bb9 = (p_bb / p_ip * 9) if p_ip > 0 else 0
                
                st.markdown("## 🎯 Pitcher Profile")
    
                pc1, pc2, pc3, pc4 = st.columns(4)
    
                with pc1:
                    st.metric("FIP", f"{p_fip:.2f}")
                with pc2:
                    st.metric("IP", f"{p_ip}")
                with pc3:
                    st.metric("K/9", f"{p_k9:.1f}")
                with pc4:
                    st.metric("BB/9", f"{p_bb9:.1f}")
    
                st.markdown("### 📊 Savant-Style Pitching Percentiles")

                strikeout_pct = min(99, max(1, int((p_k9 / 12) * 100)))
                run_prev_pct = min(99, max(1, int((1 - (p_fip / 6)) * 100)))
                control_pct = min(99, max(1, int((1 - (p_bb9 / 5)) * 100)))
                workload_pct = min(99, max(1, int((p_ip / 180) * 100)))
                
                def percentile_bar(label, pct):

                    if pct >= 85:
                        color = "#d73027"   # elite red
                    elif pct >= 70:
                        color = "#fc8d59"   # orange
                    elif pct >= 50:
                        color = "#fee08b"   # yellow
                    else:
                        color = "#4575b4"   # blue
                
                    st.markdown(f"**{label}: {pct}th percentile**")
                
                    st.markdown(
                        f'''
                        <div style="
                            background-color:#2a2a2a;
                            border-radius:8px;
                            height:18px;
                            width:100%;
                            margin-bottom:18px;
                        ">
                            <div style="
                                background-color:{color};
                                width:{pct}%;
                                height:18px;
                                border-radius:8px;
                            "></div>
                        </div>
                        ''',
                        unsafe_allow_html=True
                    )
                
                percentile_bar("Strikeout Ability", strikeout_pct)
                percentile_bar("Run Prevention", run_prev_pct)
                percentile_bar("Control", control_pct)
                percentile_bar("Workload", workload_pct)

                st.markdown("### ⚾ Pitch Arsenal + Raw Movement Profile")
                st.caption("Top bar = pitch usage frequency • Bottom blue bar = raw movement magnitude (not pitch quality)")

                arsenal_data = fetch_pitch_arsenal(player_id)

                if not arsenal_data:
                    arsenal_data = PITCH_ARSENALS.get(selected_player)

                if arsenal_data:
                
                    for item in arsenal_data:

                        pitch = item.get("pitch") or item.get("pitch_name") or item.get("type") or "Pitch"
                        usage = item["usage"]
                        velo = item["velo"]
                        h_break = item.get("h_break", 0)
                        v_break = item.get("v_break", 0)
                    
                        color = PITCH_COLORS.get(pitch, "#4575b4")
                    
                        for key in PITCH_COLORS:
                            if key.lower() in str(pitch).lower():
                                color = PITCH_COLORS[key]
                    
                        st.markdown(
                            f"<div style='font-size:16px; font-weight:700; color:white; margin-top:10px; margin-bottom:4px;'>{pitch} — {usage}% — {velo:.1f} MPH</div>"
                            f"<div style='font-size:13px; color:#9ca3af; margin-bottom:6px;'>Horizontal Break: {h_break:+.1f} in | Vertical Break: {v_break:+.1f} in</div>",
                            unsafe_allow_html=True
                        )
                        
                        st.markdown(
                            f'''
                            <div style="
                                background-color:#2a2a2a;
                                border-radius:8px;
                                height:16px;
                                width:100%;
                                margin-bottom:14px;
                            ">
                                <div style="
                                    background-color:{color};
                                    width:{usage}%;
                                    height:16px;
                                    border-radius:8px;
                                "></div>
                            </div>
                            ''',
                            unsafe_allow_html=True
                        )

                        movement_score = min(100, max(0, int((abs(h_break) + abs(v_break)) * 3)))
        
                        st.markdown(
                            f'''
                            <div style="
                                background-color:#1f2937;
                                border-radius:8px;
                                height:10px;
                                width:100%;
                                margin-bottom:18px;
                            ">
                                <div style="
                                    background-color:#60a5fa;
                                    width:{movement_score}%;
                                    height:10px;
                                    border-radius:8px;
                                "></div>
                            </div>
                            ''',
                            unsafe_allow_html=True
                        )

                    location_rows = []
    
                    for pitch_item in arsenal_data:
                        pitch_name = pitch_item.get("pitch", "Pitch")
                        pitch_usage = pitch_item.get("usage", 0)
                    
                        for loc in pitch_item.get("locations", []):
                            location_rows.append({
                                "Pitch": pitch_name,
                                "Usage": pitch_usage,
                                "Plate X": loc.get("x", 0),
                                "Plate Z": loc.get("z", 0)
                            })
                    
                    location_df = pd.DataFrame(location_rows)
                    
                    if not location_df.empty:
                        st.markdown("### 🎯 Pitch Location Map")
                        st.caption("Dots show where recent pitches crossed the plate. Box represents approximate strike zone.")
                    
                        fig_loc = go.Figure()
                    
                        for pitch_name in location_df["Pitch"].unique():
                            pitch_df = location_df[location_df["Pitch"] == pitch_name]
                    
                            pitch_color = "#60a5fa"
                    
                            for key in PITCH_COLORS:
                                if key.lower() in str(pitch_name).lower():
                                    pitch_color = PITCH_COLORS[key]
                    
                            fig_loc.add_trace(go.Scatter(
                                x=pitch_df["Plate X"],
                                y=pitch_df["Plate Z"],
                                mode="markers",
                                marker=dict(
                                    size=7,
                                    color=pitch_color,
                                    opacity=0.55,
                                    line=dict(width=0)
                                ),
                                name=pitch_name,
                                hovertemplate=
                                    "<b>%{fullData.name}</b><br>" +
                                    "Plate X: %{x:.2f}<br>" +
                                    "Plate Z: %{y:.2f}<br>" +
                                    "<extra></extra>"
                            ))
                    
                        fig_loc.add_shape(
                            type="rect",
                            x0=-0.83,
                            x1=0.83,
                            y0=1.5,
                            y1=3.5,
                            line=dict(color="white", width=2),
                            fillcolor="rgba(255,255,255,0)"
                        )
                    
                        fig_loc.update_layout(
                            height=650,
                            width=650,
                            paper_bgcolor="#0e1117",
                            plot_bgcolor="#0e1117",
                            font=dict(color="white"),
                            xaxis=dict(
                                title="Plate Location: Inside / Outside",
                                range=[-2.2, 2.2],
                                zeroline=True,
                                zerolinecolor="#6b7280",
                                gridcolor="#374151",
                                scaleanchor="y",
                                scaleratio=1
                            ),
                            yaxis=dict(
                                title="Pitch Height",
                                range=[0, 5],
                                zeroline=False,
                                gridcolor="#374151"
                            ),
                            legend_title="Pitch Type"
                        )
                    
                        st.plotly_chart(fig_loc, use_container_width=False, key=f"pitch_location_{player_id}")
                
                else:
                    st.info("Pitch arsenal data coming soon.")
                
                st.markdown("### 🧾 Season Stats")
                st.dataframe(pd.DataFrame([p_data]), use_container_width=True)

        else:
            all_hitters = sorted(list(hitter_stats.keys()))
            selected_player = st.selectbox("Search Batter", all_hitters)
        
            h_data = hitter_stats.get(selected_player, {})
        
            if h_data:

                player_id = h_data.get("ID")
                headshot_url = f"https://midfield.mlbstatic.com/v1/people/{player_id}/spots/180" if player_id else None
            
                if headshot_url:
                    st.image(headshot_url, width=140)
                
                team_name = h_data.get("Team", "N/A")
                team_logo = TEAM_LOGOS.get(team_name)
                
                logo_col, text_col = st.columns([0.4, 5])
                
                with logo_col:
                    if team_logo:
                        st.image(team_logo, width=55)
                
                with text_col:
                    st.markdown(f"### {selected_player}")
                    st.caption(team_name)

                batter_statcast = fetch_batter_statcast(player_id)
                
                st.markdown("## 🧢 Batter Profile")
        
                hc1, hc2, hc3, hc4 = st.columns(4)
        
                with hc1:
                    st.metric("Hits", h_data.get("H", 0))
                with hc2:
                    st.metric("HR", h_data.get("HR", 0))
                with hc3:
                    st.metric("RBI", h_data.get("RBI", 0))
                with hc4:
                    st.metric("SB", h_data.get("SB", 0))

                if batter_statcast:

                    st.markdown("### 🔥 Advanced Contact Snapshot")
                
                    ac1, ac2, ac3 = st.columns(3)
                
                    with ac1:
                        st.metric("Avg Exit Velocity", f"{batter_statcast.get('avg_ev', 0)} MPH")
                
                    with ac2:
                        st.metric("Hard-Hit %", f"{batter_statcast.get('hard_hit', 0)}%")
                
                    with ac3:
                        st.metric("K Rate", f"{batter_statcast.get('k_rate', 0)}%")
        
                st.markdown("### 📊 Savant-Style Hitting Percentiles")

                games = h_data.get("G", 1) or 1
                
                hr = h_data.get("HR", 0)
                hits = h_data.get("H", 0)
                sb = h_data.get("SB", 0)
                bb = h_data.get("BB", 0)
                so = h_data.get("SO", 0)
                
                power_pct = min(99, max(1, int((hr / max(1, games)) * 400)))
                contact_pct = min(99, max(1, int((hits / max(1, games)) * 70)))
                speed_pct = min(99, max(1, int((sb / max(1, games)) * 500)))
                
                discipline_raw = bb / max(1, so)
                
                discipline_pct = min(99, max(1, int(discipline_raw * 120)))
                
                def hitter_percentile_bar(label, pct):
                
                    if pct >= 85:
                        color = "#d73027"
                
                    elif pct >= 70:
                        color = "#fc8d59"
                
                    elif pct >= 50:
                        color = "#fee08b"
                
                    else:
                        color = "#4575b4"
                
                    st.markdown(f"**{label}: {pct}th percentile**")
                
                    st.markdown(
                        f'''
                        <div style="
                            background-color:#2a2a2a;
                            border-radius:8px;
                            height:18px;
                            width:100%;
                            margin-bottom:18px;
                        ">
                            <div style="
                                background-color:{color};
                                width:{pct}%;
                                height:18px;
                                border-radius:8px;
                            "></div>
                        </div>
                        ''',
                        unsafe_allow_html=True
                    )
                
                hitter_percentile_bar("Power", power_pct)
                hitter_percentile_bar("Contact", contact_pct)
                hitter_percentile_bar("Speed", speed_pct)
                hitter_percentile_bar("Plate Discipline", discipline_pct)
                
                st.dataframe(pd.DataFrame([h_data]), use_container_width=True)
    
    elif page == "🏆 Fantasy Sports Predictor":
        st.title("🏆 Season-Long Fantasy Hub")
        st.markdown("### ⚾ MLB Fantasy Command Center")
        
        fantasy_sport = st.radio("Select Active Fantasy Sport:", ["⚾ MLB Trade Analyzer & Projections"])
        st.markdown("---")
        
        if fantasy_sport == "⚾ MLB Trade Analyzer & Projections":
            st.subheader("⚖️ ESPN Standard Points Trade Analyzer")
            st.caption("Calculates Rest-of-Season (ROS) projections natively via MLB API data logs.")

            with st.spinner("Compiling League-Wide Player Database..."):
                team_stats, p_stats, h_stats = fetch_mlb_api_data()
            
            if not p_stats or not h_stats:
                st.error("🚨 Could not sync with MLB Stats API.")
                
            else:
                
                st.info("MLB Trade Analyzer loaded.")

                st.markdown("---")
                st.markdown("## 🏆 League Command Center")

                cc1, cc2, cc3, cc4 = st.columns(4)

                with cc1:
                    st.metric("Tracked Players", len(h_stats) + len(p_stats))

                with cc2:
                    st.metric("Pitchers", len(p_stats))

                with cc3:
                    st.metric("Batters", len(h_stats))

                with cc4:
                    st.metric("Projection Engine", "ACTIVE")

                st.markdown("### 📊 MLB Power Rankings")

                power_rows = []

                for t_name, vals in team_stats.items():
                    rs = vals.get("RS_per_G", 0)
                    ra = vals.get("RA_per_G", 0)
                    score = round((rs * 2) - ra, 2)

                    power_rows.append({
                        "Team": t_name,
                        "Runs/Game": round(rs, 2),
                        "Runs Allowed/Game": round(ra, 2),
                        "Power Score": score,
                        "Why": "Strong run scoring plus run prevention drives this rank."
                    })

                power_df = pd.DataFrame(power_rows).sort_values(
                    "Power Score",
                    ascending=False
                ).head(10)

                st.bar_chart(power_df[["Team", "Power Score"]].set_index("Team"))

                st.dataframe(power_df, use_container_width=True, hide_index=True)

                st.download_button(
                    "⬇️ Export MLB Power Rankings CSV",
                    data=power_df.to_csv(index=False).encode("utf-8"),
                    file_name="mlb_power_rankings.csv",
                    mime="text/csv"
                )

                st.markdown("### 🧲 Waiver Wire Finder")

                waiver_rows = []

                for player_name, pdata in list(h_stats.items())[:150]:

                    hr = pdata.get("HR", 0)
                    sb = pdata.get("SB", 0)
                    rbi = pdata.get("RBI", 0)

                    upside = (hr * 4) + (sb * 3) + (rbi * 0.5)

                    waiver_rows.append({
                        "Player": player_name,
                        "Team": pdata.get("Team", ""),
                        "Position": pdata.get("Position", "UTIL"),
                        "Upside Score": round(upside, 1),
                        "Why": "Power, speed, and RBI profile create waiver upside."
                    })

                waiver_df = pd.DataFrame(waiver_rows).sort_values(
                    "Upside Score",
                    ascending=False
                ).head(15)

                st.dataframe(waiver_df, use_container_width=True, hide_index=True)

                csv_data = waiver_df.to_csv(index=False).encode("utf-8")

                st.download_button(
                    "⬇️ Export Waiver Targets CSV",
                    data=csv_data,
                    file_name="mlb_waiver_targets.csv",
                    mime="text/csv"
                )

                st.markdown("### 🤝 Trade Value Board")

                trade_rows = []

                for player_name, pdata in list(h_stats.items())[:200]:

                    games = max(1, pdata.get("G", 1))

                    trade_value = (
                        pdata.get("HR", 0) * 5
                        + pdata.get("RBI", 0) * 1.2
                        + pdata.get("SB", 0) * 4
                        + pdata.get("H", 0) * 0.6
                    ) / games

                    trade_rows.append({
                        "Player": player_name,
                        "Team": pdata.get("Team", ""),
                        "Position": pdata.get("Position", "UTIL"),
                        "Trade Value": round(trade_value, 2),
                        "Why": "Per-game production profile is strong for fantasy trade value."
                    })

                trade_df = pd.DataFrame(trade_rows).sort_values(
                    "Trade Value",
                    ascending=False
                ).head(25)

                st.dataframe(trade_df, use_container_width=True, hide_index=True)

                st.download_button(
                    "⬇️ Export MLB Trade Value Board CSV",
                    data=trade_df.to_csv(index=False).encode("utf-8"),
                    file_name="mlb_trade_value_board.csv",
                    mime="text/csv"
                )

                st.markdown("---")


                st.markdown("---")
                st.markdown("### 🔮 MLB Fantasy Projection Lab")
    
                projection_type = st.radio(
                    "Projection Type:",
                    ["Batter", "Pitcher"],
                    horizontal=True
                )

                st.markdown("---")
                st.markdown("## 🔥 Top Fantasy Projections")

                hitter_board = []

                for hitter_name, hitter_data in h_stats.items():

                    games = hitter_data.get("G", 1) or 1

                    fantasy_ppg = (
                        (hitter_data.get("H", 0) * 1)
                        + (hitter_data.get("2B", 0) * 2)
                        + (hitter_data.get("3B", 0) * 3)
                        + (hitter_data.get("HR", 0) * 6)
                        + (hitter_data.get("R", 0) * 2)
                        + (hitter_data.get("RBI", 0) * 2)
                        + (hitter_data.get("BB", 0) * 1)
                        + (hitter_data.get("SB", 0) * 5)
                        - (hitter_data.get("SO", 0) * 0.5)
                    ) / games

                    team = hitter_data.get("Team", "N/A")

                    park_factor = PARK_FACTORS.get(team, 100) / 100

                    if fantasy_ppg >= 7:
                        lineup_factor = 1.08
                    elif fantasy_ppg >= 5:
                        lineup_factor = 1.04
                    elif fantasy_ppg >= 3:
                        lineup_factor = 1.00
                    else:
                        lineup_factor = 0.94
                    
                    proj = fantasy_ppg * 1.08 * park_factor * lineup_factor

                    hitter_board.append({
                        "Player": hitter_name,
                        "Team": team,
                        "Position": hitter_data.get("Position", "UTIL"),
                        "Lineup Factor": round(lineup_factor, 2),
                        "Proj Points": round(proj, 1)
                    })

                hitter_df = pd.DataFrame(hitter_board)

                hitter_df = hitter_df.sort_values(
                    by="Proj Points",
                    ascending=False
                )

                position_filter = st.selectbox(
                    "Filter hitters by position:",
                    ["All"] + sorted(hitter_df["Position"].dropna().unique().tolist())
                )
                
                if position_filter != "All":
                    hitter_df = hitter_df[hitter_df["Position"] == position_filter]
                
                player_search = st.text_input("Search projected hitters:")

                if player_search:
                    hitter_df = hitter_df[
                        hitter_df["Player"].str.contains(player_search, case=False, na=False)
                    ]
                    
                hitter_df = hitter_df.reset_index(drop=True)

                st.dataframe(
                    hitter_df,
                    use_container_width=True,
                    hide_index=True
                )

                pitcher_board = []

                for pitcher_name, pitcher_data in p_stats.items():
                
                    games = pitcher_data.get("G", 1) or 1
                
                    fantasy_ppg = (
                        (pitcher_data.get("IP", 0) * 3)
                        + pitcher_data.get("K", 0)
                        + (pitcher_data.get("W", 0) * 5)
                        + (pitcher_data.get("SV", 0) * 5)
                        - (pitcher_data.get("L", 0) * 5)
                        - (pitcher_data.get("ER", 0) * 2)
                        - pitcher_data.get("H", 0)
                        - pitcher_data.get("BB", 0)
                    ) / games
                
                    team = pitcher_data.get("Team", "N/A")
                    park_factor = PARK_FACTORS.get(team, 100) / 100
                
                    proj = fantasy_ppg * 1.05 * (2 - park_factor)
                
                    pitcher_board.append({
                        "Player": pitcher_name,
                        "Proj Points": round(proj, 1)
                    })
                
                pitcher_df = pd.DataFrame(pitcher_board)
                
                pitcher_df = pitcher_df.sort_values(
                    by="Proj Points",
                    ascending=False
                )

                pitcher_df = pitcher_df.reset_index(drop=True)
                
                st.markdown("### ⚾ Top Pitcher Projections")

                pitcher_search = st.text_input("Search projected pitchers:")

                if pitcher_search:
                    pitcher_df = pitcher_df[
                        pitcher_df["Player"].str.contains(pitcher_search, case=False, na=False)
                    ]
                st.dataframe(
                    pitcher_df,
                    use_container_width=True,
                    hide_index=True
                )
            
        elif fantasy_sport == "🏈 NFL Sleeper PPR Trade Engine":
            st.subheader("🏈 NFL Sleeper PPR Command Center")
            st.caption("Sleeper sync, player values, trade calculator, roster power rankings, league analysis, and saved trade history.")

            DATA_DIR = "haglabs_data"
            TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "nfl_trade_history.csv")
            SAVED_LEAGUES_FILE = os.path.join(DATA_DIR, "nfl_saved_leagues.csv")

            os.makedirs(DATA_DIR, exist_ok=True)

            @st.cache_data(ttl=CACHE_TTL_DAILY)
            def load_sleeper_players_full():
                try:
                    url = "https://api.sleeper.app/v1/players/nfl"
                    resp = requests.get(url, timeout=25).json()

                    rows = []
                    lookup = {}

                    for pid, pdata in resp.items():
                        pos = pdata.get("position") or "UNK"

                        if pos not in ["QB", "RB", "WR", "TE", "K", "DEF"]:
                            continue

                        first = pdata.get("first_name") or ""
                        last = pdata.get("last_name") or ""
                        name = f"{first} {last}".strip()

                        if not name:
                            name = pdata.get("full_name") or pdata.get("search_full_name") or str(pid)

                        age = pdata.get("age")
                        team = pdata.get("team", "FA") or "FA"
                        years_exp = pdata.get("years_exp") or 0
                        status = pdata.get("status") or "Unknown"
                        active = bool(pdata.get("active", False))

                        row = {
                            "Player ID": str(pid),
                            "Player": name,
                            "Position": pos,
                            "Team": team,
                            "Age": age,
                            "Experience": years_exp,
                            "Status": status,
                            "Active": active,
                        }

                        rows.append(row)
                        lookup[str(pid)] = row

                    df = pd.DataFrame(rows)

                    if not df.empty:
                        df = df.sort_values(["Position", "Player"]).reset_index(drop=True)

                    return df, lookup

                except Exception:
                    return pd.DataFrame(), {}

            def safe_float(value, default=0.0):
                try:
                    if value is None or pd.isna(value):
                        return default
                    return float(value)
                except Exception:
                    return default

            def nfl_base_projection(position, age=None, experience=0, active=True):
                base = {
                    "QB": 17.5,
                    "RB": 11.5,
                    "WR": 10.5,
                    "TE": 7.5,
                    "K": 7.0,
                    "DEF": 7.0,
                }.get(position, 4.0)

                exp = safe_float(experience, 0)
                age_val = safe_float(age, 0)

                if exp <= 1:
                    base *= 0.88
                elif exp <= 3:
                    base *= 1.02
                elif exp >= 8:
                    base *= 0.94

                if age_val:
                    if position == "RB" and age_val >= 29:
                        base *= 0.88
                    elif position in ["WR", "TE"] and age_val >= 31:
                        base *= 0.90
                    elif position == "QB" and age_val >= 36:
                        base *= 0.92

                if not active:
                    base *= 0.60

                return round(base, 2)

            def calculate_age_modifier(position, age):
                age_val = safe_float(age, 0)

                if age_val <= 0:
                    return 1.00

                prime_age = {
                    "QB": 32,
                    "RB": 26,
                    "WR": 28,
                    "TE": 29,
                    "K": 34,
                    "DEF": 30,
                }.get(position, 28)

                if age_val <= prime_age:
                    return 1.00

                decline = (age_val - prime_age) * 0.025
                return max(0.78, 1.00 - decline)

            def calculate_redraft_value(position, age, projected_ppr):
                scarcity = {
                    "QB": 0.92,
                    "RB": 1.20,
                    "WR": 1.08,
                    "TE": 1.15,
                    "K": 0.45,
                    "DEF": 0.45,
                }.get(position, 1.0)

                value = projected_ppr * scarcity
                return round(value, 1)

            def calculate_dynasty_value(position, age, projected_ppr):
                redraft = calculate_redraft_value(position, age, projected_ppr)
                age_mod = calculate_age_modifier(position, age)

                youth_bonus = 1.00
                age_val = safe_float(age, 0)

                if age_val:
                    if position == "RB" and age_val <= 24:
                        youth_bonus = 1.18
                    elif position in ["WR", "TE"] and age_val <= 25:
                        youth_bonus = 1.14
                    elif position == "QB" and age_val <= 27:
                        youth_bonus = 1.10

                return round(redraft * age_mod * youth_bonus, 1)

            def dynasty_tag(position, age):
                age_val = safe_float(age, 0)

                if age_val <= 0:
                    return "Unknown"

                if position == "RB":
                    if age_val <= 24:
                        return "Young Core"
                    if age_val >= 29:
                        return "Decline Risk"
                    return "Prime Window"

                if position in ["WR", "TE"]:
                    if age_val <= 25:
                        return "Young Core"
                    if age_val >= 31:
                        return "Decline Risk"
                    return "Prime Window"

                if position == "QB":
                    if age_val <= 27:
                        return "Young Core"
                    if age_val >= 36:
                        return "Late Career"
                    return "Stable Asset"

                return "Standard"

            def build_player_values(players_df, value_mode):
                if players_df.empty:
                    return pd.DataFrame()

                df = players_df.copy()
                df = df[df["Position"].isin(["QB", "RB", "WR", "TE"])].copy()

                if df.empty:
                    return df

                df["Projected PPR"] = df.apply(
                    lambda r: nfl_base_projection(
                        r.get("Position"),
                        r.get("Age"),
                        r.get("Experience"),
                        r.get("Active", True),
                    ),
                    axis=1,
                )

                df["Redraft Value"] = df.apply(
                    lambda r: calculate_redraft_value(
                        r.get("Position"),
                        r.get("Age"),
                        r.get("Projected PPR"),
                    ),
                    axis=1,
                )

                df["Dynasty Value"] = df.apply(
                    lambda r: calculate_dynasty_value(
                        r.get("Position"),
                        r.get("Age"),
                        r.get("Projected PPR"),
                    ),
                    axis=1,
                )

                df["Trade Value"] = np.where(
                    value_mode == "Dynasty",
                    df["Dynasty Value"],
                    df["Redraft Value"],
                )

                df["Dynasty Tag"] = df.apply(
                    lambda r: dynasty_tag(r.get("Position"), r.get("Age")),
                    axis=1,
                )

                df["Value Tier"] = pd.cut(
                    df["Trade Value"],
                    bins=[-1, 5, 9, 13, 17, 100],
                    labels=["Depth", "Flex", "Starter", "High-End", "Elite"],
                )

                return df.sort_values("Trade Value", ascending=False).reset_index(drop=True)

            def trade_side_value(selected_players, values_df):
                if not selected_players or values_df.empty:
                    return 0.0

                selected_names = [p.split(" (")[0] for p in selected_players]
                side_df = values_df[values_df["Player"].isin(selected_names)]

                return round(float(side_df["Trade Value"].sum()), 1)

            def save_trade_history(row):
                history_df = pd.DataFrame([row])

                if os.path.exists(TRADE_HISTORY_FILE):
                    existing = pd.read_csv(TRADE_HISTORY_FILE)
                    history_df = pd.concat([existing, history_df], ignore_index=True)

                history_df.to_csv(TRADE_HISTORY_FILE, index=False)

            def save_league(username, league_id, league_name):
                row = {
                    "Saved At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Username": username,
                    "League ID": league_id,
                    "League Name": league_name,
                }

                saved_df = pd.DataFrame([row])

                if os.path.exists(SAVED_LEAGUES_FILE):
                    existing = pd.read_csv(SAVED_LEAGUES_FILE)
                    existing = existing[existing["League ID"].astype(str) != str(league_id)]
                    saved_df = pd.concat([existing, saved_df], ignore_index=True)

                saved_df.to_csv(SAVED_LEAGUES_FILE, index=False)

            def get_player_row(player_id, player_lookup):
                return player_lookup.get(str(player_id), {})

            def roster_value_summary(roster_players, player_lookup, values_df):
                rows = []

                for pid in roster_players or []:
                    p = get_player_row(pid, player_lookup)

                    if not p:
                        continue

                    match = values_df[values_df["Player ID"].astype(str) == str(pid)]

                    if match.empty:
                        continue

                    value_row = match.iloc[0].to_dict()
                    rows.append(value_row)

                if not rows:
                    return {
                        "Roster Value": 0.0,
                        "QB Value": 0.0,
                        "RB Value": 0.0,
                        "WR Value": 0.0,
                        "TE Value": 0.0,
                        "Weakest Position": "Unknown",
                        "Top Players": "",
                        "Roster Rows": pd.DataFrame(),
                    }

                roster_df = pd.DataFrame(rows)

                if not roster_df.empty and "Position" in roster_df.columns and "Trade Value" in roster_df.columns:
                    roster_df["Position Rank"] = (
                        roster_df.groupby("Position")["Trade Value"]
                        .rank(method="first", ascending=False)
                        .astype(int)
                    )
                    roster_df["Roster Role"] = roster_df.apply(
                        lambda r: label_roster_role(
                            r.get("Position", ""),
                            int(r.get("Position Rank", 99)),
                            float(r.get("Trade Value", 0)),
                        ),
                        axis=1,
                    )
                    roster_df["Why"] = roster_df.apply(explain_player_value, axis=1)

                position_values = {
                    "QB": round(float(roster_df[roster_df["Position"] == "QB"]["Trade Value"].sum()), 1),
                    "RB": round(float(roster_df[roster_df["Position"] == "RB"]["Trade Value"].sum()), 1),
                    "WR": round(float(roster_df[roster_df["Position"] == "WR"]["Trade Value"].sum()), 1),
                    "TE": round(float(roster_df[roster_df["Position"] == "TE"]["Trade Value"].sum()), 1),
                }

                weakest = min(position_values, key=position_values.get)
                top_players = ", ".join(roster_df.sort_values("Trade Value", ascending=False)["Player"].head(5).tolist())

                return {
                    "Roster Value": round(float(roster_df["Trade Value"].sum()), 1),
                    "QB Value": position_values["QB"],
                    "RB Value": position_values["RB"],
                    "WR Value": position_values["WR"],
                    "TE Value": position_values["TE"],
                    "Weakest Position": weakest,
                    "Top Players": top_players,
                    "Roster Rows": roster_df,
                }

            def label_team_status(roster_value, rank, league_size):
                if league_size <= 0:
                    return "Unknown"

                if rank <= max(2, int(league_size * 0.25)):
                    return "Contender"
                if rank >= max(1, int(league_size * 0.75)):
                    return "Rebuilder"
                if roster_value <= 0:
                    return "Incomplete"
                return "Middle Pack"

            def explain_team_rank(row):
                status = row.get("Team Status", "Unknown")
                weakness = row.get("Weakest Position", "Unknown")
                top_players = row.get("Top Players", "")
                if status == "Contender":
                    return f"High total roster value with enough core strength to buy a {weakness} upgrade."
                if status == "Rebuilder":
                    return f"Lower roster value; strongest path is selling vets and rebuilding around {top_players}."
                if status == "Incomplete":
                    return "Roster data is incomplete or missing value inputs."
                return f"Middle-pack roster; biggest improvement path is fixing {weakness}."

            def explain_trade_partner(need, partner_name, partner_strength):
                return f"{partner_name} has above-league strength at {need}, making them a logical trade match."

            def label_roster_role(position, pos_rank, trade_value):
                if trade_value <= 0:
                    return "Depth"
                if pos_rank <= 1:
                    return "Core Starter"
                if pos_rank <= 3:
                    return "Starter/Flex"
                return "Depth"

            def explain_player_value(row):
                tag = str(row.get("Dynasty Tag", ""))
                tier = str(row.get("Value Tier", ""))
                age = row.get("Age", 0)
                pos = row.get("Position", "")
                if tag in ["Young Core", "Prime Asset"]:
                    return f"{pos} with dynasty-friendly age/value profile."
                if tag in ["Declining Vet", "Late Career", "Decline Risk"]:
                    return f"{pos} has win-now utility but age risk lowers long-term value."
                if tier == "Elite":
                    return "Elite projection tier with strong trade-market value."
                return "Useful value profile based on projection, age, and position."

            with st.spinner("Loading Sleeper player registry..."):
                sleeper_players_df, sleeper_lookup = load_sleeper_players_full()

            value_mode = st.radio(
                "Value Mode:",
                ["Redraft", "Dynasty"],
                horizontal=True,
                key="nfl_value_mode",
            )

            st.info(
                "Value Guide: Projected PPR is an estimated weekly PPR score. "
                "Redraft Value is win-now value. Dynasty Value adds age/position adjustments. "
                "Trade Value uses the selected mode above."
            )

            player_values_df = build_player_values(sleeper_players_df, value_mode)

            nfl_tab0, nfl_tab1, nfl_tab2, nfl_tab3, nfl_tab4, nfl_tab5, nfl_tab6, nfl_tab7 = st.tabs(
                [
                    "🏟️ Command Center",
                    "📊 Player Values",
                    "🃏 Player Cards",
                    "⚖️ Trade Builder V2",
                    "🔗 Sleeper League Sync",
                    "🧠 League AI",
                    "🧲 Waiver Finder",
                    "💾 Saved Reports",
                ]
            )

            with nfl_tab0:
                st.markdown("### 🏟️ League Command Center")

                power_df = st.session_state.get("nfl_power_df", pd.DataFrame())
                player_rec_df = st.session_state.get("nfl_player_rec_df", pd.DataFrame())
                rec_df = st.session_state.get("nfl_rec_df", pd.DataFrame())

                if power_df.empty:
                    st.info("Sync a Sleeper league first in the Sleeper League Sync tab.")
                else:
                    top_team = power_df.iloc[0]
                    bottom_team = power_df.iloc[-1]
                    league_avg = round(float(power_df["Roster Value"].mean()), 1)

                    cc1, cc2, cc3, cc4 = st.columns(4)

                    with cc1:
                        st.metric("League Leader", top_team["Team/User"], top_team["Roster Value"])

                    with cc2:
                        st.metric("League Average", league_avg)

                    with cc3:
                        st.metric("Best Buy-Low Team", bottom_team["Team/User"], bottom_team["Weakest Position"])

                    with cc4:
                        st.metric("Teams Synced", len(power_df))

                    st.markdown("#### Visual League Charts")

                    chart_col1, chart_col2 = st.columns(2)

                    with chart_col1:
                        roster_chart_df = power_df[["Team/User", "Roster Value"]].set_index("Team/User")
                        st.bar_chart(roster_chart_df)

                    with chart_col2:
                        position_chart_df = power_df[
                            ["Team/User", "QB Value", "RB Value", "WR Value", "TE Value"]
                        ].set_index("Team/User")
                        st.bar_chart(position_chart_df)

                    st.markdown("#### Power Snapshot")
                    st.dataframe(
                        power_df[
                            [
                                "Rank",
                                "Team/User",
                                "Roster Value",
                                "Weakest Position",
                                "Team Status",
                                "Top Players",
                            ]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.markdown("#### Top Action Items")

                    action_rows = []

                    for _, row in power_df.iterrows():
                        team_name = row["Team/User"]
                        weakness = row["Weakest Position"]
                        status = row["Team Status"]

                        if status == "Contender":
                            action = f"Buy a {weakness} upgrade for a playoff push."
                        elif status == "Rebuilder":
                            action = "Sell older assets and collect youth/picks."
                        else:
                            action = f"Pick a direction and patch {weakness} first."

                        action_rows.append(
                            {
                                "Team": team_name,
                                "Status": status,
                                "Primary Need": weakness,
                                "Recommended Action": action,
                                "Why": f"{team_name} is labeled {status} and currently needs {weakness} most.",
                            }
                        )

                    action_df = pd.DataFrame(action_rows)
                    st.dataframe(action_df, use_container_width=True, hide_index=True)

                    if not player_rec_df.empty:
                        st.markdown("#### Best Player-Level Trade Ideas")
                        st.dataframe(
                            player_rec_df.head(15),
                            use_container_width=True,
                            hide_index=True,
                        )

                    report_parts = [power_df.to_csv(index=False)]

                    if not player_rec_df.empty:
                        report_parts.append("\n\nPLAYER TRADE IDEAS\n")
                        report_parts.append(player_rec_df.to_csv(index=False))

                    if not rec_df.empty:
                        report_parts.append("\n\nTEAM TRADE PARTNERS\n")
                        report_parts.append(rec_df.to_csv(index=False))

                    st.download_button(
                        "⬇️ Download Full Command Center Report CSV",
                        data="".join(report_parts).encode("utf-8"),
                        file_name="nfl_command_center_report.csv",
                        mime="text/csv",
                        key="download_command_center_report_csv",
                    )

            with nfl_tab1:
                st.markdown("### 📊 NFL Player Values")

                if player_values_df.empty:
                    st.warning("Sleeper players did not load. Try refreshing the page.")
                else:
                    pos_filter = st.selectbox(
                        "Position Filter:",
                        ["All", "QB", "RB", "WR", "TE", "K"],
                        key="nfl_values_pos_filter",
                    )

                    search_text = st.text_input("Search Player:", key="nfl_values_search")

                    display_df = player_values_df.copy()

                    if pos_filter != "All":
                        display_df = display_df[display_df["Position"] == pos_filter]

                    if search_text:
                        display_df = display_df[
                            display_df["Player"].str.contains(search_text, case=False, na=False)
                        ]

                    display_df = display_df.copy()
                    display_df["Why"] = display_df.apply(explain_player_value, axis=1)

                    st.markdown("#### Redraft vs Dynasty Value Gap")
                    gap_chart_df = display_df.sort_values("Value Gap", ascending=False).head(15)[
                        ["Player", "Value Gap"]
                    ].set_index("Player")
                    st.bar_chart(gap_chart_df)

                    st.dataframe(
                        display_df[
                            [
                                "Player",
                                "Team",
                                "Position",
                                "Age",
                                "Projected PPR",
                                "Redraft Value",
                                "Dynasty Value",
                                "Value Gap",
                                "Trade Value",
                                "Value Tier",
                                "Dynasty Tag",
                                "Why",
                            ]
                        ].head(250),
                        use_container_width=True,
                        hide_index=True,
                    )

            with nfl_tab2:
                st.markdown("### 🃏 Player Cards")

                if player_values_df.empty:
                    st.warning("Player values are unavailable.")
                else:
                    card_options = [
                        f"{r['Player']} ({r['Position']} - {r['Team']})"
                        for _, r in player_values_df.iterrows()
                    ]

                    selected_card = st.selectbox(
                        "Select Player:",
                        card_options,
                        key="nfl_player_card_select",
                    )

                    selected_name = selected_card.split(" (")[0]
                    card_df = player_values_df[player_values_df["Player"] == selected_name]

                    if not card_df.empty:
                        card = card_df.iloc[0]

                        c1, c2, c3, c4 = st.columns(4)

                        with c1:
                            st.metric("Trade Value", card["Trade Value"])

                        with c2:
                            st.metric("Projected PPR", card["Projected PPR"])

                        with c3:
                            st.metric("Age", card["Age"])

                        with c4:
                            st.metric("Tier", card["Value Tier"])

                        st.markdown("#### Player Snapshot")
                        st.caption(explain_player_value(card))
                        st.dataframe(
                            pd.DataFrame(
                                [
                                    {
                                        "Player": card["Player"],
                                        "Team": card["Team"],
                                        "Position": card["Position"],
                                        "Age": card["Age"],
                                        "Redraft Value": card["Redraft Value"],
                                        "Dynasty Value": card["Dynasty Value"],
                                        "Trade Value": card["Trade Value"],
                                        "Dynasty Tag": card["Dynasty Tag"],
                                        "Status": card["Status"],
                                        "Experience": card["Experience"],
                                    }
                                ]
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )

                        tag = str(card["Dynasty Tag"])

                        if tag in ["Young Core", "Stable Asset"]:
                            st.success("Suggested use: hold or buy unless the offer is clearly above market.")
                        elif tag in ["Decline Risk", "Late Career"]:
                            st.warning("Suggested use: consider selling if your team is not contending.")
                        else:
                            st.info("Suggested use: value depends on roster direction and league need.")

            with nfl_tab3:
                st.markdown("### ⚖️ Trade Builder V2")

                if player_values_df.empty:
                    st.warning("Player values are unavailable.")
                else:
                    player_options = [
                        f"{r['Player']} ({r['Position']} - {r['Team']})"
                        for _, r in player_values_df.iterrows()
                    ]

                    trade_col1, trade_col2 = st.columns(2)

                    with trade_col1:
                        team_a_assets = st.multiselect(
                            "Team A Receives:",
                            player_options,
                            key="trade_team_a_assets",
                        )

                    with trade_col2:
                        team_b_assets = st.multiselect(
                            "Team B Receives:",
                            player_options,
                            key="trade_team_b_assets",
                        )

                    pick_values = {
                        "None": 0.0,
                        "2025 1st": 14.0,
                        "2025 2nd": 7.0,
                        "2025 3rd": 3.0,
                        "2026 1st": 12.0,
                        "2026 2nd": 6.0,
                        "2026 3rd": 2.5,
                    }

                    pick_col1, pick_col2 = st.columns(2)

                    with pick_col1:
                        team_a_picks = st.multiselect(
                            "Extra picks/assets Team A receives:",
                            list(pick_values.keys()),
                            default=["None"],
                            key="trade_team_a_picks",
                        )

                    with pick_col2:
                        team_b_picks = st.multiselect(
                            "Extra picks/assets Team B receives:",
                            list(pick_values.keys()),
                            default=["None"],
                            key="trade_team_b_picks",
                        )

                    team_a_pick_value = round(sum(pick_values.get(p, 0.0) for p in team_a_picks if p != "None"), 1)
                    team_b_pick_value = round(sum(pick_values.get(p, 0.0) for p in team_b_picks if p != "None"), 1)

                    team_a_value = round(trade_side_value(team_a_assets, player_values_df) + team_a_pick_value, 1)
                    team_b_value = round(trade_side_value(team_b_assets, player_values_df) + team_b_pick_value, 1)
                    value_gap = round(team_a_value - team_b_value, 1)

                    metric_col1, metric_col2, metric_col3 = st.columns(3)

                    with metric_col1:
                        st.metric("Team A Receives", team_a_value)

                    with metric_col2:
                        st.metric("Team B Receives", team_b_value)

                    with metric_col3:
                        st.metric("Gap", value_gap)

                    fairness_score = max(0, min(100, int(100 - (abs(value_gap) * 8))))
                    st.progress(fairness_score / 100)
                    st.caption(f"Fairness Meter: {fairness_score}/100")

                    if abs(value_gap) <= 3:
                        st.success("Balanced trade.")
                    elif abs(value_gap) <= 8:
                        st.warning("Close, but one side should add a small asset.")
                    elif value_gap > 8:
                        st.error("Team A receives much more value.")
                    else:
                        st.error("Team B receives much more value.")

                    notes = st.text_area("Trade Notes:", key="trade_notes")

                    if st.button("💾 Save Trade", key="save_trade_button"):
                        save_trade_history(
                            {
                                "Saved At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "Mode": value_mode,
                                "Team A Receives": " | ".join(team_a_assets + team_a_picks),
                                "Team B Receives": " | ".join(team_b_assets + team_b_picks),
                                "Team A Value": team_a_value,
                                "Team B Value": team_b_value,
                                "Gap": value_gap,
                                "Notes": notes,
                            }
                        )
                        st.success("Trade saved.")

            with nfl_tab4:
                st.markdown("### 🔗 Sleeper League Sync")

                sync_top_col1, sync_top_col2 = st.columns([3, 1])

                with sync_top_col1:
                    st.caption(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")

                with sync_top_col2:
                    if st.button("🔄 Refresh NFL Data", key="refresh_nfl_data_button"):
                        st.cache_data.clear()
                        st.rerun()

                default_sleeper_username = "marcusmaximus06"

                if os.path.exists(SAVED_LEAGUES_FILE):
                    try:
                        saved_leagues_preview = pd.read_csv(SAVED_LEAGUES_FILE)

                        if not saved_leagues_preview.empty and "Username" in saved_leagues_preview.columns:
                            default_sleeper_username = str(saved_leagues_preview.iloc[0]["Username"])
                    except Exception:
                        pass

                sleeper_username = st.text_input(
                    "Sleeper Username:",
                    value=default_sleeper_username,
                    key="sleeper_username_main",
                )

                season = st.selectbox(
                    "Season:",
                    ["2025", "2024", "2026"],
                    key="sleeper_season_select",
                )

                saved_league_hint = None

                if os.path.exists(SAVED_LEAGUES_FILE):
                    try:
                        saved_leagues_hint_df = pd.read_csv(SAVED_LEAGUES_FILE)

                        if not saved_leagues_hint_df.empty:
                            saved_labels = [
                                f"{row['League Name']} | {row['Username']}"
                                for _, row in saved_leagues_hint_df.iterrows()
                            ]

                            selected_saved_label = st.selectbox(
                                "Saved League Shortcut:",
                                ["Manual / Current Username"] + saved_labels,
                                key="saved_league_shortcut_select",
                            )

                            if selected_saved_label != "Manual / Current Username":
                                saved_index = saved_labels.index(selected_saved_label)
                                saved_league_hint = saved_leagues_hint_df.iloc[saved_index].to_dict()
                                st.caption(f"Saved league selected: {saved_league_hint.get('League Name')}")
                    except Exception:
                        saved_league_hint = None

                if sleeper_username:
                    try:
                        user_resp = requests.get(
                            f"https://api.sleeper.app/v1/user/{sleeper_username}",
                            timeout=15,
                        ).json()

                        sleeper_user_id = user_resp.get("user_id")

                        if not sleeper_user_id:
                            st.warning("Sleeper username not found.")
                        else:
                            leagues_resp = requests.get(
                                f"https://api.sleeper.app/v1/user/{sleeper_user_id}/leagues/nfl/{season}",
                                timeout=15,
                            ).json()

                            if not leagues_resp:
                                st.warning("No Sleeper leagues found for this username/season.")
                            else:
                                league_options = {
                                    league.get("name", "Unnamed League"): league.get("league_id")
                                    for league in leagues_resp
                                }

                                league_names = list(league_options.keys())
                                default_league_index = 0

                                if saved_league_hint:
                                    saved_league_name = saved_league_hint.get("League Name")

                                    if saved_league_name in league_names:
                                        default_league_index = league_names.index(saved_league_name)

                                selected_league_name = st.selectbox(
                                    "Select League:",
                                    league_names,
                                    index=default_league_index,
                                    key="selected_sleeper_league",
                                )

                                selected_league_id = league_options[selected_league_name]

                                save_col1, save_col2 = st.columns([1, 3])

                                with save_col1:
                                    if st.button("💾 Save League", key="save_sleeper_league"):
                                        save_league(
                                            sleeper_username,
                                            selected_league_id,
                                            selected_league_name,
                                        )
                                        st.success("League saved.")

                                rosters_resp = requests.get(
                                    f"https://api.sleeper.app/v1/league/{selected_league_id}/rosters",
                                    timeout=15,
                                ).json()

                                users_resp = requests.get(
                                    f"https://api.sleeper.app/v1/league/{selected_league_id}/users",
                                    timeout=15,
                                ).json()

                                user_map = {
                                    u.get("user_id"): u.get("display_name", "Unknown")
                                    for u in users_resp
                                }

                                roster_rows = []
                                detail_rosters = {}

                                for roster in rosters_resp:
                                    owner_id = roster.get("owner_id")
                                    owner_name = user_map.get(owner_id, "Unknown")
                                    roster_players = roster.get("players") or []

                                    summary = roster_value_summary(
                                        roster_players,
                                        sleeper_lookup,
                                        player_values_df,
                                    )

                                    detail_rosters[owner_name] = summary["Roster Rows"]

                                    roster_rows.append(
                                        {
                                            "Team/User": owner_name,
                                            "Roster ID": roster.get("roster_id"),
                                            "Players": len(roster_players),
                                            "Roster Value": summary["Roster Value"],
                                            "QB Value": summary["QB Value"],
                                            "RB Value": summary["RB Value"],
                                            "WR Value": summary["WR Value"],
                                            "TE Value": summary["TE Value"],
                                            "Weakest Position": summary["Weakest Position"],
                                            "Wins": roster.get("settings", {}).get("wins", 0),
                                            "Losses": roster.get("settings", {}).get("losses", 0),
                                            "Ties": roster.get("settings", {}).get("ties", 0),
                                            "Top Players": summary["Top Players"],
                                        }
                                    )

                                power_df = pd.DataFrame(roster_rows)

                                if not power_df.empty:
                                    power_df = power_df.sort_values(
                                        "Roster Value",
                                        ascending=False,
                                    ).reset_index(drop=True)

                                    power_df["Rank"] = range(1, len(power_df) + 1)
                                    league_size = len(power_df)

                                    power_df["Team Status"] = power_df.apply(
                                        lambda r: label_team_status(
                                            r["Roster Value"],
                                            r["Rank"],
                                            league_size,
                                        ),
                                        axis=1,
                                    )

                                    power_df["Why Ranked"] = power_df.apply(explain_team_rank, axis=1)

                                    st.markdown("### 🏆 Team Power Rankings")
                                    power_display_df = power_df[
                                        [
                                            "Rank",
                                            "Team/User",
                                            "Roster Value",
                                            "QB Value",
                                            "RB Value",
                                            "WR Value",
                                            "TE Value",
                                            "Weakest Position",
                                            "Team Status",
                                            "Why Ranked",
                                            "Wins",
                                            "Losses",
                                            "Top Players",
                                        ]
                                    ]

                                    st.dataframe(
                                        power_display_df,
                                        use_container_width=True,
                                        hide_index=True,
                                    )

                                    st.download_button(
                                        "⬇️ Download Power Rankings CSV",
                                        data=power_display_df.to_csv(index=False).encode("utf-8"),
                                        file_name=f"{selected_league_name}_power_rankings.csv",
                                        mime="text/csv",
                                        key="download_power_rankings_csv",
                                    )

                                    st.session_state["nfl_power_df"] = power_df
                                    st.session_state["nfl_detail_rosters"] = detail_rosters

                                    rostered_player_ids = set()
                                    for detail_df in detail_rosters.values():
                                        if not detail_df.empty and "Player ID" in detail_df.columns:
                                            rostered_player_ids.update(detail_df["Player ID"].astype(str).tolist())

                                    st.session_state["nfl_rostered_player_ids"] = rostered_player_ids

                                    selected_team = st.selectbox(
                                        "View Roster Details:",
                                        list(detail_rosters.keys()),
                                        key="selected_roster_detail",
                                    )

                                    roster_detail_df = detail_rosters.get(selected_team, pd.DataFrame())

                                    if not roster_detail_df.empty:
                                        roster_export_df = roster_detail_df[
                                            [
                                                "Player",
                                                "Team",
                                                "Position",
                                                "Age",
                                                "Projected PPR",
                                                "Redraft Value",
                                                "Dynasty Value",
                                                "Trade Value",
                                                "Dynasty Tag",
                                                "Roster Role",
                                                "Why",
                                            ]
                                        ].sort_values("Trade Value", ascending=False)

                                        st.dataframe(
                                            roster_export_df,
                                            use_container_width=True,
                                            hide_index=True,
                                        )

                                        st.download_button(
                                            "⬇️ Download Selected Roster CSV",
                                            data=roster_export_df.to_csv(index=False).encode("utf-8"),
                                            file_name=f"{selected_team}_roster_values.csv",
                                            mime="text/csv",
                                            key="download_selected_roster_csv",
                                        )

                                    st.markdown("### 🤝 Trade Partner Targets")

                                    rec_rows = []

                                    for _, team_a in power_df.iterrows():
                                        need = team_a["Weakest Position"]

                                        for _, team_b in power_df.iterrows():
                                            if team_a["Team/User"] == team_b["Team/User"]:
                                                continue

                                            strength_col = f"{need} Value"
                                            partner_strength = team_b.get(strength_col, 0)

                                            if partner_strength >= power_df[strength_col].median():
                                                rec_rows.append(
                                                    {
                                                        "Team Needing Help": team_a["Team/User"],
                                                        "Weak Position": need,
                                                        "Suggested Partner": team_b["Team/User"],
                                                        "Partner Strength": round(float(partner_strength), 1),
                                                        "Why Suggested": explain_trade_partner(
                                                            need,
                                                            team_b["Team/User"],
                                                            round(float(partner_strength), 1),
                                                        ),
                                                    }
                                                )

                                    rec_df = pd.DataFrame(rec_rows)

                                    if not rec_df.empty:
                                        rec_df = rec_df.sort_values(
                                            "Partner Strength",
                                            ascending=False,
                                        ).head(25)

                                        st.session_state["nfl_rec_df"] = rec_df

                                        st.dataframe(
                                            rec_df,
                                            use_container_width=True,
                                            hide_index=True,
                                        )

                                        st.download_button(
                                            "⬇️ Download Trade Partner Suggestions CSV",
                                            data=rec_df.to_csv(index=False).encode("utf-8"),
                                            file_name=f"{selected_league_name}_trade_partner_suggestions.csv",
                                            mime="text/csv",
                                            key="download_trade_partner_suggestions_csv",
                                        )

                                    st.markdown("### 🎯 Player-Level Trade Ideas")

                                    player_rec_rows = []

                                    for _, team_a in power_df.iterrows():
                                        team_a_name = team_a["Team/User"]
                                        need = team_a["Weakest Position"]

                                        team_a_roster = detail_rosters.get(team_a_name, pd.DataFrame())

                                        if team_a_roster.empty:
                                            continue

                                        asset_pool = team_a_roster[
                                            team_a_roster["Position"] != need
                                        ].sort_values("Trade Value", ascending=False)

                                        give_asset = "Pick / Bench Depth"
                                        give_asset_value = 0.0

                                        if not asset_pool.empty:
                                            give_asset = asset_pool.iloc[0]["Player"]
                                            give_asset_value = round(float(asset_pool.iloc[0]["Trade Value"]), 1)

                                        for _, team_b in power_df.iterrows():
                                            team_b_name = team_b["Team/User"]

                                            if team_a_name == team_b_name:
                                                continue

                                            team_b_roster = detail_rosters.get(team_b_name, pd.DataFrame())

                                            if team_b_roster.empty:
                                                continue

                                            target_pool = team_b_roster[
                                                team_b_roster["Position"] == need
                                            ].sort_values("Trade Value", ascending=False)

                                            for _, target in target_pool.head(2).iterrows():
                                                target_value = round(float(target["Trade Value"]), 1)

                                                if target_value <= 0:
                                                    continue

                                                fairness_gap = round(target_value - give_asset_value, 1)

                                                if abs(fairness_gap) <= 8:
                                                    action = "Near-even framework"
                                                elif fairness_gap > 8:
                                                    action = "Add value/pick"
                                                else:
                                                    action = "Ask for extra back"

                                                player_rec_rows.append(
                                                    {
                                                        "Team Needing Help": team_a_name,
                                                        "Need": need,
                                                        "Target Player": target["Player"],
                                                        "Target Team": team_b_name,
                                                        "Target Value": target_value,
                                                        "Possible Asset": give_asset,
                                                        "Asset Value": give_asset_value,
                                                        "Gap": fairness_gap,
                                                        "Suggestion": action,
                                                        "Why": f"{team_a_name} needs {need}; {team_b_name} has {target['Player']} as a targetable asset.",
                                                    }
                                                )

                                    player_rec_df = pd.DataFrame(player_rec_rows)

                                    if not player_rec_df.empty:
                                        player_rec_df = player_rec_df.sort_values(
                                            ["Team Needing Help", "Need", "Target Value"],
                                            ascending=[True, True, False],
                                        ).head(50)

                                        st.session_state["nfl_player_rec_df"] = player_rec_df

                                        st.dataframe(
                                            player_rec_df,
                                            use_container_width=True,
                                            hide_index=True,
                                        )

                                        st.download_button(
                                            "⬇️ Download Player Trade Ideas CSV",
                                            data=player_rec_df.to_csv(index=False).encode("utf-8"),
                                            file_name=f"{selected_league_name}_player_trade_ideas.csv",
                                            mime="text/csv",
                                            key="download_player_trade_ideas_csv",
                                        )
                                    else:
                                        st.info("No player-level trade ideas available for this league yet.")

                    except Exception as e:
                        st.error(f"Sleeper sync error: {e}")

            with nfl_tab5:
                st.markdown("### 🧠 League AI")

                power_df = st.session_state.get("nfl_power_df", pd.DataFrame())

                if power_df.empty:
                    st.info("Sync a Sleeper league first, then return here.")
                else:
                    best_team = power_df.iloc[0]
                    worst_team = power_df.iloc[-1]

                    ai_col1, ai_col2, ai_col3 = st.columns(3)

                    with ai_col1:
                        st.metric("Top Team", best_team["Team/User"], best_team["Roster Value"])

                    with ai_col2:
                        st.metric("Lowest Roster Value", worst_team["Team/User"], worst_team["Roster Value"])

                    with ai_col3:
                        league_avg = round(float(power_df["Roster Value"].mean()), 1)
                        st.metric("League Avg Value", league_avg)

                    st.markdown("#### League Read")

                    for _, row in power_df.iterrows():
                        team = row["Team/User"]
                        status = row["Team Status"]
                        weakness = row["Weakest Position"]
                        value = row["Roster Value"]

                        if status == "Contender":
                            st.success(f"{team}: Contender. Push for win-now upgrades, especially at {weakness}. Current value: {value}.")
                        elif status == "Rebuilder":
                            st.warning(f"{team}: Rebuilder. Shop older assets and target young players/picks. Weakest room: {weakness}. Current value: {value}.")
                        else:
                            st.info(f"{team}: Middle pack. Needs a direction. Weakest room: {weakness}. Current value: {value}.")

            with nfl_tab6:
                st.markdown("### 🧲 Waiver Wire Finder")

                if player_values_df.empty:
                    st.warning("Player values are unavailable.")
                else:
                    rostered_ids = st.session_state.get("nfl_rostered_player_ids", set())

                    waiver_df = player_values_df.copy()

                    if rostered_ids:
                        waiver_df = waiver_df[
                            ~waiver_df["Player ID"].astype(str).isin([str(x) for x in rostered_ids])
                        ]

                    waiver_pos = st.selectbox(
                        "Waiver Position:",
                        ["All", "QB", "RB", "WR", "TE", "K"],
                        key="waiver_position_filter",
                    )

                    if waiver_pos != "All":
                        waiver_df = waiver_df[waiver_df["Position"] == waiver_pos]

                    min_value = st.slider(
                        "Minimum Trade Value:",
                        0.0,
                        25.0,
                        5.0,
                        0.5,
                        key="waiver_min_value",
                    )

                    waiver_df = waiver_df[waiver_df["Trade Value"] >= min_value]

                    st.caption("Shows players not found on synced Sleeper rosters. Sync a league first for best results.")

                    waiver_display = waiver_df[
                        [
                            "Player",
                            "Team",
                            "Position",
                            "Age",
                            "Projected PPR",
                            "Trade Value",
                            "Value Tier",
                            "Dynasty Tag",
                            "Status",
                        ]
                    ].head(100)

                    st.dataframe(
                        waiver_display,
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.download_button(
                        "⬇️ Download Waiver Finder CSV",
                        data=waiver_display.to_csv(index=False).encode("utf-8"),
                        file_name="nfl_waiver_finder.csv",
                        mime="text/csv",
                        key="download_waiver_finder_csv",
                    )

            with nfl_tab7:
                st.markdown("### 💾 Saved Reports")

                saved_data_choice = st.radio(
                    "View:",
                    ["Trade History", "Saved Leagues", "League Report"],
                    horizontal=True,
                    key="saved_data_choice",
                )

                if saved_data_choice == "Trade History":
                    if os.path.exists(TRADE_HISTORY_FILE):
                        history_df = pd.read_csv(TRADE_HISTORY_FILE)
                        st.dataframe(history_df, use_container_width=True, hide_index=True)
                        st.download_button(
                            "⬇️ Download Trade History CSV",
                            data=history_df.to_csv(index=False).encode("utf-8"),
                            file_name="nfl_trade_history.csv",
                            mime="text/csv",
                            key="download_trade_history_csv",
                        )
                    else:
                        st.info("No saved trades yet.")

                if saved_data_choice == "Saved Leagues":
                    if os.path.exists(SAVED_LEAGUES_FILE):
                        leagues_df = pd.read_csv(SAVED_LEAGUES_FILE)
                        st.dataframe(leagues_df, use_container_width=True, hide_index=True)
                        st.download_button(
                            "⬇️ Download Saved Leagues CSV",
                            data=leagues_df.to_csv(index=False).encode("utf-8"),
                            file_name="nfl_saved_leagues.csv",
                            mime="text/csv",
                            key="download_saved_leagues_csv",
                        )
                    else:
                        st.info("No saved leagues yet.")

                if saved_data_choice == "League Report":
                    power_df = st.session_state.get("nfl_power_df", pd.DataFrame())
                    rec_df = st.session_state.get("nfl_rec_df", pd.DataFrame())
                    player_rec_df = st.session_state.get("nfl_player_rec_df", pd.DataFrame())

                    if power_df.empty:
                        st.info("Sync a Sleeper league first, then this report will populate.")
                    else:
                        report_csv_parts = ["POWER RANKINGS\n", power_df.to_csv(index=False)]

                        if not rec_df.empty:
                            report_csv_parts.extend(["\nTEAM TRADE PARTNERS\n", rec_df.to_csv(index=False)])

                        if not player_rec_df.empty:
                            report_csv_parts.extend(["\nPLAYER TRADE IDEAS\n", player_rec_df.to_csv(index=False)])

                        html_report = f"""
                        <html>
                        <head>
                            <title>Hag Labs NFL League Report</title>
                            <style>
                                body {{ font-family: Arial, sans-serif; margin: 32px; }}
                                h1, h2 {{ color: #111827; }}
                                table {{ border-collapse: collapse; width: 100%; margin-bottom: 28px; }}
                                th, td {{ border: 1px solid #d1d5db; padding: 8px; text-align: left; }}
                                th {{ background: #f3f4f6; }}
                            </style>
                        </head>
                        <body>
                            <h1>Hag Labs NFL League Report</h1>
                            <p>Generated: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}</p>
                            <h2>Power Rankings</h2>
                            {power_df.to_html(index=False)}
                            <h2>Team Trade Partners</h2>
                            {(rec_df.to_html(index=False) if not rec_df.empty else '<p>No team trade partners generated.</p>')}
                            <h2>Player Trade Ideas</h2>
                            {(player_rec_df.to_html(index=False) if not player_rec_df.empty else '<p>No player trade ideas generated.</p>')}
                        </body>
                        </html>
                        """

                        st.download_button(
                            "⬇️ Download Full League Report CSV",
                            data="".join(report_csv_parts).encode("utf-8"),
                            file_name="nfl_full_league_report.csv",
                            mime="text/csv",
                            key="download_full_league_report_csv",
                        )

                        st.download_button(
                            "⬇️ Download Full League Report HTML",
                            data=html_report.encode("utf-8"),
                            file_name="nfl_full_league_report.html",
                            mime="text/html",
                            key="download_full_league_report_html",
                        )

# ==========================================================
# SPORT BRANCH 2: NCAA SOFTBALL
# ==========================================================
if sport == "🥎 NCAA Softball":
    st.title("🥎 NCAA Softball Simulation Engine")
    st.markdown("### 📊 Log5 Win Probability Tracker & Schedule Difficulty Calibration")
    st.caption("*Scrapes live WarrenNolan standings, team pitching ERAs, and SOS Ranks to simulate 7-inning matchups and auto-grade past bets.*")
    
    def log_softball_to_sheets(row_data):
        try:
            worksheet = get_google_worksheet("MLB Daily Prediction Model", "Softball Log")
            
            values = worksheet.get_all_values()
            if not values or len(values) == 0:
                worksheet.append_row(["Date", "Away Team", "Home Team", "Away SP ERA", "Home SP ERA", "Model Away %", "Model Home %", "Predicted Winner", "Result"])
                values = [["Date", "Away Team", "Home Team"]]
            
            target_date = row_data[0]
            target_away = row_data[1]
            target_home = row_data[2]
            
            for row in values[1:]:
                if len(row) >= 3 and row[0] == target_date and row[1] == target_away and row[2] == target_home:
                    return "DUPLICATE"
            
            worksheet.append_row(row_data)
            return "SUCCESS"
            
        except gspread.exceptions.WorksheetNotFound:
            try:
                gc = get_google_client()
                sh = gc.open("MLB Daily Prediction Model")
                worksheet = sh.add_worksheet(title="Softball Log", rows="1000", cols="10")
                return "SUCCESS"
            except Exception as e:
                st.error(f"Softball Sheet Log Error: {e}")
                return "ERROR"
                
        except Exception as e:
            if "200" in str(e):
                return "SUCCESS"
            st.error(f"Softball Sheet Log Error: {e}")
            return "ERROR"
    @st.cache_data(ttl=CACHE_TTL_SHORT)
    def get_softball_log_stats():
        try:
            worksheet = get_google_worksheet("MLB Daily Prediction Model", "Softball Log")
            data = worksheet.get_all_values()
            if len(data) <= 1: return 0, 0.0
            total_games, model_wins = 0, 0
            for row in data[1:]:
                if len(row) >= 9:
                    result, model_pick = row[8].strip().upper(), row[7].strip()
                    if result in ["WIN", "LOSS"]:
                        total_games += 1
                        if result == "WIN": model_wins += 1
            mod_acc = (model_wins / total_games * 100) if total_games > 0 else 0.0
            return total_games, mod_acc
        except Exception: return 0, 0.0

    def get_daily_softball_games():
        try:
            date_str = get_local_date_str()
            year, month, day = date_str.split('-')
            games_list = []
            
            # Layer 1: ESPN Carpet Bomb (Hits all major conference Group IDs)
            espn_groups = ['50', '65', '8', '12', '9', '1', '2', '3', '4', '18', '21', '25', '90', '100']
            for grp in espn_groups:
                e_url = f"https://site.api.espn.com/apis/site/v2/sports/softball/college-softball/scoreboard?dates={year}{month}{day}&limit=200&groups={grp}"
                try:
                    resp = requests.get(e_url, timeout=7)
                    if resp.status_code == 200:
                        data = resp.json()
                        if 'events' in data:
                            for event in data['events']:
                                comps = event.get('competitions', [])
                                if comps:
                                    competitors = comps[0].get('competitors', [])
                                    if len(competitors) == 2:
                                        c1_is_home = competitors[0].get('homeAway') == 'home'
                                        home_t = competitors[0]['team'].get('location', '') if c1_is_home else competitors[1]['team'].get('location', '')
                                        away_t = competitors[1]['team'].get('location', '') if c1_is_home else competitors[0]['team'].get('location', '')
                                        if away_t and home_t and (away_t, home_t) not in games_list:
                                            games_list.append((away_t, home_t))
                except: pass

            # Layer 2: NCAA Recursive Fallback
            ncaa_urls = [
                f"https://data.ncaa.com/casablanca/scoreboard/softball/d1/{year}/{month}/{day}/scoreboard.json",
                f"https://data.ncaa.com/casandbox/scoreboard/softball/d1/{year}/{month}/{day}/scoreboard.json",
                f"https://data.ncaa.com/casablanca/championships/softball/d1/{year}/scoreboard.json"
            ]
            def extract_ncaa_games(data):
                if isinstance(data, dict):
                    away_data = data.get('away', {})
                    home_data = data.get('home', {})
                    if isinstance(away_data, dict) and isinstance(home_data, dict):
                        if ('names' in away_data or 'teamName' in away_data) and ('names' in home_data or 'teamName' in home_data):
                            a_name = away_data.get('names', {}).get('short', away_data.get('teamName', ''))
                            h_name = home_data.get('names', {}).get('short', home_data.get('teamName', ''))
                            if a_name and h_name and (a_name, h_name) not in games_list:
                                games_list.append((a_name, h_name))
                    for key, value in data.items():
                        extract_ncaa_games(value)
                elif isinstance(data, list):
                    for item in data:
                        extract_ncaa_games(item)

            for url in ncaa_urls:
                try:
                    resp = requests.get(url, timeout=7)
                    if resp.status_code == 200:
                        extract_ncaa_games(resp.json())
                except: continue

            return games_list
        except Exception:
            return []

    def map_ncaa_to_warren_nolan(ncaa_name, valid_teams):
        ncaa_clean = ncaa_name.lower().replace(".", "").replace(" ", "").replace("&", "").replace("-", "").strip()
        
        # 1. Exact Match
        for vt in valid_teams:
            vt_clean = vt.lower().replace(".", "").replace(" ", "").replace("&", "").replace("-", "").strip()
            if ncaa_clean == vt_clean:
                return vt
                
        # 2. Known Abbreviations
        abbrev = {
            'oklahomast': 'Oklahoma State', 'oklast': 'Oklahoma State',
            'fsu': 'Florida State', 'floridast': 'Florida State',
            'arizonast': 'Arizona State', 'bostonu': 'Boston University',
            'michiganst': 'Michigan State', 'mississippist': 'Mississippi State', 'missstate': 'Mississippi State',
            'ncstate': 'North Carolina State', 'northcarolinastate': 'North Carolina State',
            'pennst': 'Penn State', 'sandiegost': 'San Diego State',
            'texasam': 'Texas A&M', 'vatech': 'Virginia Tech', 'virginiatech': 'Virginia Tech',
            'wichitast': 'Wichita State', 'olemiss': 'Ole Miss',
            'ucf': 'UCF', 'lsu': 'LSU', 'usc': 'USC', 'byu': 'BYU',
            'mizzou': 'Missouri', 'southcarolina': 'South Carolina', 'georgiabulldogs': 'Georgia',
            'floridagators': 'Florida', 'tennesseeut': 'Tennessee', 'arkansasrazorbacks': 'Arkansas'
        }
        if ncaa_clean in abbrev and abbrev[ncaa_clean] in valid_teams:
            return abbrev[ncaa_clean]
            
        # 3. Safeguarded Contains Match
        for vt in valid_teams:
            vt_clean = vt.lower().replace(".", "").replace(" ", "").replace("&", "").replace("-", "").strip()
            if (vt_clean in ncaa_clean or ncaa_clean in vt_clean):
                if "texasam" in ncaa_clean and vt == "Texas": continue
                if "floridaatlantic" in ncaa_clean and vt == "Florida": continue
                if "floridastate" in ncaa_clean and vt == "Florida": continue
                if "oklahomastate" in ncaa_clean and vt == "Oklahoma": continue
                if "michiganstate" in ncaa_clean and vt == "Michigan": continue
                if "arizonastate" in ncaa_clean and vt == "Arizona": continue
                return vt
                
        return None

    def auto_grade_softball_pending_bets(valid_teams):
        try:
            worksheet = get_google_worksheet("MLB Daily Prediction Model", "Softball Log")
            data = worksheet.get_all_values()
            
            pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
            if not pending_rows: return 0
            
            pending_dates = list(set([row[0] for i, row in pending_rows]))
            score_dict = {}
            
            for d_str in pending_dates:
                try:
                    dt = datetime.strptime(d_str, "%Y-%m-%d")
                    year, month, day = dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")
                    
                    # 1. ESPN Carpet Bomb Search
                    espn_groups = ['50', '65', '8', '12', '9', '1', '2', '3', '4', '18', '21', '25', '90', '100']
                    for grp in espn_groups:
                        e_url = f"https://site.api.espn.com/apis/site/v2/sports/softball/college-softball/scoreboard?dates={year}{month}{day}&limit=200&groups={grp}"
                        try:
                            resp = requests.get(e_url, timeout=7)
                            if resp.status_code == 200:
                                data = resp.json()
                                if 'events' in data and len(data['events']) > 0:
                                    for event in data['events']:
                                        state = event.get('status', {}).get('type', {}).get('state', '').lower()
                                        if state == 'post':
                                            comps = event.get('competitions', [])
                                            if comps:
                                                competitors = comps[0].get('competitors', [])
                                                if len(competitors) == 2:
                                                    c1, c2 = competitors[0], competitors[1]
                                                    
                                                    t1_name = map_ncaa_to_warren_nolan(c1['team'].get('location', ''), valid_teams)
                                                    t2_name = map_ncaa_to_warren_nolan(c2['team'].get('location', ''), valid_teams)
                                                    
                                                    if t1_name and t2_name:
                                                        try:
                                                            s1 = int(c1.get('score', 0))
                                                            s2 = int(c2.get('score', 0))
                                                        except ValueError:
                                                            s1, s2 = 0, 0
                                                            
                                                        winner = t1_name if s1 > s2 else t2_name
                                                        score_dict[f"{d_str}_{t1_name.lower()}"] = winner.lower()
                                                        score_dict[f"{d_str}_{t2_name.lower()}"] = winner.lower()
                        except Exception: pass

                    # 2. NCAA Recursive Fallback
                    def extract_ncaa_scores(data):
                        if isinstance(data, dict):
                            away_data = data.get('away', {})
                            home_data = data.get('home', {})
                            state = data.get('gameState', '').lower()
                            
                            if state == 'final' and isinstance(away_data, dict) and isinstance(home_data, dict):
                                if ('names' in away_data or 'teamName' in away_data):
                                    a_name = away_data.get('names', {}).get('short', away_data.get('teamName', ''))
                                    h_name = home_data.get('names', {}).get('short', home_data.get('teamName', ''))
                                    
                                    a_mapped = map_ncaa_to_warren_nolan(a_name, valid_teams)
                                    h_mapped = map_ncaa_to_warren_nolan(h_name, valid_teams)
                                    
                                    if a_mapped and h_mapped:
                                        try:
                                            a_score = int(away_data.get('score', 0))
                                            h_score = int(home_data.get('score', 0))
                                        except ValueError:
                                            a_score, h_score = 0, 0
                                        
                                        winner = a_mapped if a_score > h_score else h_mapped
                                        score_dict[f"{d_str}_{a_mapped.lower()}"] = winner.lower()
                                        score_dict[f"{d_str}_{h_mapped.lower()}"] = winner.lower()
                            for key, value in data.items():
                                extract_ncaa_scores(value)
                        elif isinstance(data, list):
                            for item in data:
                                extract_ncaa_scores(item)
                                
                    ncaa_urls = [
                        f"https://data.ncaa.com/casablanca/scoreboard/softball/d1/{year}/{month}/{day}/scoreboard.json",
                        f"https://data.ncaa.com/casandbox/scoreboard/softball/d1/{year}/{month}/{day}/scoreboard.json",
                        f"https://data.ncaa.com/casablanca/championships/softball/d1/{year}/scoreboard.json"
                    ]
                    for url in ncaa_urls:
                        try:
                            resp = requests.get(url, timeout=7)
                            if resp.status_code == 200:
                                extract_ncaa_scores(resp.json())
                        except: continue
                        
                except Exception: continue
                
            updates = 0
            for i, row in pending_rows:
                d_str, away_t, model_pick = row[0], row[1].lower(), row[7].lower()
                lookup_key = f"{d_str}_{away_t}"
                
                if lookup_key in score_dict:
                    actual_winner = score_dict[lookup_key]
                    new_status = "WIN" if model_pick == actual_winner else "LOSS"
                    worksheet.update_cell(i + 1, 9, new_status)
                    updates += 1
            return updates
        except Exception as e:
            st.error(f"Softball Auto-Grader Error: {e}")
            return -1

    tot_sb_games, sb_acc = get_softball_log_stats()

    col1, col2, col3 = st.columns([2, 2, 3])
    with col1: st.metric(label="Total Graded Softball Games", value=tot_sb_games)
    with col2: st.metric(label="Model Accuracy", value=f"{sb_acc:.1f}%")

    fallback_teams = {
        'Alabama': [0.690, 11], 'Arizona': [0.710, 18], 'Arizona State': [0.550, 35], 'Arkansas': [0.715, 12], 'Auburn': [0.620, 24],
        'Baylor': [0.650, 22], 'Boston University': [0.820, 142], 'BYU': [0.580, 52], 'California': [0.680, 28], 'Charlotte': [0.700, 78],
        'Clemson': [0.680, 21], 'Duke': [0.845, 9], 'Florida': [0.765, 14], 'Florida Atlantic': [0.670, 85], 'Florida State': [0.735, 10],
        'Georgia': [0.745, 8], 'Georgia Tech': [0.590, 48], 'Grand Canyon': [0.750, 112], 'Houston': [0.520, 42], 'Illinois': [0.480, 68],
        'Indiana': [0.600, 55], 'Iowa State': [0.450, 31], 'James Madison': [0.580, 92], 'Kansas': [0.610, 45], 'Kentucky': [0.600, 13],
        'Liberty': [0.660, 40], 'Louisiana': [0.780, 38], 'Louisville': [0.560, 41], 'LSU': [0.775, 15], 'McNeese': [0.740, 65],
        'Miami (OH)': [0.810, 120], 'Michigan': [0.690, 50], 'Minnesota': [0.580, 36], 'Mississippi State': [0.640, 20], 'Missouri': [0.710, 16],
        'Nebraska': [0.590, 44], 'North Carolina': [0.550, 47], 'Northwestern': [0.650, 32], 'Notre Dame': [0.570, 39], 'Ohio State': [0.580, 54],
        'Oklahoma': [0.895, 6], 'Oklahoma State': [0.825, 7], 'Ole Miss': [0.540, 19], 'Oregon': [0.660, 25], 'Oregon State': [0.480, 27],
        'Penn State': [0.620, 62], 'San Diego State': [0.610, 58], 'South Alabama': [0.650, 51], 'South Carolina': [0.610, 17], 'South Florida': [0.590, 72],
        'Stanford': [0.760, 5], 'Syracuse': [0.500, 53], 'Tennessee': [0.810, 4], 'Texas': [0.880, 2], 'Texas A&M': [0.705, 13],
        'Texas State': [0.720, 46], 'Texas Tech': [0.560, 43], 'UCLA': [0.790, 3], 'UCF': [0.580, 34], 'USC Upstate': [0.680, 165],
        'Utah': [0.570, 30], 'Virginia': [0.630, 29], 'Virginia Tech': [0.720, 23], 'Washington': [0.725, 1], 'Wichita State': [0.580, 60],
        'Wisconsin': [0.520, 57]
    }

    fallback_eras = {
        'Alabama': 2.10, 'Arizona': 2.60, 'Arizona State': 3.20, 'Arkansas': 2.20, 'Auburn': 2.45,
        'Baylor': 2.55, 'Boston University': 1.95, 'BYU': 3.10, 'California': 2.75, 'Charlotte': 2.30,
        'Clemson': 2.15, 'Duke': 2.05, 'Florida': 2.35, 'Florida Atlantic': 2.40, 'Florida State': 2.50,
        'Georgia': 2.40, 'Georgia Tech': 3.10, 'Grand Canyon': 2.25, 'Houston': 3.60, 'Illinois': 3.40,
        'Indiana': 3.20, 'Iowa State': 3.80, 'James Madison': 2.90, 'Kansas': 2.80, 'Kentucky': 2.95,
        'Liberty': 2.45, 'Louisiana': 2.10, 'Louisville': 3.30, 'LSU': 2.25, 'McNeese': 2.15,
        'Miami (OH)': 2.20, 'Michigan': 2.40, 'Minnesota': 2.95, 'Mississippi State': 2.65, 'Missouri': 2.30,
        'Nebraska': 2.85, 'North Carolina': 3.15, 'Northwestern': 2.50, 'Notre Dame': 2.90, 'Ohio State': 3.05,
        'Oklahoma': 1.82, 'Oklahoma State': 2.15, 'Ole Miss': 2.80, 'Oregon': 2.55, 'Oregon State': 3.40,
        'Penn State': 2.60, 'San Diego State': 2.50, 'South Alabama': 2.20, 'South Carolina': 2.70, 'South Florida': 2.45,
        'Stanford': 1.75, 'Syracuse': 3.35, 'Tennessee': 1.90, 'Texas': 1.95, 'Texas A&M': 2.35,
        'Texas State': 2.10, 'Texas Tech': 3.45, 'UCLA': 2.30, 'UCF': 2.80, 'USC Upstate': 2.40,
        'Utah': 2.90, 'Virginia': 2.45, 'Virginia Tech': 2.65, 'Washington': 2.45, 'Wichita State': 3.10,
        'Wisconsin': 3.15
    }

    @st.cache_data(ttl=14400)
    def scrape_ncaa_softball_standings():
        try:
            url = "https://www.warrennolan.com/softball/2026/rpi-clean"
            response = requests.get(url, timeout=10)
            dfs = pd.read_html(response.text)
            
            df = None
            for t in dfs:
                cols = [str(c) for c in t.columns]
                has_team = any('Team' in c or 'School' in c for c in cols)
                has_record = any('Record' in c and 'Conf' not in c for c in cols)
                if has_team and has_record:
                    df = t
                    break
            if df is None: return {k: v[0] for k, v in fallback_teams.items()}, {k: v[1] for k, v in fallback_teams.items()}
            
            df.columns = [str(c).strip() for c in df.columns]
            team_col = [c for c in df.columns if 'Team' in c or 'School' in c][0]
            record_col = [c for c in df.columns if 'Record' in c and 'Conf' not in c][0]
            
            sos_rank_col = [c for c in df.columns if 'SOS' in c or 'Sched' in c]
            sos_col = sos_rank_col[0] if sos_rank_col else None
            
            win_data = {}
            sos_data = {}
            for index, row in df.iterrows():
                team = str(row[team_col]).strip()
                rec_str = str(row[record_col]).strip()
                
                sos_rank = 150
                if sos_col:
                    try:
                        sos_rank = int(row[sos_col])
                    except ValueError: pass
                
                if '-' in rec_str:
                    parts = rec_str.split('-')
                    w, l = float(parts[0]), float(parts[1])
                    win_pct = w / (w + l) if (w + l) > 0 else 0.500
                    win_pct = max(0.05, min(0.99, win_pct))
                    win_data[team] = round(win_pct, 4)
                    sos_data[team] = sos_rank
            return win_data, sos_data
        except Exception:
            return {k: v[0] for k, v in fallback_teams.items()}, {k: v[1] for k, v in fallback_teams.items()}

    @st.cache_data(ttl=14400)
    def scrape_ncaa_softball_pitching():
        try:
            url = "https://www.warrennolan.com/softball/2026/stats-team-pitching"
            response = requests.get(url, timeout=10)
            dfs = pd.read_html(response.text)
            
            df = None
            for t in dfs:
                cols = [str(c) for c in t.columns]
                has_team = any('Team' in c or 'School' in c for c in cols)
                has_era = any('ERA' in c for c in cols)
                if has_team and has_era:
                    df = t
                    break
            if df is None: return fallback_eras
            
            df.columns = [str(c).strip() for c in df.columns]
            team_col = [c for c in df.columns if 'Team' in c or 'School' in c][0]
            era_col = [c for c in df.columns if 'ERA' in c][0]
            
            era_data = {}
            for _, row in df.iterrows():
                team = str(row[team_col]).strip()
                try:
                    era_val = float(row[era_col])
                    era_data[team] = era_val
                except ValueError: continue
            return era_data
        except Exception:
            return fallback_eras

    with col3:
        st.write("")
        if st.button("🔄 Auto-Grade Yesterday's Softball"):
            with st.spinner("Accessing NCAA Scoreboard Portal..."):
                softball_teams, _ = scrape_ncaa_softball_standings()
                softball_eras = scrape_ncaa_softball_pitching()
                v_teams = sorted(list(set(softball_teams.keys()).intersection(set(softball_eras.keys()))))
                if not v_teams: v_teams = sorted(list(fallback_teams.keys()))
                updates = auto_grade_softball_pending_bets(v_teams)
                if updates > 0: st.success(f"✅ Successfully graded {updates} games! Refresh.")
                elif updates == 0: st.info("No games ready to be graded.")
    st.markdown("---")

    with st.spinner("Scraping NCAA Softball Baselines & Pitching Profiles..."):
        softball_teams, softball_sos = scrape_ncaa_softball_standings()
        softball_eras = scrape_ncaa_softball_pitching()
        
        if softball_teams and softball_eras:
            valid_teams = sorted(list(set(softball_teams.keys()).intersection(set(softball_eras.keys()))))
            if len(valid_teams) < 20:
                softball_teams = {k: v[0] for k, v in fallback_teams.items()}
                softball_sos = {k: v[1] for k, v in fallback_teams.items()}
                softball_eras = fallback_eras
                valid_teams = sorted(list(softball_teams.keys()))
             
            st.subheader("⚡ Automated Daily Softball Slate Runner")
            st.markdown("Simulate every Division I game scheduled for today on the NCAA Scoreboard and log them to Google Sheets in one click.")
            if st.button("▶ Auto-Run & Log Entire Softball Slate"):
                with st.spinner("Fetching today's NCAA schedule & running simulations..."):
                    scheduled_games = get_daily_softball_games()
                    if not scheduled_games:
                        st.warning("No D1 Softball games found on today's NCAA schedule yet.")
                    else:
                        logged_count = 0
                        slate_logs = [] 
                        for away_ncaa, home_ncaa in scheduled_games:
                            away_t = map_ncaa_to_warren_nolan(away_ncaa, valid_teams)
                            home_t = map_ncaa_to_warren_nolan(home_ncaa, valid_teams)
                            
                            if away_t and home_t and away_t != home_t:
                                wp_a = softball_teams[away_t]
                                wp_b = softball_teams[home_t]
                                 
                                sos_rank_a = softball_sos.get(away_t, 150)
                                sos_rank_b = softball_sos.get(home_t, 150)
                                 
                                sos_mult_a = 1.15 - 0.30 * ((sos_rank_a - 1) / 300)
                                sos_mult_b = 1.15 - 0.30 * ((sos_rank_b - 1) / 300)
                                
                                wp_adj_a = wp_a * sos_mult_a
                                wp_adj_b = wp_b * sos_mult_b
                                
                                away_era_val = softball_eras.get(away_t, 2.50)
                                home_era_val = softball_eras.get(home_t, 2.50)
                                
                                final_a = wp_adj_a * (2.50 / max(0.10, away_era_val))
                                final_b = wp_adj_b * (2.50 / max(0.10, home_era_val))
                                
                                final_a = max(0.01, min(0.99, final_a))
                                final_b = max(0.01, min(0.99, final_b))
                                
                                log5_away = (final_a - final_a * final_b) / (final_a + final_b - 2.0 * final_a * final_b)
                                log5_away = max(0.01, min(0.99, log5_away))
                                log5_home = 1.0 - log5_away
                                 
                                predicted_winner = away_t if log5_away > log5_home else home_t
                                
                                date_str = get_local_date_str()
                                row_data = [
                                    date_str, away_t, home_t, 
                                    away_era_val, home_era_val, 
                                    f"{log5_away:.1%}", f"{log5_home:.1%}", 
                                    predicted_winner, "PENDING"
                                ]
                                
                                log_status = log_softball_to_sheets(row_data)
                                if log_status in ["SUCCESS", "DUPLICATE"]:
                                    slate_logs.append(row_data)
                                    if log_status == "SUCCESS":
                                        logged_count += 1
                                    
                        if slate_logs:
                            st.success(f"✅ Successfully processed {len(slate_logs)} matchups! ({logged_count} new entries logged to Sheets)")
                            st.markdown("#### 📅 Today's NCAA Softball Predictions")
                            df_display = pd.DataFrame(slate_logs, columns=["Date", "Away Team", "Home Team", "Away SP ERA", "Home SP ERA", "Model Away %", "Model Home %", "Predicted Winner", "Status"])
                            st.dataframe(df_display, use_container_width=True, hide_index=True)
                        else:
                            st.info("No matchups from today's schedule could be confidently mapped to our 66 powerhouse teams.")

            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Manual Matchup Override")
                away_team = st.selectbox("Away Team:", valid_teams, index=0)
                home_team = st.selectbox("Home Team:", valid_teams, index=1 if len(valid_teams) > 1 else 0)
                
                default_away_era = softball_eras.get(away_team, 2.50)
                default_home_era = softball_eras.get(home_team, 2.50)
                
                st.markdown("---")
                st.write("**Pitcher Quality Customization**")
                
                away_era = st.slider(f"{away_team} Starting Pitcher ERA:", 0.00, 7.00, float(default_away_era), step=0.10, key=f"away_era_slider_{away_team}")
                home_era = st.slider(f"{home_team} Starting Pitcher ERA:", 0.00, 7.00, float(default_home_era), step=0.10, key=f"home_era_slider_{home_team}")
            
            with col2:
                st.subheader("Simulated Prediction Outputs")
                
                wp_a = softball_teams[away_team]
                wp_b = softball_teams[home_team]
                
                sos_rank_a = softball_sos.get(away_team, 150)
                sos_rank_b = softball_sos.get(home_team, 150)
                
                sos_mult_a = 1.15 - 0.30 * ((sos_rank_a - 1) / 300)
                sos_mult_b = 1.15 - 0.30 * ((sos_rank_b - 1) / 300)
                 
                wp_adj_a = wp_a * sos_mult_a
                wp_adj_b = wp_b * sos_mult_b
                
                final_a = wp_adj_a * (2.50 / max(0.10, away_era))
                final_b = wp_adj_b * (2.50 / max(0.10, home_era))
                
                final_a = max(0.01, min(0.99, final_a))
                final_b = max(0.01, min(0.99, final_b))
                
                log5_away = (final_a - final_a * final_b) / (final_a + final_b - 2.0 * final_a * final_b)
                log5_away = max(0.01, min(0.99, log5_away))
                log5_home = 1.0 - log5_away
                
                st.caption(f"*Schedule Multipliers: {away_team} (SOS Rank {sos_rank_a}: {sos_mult_a:.2f}x) vs {home_team} (SOS Rank {sos_rank_b}: {sos_mult_b:.2f}x)*")
                st.caption(f"*Calibrated Baselines: {away_team} ({final_a:.3f}) vs {home_team} ({final_b:.3f})*")
                st.write("")
                
                st.metric(f"{away_team} Win Probability:", f"{log5_away:.1%}")
                st.metric(f"{home_team} Win Probability:", f"{log5_home:.1%}")
                 
                predicted_winner = away_team if log5_away > log5_home else home_team
                st.info(f"🏆 Predicted Winner: **{predicted_winner}**")
                
                st.markdown("---")
                if st.button("💾 Log Softball Prediction to Google Sheets"):
                    date_str = get_local_date_str() 
                    row_data = [
                        date_str, away_team, home_team, 
                        away_era, home_era, 
                        f"{log5_away:.1%}", f"{log5_home:.1%}", 
                        predicted_winner, "PENDING"
                    ]
                    with st.spinner("Logging softball prediction..."):
                        status = log_softball_to_sheets(row_data)
                        if status == "SUCCESS":
                            st.success("✅ Logged successfully to the 'Softball Log' tab!")
                        elif status == "DUPLICATE":
                            st.info("ℹ️ This matchup is already logged for today.")
        else:
            st.error("Could not compile softball standings or pitching statistics database.")

# ==========================================================
# SPORT BRANCH 3: NFL FOOTBALL
# ==========================================================
elif sport == "🏈 NFL Football":
    nfl_page = st.sidebar.radio(
        "Select NFL Engine:",
        [
            "🏈 NFL Simulation Engine",
            "🏆 NFL Fantasy Predictor"
        ]
    )

    st.sidebar.markdown("---")

    if nfl_page == "🏆 NFL Fantasy Predictor":

        st.title("🏆 NFL Fantasy Predictor")
        st.caption("Sleeper PPR projections, rankings, tiers, and trade tools.")

        value_mode = st.radio(
            "Value Mode:",
            ["Redraft", "Dynasty"],
            horizontal=True
        )

        st.info(
            "Value Guide: Projected PPR = estimated weekly points. "
            "Redraft Value = win-now trade score. "
            "Dynasty Value = long-term keeper score adjusted for age and position. "
            "Trade Value = the active score used by the trade analyzer based on the selected mode."
        )

        @st.cache_data(ttl=CACHE_TTL_DAILY)
        def load_sleeper_players():
            try:
                url = "https://api.sleeper.app/v1/players/nfl"
                resp = requests.get(url, timeout=20).json()

                players = {}

                for pid, pdata in resp.items():
                    if not pdata.get("active"):
                        continue

                    pos = pdata.get("position", "UNK")
                    if pos not in ["QB", "RB", "WR", "TE", "K"]:
                        continue

                    name = f"{pdata.get('first_name', '')} {pdata.get('last_name', '')}".strip()
                    team = pdata.get("team", "FA")
                    age = pdata.get("age", None)

                    players[str(pid)] = {
                        "Player ID": str(pid),
                        "Name": name,
                        "Position": pos,
                        "Team": team,
                        "Age": age,
                        "Headshot URL": nfl_headshot_url(str(pid)),
                        "Status": nfl_player_status_label(pdata.get("injury_status") or pdata.get("status")),
                        "Years Exp": pdata.get("years_exp", None),
                        "College": pdata.get("college", "")
                    }

                return players

            except Exception:
                return {}

        def safe_age(age):
            try:
                if age is None:
                    return None
                return float(age)
            except Exception:
                return None

        def nfl_headshot_url(player_id):
            if not player_id:
                return None
            return f"https://sleepercdn.com/content/nfl/players/{player_id}.jpg"

        def nfl_player_status_label(status):
            if not status:
                return "Active"
            return str(status)


        def calculate_redraft_value(position, age, projected_ppr):
            projected_ppr = float(projected_ppr or 0)

            scarcity = {
                "QB": 0.92,
                "RB": 1.20,
                "WR": 1.08,
                "TE": 1.15,
                "K": 0.70
            }.get(position, 1.0)

            usage_bonus = {
                "QB": projected_ppr * 0.03,
                "RB": projected_ppr * 0.08,
                "WR": projected_ppr * 0.06,
                "TE": projected_ppr * 0.05,
                "K": projected_ppr * 0.01
            }.get(position, 0)

            return round((projected_ppr * scarcity) + usage_bonus, 1)

        def calculate_dynasty_value(position, age, projected_ppr):
            age = safe_age(age)
            value = calculate_redraft_value(position, age, projected_ppr)

            if age is None:
                return value

            prime_age = {
                "QB": 30,
                "RB": 24,
                "WR": 26,
                "TE": 27,
                "K": 30
            }.get(position, 26)

            if age <= prime_age:
                youth_boost = (prime_age - age) * 0.08
                value *= (1 + youth_boost)
            else:
                age_penalty = (age - prime_age) * 0.09
                value *= max(0.55, 1 - age_penalty)

            return round(value, 1)

        def nfl_base_projection(pos, name):
            elite_names = [
                "Josh Allen", "Jalen Hurts", "Lamar Jackson", "Patrick Mahomes",
                "Christian McCaffrey", "Bijan Robinson", "Breece Hall", "Saquon Barkley",
                "Justin Jefferson", "Ja'Marr Chase", "CeeDee Lamb", "Amon-Ra St. Brown",
                "Tyreek Hill", "Puka Nacua", "A.J. Brown",
                "Travis Kelce", "Sam LaPorta", "Trey McBride", "George Kittle"
            ]

            strong_names = [
                "Joe Burrow", "C.J. Stroud", "Dak Prescott", "Anthony Richardson",
                "Jahmyr Gibbs", "Jonathan Taylor", "Kyren Williams", "De'Von Achane",
                "Garrett Wilson", "Drake London", "Mike Evans", "Chris Olave",
                "DK Metcalf", "DeVonta Smith", "Jaylen Waddle",
                "Mark Andrews", "Dalton Kincaid", "Evan Engram"
            ]

            base = {
                "QB": 17.0,
                "RB": 11.5,
                "WR": 10.5,
                "TE": 7.5,
                "K": 8.0
            }.get(pos, 5.0)

            if name in elite_names:
                base *= 1.30
            elif name in strong_names:
                base *= 1.15

            return round(base, 1)

        @st.cache_data(ttl=CACHE_TTL_DAILY)
        def load_nfl_weekly_player_stats():
            try:
                current_year = datetime.now().year
                seasons = list(range(current_year - 3, current_year + 1))
                weekly = nfl.import_weekly_data(seasons)
                return weekly
            except Exception:
                return pd.DataFrame()

        def nfl_player_lab_snapshot(player_name, position, age, projected_ppr, redraft_value, dynasty_value, weekly_stats):
            position = position or "UNK"
            age_num = safe_age(age)

            if weekly_stats is not None and not weekly_stats.empty and "player_display_name" in weekly_stats.columns:
                pdf = weekly_stats[weekly_stats["player_display_name"].astype(str).str.lower() == str(player_name).lower()].copy()
            else:
                pdf = pd.DataFrame()

            if not pdf.empty:
                current_season = int(pdf["season"].max()) if "season" in pdf.columns else datetime.now().year
                cur = pdf[pdf["season"] == current_season].copy() if "season" in pdf.columns else pdf.copy()
                games = len(cur)

                def s(col):
                    return float(cur[col].fillna(0).sum()) if col in cur.columns else 0.0

                hist_games = len(pdf)
                hist_points = float(pdf["fantasy_points_ppr"].fillna(0).sum()) if "fantasy_points_ppr" in pdf.columns else projected_ppr * hist_games
                current_points = float(cur["fantasy_points_ppr"].fillna(0).sum()) if "fantasy_points_ppr" in cur.columns else projected_ppr * games
                current_ppg = round(current_points / max(1, games), 2)

                details = {
                    "Games Sample": games,
                    "Historical Games": hist_games,
                    "Historical Fantasy Points": round(hist_points, 1),
                    "Current PPG": current_ppg,
                    "Projected Weekly PPR": projected_ppr,
                    "Redraft Value": redraft_value,
                    "Dynasty Value": dynasty_value,
                    "Future Projection": round((projected_ppr * 0.65) + (current_ppg * 0.35), 1),
                }

                if position == "QB":
                    details.update({
                        "Pass Yards": round(s("passing_yards"), 1),
                        "Pass TD": round(s("passing_tds"), 1),
                        "Interceptions": round(s("interceptions"), 1),
                        "Rush Yards": round(s("rushing_yards"), 1),
                        "Rush TD": round(s("rushing_tds"), 1),
                    })
                elif position == "RB":
                    details.update({
                        "Carries": round(s("carries"), 1),
                        "Rush Yards": round(s("rushing_yards"), 1),
                        "Rush TD": round(s("rushing_tds"), 1),
                        "Targets": round(s("targets"), 1),
                        "Receptions": round(s("receptions"), 1),
                        "Receiving Yards": round(s("receiving_yards"), 1),
                    })
                elif position in ["WR", "TE"]:
                    details.update({
                        "Targets": round(s("targets"), 1),
                        "Receptions": round(s("receptions"), 1),
                        "Receiving Yards": round(s("receiving_yards"), 1),
                        "Receiving TD": round(s("receiving_tds"), 1),
                        "Air Yards": round(s("air_yards"), 1),
                        "Yards After Catch": round(s("receiving_yards_after_catch"), 1),
                    })
                else:
                    details.update({
                        "Kicker Profile": "Sleeper registry + projection model",
                        "Projected Weekly Points": projected_ppr,
                    })

                trend_df = pdf[[c for c in ["season", "week", "fantasy_points_ppr"] if c in pdf.columns]].copy()
                return details, trend_df

            fallback = {
                "Games Sample": 0,
                "Historical Games": 0,
                "Historical Fantasy Points": 0,
                "Current PPG": projected_ppr,
                "Projected Weekly PPR": projected_ppr,
                "Redraft Value": redraft_value,
                "Dynasty Value": dynasty_value,
                "Future Projection": round(projected_ppr * (1.05 if (age_num and age_num <= 25) else 0.95), 1),
                "Data Note": "No nfl_data_py match found. Showing Sleeper registry + Hag Labs projection model."
            }
            return fallback, pd.DataFrame()

        with st.spinner("Fetching live Sleeper player registry..."):
            sleeper_players = load_sleeper_players()

        st.markdown("## 🏈 League Command Center")
        st.caption("Sync your Sleeper league, view roster power rankings, identify weak spots, and find trade partners.")

        nfl_cc1, nfl_cc2, nfl_cc3 = st.columns(3)

        with nfl_cc1:
            st.metric("Mode", value_mode)

        with nfl_cc2:
            st.metric("Data Source", "Sleeper")

        with nfl_cc3:
            st.metric("Tools", "Rankings + Trades")

        st.markdown("### 🔗 Sleeper League Sync")

        sleeper_username = st.text_input("Sleeper Username:")

        if sleeper_username:
            try:
                user_resp = requests.get(
                    f"https://api.sleeper.app/v1/user/{sleeper_username}",
                    timeout=10
                ).json()

                sleeper_user_id = user_resp.get("user_id")

                if sleeper_user_id:
                    leagues_resp = requests.get(
                        f"https://api.sleeper.app/v1/user/{sleeper_user_id}/leagues/nfl/2025",
                        timeout=10
                    ).json()

                    if leagues_resp:
                        league_options = {
                            league.get("name", "Unnamed League"): league.get("league_id")
                            for league in leagues_resp
                        }

                        selected_league_name = st.selectbox(
                            "Select Sleeper League:",
                            list(league_options.keys())
                        )

                        selected_league_id = league_options[selected_league_name]

                        rosters_resp = requests.get(
                            f"https://api.sleeper.app/v1/league/{selected_league_id}/rosters",
                            timeout=10
                        ).json()

                        users_resp = requests.get(
                            f"https://api.sleeper.app/v1/league/{selected_league_id}/users",
                            timeout=10
                        ).json()

                        user_map = {
                            u.get("user_id"): u.get("display_name", "Unknown")
                            for u in users_resp
                        }

                        roster_rows = []

                        for roster in rosters_resp:
                            owner_id = roster.get("owner_id")
                            owner_name = user_map.get(owner_id, "Unknown")
                            roster_players = roster.get("players") or []

                            qb_count = 0
                            rb_count = 0
                            wr_count = 0
                            te_count = 0
                            k_count = 0

                            for pid in roster_players:
                                p = sleeper_players.get(str(pid), {})
                                pos = p.get("Position", "UNK")

                                if pos == "QB":
                                    qb_count += 1
                                elif pos == "RB":
                                    rb_count += 1
                                elif pos == "WR":
                                    wr_count += 1
                                elif pos == "TE":
                                    te_count += 1
                                elif pos == "K":
                                    k_count += 1

                            roster_rows.append({
                                "Team/User": owner_name,
                                "Roster ID": roster.get("roster_id"),
                                "Players": len(roster_players),
                                "QB": qb_count,
                                "RB": rb_count,
                                "WR": wr_count,
                                "TE": te_count,
                                "K": k_count,
                                "Wins": roster.get("settings", {}).get("wins", 0),
                                "Losses": roster.get("settings", {}).get("losses", 0),
                                "Ties": roster.get("settings", {}).get("ties", 0)
                            })

                        roster_export_df = pd.DataFrame(roster_rows)
                        st.dataframe(
                            roster_export_df,
                            use_container_width=True,
                            hide_index=True
                        )
                        st.download_button(
                            "⬇️ Export Sleeper Roster Summary CSV",
                            data=roster_export_df.to_csv(index=False).encode("utf-8"),
                            file_name="sleeper_roster_summary.csv",
                            mime="text/csv"
                        )

                        player_lookup = sleeper_players
                        power_rows = []
                        team_targets = {}

                        for roster in rosters_resp:
                            owner_id = roster.get("owner_id")
                            owner_name = user_map.get(owner_id, "Unknown")
                            roster_players = roster.get("players") or []

                            total_value = 0
                            qb_value = 0
                            rb_value = 0
                            wr_value = 0
                            te_value = 0
                            k_value = 0
                            player_names = []
                            player_targets_by_pos = {"QB": [], "RB": [], "WR": [], "TE": [], "K": []}

                            for pid in roster_players:
                                p = player_lookup.get(str(pid))

                                if not p:
                                    continue

                                name = p.get("Name", "Unknown")
                                pos = p.get("Position", "UNK")
                                age = p.get("Age", None)
                                projected_ppr = nfl_base_projection(pos, name)

                                if value_mode == "Dynasty":
                                    player_value = calculate_dynasty_value(pos, age, projected_ppr)
                                else:
                                    player_value = calculate_redraft_value(pos, age, projected_ppr)

                                total_value += player_value
                                player_names.append(name)
                                if pos in player_targets_by_pos:
                                    player_targets_by_pos[pos].append((name, player_value))

                                if pos == "QB":
                                    qb_value += player_value
                                elif pos == "RB":
                                    rb_value += player_value
                                elif pos == "WR":
                                    wr_value += player_value
                                elif pos == "TE":
                                    te_value += player_value
                                elif pos == "K":
                                    k_value += player_value

                            for _pos in player_targets_by_pos:
                                player_targets_by_pos[_pos] = [n for n, v in sorted(player_targets_by_pos[_pos], key=lambda x: x[1], reverse=True)]
                            team_targets[owner_name] = player_targets_by_pos

                            position_values = {
                                "QB": qb_value,
                                "RB": rb_value,
                                "WR": wr_value,
                                "TE": te_value,
                                "K": k_value
                            }

                            weakest_position = min(position_values, key=position_values.get)

                            if total_value >= 230:
                                team_status = "Contender"
                            elif total_value >= 180:
                                team_status = "Middle Pack"
                            else:
                                team_status = "Rebuilder"

                            power_rows.append({
                                "Team/User": owner_name,
                                "Roster Value": round(total_value, 1),
                                "QB Value": round(qb_value, 1),
                                "RB Value": round(rb_value, 1),
                                "WR Value": round(wr_value, 1),
                                "TE Value": round(te_value, 1),
                                "K Value": round(k_value, 1),
                                "Weakest Position": weakest_position,
                                "Team Status": team_status,
                                "Top Players": ", ".join(player_names[:5])
                            })

                        if power_rows:
                            power_df = pd.DataFrame(power_rows)
                            power_df = power_df.sort_values("Roster Value", ascending=False)

                            st.markdown("### 📊 Power Rankings")
                            st.caption("Ranks every synced Sleeper roster using the currently selected Redraft/Dynasty value mode.")

                            st.dataframe(
                                power_df,
                                use_container_width=True,
                                hide_index=True
                            )

                            st.download_button(
                                "⬇️ Export NFL Power Rankings CSV",
                                data=power_df.to_csv(index=False).encode("utf-8"),
                                file_name="nfl_power_rankings.csv",
                                mime="text/csv"
                            )

                            st.markdown("### 📈 Roster Value Chart")
                            fig_power = go.Figure()
                            fig_power.add_trace(go.Bar(
                                x=power_df["Team/User"],
                                y=power_df["Roster Value"],
                                name="Roster Value"
                            ))
                            fig_power.update_layout(
                                height=360,
                                xaxis_title="Team",
                                yaxis_title="Roster Value"
                            )
                            st.plotly_chart(fig_power, use_container_width=True)

                            st.markdown("### 📊 Position Strength Chart")
                            strength_team = st.selectbox(
                                "Select team for position strength chart:",
                                power_df["Team/User"].tolist(),
                                key="nfl_position_strength_team"
                            )
                            strength_row = power_df[power_df["Team/User"] == strength_team].iloc[0]
                            strength_df = pd.DataFrame({
                                "Position": ["QB", "RB", "WR", "TE", "K"],
                                "Value": [
                                    strength_row["QB Value"],
                                    strength_row["RB Value"],
                                    strength_row["WR Value"],
                                    strength_row["TE Value"],
                                    strength_row.get("K Value", 0),
                                ]
                            })
                            fig_strength = go.Figure()
                            fig_strength.add_trace(go.Bar(
                                x=strength_df["Position"],
                                y=strength_df["Value"],
                                name="Position Value"
                            ))
                            fig_strength.update_layout(height=320)
                            st.plotly_chart(fig_strength, use_container_width=True)


                            recommendations = []

                            for _, team_a in power_df.iterrows():
                                for _, team_b in power_df.iterrows():
                                    if team_a["Team/User"] == team_b["Team/User"]:
                                        continue

                                    need = team_a["Weakest Position"]
                                    partner_strength = team_b[f"{need} Value"]

                                    partner_name = team_b["Team/User"]
                                    needy_team = team_a["Team/User"]
                                    target_names = team_targets.get(partner_name, {}).get(need, [])[:4]

                                    team_a_strengths = {
                                        "QB": team_a.get("QB Value", 0),
                                        "RB": team_a.get("RB Value", 0),
                                        "WR": team_a.get("WR Value", 0),
                                        "TE": team_a.get("TE Value", 0),
                                        "K": team_a.get("K Value", 0),
                                    }
                                    surplus_position = max(
                                        {k: v for k, v in team_a_strengths.items() if k != need},
                                        key=lambda k: team_a_strengths[k]
                                    )
                                    offer_names = team_targets.get(needy_team, {}).get(surplus_position, [])[:4]

                                    threshold = 8 if need == "K" else 18

                                    if partner_strength >= threshold and target_names:
                                        recommendations.append({
                                            "Mode": value_mode,
                                            "Team Needing Help": needy_team,
                                            "Need": need,
                                            "Suggested Partner": partner_name,
                                            "Suggested Player Targets": ", ".join(target_names),
                                            "Suggested Offer From Needy Team": ", ".join(offer_names),
                                            "Offer Position": surplus_position,
                                            "Partner Strength": round(partner_strength, 1),
                                            "Fairness Hint": "Use one target plus one offer candidate as the starting point.",
                                            "Why": f"{partner_name} is strong at {need}; {needy_team} can shop surplus {surplus_position} depth."
                                        })

                            rec_df = pd.DataFrame(recommendations)

                            if not rec_df.empty:
                                rec_df = rec_df.sort_values(
                                    "Partner Strength",
                                    ascending=False
                                ).head(15)

                                st.markdown("### 🤝 Trade Partner Targets")

                                st.dataframe(
                                    rec_df,
                                    use_container_width=True,
                                    hide_index=True
                                )

                                st.download_button(
                                    "⬇️ Export Trade Partner Targets CSV",
                                    data=rec_df.to_csv(index=False).encode("utf-8"),
                                    file_name="nfl_trade_partner_targets.csv",
                                    mime="text/csv"
                                )

                                st.markdown("### 🎯 Exact Trade Suggestions")
                                exact_trade_df = build_exact_trade_suggestions(power_df, filtered_df if "filtered_df" in locals() else pd.DataFrame())

                                if not exact_trade_df.empty:
                                    st.dataframe(
                                        exact_trade_df,
                                        use_container_width=True,
                                        hide_index=True
                                    )

                                    st.download_button(
                                        "⬇️ Export Exact Trade Suggestions CSV",
                                        data=exact_trade_df.to_csv(index=False).encode("utf-8"),
                                        file_name="nfl_exact_trade_suggestions.csv",
                                        mime="text/csv"
                                    )
                                else:
                                    st.info("Exact trade suggestions will appear after player values and roster rankings are loaded.")

                    else:
                        st.warning("No 2025 Sleeper leagues found for this username.")
                else:
                    st.warning("Sleeper username not found.")

            except Exception as e:
                st.error(f"Sleeper sync error: {e}")

        if not sleeper_players:
            st.warning("Could not load Sleeper NFL players.")
        else:
            rows = []

            for pid, p in sleeper_players.items():
                name = p.get("Name", "Unknown")
                pos = p.get("Position", "UNK")
                team = p.get("Team", "FA")
                age = p.get("Age", None)

                projected_ppr = nfl_base_projection(pos, name)

                if pos == "QB":
                    floor = projected_ppr * 0.75
                    ceiling = projected_ppr * 1.35
                elif pos == "RB":
                    floor = projected_ppr * 0.65
                    ceiling = projected_ppr * 1.55
                elif pos == "WR":
                    floor = projected_ppr * 0.60
                    ceiling = projected_ppr * 1.65
                else:
                    floor = projected_ppr * 0.55
                    ceiling = projected_ppr * 1.75

                if projected_ppr >= 18:
                    tier = "Elite"
                elif projected_ppr >= 14:
                    tier = "Strong Starter"
                elif projected_ppr >= 10:
                    tier = "Starter/Flex"
                elif projected_ppr >= 7:
                    tier = "Depth"
                else:
                    tier = "Bench"

                redraft_value = calculate_redraft_value(pos, age, projected_ppr)
                dynasty_value = calculate_dynasty_value(pos, age, projected_ppr)

                rows.append({
                    "Player ID": pid,
                    "Headshot URL": nfl_headshot_url(pid),
                    "Player": name,
                    "Team": team,
                    "Position": pos,
                    "Age": age,
                    "Status": p.get("Status", "Active"),
                    "Years Exp": p.get("Years Exp", None),
                    "College": p.get("College", ""),
                    "Projected PPR": round(projected_ppr, 1),
                    "Floor": round(floor, 1),
                    "Ceiling": round(ceiling, 1),
                    "Tier": tier,
                    "Redraft Value": redraft_value,
                    "Dynasty Value": dynasty_value,
                    "Value Gap": round(dynasty_value - redraft_value, 1),
                    "Dynasty Tag": (
                        "Young Core" if age and age <= 24 else
                        "Prime Asset" if age and age <= 28 else
                        "Win-Now Vet" if age and age <= 31 else
                        "Declining Vet"
                    ),
                    "Why Ranked Here": f"{pos} baseline projection plus age and positional scarcity adjustment.",
                    "Future Outlook": (
                        "Ascending / long runway" if age and age <= 24 else
                        "Prime production window" if age and age <= 28 else
                        "Win-now value" if age and age <= 31 else
                        "Short-term depth"
                    )
                })

            nfl_df = pd.DataFrame(rows)

            if value_mode == "Dynasty":
                nfl_df["Trade Value"] = nfl_df["Dynasty Value"]
            else:
                nfl_df["Trade Value"] = nfl_df["Redraft Value"]

            st.markdown("### 🔮 NFL Player Projections")

            c1, c2, c3 = st.columns(3)

            with c1:
                position_filter = st.selectbox(
                    "Filter by position:",
                    ["All", "QB", "RB", "WR", "TE"]
                )

            with c2:
                tier_filter = st.selectbox(
                    "Filter by tier:",
                    ["All", "Elite", "Strong Starter", "Starter/Flex", "Depth", "Bench"]
                )

            with c3:
                player_search = st.text_input("Search NFL players:")

            filtered_df = nfl_df.copy()

            if position_filter != "All":
                filtered_df = filtered_df[filtered_df["Position"] == position_filter]

            if tier_filter != "All":
                filtered_df = filtered_df[filtered_df["Tier"] == tier_filter]

            if player_search:
                filtered_df = filtered_df[
                    filtered_df["Player"].str.contains(player_search, case=False, na=False)
                ]

            filtered_df = filtered_df.sort_values("Trade Value", ascending=False)

            nfl_player_values_display_df = filtered_df.drop(
                columns=[c for c in ["Headshot URL", "headshot_url", "HeadshotURL"] if c in filtered_df.columns],
                errors="ignore"
            )

            st.dataframe(
                nfl_player_values_display_df,
                use_container_width=True,
                hide_index=True
            )
            st.download_button(
                "⬇️ Export NFL Player Values CSV",
                data=nfl_player_values_display_df.to_csv(index=False).encode("utf-8"),
                file_name="nfl_player_values.csv",
                mime="text/csv"
            )

            st.markdown("---")
            st.markdown("### 🧑‍💼 Roster Detail View")

            if 'rosters_resp' in locals() and 'user_map' in locals():
                roster_team_options = [user_map.get(r.get("owner_id"), "Unknown") for r in rosters_resp]
                selected_roster_team = st.selectbox("Select Sleeper team to inspect:", roster_team_options, key="nfl_roster_detail_team")
                selected_roster = next((r for r in rosters_resp if user_map.get(r.get("owner_id"), "Unknown") == selected_roster_team), None)

                detail_rows = []
                if selected_roster:
                    for pid in selected_roster.get("players") or []:
                        p = sleeper_players.get(str(pid), {})
                        if not p:
                            continue
                        name = p.get("Name", "Unknown")
                        pos = p.get("Position", "UNK")
                        age = p.get("Age", None)
                        projected_ppr = nfl_base_projection(pos, name)
                        redraft_value = calculate_redraft_value(pos, age, projected_ppr)
                        dynasty_value = calculate_dynasty_value(pos, age, projected_ppr)
                        active_value = dynasty_value if value_mode == "Dynasty" else redraft_value
                        detail_rows.append({
                            "Player": name,
                            "Position": pos,
                            "Team": p.get("Team", "FA"),
                            "Age": age,
                            "Projected PPR": projected_ppr,
                            "Redraft Value": redraft_value,
                            "Dynasty Value": dynasty_value,
                            "Active Value": active_value,
                            "Roster Label": "Core" if active_value >= 18 else "Starter" if active_value >= 12 else "Depth"
                        })

                detail_df = pd.DataFrame(detail_rows).sort_values(["Position", "Active Value"], ascending=[True, False]) if detail_rows else pd.DataFrame()
                if not detail_df.empty:
                    st.dataframe(detail_df, use_container_width=True, hide_index=True)

                    st.markdown("#### 🪪 Roster Cards")
                    card_positions = ["QB", "RB", "WR", "TE", "K"]
                    for card_pos in card_positions:
                        pos_cards = detail_df[detail_df["Position"] == card_pos].head(6)
                        if not pos_cards.empty:
                            st.markdown(f"**{card_pos}**")
                            cols = st.columns(min(3, len(pos_cards)))
                            for idx, (_, card_player) in enumerate(pos_cards.iterrows()):
                                with cols[idx % len(cols)]:
                                    card_id = str(card_player.get("Player ID", ""))
                                    card_img = nfl_headshot_url(card_id)
                                    if card_img:
                                        st.image(card_img, width=90)
                                    st.caption(f"{card_player.get('Player')} • {card_player.get('Trade Value')}")
                    st.download_button(
                        "⬇️ Export Selected Roster CSV",
                        data=detail_df.to_csv(index=False).encode("utf-8"),
                        file_name="nfl_selected_roster.csv",
                        mime="text/csv"
                    )
            else:
                st.info("Sync a Sleeper league above to unlock roster detail view.")

            st.markdown("---")
            st.markdown("### 🧪 NFL Player Lab")
            st.caption("Historical, current, and future projection view using nfl_data_py when available plus Sleeper registry context.")

            lab_position = st.selectbox("Player Lab Position:", ["QB", "RB", "WR", "TE", "K"], key="nfl_lab_position")
            lab_df = nfl_df[nfl_df["Position"] == lab_position].copy()
            lab_players = sorted(lab_df["Player"].dropna().unique().tolist())

            if lab_players:
                selected_lab_player = st.selectbox("Select NFL player:", lab_players, key="nfl_lab_player")
                player_row = lab_df[lab_df["Player"] == selected_lab_player].sort_values("Trade Value", ascending=False).iloc[0]

                weekly_stats = load_nfl_weekly_player_stats()
                snapshot, trend_df = nfl_player_lab_snapshot(
                    selected_lab_player,
                    player_row.get("Position"),
                    player_row.get("Age"),
                    player_row.get("Projected PPR"),
                    player_row.get("Redraft Value"),
                    player_row.get("Dynasty Value"),
                    weekly_stats
                )

                lab_player_id = player_row.get("Player ID")
                lab_headshot = player_row.get("Headshot URL") or nfl_headshot_url(lab_player_id)

                profile_col, summary_col = st.columns([1, 5])
                with profile_col:
                    if lab_headshot:
                        st.image(lab_headshot, width=150)
                with summary_col:
                    st.markdown(f"## {selected_lab_player}")
                    st.caption(f"{player_row.get('Team', 'FA')} • {player_row.get('Position')} • Age {player_row.get('Age', 'N/A')} • {player_row.get('Status', 'Active')}")

                    if st.button("⭐ Add to Watchlist", key=f"watch_{selected_lab_player}"):
                        if "nfl_watchlist" not in st.session_state:
                            st.session_state["nfl_watchlist"] = []
                        if selected_lab_player not in st.session_state["nfl_watchlist"]:
                            st.session_state["nfl_watchlist"].append(selected_lab_player)

                m1, m2, m3, m4 = st.columns(4)
                with m1:
                    st.metric("Current PPG", snapshot.get("Current PPG", 0))
                with m2:
                    st.metric("Future Projection", snapshot.get("Future Projection", 0))
                with m3:
                    st.metric("Redraft Value", snapshot.get("Redraft Value", 0))
                with m4:
                    st.metric("Dynasty Value", snapshot.get("Dynasty Value", 0))

                st.markdown("#### Player Profile Details")
                st.dataframe(pd.DataFrame([snapshot]), use_container_width=True, hide_index=True)

                if not trend_df.empty and "fantasy_points_ppr" in trend_df.columns:
                    st.markdown("#### Historical Fantasy Trend")
                    trend_df = trend_df.sort_values(["season", "week"])
                    fig_lab = go.Figure()
                    fig_lab.add_trace(go.Scatter(
                        x=list(range(1, len(trend_df) + 1)),
                        y=trend_df["fantasy_points_ppr"],
                        mode="lines+markers",
                        name="PPR Points"
                    ))
                    fig_lab.update_layout(
                        height=350,
                        xaxis_title="Game Sample",
                        yaxis_title="Fantasy Points"
                    )
                    st.plotly_chart(fig_lab, use_container_width=True)

                    st.markdown("#### Future Projection Curve")
                    future_projection = float(snapshot.get("Future Projection", 0) or 0)
                    projection_curve = pd.DataFrame({
                        "Window": ["Now", "Short Term", "Rest of Season", "Long Term"],
                        "Projected PPG": [
                            float(snapshot.get("Current PPG", 0) or 0),
                            round(future_projection * 0.96, 2),
                            round(future_projection, 2),
                            round(future_projection * (1.06 if snapshot.get("Age", 99) and snapshot.get("Age", 99) <= 25 else 0.92), 2)
                        ]
                    })
                    fig_future = go.Figure()
                    fig_future.add_trace(go.Scatter(
                        x=projection_curve["Window"],
                        y=projection_curve["Projected PPG"],
                        mode="lines+markers",
                        name="Projection"
                    ))
                    fig_future.update_layout(height=320)
                    st.plotly_chart(fig_future, use_container_width=True)

                st.download_button(
                    "⬇️ Export NFL Player Lab Snapshot CSV",
                    data=pd.DataFrame([snapshot]).to_csv(index=False).encode("utf-8"),
                    file_name="nfl_player_lab_snapshot.csv",
                    mime="text/csv"
                )

                st.markdown("#### ⭐ Watchlist")
                watchlist = st.session_state.get("nfl_watchlist", [])
                if watchlist:
                    watch_df = nfl_df[nfl_df["Player"].isin(watchlist)].copy()
                    st.dataframe(watch_df, use_container_width=True, hide_index=True)
                    st.download_button(
                        "⬇️ Export Watchlist CSV",
                        data=watch_df.to_csv(index=False).encode("utf-8"),
                        file_name="nfl_watchlist.csv",
                        mime="text/csv"
                    )
                else:
                    st.info("No watchlist players yet.")
            else:
                st.info("No players available for this position.")

            st.markdown("---")
            st.markdown("### ⚖️ NFL Trade Analyzer")

            trade_players = sorted(nfl_df["Player"].dropna().unique().tolist())

            t1, t2 = st.columns(2)

            with t1:
                team_a = st.multiselect("Team A Receives:", trade_players, key="nfl_trade_a")

            with t2:
                team_b = st.multiselect("Team B Receives:", trade_players, key="nfl_trade_b")

            if st.button("Analyze NFL Trade"):
                a_df = nfl_df[nfl_df["Player"].isin(team_a)]
                b_df = nfl_df[nfl_df["Player"].isin(team_b)]

                a_total = a_df["Trade Value"].sum()
                b_total = b_df["Trade Value"].sum()
                diff = abs(a_total - b_total)

                r1, r2 = st.columns(2)

                trade_cols = [
                    "Player", "Team", "Position", "Projected PPR",
                    "Redraft Value", "Dynasty Value", "Trade Value",
                    "Value Gap", "Dynasty Tag", "Tier"
                ]

                with r1:
                    st.metric("Team A Receives", f"{a_total:.1f} Value")
                    if not a_df.empty:
                        st.dataframe(a_df[trade_cols], hide_index=True)
                        st.download_button(
                            "⬇️ Export Team A Trade Side CSV",
                            data=a_df[trade_cols].to_csv(index=False).encode("utf-8"),
                            file_name="nfl_trade_team_a.csv",
                            mime="text/csv"
                        )

                with r2:
                    st.metric("Team B Receives", f"{b_total:.1f} Value")
                    if not b_df.empty:
                        st.dataframe(b_df[trade_cols], hide_index=True)
                        st.download_button(
                            "⬇️ Export Team B Trade Side CSV",
                            data=b_df[trade_cols].to_csv(index=False).encode("utf-8"),
                            file_name="nfl_trade_team_b.csv",
                            mime="text/csv"
                        )

                if a_total > b_total + 3:
                    st.success(f"Team A wins this trade by {diff:.1f} trade value points.")
                elif b_total > a_total + 3:
                    st.success(f"Team B wins this trade by {diff:.1f} trade value points.")
                else:
                    st.info(f"This trade is balanced. Difference: {diff:.1f} trade value points.")

        st.stop()
        if nfl_page == "🏈 NFL Simulation Engine":
            st.title("🏈 NFL Ensemble Simulation Engine")
            st.markdown("### 📊 Elo + Split EPA Power Ratings")
            st.caption(
                "*Fuses structural base Elo ratings with weighted Pass/Rush Expected Points Added (EPA) per play. Includes situational edges.*"
            )
    
    def log_nfl_to_sheets(row_data):
        try:
            gc = get_google_client()
            sh = gc.open("NFL Prediction Model")
            try:
                worksheet = sh.worksheet("NFL Log")
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sh.add_worksheet(title="NFL Log", rows="1000", cols="10")
                
            values = worksheet.get_all_values()
            if not values or len(values) == 0:
                worksheet.append_row(["Date", "Away Team", "Home Team", "Away Odds", "Home Odds", "Model Away %", "Model Home %", "Predicted Winner", "Result"])
                values = [["Date", "Away Team", "Home Team"]]
                
            target_date = row_data[0]
            target_away = row_data[1]
            target_home = row_data[2]
            
            for row in values[1:]:
                if len(row) >= 3 and row[0] == target_date and row[1] == target_away and row[2] == target_home:
                    return "DUPLICATE"
                    
            worksheet.append_row(row_data)
            return "SUCCESS"
        except Exception as e:
            return "ERROR"

    def get_nfl_log_stats():
        try:
            worksheet = get_google_worksheet("NFL Prediction Model", "NFL Log")
            data = worksheet.get_all_values()
            if len(data) <= 1: return 0, 0.0, 0.0
            total_games, model_wins, vegas_wins = 0, 0, 0
            for row in data[1:]:
                if len(row) >= 9:
                    result, model_pick = row[8].strip().upper(), row[7].strip()
                    try: away_ml = int(row[3])
                    except: away_ml = 0
                    try: home_ml = int(row[4])
                    except: home_ml = 0
                    
                    away_t, home_t = row[1], row[2]
                    vegas_pick = away_t if away_ml < home_ml else home_t
                    
                    if result in ["WIN", "LOSS"]:
                        total_games += 1
                        if result == "WIN": model_wins += 1
                        actual_winner = model_pick if result == "WIN" else (away_t if model_pick == home_t else home_t)
                        if actual_winner == vegas_pick: vegas_wins += 1
            
            mod_acc = (model_wins / total_games * 100) if total_games > 0 else 0.0
            veg_acc = (vegas_wins / total_games * 100) if total_games > 0 else 0.0
            return total_games, mod_acc, veg_acc
        except Exception: return 0, 0.0, 0.0

    def auto_grade_nfl_pending_bets():
        try:
            worksheet = get_google_worksheet("NFL Prediction Model", "NFL Log")
            data = worksheet.get_all_values()
            
            pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
            if not pending_rows: return 0
            
            pending_dates = list(set([row[0] for i, row in pending_rows]))
            score_dict = {}
            
            for d_str in pending_dates:
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                espn_date = dt.strftime("%Y%m%d")
                
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={espn_date}"
                try:
                    resp = requests.get(url, timeout=10).json()
                    if 'events' in resp:
                        for event in resp['events']:
                            if event['status']['type']['state'] == 'post':
                                comp = event['competitions'][0]
                                team1 = comp['competitors'][0]
                                team2 = comp['competitors'][1]
                                
                                t1_name = team1['team']['displayName']
                                t2_name = team2['team']['displayName']
                                
                                t1_score = int(team1.get('score', 0))
                                t2_score = int(team2.get('score', 0))
                                
                                winner = t1_name if t1_score > t2_score else t2_name
                                score_dict[f"{d_str}_{t1_name}"] = winner
                                score_dict[f"{d_str}_{t2_name}"] = winner
                except Exception: continue
                
            updates = 0
            for i, row in pending_rows:
                d_str, away_t, model_pick = row[0], row[1], row[7]
                match_key = next((k for k in score_dict.keys() if d_str in k and (away_t in k or k.split('_')[1] in away_t)), None)
                
                if match_key:
                    actual_winner = score_dict[match_key]
                    new_status = "WIN" if (model_pick in actual_winner or actual_winner in model_pick) else "LOSS"
                    worksheet.update_cell(i + 1, 9, new_status)
                    updates += 1
            return updates
        except Exception as e:
            st.error(f"NFL Auto-Grader Error: {e}")
            return -1

    @st.cache_data(ttl=CACHE_TTL_ODDS)
    def get_nfl_live_odds():
        api_key = os.environ.get('ODDS_API_KEY')
        if not api_key: return {}
        url = f'https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=american&bookmakers=draftkings,fanduel'
        try:
            response = requests.get(url, timeout=15).json()
            odds_dict = {}
            for game in response:
                if 'bookmakers' in game and len(game['bookmakers']) > 0:
                    outcomes = game['bookmakers'][0]['markets'][0]['outcomes']
                    away = game['away_team']
                    home = game['home_team']
                    away_ml = next((o['price'] for o in outcomes if o['name'] == away), 100)
                    home_ml = next((o['price'] for o in outcomes if o['name'] == home), -110)
                    odds_dict[f"{away} @ {home}"] = [away_ml, home_ml]
            return odds_dict
        except Exception: return {}

    @st.cache_data(ttl=CACHE_TTL_DAILY) 
    def generate_baseline_power_matrix():
        base_elo = {
            'ARI': 1480, 'ATL': 1495, 'BAL': 1650, 'BUF': 1640, 'CAR': 1350, 'CHI': 1490, 'CIN': 1560, 'CLE': 1540,
            'DAL': 1600, 'DEN': 1460, 'DET': 1620, 'GB':  1580, 'HOU': 1570, 'IND': 1510, 'JAX': 1500, 'KC':  1680,
            'LV':  1470, 'LAC': 1520, 'LAR': 1550, 'MIA': 1590, 'MIN': 1510, 'NE':  1420, 'NO':  1500, 'NYG': 1430,
            'NYJ': 1510, 'PHI': 1610, 'PIT': 1550, 'SF':  1660, 'SEA': 1520, 'TB':  1540, 'TEN': 1450, 'WAS': 1440
        }
        
        power_matrix = {}
        teams = nfl.import_team_desc()
        
        try:
            now = datetime.now()
            target_year = now.year if now.month >= 9 else now.year - 1
            
            cols_needed = ['posteam', 'defteam', 'epa', 'season_type', 'play_type']
            pbp = nfl.import_pbp_data([target_year], columns=cols_needed)
            
            pbp_pass = pbp[(pbp['season_type'] == 'REG') & (pbp['play_type'] == 'pass')]
            pbp_rush = pbp[(pbp['season_type'] == 'REG') & (pbp['play_type'] == 'run')]
            
            off_pass_epa = pbp_pass.groupby('posteam')['epa'].mean().to_dict()
            def_pass_epa = pbp_pass.groupby('defteam')['epa'].mean().to_dict()
            off_rush_epa = pbp_rush.groupby('posteam')['epa'].mean().to_dict()
            def_rush_epa = pbp_rush.groupby('defteam')['epa'].mean().to_dict()

            for index, row in teams.iterrows():
                abbr = row['team_abbr']
                if abbr in base_elo:
                    power_matrix[abbr] = {
                        'Elo': base_elo[abbr],
                        'Off_Pass_EPA': round(off_pass_epa.get(abbr, 0.0), 3),
                        'Def_Pass_EPA': round(def_pass_epa.get(abbr, 0.0), 3),
                        'Off_Rush_EPA': round(off_rush_epa.get(abbr, 0.0), 3),
                        'Def_Rush_EPA': round(def_rush_epa.get(abbr, 0.0), 3),
                        'Name': row['team_name']
                    }
        except Exception as e:
            for index, row in teams.iterrows():
                abbr = row['team_abbr']
                if abbr in base_elo:
                    power_matrix[abbr] = {
                        'Elo': base_elo[abbr], 'Off_Pass_EPA': 0.0, 'Def_Pass_EPA': 0.0, 'Off_Rush_EPA': 0.0, 'Def_Rush_EPA': 0.0, 'Name': row['team_name']
                    }
                    
        return power_matrix

    st.markdown("### 📊 Live Model Log & Automation")
    tot_games, mod_acc, veg_acc = get_nfl_log_stats()
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1: st.metric(label="Total Graded Games", value=tot_games)
    with col2: st.metric(label="Model Accuracy", value=f"{mod_acc:.1f}%")
    with col3: st.metric(label="Vegas Accuracy", value=f"{veg_acc:.1f}%")
    with col4: 
        st.write("")
        if st.button("🔄 Auto-Grade Completed Games"):
            with st.spinner("Pinging ESPN NFL Scoreboard..."):
                updates = auto_grade_nfl_pending_bets()
                if updates > 0: st.success(f"✅ Successfully graded {updates} games! Refresh.")
                elif updates == 0: st.info("No games ready to be graded.")
    st.markdown("---")

    power_matrix = generate_baseline_power_matrix()
    full_team_names = [data['Name'] for abbr, data in power_matrix.items()]
    name_to_abbr = {data['Name']: abbr for abbr, data in power_matrix.items()}

    with st.spinner('Syncing active Odds API lines...'):
        live_odds = get_nfl_live_odds()
        
        st.subheader("⚡ Automated Weekly Slate Runner")
        st.caption("Pulls every active NFL matchup currently on the board, simulates win probabilities using base engine calibrations, and logs actionable edges.")
        
        if st.button("▶ Auto-Run & Log Active NFL Slate"):
            with st.spinner("Processing live odds against Split-EPA matrix..."):
                slate_logs = []
                new_logs_count = 0
                date_str = get_local_date_str()
                
                for game_key, odds in live_odds.items():
                    try:
                        away_team_name, home_team_name = game_key.split(" @ ")
                        a_ml, h_ml = odds
                        
                        if away_team_name in name_to_abbr and home_team_name in name_to_abbr:
                            away_abbr = name_to_abbr[away_team_name]
                            home_abbr = name_to_abbr[home_team_name]
                            
                            a_off_pass, a_def_pass = power_matrix[away_abbr]['Off_Pass_EPA'], power_matrix[away_abbr]['Def_Pass_EPA']
                            a_off_rush, a_def_rush = power_matrix[away_abbr]['Off_Rush_EPA'], power_matrix[away_abbr]['Def_Rush_EPA']
                            h_off_pass, h_def_pass = power_matrix[home_abbr]['Off_Pass_EPA'], power_matrix[home_abbr]['Def_Pass_EPA']
                            h_off_rush, h_def_rush = power_matrix[home_abbr]['Off_Rush_EPA'], power_matrix[home_abbr]['Def_Rush_EPA']

                            away_pass_edge = a_off_pass - h_def_pass
                            away_rush_edge = a_off_rush - h_def_rush
                            home_pass_edge = h_off_pass - a_def_pass
                            home_rush_edge = h_off_rush - a_def_rush

                            away_net_epa = (0.65 * away_pass_edge) + (0.35 * away_rush_edge)
                            home_net_epa = (0.65 * home_pass_edge) + (0.35 * home_rush_edge)
                            
                            away_elo = power_matrix[away_abbr]['Elo']
                            home_elo = power_matrix[home_abbr]['Elo']
                            hfa = 45 
                            
                            adj_power_away = away_elo + (away_net_epa * 400)
                            adj_power_home = home_elo + hfa + (home_net_epa * 400)
                            
                            prob_away = 1 / (1 + 10 ** ((adj_power_home - adj_power_away) / 400))
                            prob_home = 1.0 - prob_away
                            
                            v_prob_a = calculate_implied_prob(a_ml)
                            v_prob_h = calculate_implied_prob(h_ml)
                            
                            action_taken = "No Edge"
                            if prob_away > v_prob_a + 0.03: action_taken = away_team_name
                            if prob_home > v_prob_h + 0.03: action_taken = home_team_name

                            confidence_tier = "Low"

                            if action_taken == away_t:
                                confidence_tier = get_confidence_tier(model_away_prob, v_a_prob)
                            
                            elif action_taken == home_t:
                                confidence_tier = get_confidence_tier(model_home_prob, v_h_prob)
                        
                            
                            if action_taken != "No Edge":
                                row_data = [date_str, away_team_name, home_team_name, a_ml, h_ml, f"{prob_away:.1%}", f"{prob_home:.1%}", action_taken, "PENDING"]
                                log_status = log_nfl_to_sheets(row_data)
                                if log_status in ["SUCCESS", "DUPLICATE"]:
                                    slate_logs.append(row_data)
                                    if log_status == "SUCCESS":
                                        new_logs_count += 1
                    except Exception as e:
                        continue
                        
                if slate_logs:
                    st.success(f"✅ Successfully processed {len(slate_logs)} actionable edges! ({new_logs_count} new entries logged to Sheets)")
                    df_display = pd.DataFrame(slate_logs, columns=["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Confidence", "Status"])
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.info("No actionable edges found on the active NFL slate.")

    st.markdown("---")

    col1, col2 = st.columns([1, 1.2])
    with col1:
        st.subheader("Matchup Override & Situational Matrix")
        away_team_name = st.selectbox("Away Team:", sorted(full_team_names), index=3)
        home_team_name = st.selectbox("Home Team:", sorted(full_team_names), index=15)
        
        away_abbr = name_to_abbr[away_team_name]
        home_abbr = name_to_abbr[home_team_name]
        
        st.markdown("---")
        st.write("### ⚙️ Engine Calibration")
        away_elo = st.slider(f"{away_abbr} Base Elo:", 1200, 1800, power_matrix[away_abbr]['Elo'], step=5)
        home_elo = st.slider(f"{home_abbr} Base Elo:", 1200, 1800, power_matrix[home_abbr]['Elo'], step=5)
        hfa = st.number_input("Home Field Advantage (Elo Points):", value=45, step=5)

    with col2:
        st.subheader("Simulated Prediction Outputs")
        
        a_off_pass, a_def_pass = power_matrix[away_abbr]['Off_Pass_EPA'], power_matrix[away_abbr]['Def_Pass_EPA']
        a_off_rush, a_def_rush = power_matrix[away_abbr]['Off_Rush_EPA'], power_matrix[away_abbr]['Def_Rush_EPA']
        h_off_pass, h_def_pass = power_matrix[home_abbr]['Off_Pass_EPA'], power_matrix[home_abbr]['Def_Pass_EPA']
        h_off_rush, h_def_rush = power_matrix[home_abbr]['Off_Rush_EPA'], power_matrix[home_abbr]['Def_Rush_EPA']

        away_pass_edge = a_off_pass - h_def_pass
        away_rush_edge = a_off_rush - h_def_rush
        home_pass_edge = h_off_pass - a_def_pass
        home_rush_edge = h_off_rush - a_def_rush

        away_net_epa = (0.65 * away_pass_edge) + (0.35 * away_rush_edge)
        home_net_epa = (0.65 * home_pass_edge) + (0.35 * home_rush_edge)
        
        adj_power_away = away_elo + (away_net_epa * 400)
        adj_power_home = home_elo + hfa + (home_net_epa * 400)
        
        prob_away = 1 / (1 + 10 ** ((adj_power_home - adj_power_away) / 400))
        prob_home = 1.0 - prob_away
        
        st.write(f"Engine Calibration: {away_abbr} (Adj Power: **{adj_power_away:.1f}**) vs {home_abbr} (Adj Power: **{adj_power_home:.1f}**)")
        st.write("")
        
        res_c1, res_c2 = st.columns(2)
        with res_c1:
            st.metric(f"{away_abbr} Win Probability:", f"{prob_away:.1%}")
        with res_c2:
            st.metric(f"{home_abbr} Win Probability:", f"{prob_home:.1%}")
            
        predicted_winner = away_team_name if prob_away > prob_home else home_team_name
        st.info(f"🏆 Predicted Winner: **{predicted_winner}**")

# ==========================================================
# SPORT BRANCH 4: NCAA FOOTBALL (DYNAMIC CFBD API)
# ==========================================================
elif sport == "🎓 NCAA Football":
    st.title("🎓 NCAA Football Composite Power Simulation")
    st.markdown("### 📊 Advanced CFBD Power Rating Engine")
    st.caption("*Leverages the CollegeFootballData API to sync real-time Elo ratings and maps them into our 100-point structural Composite Power Rating (CPR) scale.*")

    def log_ncaaf_to_sheets(row_data):
        try:
            gc = get_google_client()
            sh = gc.open("NCAAF Prediction Model") 
            try:
                worksheet = sh.worksheet("NCAAF Log")
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sh.add_worksheet(title="NCAAF Log", rows="1000", cols="10")
                
            values = worksheet.get_all_values()
            if not values or len(values) == 0:
                worksheet.append_row(["Date", "Away Team", "Home Team", "Away Odds", "Home Odds", "Model Away %", "Model Home %", "Predicted Winner", "Result"])
                values = [["Date", "Away Team", "Home Team"]]
                
            target_date = row_data[0]
            target_away = row_data[1]
            target_home = row_data[2]
            
            for row in values[1:]:
                if len(row) >= 3 and row[0] == target_date and row[1] == target_away and row[2] == target_home:
                    return "DUPLICATE"
                    
            worksheet.append_row(row_data)
            return "SUCCESS"
        except Exception:
            return "ERROR"

    def get_ncaaf_log_stats():
        try:
            gc = get_google_client()
            sh = gc.open("NCAAF Prediction Model")
            worksheet = sh.worksheet("NCAAF Log")
            data = worksheet.get_all_values()
            if len(data) <= 1: return 0, 0.0, 0.0
            total_games, model_wins, vegas_wins = 0, 0, 0
            for row in data[1:]:
                if len(row) >= 9:
                    result, model_pick = row[8].strip().upper(), row[7].strip()
                    try: away_ml = int(row[3])
                    except: away_ml = 0
                    try: home_ml = int(row[4])
                    except: home_ml = 0
                    
                    away_t, home_t = row[1], row[2]
                    vegas_pick = away_t if away_ml < home_ml else home_t
                    
                    if result in ["WIN", "LOSS"]:
                        total_games += 1
                        if result == "WIN": model_wins += 1
                        actual_winner = model_pick if result == "WIN" else (away_t if model_pick == home_t else home_t)
                        if actual_winner == vegas_pick: vegas_wins += 1
            
            mod_acc = (model_wins / total_games * 100) if total_games > 0 else 0.0
            veg_acc = (vegas_wins / total_games * 100) if total_games > 0 else 0.0
            return total_games, mod_acc, veg_acc
        except Exception: return 0, 0.0, 0.0

    def auto_grade_ncaaf_pending_bets():
        try:
            gc = get_google_client()
            sh = gc.open("NCAAF Prediction Model")
            worksheet = sh.worksheet("NCAAF Log")
            data = worksheet.get_all_values()
            
            pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
            if not pending_rows: return 0
            
            pending_dates = list(set([row[0] for i, row in pending_rows]))
            score_dict = {}
            
            for d_str in pending_dates:
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                espn_date = dt.strftime("%Y%m%d")
                
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates={espn_date}"
                try:
                    resp = requests.get(url, timeout=10).json()
                    if 'events' in resp:
                        for event in resp['events']:
                            if event['status']['type']['state'] == 'post':
                                comp = event['competitions'][0]
                                team1 = comp['competitors'][0]
                                team2 = comp['competitors'][1]
                                
                                t1_name = team1['team']['displayName']
                                t2_name = team2['team']['displayName']
                                
                                t1_score = int(team1.get('score', 0))
                                t2_score = int(team2.get('score', 0))
                                
                                winner = t1_name if t1_score > t2_score else t2_name
                                score_dict[f"{d_str}_{t1_name}"] = winner
                                score_dict[f"{d_str}_{t2_name}"] = winner
                except Exception: continue
                
            updates = 0
            for i, row in pending_rows:
                d_str, away_t, model_pick = row[0], row[1], row[7]
                match_key = next((k for k in score_dict.keys() if d_str in k and (away_t in k or k.split('_')[1] in away_t)), None)
                
                if match_key:
                    actual_winner = score_dict[match_key]
                    new_status = "WIN" if (model_pick in actual_winner or actual_winner in model_pick) else "LOSS"
                    worksheet.update_cell(i + 1, 9, new_status)
                    updates += 1
            return updates
        except Exception as e:
            st.error(f"NCAAF Auto-Grader Error: {e}")
            return -1

    @st.cache_data(ttl=CACHE_TTL_ODDS)
    def get_ncaaf_live_odds():
        api_key = os.environ.get('ODDS_API_KEY')
        if not api_key: return {}
        url = f'https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf/odds/?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=american&bookmakers=draftkings,fanduel'
        try:
            response = requests.get(url, timeout=15).json()
            odds_dict = {}
            for game in response:
                if 'bookmakers' in game and len(game['bookmakers']) > 0:
                    outcomes = game['bookmakers'][0]['markets'][0]['outcomes']
                    away = game['away_team']
                    home = game['home_team']
                    away_ml = next((o['price'] for o in outcomes if o['name'] == away), 100)
                    home_ml = next((o['price'] for o in outcomes if o['name'] == home), -110)
                    odds_dict[f"{away} @ {home}"] = [away_ml, home_ml]
            return odds_dict
        except Exception: return {}

    def map_odds_to_cfbd(odds_team_name, cfb_teams_list):
        if odds_team_name in cfb_teams_list:
            return odds_team_name
        for ct in cfb_teams_list:
            if ct in odds_team_name or odds_team_name in ct:
                return ct
            if ct.replace("State", "St") in odds_team_name:
                return ct
        return None

    @st.cache_data(ttl=43200) # Syncs API twice a day to save rate limits
    def generate_dynamic_cfb_power_matrix():
        fallback_matrix = {
            'Georgia Bulldogs': 98.5, 'Ohio State Buckeyes': 97.0, 'Texas Longhorns': 95.5,
            'Oregon Ducks': 94.0, 'Alabama Crimson Tide': 93.0, 'Ole Miss Rebels': 90.5,
            'Notre Dame Fighting Irish': 89.0, 'Michigan Wolverines': 88.5, 'Penn State Nittany Lions': 88.0,
            'Missouri Tigers': 86.5, 'LSU Tigers': 85.5, 'Utah Utes': 85.0,
            'Oklahoma Sooners': 84.5, 'Tennessee Volunteers': 84.0, 'Florida State Seminoles': 83.5,
            'Clemson Tigers': 83.0, 'Kansas State Wildcats': 82.5, 'Oklahoma State Cowboys': 81.0,
            'Miami Hurricanes': 80.5, 'USC Trojans': 80.0, 'Texas A&M Aggies': 79.5,
            'NC State Wolfpack': 78.5, 'Arizona Wildcats': 77.0, 'Louisville Cardinals': 76.5,
            'Washington Huskies': 75.0, 'Iowa Hawkeyes': 74.5, 'Kansas Jayhawks': 73.5,
            'Wisconsin Badgers': 72.0, 'SMU Mustangs': 71.0, 'Boise State Broncos': 70.0,
            'Liberty Flames': 68.0, 'Tulane Green Wave': 67.5, 'Memphis Tigers': 66.0,
            'Florida Gators': 77.0, 'Auburn Tigers': 75.0, 'Kentucky Wildcats': 74.0,
            'Average Power 4 Team': 70.0, 'Average Group of 5 Team': 50.0, 'FCS Opponent': 25.0
        }
        
        api_key = os.environ.get('CFBD_API_KEY', '').strip()
        
        if api_key.lower().startswith('bearer '):
            api_key = api_key[7:].strip()
            
        if not api_key:
            return fallback_matrix, False, "No API key found in environment."

        try:
            now = datetime.now()
            target_year = now.year if now.month >= 8 else now.year - 1

            url = f"https://api.collegefootballdata.com/ratings/elo?year={target_year}"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "accept": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return fallback_matrix, False, f"API returned status {response.status_code}: {response.text}"
                
            elo_data = response.json()
            
            dynamic_matrix = {}
            for team in elo_data:
                team_name = team.get('team', 'FA')
                elo = team.get('elo')
                
                if team_name and elo:
                    cpr = (elo - 1000) / 10
                    cpr = max(20.0, min(100.0, cpr)) 
                    dynamic_matrix[team_name] = round(cpr, 1)

            if len(dynamic_matrix) > 10:
                dynamic_matrix['Average Power 4 Team'] = 70.0
                dynamic_matrix['Average Group of 5 Team'] = 50.0
                dynamic_matrix['FCS Opponent'] = 25.0
                return dynamic_matrix, True, ""
            else:
                return fallback_matrix, False, "API returned empty or insufficient data."

        except Exception as e:
            return fallback_matrix, False, str(e)

    # --- TOP DASHBOARD BLOCK ---
    st.markdown("### 📊 Live Model Log & Automation")
    tot_games, mod_acc, veg_acc = get_ncaaf_log_stats()
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1: st.metric(label="Total Graded Games", value=tot_games)
    with col2: st.metric(label="Model Accuracy", value=f"{mod_acc:.1f}%")
    with col3: st.metric(label="Vegas Accuracy", value=f"{veg_acc:.1f}%")
    with col4: 
        st.write("")
        if st.button("🔄 Auto-Grade Completed Games"):
            with st.spinner("Pinging ESPN College Football Scoreboard..."):
                updates = auto_grade_ncaaf_pending_bets()
                if updates > 0: st.success(f"✅ Successfully graded {updates} games! Refresh.")
                elif updates == 0: st.info("No games ready to be graded.")
    st.markdown("---")

    with st.spinner("Syncing CFBD Data Engine..."):
        cfb_power_matrix, api_success, err_msg = generate_dynamic_cfb_power_matrix()
        
    cfb_teams = sorted(list(cfb_power_matrix.keys()))

    if not api_success:
        if err_msg:
            st.warning(f"⚠️ CFBD API Error: {err_msg}. Using static offseason baseline matrix.")
        else:
            st.warning("⚠️ CFBD API Key missing. Using static offseason baseline matrix.")
    else:
        st.success("✅ CFBD API Synchronized. Live CPR matrix populated for 130+ teams.")

    # --- AUTOMATED SLATE RUNNER ---
    with st.spinner('Syncing active Odds API lines for NCAAF...'):
        live_odds = get_ncaaf_live_odds()
        
        st.subheader("⚡ Automated Weekly Slate Runner")
        st.caption("Pulls every active NCAAF matchup currently on the board, maps Vegas team names to the CFBD power matrix, and logs actionable edges.")
        
        if st.button("▶ Auto-Run & Log Active NCAAF Slate"):
            with st.spinner("Processing live odds against Composite Power Ratings..."):
                slate_logs = []
                new_logs_count = 0
                date_str = get_local_date_str()
                
                st.write(f"Debug: Found {len(live_odds)} games with live odds.")
                
                for game_key, odds in live_odds.items():
                    try:
                        away_odds_name, home_odds_name = game_key.split(" @ ")
                        a_ml, h_ml = odds
                        
                        away_t = map_odds_to_cfbd(away_odds_name, cfb_teams)
                        home_t = map_odds_to_cfbd(home_odds_name, cfb_teams)
                        
                        if away_t and home_t and away_t != home_t:
                            away_base_pwr = cfb_power_matrix[away_t]
                            home_base_pwr = cfb_power_matrix[home_t]
                            
                            hfa = 3.0 # College HFA standard 
                            
                            adj_power_away = away_base_pwr 
                            adj_power_home = home_base_pwr + hfa
                            
                            prob_away = 1 / (1 + 10 ** ((adj_power_home - adj_power_away) / 25))
                            prob_home = 1.0 - prob_away
                            
                            v_prob_a = calculate_implied_prob(a_ml)
                            v_prob_h = calculate_implied_prob(h_ml)
                            
                            action_taken = "No Edge"
                            if prob_away > v_prob_a + 0.03: action_taken = away_t
                            if prob_home > v_prob_h + 0.03: action_taken = home_t

                            confidence_tier = "Low"

                            if action_taken == away_t:
                                confidence_tier = get_confidence_tier(model_away_prob, v_a_prob)

                            elif action_taken == home_t:
                                confidence_tier = get_confidence_tier(model_home_prob, v_h_prob)
                            
                            if action_taken != "No Edge":
                                row_data = [date_str, away_t, home_t, a_ml, h_ml, f"{prob_away:.1%}", f"{prob_home:.1%}", action_taken, "PENDING"]
                                log_status = log_ncaaf_to_sheets(row_data)
                                if log_status in ["SUCCESS", "DUPLICATE"]:
                                    slate_logs.append(row_data)
                                    if log_status == "SUCCESS":
                                        new_logs_count += 1
                    except Exception as e:
                        continue
                        
                if slate_logs:
                    st.success(f"✅ Successfully processed {len(slate_logs)} actionable edges! ({new_logs_count} new entries logged to Sheets)")
                    df_display = pd.DataFrame(slate_logs, columns=["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Confidence", "Status"])
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.info("No actionable edges found on the active NCAAF slate.")

    st.markdown("---")

    col1, col2 = st.columns([1, 1.2])
    with col1:
        st.subheader("Matchup Override & Situational Matrix")
        
        idx_away = cfb_teams.index("Oklahoma") if "Oklahoma" in cfb_teams else 0
        idx_home = cfb_teams.index("Oklahoma State") if "Oklahoma State" in cfb_teams else 1
        
        away_team = st.selectbox("Away Team:", cfb_teams, index=idx_away)
        home_team = st.selectbox("Home Team:", cfb_teams, index=idx_home)
        
        st.markdown("---")
        st.write("### 🛠️ Situational Modifiers")
        
        with st.expander("Quarterback Injury / Transfer Downgrade"):
            st.caption("College football lines shift dramatically on QB news. Adjust base power to account for backups.")
            away_qb_penalty = st.slider(f"{away_team} QB Penalty (Power Points):", 0.0, 15.0, 0.0, step=0.5)
            home_qb_penalty = st.slider(f"{home_team} QB Penalty (Power Points):", 0.0, 15.0, 0.0, step=0.5)

        with st.expander("Look-Ahead / Let-Down Spot"):
            st.caption("Dock a team 2-3 power points if they are coming off an emotional win or looking ahead to a massive rivalry next week.")
            letdown_away = st.checkbox(f"{away_team} is in a Let-Down Spot")
            letdown_home = st.checkbox(f"{home_team} is in a Let-Down Spot")
        
        st.markdown("---")
        st.write("### ⚙️ Engine Calibration")
        away_base_pwr = st.slider(f"{away_team} Base CPR:", 0.0, 100.0, float(cfb_power_matrix[away_team]), step=0.5)
        home_base_pwr = st.slider(f"{home_team} Base CPR:", 0.0, 100.0, float(cfb_power_matrix[home_team]), step=0.5)
        
        hfa = st.number_input("NCAAF Home Field Advantage (Power Points):", value=3.0, step=0.5)

    with col2:
        st.subheader("Simulated Prediction Outputs")
        
        ld_mod_away = 2.5 if letdown_away else 0.0
        ld_mod_home = 2.5 if letdown_home else 0.0
        
        adj_power_away = away_base_pwr - away_qb_penalty - ld_mod_away
        adj_power_home = home_base_pwr - home_qb_penalty - ld_mod_home + hfa
        
        prob_away = 1 / (1 + 10 ** ((adj_power_home - adj_power_away) / 25))
        prob_home = 1.0 - prob_away
        
        st.write(f"Engine Calibration: {away_team} (Adj Power: **{adj_power_away:.1f}**) vs {home_team} (Adj Power: **{adj_power_home:.1f}**)")
        st.write("")
        
        res_c1, res_c2 = st.columns(2)
        with res_c1:
            st.metric(f"{away_team} Win Probability:", f"{prob_away:.1%}")
        with res_c2:
            st.metric(f"{home_team} Win Probability:", f"{prob_home:.1%}")
            
        predicted_winner = away_team if prob_away > prob_home else home_team
        st.info(f"🏆 Predicted Winner: **{predicted_winner}**")

        st.markdown("#### Odds vs Model Edge")
        away_odds_input = st.number_input(f"{away_team} Live Moneyline:", value=150, step=10)
        home_odds_input = st.number_input(f"{home_team} Live Moneyline:", value=-175, step=10)

        v_prob_a = calculate_implied_prob(away_odds_input)
        v_prob_h = calculate_implied_prob(home_odds_input)
        
        edge_a = prob_away - v_prob_a
        edge_h = prob_home - v_prob_h
        
        st.write(f"Vegas Implied {away_team}: **{v_prob_a:.1%}** | Model Edge: **{edge_a:.1%}**")
        st.write(f"Vegas Implied {home_team}: **{v_prob_h:.1%}** | Model Edge: **{edge_h:.1%}**")
        
        if st.button("💾 Log NCAAF Matchup to Google Sheets"):
            date_str = get_local_date_str() 
            row_data = [
                date_str, away_team, home_team, 
                away_odds_input, home_odds_input, 
                f"{prob_away:.1%}", f"{prob_home:.1%}", 
                predicted_winner, "PENDING"
            ]
            with st.spinner("Logging NCAAF prediction..."):
                status = log_ncaaf_to_sheets(row_data)
                if status == "SUCCESS":
                    st.success("✅ Logged successfully to the 'NCAAF Log' tab!")
                elif status == "DUPLICATE":
                    st.info("ℹ️ This matchup is already logged for today.")
