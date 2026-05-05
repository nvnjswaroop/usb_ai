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

echo "Downloading '$CHOICE' model..."
# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 -c "import whisper; whisper.load_model('$CHOICE', download_root='whisper_models')"
if [ $? -eq 0 ]; then
    echo "Whisper '$CHOICE' model ready!"
else
    echo "Error downloading Whisper model"
    echo "Please check your internet connection and try again."
    exit 1
fi
