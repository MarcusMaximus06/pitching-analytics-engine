import streamlit as st
import pandas as pd
import numpy as np
from pybaseball import pitching_stats_bref, batting_stats_bref, pitching_stats_range, batting_stats_range, statcast_pitcher_expected_stats, statcast_pitcher
import plotly.express as px
from datetime import datetime, timedelta
import traceback
import requests
import gspread
import os

st.set_page_config(page_title="Apex Multi-Sport Analytics", layout="wide")

# ==========================================================
# MASTER SPORT ROUTER
# ==========================================================
st.sidebar.title("Apex Quantitative Syndicate")
sport = st.sidebar.selectbox("Select Sport Engine:", ["⚾ MLB Baseball", "🥎 NCAA Softball"])
st.sidebar.markdown("---")

# --- CENTRALIZED TIMEZONE UTILITY ---
def get_local_date_str():
    utc_now = datetime.utcnow()
    central_now = utc_now - timedelta(hours=5) 
    return central_now.strftime("%Y-%m-%d")

# --- UNICODE CLEANER ---
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
# SPORT BRANCH 1: MLB BASEBALL
# ==========================================================
if sport == "⚾ MLB Baseball":
    page = st.sidebar.radio("Select Engine:", ["⚾ Pitching Analytics Matrix", "🎲 Monte Carlo Simulation Engine"])
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

    TEAM_NAME_MAP = {
        'Arizona Diamondbacks': 'Arizona', 'Atlanta Braves': 'Atlanta', 'Baltimore Orioles': 'Baltimore',
        'Boston Red Sox': 'Boston', 'Chicago Cubs': 'Chicago', 'Chicago White Sox': 'Chicago',
        'Cincinnati Reds': 'Cincinnati', 'Cleveland Guardians': 'Cleveland', 'Colorado Rockies': 'Colorado',
        'Detroit Tigers': 'Detroit', 'Houston Astros': 'Houston', 'Kansas City Royals': 'Kansas City',
        'Los Angeles Angels': 'Los Angeles', 'Los Angeles Dodgers': 'Los Angeles', 'Miami Marlins': 'Miami',
        'Milwaukee Brewers': 'Milwaukee', 'Minnesota Twins': 'Minnesota', 'New York Mets': 'New York',
        'New York Yankees': 'New York', 'Oakland Athletics': 'Athletics', 'Philadelphia Phillies': 'Philadelphia',
        'Pittsburgh Pirates': 'Pittsburgh', 'San Diego Padres': 'San Diego', 'San Francisco Giants': 'San Francisco',
        'Seattle Mariners': 'Seattle', 'St. Louis Cardinals': 'St. Louis', 'Tampa Bay Rays': 'Tampa Bay',
        'Texas Rangers': 'Texas', 'Toronto Blue Jays': 'Toronto', 'Washington Nationals': 'Washington'
    }

    FULL_TO_ABBR = {
        'Arizona Diamondbacks': 'ARI', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL', 'Boston Red Sox': 'BOS', 
        'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CHW', 'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 
        'Colorado Rockies': 'COL', 'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KCR', 
        'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA', 'Milwaukee Brewers': 'MIL', 
        'Minnesota Twins': 'MIN', 'New York Mets': 'NYM', 'New York Yankees': 'NYY', 'Oakland Athletics': 'OAK', 
        'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SDP', 'San Francisco Giants': 'SFG', 
        'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL', 'Tampa Bay Rays': 'TBR', 'Texas Rangers': 'TEX', 
        'Toronto Blue Jays': 'TOR', 'Washington Nationals': 'WSN'
    }

    @st.cache_data(ttl=3600)
    def get_live_odds():
        api_key = os.environ.get('ODDS_API_KEY')
        if not api_key: return {}
        url = f'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=american&bookmakers=draftkings,fanduel'
        try:
            response = requests.get(url)
            data = response.json()
            odds_dict = {}
            for game in data:
                if 'bookmakers' in game and len(game['bookmakers']) > 0:
                    outcomes = game['bookmakers'][0]['markets'][0]['outcomes']
                    away = game['away_team']
                    home = game['home_team']
                    away_ml = next((o['price'] for o in outcomes if o['name'] == away), 100)
                    home_ml = next((o['price'] for o in outcomes if o['name'] == home), -110)
                    odds_dict[f"{away} @ {home}"] = [away_ml, home_ml]
            return odds_dict
        except Exception: return {}

    def log_to_google_sheets(row_data):
        try:
            gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
            sh = gc.open("MLB Daily Prediction Model")
            worksheet = sh.worksheet("Master Log")
            
            values = worksheet.get_all_values()
            if not values or len(values) == 0:
                worksheet.append_row(["Date", "Away Team", "Home Team", "Away ML", "Home ML", "Model Away %", "Model Home %", "Model Pick", "Result"])
                
            worksheet.append_row(row_data)
            return True
        except Exception as e:
            if "200" in str(e): return True
            return False

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
                resp = requests.get(url).json()
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

    def calc_advanced_metrics(df):
        if 'BB9' not in df.columns: df['BB9'] = (df['BB'] / df['IP']) * 9
        if 'SO9' not in df.columns: df['SO9'] = (df['SO'] / df['IP']) * 9
        if 'FIP' not in df.columns:
            hbp = df['HBP'] if 'HBP' in df.columns else 0
            df['FIP'] = ((13 * df['HR']) + (3 * (df['BB'] + hbp)) - (2 * df['SO'])) / df['IP'] + 3.15
        df = df.dropna(subset=['ERA', 'FIP', 'SO9', 'BB9'])
        df['FPI'] = ((df['SO9'] * 1.5) - (df['BB9'] * 1.2) - df['FIP']).round(2)
        return df

    @st.cache_data
    def load_pitching_data():
        season_df = pitching_stats_bref(2026)
        season_df['Name'] = season_df['Name'].apply(clean_name)
        season_df = calc_advanced_metrics(season_df)
        today = datetime.now()
        two_weeks_ago = today - timedelta(days=14)
        recent_df = pitching_stats_range(two_weeks_ago.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
        if not recent_df.empty:
            recent_df['Name'] = recent_df['Name'].apply(clean_name)
            recent_df = calc_advanced_metrics(recent_df)
            recent_df = recent_df[['Name', 'FPI']].rename(columns={'FPI': 'Recent_FPI'})
            merged_df = pd.merge(season_df, recent_df, on='Name', how='left')
            merged_df['Momentum_Shift'] = (merged_df['Recent_FPI'] - merged_df['FPI']).round(2)
        else:
            merged_df = season_df
            merged_df['Recent_FPI'] = None
            merged_df['Momentum_Shift'] = None
        return merged_df

    @st.cache_data
    def get_team_data():
        try:
            bat_df = batting_stats_bref(2026)
            pitch_df = pitching_stats_bref(2026)
            team_bat = bat_df.groupby('Tm').agg(RS=('R', 'sum')).reset_index()
            team_pitch = pitch_df.groupby('Tm').agg(RA=('R', 'sum'), Team_G=('GS', 'sum')).reset_index()
            pitch_df['is_reliever'] = pitch_df['GS'] <= (pitch_df['G'] * 0.25)
            bp_df = pitch_df[pitch_df['is_reliever']]
            team_bp = bp_df.groupby('Tm').agg(BP_R=('R', 'sum'), BP_IP=('IP', 'sum')).reset_index()
            team_bp['BP_IP'] = team_bp['BP_IP'].replace(0, 1)
            team_bp['BP_RA9'] = (team_bp['BP_R'] / team_bp['BP_IP']) * 9
            team_df = pd.merge(team_bat, team_pitch, on='Tm')
            team_df = team_df[team_df['Tm'] != 'TOT']
            team_df['Team_G'] = team_df['Team_G'].replace(0, 1)
            team_df['RS_per_G'] = team_df['RS'] / team_df['Team_G']
            team_df['RA_per_G'] = team_df['RA'] / team_df['Team_G']
            team_df = pd.merge(team_df, team_bp[['Tm', 'BP_RA9']], on='Tm', how='left')
            team_df['BP_RA9'] = team_df['BP_RA9'].fillna(team_df['RA_per_G'])
            today = datetime.now()
            two_weeks_ago = today - timedelta(days=14)
            recent_bat = batting_stats_range(two_weeks_ago.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
            recent_bat_agg = recent_bat.groupby('Tm').agg(Recent_RS=('R', 'sum'), Recent_G=('G', 'max')).reset_index()
            recent_bat_agg['Recent_G'] = recent_bat_agg['Recent_G'].replace(0, 1)
            recent_bat_agg['Recent_RS_per_G'] = recent_bat_agg['Recent_RS'] / recent_bat_agg['Recent_G']
            return team_df.sort_values('Tm'), recent_bat_agg
        except Exception: return pd.DataFrame(), pd.DataFrame()

    # --- MLB PAGE UI ROUTING ---
    if page == "⚾ Pitching Analytics Matrix":
        st.title("⚾ Pitching Analytics Matrix")
        with st.spinner('Compiling matrix...'):
            try:
                raw_df = load_pitching_data()
                st.sidebar.header("Matrix Parameters")
                min_ip = st.sidebar.slider("Min IP (Leaderboard):", 1, int(raw_df['IP'].max()), 15)
                filtered_df = raw_df[raw_df['IP'] >= min_ip]
                selected_player = st.sidebar.selectbox("Target Profile Search:", ["All Pitchers"] + sorted(raw_df['Name'].unique().tolist()))
                display_cols = ['Name', 'Tm', 'IP', 'FPI', 'Momentum_Shift', 'ERA', 'SO9', 'BB9']
                
                if selected_player != "All Pitchers":
                    st.subheader(f"Isolated Profile: {selected_player}")
                    p_data = raw_df[raw_df['Name'] == selected_player]
                    st.dataframe(p_data[display_cols], hide_index=True)
                else:
                    st.subheader("League Overview: The Momentum Tracker")
                    st.dataframe(filtered_df[display_cols].sort_values('Momentum_Shift', ascending=False).head(25), hide_index=True)

                # --- THE RESTORED VISUAL ENGINE ---
                st.markdown("---")
                st.subheader("📊 FPI & Momentum Scatter Matrix")
                st.caption("*Hover over points to inspect profiles. The green quadrant represents elite FPI with positive recent momentum.*")
                
                fig = px.scatter(
                    filtered_df, 
                    x="FPI", 
                    y="Momentum_Shift", 
                    color="ERA",
                    hover_name="Name", 
                    hover_data=["Tm", "IP", "SO9", "BB9"],
                    color_continuous_scale=px.colors.diverging.RdYlGn_r, # Red is bad ERA, Green is good
                    labels={"Momentum_Shift": "14-Day Momentum Shift", "FPI": "Season FPI Rating"}
                )
                
                # Add crosshairs for league averages
                fig.add_hline(y=0, line_dash="dot", line_color="white", opacity=0.5)
                fig.add_vline(x=filtered_df['FPI'].median(), line_dash="dot", line_color="white", opacity=0.5)
                fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
                
                st.plotly_chart(fig, use_container_width=True)
                
            except Exception as e: st.error(f"Engine failure: {e}")

    elif page == "🎲 Monte Carlo Simulation Engine":
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
        
        with st.spinner('Syncing data, live odds, and Google authentications...'):
            team_df, recent_bat_agg = get_team_data()
            pitcher_df = load_pitching_data()
            live_odds = get_live_odds()
            
            if not team_df.empty and not pitcher_df.empty:
                st.subheader("⚡ Automated Daily Slate Runner")
                if st.button("▶ Auto-Run & Log Entire Daily Slate"):
                    with st.spinner("Simulating full MLB Slate..."):
                        slate_logs = []
                        for game_key, odds in live_odds.items():
                            try:
                                away_t, home_t = game_key.split(" @ ")
                                a_ml, h_ml = odds
                                away_p_target, home_p_target = TEAM_NAME_MAP.get(away_t, away_t), TEAM_NAME_MAP.get(home_t, home_t)
                                a_stats = team_df[team_df['Tm'] == away_p_target].iloc[0]
                                h_stats = team_df[team_df['Tm'] == home_p_target].iloc[0]
                                a_abbr, h_abbr = FULL_TO_ABBR.get(away_t, ''), FULL_TO_ABBR.get(home_t, '')
                                a_recent_offense = a_stats['RS_per_G']
                                if not recent_bat_agg.empty and a_abbr in recent_bat_agg['Tm'].values: a_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == a_abbr]['Recent_RS_per_G'].values[0]
                                h_recent_offense = h_stats['RS_per_G']
                                if not recent_bat_agg.empty and h_abbr in recent_bat_agg['Tm'].values: h_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == h_abbr]['Recent_RS_per_G'].values[0]
                                a_blended_rs = (a_stats['RS_per_G'] * 0.70) + (a_recent_offense * 0.30)
                                h_blended_rs = (h_stats['RS_per_G'] * 0.70) + (h_recent_offense * 0.30)
                                a_run_prevention = (a_stats['RA_per_G'] * 0.61) + (a_stats['BP_RA9'] * 0.39)
                                h_run_prevention = (h_stats['RA_per_G'] * 0.61) + (h_stats['BP_RA9'] * 0.39)
                                p_factor = PARK_FACTORS.get(home_t, 100) / 100
                                away_lam = ((a_blended_rs + h_run_prevention) / 2) * p_factor
                                home_lam = ((h_blended_rs + a_run_prevention) / 2) * p_factor
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
                                    date_str = get_local_date_str() 
                                    row_data = [date_str, away_t, home_t, a_ml, h_ml, f"{model_away_prob:.1%}", f"{model_home_prob:.1%}", action_taken, "PENDING"]
                                    log_to_google_sheets(row_data)
                                    slate_logs.append(row_data)
                            except: continue
                        st.success(f"✅ Successfully processed {len(slate_logs)} edges!")

                st.markdown("---")
                st.subheader("Manual Matchup Override")
                MLB_TEAMS = sorted(list(TEAM_NAME_MAP.keys()))
                away_t = st.sidebar.selectbox("Away Team:", MLB_TEAMS, index=0)
                home_t = st.sidebar.selectbox("Home Team:", MLB_TEAMS, index=1)
                matchup_key = f"{away_t} @ {home_t}"
                default_away_ml = int(live_odds.get(matchup_key, [100, -110])[0])
                default_home_ml = int(live_odds.get(matchup_key, [100, -110])[1])
                away_p_target, home_p_target = TEAM_NAME_MAP.get(away_t, away_t), TEAM_NAME_MAP.get(home_t, home_t)
                away_pitchers = sorted(pitcher_df[pitcher_df['Tm'] == away_p_target]['Name'].unique().tolist())
                home_pitchers = sorted(pitcher_df[pitcher_df['Tm'] == home_p_target]['Name'].unique().tolist())
                away_sp = st.sidebar.selectbox(f"{away_t} SP:", ["League Average SP"] + away_pitchers)
                home_sp = st.sidebar.selectbox(f"{home_t} SP:", ["League Average SP"] + home_pitchers)
                location = st.sidebar.selectbox("Location:", list(PARK_FACTORS.keys()), index=list(PARK_FACTORS.keys()).index(home_t) if home_t in PARK_FACTORS else 0)
                p_factor = PARK_FACTORS.get(location, 100) / 100
                st.sidebar.markdown("---")
                vegas_away = st.sidebar.number_input("Away ML:", value=default_away_ml)
                vegas_home = st.sidebar.number_input("Home ML:", value=default_home_ml)
                
                try:
                    a_stats = team_df[team_df['Tm'] == away_p_target].iloc[0]
                    h_stats = team_df[team_df['Tm'] == home_p_target].iloc[0]
                    a_abbr, h_abbr = FULL_TO_ABBR.get(away_t, ''), FULL_TO_ABBR.get(home_t, '')
                    
                    a_recent_offense = a_stats['RS_per_G']
                    if not recent_bat_agg.empty and a_abbr in recent_bat_agg['Tm'].values: 
                        a_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == a_abbr]['Recent_RS_per_G'].values[0]
                    h_recent_offense = h_stats['RS_per_G']
                    if not recent_bat_agg.empty and h_abbr in recent_bat_agg['Tm'].values: 
                        h_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == h_abbr]['Recent_RS_per_G'].values[0]
                        
                    a_blended_rs = (a_stats['RS_per_G'] * 0.70) + (a_recent_offense * 0.30)
                    h_blended_rs = (h_stats['RS_per_G'] * 0.70) + (h_recent_offense * 0.30)
                    a_sp_fip = pitcher_df[pitcher_df['Name'] == away_sp]['FIP'].values[0] if away_sp != "League Average SP" else a_stats['RA_per_G']
                    h_sp_fip = pitcher_df[pitcher_df['Name'] == home_sp]['FIP'].values[0] if home_sp != "League Average SP" else h_stats['RA_per_G']
                    a_run_prevention = (a_sp_fip * 0.61) + (a_stats['BP_RA9'] * 0.39)
                    h_run_prevention = (h_sp_fip * 0.61) + (h_stats['BP_RA9'] * 0.39)
                    away_lam = ((a_blended_rs + h_run_prevention) / 2) * p_factor
                    home_lam = ((h_blended_rs + a_run_prevention) / 2) * p_factor
                    
                    if st.button("▶ Run Manual Simulation"):
                        sim_a = np.random.poisson(away_lam, 10000)
                        sim_h = np.random.poisson(home_lam, 10000)
                        a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                        h_wins = 10000 - a_wins
                        model_away_prob, model_home_prob = a_wins / 10000, h_wins / 10000
                        st.write(f"Final Expected Runs: {away_t} **{away_lam:.2f}** | {home_t} **{home_lam:.2f}**")
                        c1, c2 = st.columns(2)
                        v_a_prob = 100/(vegas_away+100) if vegas_away > 0 else abs(vegas_away)/(abs(vegas_away)+100)
                        v_h_prob = 100/(vegas_home+100) if vegas_home > 0 else abs(vegas_home)/(abs(vegas_home)+100)
                        with c1:
                            st.metric(f"{away_t} Win Prob", f"{model_away_prob:.1%}")
                            if model_away_prob > v_a_prob + 0.03: st.success("🔥 ACTIONABLE EDGE")
                        with c2:
                            st.metric(f"{home_t} Win Prob", f"{model_home_prob:.1%}")
                            if model_home_prob > v_h_prob + 0.03: st.success("🔥 ACTIONABLE EDGE")
                except Exception: st.error("Engine failure mapping data.")

# ==========================================================
# SPORT BRANCH 2: NCAA SOFTBALL (CALIBRATED WITH SOS & AUTO-GRADING!)
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
                
            worksheet.append_row(row_data)
            return True
        except Exception as e:
            if "200" in str(e): return True
            st.error(f"Softball Sheet Log Error: {e}")
            return False

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
            today = datetime.now()
            year, month, day = today.strftime("%Y"), today.strftime("%m"), today.strftime("%d")
            url = f"https://data.ncaa.com/casandbox/scoreboard/softball/d1/{year}/{month}/{day}/scoreboard.json"
            resp = requests.get(url, timeout=10).json()
            games_list = []
            if 'games' in resp:
                for g in resp['games']:
                    game_data = g.get('game', g)
                    away_info = game_data.get('away', {})
                    home_info = game_data.get('home', {})
                    away_team_name = away_info.get('names', {}).get('short', away_info.get('teamName', ''))
                    home_team_name = home_info.get('names', {}).get('short', home_info.get('teamName', ''))
                    if away_team_name and home_team_name:
                        games_list.append((away_team_name, home_team_name))
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
                    url = f"https://data.ncaa.com/casandbox/scoreboard/softball/d1/{year}/{month}/{day}/scoreboard.json"
                    resp = requests.get(url, timeout=10).json()
                    
                    if 'games' in resp:
                        for g in resp['games']:
                            game_data = g.get('game', g)
                            state = game_data.get('gameState', '').lower()
                            
                            if state == 'final':
                                away_info = game_data.get('away', {})
                                home_info = game_data.get('home', {})
                                away_ncaa = away_info.get('names', {}).get('short', away_info.get('teamName', ''))
                                home_ncaa = home_info.get('names', {}).get('short', home_info.get('teamName', ''))
                                
                                away_team_name = map_ncaa_to_warren_nolan(away_ncaa, valid_teams)
                                home_team_name = map_ncaa_to_warren_nolan(home_ncaa, valid_teams)
                                
                                if away_team_name and home_team_name:
                                    try:
                                        away_score = int(away_info.get('score', 0))
                                        home_score = int(home_info.get('score', 0))
                                    except ValueError:
                                        away_score, home_score = 0, 0
                                        
                                    winner = away_team_name if away_score > home_score else home_team_name
                                    score_dict[f"{d_str}_{away_team_name.lower()}"] = winner.lower()
                                    score_dict[f"{d_str}_{home_team_name.lower()}"] = winner.lower()
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
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0'}
            response = requests.get(url, headers=headers, timeout=10)
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
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0'}
            response = requests.get(url, headers=headers, timeout=10)
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

    # === FETCH STATS BEFORE UI RENDERS ===
    tot_sb_games, sb_acc = get_softball_log_stats()

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
            
            # --- AUTO-GRADE OPTION ON DASHBOARD ---
            col1, col2, col3 = st.columns([2, 2, 3])
            with col1: st.metric(label="Total Graded Softball Games", value=tot_sb_games)
            with col2: st.metric(label="Model Accuracy", value=f"{sb_acc:.1f}%")
            with col3:
                st.write("")
                if st.button("🔄 Auto-Grade Yesterday's Softball"):
                    with st.spinner("Accessing NCAA Scoreboard Portal..."):
                        updates = auto_grade_softball_pending_bets(valid_teams)
                        if updates > 0: st.success(f"✅ Successfully graded {updates} games! Refresh.")
                        elif updates == 0: st.info("No games ready to be graded.")
            st.markdown("---")
            
            # --- AUTOMATED SOFTBALL DAILY SLATE RUNNER ---
            st.subheader("⚡ Automated Daily Softball Slate Runner")
            st.markdown("Simulate every Division I game scheduled for today on the NCAA Scoreboard and log them to Google Sheets in one click.")
            if st.button("▶ Auto-Run & Log Entire Softball Slate"):
                with st.spinner("Fetching today's NCAA schedule & running simulations..."):
                    scheduled_games = get_daily_softball_games()
                    if not scheduled_games:
                        st.warning("No D1 Softball games found on today's NCAA schedule yet.")
                    else:
                        logged_count = 0
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
                                
                                if log_softball_to_sheets(row_data):
                                    logged_count += 1
                                    
                        if logged_count > 0:
                            st.success(f"✅ Successfully simulated today's slate and logged {logged_count} games to Google Sheets!")
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
                st.caption("*Softball is highly pitching-dominant. Lower Starting Pitcher ERAs dynamically scale their team's Log5 win probability.*")
                
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
                        if log_softball_to_sheets(row_data):
                            st.success("✅ Logged successfully to the 'Softball Log' tab!")
        else:
            st.error("Could not compile softball standings or pitching statistics database.")
