@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
echo ============================================================
echo  USB AI - Complete Setup
echo ============================================================
set "USB=%~dp0"
set "WIN_DIR=%USB%python\win"
set "LLAMA_CPP_VERSION=0.3.19"

REM ── Resolve Python interpreter: system → winget → embeddable ─────────────
echo [1/7] Locating Python interpreter...

set "SYS_PY="
for /f "tokens=2" %%v in ('reg query "HKLM\SOFTWARE\Python\PythonCore\3.11\InstallPath" /ve 2^>nul ^| findstr /ri "REG_SZ"') do set "SYS_PY=%%vpython.exe"
if not defined SYS_PY for /f "tokens=2" %%v in ('reg query "HKCU\SOFTWARE\Python\PythonCore\3.11\InstallPath" /ve 2^>nul ^| findstr /ri "REG_SZ"') do set "SYS_PY=%%vpython.exe"
if defined SYS_PY if exist "!SYS_PY!" "!SYS_PY!" -c "import sys; sys.exit(0)" >nul 2>&1 && (
    set "PY=!SYS_PY!"
    echo       Using system Python 3.11: !PY!
    goto :py_ready
)
echo       No system Python 3.11 found.

where winget >nul 2>&1
if not errorlevel 1 (
    echo       Installing Python 3.11 via winget (1-2 minutes)...
    winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if not errorlevel 1 (
        set "SYS_PY="
        for /f "tokens=2" %%v in ('reg query "HKLM\SOFTWARE\Python\PythonCore\3.11\InstallPath" /ve 2^>nul ^| findstr /ri "REG_SZ"') do set "SYS_PY=%%vpython.exe"
        if defined SYS_PY if exist "!SYS_PY!" (
            set "PY=!SYS_PY!"
            echo       Using winget-installed Python: !PY!
            goto :py_ready
        )
        echo       winget reported success but registry lookup failed.
    ) else (
        echo       winget install failed.
    )
) else (
    echo       winget not available.
)

:try_embed
if exist "%WIN_DIR%" (
    echo       Cleaning previous embeddable install...
    taskkill /F /IM python.exe /T >nul 2>&1
    timeout /t 2 /nobreak >nul
    rd /s /q "%WIN_DIR%" >nul 2>&1
)
echo       Downloading Python 3.11.9 embeddable...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile '%TEMP%\py-embed.zip' -UseBasicParsing"
if errorlevel 1 (
    echo [ERROR] Download failed and no Python available.
    echo         Install Python 3.11 manually from https://python.org and re-run.
    pause
    exit /b 1
)
if not exist "%WIN_DIR%" mkdir "%WIN_DIR%"
powershell -Command "Expand-Archive -Path '%TEMP%\py-embed.zip' -DestinationPath '%WIN_DIR%' -Force"
if errorlevel 1 (
    echo [ERROR] Extraction failed.
    pause
    exit /b 1
)
del "%TEMP%\py-embed.zip" >nul 2>&1
set "PY=%WIN_DIR%\python.exe"
set "PTH=%WIN_DIR%\python311._pth"
if exist "%PTH%" powershell -Command "(Get-Content '%PTH%') -replace '#import site','import site' | Set-Content '%PTH%'"
echo       Embeddable Python ready (limited sys.path).

:py_ready
"%PY%" -c "import sys; print('       Python', sys.version.split()[0], '- OK')"

echo [2/7] Installing pip...
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
    del "%TEMP%\get-pip.py" >nul 2>&1
)
"%PY%" -m pip install --upgrade pip --no-warn-script-location --quiet
echo       pip ready.

echo [3/7] Installing base packages...
"%PY%" -m pip install fastapi "uvicorn[standard]" pydantic python-multipart aiofiles pypdf pymupdf python-pptx pillow jinja2 "numpy>=1.20" --no-warn-script-location --quiet
if errorlevel 1 (
    echo [ERROR] Base packages failed.
    pause
    exit /b 1
)
echo       Base packages done.

echo [4/7] Installing llama-cpp-python==!LLAMA_CPP_VERSION! (CPU wheel)...
REM Prefer local wheel if ./wheels/*.whl exists (offline-install path)
set "LOCAL_WHL="
for %%w in ("%USB%wheels\llama_cpp_python-!LLAMA_CPP_VERSION!-cp311-win_amd64.whl") do (
    if exist "%%~w" set "LOCAL_WHL=%%~w"
)
if defined LOCAL_WHL (
    echo       Using local wheel: !LOCAL_WHL!
    "%PY%" -m pip install "!LOCAL_WHL!" --no-index --no-warn-script-location --quiet
) else (
    "%PY%" -m pip install "llama-cpp-python==!LLAMA_CPP_VERSION!" --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --no-warn-script-location --quiet
)
if errorlevel 1 (
    echo [ERROR] llama-cpp-python wheel install failed.
    echo        Requires Python 3.11 (cp311) and AVX2-capable CPU (Intel Haswell 2013+ / AMD 2015+).
    echo        Try manually: pip install llama-cpp-python==!LLAMA_CPP_VERSION! --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
    pause
    exit /b 1
)
echo       llama-cpp-python done.

echo [5/7] Installing feature packages...
"%PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cpu --no-warn-script-location --quiet
if errorlevel 1 (
    echo [WARNING] torch CPU index failed. Trying default PyPI...
    "%PY%" -m pip install torch --no-warn-script-location --quiet
    if errorlevel 1 echo [WARNING] torch unavailable.
)

"%PY%" -m pip install openai-whisper --no-warn-script-location --quiet
if errorlevel 1 (
    echo [WARNING] openai-whisper unavailable. Voice input disabled.
) else (
    REM Pre-download the base model so /api/voice/transcribe works first-try
    echo       Pre-downloading whisper 'base' model...
    "%PY%" -c "import whisper; whisper.load_model('base', download_root='whisper_models')" 2>nul
    if errorlevel 1 echo [WARNING] whisper model download failed. Run download_whisper.bat later.
)
echo       Feature packages done.

echo [6/7] Saving launcher helper...
REM Write a small helper so launch.bat doesn't need to redo resolver logic.
> "%USB%py_path.txt" echo %PY%
echo       Saved PY path to py_path.txt

echo [7/7] Creating folders...
for %%d in (models history output prefeeds whisper_models) do (
    if not exist "%USB%%%d" mkdir "%USB%%%d"
)
if not exist "%USB%prefeeds\system_prompt.txt" (
    echo You are a helpful AI assistant on a USB drive. Completely private. Be concise and honest.> "%USB%prefeeds\system_prompt.txt"
)

REM ── Final verification: prove the wheel-installed llama-cpp imports and reports CPU baseline ──
echo.
echo Verifying llama-cpp-python install...
"%PY%" -c "import llama_cpp; v=llama_cpp.__version__ if hasattr(llama_cpp,'__version__') else '?'; info=llama_cpp.llama_print_system_info().decode(); req=['AVX2']; missing=[x for x in req if x not in info]; print('  version:', v); print('  CPU  :', info.strip()); print('  status:', 'OK' if not missing else 'MISSING: '+','.join(missing))"
if errorlevel 1 echo [WARNING] llama-cpp-python import/verify failed. Run launch.bat to test.

echo.
echo ============================================================
echo  Setup complete!
echo  1. Python: %PY%
echo  2. Drop a .gguf model into models\
echo  3. Run download_whisper.bat for voice input
echo  4. Run launch.bat
echo ============================================================
pause