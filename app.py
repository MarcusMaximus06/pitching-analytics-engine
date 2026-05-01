import streamlit as st
import pandas as pd
from pybaseball import pitching_stats_bref, pitching_stats_range, statcast_pitcher_expected_stats, playerid_lookup, statcast_pitcher
import plotly.express as px
from datetime import datetime, timedelta
import traceback

st.set_page_config(page_title="Pitching Analytics Matrix", layout="wide")

st.title("⚾ Pitching Analytics Matrix")

st.markdown("""
Welcome to the engine. This dashboard filters raw data through custom parameters, focusing heavily on **historical performance and future predictions**.
""")

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
def load_data():
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

    # Robust Statcast Expected Stats Processing
    try:
        savant_df = statcast_pitcher_expected_stats(2026, 10)
        
        # Helper function to flip "Last, First" to "First Last"
        def format_savant_name(name_string):
            if pd.isna(name_string): return name_string
            parts = str(name_string).split(', ')
            if len(parts) == 2:
                return f"{parts[1].strip()} {parts[0].strip()}"
            return str(name_string).strip()

        # Check for all possible Savant column naming conventions
        if 'first_name' in savant_df.columns and 'last_name' in savant_df.columns:
            savant_df['Name'] = savant_df['first_name'].astype(str).str.strip() + ' ' + savant_df['last_name'].astype(str).str.strip()
        elif 'player' in savant_df.columns:
            savant_df['Name'] = savant_df['player'].apply(format_savant_name)
        elif 'player_name' in savant_df.columns:
            savant_df['Name'] = savant_df['player_name'].apply(format_savant_name)
        else:
            raise KeyError("Could not locate a recognizable player name column in the Savant database.")

        savant_df = savant_df[['Name', 'est_woba', 'xera']].rename(columns={'est_woba': 'xwOBA', 'xera': 'xERA'})
        merged_df = pd.merge(merged_df, savant_df, on='Name', how='left')
        
        # Clear any previous errors if successful
        if 'savant_error' in st.session_state:
            del st.session_state['savant_error']
            
    except Exception as e:
        st.session_state['savant_error'] = str(e)
        merged_df['xwOBA'] = None
        merged_df['xERA'] = None

    return merged_df

with st.spinner('Compiling matrix, pulling Statcast tracking, and generating models...'):
    try:
        raw_df = load_data()
        
        st.sidebar.header("Matrix Parameters")
        max_ip = int(raw_df['IP'].max()) if not raw_df.empty else 100
        min_ip = st.sidebar.slider("Minimum Innings Pitched (Season):", min_value=1, max_value=max_ip, value=15)
        
        if 'savant_error' in st.session_state:
            st.sidebar.warning(f"Savant Expected Stats Error: {st.session_state['savant_error']}")
            
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
            
            # --- ARSENAL BREAKDOWN & STATCAST TARGETING ---
            st.markdown("---")
            st.subheader("Arsenal Breakdown (Statcast Optical Tracking)")
            
            with st.spinner('Accessing MLBAM Database for Pitch Tracking...'):
                try:
                    name_parts = selected_player.split(' ')
                    first_name = name_parts[0].lower()
                    last_name = ' '.join(name_parts[1:]).lower()
                    
                    id_df = playerid_lookup(last_name, first_name)
                    
                    if not id_df.empty:
                        mlbam_id = id_df['key_mlbam'].values[0]
                        
                        start_date = '2026-03-20' 
                        end_date = datetime.now().strftime('%Y-%m-%d')
                        pitch_data = statcast_pitcher(start_date, end_date, mlbam_id)
                        
                        if not pitch_data.empty:
                            col1, col2 = st.columns(2)
                            
                            with col1:
                                pitch_counts = pitch_data['pitch_name'].value_counts().reset_index()
                                pitch_counts.columns = ['Pitch Type', 'Count']
                                fig_pie = px.pie(pitch_counts, values='Count', names='Pitch Type', 
                                                 title=f"2026 Pitch Usage Matrix", hole=0.4,
                                                 color_discrete_sequence=px.colors.qualitative.Bold)
                                st.plotly_chart(fig_pie, use_container_width=True)
                                
                            with col2:
                                st.markdown("##### Average Velocity & Spin Profiles")
                                velo_df = pitch_data.groupby('pitch_name')[['release_speed', 'release_spin_rate']].mean().round(1).reset_index()
                                velo_df.columns = ['Pitch Type', 'Avg Velo (MPH)', 'Avg Spin (RPM)']
                                st.dataframe(velo_df, hide_index=True)
                        else:
                            st.info("No Statcast pitch tracking data available for this player yet in 2026.")
                    else:
                        st.info("Could not map this player's name to an active MLB ID.")
                except Exception as e:
                    st.warning(f"Error accessing individual Statcast Arsenal data: {e}")
                
        else:
            st.subheader("League Overview: Statcast & Momentum Tracker")
            st.markdown("Sorting by **Momentum Shift**.")
            sorted_df = filtered_df.sort_values(by='Momentum_Shift', ascending=False)
            st.dataframe(sorted_df[display_cols].head(25), hide_index=True)
        
        st.markdown("---")
        st.subheader("Predictive Matrix: Strikeout Rate vs. Walk Rate")
        
        fig = px.scatter(
            filtered_df, 
            x='BB9', 
            y='SO9', 
            hover_name='Name',
            hover_data=['Tm', 'FPI', 'Momentum_Shift', 'ERA', 'xERA'],
            color='FPI', 
            color_continuous_scale="Viridis",
            labels={'BB9': 'Walks per 9 Innings (BB/9)', 'SO9': 'Strikeouts per 9 Innings (SO/9)'}
        )
        
        fig.update_xaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
