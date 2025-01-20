#!/bin/bash
# Check if virtual environment directory exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Check if 'comfystudio' command is available
if ! command -v comfystudio &> /dev/null; then
    echo "Installing comfystudio..."
    pip install .
else
    echo "comfystudio is already installed."
fi

# Launch the application
comfystudio
