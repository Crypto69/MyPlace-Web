#!/bin/sh
# Rebuild and restart the container, stamping the image with the git commit.
# Usage, on the NAS:  ./deploy.sh
set -e
cd "$(dirname "$0")"

# The NAS keeps git off the default PATH for non-login shells; look in the
# usual places, and if all else fails read the commit straight from .git.
GIT=""
for c in git /usr/bin/git /usr/local/bin/git /opt/bin/git \
         /Volume1/@apps/git/bin/git; do
  if command -v "$c" >/dev/null 2>&1; then GIT="$c"; break; fi
done

if [ -n "$GIT" ]; then
  GIT_SHA="$("$GIT" rev-parse --short HEAD)"
  if [ -n "$("$GIT" status --porcelain --untracked-files=no)" ]; then
    GIT_SHA="${GIT_SHA}-dirty"
  fi
else
  ref="$(sed -n 's/^ref: //p' .git/HEAD)"
  if [ -n "$ref" ] && [ -f ".git/$ref" ]; then
    full="$(cat ".git/$ref")"
  elif [ -n "$ref" ] && [ -f .git/packed-refs ]; then
    full="$(grep " $ref\$" .git/packed-refs | cut -d' ' -f1)"
  else
    full="$(cat .git/HEAD)"          # detached HEAD: the file holds the sha
  fi
  GIT_SHA="$(printf '%s' "$full" | cut -c1-7)"
  [ -n "$GIT_SHA" ] || GIT_SHA=unknown
  echo "note: git not on PATH; commit read from .git (dirty check skipped)"
fi

BUILD_TIME="$(date -u +%Y-%m-%dT%H:%MZ)"
export GIT_SHA BUILD_TIME

# The tablet's IP lives in .env next to this script so it survives git pulls.
if [ -f .env ]; then
  echo "using MYPLACE_HOST from .env"
else
  echo "WARNING: no .env file — the app will start but cannot reach the tablet."
  echo "  Create one with:  echo 'MYPLACE_HOST=192.168.1.115' > .env"
fi

# --- pick a LAN port ------------------------------------------------------
# Prefer whatever this app is already published on, so a redeploy keeps the
# same URL. Otherwise take 8322, and if the box already has that, walk up
# until something is free. The chosen port is written to .env so it stays
# stable across future deploys - a heating app whose address moves is worse
# than useless.

port_in_use() {
  # Any container publishing this host port, plus anything else listening.
  if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q "[:.]$1->"; then
    return 0
  fi
  if command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 "$1" 2>/dev/null; then
    return 0
  fi
  return 1
}

# What is this app already using, if anything?
LAN_PORT="$(docker compose ps --format '{{.Publishers}}' 2>/dev/null \
  | tr ',' '\n' | sed -n 's/.*[:.]\([0-9]\{4,5\}\)->8000.*/\1/p' | head -1)"

# Or a port previously chosen and recorded.
if [ -z "$LAN_PORT" ]; then
  LAN_PORT="$(sed -n 's/^MYPLACE_PORT_LAN=//p' .env 2>/dev/null | tail -1)"
fi

# Otherwise find a free one, starting at the usual 8322.
if [ -z "$LAN_PORT" ]; then
  LAN_PORT=8322
  while port_in_use "$LAN_PORT"; do
    echo "port ${LAN_PORT} is taken, trying $((LAN_PORT + 1))"
    LAN_PORT=$((LAN_PORT + 1))
    if [ "$LAN_PORT" -gt 8400 ]; then
      echo "could not find a free port between 8322 and 8400" >&2
      exit 1
    fi
  done
fi

# Record it so this app keeps the same address on every future deploy.
if ! grep -q "^MYPLACE_PORT_LAN=${LAN_PORT}\$" .env 2>/dev/null; then
  [ -f .env ] && sed -i.bak '/^MYPLACE_PORT_LAN=/d' .env 2>/dev/null && rm -f .env.bak
  echo "MYPLACE_PORT_LAN=${LAN_PORT}" >> .env
fi
export MYPLACE_PORT_LAN="$LAN_PORT"

echo "building myplace ${GIT_SHA} (${BUILD_TIME}) on port ${LAN_PORT}"
docker compose build
docker compose up -d
sleep 3
echo "running: $(curl -s "http://localhost:${LAN_PORT}/api/version" || echo '(not up yet)')"
echo "open: http://big-kahuna-stor:${LAN_PORT}"
