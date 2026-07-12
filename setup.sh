#!/bin/bash
# USB AI - Complete Setup for Linux/Mac
# Every step has explicit error checking and aborts on failure.

echo "============================================================"
echo " USB AI - Complete Setup"
echo "============================================================"
echo


LLAMA_CPP_VERSION="0.3.19"

# Note: we intentionally do NOT use `set -e` because we rely on explicit
# `if [ $? -ne 0 ]` checks after every install line for clear error messages.
# ── Python version check ───────────────────────────────────────────────────────
# llama-cpp-python wheels require cp311 (Python 3.11).
REQUIRED_PY_MAJOR=3
REQUIRED_PY_MINOR=11

PLATFORM=$(uname -s)
echo "Detected platform: $PLATFORM"

# Detect python3 or python
PY=""
for candidate in python3.11 python3 python python3.12 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        # Test the candidate actually executes (Windows Apps stubs may exit 49)
        if "$candidate" -c 'import sys; sys.exit(0)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "[ERROR] Python is not installed. Please install Python 3.11+."
    exit 1
fi
echo "Using Python interpreter: $PY"

PY_VERSION="$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION#*.}"
PY_MINOR="${PY_MINOR%%.*}"

if [ "$PY_MAJOR" -gt "$REQUIRED_PY_MAJOR" ] 2>/dev/null || \
   { [ "$PY_MAJOR" -eq "$REQUIRED_PY_MAJOR" ] 2>/dev/null && [ "$PY_MINOR" -ge "$REQUIRED_PY_MINOR" ] 2>/dev/null; }; then
    echo "Python $PY_VERSION detected — OK"
else
    echo "[ERROR] Python $REQUIRED_PY_MAJOR.$REQUIRED_PY_MINOR+ required (found $PY_VERSION)."
    echo "  llama-cpp-python CPU wheels are built for cp311."
    exit 1
fi

# ── Virtual environment ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PY -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        echo "  On Debian/Ubuntu: sudo apt install python3-venv"
        echo "  On macOS: brew install python@3.11"
        exit 1
    fi
fi

echo "Activating environment..."
# git-bash on Windows creates venv with Scripts/ instead of bin/.
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "[ERROR] Could not find venv activate script."
    echo "  Expected: venv/bin/activate (POSIX) or venv/Scripts/activate (Windows git-bash)"
    exit 1
fi
# Sanity check: python in PATH must be the venv python
if [ -z "$VIRTUAL_ENV" ]; then
    echo "[ERROR] Virtual environment did not activate properly."
    exit 1
fi
echo "  venv: $VIRTUAL_ENV"

# ── pip upgrade ────────────────────────────────────────────────────────────────
echo "Updating pip..."
python -m pip install --upgrade pip --quiet
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to upgrade pip."
    exit 1
fi

# ── Core + feature packages ─────────────────────────────────────────────────────
echo "Installing core packages..."
python -m pip install fastapi "uvicorn[standard]" pydantic python-multipart aiofiles \
            jinja2 numpy \
            pypdf pymupdf python-pptx pillow --quiet
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install core packages."
    exit 1
fi
echo "  Core packages done."

# ── llama-cpp-python (pinned CPU wheel) ────────────────────────────────────────
echo "Installing llama-cpp-python==$LLAMA_CPP_VERSION (CPU wheel)..."
# Prefer local wheel if ./wheels/*.whl exists (offline-install path)
LOCAL_WHL=""
for f in "$SCRIPT_DIR/wheels"/llama_cpp_python-"$LLAMA_CPP_VERSION"-cp311-win_amd64.whl \
         "$SCRIPT_DIR/wheels"/llama_cpp_python-"$LLAMA_CPP_VERSION"-cp311-cp311-linux_x86_64.whl; do
    if [ -f "$f" ]; then
        LOCAL_WHL="$f"
        break
    fi
done
if [ -n "$LOCAL_WHL" ]; then
    echo "  Using local wheel: $LOCAL_WHL"
    python -m pip install "$LOCAL_WHL" --no-index --no-warn-script-location --quiet
else
    python -m pip install "llama-cpp-python==$LLAMA_CPP_VERSION" \
        --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
        --no-warn-script-location --quiet
fi
if [ $? -ne 0 ]; then
    echo "[ERROR] llama-cpp-python CPU wheel install failed."
    echo "  This version requires Python 3.11 (cp311) and an AVX2-capable CPU."
    echo "  Try manually: python -m pip install llama-cpp-python==$LLAMA_CPP_VERSION --index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
    exit 1
fi
echo "  llama-cpp-python done."

# ── torch (explicit, CPU-only) ──────────────────────────────────────────────────
echo "Installing torch (CPU-only)..."
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu \
    --no-warn-script-location --quiet
if [ $? -ne 0 ]; then
    echo "[WARNING] torch unavailable (PyTorch CPU index). Voice features may be limited."
fi
echo "  torch done."

# ── Optional feature packages ──────────────────────────────────────────────────
echo "Installing openai-whisper..."
# Upgrade pip+setuptools first — whisper's build dependencies need setuptools>=61.2
# and the venv ships with an older version.
python -m pip install --upgrade pip setuptools wheel --quiet 2>/dev/null
python -m pip install openai-whisper --no-warn-script-location --quiet
if [ $? -ne 0 ]; then
    echo "[WARNING] openai-whisper unavailable. Voice input disabled."
fi
echo "  Feature packages done."

# ── Download Whisper model ─────────────────────────────────────────────────────
echo "Downloading Whisper base model..."
python -c "import whisper; whisper.load_model('base', download_root='whisper_models')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[WARNING] Whisper model download failed. Run download_whisper.sh manually."
fi

# ── Create folders ─────────────────────────────────────────────────────────────
for d in models history output prefeeds whisper_models; do
    if [ ! -d "$d" ]; then
        mkdir -p "$d"
    fi
done
if [ ! -f "prefeeds/system_prompt.txt" ]; then
    echo "You are a helpful AI assistant on a USB drive. Completely private. Be concise and honest." > prefeeds/system_prompt.txt
fi

echo
echo "============================================================"
echo " Setup complete! Run launch.sh to start the application."
echo "============================================================"
