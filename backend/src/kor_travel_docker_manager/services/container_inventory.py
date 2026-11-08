"""실행 중인 미등록 컨테이너를 관측한다. 제어 registry에는 등록하지 않는다."""

import hashlib
from typing import Any

from docker.errors import DockerException


def observed_container_specs(client: Any, managed: Any) -> dict[str, dict[str, Any]]:
    known_names = {spec["name"] for spec in managed.values()}
    result = {}
    for container in client.containers.list(sparse=True):
        # sparse Docker 목록은 Name 대신 Names 배열을 준다. .name은 None일 수 있다.
        attrs = getattr(container, "attrs", {})
        names = attrs.get("Names") or []
        name = container.name or (str(names[0]).lstrip("/") if names else "")
        if not name:
            raise DockerException("Docker inventory entry has no container name")
        if name in known_names:
            continue
        # Metric.container_id의50자 제한과 재생성 후 같은 이름의 이력을 유지한다.
        key = "observed:" + hashlib.sha256(name.encode()).hexdigest()[:40]
        result[key] = {
            "name": name,
            "display_name": name,
            "role": "observed",
            "compose_service": "",
            "connection": "",
            "expected_ports": [],
            "observed_only": True,
        }
    return result
