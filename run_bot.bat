@echo off
REM ============================================================
REM  threads-bot background launcher
REM  - On first run: creates .venv and installs deps
REM  - On later runs: reuses .venv (fast)
REM  - Runs detached; window can close
REM  - PID saved to bot.pid (used by stop_bot.bat)
REM ============================================================

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

set "VENV_DIR=%PROJECT_ROOT%.venv"
set "SCRIPT_PATH=%PROJECT_ROOT%scripts\telegram_listener.py"
set "LOG_FILE=%PROJECT_ROOT%bot.log"
set "ERR_FILE=%PROJECT_ROOT%bot_error.log"
set "PID_FILE=%PROJECT_ROOT%bot.pid"

REM Defensive cleanup: ExecutablePath OR CommandLine (catches orphans too)
echo [*] Cleaning up any existing instance(s)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe' or name='python.exe'\" | Where-Object { ($_.ExecutablePath -like '%PROJECT_ROOT%.venv*') -or ($_.CommandLine -like '*%PROJECT_ROOT%scripts\telegram_listener.py*') -or ($_.CommandLine -like '*telegram_listener.py*') } | ForEach-Object { Write-Host ('  killed PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>&1
timeout /t 2 /nobreak >nul

REM Create .venv if missing
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [*] Creating virtual environment... (first time only, 1-2 min^)
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [!] venv creation failed. Is Python installed?
        pause
        exit /b 1
    )
    echo [*] Installing dependencies...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV_DIR%\Scripts\python.exe" -m pip install python-telegram-bot==21.6 python-dotenv playwright
    if errorlevel 1 (
        echo [!] Dependency install failed.
        exit /b 1
    )
    "%VENV_DIR%\Scripts\python.exe" -m playwright install chromium
    if errorlevel 1 (
        echo [!] Playwright chromium install failed.
        exit /b 1
    )
    echo [+] Initial setup complete.
)

REM Check .env
if not exist "%PROJECT_ROOT%.env" (
    echo [!] .env not found: %PROJECT_ROOT%.env
    exit /b 1
)

REM Check script
if not exist "%SCRIPT_PATH%" (
    echo [!] Script not found: %SCRIPT_PATH%
    exit /b 1
)

REM Launch in background (no window; logs to bot.log / bot_error.log)
echo [*] Starting bot in background...
powershell -WindowStyle Hidden -Command ^
    "$p = Start-Process -FilePath '%VENV_DIR%\Scripts\pythonw.exe' -ArgumentList '%SCRIPT_PATH%' -WorkingDirectory '%PROJECT_ROOT%' -RedirectStandardOutput '%LOG_FILE%' -RedirectStandardError '%ERR_FILE%' -PassThru -WindowStyle Hidden; $p.Id | Out-File -FilePath '%PID_FILE%' -Encoding ASCII -NoNewline"

REM Give it 2s to start
timeout /t 2 /nobreak >nul

if exist "%PID_FILE%" (
    set /p NEW_PID=<"%PID_FILE%"
    tasklist /FI "PID eq !NEW_PID!" 2>nul | find "!NEW_PID!" >nul
    if not errorlevel 1 (
        echo [+] Bot running. PID: !NEW_PID!
        echo     Log file: %LOG_FILE%
        echo     Error log: %ERR_FILE%
        echo     Run stop_bot.bat to stop.
    ) else (
        echo [!] Bot exited shortly after start.
        echo     Check %ERR_FILE% for details.
    )
) else (
    echo [!] PID file not created. Bot may not have started.
)

endlocal
