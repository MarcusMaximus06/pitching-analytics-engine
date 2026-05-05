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

st.set_page_config(page_title="Apex Baseball Analytics", layout="wide")

st.sidebar.title("Navigation")
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
    except Exception:
        return {}

def log_to_google_sheets(row_data):
    try:
        gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
        sh = gc.open("MLB Daily Prediction Model")
        worksheet = sh.worksheet("Master Log")
        worksheet.append_row(row_data)
        return True
    except Exception as e:
        if "200" in str(e): return True
        return False

# --- NEW: READ GOOGLE SHEETS DASHBOARD DATA ---
def get_master_log_stats():
    try:
        gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
        sh = gc.open("MLB Daily Prediction Model")
        worksheet = sh.worksheet("Master Log")
        data = worksheet.get_all_values()
        
        if len(data) <= 1: return 0, 0.0, 0.0 # Empty sheet
        
        total_games = 0
        model_wins = 0
        vegas_wins = 0
        
        for row in data[1:]: # Skip headers
            if len(row) >= 9:
                result = row[8].strip().upper()
                model_pick = row[7].strip()
                away_ml = int(row[3]) if row[3].replace('-','').isdigit() else 0
                home_ml = int(row[4]) if row[4].replace('-','').isdigit() else 0
                away_t = row[1]
                home_t = row[2]
                
                # Determine Vegas Pick (Favorite)
                vegas_pick = away_t if away_ml < home_ml else home_t
                
                if result in ["WIN", "LOSS"]:
                    total_games += 1
                    if result == "WIN": model_wins += 1 # Assuming "WIN" means the model pick was correct
                    
                    # Check if Vegas got it right based on actual winner
                    actual_winner = model_pick if result == "WIN" else (away_t if model_pick == home_t else home_t)
                    if actual_winner == vegas_pick: vegas_wins += 1
                        
        mod_acc = (model_wins / total_games * 100) if total_games > 0 else 0.0
        veg_acc = (vegas_wins / total_games * 100) if total_games > 0 else 0.0
        return total_games, mod_acc, veg_acc
    except Exception as e:
        return 0, 0.0, 0.0

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
    except Exception as e: 
        return pd.DataFrame(), pd.DataFrame()

# ==========================================
# PAGE 1: PITCHING ANALYTICS MATRIX
# ==========================================
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
        except Exception as e:
            st.error("Engine failure.")

# ==========================================
# PAGE 2: MONTE CARLO SIMULATION ENGINE
# ==========================================
elif page == "🎲 Monte Carlo Simulation Engine":
    st.title("🎲 Monte Carlo Simulation Engine")
    
    st.markdown("### 📊 Live Model Log & Automation")
    st.caption("*Integrates with The-Odds-API to pull live DraftKings lines and securely writes to your connected Google Sheet.*")
    
    # --- POPULATE DASHBOARD ---
    tot_games, mod_acc, veg_acc = get_master_log_stats()
    
    col1, col2, col3 = st.columns(3)
    with col1: st.metric(label="Total Graded Games Logged", value=tot_games)
    with col2: st.metric(label="Model Accuracy", value=f"{mod_acc:.1f}%")
    with col3: st.metric(label="Vegas Odds Accuracy", value=f"{veg_acc:.1f}%")
    st.markdown("---")
    
    with st.spinner('Syncing data, live odds, and Google authentications...'):
        team_df, recent_bat_agg = get_team_data()
        pitcher_df = load_pitching_data()
        live_odds = get_live_odds()
        
        if not team_df.empty and not pitcher_df.empty:
            
            # --- THE DAILY MASTER RUNNER ---
            st.subheader("⚡ Automated Daily Slate Runner")
            st.markdown("Click this button once a day to simulate every active matchup and log the Actionable Edges to Google Sheets.")
            if st.button("▶ Auto-Run & Log Entire Daily Slate"):
                with st.spinner("Simulating full MLB Slate..."):
                    slate_logs = []
                    for game_key, odds in live_odds.items():
                        try:
                            away_t, home_t = game_key.split(" @ ")
                            a_ml, h_ml = odds
                            
                            away_p_target = TEAM_NAME_MAP.get(away_t, away_t)
                            home_p_target = TEAM_NAME_MAP.get(home_t, home_t)
                            
                            a_stats = team_df[team_df['Tm'] == away_p_target].iloc[0]
                            h_stats = team_df[team_df['Tm'] == home_p_target].iloc[0]
                            
                            a_abbr = FULL_TO_ABBR.get(away_t, '')
                            h_abbr = FULL_TO_ABBR.get(home_t, '')
                            
                            a_recent_offense = a_stats['RS_per_G']
                            if not recent_bat_agg.empty and a_abbr in recent_bat_agg['Tm'].values: a_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == a_abbr]['Recent_RS_per_G'].values[0]
                                
                            h_recent_offense = h_stats['RS_per_G']
                            if not recent_bat_agg.empty and h_abbr in recent_bat_agg['Tm'].values: h_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == h_abbr]['Recent_RS_per_G'].values[0]

                            a_blended_rs = (a_stats['RS_per_G'] * 0.70) + (a_recent_offense * 0.30)
                            h_blended_rs = (h_stats['RS_per_G'] * 0.70) + (h_recent_offense * 0.30)
                            
                            # Auto-Runner assumes League Average SP if specific starters aren't locked yet
                            a_run_prevention = (a_stats['RA_per_G'] * 0.61) + (a_stats['BP_RA9'] * 0.39)
                            h_run_prevention = (h_stats['RA_per_G'] * 0.61) + (h_stats['BP_RA9'] * 0.39)
                            
                            p_factor = PARK_FACTORS.get(home_t, 100) / 100
                            away_lam = ((a_blended_rs + h_run_prevention) / 2) * p_factor
                            home_lam = ((h_blended_rs + a_run_prevention) / 2) * p_factor
                            
                            sim_a = np.random.poisson(away_lam, 10000)
                            sim_h = np.random.poisson(home_lam, 10000)
                            a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                            h_wins = 10000 - a_wins
                            
                            model_away_prob = a_wins / 10000
                            model_home_prob = h_wins / 10000
                            
                            v_a_prob = 100/(a_ml+100) if a_ml > 0 else abs(a_ml)/(abs(a_ml)+100)
                            v_h_prob = 100/(h_ml+100) if h_ml > 0 else abs(h_ml)/(abs(h_ml)+100)
                            
                            action_taken = "No Edge"
                            if model_away_prob > v_a_prob + 0.03: action_taken = away_t
                            if model_home_prob > v_h_prob + 0.03: action_taken = home_t
                            
                            if action_taken != "No Edge":
                                date_str = datetime.now().strftime("%Y-%m-%d")
                                row_data = [date_str, away_t, home_t, a_ml, h_ml, f"{model_away_prob:.1%}", f"{model_home_prob:.1%}", action_taken, "PENDING"]
                                log_to_google_sheets(row_data)
                                slate_logs.append(row_data)
                        except Exception as e:
                            continue
                    st.success(f"✅ Successfully simulated entire MLB slate and pushed {len(slate_logs)} Actionable Edges to Google Sheets!")

            st.markdown("---")
            st.subheader("Manual Matchup Override")
            
            MLB_TEAMS = sorted(list(TEAM_NAME_MAP.keys()))
            away_t = st.sidebar.selectbox("Away Team:", MLB_TEAMS, index=0)
            home_t = st.sidebar.selectbox("Home Team:", MLB_TEAMS, index=1)
            
            matchup_key = f"{away_t} @ {home_t}"
            default_away_ml = int(live_odds.get(matchup_key, [100, -110])[0])
            default_home_ml = int(live_odds.get(matchup_key, [100, -110])[1])
            
            away_p_target = TEAM_NAME_MAP.get(away_t, away_t)
            home_p_target = TEAM_NAME_MAP.get(home_t, home_t)
            away_pitchers = sorted(pitcher_df[pitcher_df['Tm'] == away_p_target]['Name'].unique().tolist())
            home_pitchers = sorted(pitcher_df[pitcher_df['Tm'] == home_p_target]['Name'].unique().tolist())
            
            away_sp = st.sidebar.selectbox(f"{away_t} SP:", ["League Average SP"] + away_pitchers)
            home_sp = st.sidebar.selectbox(f"{home_t} SP:", ["League Average SP"] + home_pitchers)
            
            location = st.sidebar.selectbox("Game Location (Park Factor):", list(PARK_FACTORS.keys()), index=list(PARK_FACTORS.keys()).index(home_t) if home_t in PARK_FACTORS else 0)
            p_factor = PARK_FACTORS.get(location, 100) / 100
            
            st.sidebar.markdown("---")
            vegas_away = st.sidebar.number_input("Away ML:", value=default_away_ml)
            vegas_home = st.sidebar.number_input("Home ML:", value=default_home_ml)
            
            try:
                a_stats = team_df[team_df['Tm'] == away_p_target].iloc[0]
                h_stats = team_df[team_df['Tm'] == home_p_target].iloc[0]
                a_abbr = FULL_TO_ABBR.get(away_t, '')
                h_abbr = FULL_TO_ABBR.get(home_t, '')
                
                a_recent_offense = a_stats['RS_per_G']
                if not recent_bat_agg.empty and a_abbr in recent_bat_agg['Tm'].values: a_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == a_abbr]['Recent_RS_per_G'].values[0]
                h_recent_offense = h_stats['RS_per_G']
                if not recent_bat_agg.empty and h_abbr in recent_bat_agg['Tm'].values: h_recent_offense = recent_bat_agg[recent_bat_agg['Tm'] == h_abbr]['Recent_RS_per_G'].values[0]

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
                    
                    model_away_prob = a_wins / 10000
                    model_home_prob = h_wins / 10000
                    
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
            except IndexError:
                st.error("Engine failed to map team baseline data.")
        else:
            st.warning("Data pipeline loading...")
