#!/bin/bash
# Check if virtual environment directory exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    # Pick the newest Python 3 interpreter available on this machine.
    PYTHON_BIN=""
    BEST_VER=0
    for cand in python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            ver=$("$cand" -c 'import sys; print(sys.version_info[0]*1000 + sys.version_info[1])' 2>/dev/null) || continue
            if [ -n "$ver" ] && [ "$ver" -gt "$BEST_VER" ]; then
                BEST_VER=$ver
                PYTHON_BIN=$cand
            fi
        fi
    done
    if [ -z "$PYTHON_BIN" ]; then
        echo "Error: no python3 interpreter found on PATH." >&2
        exit 1
    fi
    echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"
    "$PYTHON_BIN" -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Check if 'comfystudio' command is available
if ! command -v comfystudio &> /dev/null; then
    echo "Installing comfystudio..."
    pip install -e .
else
    echo "comfystudio is already installed."
fi

# Launch the application
comfystudio
