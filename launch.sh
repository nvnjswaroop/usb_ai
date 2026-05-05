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
    source venv/bin/activate
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

# Start the server
python3 "$SCRIPT_DIR/app/main.py" 2>&1 | tee "$SCRIPT_DIR/server.log"