@echo off
REM Stops the NexScout autopilot and dashboard windows.
taskkill /FI "WINDOWTITLE eq NexScout autopilot*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq NexScout dashboard*" /T /F >nul 2>&1
echo NexScout stopped.
