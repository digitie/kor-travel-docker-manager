#!/usr/bin/env sh
set -eu

log() {
  printf '[rustfs-init] %s\n' "$*"
}

endpoint="${RUSTFS_ENDPOINT:-http://rustfs:${RUSTFS_API_CONTAINER_PORT:-9000}}"
access_key="${RUSTFS_ACCESS_KEY:-rustfsadmin}"
secret_key="${RUSTFS_SECRET_KEY:-rustfsadmin}"

i=0
while [ "$i" -lt "${RUSTFS_WAIT_RETRIES:-60}" ]; do
  if mc alias set local "$endpoint" "$access_key" "$secret_key" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$i" -ge "${RUSTFS_WAIT_RETRIES:-60}" ]; then
  echo "rustfs did not become ready in time: $endpoint" >&2
  exit 1
fi

for bucket in \
  "${TRIPMATE_RUSTFS_BUCKET:-tripmate-media}" \
  "${KRADDR_GEO_RUSTFS_BUCKET:-kraddr-geo}" \
  "${KRTOUR_MAP_RUSTFS_BUCKET:-krtour-map}" \
  "${KRTOUR_MAP_OFFLINE_UPLOAD_BUCKET:-krtour-uploads}"; do
  if [ -n "$bucket" ]; then
    log "ensuring bucket: $bucket"
    mc mb -p "local/$bucket" >/dev/null 2>&1 || true
  fi
done

log "bucket recovery complete"
