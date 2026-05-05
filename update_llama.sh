#!/bin/bash

# USB AI - Update llama-cpp-python

echo "============================================================"
echo "USB AI - Update llama-cpp-python"
echo "============================================================"
echo

echo "This script will update llama-cpp-python to the latest version."

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "Current llama-cpp-python version:"
pip show llama-cpp-python 2>/dev/null | grep Version || echo "Not installed"

echo "Updating llama-cpp-python..."
pip install --upgrade llama-cpp-python --quiet

if [ $? -eq 0 ]; then
    echo "llama-cpp-python updated successfully!"
    echo "New version:"
    pip show llama-cpp-python 2>/dev/null | grep Version || echo "Installation successful"
else
    echo "Failed to update llama-cpp-python"
    echo "Please check your internet connection and try again."
    exit 1
fi