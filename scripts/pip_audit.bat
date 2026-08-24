@echo off
setlocal enabledelayedexpansion

:: Run pip-audit, capture exit code + output
:: Note: pip-audit 2.10.1 does not support --fail-level, so we use output parsing.
:: Strategy: run pip-audit with raw exit-code as a first-pass signal, then
:: scan output for "Found N known vulnerabilities" message as fallback.

pip-audit -r requirements.txt -f columns 2>&1 > "%TEMP%\pip_audit_out.txt"
set "AUDIT_RC=%ERRORLEVEL%"

set "HAS_VULN=0"

:: pip-audit returns non-zero when findings exist; treat any non-zero as failure.
if not "%AUDIT_RC%"=="0" set "HAS_VULN=1"

:: Belt-and-braces: scan output for the word "Found" + numeric count.
findstr /C:"Found " "%TEMP%\pip_audit_out.txt" >nul 2>&1
if !ERRORLEVEL! EQU 0 set "HAS_VULN=1"

:: Strip the "Found 0 known vulnerabilities" line — that's the no-op pass message.
findstr /C:"Found 0 known vulnerabilities" "%TEMP%\pip_audit_out.txt" >nul 2>&1
if !ERRORLEVEL! EQU 0 set "HAS_VULN=0"

:: Surface a banner if anything tripped
if "%HAS_VULN%"=="1" (
    echo.
    echo ============================================================
    echo  pip-audit found vulnerabilities in requirements.txt
    echo  Bump vulnerable pins, then re-run this script.
    echo ============================================================
    type "%TEMP%\pip_audit_out.txt"
    exit /b 1
)

exit /b 0
