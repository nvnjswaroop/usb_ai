#!/bin/bash
# USB AI - Linux/macOS Setup
# Finds a Python interpreter, then hands off to scripts/install.py which owns
# all dependency decisions (hardware variant, voice/ocr toggles).
# NOTE: until the llama-server sidecar lands (Phase B/C), the legacy in-process
# engine still needs Python 3.11 for its cp311 wheels.

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

# ── Hardware-driven dependency install ───────────────────────────────────────
# ponytail: all package decisions live in scripts/install.py -- hardware
# detection, feature toggles (voice/ocr), verify + summary. The shell script
# keeps only what must be shell: finding a Python interpreter.
echo "Running dependency installer..."
"$PY" scripts/install.py "$@"
if [ $? -ne 0 ]; then
    echo "[ERROR] Installer failed. See output above."
    exit 1
fi

echo
echo "============================================================"
echo " Setup complete! Run ./launch.sh to start."
echo "============================================================"
