#!/bin/bash
# USB AI - Linux/macOS Launcher
# System python3 only — no venv activation, no embeddable.

echo "========================================"
echo " USB AI - Launcher"
echo "========================================"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM=$(uname -s)

if [ "$PLATFORM" != "Linux" ] && [ "$PLATFORM" != "linux" ] && [ "$PLATFORM" != "Darwin" ]; then
    echo "Unsupported platform: $PLATFORM"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/app/main.py" ]; then
    echo "[ERROR] app/main.py not found. Drive may be incomplete."
    exit 1
fi

PY=""
for candidate in python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c 'import sys; sys.exit(0)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] Python 3.11+ not found on PATH."
    exit 1
fi

echo "----------------------------------------"
echo " http://localhost:8080"
echo " Ctrl+C to stop"
echo "----------------------------------------"
echo

$PY "$SCRIPT_DIR/app/main.py" 2>&1 | tee "$SCRIPT_DIR/server.log"
