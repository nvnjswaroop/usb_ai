#!/bin/bash
# USB AI - Download Whisper Voice Model

echo "============================================================"
echo "USB AI - Download Whisper Voice Model"
echo "============================================================"
echo

USB_DIR=$(pwd)
MODEL_CHOICE="base"

echo "Available models:"
echo "  tiny  -  75MB  fastest"
echo "  base  - 145MB  recommended"
echo "  small - 480MB  most accurate"
echo

read -p "Choose model [tiny/base/small] (Enter=base): " CHOICE

# Convert to lowercase
CHOICE=$(echo "$CHOICE" | tr '[:upper:]' '[:lower:]')

if [ -z "$CHOICE" ]; then
    CHOICE="base"
fi

if [ "$CHOICE" != "tiny" ] && [ "$CHOICE" != "base" ] && [ "$CHOICE" != "small" ]; then
    echo "Invalid choice. Defaulting to 'base'."
    CHOICE="base"
fi

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

echo "Downloading '$CHOICE' model..."
# Activate virtual environment if it exists
if [ -d "venv" ]; then
    if [ -f "venv/Scripts/activate" ]; then
        source venv/Scripts/activate
    elif [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi
fi

$PY -c "import whisper; whisper.load_model('$CHOICE', download_root='whisper_models')"
if [ $? -eq 0 ]; then
    echo "Whisper '$CHOICE' model ready!"
else
    echo "Error downloading Whisper model"
    echo "Please check your internet connection and try again."
    exit 1
fi
