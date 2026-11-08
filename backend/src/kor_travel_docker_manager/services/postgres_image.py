"""PostgreSQL 서버 이미지 가드(ADR-58 결정 9, T-MGR-03 리뷰) — 렌더된 이미지는 digest 핀이고 PGDATA와 같은 major다.

서버 이미지는 호스트별 root `.env`(`KTDM_POSTGRES_IMAGE`)로 바꿀 수 있다. 거기에 다른 major(`postgis/postgis:17-…`)나
floating tag가 들어가면 재생성된 서버가 기존 PGDATA를 거부하거나(major 불일치) 언젠가 다른 내용이 된다 — 공용 instance
하나가 죽으면 전 테넌트가 끊긴다. 그래서 `targets validate`(설치기 flip 전)와 `ensure` 프리플라이트가 같은 규칙 하나를 본다.

대상은 registry가 PostgreSQL이라고 **선언한** 서비스(`role`이 `postgresql`로 끝나는 Manager compose 컨테이너 — C6c 후보
검사의 declared 축과 같은 파생)다. 이름을 적지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

#: 공용 instance PGDATA의 major. 바꾸는 것은 `pg_upgrade`/dump-restore 절차이지 이미지 줄 하나가 아니다 — 그때 함께 올린다.
POSTGRES_MAJOR: Final = "18"
_DECLARED_ROLE_SUFFIX: Final = "postgresql"


def declared_postgres_services(config: Mapping[str, Any]) -> frozenset[str]:
    """registry가 PostgreSQL이라고 선언한 Manager compose 서비스(외부 project 제외)."""
    declared: set[str] = set()
    for spec in (config.get("containers") or {}).values():
        if not isinstance(spec, Mapping) or spec.get("external_project"):
            continue
        role = spec.get("role")
        service = spec.get("compose_service")
        if isinstance(role, str) and role.endswith(_DECLARED_ROLE_SUFFIX) and isinstance(service, str) and service:
            declared.add(service)
    return frozenset(declared)


def postgres_image_violations(services: Mapping[str, Any], declared: Iterable[str]) -> list[str]:
    """렌더된(보간된) `services`에서 선언된 PG 서버 이미지가 `…:<major>-…@sha256:<digest>`가 아니면 위반 문구."""
    violations: list[str] = []
    for name in sorted(set(declared)):
        service = services.get(name)
        if not isinstance(service, Mapping):
            continue
        image = str(service.get("image") or "")
        reference, _, digest = image.partition("@sha256:")
        tag = reference.rsplit("/", 1)[-1]
        if not digest or f":{POSTGRES_MAJOR}-" not in tag:
            violations.append(
                f"PostgreSQL server image must be digest-pinned major {POSTGRES_MAJOR} "
                f"(…:{POSTGRES_MAJOR}-…@sha256:…): {name} uses {image or '(none)'}"
            )
    return violations
