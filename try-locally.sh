#!/bin/sh
# Run the app on this Mac under Docker, for trying it before deploying to the NAS.
#
#   ./try-locally.sh                 # against the fake tablet (no heater needed)
#   ./try-locally.sh 192.168.1.115    # against the real wall tablet
#
# Then open http://localhost:8322
set -e
cd "$(dirname "$0")"

HOST="$1"

if [ -z "$HOST" ]; then
  echo "No tablet IP given — starting the fake tablet for a demo run."
  echo "(Pass the real IP to use your heating: ./try-locally.sh 192.168.1.115)"
  if ! curl -s -m 2 http://localhost:2025/getSystemData >/dev/null 2>&1; then
    PY=python3
    [ -x .venv/bin/python ] && PY=.venv/bin/python
    "$PY" probe/fake_tablet.py 2025 >/tmp/myplace-fake-tablet.log 2>&1 &
    sleep 2
  fi
  # Containers reach the Mac's own services via host.docker.internal.
  HOST=host.docker.internal
fi

GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%MZ)"
export GIT_SHA BUILD_TIME MYPLACE_HOST="$HOST"

echo "building myplace ${GIT_SHA}"
docker compose build
docker compose up -d
sleep 4

echo
if curl -s -m 5 http://localhost:8322/api/status >/dev/null 2>&1; then
  echo "Ready:  http://localhost:8322"
else
  echo "Container is up but the tablet did not answer. Check the IP, then:"
  echo "  docker compose logs"
fi
echo "Stop it with:  docker compose down"
