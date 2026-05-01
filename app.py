import streamlit as st
import pandas as pd
from pybaseball import pitching_stats_bref, pitching_stats_range
import plotly.express as px
from datetime import datetime, timedelta
import traceback

st.set_page_config(page_title="Pitching Analytics Matrix", layout="wide")

st.title("⚾ Pitching Analytics Matrix")

st.markdown("""
Welcome to the engine. This dashboard filters raw data through custom parameters, focusing heavily on **historical performance and future predictions**.
""")

# Centralized math function so we can run it on both Season and Recent data
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
    # 1. Pull Season Data
    season_df = pitching_stats_bref(2026)
    season_df = calc_advanced_metrics(season_df)
    season_df['ERA_minus_FIP'] = (season_df['ERA'] - season_df['FIP']).round(2)
    
    # 2. Pull Momentum Data (Last 14 Days)
    today = datetime.now()
    two_weeks_ago = today - timedelta(days=14)
    
    # Scrape the specific 14-day split
    recent_df = pitching_stats_range(two_weeks_ago.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    
    if not recent_df.empty:
        recent_df = calc_advanced_metrics(recent_df)
        # Isolate just the columns we need to merge
        recent_df = recent_df[['Name', 'FPI', 'IP']].rename(columns={'FPI': 'Recent_FPI', 'IP': 'Recent_IP'})
        
        # Merge the Season data with the 14-Day data
        merged_df = pd.merge(season_df, recent_df, on='Name', how='left')
        
        # Calculate Momentum (Positive number = Pitcher is getting hotter than their season average)
        merged_df['Momentum_Shift'] = (merged_df['Recent_FPI'] - merged_df['FPI']).round(2)
    else:
        # Fallback if the 14-day database is temporarily unavailable
        season_df['Recent_FPI'] = None
        season_df['Recent_IP'] = None
        season_df['Momentum_Shift'] = None
        merged_df = season_df

    return merged_df

with st.spinner('Compiling matrix and generating momentum models...'):
    try:
        raw_df = load_data()
        
        st.sidebar.header("Matrix Parameters")
        max_ip = int(raw_df['IP'].max()) if not raw_df.empty else 100
        min_ip = st.sidebar.slider("Minimum Innings Pitched (Season):", min_value=1, max_value=max_ip, value=15)
        
        filtered_df = raw_df[raw_df['IP'] >= min_ip]
        
        st.sidebar.markdown("---")
        st.sidebar.header("Target Profile Search")
        player_list = sorted(filtered_df['Name'].unique().tolist())
        selected_player = st.sidebar.selectbox("Search for a Pitcher:", ["All Pitchers"] + player_list)
        
        # Added the Momentum columns to the display
        display_cols = ['Name', 'Tm', 'IP', 'FPI', 'Recent_FPI', 'Momentum_Shift', 'ERA', 'SO9', 'BB9']
        
        if selected_player != "All Pitchers":
            st.subheader(f"Isolated Profile: {selected_player}")
            player_data = filtered_df[filtered_df['Name'] == selected_player]
            st.dataframe(player_data[display_cols], hide_index=True)
            
            fpi_score = player_data['FPI'].values[0]
            momentum = player_data['Momentum_Shift'].values[0]
            
            st.markdown("### Model Projection")
            if momentum > 2.0:
                st.success(f"📈 **Surging:** {selected_player} has a massive Momentum Shift of +{momentum}. He is currently pitching significantly better than his season averages indicate.")
            elif momentum < -2.0:
                st.error(f"📉 **Collapsing:** {selected_player}'s recent FPI is much lower than his season average. He is in a severe slump or experiencing underlying mechanical issues.")
            else:
                st.info(f"⚖️ **Consistent Form:** {selected_player} is maintaining his baseline dominance. His recent metrics align with his season-long performance.")
                
        else:
            st.subheader("League Overview: The Momentum Tracker")
            st.markdown("Sorting by **Momentum Shift**. Pitchers at the top of this leaderboard are on massive recent hot streaks, generating higher dominance scores over the last 14 days compared to their season baselines.")
            sorted_df = filtered_df.sort_values(by='Momentum_Shift', ascending=False)
            st.dataframe(sorted_df[display_cols].head(25), hide_index=True)
        
        st.markdown("---")
        st.subheader("Predictive Matrix: Strikeout Rate vs. Walk Rate")
        st.markdown("Elite command profiles live in the **top-right quadrant** (High Strikeouts, Low Walks).")
        
        fig = px.scatter(
            filtered_df, 
            x='BB9', 
            y='SO9', 
            hover_name='Name',
            hover_data=['Tm', 'FPI', 'Recent_FPI', 'Momentum_Shift'],
            color='FPI', 
            color_continuous_scale="Viridis",
            labels={'BB9': 'Walks per 9 Innings (BB/9)', 'SO9': 'Strikeouts per 9 Innings (SO/9)', 'FPI': 'Season FPI'}
        )
        
        fig.update_xaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
