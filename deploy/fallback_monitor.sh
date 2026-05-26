#!/usr/bin/env bash
# Run fallback monitoring CLI on VM2 (when PC3/VM3 is down).
# Usage: bash deploy/fallback_monitor.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/env.sh"

cd "$PROJECT_DIR"

echo "=== Fallback Monitoring (running on VM2) ==="
echo "PC3 is assumed DOWN — querying replica DB on this machine."
echo ""

exec python -m pc2.monitoring_fallback
