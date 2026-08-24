@echo off
REM -------------------------------------------------------------------------
REM  USB AI -- Windows Setup (embeddable Python only)
REM
REM  Self-contained: downloads the official python.org embeddable zip into
REM  python\win\, bootstraps pip, and reinstalls every dep from scratch.
REM  No system Python, no PATH probe, no registry lookup, no winget.
REM
REM  Run as Administrator is NOT required -- only the local project folder is
REM  touched.
REM -------------------------------------------------------------------------
REM    ponytail: ASCII-only echoes (AGENTS.md rule). cmd.exe default codepage
REM    is fragile across Windows installs -- do NOT rely on chcp 65001 sticking
REM    through nested invocations. PowerShell blocks use $env:TEMP for the
REM    same reason -- %TEMP% in cmd strings gets eaten by MSYS bash shells.
setlocal EnableDelayedExpansion
REM    ponytail: forward any installer flags straight through, e.g.
REM      setup.bat --yes --cpu --voice
set "SETUP_ARGS=%*"

cd /d "%~dp0"
set "USB=%~dp0"
set "PY_DIR=%USB%python\win"
set "PY=%PY_DIR%\python.exe"
set "PTH=%PY_DIR%\python311._pth"
set "LLAMA_CPP_VERSION=0.3.19"

echo ============================================================
echo  USB AI - Windows Setup (embeddable Python)
echo ============================================================
echo.

REM -- 1/7 Terminate any running python.exe from this project ---------------
echo [1/7] Stopping any running python.exe from this project...
taskkill /F /IM python.exe /T >nul 2>&1
REM    ponytail: use PowerShell Start-Sleep instead of `timeout` -- the Windows
REM    timeout.exe conflicts with MSYS GNU timeout under bash invocation.
powershell -NoProfile -Command "Start-Sleep -Seconds 2"

REM -- 2/7 Wipe prior embeddable install (clean reinstall, not incremental) --
echo [2/7] Wiping prior install...
if exist "%PY_DIR%" (
    rd /s /q "%PY_DIR%" >nul 2>&1
    if exist "%PY_DIR%" (
        echo [ERROR] Could not delete %PY_DIR%. Close any running python.exe and retry.
        pause
        exit /b 1
    )
)
if not exist "%PY_DIR%" mkdir "%PY_DIR%"

REM -- 3/7 Download + extract python.org embeddable 3.11 -------------------
echo [3/7] Downloading Python 3.11.9 embeddable from python.org...
REM    ponytail: use $env:TEMP inside PowerShell (PowerShell-native expansion) so
REM    the temp path resolves correctly regardless of how the .bat was invoked.
REM    The original %TEMP% form breaks under MSYS/bash invocation because the
REM    shell expands it to a literal "%TEMP%" before PowerShell sees the arg.
powershell -NoProfile -Command "$tmp=Join-Path $env:TEMP 'py-embed-3.11.zip'; try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile $tmp -UseBasicParsing; Write-Output ('saved:'+$tmp) } catch { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Download failed. Check internet connection.
    pause
    exit /b 1
)
echo       Extracting...
REM    ponytail: capture the temp path in a var, reuse for cleanup -- never
REM    re-derive it from %TEMP% (same expansion trap as above).
powershell -NoProfile -Command "$tmp=Join-Path $env:TEMP 'py-embed-3.11.zip'; try { Expand-Archive -Path $tmp -DestinationPath '%PY_DIR%' -Force } catch { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Extraction failed.
    powershell -NoProfile -Command "Remove-Item -LiteralPath (Join-Path $env:TEMP 'py-embed-3.11.zip') -ErrorAction SilentlyContinue"
    pause
    exit /b 1
)
powershell -NoProfile -Command "Remove-Item -LiteralPath (Join-Path $env:TEMP 'py-embed-3.11.zip') -ErrorAction SilentlyContinue"

REM -- 4/7 Patch python311._pth -- uncomment `import site` -------------------
REM    ponytail: embeddable Python ships with `#import site` commented and
REM    site-packages disabled. Without this line `import fastapi` fails
REM    silently with ModuleNotFoundError despite pip reporting success.
echo [4/7] Patching python311._pth (enable site-packages)...
if not exist "%PTH%" (
    echo [ERROR] %PTH% not found after extraction. Zip may be corrupt.
    pause
    exit /b 1
)
powershell -NoProfile -Command "$p='%PTH%'; $c=Get-Content $p; if ($c -match '#import site') { ($c -replace '#import site','import site') | Set-Content $p }; if (Select-String -Path $p -Pattern '^import site' -Quiet) { 'import site OK' } else { Add-Content -Path $p -Value 'import site'; 'appended import site' }"
if errorlevel 1 (
    echo [ERROR] _pth patch failed.
    pause
    exit /b 1
)

REM -- 5/7 Bootstrap pip (embeddable ships without pip) ---------------------
echo [5/7] Bootstrapping pip...
"%PY%" -m ensurepip --upgrade >nul 2>&1
if errorlevel 1 (
    echo       ensurepip not bundled (normal for 3.11+ embeddable) -- downloading get-pip.py...
    REM    ponytail: $env:TEMP path computed inside PowerShell so MSYS bash
    REM    invocation doesn't break the OutFile path.
    powershell -NoProfile -Command "$tmp=Join-Path $env:TEMP 'get-pip.py'; try { Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $tmp -UseBasicParsing; Write-Output ('saved:'+$tmp) } catch { exit 1 }"
    if errorlevel 1 (
        echo [ERROR] Could not download get-pip.py.
        pause
        exit /b 1
    )
    REM    ponytail: same $env:TEMP lookup, single source of truth for the path.
    for /f "delims=" %%p in ('powershell -NoProfile -Command "Write-Output (Join-Path $env:TEMP 'get-pip.py')"') do set "GETPIP=%%p"
    "%PY%" "%GETPIP%" --no-warn-script-location >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] get-pip.py install failed.
        powershell -NoProfile -Command "Remove-Item -LiteralPath '%GETPIP%' -ErrorAction SilentlyContinue"
        pause
        exit /b 1
    )
    powershell -NoProfile -Command "Remove-Item -LiteralPath '%GETPIP%' -ErrorAction SilentlyContinue"
)
"%PY%" -m pip install --upgrade pip --no-warn-script-location >nul 2>&1
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip not available after bootstrap.
    pause
    exit /b 1
)
echo       pip ready.

REM -- 6/7 Hardware-driven dependency install -------------------------------
REM    ponytail: all package decisions live in scripts/install.py now --
REM    hardware detection, feature toggles (voice/ocr), verify + summary.
REM    The .bat keeps only what MUST be batch: acquiring Python itself.
echo [6/7] Running dependency installer...
"%PY%" "%USB%scripts\install.py" %SETUP_ARGS%
if errorlevel 1 (
    echo [ERROR] Installer failed. See output above.
    pause
    exit /b 1
)

REM -- 7/7 Done --------------------------------------------------------------
echo.
echo ============================================================
echo  Setup complete!
echo    Python : %PY%
echo    1. Drop a .gguf model into models\
echo    2. Run download_whisper.bat for voice input
echo    3. Run launch.bat
echo ============================================================
pause
