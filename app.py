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
from utils import get_local_date_str, clean_name, calculate_implied_prob, get_confidence_tier
from google_sheets import get_google_client, get_google_worksheet
from config import APP_TITLE, APP_PAGE_TITLE, CACHE_TTL_SHORT, CACHE_TTL_ODDS, CACHE_TTL_STATS, CACHE_TTL_DAILY, DEFAULT_SIMULATION_SIZE, MIN_ACTIONABLE_EDGE
from constants import MLB_PARK_FACTORS
from mlb_recent_form import calculate_recent_form_adjustment, fetch_recent_mlb_team_form
from mlb_pitcher_form import blend_pitcher_form, fetch_pitcher_recent_era

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
# MASTER SPORT ROUTER
# ==========================================================
st.sidebar.title(APP_TITLE)
sport = st.sidebar.selectbox("Select Sport Engine:", ["⚾ MLB Baseball", "🥎 NCAA Softball", "🏈 NFL Football", "🎓 NCAA Football"])
st.sidebar.markdown("---")

# ==========================================================
# SPORT BRANCH 1: MLB BASEBALL
# ==========================================================
if sport == "⚾ MLB Baseball":
    page = st.sidebar.radio("Select Engine:", ["🎲 Monte Carlo Simulation Engine", "🏆 Fantasy Sports Predictor"])
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

            h_url = "https://statsapi.mlb.com/api/v1/stats?stats=season&group=hitting&playerPool=ALL&season=2026&limit=1500"
            h_resp = requests.get(h_url, timeout=15).json()
            for h in h_resp.get('stats', [{}])[0].get('splits', []):
                h_name = clean_name(h['player']['fullName'])
                s = h['stat']
                hitter_data[h_name] = {
                    'H': s.get('hits',0), '2B': s.get('doubles',0), '3B': s.get('triples',0), 'HR': s.get('homeRuns',0),
                    'BB': s.get('baseOnBalls',0), 'R': s.get('runs',0), 'RBI': s.get('rbi',0), 'SB': s.get('stolenBases',0),
                    'SO': s.get('strikeOuts',0), 'G': s.get('gamesPlayed', 1) or 1
                }
                
        except Exception as e:
            st.error(f"API Sync Warning: {e}")
            
        return team_data, pitcher_data, hitter_data

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
                                
                                a_run_prevention = (a_sp_fip * 0.60) + (a_ra_g * 0.40)
                                h_run_prevention = (h_sp_fip * 0.60) + (h_ra_g * 0.40)
                                p_factor = PARK_FACTORS.get(home_t, 100) / 100
                                
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
                                
                                if action_taken != "No Edge":
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
            away_pitchers = sorted([p for p, data in pitcher_stats.items() if data.get('Team') == away_t]) if 'pitcher_stats' in locals() else []
            away_sp = st.selectbox(f"{away_t} SP Override:", ["League Average SP"] + away_pitchers)
        
        with col_b:
            home_t = st.selectbox("Home Team:", MLB_TEAMS, index=1)
            home_pitchers = sorted([p for p, data in pitcher_stats.items() if data.get('Team') == home_t]) if 'pitcher_stats' in locals() else []
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

                st.caption(
                    f"Pitcher Form Blend | "
                    f"{away_sp}: Recent ERA {a_recent_era:.2f} | "
                    f"{home_sp}: Recent ERA {h_recent_era:.2f}"
                )
                
                a_run_prevention = (a_sp_fip * 0.60) + (a_ra_g * 0.40)
                h_run_prevention = (h_sp_fip * 0.60) + (h_ra_g * 0.40)
                
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
                    st.markdown(f"#### {away_t}")
                    st.metric("Recent Runs/Game", f"{away_recent_rs:.2f}")
                    st.metric("Recent Runs Allowed/Game", f"{away_recent_ra:.2f}")
                    st.metric("Recent Run Diff", f"{away_recent_rs - away_recent_ra:+.2f}")
                    st.metric("Recent Games Sample", away_recent_games)
                    st.caption(momentum_label(away_recent_rs, away_recent_ra))
                
                with tm2:
                    st.markdown(f"#### {home_t}")
                    st.metric("Recent Runs/Game", f"{home_recent_rs:.2f}")
                    st.metric("Recent Runs Allowed/Game", f"{home_recent_ra:.2f}")
                    st.metric("Recent Run Diff", f"{home_recent_rs - home_recent_ra:+.2f}")
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
                    st.metric(f"{away_t} Win Prob", f"{model_away_prob:.1%}")
                
                with edge_col2:
                    st.metric("Model Lean", fav_team)
                    st.progress(float(fav_prob))
                
                with edge_col3:
                    st.metric(f"{home_t} Win Prob", f"{model_home_prob:.1%}")
                    st.caption(edge_label)

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

    elif page == "🏆 Fantasy Sports Predictor":
        st.title("🏆 Season-Long Fantasy Hub")
        st.markdown("### 🏈 NFL (Sleeper PPR) & ⚾ MLB (Standard Points)")
        
        fantasy_sport = st.radio("Select Active Fantasy Sport:", ["⚾ MLB Trade Analyzer & Projections", "🏈 NFL Sleeper PPR Trade Engine"])
        st.markdown("---")
        
        if fantasy_sport == "⚾ MLB Trade Analyzer & Projections":
            st.subheader("⚖️ ESPN Standard Points Trade Analyzer")
            st.caption("Calculates Rest-of-Season (ROS) projections natively via MLB API data logs.")
            
            with st.spinner("Compiling League-Wide Player Database..."):
                _, p_stats, h_stats = fetch_mlb_api_data()
                
                if not p_stats or not h_stats:
                    st.error("🚨 Could not sync with MLB Stats API.")
                else:
                    hitter_list = []
                    for name, s in h_stats.items():
                        tb = (s['H'] - s['2B'] - s['3B'] - s['HR']) + (2*s['2B']) + (3*s['3B']) + (4*s['HR'])
                        fpts = tb + s['BB'] + s['R'] + s['RBI'] + s['SB'] - s['SO']
                        fpts_per_g = fpts / s['G']
                        ros_proj = fpts_per_g * (162 - s['G'])
                        hitter_list.append({'Name': name, 'Position': 'Batter', 'FPts': fpts, 'FPts_per_G': round(fpts_per_g, 2), 'ROS_Proj': round(ros_proj, 1)})
                    
                    pitcher_list = []
                    for name, s in p_stats.items():
                        fpts = (s['IP'] * 3) + s['K'] + (s['W'] * 5) + (s['SV'] * 5) - (s['L'] * 5) - (s['ER'] * 2) - s['H'] - s['BB']
                        fpts_per_g = fpts / s['G']
                        ros_proj = fpts_per_g * (162 - s['G'])
                        pitcher_list.append({'Name': name, 'Position': 'Pitcher', 'FPts': fpts, 'FPts_per_G': round(fpts_per_g, 2), 'ROS_Proj': round(ros_proj, 1)})
                        
                    fantasy_df = pd.DataFrame(hitter_list + pitcher_list)
                    fantasy_df = fantasy_df.dropna(subset=['ROS_Proj']).sort_values('ROS_Proj', ascending=False)
                    all_players = sorted(fantasy_df['Name'].astype(str).unique().tolist())
                    
                    col1, col2 = st.columns(2)
                    with col1: team_a = st.multiselect("Team A Receives:", all_players, key="team_a")
                    with col2: team_b = st.multiselect("Team B Receives:", all_players, key="team_b")
                        
                    if st.button("⚖️ Analyze Trade Edge"):
                        if team_a or team_b:
                            a_df = fantasy_df[fantasy_df['Name'].isin(team_a)]
                            b_df = fantasy_df[fantasy_df['Name'].isin(team_b)]
                            
                            a_proj = a_df['ROS_Proj'].sum()
                            b_proj = b_df['ROS_Proj'].sum()
                            
                            c1, c2 = st.columns(2)
                            with c1:
                                st.metric("Team A Total ROS Projection", f"{a_proj:.1f} FPts")
                                if not a_df.empty: st.dataframe(a_df[['Name', 'Position', 'FPts_per_G', 'ROS_Proj']], hide_index=True)
                            with c2:
                                st.metric("Team B Total ROS Projection", f"{b_proj:.1f} FPts")
                                if not b_df.empty: st.dataframe(b_df[['Name', 'Position', 'FPts_per_G', 'ROS_Proj']], hide_index=True)
                                
                            st.markdown("---")
                            diff = abs(a_proj - b_proj)
                            if a_proj > b_proj + 15:
                                st.success(f"📈 **Team A** wins this trade by a projected **{diff:.1f}** Rest-of-Season points.")
                            elif b_proj > a_proj + 15:
                                st.success(f"📈 **Team B** wins this trade by a projected **{diff:.1f}** Rest-of-Season points.")
                            else:
                                st.info(f"🤝 This trade is highly balanced. Only a **{diff:.1f}** point differential.")
                        else:
                            st.warning("Add players to both sides to analyze a trade.")

        elif fantasy_sport == "🏈 NFL Sleeper PPR Trade Engine":
            @st.cache_data(ttl=CACHE_TTL_DAILY)
            def load_sleeper_players():
                try:
                    url = "https://api.sleeper.app/v1/players/nfl"
                    resp = requests.get(url, timeout=15).json()
                    active_players = {}
                    for pid, pdata in resp.items():
                        if pdata.get('active'):
                            name = f"{pdata.get('first_name', '')} {pdata.get('last_name', '')}".strip()
                            pos = pdata.get('position', 'UNK')
                            team = pdata.get('team', 'FA')
                            active_players[pid] = {'Name': name, 'Pos': pos, 'Team': team}
                    return active_players
                except Exception:
                    return {}

            st.subheader("🏈 Sleeper PPR Dynasty & Redraft Analyzer")
            st.caption("Sync your Sleeper account or use the manual trade matrix to evaluate PPR values.")
            
            with st.spinner("Fetching live Sleeper player registry..."):
                sleeper_players = load_sleeper_players()
                
            if not sleeper_players:
                st.warning("⚠️ Could not sync with Sleeper API. Running in manual mode.")
                player_list = ["Christian McCaffrey (RB - SF)", "CeeDee Lamb (WR - DAL)", "Josh Allen (QB - BUF)", "Justin Jefferson (WR - MIN)", "Tyreek Hill (WR - MIA)"]
            else:
                player_list = [f"{data['Name']} ({data['Pos']} - {data['Team']})" for pid, data in sleeper_players.items() if data['Pos'] in ['QB', 'RB', 'WR', 'TE', 'K', 'DEF']]
                player_list = sorted(list(set(player_list)))
            
            st.markdown("### ⚖️ Trade Simulator")
            col1, col2 = st.columns(2)
            with col1:
                team_a_nfl = st.multiselect("Team A Receives:", player_list, key="nfl_team_a")
            with col2:
                team_b_nfl = st.multiselect("Team B Receives:", player_list, key="nfl_team_b")
                
            st.markdown("#### 🎯 PPR Value Assignment")
            st.caption("Assign your projected PPR Points (or Dynasty Value metric) for the selected assets.")
            
            c1, c2 = st.columns(2)
            a_val = 0
            b_val = 0
            with c1:
                for p in team_a_nfl:
                    val = st.number_input(f"Value for {p}:", value=200, step=10, key=f"val_{p}_a")
                    a_val += val
            with c2:
                for p in team_b_nfl:
                    val = st.number_input(f"Value for {p}:", value=200, step=10, key=f"val_{p}_b")
                    b_val += val
                    
            if st.button("⚖️ Calculate NFL Trade Edge"):
                if team_a_nfl or team_b_nfl:
                    st.markdown("---")
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric("Team A Total Value", f"{a_val} PPR Pts")
                    with col2:
                        st.metric("Team B Total Value", f"{b_val} PPR Pts")
                        
                    diff = abs(a_val - b_val)
                    if a_val > b_val + 20:
                        st.success(f"📈 **Team A** wins this trade by **{diff}** points.")
                    elif b_val > a_val + 20:
                        st.success(f"📈 **Team B** wins this trade by **{diff}** points.")
                    else:
                        st.info(f"🤝 This trade is highly balanced (Differential: {diff}).")
                else:
                    st.warning("Select players to analyze.")
                    
            st.markdown("---")
            st.subheader("📡 Sleeper League Sync")
            username = st.text_input("Enter Sleeper Username to view active leagues:", value="marcusmaximus06")
            if st.button("Sync Rosters"):
                with st.spinner("Pinging Sleeper API..."):
                    try:
                        user_resp = requests.get(f"https://api.sleeper.app/v1/user/{username}", timeout=15).json()
                        if user_resp and 'user_id' in user_resp:
                            user_id = user_resp['user_id']
                            leagues = requests.get(f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/2026", timeout=15).json()
                            if not leagues:
                                leagues = requests.get(f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/2025", timeout=15).json()
                            if leagues:
                                st.success(f"✅ Synced {len(leagues)} leagues for {username}!")
                                for league in leagues:
                                    st.write(f"🏆 **{league['name']}**")
                            else:
                                st.info("No active NFL leagues found for this user.")
                        else:
                            st.error("User not found on Sleeper.")
                    except Exception as e:
                        st.error("Failed to connect to Sleeper API. The connection may have been blocked or timed out.")

# ==========================================================
# SPORT BRANCH 2: NCAA SOFTBALL
# ==========================================================
elif sport == "🥎 NCAA Softball":
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
    st.title("🏈 NFL Ensemble Simulation Engine")
    st.markdown("### 📊 Elo + Split EPA Power Ratings")
    st.caption("*Fuses structural base Elo ratings with weighted Pass/Rush Expected Points Added (EPA) per play. Includes situational edges.*")
    
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
                team_name = team.get('team')
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
