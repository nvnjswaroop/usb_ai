#!/bin/bash
# USB AI - Update llama-cpp-python

LLAMA_CPP_VERSION="0.3.19"

echo "============================================================"
echo "USB AI - Update llama-cpp-python"
echo "============================================================"
echo

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    if [ -f "venv/Scripts/activate" ]; then
        source venv/Scripts/activate
    elif [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi
fi

echo "Current llama-cpp-python version:"
python -m pip show llama-cpp-python 2>/dev/null | grep Version || echo "Not installed"

echo "Updating llama-cpp-python to v$LLAMA_CPP_VERSION (CPU wheel)..."
python -m pip install "llama-cpp-python==$LLAMA_CPP_VERSION" \
    --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    --no-warn-script-location --quiet

if [ $? -eq 0 ]; then
    echo "llama-cpp-python updated successfully!"
    echo "New version:"
    python -m pip show llama-cpp-python 2>/dev/null | grep Version || echo "Installation successful"
else
    echo "[ERROR] Failed to update llama-cpp-python."
    echo "  Try manually: python -m pip install llama-cpp-python==$LLAMA_CPP_VERSION --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
    exit 1
fi
