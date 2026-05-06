import os
import requests
import gspread
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def get_local_date_str():
    utc_now = datetime.utcnow()
    central_now = utc_now - timedelta(hours=5) 
    return central_now.strftime("%Y-%m-%d")

# ==========================================
# 1. SEASONAL GATING LOGIC
# ==========================================
today = datetime.utcnow() - timedelta(hours=5)
current_month = today.month

# Define active months for each sport (1 = Jan, 12 = Dec)
IS_SOFTBALL_SEASON = 2 <= current_month <= 6   # Feb to June
IS_MLB_SEASON = 3 <= current_month <= 11       # March to Nov

print(f"--- APEX SYNDICATE CRON JOB STARTED FOR {get_local_date_str()} ---")
print(f"Softball Season Active: {IS_SOFTBALL_SEASON}")
print(f"MLB Season Active: {IS_MLB_SEASON}")

# ==========================================
# 2. GOOGLE SHEETS CONNECTION
# ==========================================
def get_google_sheet(tab_name):
    try:
        gc = gspread.service_account(filename='/etc/secrets/google_credentials.json') if os.path.exists('/etc/secrets/google_credentials.json') else gspread.service_account(filename='google_credentials.json')
        sh = gc.open("MLB Daily Prediction Model")
        return sh.worksheet(tab_name)
    except Exception as e:
        print(f"Google Sheets Auth Error: {e}")
        return None

# ==========================================
# 3. SOFTBALL AUTONOMOUS PROTOCOLS
# ==========================================
def run_softball_automation():
    print("Initiating NCAA Softball Protocols...")
    worksheet = get_google_sheet("Softball Log")
    if not worksheet: return

    # --- AUTO-GRADER ---
    print("Running Softball Auto-Grader...")
    data = worksheet.get_all_values()
    pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
    
    if pending_rows:
        pending_dates = list(set([row[0] for i, row in pending_rows]))
        score_dict = {}
        for d_str in pending_dates:
            try:
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                url = f"https://data.ncaa.com/casandbox/scoreboard/softball/d1/{dt.strftime('%Y')}/{dt.strftime('%m')}/{dt.strftime('%d')}/scoreboard.json"
                resp = requests.get(url, timeout=10).json()
                if 'games' in resp:
                    for g in resp['games']:
                        game_data = g.get('game', g)
                        if game_data.get('gameState', '').lower() == 'final':
                            away_info = game_data.get('away', {})
                            home_info = game_data.get('home', {})
                            away_name = away_info.get('names', {}).get('short', away_info.get('teamName', ''))
                            home_name = home_info.get('names', {}).get('short', home_info.get('teamName', ''))
                            try:
                                a_score, h_score = int(away_info.get('score', 0)), int(home_info.get('score', 0))
                                winner = away_name if a_score > h_score else home_name
                                score_dict[f"{d_str}_{away_name.lower().replace('.','').replace(' ','')}"] = winner.lower().replace(' ','')
                                score_dict[f"{d_str}_{home_name.lower().replace('.','').replace(' ','')}"] = winner.lower().replace(' ','')
                            except: continue
            except: continue

        updates = 0
        for i, row in pending_rows:
            d_str, away_t, model_pick = row[0], row[1].lower().replace('.','').replace(' ',''), row[7].lower().replace(' ','')
            lookup_key = f"{d_str}_{away_t}"
            if lookup_key in score_dict:
                actual_winner = score_dict[lookup_key]
                new_status = "WIN" if (model_pick in actual_winner or actual_winner in model_pick) else "LOSS"
                worksheet.update_cell(i + 1, 9, new_status)
                updates += 1
        print(f"Softball Games Graded: {updates}")
    else:
        print("No pending Softball bets to grade.")

# ==========================================
# 4. MLB AUTONOMOUS PROTOCOLS
# ==========================================
def run_mlb_automation():
    print("Initiating MLB Baseball Protocols...")
    worksheet = get_google_sheet("Master Log")
    if not worksheet: return

    # --- AUTO-GRADER ---
    print("Running MLB Auto-Grader...")
    data = worksheet.get_all_values()
    pending_rows = [(i, row) for i, row in enumerate(data) if i > 0 and len(row) >= 9 and row[8] == "PENDING"]
    
    if pending_rows:
        pending_dates = list(set([row[0] for i, row in pending_rows]))
        score_dict = {}
        for d_str in pending_dates:
            try:
                url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d_str}"
                resp = requests.get(url).json()
                if 'dates' in resp and len(resp['dates']) > 0:
                    for g in resp['dates'][0]['games']:
                        if g['status']['abstractGameState'] == 'Final':
                            away = g['teams']['away']['team']['name']
                            home = g['teams']['home']['team']['name']
                            winner = away if g['teams']['away'].get('score', 0) > g['teams']['home'].get('score', 0) else home
                            score_dict[f"{d_str}_{away}"] = winner
            except: continue

        updates = 0
        for i, row in pending_rows:
            d_str, away_t, model_pick = row[0], row[1], row[7]
            lookup_key = f"{d_str}_{away_t}"
            if lookup_key in score_dict:
                actual_winner = score_dict[lookup_key]
                new_status = "WIN" if model_pick == actual_winner else "LOSS"
                worksheet.update_cell(i + 1, 9, new_status)
                updates += 1
        print(f"MLB Games Graded: {updates}")
    else:
        print("No pending MLB bets to grade.")

# ==========================================
# EXECUTION ROUTER
# ==========================================
if __name__ == "__main__":
    if IS_SOFTBALL_SEASON:
        try:
            run_softball_automation()
        except Exception as e:
            print(f"Softball Error: {e}")
            
    if IS_MLB_SEASON:
        try:
            run_mlb_automation()
        except Exception as e:
            print(f"MLB Error: {e}")
            
    print("--- APEX SYNDICATE CRON JOB COMPLETE ---")
