#!/usr/bin/env bash
# setup-tx.sh — blackhole for order sending (bench-tx, bench-big, recv-*): dummy0 with 10.255.0.1/24 and permanent
# neighbours 10.255.0.2 (real orders) and 10.255.0.3 (warm orders, COLDPATH_WARM_SEND=2). Packets go through the
# whole kernel send path and are dropped by the dummy driver; nothing leaves the host. Not persistent; idempotent.

set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

# Loading the dummy module may create dummy0 itself, and then "ip link add" answers "File exists".
ip link show dummy0 >/dev/null 2>&1 || ip link add dummy0 type dummy 2>/dev/null || true
ip link show dummy0 >/dev/null
ip link set dummy0 up
ip addr replace 10.255.0.1/24 dev dummy0
ip neigh replace 10.255.0.2 lladdr 02:00:00:00:00:02 dev dummy0 nud permanent
ip neigh replace 10.255.0.3 lladdr 02:00:00:00:00:03 dev dummy0 nud permanent

for a in 10.255.0.2 10.255.0.3; do
  dev=$(ip -o route get "$a" | sed -n 's/.* dev \([^ ]*\).*/\1/p')
  [ "$dev" = dummy0 ] || { echo "!! route to $a goes via $dev, not dummy0"; exit 1; }
done

echo "dummy0 ready: orders to 10.255.0.2:9, warm-up orders to 10.255.0.3:9"
ip -s link show dummy0
