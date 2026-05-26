#!/usr/bin/env bash
# Shared environment for all deploy scripts. Source this file, don't execute it.
# Usage: source deploy/env.sh

# === VM IPs ===
export PC1_HOST="10.43.98.238"
export PC2_HOST="10.43.100.96"
export PC3_HOST="10.43.99.5"

# === Paths ===
export PROJECT_DIR="$HOME/traffic-system/app"
export VENV_DIR="$HOME/traffic-system/venv"
export DATA_DIR="$HOME/traffic-system/data"

# === Python ===
export PYTHONPATH="$PROJECT_DIR"
export PYTHONUNBUFFERED=1

# Activate virtualenv
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
fi
