#!/usr/bin/env bash
# Start PC1 (Sensors + Broker) on VM1.
# Usage: bash deploy/start_vm1.sh
#   or:  BROKER_MODE=threaded SENSOR_COUNT=2 SENSOR_INTERVAL=5 bash deploy/start_vm1.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/env.sh"

cd "$PROJECT_DIR"

echo "=== Starting PC1: Sensors & Broker ==="
echo "VM1 IP     : $PC1_HOST"
echo "Broker mode: ${BROKER_MODE:-standard}"
echo "Sensor count: ${SENSOR_COUNT:-0 (all from config)}"
echo "Sensor interval: ${SENSOR_INTERVAL:-0 (from config)}"
echo ""

exec python pc1/start_pc1.py
