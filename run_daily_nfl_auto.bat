@echo off
setlocal
cd /d C:\HagLabs\pitching-analytics-engine
if not exist haglabs_data mkdir haglabs_data
.venv\Scripts\python.exe daily_nfl_auto.py >> haglabs_data\daily_nfl_auto.log 2>&1
set "nfl_exit=%ERRORLEVEL%"
exit /b %nfl_exit%
