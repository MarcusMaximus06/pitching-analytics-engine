import streamlit as st
import pandas as pd
from pybaseball import pitching_stats
import traceback

# Set up the visual configuration of the page
st.set_page_config(page_title="Pitching Analytics Matrix", layout="wide")

st.title("⚾ Pitching Analytics Matrix")
st.subheader("Data Pipeline Test: Current Season Leaders")

st.markdown("""
Welcome to the engine. This initial dashboard pulls live data directly from standard MLB databases to ensure the pipeline is functional. 

Moving forward, this raw data will be filtered through our custom models, focusing heavily on **historical performance and future predictions** rather than standard reliability thresholds.
""")

# @st.cache_data tells the app to remember the data so it doesn't download it every time
@st.cache_data
def load_data():
    # Pull current 2026 pitching stats
    data = pitching_stats(2026, qual=1)
    return data

# Load the data and display a loading spinner while it works
with st.spinner('Pulling live pitching data...'):
    try:
        df = load_data()
        
        # Display the top 20 pitchers, selecting just the core columns for a clean view
        display_columns = ['Name', 'Team', 'W', 'L', 'ERA', 'SO', 'IP', 'WAR']
        st.dataframe(df[display_columns].head(20))
        st.success("Pipeline connected successfully!")
        
    except Exception as e:
        # This will now print the EXACT technical error to the screen so we can fix it
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
