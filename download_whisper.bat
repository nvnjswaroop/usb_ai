@echo off
chcp 65001 >nul 2>&1
echo ============================================================
echo  USB AI - Download Whisper Voice Model
echo ============================================================
echo.
echo  tiny  -  75MB  fastest
echo  base  - 145MB  recommended
echo  small - 480MB  most accurate
echo.
set "USB=%~dp0"
set "PY=%USB%python\win\python.exe"
if not exist "%PY%" ( echo [ERROR] Run setup.bat first. & pause & exit /b 1 )
set /p CHOICE="Model [tiny/base/small] (Enter=base)]: "

if "%CHOICE%"=="" set "CHOICE=base"

rem Convert to lowercase for case-insensitive check
set "CHOICE_LOWER=%CHOICE%"
for %%i in (A B C D E F G H I J K L M N O P Q R S T U V W X Y Z) do set "CHOICE_LOWER=%CHOICE_LOWER:%%i=%%i%"
rem Note: standard batch lowercase is tricky; using a simpler approach for this specific set
if /I "%CHOICE%"=="tiny" (set "CHOICE=tiny") else if /I "%CHOICE%"=="base" (set "CHOICE=base") else if /I "%CHOICE%"=="small" (set "CHOICE=small") else (set "CHOICE=base")

set "WDIR=%USB%whisper_models"
if not exist "%WDIR%" mkdir "%WDIR%"
echo.
echo Downloading '%CHOICE%' model...
"%PY%" -c "import whisper; whisper.load_model('%CHOICE%', download_root=r'%WDIR%'); print('Done!')"
if errorlevel 1 (
    echo [ERROR] Download failed.
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)
echo.
echo Whisper '%CHOICE%' ready. Mic button now works offline.
pause
