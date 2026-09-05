#!/usr/bin/env bash
# Find an Advantage Air (MyPlace/MyAir) wall tablet on the local network.
# The tablet runs an unauthenticated HTTP API on port 2025.
set -uo pipefail

PORT="${PORT:-2025}"
SUBNET="${1:-}"

if [[ -z "$SUBNET" ]]; then
  SUBNET=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
  SUBNET="${SUBNET%.*}"
fi

if [[ -z "$SUBNET" ]]; then
  echo "Could not detect subnet. Usage: $0 192.168.1" >&2
  exit 1
fi

echo "Scanning ${SUBNET}.1-254 on port ${PORT} for getSystemData ..."

for i in $(seq 1 254); do
  ip="${SUBNET}.${i}"
  (
    body=$(curl -s -m 2 "http://${ip}:${PORT}/getSystemData" 2>/dev/null)
    if [[ "$body" == *"aircons"* || "$body" == *"system"* ]]; then
      echo "FOUND: http://${ip}:${PORT}"
    fi
  ) &
  while (( $(jobs -rp | wc -l) >= 40 )); do wait -n 2>/dev/null || sleep 0.1; done
done
wait
echo "Scan complete."
