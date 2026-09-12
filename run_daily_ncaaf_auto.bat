@echo off
setlocal
cd /d C:\HagLabs\pitching-analytics-engine
if not exist haglabs_data mkdir haglabs_data
.venv\Scripts\python.exe daily_ncaaf_auto.py >> haglabs_data\daily_ncaaf_auto.log 2>&1
set "ncaaf_exit=%ERRORLEVEL%"
exit /b %ncaaf_exit%
