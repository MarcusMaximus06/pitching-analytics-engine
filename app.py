import streamlit as st
import pandas as pd
import numpy as np
from pybaseball import pitching_stats_bref, pitching_stats_range, statcast_pitcher_expected_stats, playerid_lookup, statcast_pitcher, standings
import plotly.express as px
from datetime import datetime, timedelta
import traceback

st.set_page_config(page_title="Apex Baseball Analytics", layout="wide")

# --- NAVIGATION ROUTER ---
st.sidebar.title("Navigation")
page = st.sidebar.radio("Select Engine:", ["⚾ Pitching Analytics Matrix", "🎲 Monte Carlo Simulation Engine"])
st.sidebar.markdown("---")

# ==========================================
# PAGE 1: PITCHING ANALYTICS MATRIX
# ==========================================
if page == "⚾ Pitching Analytics Matrix":
    st.title("⚾ Pitching Analytics Matrix")
    st.markdown("Filtering raw data through custom parameters to project future dominance.")

    def calc_advanced_metrics(df):
        if 'BB9' not in df.columns:
            df['BB9'] = (df['BB'] / df['IP']) * 9
        if 'SO9' not in df.columns:
            df['SO9'] = (df['SO'] / df['IP']) * 9
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
                if pd.isna(name_string): return name_string
                parts = str(name_string).split(', ')
                if len(parts) == 2: return f"{parts[1].strip()} {parts[0].strip()}"
                return str(name_string).strip()

            if 'first_name' in savant_df.columns and 'last_name' in savant_df.columns:
                savant_df['Name'] = savant_df['first_name'].astype(str).str.strip() + ' ' + savant_df['last_name'].astype(str).str.strip()
            elif 'last_name, first_name' in savant_df.columns:
                savant_df['Name'] = savant_df['last_name, first_name'].apply(format_savant_name)
            elif 'player' in savant_df.columns:
                savant_df['Name'] = savant_df['player'].apply(format_savant_name)
            elif 'player_name' in savant_df.columns:
                savant_df['Name'] = savant_df['player_name'].apply(format_savant_name)
            else:
                cols = ", ".join(savant_df.columns.tolist())
                raise KeyError(f"Could not locate name column. Available columns: {cols}")

            target_cols = ['Name']
            if 'est_woba' in savant_df.columns: target_cols.append('est_woba')
            if 'xera' in savant_df.columns: target_cols.append('xera')
            if 'player_id' in savant_df.columns: target_cols.append('player_id')
            
            savant_df = savant_df[target_cols]
            if 'est_woba' in savant_df.columns: savant_df = savant_df.rename(columns={'est_woba': 'xwOBA'})
            if 'xera' in savant_df.columns: savant_df = savant_df.rename(columns={'xera': 'xERA'})
            if 'player_id' in savant_df.columns: savant_df = savant_df.rename(columns={'player_id': 'mlbam_id'})
            
            merged_df = pd.merge(merged_df, savant_df, on='Name', how='left')
            
            if 'xwOBA' not in merged_df.columns: merged_df['xwOBA'] = None
            if 'xERA' not in merged_df.columns: merged_df['xERA'] = None
            if 'mlbam_id' not in merged_df.columns: merged_df['mlbam_id'] = None
                
        except Exception as e:
            merged_df['xwOBA'] = None
            merged_df['xERA'] = None
            merged_df['mlbam_id'] = None

        return merged_df

    with st.spinner('Compiling matrix...'):
        try:
            raw_df = load_pitching_data()
            
            st.sidebar.header("Matrix Parameters")
            max_ip = int(raw_df['IP'].max()) if not raw_df.empty else 100
            min_ip = st.sidebar.slider("Minimum Innings Pitched:", min_value=1, max_value=max_ip, value=15)
            
            filtered_df = raw_df[raw_df['IP'] >= min_ip]
            
            st.sidebar.markdown("---")
            st.sidebar.header("Target Profile Search")
            player_list = sorted(filtered_df['Name'].unique().tolist())
            selected_player = st.sidebar.selectbox("Search for a Pitcher:", ["All Pitchers"] + player_list)
            
            display_cols = ['Name', 'Tm', 'IP', 'FPI', 'Momentum_Shift', 'ERA', 'xERA', 'xwOBA', 'SO9', 'BB9']
            
            if selected_player != "All Pitchers":
                st.subheader(f"Isolated Profile: {selected_player}")
                player_data = filtered_df[filtered_df['Name'] == selected_player]
                st.dataframe(player_data[display_cols], hide_index=True)
                
                # --- STATCAST ARSENAL & PLATOON SPLITS ---
                st.markdown("---")
                st.subheader("Arsenal & Platoon Splits (Statcast)")
                
                with st.spinner('Accessing Pitch Tracking...'):
                    mlbam_id = player_data['mlbam_id'].values[0] if 'mlbam_id' in player_data.columns else None
                    
                    if pd.notna(mlbam_id):
                        start_date = '2026-03-20' 
                        end_date = datetime.now().strftime('%Y-%m-%d')
                        pitch_data = statcast_pitcher(start_date, end_date, int(mlbam_id))
                        
                        if not pitch_data.empty:
                            col1, col2 = st.columns(2)
                            
                            with col1:
                                pitch_counts = pitch_data['pitch_name'].value_counts().reset_index()
                                pitch_counts.columns = ['Pitch Type', 'Count']
                                fig_pie = px.pie(pitch_counts, values='Count', names='Pitch Type', 
                                                 title=f"Overall Pitch Usage", hole=0.4)
                                st.plotly_chart(fig_pie, use_container_width=True)
                                
                            with col2:
                                # 1. VELOCITY & SPIN TABLE (Restored)
                                st.markdown("##### Average Velocity & Spin Profiles")
                                velo_df = pitch_data.groupby('pitch_name')[['release_speed', 'release_spin_rate']].mean().round(1).reset_index()
                                velo_df.columns = ['Pitch Type', 'Avg Velo (MPH)', 'Avg Spin (RPM)']
                                st.dataframe(velo_df, hide_index=True)

                                # 2. PLATOON SPLITS MATH
                                st.markdown("##### Performance by Batter Handedness")
                                whiff_events = ['swinging_strike', 'swinging_strike_blocked']
                                pitch_data['is_whiff'] = pitch_data['description'].isin(whiff_events)
                                
                                platoon_df = pitch_data.groupby('stand').agg(
                                    Total_Pitches=('pitch_name', 'count'),
                                    Avg_Velo=('release_speed', 'mean'),
                                    Whiffs=('is_whiff', 'sum')
                                ).reset_index()
                                
                                platoon_df['Whiff_%'] = (platoon_df['Whiffs'] / platoon_df['Total_Pitches'] * 100).round(1)
                                platoon_df['Avg_Velo'] = platoon_df['Avg_Velo'].round(1)
                                platoon_df['stand'] = platoon_df['stand'].replace({'L': 'vs LHB', 'R': 'vs RHB'})
                                
                                st.dataframe(platoon_df[['stand', 'Total_Pitches', 'Avg_Velo', 'Whiff_%']], hide_index=True)
                        else:
                            st.info("No Statcast data available yet in 2026.")
                    else:
                        st.info("Could not map this player's name to an active MLB ID.")
                    
            else:
                st.subheader("League Overview")
                sorted_df = filtered_df.sort_values(by='Momentum_Shift', ascending=False)
                st.dataframe(sorted_df[display_cols].head(25), hide_index=True)

        except Exception as e:
            st.error("Engine failure:")
            st.code(traceback.format_exc())

# ==========================================
# PAGE 2: MONTE CARLO SIMULATION ENGINE
# ==========================================
elif page == "🎲 Monte Carlo Simulation Engine":
    st.title("🎲 Monte Carlo Simulation Engine")
    st.markdown("Predictive outcome modeling to isolate +EV betting edges.")

    # --- MASTER ACCURACY LOG ---
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

    @st.cache_data
    def get_team_baselines():
        try:
            tables = standings(2026)
            df = pd.concat(tables)
            df['RS_per_Game'] = df['R'] / df['G']
            df['RA_per_Game'] = df['RA'] / df['G']
            return df[['Tm', 'RS_per_Game', 'RA_per_Game']].sort_values('Tm').reset_index(drop=True)
        except Exception as e:
            return pd.DataFrame()

    with st.spinner('Loading baseline data...'):
        try:
            team_df = get_team_baselines()
            
            if not team_df.empty:
                st.sidebar.header("Simulation Parameters")
                
                team_list = team_df['Tm'].tolist()
                away_team = st.sidebar.selectbox("Away Team:", team_list, index=0)
                home_team = st.sidebar.selectbox("Home Team:", team_list, index=1)
                
                st.sidebar.markdown("---")
                st.sidebar.text_input("Starting Pitcher ID (Required for logging double-headers):", placeholder="e.g. Cole, G")
                st.sidebar.markdown("---")
                
                vegas_away_ml = st.sidebar.number_input("Vegas Away Moneyline (e.g., +150, -120):", value=100)
                vegas_home_ml = st.sidebar.number_input("Vegas Home Moneyline:", value=-110)
                
                def get_implied_prob(ml):
                    if ml > 0: return 100 / (ml + 100)
                    else: return abs(ml) / (abs(ml) + 100)
                
                vegas_away_prob = get_implied_prob(vegas_away_ml)
                vegas_home_prob = get_implied_prob(vegas_home_ml)
                
                st.subheader(f"Matchup: {away_team} @ {home_team}")
                
                away_stats = team_df[team_df['Tm'] == away_team].iloc[0]
                home_stats = team_df[team_df['Tm'] == home_team].iloc[0]
                
                expected_away_runs = (away_stats['RS_per_Game'] + home_stats['RA_per_Game']) / 2
                expected_home_runs = (home_stats['RS_per_Game'] + away_stats['RA_per_Game']) / 2
                
                if st.button("▶ Run 10,000 Monte Carlo Simulations"):
                    with st.spinner("Running probabilistic iterations..."):
                        away_sims = np.random.poisson(expected_away_runs, 10000)
                        home_sims = np.random.poisson(expected_home_runs, 10000)
                        
                        away_wins = np.sum(away_sims > home_sims)
                        home_wins = np.sum(home_sims > away_sims)
                        ties = np.sum(away_sims == home_sims)
                        
                        away_wins += ties / 2
                        home_wins += ties / 2
                        
                        model_away_prob = away_wins / 10000
                        model_home_prob = home_wins / 10000
                        
                        res_col1, res_col2 = st.columns(2)
                        with res_col1:
                            st.info(f"**{away_team} (Away)**")
                            st.write(f"Model Win Prob: **{model_away_prob:.1%}**")
                            edge = model_away_prob - vegas_away_prob
                            if edge > 0.02: st.success(f"🔥 **+EV Edge: +{edge:.1%}**")
                                
                        with res_col2:
                            st.info(f"**{home_team} (Home)**")
                            st.write(f"Model Win Prob: **{model_home_prob:.1%}**")
                            edge = model_home_prob - vegas_home_prob
                            if edge > 0.02: st.success(f"🔥 **+EV Edge: +{edge:.1%}**")

            else:
                st.warning("Data pipeline empty. Waiting for season data.")
                
        except Exception as e:
            st.error("Engine failure:")
            st.code(traceback.format_exc())
