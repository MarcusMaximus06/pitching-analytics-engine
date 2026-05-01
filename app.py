import streamlit as st
import pandas as pd
from pybaseball import pitching_stats_bref
import plotly.express as px
import traceback

st.set_page_config(page_title="Pitching Analytics Matrix", layout="wide")

st.title("⚾ Pitching Analytics Matrix")

st.markdown("""
Welcome to the engine. This dashboard filters raw data through custom parameters, focusing heavily on **historical performance and future predictions** rather than standard reliability thresholds.
""")

@st.cache_data
def load_data():
    df = pitching_stats_bref(2026)
    
    if 'BB9' not in df.columns:
        df['BB9'] = (df['BB'] / df['IP']) * 9
    if 'SO9' not in df.columns:
        df['SO9'] = (df['SO'] / df['IP']) * 9
        
    if 'FIP' not in df.columns:
        hbp = df['HBP'] if 'HBP' in df.columns else 0
        df['FIP'] = ((13 * df['HR']) + (3 * (df['BB'] + hbp)) - (2 * df['SO'])) / df['IP'] + 3.15
        
    df = df.dropna(subset=['ERA', 'FIP', 'SO9', 'BB9'])
    df['ERA_minus_FIP'] = df['ERA'] - df['FIP']
    
    # ---------------------------------------------------------
    # CUSTOM PREDICTION METRIC: Future Performance Index (FPI)
    # Weighs strikeouts heavily, penalizes walks, anchors to FIP.
    # Formula: (SO/9 * 1.5) - (BB/9 * 1.2) - FIP
    # ---------------------------------------------------------
    df['FPI'] = (df['SO9'] * 1.5) - (df['BB9'] * 1.2) - df['FIP']
    
    # Round metrics for a cleaner UI
    df['FPI'] = df['FPI'].round(2)
    df['ERA_minus_FIP'] = df['ERA_minus_FIP'].round(2)
    df['FIP'] = df['FIP'].round(2)
    df['SO9'] = df['SO9'].round(2)
    df['BB9'] = df['BB9'].round(2)
    
    return df

with st.spinner('Compiling matrix and generating models...'):
    try:
        raw_df = load_data()
        
        # --- DATA FILTERING ---
        st.sidebar.header("Matrix Parameters")
        max_ip = int(raw_df['IP'].max()) if not raw_df.empty else 100
        min_ip = st.sidebar.slider("Minimum Innings Pitched (Filter Noise):", min_value=1, max_value=max_ip, value=15)
        
        filtered_df = raw_df[raw_df['IP'] >= min_ip]
        
        # --- TARGET PROFILE SEARCH ---
        st.sidebar.markdown("---")
        st.sidebar.header("Target Profile Search")
        player_list = sorted(filtered_df['Name'].unique().tolist())
        selected_player = st.sidebar.selectbox("Search for a Pitcher:", ["All Pitchers"] + player_list)
        
        # Added FPI to the display columns
        display_cols = ['Name', 'Tm', 'IP', 'ERA', 'FIP', 'FPI', 'ERA_minus_FIP', 'SO9', 'BB9']
        
        if selected_player != "All Pitchers":
            st.subheader(f"Isolated Profile: {selected_player}")
            player_data = filtered_df[filtered_df['Name'] == selected_player]
            st.dataframe(player_data[display_cols], hide_index=True)
            
            # Expanded Predictive Analysis
            fpi_score = player_data['FPI'].values[0]
            st.markdown("### Model Projection")
            if fpi_score >= 8.0:
                st.success(f"🔥 **Elite Future Projection:** {selected_player} has a massive FPI of {fpi_score}. His historical strikeout dominance points to sustained, elite success.")
            elif fpi_score >= 5.0:
                st.info(f"📈 **Strong Future Projection:** {selected_player} has an FPI of {fpi_score}. He possesses highly favorable underlying metrics for future starts.")
            elif fpi_score >= 2.0:
                st.warning(f"⚖️ **Average Future Projection:** {selected_player} has an FPI of {fpi_score}. His strikeout-to-walk ratios limit his ceiling.")
            else:
                st.error(f"📉 **Poor Future Projection:** {selected_player} has an FPI of {fpi_score}. His historical lack of strikeouts and high walk rates point to severe future regression.")
                
        else:
            st.subheader("League Overview: Future Performance Index (FPI)")
            st.markdown("Sorting by the custom **FPI Metric**. Pitchers at the top of this leaderboard are projected to be the most dominant arms moving forward based strictly on their underlying historical traits, regardless of their current surface ERA.")
            # Sort the main table by our new FPI metric
            sorted_df = filtered_df.sort_values(by='FPI', ascending=False)
            st.dataframe(sorted_df[display_cols].head(25), hide_index=True)
        
        # --- PREDICTIVE SCATTER PLOT ---
        st.markdown("---")
        st.subheader("Predictive Matrix: Strikeout Rate vs. Walk Rate")
        st.markdown("Elite command profiles live in the **top-right quadrant** (High Strikeouts, Low Walks).")
        
        fig = px.scatter(
            filtered_df, 
            x='BB9', 
            y='SO9', 
            hover_name='Name',
            hover_data=['Tm', 'ERA', 'FIP', 'FPI', 'IP'],
            color='FPI', # Color the dots based on their new FPI score
            color_continuous_scale="Viridis",
            labels={'BB9': 'Walks per 9 Innings (BB/9)', 'SO9': 'Strikeouts per 9 Innings (SO/9)', 'FPI': 'Future Perf. Index'}
        )
        
        fig.update_xaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
