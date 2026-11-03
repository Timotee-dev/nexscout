@echo off
REM Makes NexScout start by itself every time you log in to Windows.
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
(
  echo @echo off
  echo call "%~dp0start_nexscout.bat"
) > "%STARTUP%\NexScout.bat"
REM The old Task Scheduler job is no longer needed (autopilot replaces it).
schtasks /Delete /TN "NexScout" /F >nul 2>&1
echo NexScout will now start automatically when you log in.
echo To undo: run remove_startup.bat
call "%~dp0start_nexscout.bat"
