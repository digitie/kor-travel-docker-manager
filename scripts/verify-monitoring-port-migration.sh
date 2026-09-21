#!/usr/bin/env bash
set -euo pipefail

# Prometheus/cAdvisor/Grafana 포트 변경 뒤에만 실행한다. 비밀값을 출력하지 않고,
# Compose가 오래된 환경변수 오버라이드를 실제로 사용하지 않는지 먼저 확인한다.

for expected in PROMETHEUS_PORT=12102 CADVISOR_PORT=12103 GRAFANA_PORT=12104; do
  name="${expected%%=*}"
  required="${expected#*=}"
  actual="${!name:-$required}"
  if [[ "$actual" != "$required" ]]; then
    echo "$name must be $required for this migration (effective value: $actual)" >&2
    exit 1
  fi
done

curl --fail --silent --show-error http://127.0.0.1:12102/-/ready >/dev/null
curl --fail --silent --show-error http://127.0.0.1:12103/healthz >/dev/null
curl --fail --silent --show-error http://127.0.0.1:12104/api/health >/dev/null

python3 - <<'PY'
import json
import urllib.parse
import urllib.request

query = urllib.parse.urlencode({"query": 'up{job=~"prometheus|cadvisor"}'})
with urllib.request.urlopen(f"http://127.0.0.1:12102/api/v1/query?{query}", timeout=10) as response:
    payload = json.load(response)

if payload.get("status") != "success":
    raise SystemExit("Prometheus query failed")

values = {item["metric"].get("job"): item["value"][1] for item in payload["data"]["result"]}
if values.get("prometheus") != "1" or values.get("cadvisor") != "1":
    raise SystemExit(f"Prometheus self/cAdvisor scrape is unhealthy: {values}")
PY

echo "Monitoring port migration verification passed."
