@echo off
REM Install the scheduled task: every 4 hours, 8am to midnight, only while you are logged on.
set TASK=NexScout
set BAT=%~dp0run_scout.bat
schtasks /Create /F /TN "%TASK%" /TR "\"%BAT%\"" /SC DAILY /ST 08:07 /RI 240 /DU 0016:00 /IT
echo.
echo Installed. Test it now with:  schtasks /Run /TN "%TASK%"
echo Remove it with:            schtasks /Delete /TN "%TASK%" /F
