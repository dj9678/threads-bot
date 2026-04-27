@echo off
REM ============================================================
REM  threads-bot stop script
REM  - Reads PID from bot.pid and terminates the process
REM  - If PID file missing or process already dead, just cleans up
REM ============================================================

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%~dp0"
set "PID_FILE=%PROJECT_ROOT%bot.pid"

REM Selective kill: ExecutablePath OR CommandLine match (catches orphans too)
echo [*] Stopping any threads-bot instance(s)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe' or name='python.exe'\" | Where-Object { ($_.ExecutablePath -like '%PROJECT_ROOT%.venv*') -or ($_.CommandLine -like '*%PROJECT_ROOT%scripts\telegram_listener.py*') -or ($_.CommandLine -like '*telegram_listener.py*') } | ForEach-Object { Write-Host ('  killed PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>&1
echo [+] Done.

endlocal
