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
sport = st.sidebar.selectbox("Select Sport Engine:", ["⚾ MLB Baseball", "🥎 NCAA Softball"])
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

# ==========================================================
# SPORT BRANCH 1: MLB BASEBALL (MLB API NATIVE)
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
                                v_a_prob = 100/(a_ml+100) if a_ml > 0 else abs(a_ml)/(abs(a_ml)+100)
                                v_h_prob = 100/(h_ml+100) if h_ml > 0 else abs(h_ml)/(abs(h_ml)+100)
                                
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
            st.caption("Since it is the offseason, assign your projected 2026 PPR Points (or Dynasty Value metric) for the selected assets.")
            
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
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            try:
                worksheet = sh.worksheet("Softball Log")
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sh.add_worksheet(title="Softball Log", rows="1000", cols="10")
                
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
        except Exception as e:
            if "200" in str(e): return "SUCCESS"
            st.error(f"Softball Sheet Log Error: {e}")
            return "ERROR"

    def get_softball_log_stats():
        try:
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("Softball Log")
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
            
            # Layer 1: ESPN Primary Feed
            espn_urls = [
                f"https://site.api.espn.com/apis/site/v2/sports/softball/college-softball/scoreboard?dates={year}{month}{day}&limit=500&groups=50",
                f"https://site.api.espn.com/apis/site/v2/sports/softball/college-softball/scoreboard?dates={year}{month}{day}&limit=500"
            ]
            for e_url in espn_urls:
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
                
            # Layer 3: ESPN HTML Text Parser
            try:
                resp = requests.get(f"https://www.espn.com/college-softball/scoreboard/_/date/{year}{month}{day}", timeout=7)
                teams = re.findall(r'<div class="ScoreCell__TeamName[^>]*>(.*?)</div>', resp.text)
                if teams and len(teams) % 2 == 0:
                    for i in range(0, len(teams), 2):
                        away_t, home_t = teams[i], teams[i+1]
                        if away_t and home_t and (away_t, home_t) not in games_list:
                            games_list.append((away_t, home_t))
            except: pass

            return games_list
        except Exception:
            return []

    def map_ncaa_to_warren_nolan(ncaa_name, valid_teams):
        ncaa_clean = ncaa_name.lower().replace(".", "").replace(" ", "").strip()
        
        for vt in valid_teams:
            vt_clean = vt.lower().replace(" ", "").strip()
            if ncaa_clean == vt_clean or ncaa_clean in vt_clean or vt_clean in ncaa_clean:
                return vt
        
        abbreviations = {
            'oklahoma st': 'Oklahoma State', 'oklahoma state': 'Oklahoma State', 'okla st': 'Oklahoma State',
            'oklahoma': 'Oklahoma', 'fsu': 'Florida State', 'florida st': 'Florida State',
            'florida state': 'Florida State', 'arizona st': 'Arizona State', 'arizona state': 'Arizona State',
            'boston u': 'Boston University', 'boston university': 'Boston University',
            'michigan st': 'Michigan State', 'michigan state': 'Michigan State',
            'mississippi st': 'Mississippi State', 'mississippi state': 'Mississippi State', 'miss state': 'Mississippi State',
            'nc state': 'North Carolina State', 'north carolina state': 'North Carolina State',
            'penn st': 'Penn State', 'penn state': 'Penn State',
            'san diego st': 'San Diego State', 'san diego state': 'San Diego State',
            'south carolina': 'South Carolina', 'texas am': 'Texas A&M', 'texas a&m': 'Texas A&M',
            'texas tech': 'Texas Tech', 'virginia tech': 'Virginia Tech', 'va tech': 'Virginia Tech',
            'wichita st': 'Wichita State', 'wichita state': 'Wichita State'
        }
        for k, v in abbreviations.items():
            if k in ncaa_clean and v in valid_teams:
                return v
        return None

    def auto_grade_softball_pending_bets(valid_teams):
        try:
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("Softball Log")
            data = worksheet.get_all_values()
            
            pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
            if not pending_rows: return 0
            
            pending_dates = list(set([row[0] for i, row in pending_rows]))
            score_dict = {}
            
            for d_str in pending_dates:
                try:
                    dt = datetime.strptime(d_str, "%Y-%m-%d")
                    year, month, day = dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")
                    
                    # 1. ESPN Primary Feed
                    espn_urls = [
                        f"https://site.api.espn.com/apis/site/v2/sports/softball/college-softball/scoreboard?dates={year}{month}{day}&limit=500&groups=50",
                        f"https://site.api.espn.com/apis/site/v2/sports/softball/college-softball/scoreboard?dates={year}{month}{day}&limit=500"
                    ]
                    for e_url in espn_urls:
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
                        
                    # 3. ESPN HTML Text Parser Fallback
                    try:
                        resp = requests.get(f"https://www.espn.com/college-softball/scoreboard/_/date/{year}{month}{day}", timeout=7)
                        teams = re.findall(r'<div class="ScoreCell__TeamName[^>]*>(.*?)</div>', resp.text)
                        scores = re.findall(r'<div class="ScoreCell__Score[^>]*>(.*?)</div>', resp.text)
                        if teams and scores and len(teams) == len(scores) and len(teams) % 2 == 0:
                            for i in range(0, len(teams), 2):
                                t1_name = map_ncaa_to_warren_nolan(teams[i], valid_teams)
                                t2_name = map_ncaa_to_warren_nolan(teams[i+1], valid_teams)
                                if t1_name and t2_name:
                                    try:
                                        s1 = int(scores[i])
                                        s2 = int(scores[i+1])
                                    except ValueError:
                                        s1, s2 = 0, 0
                                    winner = t1_name if s1 > s2 else t2_name
                                    score_dict[f"{d_str}_{t1_name.lower()}"] = winner.lower()
                                    score_dict[f"{d_str}_{t2_name.lower()}"] = winner.lower()
                    except: pass
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
