#!/usr/bin/env bash
# Start PC2 (Analytics + Semaphore Control + Replica DB) on VM2.
# Usage: bash deploy/start_vm2.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/env.sh"

export REPLICA_DB_PATH="$DATA_DIR/traffic_replica.db"

cd "$PROJECT_DIR"

echo "=== Starting PC2: Analytics, Control & Replica DB ==="
echo "VM2 IP        : $PC2_HOST"
echo "Replica DB    : $REPLICA_DB_PATH"
echo ""

exec python pc2/start_pc2.py
