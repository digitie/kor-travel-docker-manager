#!/usr/bin/env sh
set -eu

log() {
  printf '[rustfs-init] %s\n' "$*"
}

# host 네트워크 모드 기본값: rustfs는 호스트 정규 포트(12101)에 직접 바인딩되므로 127.0.0.1로 접속한다.
endpoint="${RUSTFS_ENDPOINT:-http://127.0.0.1:${RUSTFS_API_CONTAINER_PORT:-12101}}"
access_key="${RUSTFS_ACCESS_KEY:-rustfsadmin}"
secret_key="${RUSTFS_SECRET_KEY:-rustfsadmin}"

retries="${RUSTFS_WAIT_RETRIES:-60}"
response=/tmp/rustfs-init.out

# minio/mc는 Docker Hub에서 사라졌다(2026-09-27) — rustfs 이미지에 든 curl의 SigV4로 버킷을 만든다.
# 접속 실패·5xx만 기동 대기로 보고 재시도한다(RustFS는 health가 200이 된 뒤에도 잠시 `503 waiting
# for storage_quorum`을 돌려준다). 인증·이름 같은 4xx는 바로 실패한다 — 이미 있는
# bucket은 200(RustFS) 또는 409 BucketAlreadyOwnedByYou(S3)로 멱등이다.
ensure_bucket() {
  i=0
  while :; do
    rm -f "$response"
    code=$(curl -sS --connect-timeout 5 --max-time 30 -o "$response" -w '%{http_code}' --aws-sigv4 aws:amz:us-east-1:s3 \
      --user "$access_key:$secret_key" -X PUT "$endpoint/$1") || code=000
    case "$code" in
      200) return 0 ;;
      409) grep -q BucketAlreadyOwnedByYou "$response" && return 0 ;;
      000|5??)
        i=$((i + 1))
        if [ "$i" -lt "$retries" ]; then
          sleep 2
          continue
        fi
        ;;
    esac
    echo "rustfs bucket $1: HTTP $code from $endpoint" >&2
    if [ -s "$response" ]; then cat "$response" >&2; echo >&2; fi
    return 1
  done
}

for bucket in \
  "${PINVI_RUSTFS_BUCKET:-pinvi-media}" \
  "${KOR_TRAVEL_GEO_RUSTFS_BUCKET:-kor-travel-geo}" \
  "${KOR_TRAVEL_CONCIERGE_RUSTFS_BUCKET:-kor-travel-concierge}" \
  "${KRTOUR_MAP_RUSTFS_BUCKET:-krtour-map}" \
  "${KRTOUR_MAP_OFFLINE_UPLOAD_BUCKET:-krtour-uploads}" \
  "${KOR_TRAVEL_TRANSPORT_RUSTFS_BUCKET:-kor-travel-transport-raw}"; do
  if [ -n "$bucket" ]; then
    log "ensuring bucket: $bucket"
    # 이미 존재하는 bucket만 멱등으로 허용한다. 인증·연결·권한 같은 실패를
    # `|| true`로 삼키면 init one-shot이 성공한 것처럼 보인다.
    ensure_bucket "$bucket"
  fi
done

log "bucket recovery complete"
