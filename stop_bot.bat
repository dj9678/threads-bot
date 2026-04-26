@echo off
REM ============================================================
REM  threads-bot stop script
REM  - Reads PID from bot.pid and terminates the process
REM  - If PID file missing or process already dead, just cleans up
REM ============================================================

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%~dp0"
set "PID_FILE=%PROJECT_ROOT%bot.pid"

if not exist "%PID_FILE%" (
    echo [!] bot.pid not found. Bot may not be running.
    echo     Checking for any pythonw.exe processes:
    tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | find "pythonw.exe"
    if errorlevel 1 (
        echo     No pythonw.exe processes running.
    ) else (
        echo.
        echo     The processes above may belong to other bots.
        echo     Stop manually if needed: taskkill /PID [number] /F
    )
    exit /b 0
)

set /p PID=<"%PID_FILE%"

REM Check if PID is alive
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 (
    echo [!] PID %PID% is already gone.
    del "%PID_FILE%"
    echo [+] Stale PID file cleaned up.
    exit /b 0
)

REM Terminate
echo [*] Stopping PID %PID%...
taskkill /PID %PID% /F >nul 2>&1

if errorlevel 1 (
    echo [!] Stop failed. May require admin privileges.
) else (
    echo [+] Bot stopped successfully.
    del "%PID_FILE%"
)

endlocal
