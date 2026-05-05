@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
echo ============================================================
echo  USB AI - Update llama-cpp-python
echo  Adds support for new models (Gemma 4, Qwen3, etc.)
echo ============================================================
set "USB=%~dp0"
set "PY=%USB%python\win\python.exe"
if not exist "%PY%" ( echo [ERROR] Run setup.bat first. & pause & exit /b 1 )
echo Current version:
"%PY%" -m pip show llama-cpp-python 2>nul | findstr /i "version"
echo.
echo Removing old version...
"%PY%" -m pip uninstall llama-cpp-python -y >nul 2>&1
echo Installing latest CPU build...
"%PY%" -m pip install llama-cpp-python --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --no-warn-script-location --quiet
if errorlevel 1 (
    echo Trying PyPI...
    "%PY%" -m pip install llama-cpp-python --prefer-binary --no-warn-script-location --quiet
    if errorlevel 1 (
        echo [ERROR] Update failed.
        echo Please check your internet connection and try again.
        pause
        exit /b 1
    )
)
echo.
echo New version:
"%PY%" -m pip show llama-cpp-python 2>nul | findstr /i "version"
echo.
echo Done! Restart launch.bat to use the new version.
pause