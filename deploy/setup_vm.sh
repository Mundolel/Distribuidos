#!/usr/bin/env bash
# One-time VM provisioning. Run this once on each of the 3 VMs.
# Usage: bash deploy/setup_vm.sh [--repo-url <git-clone-url>]
set -euo pipefail

REPO_URL="${1:-}"
BASE_DIR="$HOME/traffic-system"
APP_DIR="$BASE_DIR/app"
VENV_DIR="$BASE_DIR/venv"
DATA_DIR="$BASE_DIR/data"

echo "=== Traffic System VM Setup ==="
echo "Base directory: $BASE_DIR"

# ---- 1. Install system packages (if sudo available) ----
if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    echo "[1/4] Installing system packages (sudo available)..."
    sudo apt-get update -qq
    # Try Python 3.11 from deadsnakes; fall back to system Python 3.10
    if ! command -v python3.11 &>/dev/null; then
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt-get update -qq
        sudo apt-get install -y python3.11 python3.11-venv git
    fi
else
    echo "[1/4] No sudo access — using system Python 3 ($(python3 --version 2>&1))"
fi

# Pick the best available Python
PYTHON=""
for candidate in python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: No python3 found. Install Python 3.10+ and retry."
    exit 1
fi
echo "Using Python: $PYTHON ($($PYTHON --version))"

# ---- 2. Create directory structure ----
echo "[2/4] Creating directories..."
mkdir -p "$APP_DIR" "$VENV_DIR" "$DATA_DIR"

# ---- 3. Clone or copy the repo ----
echo "[3/4] Setting up project code..."
if [ -n "$REPO_URL" ]; then
    if [ -d "$APP_DIR/.git" ]; then
        echo "Repo already cloned, pulling latest..."
        git -C "$APP_DIR" pull
    else
        rm -rf "$APP_DIR"
        git clone "$REPO_URL" "$APP_DIR"
    fi
else
    echo "No --repo-url provided."
    echo "Clone the repo manually into $APP_DIR, e.g.:"
    echo "  git clone <your-repo-url> $APP_DIR"
    if [ ! -f "$APP_DIR/requirements.txt" ]; then
        echo "WARNING: $APP_DIR/requirements.txt not found. Skipping venv setup."
        exit 0
    fi
fi

# ---- 4. Create virtualenv and install dependencies ----
echo "[4/4] Setting up Python virtualenv..."
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    $PYTHON -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$APP_DIR/requirements.txt" -q

echo ""
echo "=== Setup complete ==="
echo "Project dir : $APP_DIR"
echo "Virtualenv  : $VENV_DIR"
echo "Data dir    : $DATA_DIR"
echo "Python      : $(python --version)"
echo "pyzmq       : $(python -c 'import zmq; print(zmq.__version__)')"
echo ""
echo "Next: edit deploy/env.sh if IPs changed, then run the appropriate start script."
