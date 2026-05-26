#!/usr/bin/env bash
# Pre-flight check: verify network connectivity between VMs.
# Run from any VM after sourcing env.sh.
# Usage: bash deploy/test_connectivity.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/env.sh"

PASS=0
FAIL=0

check_port() {
    local host="$1" port="$2" label="$3"
    if (echo >/dev/tcp/"$host"/"$port") 2>/dev/null; then
        echo "  [OK]   $label ($host:$port)"
        ((PASS++))
    elif command -v nc &>/dev/null && nc -z -w2 "$host" "$port" 2>/dev/null; then
        echo "  [OK]   $label ($host:$port)"
        ((PASS++))
    else
        echo "  [FAIL] $label ($host:$port)"
        ((FAIL++))
    fi
}

echo "=== Network Connectivity Test ==="
echo ""

echo "Ping tests:"
for vm_label in "VM1:$PC1_HOST" "VM2:$PC2_HOST" "VM3:$PC3_HOST"; do
    label="${vm_label%%:*}"
    ip="${vm_label##*:}"
    if ping -c1 -W2 "$ip" &>/dev/null; then
        echo "  [OK]   $label ($ip)"
        ((PASS++))
    else
        echo "  [FAIL] $label ($ip)"
        ((FAIL++))
    fi
done

echo ""
echo "Port tests (run AFTER starting services):"
check_port "$PC1_HOST" 5560 "VM1 broker PUB"
check_port "$PC2_HOST" 5561 "VM2 analytics REP"
check_port "$PC3_HOST" 5563 "VM3 DB primary PULL"
check_port "$PC3_HOST" 5565 "VM3 health check REP"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Some checks failed. Ensure services are running and firewall allows traffic."
    exit 1
fi
