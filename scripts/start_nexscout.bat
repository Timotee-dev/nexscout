@echo off
REM Starts NexScout: autopilot in the background + the dashboard, then opens your browser.
REM Double-click this, or let install_startup.bat run it every time you log in.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
tasklist /FI "WINDOWTITLE eq NexScout autopilot*" | find /I "cmd.exe" >nul && goto dashboard
start "NexScout autopilot" /min cmd /c ".venv\Scripts\python.exe manage.py autopilot >> logs\autopilot.log 2>&1"
:dashboard
tasklist /FI "WINDOWTITLE eq NexScout dashboard*" | find /I "cmd.exe" >nul && goto open
start "NexScout dashboard" /min cmd /c ".venv\Scripts\python.exe manage.py runserver 8001 >> logs\dashboard.log 2>&1"
timeout /t 5 /nobreak >nul
:open
start "" http://127.0.0.1:8001/
endlocal
