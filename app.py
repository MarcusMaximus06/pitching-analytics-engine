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
    # Pull current 2026 pitching stats
    df = pitching_stats_bref(2026)
    
    # Drop pitchers with missing critical data to clean the set
    df = df.dropna(subset=['ERA', 'FIP', 'SO9', 'BB9'])
    
    # 1. THE MATH: Calculate the differential between actual ERA and Expected Performance (FIP)
    # A negative number means their ERA is higher than their FIP (Unlucky / Due for positive progression)
    df['ERA_minus_FIP'] = df['ERA'] - df['FIP']
    return df

with st.spinner('Compiling matrix and generating models...'):
    try:
        raw_df = load_data()
        
        # --- 3. TARGET PROFILE SEARCH (The Tool) ---
        st.sidebar.header("Target Profile Search")
        # Create a sorted list of all player names for the dropdown
        player_list = sorted(raw_df['Name'].unique().tolist())
        selected_player = st.sidebar.selectbox("Search for a Pitcher:", ["All Pitchers"] + player_list)
        
        # Define the columns we actually want to look at
        display_cols = ['Name', 'Tm', 'IP', 'ERA', 'FIP', 'ERA_minus_FIP', 'SO9', 'BB9', 'WAR']
        
        if selected_player != "All Pitchers":
            # Isolate the specific player chosen in the sidebar
            st.subheader(f"Isolated Profile: {selected_player}")
            player_data = raw_df[raw_df['Name'] == selected_player]
            st.dataframe(player_data[display_cols])
            
            # Predictive Analysis text based on the math
            era_diff = player_data['ERA_minus_FIP'].values[0]
            if era_diff > 0.5:
                st.warning(f"📈 **Positive Progression Candidate:** {selected_player}'s ERA is significantly higher than his FIP. The underlying metrics suggest his future performance should improve.")
            elif era_diff < -0.5:
                st.error(f"📉 **Overperforming:** {selected_player}'s ERA is much lower than his FIP. His current run prevention outpaces his actual raw metrics. Expect future regression.")
            else:
                st.success(f"⚖️ **Stable:** {selected_player}'s ERA and FIP are closely aligned. Historical performance is currently a true indicator of future output.")
                
        else:
            # --- 1. EXPECTED PERFORMANCE MODELING (The Math) ---
            st.subheader("League Overview: Expected Performance Differential")
            st.markdown("Sorting by **ERA minus FIP**. Pitchers at the top of this list are generating the best underlying metrics despite poor surface-level ERAs, making them prime targets for future breakouts.")
            
            # Sort the dataframe to show the most "unlucky" pitchers first
            sorted_df = raw_df.sort_values(by='ERA_minus_FIP', ascending=False)
            st.dataframe(sorted_df[display_cols].head(20))
        
        # --- 2. PREDICTIVE SCATTER PLOT (The Visual) ---
        st.markdown("---")
        st.subheader("Predictive Matrix: Strikeout Rate vs. Walk Rate")
        st.markdown("Elite command profiles live in the **top-right quadrant** (High Strikeouts, Low Walks).")
        
        # Build the interactive Plotly chart
        fig = px.scatter(
            raw_df, 
            x='BB9', 
            y='SO9', 
            hover_name='Name',
            hover_data=['Tm', 'ERA', 'FIP', 'IP'],
            color='Tm', # Color-code the dots by Team
            labels={'BB9': 'Walks per 9 Innings (BB/9)', 'SO9': 'Strikeouts per 9 Innings (SO/9)'}
        )
        
        # Reverse the X-axis so fewer walks (better) pushes the dot to the right side of the screen
        fig.update_xaxes(autorange="reversed")
        
        # Display the chart making it stretch to the full width of the screen
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
