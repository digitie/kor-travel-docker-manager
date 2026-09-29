"""살아있는 PostgreSQL에서 PUBLIC에게 CONNECT를 주는 database를 찾는다.

## 왜 이 모듈이 필요한가

한 instance를 여러 테넌트가 나눠 쓰면 **database를 나누는 것만으로는 격리가 안 된다**
(onboarding §5.3). PostgreSQL은 새 database에 PUBLIC CONNECT·TEMP를 기본으로 준다 —
`datacl`이 NULL이면 그 기본값이다. 그래서 그 instance에 로그인할 수 있는 모든 role이 그 database에
붙어 catalog를 읽고 TEMP table을 만든다. 공용 instance(`:11000`)의 db-init은 전부 자기 DB의
PUBLIC CONNECT를 걷지만, **db-init을 거치지 않고 만들어진 DB는 아무도 걷지 않는다.**
2026-09-30 적대 리뷰(M4)가 n150 공용 instance에서 `pinvi`가 PUBLIC CONNECT를 가진 것을 실측했다 —
공용 Dagster role(`kor_travel_dagster_shared_app`)처럼 합류하는 모든 role이 거기 붙는다.

## 등급 — `warn`이지 차단이 아니다

CONNECT는 table 권한이 아니다 — 붙어도 PUBLIC에 GRANT되지 않은 table은 못 읽는다(격리 실행
테스트가 새 role로 그것을 확인한다). 그리고 걷는 것은 그 DB를 소유한 저장소의 일이다. 그래서
발견은 `warn`이다(`missing`은 `pinned_rebuild_preflight`가 재구축 blocker로 올린다 — 남의 DB 때문에
재구축을 막지 않는다). 관측하지 못한 것은 `postgres_hba_posture`와 같은 규칙으로 `unknown`이다 —
"확인 불가"는 "없다"가 아니다.

## 관측 경로

`postgres_hba_posture`와 같다 — 관리 대상 컨테이너 중 **지금 postgres 서버를 돌리는 것**에
컨테이너 안 unix socket으로 붙는다(무자격증명, 읽기 전용). 조회하는 것은 `pg_database`의
이름·ACL뿐이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from kor_travel_docker_manager.services.postgres_hba_posture import (
    _CONTAINER_NAME,
    _DECLARED_ROLE_SUFFIX,
    _ROLE_NAME,
    _discover_port,
    _env_map,
    _inspect,
    _live_runs_postgres_server,
    _psql,
    docker_daemon_reachable,
)
from kor_travel_docker_manager.services.registry import (
    MANAGED_CONTAINERS,
    external_project_for_container,
)

#: template이 아닌 database 중 PUBLIC(`grantee = 0`)이 CONNECT를 가진 것. `datacl`이 NULL이면
#: 기본 ACL(`acldefault('d', owner)` — PUBLIC CONNECT·TEMP)이므로 그것으로 펼친다. NULL을 빼먹으면
#: 한 번도 GRANT/REVOKE를 거치지 않은 DB — 가장 흔한 노출 — 가 검사에서 사라진다.
PUBLIC_CONNECT_SQL: Final = (
    "SELECT d.datname FROM pg_database AS d "
    "WHERE NOT d.datistemplate AND EXISTS ("
    "SELECT 1 FROM aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) AS a "
    "WHERE a.grantee = 0 AND a.privilege_type = 'CONNECT') "
    "ORDER BY 1"
)

PostureState = Literal["ok", "warn", "unknown"]


@dataclass(frozen=True)
class ConnectProbe:
    """한 instance에서 **관측한 것**. 판정은 담지 않는다."""

    container_id: str
    container_name: str
    external_project: str | None
    declared_postgres: bool
    live_postgres: bool | None
    public_connect_databases: tuple[str, ...] = ()
    unknown_reason: str | None = None


@dataclass(frozen=True)
class ConnectVerdict:
    state: PostureState
    detail: str
    evidence: Mapping[str, object] = field(default_factory=dict)


def _probe_one(container_id: str, spec: Mapping[str, object]) -> ConnectProbe:
    container_name = str(spec.get("name") or "")
    external = external_project_for_container(container_id)
    base = {
        "container_id": container_id,
        "container_name": container_name,
        "external_project": external.project if external is not None else None,
        "declared_postgres": str(spec.get("role") or "").endswith(_DECLARED_ROLE_SUFFIX),
    }
    if not _CONTAINER_NAME.match(container_name):
        return ConnectProbe(
            **base, live_postgres=None, unknown_reason="컨테이너 이름 형태가 예상과 다르다"
        )
    config = _inspect(container_name)
    if config is None:
        return ConnectProbe(
            **base,
            live_postgres=None,
            unknown_reason="컨테이너를 조회할 수 없다(부재이거나 daemon 접근 불가)",
        )
    if not _live_runs_postgres_server(config):
        return ConnectProbe(**base, live_postgres=False)
    role = _env_map(config).get("POSTGRES_USER", "").strip()
    port = _discover_port(config)
    if not _ROLE_NAME.match(role) or port is None:
        return ConnectProbe(
            **base,
            live_postgres=True,
            unknown_reason="POSTGRES_USER 또는 서버 포트를 라이브 설정에서 유도할 수 없다",
        )
    rows = _psql(container_name, role, port, PUBLIC_CONNECT_SQL)
    if rows is None:
        return ConnectProbe(
            **base,
            live_postgres=True,
            unknown_reason="pg_database를 조회할 수 없다(권한 또는 무응답)",
        )
    return ConnectProbe(
        **base,
        live_postgres=True,
        public_connect_databases=tuple(row[0] for row in rows if row and row[0]),
    )


def probe_instances() -> tuple[ConnectProbe, ...] | None:
    """관리 대상 컨테이너 전부를 관측한다. daemon 접근 불가면 ``None``."""

    if docker_daemon_reachable() is not True:
        return None
    return tuple(
        _probe_one(container_id, spec) for container_id, spec in MANAGED_CONTAINERS.items()
    )


def decide(probes: Sequence[ConnectProbe] | None) -> ConnectVerdict:
    """**순수 함수.** 발견은 `warn`, 관측 실패는 `unknown`, 둘 다 없으면 `ok`다."""

    if probes is None:
        return ConnectVerdict(
            state="unknown",
            detail="Docker daemon에 접근할 수 없어 database ACL을 확인하지 못했습니다.",
        )
    # 범위는 `postgres_hba_posture`와 같다 — 선언됐거나 라이브로 증명된 것. 선언과 라이브가
    # 어긋나는 것(선언은 postgres인데 서버가 아니다)은 그 검사가 소견으로 낸다.
    in_scope = [
        probe
        for probe in probes
        if (probe.declared_postgres or probe.live_postgres is True)
        and probe.live_postgres is not False
    ]
    unknown = [probe for probe in in_scope if probe.unknown_reason is not None]
    exposed = {
        probe.container_id: list(probe.public_connect_databases)
        for probe in in_scope
        if probe.unknown_reason is None and probe.public_connect_databases
    }
    checked = [probe.container_id for probe in in_scope if probe.unknown_reason is None]
    evidence: dict[str, object] = {
        "checked": checked,
        "public_connect": exposed,
        "unknown": {probe.container_id: probe.unknown_reason for probe in unknown},
    }
    listing = "; ".join(
        f"{container}: {', '.join(databases)}" for container, databases in exposed.items()
    )
    if unknown:
        detail = "일부 instance의 database ACL을 확인하지 못했습니다: " + ", ".join(
            probe.container_id for probe in unknown
        )
        if exposed:
            detail += f". 확인한 곳에서는 PUBLIC CONNECT가 있습니다 — {listing}"
        return ConnectVerdict(state="unknown", detail=detail, evidence=evidence)
    if not checked:
        return ConnectVerdict(
            state="unknown",
            detail="PostgreSQL로 판정된 관리 대상 컨테이너가 없습니다.",
            evidence=evidence,
        )
    if exposed:
        return ConnectVerdict(
            state="warn",
            detail=(
                "PUBLIC에게 CONNECT를 주는 database가 있습니다 — 그 instance에 로그인하는 모든 "
                "role(합류한 다른 테넌트 포함)이 붙어 catalog를 읽고 TEMP table을 만들 수 있습니다. "
                f"걷는 것은 그 DB를 소유한 저장소의 일입니다: {listing}"
            ),
            evidence=evidence,
        )
    return ConnectVerdict(
        state="ok",
        detail=f"{len(checked)}개 instance에 PUBLIC CONNECT를 주는 database가 없습니다.",
        evidence=evidence,
    )


def read_posture() -> ConnectVerdict:
    """관측 + 판정. **절대 예외를 던지지 않는다.**"""

    try:
        return decide(probe_instances())
    except Exception:  # noqa: BLE001 - 진단 패널이 500을 내면 볼 창을 잃는다
        return ConnectVerdict(
            state="unknown",
            detail="database ACL을 확인하는 중 예상하지 못한 오류가 발생했습니다.",
        )
