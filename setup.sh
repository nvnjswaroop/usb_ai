#!/bin/bash
# USB AI - Complete Setup for Linux/Mac

echo "============================================================"
echo " USB AI - Complete Setup"
echo "============================================================"
echo

# Check if running on Linux or macOS
PLATFORM=$(uname -s)
echo "Detected platform: $PLATFORM"

# Ensure we are in a virtual environment for portability
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment. Please ensure python3-venv is installed."
        exit 1
    fi
fi

echo "Activating environment..."
source venv/bin/activate

echo "Updating pip..."
pip install --upgrade pip --quiet

echo "Installing base packages..."
pip install fastapi "uvicorn[standard]" pydantic python-pptx python-multipart aiofiles pypdf pymupdf duckduckgo-search openai-whisper pillow --quiet

if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install base packages."
    exit 1
fi

# Download Whisper model
echo "Downloading Whisper base model..."
python3 -c "import whisper; whisper.load_model('base', download_root='whisper_models')"

echo
echo "============================================================"
echo " Setup complete! Run launch.sh to start the application."
echo "============================================================"
