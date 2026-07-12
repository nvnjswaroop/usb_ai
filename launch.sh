#!/bin/bash
# USB AI - Launcher (Linux/Mac)

echo "========================================"
echo " USB AI - Launcher"
echo "========================================"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if running on Linux or macOS
PLATFORM=$(uname -s)

if [ "$PLATFORM" = "Linux" ] || [ "$PLATFORM" = "linux" ] || [ "$PLATFORM" = "Darwin" ]; then
    echo "Starting USB AI application..."
else
    echo "Unsupported platform: $PLATFORM"
    exit 1
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    if [ -f "venv/Scripts/activate" ]; then
        source venv/Scripts/activate
    elif [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi
fi

# Check if app/main.py exists
if [ ! -f "$SCRIPT_DIR/app/main.py" ]; then
    echo "[ERROR] app/main.py not found. Drive may be incomplete."
    exit 1
fi

echo
echo "----------------------------------------"
echo " http://localhost:8080"
echo " Ctrl+C to stop"
echo "----------------------------------------"
echo

# Detect python (python3 may be a Windows App alias stub)
PY=""
for candidate in python3.11 python3 python python3.12 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c 'import sys; sys.exit(0)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] Python not found."
    exit 1
fi

# Start the server
$PY "$SCRIPT_DIR/app/main.py" 2>&1 | tee "$SCRIPT_DIR/server.log"
