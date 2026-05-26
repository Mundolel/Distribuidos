#!/usr/bin/env bash
# Open firewall ports for a specific VM role. Requires sudo + ufw.
# Usage: bash deploy/firewall_open.sh --vm=1|2|3
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/env.sh"

VM_NUM=""
for arg in "$@"; do
    case "$arg" in
        --vm=*) VM_NUM="${arg#*=}" ;;
    esac
done

if [ -z "$VM_NUM" ]; then
    # Auto-detect by matching local IPs
    LOCAL_IPS="$(hostname -I 2>/dev/null || ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}')"
    if echo "$LOCAL_IPS" | grep -qw "$PC1_HOST"; then VM_NUM=1;
    elif echo "$LOCAL_IPS" | grep -qw "$PC2_HOST"; then VM_NUM=2;
    elif echo "$LOCAL_IPS" | grep -qw "$PC3_HOST"; then VM_NUM=3;
    else
        echo "Could not auto-detect VM role. Use: bash firewall_open.sh --vm=1|2|3"
        exit 1
    fi
fi

echo "=== Configuring firewall for VM${VM_NUM} ==="

sudo ufw allow 22/tcp comment "SSH"

case "$VM_NUM" in
    1)
        sudo ufw allow 5560/tcp comment "Broker PUB"
        ;;
    2)
        sudo ufw allow 5561/tcp comment "Analytics REP"
        ;;
    3)
        sudo ufw allow 5563/tcp comment "DB Primary PULL"
        sudo ufw allow 5565/tcp comment "Health Check REP"
        ;;
esac

sudo ufw --force enable
sudo ufw status numbered
echo "Done."
