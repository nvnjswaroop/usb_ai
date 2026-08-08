#!/bin/bash
# USB AI - Linux/macOS Setup
# Requires Python 3.11+ available on PATH (llama-cpp-python cp311 wheels).
# Embeddable zip is Windows-only; Linux/mac use the system interpreter.

echo "============================================================"
echo " USB AI - Setup (Linux/macOS)"
echo "============================================================"
echo

LLAMA_CPP_VERSION="0.3.19"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Python detection (system PATH only — no embeddable on POSIX) ────────────
PY=""
for candidate in python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        # Test the candidate actually executes (Windows Apps stubs may exit 49)
        if "$candidate" -c 'import sys; sys.exit(0)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "[ERROR] Python 3.11+ not found on PATH."
    echo "        Debian/Ubuntu: sudo apt install python3.11 python3.11-venv"
    echo "        macOS:         brew install python@3.11"
    exit 1
fi

PY_VERSION="$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION#*.}"
PY_MINOR="${PY_MINOR%%.*}"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "[ERROR] Python 3.11+ required (found $PY_VERSION)."
    exit 1
fi
echo "Using Python: $PY ($PY_VERSION)"

# ── pip upgrade ──────────────────────────────────────────────────────────────
echo "Updating pip..."
$PY -m pip install --upgrade pip --quiet 2>&1 | grep -v "^$" || true
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to upgrade pip."
    exit 1
fi

# ── Core + feature packages ──────────────────────────────────────────────────
echo "Installing requirements.txt..."
$PY -m pip install -r requirements.txt --no-warn-script-location --quiet
if [ $? -ne 0 ]; then
    echo "[ERROR] requirements.txt install failed."
    exit 1
fi
echo "  requirements.txt done."

# OCR is optional — only if requirements-ocr.txt exists.
if [ -f "requirements-ocr.txt" ]; then
    echo "Installing OCR (requirements-ocr.txt)..."
    $PY -m pip install -r requirements-ocr.txt --no-warn-script-location --quiet 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "[WARNING] OCR deps unavailable. PDF OCR disabled."
    fi
fi

# torch (CPU-only) — best-effort.
echo "Installing torch (CPU-only)..."
$PY -m pip install torch --index-url https://download.pytorch.org/whl/cpu \
    --no-warn-script-location --quiet 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[WARNING] torch unavailable. Voice features may be limited."
fi

# ── Create folders ───────────────────────────────────────────────────────────
for d in models history output prefeeds whisper_models; do
    if [ ! -d "$d" ]; then mkdir -p "$d"; fi
done
if [ ! -f "prefeeds/system_prompt.txt" ]; then
    echo "You are a helpful AI assistant on a USB drive. Completely private. Be concise and honest." > prefeeds/system_prompt.txt
fi

echo
echo "Running smoke test..."
$PY -c "import fastapi, pydantic, pymupdf, pptx, PIL, numpy; from app import main; assert main.app, 'app not loaded'; print('  app.main: OK')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[WARNING] Smoke test failed. Try ./launch.sh to see the error."
else
    echo "  Smoke test passed."
fi

echo
echo "============================================================"
echo " Setup complete! Run ./launch.sh to start."
echo "============================================================"
