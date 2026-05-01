import streamlit as st
import pandas as pd
from pybaseball import pitching_stats_bref, pitching_stats_range, statcast_pitcher_expected_stats
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
    # 1. Season Data (Baseball-Reference)
    season_df = pitching_stats_bref(2026)
    season_df = calc_advanced_metrics(season_df)
    
    # 2. Momentum Data (Last 14 Days)
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

    # 3. STATCAST DATA (Baseball Savant Expected Stats)
    try:
        # Pulls aggregated optical tracking data (takes very little memory)
        savant_df = statcast_pitcher_expected_stats(2026, 10) # minimum 10 batted ball events
        # Format the Savant names to match B-Ref names
        savant_df['Name'] = savant_df['first_name'].astype(str).str.strip() + ' ' + savant_df['last_name'].astype(str).str.strip()
        
        # Grab Expected wOBA and Expected ERA
        savant_df = savant_df[['Name', 'est_woba', 'xera']].rename(columns={'est_woba': 'xwOBA', 'xera': 'xERA'})
        
        # Merge the Statcast data into our main engine
        merged_df = pd.merge(merged_df, savant_df, on='Name', how='left')
    except Exception as e:
        # Fallback if Savant is updating
        merged_df['xwOBA'] = None
        merged_df['xERA'] = None

    return merged_df

with st.spinner('Compiling matrix, pulling Statcast tracking, and generating models...'):
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
        
        # Added xERA and xwOBA to the dashboard columns
        display_cols = ['Name', 'Tm', 'IP', 'FPI', 'Momentum_Shift', 'ERA', 'xERA', 'xwOBA', 'SO9', 'BB9']
        
        if selected_player != "All Pitchers":
            st.subheader(f"Isolated Profile: {selected_player}")
            player_data = filtered_df[filtered_df['Name'] == selected_player]
            st.dataframe(player_data[display_cols], hide_index=True)
            
            momentum = player_data['Momentum_Shift'].values[0]
            actual_era = player_data['ERA'].values[0]
            expected_era = player_data['xERA'].values[0]
            
            st.markdown("### Model Projection")
            
            # Momentum Analysis
            if momentum > 2.0:
                st.success(f"📈 **Surging:** {selected_player} has a massive Momentum Shift of +{momentum}. He is currently pitching significantly better than his season averages indicate.")
            elif momentum < -2.0:
                st.error(f"📉 **Collapsing:** {selected_player}'s recent FPI is much lower than his season average. He is in a severe slump or experiencing underlying mechanical issues.")
                
            # Statcast Luck Analysis
            if pd.notna(expected_era) and pd.notna(actual_era):
                era_diff = actual_era - expected_era
                if era_diff > 0.75:
                    st.warning(f"🛸 **Statcast Insight - Horrible Luck:** {selected_player}'s actual ERA ({actual_era}) is much worse than his Statcast Expected ERA ({expected_era}). Based on the exit velocity of hits against him, his defense has let him down. Expect massive positive regression.")
                elif era_diff < -0.75:
                    st.error(f"🛸 **Statcast Insight - Smoke & Mirrors:** {selected_player}'s actual ERA ({actual_era}) is much better than his Statcast Expected ERA ({expected_era}). Hitters are crushing the ball, but they happen to be flying right into fielders' gloves. Expect hard negative regression.")

        else:
            st.subheader("League Overview: Statcast & Momentum Tracker")
            st.markdown("Sorting by **Momentum Shift**. You can now compare a pitcher's actual ERA against their Statcast **xERA** (Expected ERA) to identify hidden gems.")
            sorted_df = filtered_df.sort_values(by='Momentum_Shift', ascending=False)
            st.dataframe(sorted_df[display_cols].head(25), hide_index=True)
        
        st.markdown("---")
        st.subheader("Predictive Matrix: Strikeout Rate vs. Walk Rate")
        st.markdown("Elite command profiles live in the **top-right quadrant**.")
        
        fig = px.scatter(
            filtered_df, 
            x='BB9', 
            y='SO9', 
            hover_name='Name',
            hover_data=['Tm', 'FPI', 'Momentum_Shift', 'ERA', 'xERA'],
            color='FPI', 
            color_continuous_scale="Viridis",
            labels={'BB9': 'Walks per 9 Innings (BB/9)', 'SO9': 'Strikeouts per 9 Innings (SO/9)', 'FPI': 'Season FPI'}
        )
        
        fig.update_xaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
