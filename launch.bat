@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
echo ============================================================
echo  USB AI - Launcher
echo ============================================================
set "USB=%~dp0"
set "PY=%USB%python\win\python.exe"

REM    ponytail: hardcode the embeddable path — no PATH probe, no registry,
REM    no winget, no py launcher. The contract is: setup.bat owns python\win\.
if not exist "%PY%" (
    echo [ERROR] %PY% not found. Run setup.bat first.
    pause
    exit /b 1
)
echo Using Python: %PY%

REM    Set PYTHONHOME/PYTHONPATH only when using the embeddable layout, so
REM    `import site` resolves and `app.main` is on the path.
set "PYTHONHOME=%USB%python\win"
set "PYTHONPATH=%USB%python\win\Lib\site-packages;%USB%app"
set "PATH=%USB%python\win;%USB%python\win\Scripts;%PATH%"

echo Freeing port 8080...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":8080 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
REM    ponytail: ping -n 2 sleeps ~1s on every Windows version without needing
REM    the timeout.exe binary on PATH. setup.bat uses PowerShell Start-Sleep
REM    for the same reason — this one stays pure cmd. Audit 2026-09-16
REM    smoke test: GNU `timeout` from MSYS git-bash was shadowing Windows
REM    `timeout /t N` and the readiness loop was running at full speed,
REM    burning CPU before the server even came up.
ping -n 2 127.0.0.1 >nul

if not exist "%USB%app\main.py" (
    echo [ERROR] app\main.py not found. Drive may be incomplete.
    pause
    exit /b 1
)

echo Starting server...
cd /d "%USB%app"
REM    ponytail: invoke via `cmd /c` so MSYS bash, git-bash, and bare
REM    double-click all see the same redirect. The inner `>` and `2>&1`
REM    are cmd's, never touched by a shell that might strip them.
REM    Audit 2026-09-16 smoke test: bare `start /b ... > log 2>&1` got
REM    its `>` eaten by bash's redirection when invoked through MSYS,
REM    and `^>` worked under bash but not bare cmd. cmd /c is the
REM    cross-shell port.
start /b cmd /c ""%PY%" main.py > "%USB%server.log" 2>&1"

echo Waiting for server to be ready...
set READY=0
for /l %%i in (1,1,45) do (
    if !READY!==0 (
        "%PY%" -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status',timeout=2)" >nul 2>&1
        if not errorlevel 1 set READY=1
        if !READY!==0 (
            REM    ponytail: see port-free block above — same fix.
            ping -n 2 127.0.0.1 >nul
            echo    Waiting... %%i/45
        )
    )
)

if !READY!==0 (
    echo [WARNING] Server slow to respond. Check server.log if page fails.
) else (
    echo Server is ready!
)

start "" "http://localhost:8080"

echo.
echo USB AI running at http://localhost:8080
echo Close this window to stop the server.
echo.
pause
