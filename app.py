import streamlit as st
import pandas as pd
# We change the import to specifically use the Baseball-Reference scraper
from pybaseball import pitching_stats_bref
import traceback

st.set_page_config(page_title="Pitching Analytics Matrix", layout="wide")

st.title("⚾ Pitching Analytics Matrix")
st.subheader("Data Pipeline Test: Current Season Leaders (B-Ref Data)")

st.markdown("""
Welcome to the engine. This initial dashboard pulls live data directly from standard MLB databases to ensure the pipeline is functional. 

Moving forward, this raw data will be filtered through our custom models, focusing heavily on **historical performance and future predictions** rather than standard reliability thresholds.
""")

@st.cache_data
def load_data():
    # Rerouted to pull from Baseball-Reference instead of FanGraphs
    data = pitching_stats_bref(2026)
    return data

with st.spinner('Pulling live pitching data...'):
    try:
        df = load_data()
        
        # B-Ref uses slightly different column names (e.g., 'Tm' instead of 'Team')
        display_columns = ['Name', 'Tm', 'W', 'L', 'ERA', 'SO', 'IP']
        
        # Drop empty rows and show the top 20
        st.dataframe(df[display_columns].dropna().head(20))
        st.success("Pipeline connected successfully!")
        
    except Exception as e:
        st.error("Error loading data. Here is the exact technical reason why:")
        st.code(traceback.format_exc())
