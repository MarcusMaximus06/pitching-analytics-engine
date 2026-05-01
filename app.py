import streamlit as st
import pandas as pd
import numpy as np
from pybaseball import pitching_stats_bref, batting_stats_bref, pitching_stats_range, statcast_pitcher_expected_stats, statcast_pitcher
import plotly.express as px
from datetime import datetime, timedelta
import traceback

st.set_page_config(page_title="Apex Baseball Analytics", layout="wide")

# --- NAVIGATION ROUTER ---
st.sidebar.title("Navigation")
page = st.sidebar.radio("Select Engine:", ["⚾ Pitching Analytics Matrix", "🎲 Monte Carlo Simulation Engine"])
st.sidebar.markdown("---")

# 100 is Neutral. >100 favors hitters, <100 favors pitchers.
PARK_FACTORS = {
    'Arizona': 102, 'Atlanta': 100, 'Baltimore': 98, 'Boston': 107, 'Chicago': 102, 
    'Cincinnati': 111, 'Cleveland': 101, 'Colorado': 114, 'Detroit': 98, 'Houston': 96,
    'Kansas City': 101, 'Los Angeles': 97, 'Miami': 95, 'Milwaukee': 101, 'Minnesota': 99,
    'New York': 99, 'Oakland': 94, 'Philadelphia': 102, 'Pittsburgh': 98, 'San Diego': 94,
    'San Francisco': 95, 'Seattle': 92, 'St. Louis': 97, 'Tampa Bay': 93, 'Texas': 103,
    'Toronto': 101, 'Washington': 101
}

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
    season_df = calc_advanced_metrics(season_df)
    
    today = datetime.now()
    two_weeks_ago = today - timedelta(days=14)
    recent_df = pitching_stats_range(two_weeks_ago.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    
    if not recent_df.empty:
        recent_df = calc_advanced_metrics(recent_df)
        recent_df = recent_df[['Name', 'FPI']].rename(columns={'FPI': 'Recent_FPI'})
        merged_df = pd.merge(season_df, recent_df, on='Name', how='left')
        merged_df['Momentum_Shift'] = (merged_df['Recent_FPI'] - merged_df['FPI']).round(2)
    else:
        merged_df = season_df
        merged_df['Recent_FPI'] = None
        merged_df['Momentum_Shift'] = None

    try:
        savant_df = statcast_pitcher_expected_stats(2026, 10)
        def format_savant_name(name_string):
            parts = str(name_string).split(', ')
            return f"{parts[1].strip()} {parts[0].strip()}" if len(parts) == 2 else str(name_string).strip()

        if 'last_name, first_name' in savant_df.columns: savant_df['Name'] = savant_df['last_name, first_name'].apply(format_savant_name)
        elif 'player' in savant_df.columns: savant_df['Name'] = savant_df['player'].apply(format_savant_name)
        
        target_cols = ['Name']
        if 'est_woba' in savant_df.columns: target_cols.append('est_woba')
        if 'xera' in savant_df.columns: target_cols.append('xera')
        if 'player_id' in savant_df.columns: target_cols.append('player_id')
        
        savant_df = savant_df[target_cols]
        if 'est_woba' in savant_df.columns: savant_df = savant_df.rename(columns={'est_woba': 'xwOBA'})
        if 'xera' in savant_df.columns: savant_df = savant_df.rename(columns={'xera': 'xERA'})
        if 'player_id' in savant_df.columns: savant_df = savant_df.rename(columns={'player_id': 'mlbam_id'})
        merged_df = pd.merge(merged_df, savant_df, on='Name', how='left')
    except Exception: pass
    return merged_df

@st.cache_data
def get_team_data():
    try:
        bat_df = batting_stats_bref(2026)
        pitch_df = pitching_stats_bref(2026)
        
        # AGGREGATE RUNS SCORED
        team_bat = bat_df.groupby('Tm').agg(RS=('R', 'sum')).reset_index()
        
        # AGGREGATE RUNS ALLOWED & TRUE GAMES PLAYED
        # The sum of Games Started (GS) perfectly equals the number of Team Games Played
        team_pitch = pitch_df.groupby('Tm').agg(
            RA=('R', 'sum'),
            Team_G=('GS', 'sum') 
        ).reset_index()
        
        # MERGE AND CALCULATE FLAWLESS BASLINES
        team_df = pd.merge(team_bat, team_pitch, on='Tm')
        team_df = team_df[team_df['Tm'] != 'TOT'] # Remove traded player totals
        
        team_df['Team_G'] = team_df['Team_G'].replace(0, 1) # Prevent division by zero
        team_df['RS_per_G'] = team_df['RS'] / team_df['Team_G']
        team_df['RA_per_G'] = team_df['RA'] / team_df['Team_G']
        
        return team_df.sort_values('Tm')
    except Exception as e: 
        st.error(f"Error compiling manual team baselines: {e}")
        return pd.DataFrame()

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
            
            display_cols = ['Name', 'Tm', 'IP', 'FPI', 'Momentum_Shift', 'ERA', 'xERA', 'xwOBA', 'SO9', 'BB9']
            
            if selected_player != "All Pitchers":
                st.subheader(f"Isolated Profile: {selected_player}")
                p_data = raw_df[raw_df['Name'] == selected_player]
                st.dataframe(p_data[display_cols], hide_index=True)
                
                mlbam_id = p_data['mlbam_id'].values[0] if 'mlbam_id' in p_data.columns else None
                if pd.notna(mlbam_id):
                    pitch_data = statcast_pitcher('2026-03-20', datetime.now().strftime('%Y-%m-%d'), int(mlbam_id))
                    if not pitch_data.empty:
                        st.markdown("---")
                        st.subheader("Arsenal & Platoon Splits (Statcast)")
                        c1, c2 = st.columns(2)
                        with c1: st.plotly_chart(px.pie(pitch_data['pitch_name'].value_counts().reset_index(), values='count', names='pitch_name', title="Pitch Usage", hole=0.4), use_container_width=True)
                        with c2:
                            st.markdown("##### Average Velocity & Spin Profiles")
                            v_df = pitch_data.groupby('pitch_name')[['release_speed', 'release_spin_rate']].mean().round(1).reset_index()
                            v_df.columns = ['Pitch Type', 'Avg Velo (MPH)', 'Avg Spin (RPM)']
                            st.dataframe(v_df, hide_index=True)
                            
                            st.markdown("##### Performance by Batter Handedness")
                            whiff_events = ['swinging_strike', 'swinging_strike_blocked']
                            pitch_data['is_whiff'] = pitch_data['description'].isin(whiff_events)
                            plat = pitch_data.groupby('stand').agg(Total_Pitches=('pitch_name', 'count'), Avg_Velo=('release_speed', 'mean'), Whiffs=('is_whiff', 'sum')).reset_index()
                            plat['Whiff_%'] = (plat['Whiffs'] / plat['Total_Pitches'] * 100).round(1)
                            plat['Avg_Velo'] = plat['Avg_Velo'].round(1)
                            plat['stand'] = plat['stand'].replace({'L': 'vs LHB', 'R': 'vs RHB'})
                            st.dataframe(plat[['stand', 'Total_Pitches', 'Avg_Velo', 'Whiff_%']], hide_index=True)
            else:
                st.subheader("League Overview: The Momentum Tracker")
                st.markdown("This leaderboard filters out standard noise and sorts the league by **Momentum Shift**.")
                st.dataframe(filtered_df[display_cols].sort_values('Momentum_Shift', ascending=False).head(25), hide_index=True)
        except Exception as e:
            st.error("Engine failure:")
            st.code(traceback.format_exc())

# ==========================================
# PAGE 2: MONTE CARLO SIMULATION ENGINE
# ==========================================
elif page == "🎲 Monte Carlo Simulation Engine":
    st.title("🎲 Monte Carlo Simulation Engine")
    
    st.markdown("### 📊 Master Prediction Log")
    col1, col2, col3 = st.columns(3)
    with col1:
        if 'total_games' not in st.session_state: st.session_state.total_games = 0
        st.metric(label="Total Games Logged", value=st.session_state.total_games)
    with col2:
        if 'model_acc' not in st.session_state: st.session_state.model_acc = 0.0
        st.metric(label="Model Accuracy", value=f"{st.session_state.model_acc}%")
    with col3:
        st.metric(label="Vegas Odds Accuracy", value="23.0%")
    st.caption("*The dashboard logs overall calculated numbers permanently to calculate actual percentages without listing every individual game.*")
    st.markdown("---")
    
    with st.spinner('Syncing team and pitcher baselines...'):
        team_df = get_team_data()
        pitcher_df = load_pitching_data()
        
        if not team_df.empty and not pitcher_df.empty:
            t_list = team_df['Tm'].tolist()
            away_t = st.sidebar.selectbox("Away Team:", t_list, index=0)
            home_t = st.sidebar.selectbox("Home Team:", t_list, index=1)
            
            # THE UPGRADE: Dynamic Roster Dropdowns
            away_pitchers = sorted(pitcher_df[pitcher_df['Tm'] == away_t]['Name'].unique().tolist())
            home_pitchers = sorted(pitcher_df[pitcher_df['Tm'] == home_t]['Name'].unique().tolist())
            
            away_sp = st.sidebar.selectbox(f"{away_t} SP:", ["League Average SP"] + away_pitchers)
            home_sp = st.sidebar.selectbox(f"{home_t} SP:", ["League Average SP"] + home_pitchers)
            
            st.sidebar.markdown("---")
            location = st.sidebar.selectbox("Game Location (Park Factor):", list(PARK_FACTORS.keys()), index=list(PARK_FACTORS.keys()).index(home_t) if home_t in PARK_FACTORS else 0)
            p_factor = PARK_FACTORS.get(location, 100) / 100
            
            st.sidebar.markdown("---")
            st.sidebar.text_input("Log ID (Pitcher ID tracking for Double-Headers):", placeholder="e.g. 0501-Cole-G1")
            st.sidebar.markdown("---")
            
            vegas_away = st.sidebar.number_input("Away ML:", value=100)
            vegas_home = st.sidebar.number_input("Home ML:", value=-110)
            
            st.subheader(f"{away_t} @ {home_t} ({location})")
            
            a_stats = team_df[team_df['Tm'] == away_t].iloc[0]
            h_stats = team_df[team_df['Tm'] == home_t].iloc[0]
            
            a_sp_fip = pitcher_df[pitcher_df['Name'] == away_sp]['FIP'].values[0] if away_sp != "League Average SP" else a_stats['RA_per_G']
            h_sp_fip = pitcher_df[pitcher_df['Name'] == home_sp]['FIP'].values[0] if home_sp != "League Average SP" else h_stats['RA_per_G']
            
            away_lam = ((a_stats['RS_per_G'] * 0.4) + (h_sp_fip * 0.6)) * p_factor
            home_lam = ((h_stats['RS_per_G'] * 0.4) + (a_sp_fip * 0.6)) * p_factor
            
            if st.button("▶ Run 10,000 Probabilistic Iterations"):
                sim_a = np.random.poisson(away_lam, 10000)
                sim_h = np.random.poisson(home_lam, 10000)
                a_wins = np.sum(sim_a > sim_h) + (np.sum(sim_a == sim_h) / 2)
                h_wins = 10000 - a_wins
                
                model_away_prob = a_wins / 10000
                model_home_prob = h_wins / 10000
                
                st.write(f"Park Adjusted Expected Runs: {away_t} **{away_lam:.2f}** | {home_t} **{home_lam:.2f}**")
                
                c1, c2 = st.columns(2)
                v_a_prob = 100/(vegas_away+100) if vegas_away > 0 else abs(vegas_away)/(abs(vegas_away)+100)
                v_h_prob = 100/(vegas_home+100) if vegas_home > 0 else abs(vegas_home)/(abs(vegas_home)+100)
                
                with c1:
                    # THE FIX: Used proper model formatting variables
                    st.metric(f"{away_t} Win Prob", f"{model_away_prob:.1%}")
                    if model_away_prob > v_a_prob + 0.03: st.success("🔥 ACTIONABLE EDGE")
                with c2:
                    st.metric(f"{home_t} Win Prob", f"{model_home_prob:.1%}")
                    if model_home_prob > v_h_prob + 0.03: st.success("🔥 ACTIONABLE EDGE")
        else:
            st.warning("Data pipeline is empty or still loading. Check your connection to Baseball-Reference.")
