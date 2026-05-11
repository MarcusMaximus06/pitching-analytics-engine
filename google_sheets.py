import os
import gspread
import streamlit as st


GOOGLE_CREDS_PATH = (
    '/etc/secrets/google_credentials.json'
    if os.path.exists('/etc/secrets/google_credentials.json')
    else 'google_credentials.json'
)


@st.cache_resource
def get_google_client():
    return gspread.service_account(filename=GOOGLE_CREDS_PATH)


def get_google_worksheet(spreadsheet_name, worksheet_name):
    gc = get_google_client()
    sh = gc.open(spreadsheet_name)
    return sh.worksheet(worksheet_name)
