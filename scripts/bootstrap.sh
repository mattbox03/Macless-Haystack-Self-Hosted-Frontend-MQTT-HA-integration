#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

mkdir -p data/google data/web

if [ ! -f .env ]; then
  if command -v openssl >/dev/null 2>&1; then
    TOKEN=$(openssl rand -hex 32)
  elif command -v python3 >/dev/null 2>&1; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  else
    echo "OpenSSL or Python 3 is required to generate GOOGLE_TOKEN." >&2
    exit 1
  fi

  cat > .env <<EOF
WEB_PORT=8125
TZ=UTC
GOOGLE_TOKEN=$TOKEN
GOOGLE_FIND_HUB_REF=main
RETENTION_DAYS=21
REFRESH_INTERVAL=1800
EOF
  echo "Created .env with a random Google sidecar token."
else
  echo "Existing .env kept unchanged."
fi

docker compose pull anisette macless-haystack
docker compose build find-my-web google-provider
docker compose up -d anisette

echo ""
echo "Bootstrap complete."
echo "Next: authenticate Apple with:"
echo "  docker compose run --rm macless-haystack"
echo ""
echo "Generate Google Auth/secrets.json on a desktop, then copy it to:"
echo "  $ROOT/data/google/secrets.json"
echo ""
echo "Finally start everything with:"
echo "  docker compose up -d"
