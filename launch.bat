@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
echo ============================================================
echo  USB AI - Launcher
echo ============================================================
set "USB=%~dp0"

REM ── Resolve Python: py_path.txt (from setup.bat) → else fallback chain ──
set "PY="
if exist "%USB%py_path.txt" (
    set /p PY=<"%USB%py_path.txt"
)
if defined PY if not exist "!PY!" set "PY="

if not defined PY (
    REM No setup record — try system Python first, then embeddable
    for /f "tokens=2" %%v in ('reg query "HKLM\SOFTWARE\Python\PythonCore\3.11\InstallPath" /ve 2^>nul ^| findstr /ri "REG_SZ"') do set "T=%%vpython.exe"
    if defined T if exist "!T!" set "PY=!T!"
)
if not defined PY if exist "%USB%python\win\python.exe" set "PY=%USB%python\win\python.exe"

if not defined PY (
    echo [ERROR] Run setup.bat first.
    pause
    exit /b 1
)
echo Using Python: %PY%

REM Only set PYTHONHOME/PYTHONPATH when using the embeddable layout.
echo %PY% | findstr /i "python\\win" >nul 2>&1
if not errorlevel 1 (
    set "PYTHONHOME=%USB%python\win"
    set "PYTHONPATH=%USB%python\win\Lib\site-packages;%USB%app"
    set "PATH=%USB%python\win;%USB%python\win\Scripts;%PATH%"
)

echo Freeing port 8080...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":8080 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

if not exist "%USB%app" (
    echo [ERROR] app\ directory missing.
    pause
    exit /b 1
)

echo Starting server...
cd /d "%USB%app"
start /b "" "%PY%" main.py > "%USB%server.log" 2>&1

echo Waiting for server to be ready...
set READY=0
for /l %%i in (1,1,45) do (
    if !READY!==0 (
        "%PY%" -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status',timeout=2)" >nul 2>&1
        if not errorlevel 1 set READY=1
        if !READY!==0 (
            timeout /t 1 /nobreak >nul
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