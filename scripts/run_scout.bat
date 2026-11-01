@echo off
REM NexScout scheduled run. Task Scheduler calls this file.
REM Runs from the project folder, uses the project's venv, appends to logs\scout.log.
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
echo. >> logs\scout.log
echo ===== %date% %time% ===== >> logs\scout.log
".venv\Scripts\python.exe" manage.py run_scout --minutes 30 >> logs\scout.log 2>&1
endlocal
