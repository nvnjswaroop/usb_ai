@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
echo ============================================================
echo  USB AI - Complete Setup
echo ============================================================
set "USB=%~dp0"
set "WIN_DIR=%USB%python\win"
set "PY=%WIN_DIR%\python.exe"

if exist "%WIN_DIR%" (
    echo [1/7] Killing any running python process from USB before cleanup...
    taskkill /F /IM python.exe /T >nul 2>&1
    timeout /t 2 /nobreak >nul

    echo [1/7] Python directory found. Removing for fresh install...
    rd /s /q "%WIN_DIR%" >nul 2>&1
    if exist "%WIN_DIR%" (
        echo [ERROR] Could not delete old Python directory.
        echo Please manually delete: %WIN_DIR%
        echo Then run setup.bat again.
        pause
        exit /b 1
    )
)

echo [1/7] Downloading latest Python 3.11 embeddable...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile '%TEMP%\py-embed.zip' -UseBasicParsing"
if errorlevel 1 (
    echo [ERROR] Download failed.
    pause
    exit /b 1
)
if not exist "%WIN_DIR%" mkdir "%WIN_DIR%"

echo       Extracting Python...
powershell -Command "Expand-Archive -Path '%TEMP%\py-embed.zip' -DestinationPath '%WIN_DIR%' -Force"
if errorlevel 1 (
    echo [ERROR] Extraction failed.
    pause
    exit /b 1
)
echo       Python extracted.

:patch_pth
echo [2/7] Patching python311._pth...
set "PTH=%WIN_DIR%\python311._pth"
if exist "%PTH%" (
    powershell -Command "(Get-Content '%PTH%') -replace '#import site','import site' | Set-Content '%PTH%'"
    echo       Patched.
)

echo [3/7] Installing pip...
"%PY%" -m ensurepip --upgrade --quiet 2>nul
if errorlevel 1 (
    echo       Downloading get-pip.py...
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py' -UseBasicParsing"
    if errorlevel 1 (
        echo [ERROR] Could not download get-pip.py. Check internet connection.
        pause
        exit /b 1
    )
    "%PY%" "%TEMP%\get-pip.py" --no-warn-script-location --quiet
    if errorlevel 1 (
        echo [ERROR] Failed to install pip.
        pause
        exit /b 1
    )
)
"%PY%" -m pip install --upgrade pip --no-warn-script-location --quiet
echo       pip ready.

echo [4/7] Installing base packages...
"%PY%" -m pip install fastapi "uvicorn[standard]" pydantic python-pptx python-multipart aiofiles pypdf pymupdf jinja2 --no-warn-script-location --quiet
if errorlevel 1 (
    echo [ERROR] Base packages failed.
    pause
    exit /b 1
)
echo       Base packages done.

echo [5/7] Installing llama-cpp-python (CPU wheel)...
"%PY%" -m pip install "numpy>=1.20" "diskcache>=5.6.1" --no-warn-script-location --quiet

for /f "delims=" %%r in ('powershell -Command "(Invoke-RestMethod \"https://api.github.com/repos/abetlen/llama-cpp-python/releases\") | Where-Object { $_.tag_name -notlike '*cu*' -and $_.tag_name -notlike '*metal*' } | Select-Object -First 1 -ExpandProperty tag_name | ForEach-Object { $_.TrimStart('v') }"') do set "VER=%%r"

if not defined VER (
    echo [WARNING] Could not determine latest version. Installing from PyPI...
    "%PY%" -m pip install llama-cpp-python --no-warn-script-location --quiet
) else (
    echo       Latest CPU version: !VER!
    "%PY%" -m pip install "llama-cpp-python==!VER!" --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --no-warn-script-location --quiet
    if errorlevel 1 (
        echo       Trying PyPI fallback...
        "%PY%" -m pip install llama-cpp-python --no-warn-script-location --quiet
        if errorlevel 1 (
            echo [ERROR] llama-cpp-python failed. Please install manually.
            pause
            exit /b 1
        )
    )
)
echo       llama-cpp-python done.

echo [6/7] Installing feature packages...
"%PY%" -m pip install duckduckgo-search --no-warn-script-location --quiet
if errorlevel 1 echo [WARNING] duckduckgo-search unavailable.

"%PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cpu --no-warn-script-location --quiet
if errorlevel 1 echo [WARNING] torch unavailable.

"%PY%" -m pip install openai-whisper --no-warn-script-location --quiet
if errorlevel 1 echo [WARNING] openai-whisper unavailable. Voice input disabled.
echo       Feature packages done.

echo [7/7] Creating folders...
for %%d in (models history output prefeeds whisper_models) do (
    if not exist "%USB%%%d" mkdir "%USB%%%d"
)
if not exist "%USB%prefeeds\system_prompt.txt" (
    echo You are a helpful AI assistant on a USB drive. Completely private. Be concise and honest.> "%USB%prefeeds\system_prompt.txt"
)

echo.
echo ============================================================
echo  Setup complete!
echo  1. Drop a .gguf model into models\
echo  2. Run download_whisper.bat for voice input
echo  3. Run launch.bat
echo ============================================================
pause