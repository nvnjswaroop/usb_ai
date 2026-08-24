@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
echo ============================================================
echo  USB AI - Update LLM runtime (llama-server)
echo  Downloads the pinned official prebuilt binary. No compiling.
echo ============================================================
set "USB=%~dp0"
set "PY=%USB%python\win\python.exe"
if not exist "%PY%" ( echo [ERROR] Run setup.bat first. & pause & exit /b 1 )

REM    ponytail: variant comes from args (cpu default). e.g.
REM      update_llama.bat cuda     -> CUDA build
REM      update_llama.bat --list   -> show latest assets (passed straight through)
set "VARIANT=%1"
if "%VARIANT%"=="" set "VARIANT=cpu"
if "%VARIANT:~0,2%"=="--" (
    "%PY%" "%USB%scripts\fetch_llama.py" %*
    if errorlevel 1 ( echo [ERROR] Failed. & pause & exit /b 1 )
    pause
    exit /b 0
)

echo Current binary:
if exist "%USB%bin\llama\llama-server.exe" (
    "%USB%bin\llama\llama-server.exe" --version 2>nul | findstr /i "version"
) else (
    echo   none installed yet
)
echo.
echo Fetching pinned build (%VARIANT%)...
"%PY%" "%USB%scripts\fetch_llama.py" --variant %VARIANT% %2 %3
if errorlevel 1 (
    echo [ERROR] Update failed. See output above.
    pause
    exit /b 1
)
echo.
echo New binary:
"%USB%bin\llama\llama-server.exe" --version 2>nul | findstr /i "version"
echo.
echo Done! Restart launch.bat to use it.
pause
