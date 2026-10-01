#!/bin/sh
# READ-ONLY sampler for the VM (owner runs it; no root, no config change): 1 Hz TCP internals of the upload
# connection (rtt, retrans, cwnd, mss, bytes_received ...) so a failing/passing upload can be explained.
#   sh vm_ss_sampler.sh 8443 > ss_$(date +%Y%m%dT%H%M%S).log &
# NOT executed in the review sandbox (no iproute2 there): check one sample by hand before relying on it.
PORT="${1:-8443}"
while :; do
  printf '=== %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ss -tin state established "( sport = :$PORT )" 2>/dev/null | grep -E 'rtt:|retrans|bytes_|cwnd|mss' || echo "(no established connection)"
  sleep 1
done
