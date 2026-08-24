#!/bin/bash
# USB AI - Update LLM runtime (llama-server)
# Downloads the pinned official prebuilt binary. No compiling.
VARIANT="${1:-cpu}"

echo "============================================================"
echo "USB AI - Update llama-server runtime ($VARIANT)"
echo "============================================================"

PY=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null && "$candidate" -c 'import sys; sys.exit(0)' 2>/dev/null; then
        PY="$candidate"; break
    fi
done
if [ -z "$PY" ]; then echo "[ERROR] Run setup.sh first."; exit 1; fi

echo "Current binary:"
if [ -x "bin/llama/llama-server" ]; then
    bin/llama/llama-server --version 2>/dev/null | head -1 || true
else
    echo "  none installed yet"
fi

echo "Fetching pinned build..."
"$PY" scripts/fetch_llama.py --variant "$VARIANT" "${@:2}"
if [ $? -ne 0 ]; then
    echo "[ERROR] Update failed. See output above."
    exit 1
fi

echo "New binary:"
bin/llama/llama-server --version 2>/dev/null | head -1
echo "Done! Restart ./launch.sh to use it."
