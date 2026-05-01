import streamlit as st
import pandas as pd
from pybaseball import pitching_stats_bref
import plotly.express as px
import traceback

# Set up the visual configuration of the page
st.set_page_config(page_title="Pitching Analytics Matrix", layout="wide")

st.title("⚾ Pitching Analytics Matrix")

st.markdown("""
Welcome to the engine. This dashboard filters raw data through custom parameters, focusing heavily on **historical performance and future predictions** rather than standard reliability thresholds.
""")

@st.cache_data
def load_data():
    # Pull current pitching stats
    df = pitching_stats_bref(2026)
    
    # ---------------------------------------------------------
    # BULLETPROOFING THE MATH: Calculate advanced metrics manually
    # if the database fails to provide them.
    # ---------------------------------------------------------
    
    # Calculate Walk Rate (BB/9) and Strikeout Rate (SO/9)
    if 'BB9' not in df.columns:
        df['BB9'] = (df['BB'] / df['IP']) * 9
    if 'SO9' not in df.columns:
        df['SO9'] = (df['SO'] / df['IP']) * 9
        
    # Calculate standard FIP (Fielding Independent Pitching)
    # Formula: ((13*HR) + (3*(BB+HBP)) - (2*K)) / IP + Constant (~3.15)
    if 'FIP' not in df.columns:
        hbp = df['HBP'] if 'HBP' in df.columns else 0 # Fallback if Hit-By-Pitch is missing
        df['FIP'] = ((13 * df['HR']) + (3 * (df['BB'] + hbp)) - (2 * df['SO'])) / df['IP'] + 3.15
        
    # Now that we know the math exists, drop pitchers with missing critical data
    df = df.dropna(subset=['ERA', 'FIP', 'SO9', 'BB9'])
    
    # The Math: Differential
    df['ERA_minus_FIP'] = df['ERA'] - df['FIP']
    return df

with st.spinner('Compiling matrix and generating models...'):
    try:
        raw_df = load_data()
        
        # --- TARGET PROFILE SEARCH ---
        st.sidebar.header("Target Profile Search")
        player_list = sorted(raw_df['Name'].unique().tolist())
        selected_player = st.sidebar.selectbox("Search for a Pitcher:", ["All Pitchers"] + player_list)
        
        # Define columns to display (Removed WAR to prevent another missing key error)
        display_cols = ['Name', 'Tm', 'IP', 'ERA', 'FIP', 'ERA_minus_FIP', 'SO9', 'BB9']
        
        if selected_player != "All Pitchers":
            st.subheader(f"Isolated Profile: {selected_player}")
            player_data = raw_df[raw_df['Name'] == selected_player]
            st.dataframe(player_data[display_cols])
            
            # Predictive Analysis logic
            era_diff = player_data['ERA_minus_FIP'].values[0]
            if era_diff > 0.5:
                st.warning(f"📈 **Positive Progression Candidate:** {selected_player}'s ERA is significantly higher than his FIP. The underlying metrics suggest his future performance should improve.")
            elif era_diff < -0.5:
                st.error(f"📉 **Overperforming:** {selected_player}'s ERA is much lower than his FIP. His current run prevention outpaces his actual raw metrics. Expect future regression.")
            else:
                st.success(f"⚖️ **Stable:** {selected_player}'s ERA and FIP are closely aligned. Historical performance is currently a true indicator of future output.")
                
        else:
            st.subheader("League Overview: Expected Performance Differential")
            st.markdown("Sorting by **ERA minus FIP**. Pitchers at the top of this list are generating the best underlying metrics despite poor surface-level ERAs, making them prime targets for future breakouts.")
            sorted_df = raw_df.sort_values(by='ERA_minus_FIP', ascending=False)
            st.dataframe(sorted_df[display_cols].head(20))
        
        # --- PREDICTIVE SCATTER PLOT ---
        st.markdown("---")
        st.subheader("Predictive Matrix: Strikeout Rate vs. Walk Rate")
        st.markdown("Elite command profiles live in the **top-right quadrant** (High Strikeouts, Low Walks).")
        
        fig = px.scatter(
            raw_df, 
            x='BB9', 
            y='SO9', 
            hover_name='Name',
            hover_data=['Tm', 'ERA', 'FIP', 'IP'],
            color='Tm',
            labels={'BB9': 'Walks per 9 Innings (BB/9)', 'SO9': 'Strikeouts per 9 Innings (SO/9)'}
        )
        
        fig.update_xaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
