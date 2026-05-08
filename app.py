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

# --- CLOUDFLARE BYPASS V8: THE SMART TLS SPOOFER ---
original_get = requests.get
def custom_get(url, **kwargs):
    if "googleapis.com" in str(url) or "googleusercontent.com" in str(url):
        return original_get(url, **kwargs)
    kwargs.pop('headers', None) 
    try:
        return cffi_requests.get(url, impersonate="chrome120", **kwargs)
    except Exception:
        return original_get(url, **kwargs)
requests.get = custom_get

original_post = requests.post
def custom_post(url, **kwargs):
    if "googleapis.com" in str(url) or "googleusercontent.com" in str(url):
        return original_post(url, **kwargs)
    kwargs.pop('headers', None) 
    try:
        return cffi_requests.post(url, impersonate="chrome120", **kwargs)
    except Exception:
        return original_post(url, **kwargs)
requests.post = custom_post

original_request = requests.Session.request
def custom_request(self, method, url, **kwargs):
    if "googleapis.com" in str(url) or "googleusercontent.com" in str(url):
        return original_request(self, method, url, **kwargs)
    kwargs.pop('headers', None)
    try:
        return cffi_requests.request(method, url, impersonate="chrome120", **kwargs)
    except Exception:
        return original_request(self, method, url, **kwargs)
requests.Session.request = custom_request
# ---------------------------------------

st.set_page_config(page_title="Apex Multi-Sport Analytics", layout="wide")

# ==========================================================
# MASTER SPORT ROUTER
# ==========================================================
st.sidebar.title("Apex Quantitative Syndicate")
sport = st.sidebar.selectbox("Select Sport Engine:", ["⚾ MLB Baseball", "🏈 NFL Football"])
st.sidebar.markdown("---")

def get_local_date_str():
    utc_now = datetime.utcnow()
    central_now = utc_now - timedelta(hours=5) 
    return central_now.strftime("%Y-%m-%d")

def clean_name(name):
    if not isinstance(name, str): return name
    replacements = {
        r'\xc3\xad': 'í', r'\xc3\xa1': 'á', r'\xc3\xa9': 'é',
        r'\xc3\xb1': 'ñ', r'\xc3\xb3': 'ó', r'\xc3\xba': 'ú',
        r'\xc3\x8d': 'Í', r'\xc3\x81': 'Á', r'\xc3\x89': 'É'
    }
    for bad, good in replacements.items():
        name = name.replace(bad, good)
    return name

def calculate_implied_prob(american_odds):
    """Accurately calculates Vegas implied probability."""
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return abs(american_odds) / (abs(american_odds) + 100)

# ==========================================================
# SPORT BRANCH 1: MLB BASEBALL
# ==========================================================
if sport == "⚾ MLB Baseball":
    page = st.sidebar.radio("Select Engine:", ["🎲 Monte Carlo Simulation Engine", "🏆 Fantasy Sports Predictor"])
    st.sidebar.markdown("---")

    PARK_FACTORS = {
        'Arizona Diamondbacks': 102, 'Atlanta Braves': 100, 'Baltimore Orioles': 98, 'Boston Red Sox': 107, 
        'Chicago Cubs': 102, 'Chicago White Sox': 102, 'Cincinnati Reds': 111, 'Cleveland Guardians': 101, 
        'Colorado Rockies': 114, 'Detroit Tigers': 98, 'Houston Astros': 96, 'Kansas City Royals': 101, 
        'Los Angeles Angels': 97, 'Los Angeles Dodgers': 97, 'Miami Marlins': 95, 'Milwaukee Brewers': 101, 
        'Minnesota Twins': 99, 'New York Mets': 99, 'New York Yankees': 99, 'Oakland Athletics': 94, 
        'Philadelphia Phillies': 102, 'Pittsburgh Pirates': 98, 'San Diego Padres': 94, 'San Francisco Giants': 95, 
        'Seattle Mariners': 92, 'St. Louis Cardinals': 97, 'Tampa Bay Rays': 93, 'Texas Rangers': 103, 
        'Toronto Blue Jays': 101, 'Washington Nationals': 101
    }

    @st.cache_data(ttl=3600)
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

    @st.cache_data(ttl=7200)
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
                
                pitcher_data[p_name] = {'FIP': fip, 'Team': t_name, 'IP': ip, 'K': s.get('strikeOuts', 0), 'W': s.get('wins',0), 'SV': s.get('saves',0), 'L': s.get('losses',0), 'ER': s.get('earnedRuns',0), 'H': s.get('hits',0), 'BB': s.get('baseOnBalls',0), 'G': s.get('gamesPlayed', 1) or 1}

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
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("Master Log")
            values = worksheet.get_all_values()
            
            if not values or len(values) == 0:
                worksheet.append_row(["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Result"])
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

    def get_master_log_stats():
        try:
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("Master Log")
            data = worksheet.get_all_values()
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
            return total_games, mod_acc, veg_acc
        except Exception: return 0, 0.0, 0.0

    def auto_grade_pending_bets():
        try:
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("Master Log")
            data = worksheet.get_all_values()
            pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
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
                    worksheet.update_cell(i + 1, 9, new_status)
                    updates += 1
            return updates
        except Exception as e:
            st.error(f"Auto-Grader Error: {e}")
            return -1

    if page == "🎲 Monte Carlo Simulation Engine":
        st.title("🎲 Monte Carlo Simulation Engine")
        st.markdown("### 📊 Live Model Log & Automation")
        tot_games, mod_acc, veg_acc = get_master_log_stats()
        col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
        with col1: st.metric(label="Total Graded Games", value=tot_games)
        with col2: st.metric(label="Model Accuracy", value=f"{mod_acc:.1f}%")
        with col3: st.metric(label="Vegas Accuracy", value=f"{veg_acc:.1f}%")
        with col4: 
            st.write("")
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

                                a_run_prevention = (a_sp_fip * 0.60) + (a_ra_g * 0.40)
                                h_run_prevention = (h_sp_fip * 0.60) + (h_ra_g * 0.40)
                                p_factor = PARK_FACTORS.get(home_t, 100) / 100
                                
                                away_lam = ((a_rs_g + h_run_prevention) / 2) * p_factor
                                home_lam = ((h_rs_g + a_run_prevention) / 2) * p_factor
                                
                                sim_a = np.random.poisson(away_lam, 10000)
                                sim_h = np.random.poisson(home_lam, 10000)
                                a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                                h_wins = 10000 - a_wins
                                model_away_prob, model_home_prob = a_wins / 10000, h_wins / 10000
                                
                                v_a_prob = calculate_implied_prob(a_ml)
                                v_h_prob = calculate_implied_prob(h_ml)
                                
                                action_taken = "No Edge"
                                if model_away_prob > v_a_prob + 0.03: action_taken = away_t
                                if model_home_prob > v_h_prob + 0.03: action_taken = home_t
                                
                                if action_taken != "No Edge":
                                    row_data = [date_str, away_t, home_t, a_ml, h_ml, f"{model_away_prob:.1%}", f"{model_home_prob:.1%}", action_taken, "PENDING"]
                                    log_status = log_to_google_sheets(row_data)
                                    if log_status in ["SUCCESS", "DUPLICATE"]:
                                        slate_logs.append(row_data)
                                        if log_status == "SUCCESS":
                                            new_logs_count += 1
                            except: continue
                            
                        if slate_logs:
                            st.success(f"✅ Successfully processed {len(slate_logs)} actionable edges! ({new_logs_count} new entries logged to Sheets)")
                            st.markdown("#### 📅 Today's MLB Actionable Edges")
                            df_display = pd.DataFrame(slate_logs, columns=["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Status"])
                            st.dataframe(df_display, use_container_width=True, hide_index=True)
                        else:
                            st.info("No actionable edges found on today's MLB slate.")

        st.markdown("---")
        st.subheader("Manual Matchup Override")
        st.caption("Standalone Engine: Calculates probability edges using native MLB API logic and visualizes Poisson distributions.")
        
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

                a_run_prevention = (a_sp_fip * 0.60) + (a_ra_g * 0.40)
                h_run_prevention = (h_sp_fip * 0.60) + (h_ra_g * 0.40)
                
                away_lam = ((a_rs_g + h_run_prevention) / 2) * p_factor
                home_lam = ((h_rs_g + a_run_prevention) / 2) * p_factor

                sim_a = np.random.poisson(away_lam, 10000)
                sim_h = np.random.poisson(home_lam, 10000)
                a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                h_wins = 10000 - a_wins
                model_away_prob, model_home_prob = a_wins / 10000, h_wins / 10000
                
                st.write(f"Final Expected Runs: {away_t} **{away_lam:.2f}** | {home_t} **{home_lam:.2f}**")
                
                res_c1, res_c2 = st.columns(2)

                with res_c1:
                    st.metric(f"{away_t} Win Prob", f"{model_away_prob:.1%}")
        
                with res_c2:
                    st.metric(f"{home_t} Win Prob", f"{model_home_prob:.1%}")

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
            @st.cache_data(ttl=86400)
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
# SPORT BRANCH 2: NFL FOOTBALL (EPA + ELO HYBRID ENGINE)
# ==========================================================
elif sport == "🏈 NFL Football":
    st.title("🏈 NFL Ensemble Simulation Engine")
    st.markdown("### 📊 Elo + EPA/Play Hybrid Power Ratings")
    st.caption("*Fuses structural base Elo ratings with high-variance Expected Points Added (EPA) per play for ultimate predictive accuracy.*")
    
    def log_nfl_to_sheets(row_data):
        try:
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("NFL Prediction Model") # Update sheet name if needed
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

    @st.cache_data(ttl=3600)
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

    @st.cache_data(ttl=86400) # Cache clears once every 24 hours
    def generate_baseline_power_matrix():
        # 1. Structural Base Elo (Updates slowly)
        base_elo = {
            'ARI': 1480, 'ATL': 1495, 'BAL': 1650, 'BUF': 1640, 'CAR': 1350, 'CHI': 1490, 'CIN': 1560, 'CLE': 1540,
            'DAL': 1600, 'DEN': 1460, 'DET': 1620, 'GB':  1580, 'HOU': 1570, 'IND': 1510, 'JAX': 1500, 'KC':  1680,
            'LV':  1470, 'LAC': 1520, 'LAR': 1550, 'MIA': 1590, 'MIN': 1510, 'NE':  1420, 'NO':  1500, 'NYG': 1430,
            'NYJ': 1510, 'PHI': 1610, 'PIT': 1550, 'SF':  1660, 'SEA': 1520, 'TB':  1540, 'TEN': 1450, 'WAS': 1440
        }
        
        power_matrix = {}
        teams = nfl.import_team_desc()
        
        try:
            # 2. Fetch Live Season Play-by-Play Data (Fast parquet download)
            current_year = 2026
            pbp = nfl.import_pbp_data([current_year])
            
            # Filter to regular season passing/rushing plays only
            pbp = pbp[(pbp['season_type'] == 'REG') & (pbp['play_type'].isin(['pass', 'run']))]
            
            # 3. Calculate Aggregate EPA
            off_epa = pbp.groupby('posteam')['epa'].mean().to_dict()
            def_epa = pbp.groupby('defteam')['epa'].mean().to_dict()

            # 4. Merge Live EPA with Base Elo
            for index, row in teams.iterrows():
                abbr = row['team_abbr']
                if abbr in base_elo:
                    power_matrix[abbr] = {
                        'Elo': base_elo[abbr],
                        'Off_EPA': round(off_epa.get(abbr, 0.0), 3),
                        'Def_EPA': round(def_epa.get(abbr, 0.0), 3),
                        'Name': row['team_name']
                    }
        except Exception as e:
            st.error("Live EPA sync failed. Using static baseline.")
            # Fallback block if nflfastR is down
            for index, row in teams.iterrows():
                abbr = row['team_abbr']
                if abbr in base_elo:
                    power_matrix[abbr] = {
                        'Elo': base_elo[abbr], 'Off_EPA': 0.0, 'Def_EPA': 0.0, 'Name': row['team_name']
                    }
                    
        return power_matrix
        
        # Simulated Baseline Metrics (will be overridden by live API data during season)
        power_matrix = {
            'ARI': {'Elo': 1480, 'Off_EPA': 0.02, 'Def_EPA': 0.05, 'Name': 'Arizona Cardinals'},
            'ATL': {'Elo': 1495, 'Off_EPA': -0.01, 'Def_EPA': 0.02, 'Name': 'Atlanta Falcons'},
            'BAL': {'Elo': 1650, 'Off_EPA': 0.12, 'Def_EPA': -0.08, 'Name': 'Baltimore Ravens'},
            'BUF': {'Elo': 1640, 'Off_EPA': 0.14, 'Def_EPA': -0.05, 'Name': 'Buffalo Bills'},
            'CAR': {'Elo': 1350, 'Off_EPA': -0.15, 'Def_EPA': 0.08, 'Name': 'Carolina Panthers'},
            'CHI': {'Elo': 1490, 'Off_EPA': 0.01, 'Def_EPA': 0.01, 'Name': 'Chicago Bears'},
            'CIN': {'Elo': 1560, 'Off_EPA': 0.08, 'Def_EPA': 0.04, 'Name': 'Cincinnati Bengals'},
            'CLE': {'Elo': 1540, 'Off_EPA': -0.02, 'Def_EPA': -0.09, 'Name': 'Cleveland Browns'},
            'DAL': {'Elo': 1600, 'Off_EPA': 0.10, 'Def_EPA': -0.03, 'Name': 'Dallas Cowboys'},
            'DEN': {'Elo': 1460, 'Off_EPA': -0.04, 'Def_EPA': 0.03, 'Name': 'Denver Broncos'},
            'DET': {'Elo': 1620, 'Off_EPA': 0.11, 'Def_EPA': -0.02, 'Name': 'Detroit Lions'},
            'GB':  {'Elo': 1580, 'Off_EPA': 0.09, 'Def_EPA': 0.01, 'Name': 'Green Bay Packers'},
            'HOU': {'Elo': 1570, 'Off_EPA': 0.07, 'Def_EPA': -0.01, 'Name': 'Houston Texans'},
            'IND': {'Elo': 1510, 'Off_EPA': 0.03, 'Def_EPA': 0.02, 'Name': 'Indianapolis Colts'},
            'JAX': {'Elo': 1500, 'Off_EPA': 0.02, 'Def_EPA': 0.05, 'Name': 'Jacksonville Jaguars'},
            'KC':  {'Elo': 1680, 'Off_EPA': 0.15, 'Def_EPA': -0.06, 'Name': 'Kansas City Chiefs'},
            'LV':  {'Elo': 1470, 'Off_EPA': -0.05, 'Def_EPA': -0.01, 'Name': 'Las Vegas Raiders'},
            'LAC': {'Elo': 1520, 'Off_EPA': 0.04, 'Def_EPA': 0.03, 'Name': 'Los Angeles Chargers'},
            'LAR': {'Elo': 1550, 'Off_EPA': 0.08, 'Def_EPA': 0.02, 'Name': 'Los Angeles Rams'},
            'MIA': {'Elo': 1590, 'Off_EPA': 0.10, 'Def_EPA': 0.04, 'Name': 'Miami Dolphins'},
            'MIN': {'Elo': 1510, 'Off_EPA': 0.04, 'Def_EPA': 0.01, 'Name': 'Minnesota Vikings'},
            'NE':  {'Elo': 1420, 'Off_EPA': -0.10, 'Def_EPA': -0.02, 'Name': 'New England Patriots'},
            'NO':  {'Elo': 1500, 'Off_EPA': 0.02, 'Def_EPA': 0.01, 'Name': 'New Orleans Saints'},
            'NYG': {'Elo': 1430, 'Off_EPA': -0.08, 'Def_EPA': 0.06, 'Name': 'New York Giants'},
            'NYJ': {'Elo': 1510, 'Off_EPA': -0.02, 'Def_EPA': -0.07, 'Name': 'New York Jets'},
            'PHI': {'Elo': 1610, 'Off_EPA': 0.09, 'Def_EPA': -0.01, 'Name': 'Philadelphia Eagles'},
            'PIT': {'Elo': 1550, 'Off_EPA': -0.01, 'Def_EPA': -0.05, 'Name': 'Pittsburgh Steelers'},
            'SF':  {'Elo': 1660, 'Off_EPA': 0.13, 'Def_EPA': -0.04, 'Name': 'San Francisco 49ers'},
            'SEA': {'Elo': 1520, 'Off_EPA': 0.05, 'Def_EPA': 0.05, 'Name': 'Seattle Seahawks'},
            'TB':  {'Elo': 1540, 'Off_EPA': 0.06, 'Def_EPA': 0.02, 'Name': 'Tampa Bay Buccaneers'},
            'TEN': {'Elo': 1450, 'Off_EPA': -0.06, 'Def_EPA': 0.04, 'Name': 'Tennessee Titans'},
            'WAS': {'Elo': 1440, 'Off_EPA': -0.05, 'Def_EPA': 0.08, 'Name': 'Washington Commanders'}
        }
        return power_matrix

    power_matrix = generate_baseline_power_matrix()
    full_team_names = [data['Name'] for abbr, data in power_matrix.items()]
    name_to_abbr = {data['Name']: abbr for abbr, data in power_matrix.items()}

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Manual Matchup Override")
        away_team_name = st.selectbox("Away Team:", sorted(full_team_names), index=3)
        home_team_name = st.selectbox("Home Team:", sorted(full_team_names), index=15)
        
        away_abbr = name_to_abbr[away_team_name]
        home_abbr = name_to_abbr[home_team_name]
        
        st.markdown("---")
        st.write("**Real-Time Rating Calibration**")
        
        away_elo = st.slider(f"{away_abbr} Base Elo:", 1200, 1800, power_matrix[away_abbr]['Elo'], step=5)
        away_epa = st.slider(f"{away_abbr} Net EPA/Play (Offense - Defense):", -0.50, 0.50, float(power_matrix[away_abbr]['Off_EPA'] - power_matrix[away_abbr]['Def_EPA']), step=0.01)
        
        home_elo = st.slider(f"{home_abbr} Base Elo:", 1200, 1800, power_matrix[home_abbr]['Elo'], step=5)
        home_epa = st.slider(f"{home_abbr} Net EPA/Play (Offense - Defense):", -0.50, 0.50, float(power_matrix[home_abbr]['Off_EPA'] - power_matrix[home_abbr]['Def_EPA']), step=0.01)
        
        hfa = st.number_input("Home Field Advantage (Elo Points):", value=45, step=5)

    with col2:
        st.subheader("Simulated Prediction Outputs")
        
        # Calculate Adjusted Power: Elo + (Net EPA * 400 scale factor)
        adj_power_away = away_elo + (away_epa * 400)
        adj_power_home = home_elo + (home_epa * 400) + hfa
        
        # Convert difference to Win Probability
        # P(Win) = 1 / (1 + 10^((Opp_Power - Team_Power)/400))
        prob_away = 1 / (1 + 10 ** ((adj_power_home - adj_power_away) / 400))
        prob_home = 1.0 - prob_away
        
        st.caption(f"*Engine Calibration: {away_abbr} (Adj Power: {adj_power_away:.1f}) vs {home_abbr} (Adj Power: {adj_power_home:.1f})*")
        st.write("")
        
        res_c1, res_c2 = st.columns(2)
        with res_c1:
            st.metric(f"{away_abbr} Win Probability:", f"{prob_away:.1%}")
        with res_c2:
            st.metric(f"{home_abbr} Win Probability:", f"{prob_home:.1%}")
            
        predicted_winner = away_team_name if prob_away > prob_home else home_team_name
        st.info(f"🏆 Predicted Winner: **{predicted_winner}**")

        st.markdown("#### Odds vs Model Edge")
        away_odds_input = st.number_input(f"{away_abbr} Live Moneyline:", value=150, step=10)
        home_odds_input = st.number_input(f"{home_abbr} Live Moneyline:", value=-175, step=10)

        v_prob_a = calculate_implied_prob(away_odds_input)
        v_prob_h = calculate_implied_prob(home_odds_input)
        
        st.write(f"Vegas Implied {away_abbr}: **{v_prob_a:.1%}** | Model Edge: **{(prob_away - v_prob_a):.1%}**")
        st.write(f"Vegas Implied {home_abbr}: **{v_prob_h:.1%}** | Model Edge: **{(prob_home - v_prob_h):.1%}**")
        
        if st.button("💾 Log NFL Matchup to Google Sheets"):
            date_str = get_local_date_str() 
            row_data = [
                date_str, away_team_name, home_team_name, 
                away_odds_input, home_odds_input, 
                f"{prob_away:.1%}", f"{prob_home:.1%}", 
                predicted_winner, "PENDING"
            ]
            with st.spinner("Logging NFL prediction..."):
                status = log_nfl_to_sheets(row_data)
                if status == "SUCCESS":
                    st.success("✅ Logged successfully to the 'NFL Log' tab!")
                elif status == "DUPLICATE":
                    st.info("ℹ️ This matchup is already logged for today.")
