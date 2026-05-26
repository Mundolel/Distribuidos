#!/usr/bin/env bash
# Start PC3 (Primary DB + Monitoring CLI) on VM3.
# Usage: bash deploy/start_vm3.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/env.sh"

export PRIMARY_DB_PATH="$DATA_DIR/traffic_primary.db"

cd "$PROJECT_DIR"

echo "=== Starting PC3: Primary DB & Monitoring ==="
echo "VM3 IP      : $PC3_HOST"
echo "Primary DB  : $PRIMARY_DB_PATH"
echo ""

exec python pc3/start_pc3.py
