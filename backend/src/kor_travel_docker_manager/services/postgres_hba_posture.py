"""살아있는 PostgreSQL의 `pg_hba` 자세(posture)를 관측한다.

## 왜 이 모듈이 필요한가

2026-09-18에 적대 리뷰가 n150에서 실측했다. 여섯 PostgreSQL 인스턴스가 모두 이 행을
갖고 있었다:

    host    replication     all     127.0.0.1/32    trust
    host    replication     all     ::1/128         trust

각 컨테이너 안에서 빈 `PGPASSWORD`로 `IDENTIFY_SYSTEM`이 **응답했다**.
`pg_basebackup` 한 줄로 클러스터 전체 — 모든 DB와 `pg_authid`의 롤 해시 — 가 복사된다.
네 대가 `network_mode: host`라 **호스트 loopback에 붙는 모든 컨테이너**가 자격증명
없이 닿았다.

원인은 `POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256`이 **initdb 시점에만** 적용된다는
것이다. prod PGDATA는 그 값이 계약에 들어오기 전에 만들어졌다. 즉 **compose 문서 검사와
live 상태 검사는 다른 축**이고, 저장소에 후자가 **0건**이었다(`rg pg_hba backend/src` →
주석뿐).

이 모듈이 그 축이다.

## 모양 — probe / decide 2분할

`probe_*`가 I/O를 하고(실패는 예외가 아니라 `unknown` 상태값), `decide`는 시계·
파일시스템·네트워크가 없는 **순수 함수**다. 그러면 판정을 형상으로 직접 태울 수 있고
관측 경로의 timeout·권한 문제와 섞이지 않는다.

## 세 가지 상태 — "확인 불가"는 "안전"이 아니다

`admin_password_service`가 이 규칙의 정본이다: 통과 / **증명된 위반** / `unknown`.
관측면은 절대 예외를 던지지 않고, 판단 근거가 하나라도 없으면 초록불을 켜지 않는다
(`docs/dashboard-ui.md`: "판정 근거를 하나라도 잃으면 초록불을 켜지 않는다").

## 왜 `type <> 'local'`인가 — `type = 'host'`는 **틀린 술어**다

PostgreSQL은 `pg_hba_file_rules.type`에 **작성된 키워드를 그대로** 싣는다. `hostssl`·
`hostnossl`·`hostgssenc`·`hostnogssenc`도 전부 TCP다. `type = 'host'`로 쓰면
`hostnossl all all 0.0.0.0/0 trust` 한 줄이 통과한다. 지금 여섯 인스턴스에는 `host`와
`local`만 있어서 **오늘은 차이가 없다 — 그래서 더 위험하다.**

`local ... trust`는 **건드리지 않는다.** 컨테이너 내부 unix socket/peer 접속이고
entrypoint·healthcheck·db-init one-shot이 전부 그것에 의존한다. 이 모듈 자신도 그것으로
붙는다.

## 자격증명을 읽지 않는다

컨테이너 안 unix socket 접속이 `local all all trust`에 매칭되므로 무자격증명으로
superuser다(n150 여섯 인스턴스 전부 실측). `standalone_backup`이 같은 이유로 같은 경로를
쓴다 — "이 모듈은 어떤 postgres 비밀번호도 읽거나 다루지 않는다".

`$POSTGRES_PASSWORD_FILE`을 읽는 것은 **오히려 금지**다: 내부 넷에만 있고 외부 둘에는
없으며, `validate_*_postgres_runtime_secret_isolation`이 실행 중 postgres가
`POSTGRES_PASSWORD`를 Env에 갖는 것 자체를 거부하고, 평문이 프로세스 목록·subprocess
캡처·오류 문구에 남는다.

조회하는 컬럼에는 비밀번호도 해시도 없다. `pg_authid`는 보지 않는다.

## 파일과 로드된 설정의 간극

`pg_hba_file_rules`는 **파일**을 파싱한 결과이고 로드된 설정이 아니다. PG16에 활성 HBA를
보는 뷰는 없으므로 시각으로 관측한다 — `pg_stat_file(hba_file).modification >
pg_conf_load_time()`이면 파일과 살아있는 동작이 갈렸다는 뜻이므로 `ok`가 아니라
`unknown`이다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from kor_travel_docker_manager.services.registry import (
    MANAGED_CONTAINERS,
    external_project_for_container,
)

#: 컨테이너 이름·role 이름은 argv에 들어가기 전에 형태를 확인한다.
#: `standalone_backup`의 같은 규칙을 따른다 — argv 리스트라 셸 주입은 없지만, 이상한
#: 값이 들어오면 `docker exec`가 엉뚱한 대상을 잡는 것이 더 위험하다.
_CONTAINER_NAME: Final = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_ROLE_NAME: Final = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")

#: 이 서버를 **실행하는** 컨테이너인지 판정하는 라이브 신호. 정본 compose의 넷은
#: `Cmd[0] == "postgres"`이고, 외부 둘(`weather-db`·`airport-db`)은 `Cmd`가 정확히
#: `["postgres"]`다(실측). one-shot은 `Cmd[0] == "sh"`다(`concierge-db-init`).
_POSTGRES_SERVER_COMMAND: Final = "postgres"
#: official 이미지의 entrypoint. 이것이 아니면 서버 기동 경로가 다르다.
_POSTGRES_ENTRYPOINT: Final = ("docker-entrypoint.sh",)
#: `role`이 이것으로 끝나는 컨테이너 선언이 "PostgreSQL이다"라고 말한다.
#: **게이팅 근거로 쓰지 않는다** — 라이브 판정과 **대조**해서 불일치를 소견으로 낸다.
_DECLARED_ROLE_SUFFIX: Final = "postgresql"

#: `local`은 컨테이너 내부 소켓/peer 접속이다. 이 모듈 자신이 그것으로 붙고,
#: entrypoint·healthcheck·db-init이 전부 의존한다 — **판정 대상이 아니다.**
_LOCAL_CONNECTION_TYPE: Final = "local"
_TRUST_AUTH_METHOD: Final = "trust"

_FIELD_SEPARATOR: Final = "|"
_MISSING: Final = "-"

_RULES_SQL: Final = (
    "SELECT coalesce(rule_number::text,'-'), coalesce(file_name,'-'), "
    "coalesce(line_number::text,'-'), coalesce(type,'-'), "
    "coalesce(database::text,'-'), coalesce(user_name::text,'-'), "
    "coalesce(address,'-'), coalesce(auth_method,'-'), coalesce(error,'-') "
    "FROM pg_hba_file_rules ORDER BY rule_number"
)
_LOAD_SQL: Final = (
    "SELECT current_setting('hba_file'), "
    "to_char(pg_conf_load_time(),'YYYY-MM-DD\"T\"HH24:MI:SSOF'), "
    "to_char((pg_stat_file(current_setting('hba_file'))).modification,"
    "'YYYY-MM-DD\"T\"HH24:MI:SSOF'), "
    "((pg_stat_file(current_setting('hba_file'))).modification "
    "> pg_conf_load_time())::text"
)

#: 관측은 UI가 폴링하는 route에서 돈다 — timeout 없는 자식 프로세스 하나가 anyio
#: worker를 무한히 문다. `deployment_readiness._run_read_only`가 같은 이유로 timeout을
#: 필수로 둔다.
_DOCKER_TIMEOUT_SECONDS: Final = 8.0
_PSQL_TIMEOUT_SECONDS: Final = 12.0

InstanceState = Literal["ok", "violation", "unknown"]


@dataclass(frozen=True)
class HbaRule:
    """`pg_hba_file_rules` 한 행. 비밀번호도 해시도 담기지 않는 컬럼들이다."""

    line_number: str
    connection_type: str
    database: str
    user_name: str
    address: str
    auth_method: str
    error: str

    @property
    def is_local(self) -> bool:
        return self.connection_type == _LOCAL_CONNECTION_TYPE

    @property
    def grants_trust_over_tcp(self) -> bool:
        """TCP 경로에 `trust`를 주는가.

        **`type == "host"`로 쓰지 않는다.** PostgreSQL은 작성된 키워드를 그대로 싣고,
        `hostssl`·`hostnossl`·`hostgssenc`·`hostnogssenc`도 전부 TCP다.
        """

        return not self.is_local and self.auth_method == _TRUST_AUTH_METHOD

    def to_evidence(self) -> dict[str, str]:
        """증거로 실을 값. 주소·인증방식까지만 — 비밀은 애초에 여기 없다."""

        return {
            "line": self.line_number,
            "type": self.connection_type,
            "database": self.database,
            "user": self.user_name,
            "address": self.address,
            "auth_method": self.auth_method,
        }


@dataclass(frozen=True)
class InstanceProbe:
    """한 인스턴스에서 **관측한 것**. 판정은 담지 않는다."""

    container_id: str
    container_name: str
    external_project: str | None
    declared_postgres: bool
    live_postgres: bool | None
    rules: tuple[HbaRule, ...] = ()
    hba_file: str | None = None
    conf_load_time: str | None = None
    hba_modified_at: str | None = None
    file_newer_than_reload: bool | None = None
    unknown_reason: str | None = None

    @property
    def manager_owned(self) -> bool:
        return self.external_project is None


@dataclass(frozen=True)
class InstanceVerdict:
    state: InstanceState
    container_id: str
    container_name: str
    external_project: str | None
    detail: str
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PostureVerdict:
    """전체 판정. `deployment_readiness`가 이것을 행 하나로 옮긴다."""

    state: Literal["ok", "missing", "warn", "unknown"]
    detail: str
    instances: tuple[InstanceVerdict, ...]
    evidence: Mapping[str, object] = field(default_factory=dict)


def _child_environment() -> dict[str, str]:
    """자식 프로세스에 넘길 최소 환경. 호출자의 env를 그대로 물려주지 않는다."""

    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    for name in ("DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR"):
        value = os.environ.get(name, "").strip()
        if value:
            environment[name] = value
    return environment


def _read_only_text(command: Sequence[str], *, timeout: float) -> str | None:
    """프로세스를 띄우는 유일한 지점. 실패·타임아웃·비정상 종료는 모두 ``None``이다.

    `deployment_readiness._run_read_only`를 그대로 쓰지 않는 이유는 하나다 — 그쪽은
    stdout을 `DEVNULL`로 버린다. timeout 규약과 최소 환경은 그대로 따른다.
    """

    try:
        completed = subprocess.run(
            list(command),
            cwd="/",
            env=_child_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def docker_daemon_reachable() -> bool | None:
    """daemon 접근 가능 여부. 이 게이트는 load-bearing이다.

    `docker exec`는 "컨테이너가 없다"와 "daemon에 접근할 수 없다"를 같은 비정상
    종료로 알린다. 게이트가 없으면 비-root backend가 **거짓 차단**을 보고한다 —
    `deployment_readiness._docker_daemon_reachable`이 같은 이유로 존재한다.
    """

    text = _read_only_text(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )
    if text is None:
        return None
    return bool(text.strip())


def _inspect(container_name: str) -> Mapping[str, object] | None:
    text = _read_only_text(
        ["docker", "inspect", "--format", "{{json .Config}}", container_name],
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _env_map(config: Mapping[str, object]) -> dict[str, str]:
    raw = config.get("Env")
    values: dict[str, str] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, str):
                continue
            name, separator, value = entry.partition("=")
            if separator:
                values[name] = value
    return values


def _live_runs_postgres_server(config: Mapping[str, object]) -> bool:
    """이 컨테이너가 **지금 postgres 서버를 돌리는가.**

    이름을 열거하지 않는다. `Cmd[0]`과 `Entrypoint`로 증명한다 — 실측으로 이 술어가
    정확히 여섯을 고르고 one-shot을 배제한다(`concierge-db-init`은 같은 postgis
    이미지지만 `Cmd[0] == "sh"`다).
    """

    command = config.get("Cmd")
    if not isinstance(command, list) or not command:
        return False
    if not isinstance(command[0], str) or command[0] != _POSTGRES_SERVER_COMMAND:
        return False
    entrypoint = config.get("Entrypoint")
    if isinstance(entrypoint, list):
        return tuple(entrypoint) == _POSTGRES_ENTRYPOINT
    return False


def _discover_port(config: Mapping[str, object]) -> str | None:
    """서버가 듣는 포트. **세 단계로 유도한다.**

    `standalone_backup._discover_port`를 재사용하지 **않는다**: 그 함수는 `-p` 토큰만
    보고 없으면 예외를 던지는데, 실측으로 `weather-db`·`airport-db`의 `Cmd`는 정확히
    `["postgres"]`다 — 재사용하면 **지금 문제가 있는 두 인스턴스가 검사에서 사라진다**
    (소견이 아니라 하드 에러가 된다).

    `--port`는 소켓 접속에도 필수다. unix socket 파일 이름이 `.s.PGSQL.<port>`라서
    포트가 틀리면 소켓 자체를 못 찾는다.
    """

    command = config.get("Cmd")
    if isinstance(command, list):
        for index, token in enumerate(command):
            if token == "-p" and index + 1 < len(command):
                candidate = command[index + 1]
                if isinstance(candidate, str) and candidate.isdigit():
                    return candidate
    environment = _env_map(config)
    pgport = environment.get("PGPORT", "").strip()
    if pgport.isdigit():
        return pgport
    exposed = config.get("ExposedPorts")
    if isinstance(exposed, Mapping):
        for key in exposed:
            if isinstance(key, str) and key.endswith("/tcp"):
                candidate = key.split("/", 1)[0]
                if candidate.isdigit():
                    return candidate
    return None


def _psql(container_name: str, role: str, port: str, sql: str) -> list[list[str]] | None:
    """컨테이너 안 unix socket으로 읽기 전용 질의. **자격증명을 쓰지 않는다.**

    `--no-psqlrc`가 없으면 `~/.psqlrc`가 출력을 오염시킨다.
    """

    text = _read_only_text(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            container_name,
            "psql",
            "--username",
            role,
            "--port",
            port,
            "--dbname",
            "postgres",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--field-separator",
            _FIELD_SEPARATOR,
            "--command",
            sql,
        ],
        timeout=_PSQL_TIMEOUT_SECONDS,
    )
    if text is None:
        return None
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rows.append(line.split(_FIELD_SEPARATOR))
    return rows


def _probe_one(container_id: str, spec: Mapping[str, object]) -> InstanceProbe:
    container_name = str(spec.get("name") or "")
    role_declaration = str(spec.get("role") or "")
    external = external_project_for_container(container_id)
    declared = role_declaration.endswith(_DECLARED_ROLE_SUFFIX)
    base = {
        "container_id": container_id,
        "container_name": container_name,
        "external_project": external.project if external is not None else None,
        "declared_postgres": declared,
    }
    if not _CONTAINER_NAME.match(container_name):
        return InstanceProbe(
            **base, live_postgres=None, unknown_reason="컨테이너 이름 형태가 예상과 다르다"
        )
    config = _inspect(container_name)
    if config is None:
        return InstanceProbe(
            **base,
            live_postgres=None,
            unknown_reason="컨테이너를 조회할 수 없다(부재이거나 daemon 접근 불가)",
        )
    live = _live_runs_postgres_server(config)
    if not live:
        return InstanceProbe(**base, live_postgres=False)
    environment = _env_map(config)
    role = environment.get("POSTGRES_USER", "").strip()
    if not _ROLE_NAME.match(role):
        # 하드코딩 `postgres`는 여섯 전부 실패한다 — 실측 admin role이 모두 다르다.
        return InstanceProbe(
            **base,
            live_postgres=True,
            unknown_reason="POSTGRES_USER를 라이브 env에서 읽을 수 없다",
        )
    port = _discover_port(config)
    if port is None:
        return InstanceProbe(
            **base, live_postgres=True, unknown_reason="서버 포트를 유도할 수 없다"
        )
    rows = _psql(container_name, role, port, _RULES_SQL)
    if rows is None:
        return InstanceProbe(
            **base,
            live_postgres=True,
            unknown_reason="pg_hba_file_rules를 조회할 수 없다(권한 또는 무응답)",
        )
    rules = tuple(
        HbaRule(
            line_number=row[2] if len(row) > 2 else _MISSING,
            connection_type=row[3] if len(row) > 3 else _MISSING,
            database=row[4] if len(row) > 4 else _MISSING,
            user_name=row[5] if len(row) > 5 else _MISSING,
            address=row[6] if len(row) > 6 else _MISSING,
            auth_method=row[7] if len(row) > 7 else _MISSING,
            error=row[8] if len(row) > 8 else _MISSING,
        )
        for row in rows
        if len(row) >= 4
    )
    load_rows = _psql(container_name, role, port, _LOAD_SQL)
    hba_file = conf_load_time = hba_modified_at = None
    file_newer: bool | None = None
    if load_rows and len(load_rows[0]) >= 4:
        hba_file, conf_load_time, hba_modified_at, newer = load_rows[0][:4]
        file_newer = newer.strip().lower() in {"t", "true"}
    return InstanceProbe(
        **base,
        live_postgres=True,
        rules=rules,
        hba_file=hba_file,
        conf_load_time=conf_load_time,
        hba_modified_at=hba_modified_at,
        file_newer_than_reload=file_newer,
    )


def probe_instances() -> tuple[InstanceProbe, ...] | None:
    """관리 대상 컨테이너 전부를 관측한다. daemon 접근 불가면 ``None``.

    후보 집합이 `MANAGED_CONTAINERS`인 것이 중요하다 — `docker ps` 전수 스캔으로
    식별하면 compose 라벨 없이 `docker run`으로 뜬 유령 postgres까지 잡아 거짓 소견을
    낸다(실측: `ktm-live-301-pg`가 그런 컨테이너다).
    """

    if docker_daemon_reachable() is not True:
        return None
    return tuple(
        _probe_one(container_id, spec) for container_id, spec in MANAGED_CONTAINERS.items()
    )


def decide(probes: Sequence[InstanceProbe] | None) -> PostureVerdict:
    """**순수 함수.** 시계·파일시스템·네트워크가 없다.

    등급을 소유권으로 나눈다. Manager 소유 인스턴스의 위반은 차단(`missing`)이지만,
    외부 프로젝트 인스턴스는 최대 `warn`이다 — Manager는 남의 compose를 고칠 수단이
    없고(`ensure_target`이 외부 target을 거부한다) 조치 주체가 그 저장소다. `missing`은
    `pinned_rebuild_preflight`가 재구축 blocker로 승격시키므로, 그것을 켜면 남의 DB
    때문에 Map 재구축이 막힌다. "차단이 아닌 것을 차단으로 만들지 않는다."
    """

    if probes is None:
        return PostureVerdict(
            state="unknown",
            detail="Docker daemon에 접근할 수 없어 살아있는 pg_hba를 확인하지 못했습니다.",
            instances=(),
        )

    verdicts: list[InstanceVerdict] = []
    for probe in probes:
        # **범위는 "선언됐거나 라이브로 증명된 것"이다.** 첫 판은 `live_postgres is
        # False`만 걸렀는데, 조회 실패는 `None`이라 **비-postgres 컨테이너 스무 개가
        # 전부 `unknown`으로 올라와** 전체를 `unknown`으로 만들었다(실측). 선언되지도
        # 않고 postgres로 증명되지도 않은 컨테이너는 이 검사의 대상이 아니다.
        if not (probe.declared_postgres or probe.live_postgres is True):
            continue
        verdicts.append(_decide_one(probe))

    if not verdicts:
        return PostureVerdict(
            state="unknown",
            detail="PostgreSQL로 판정된 관리 대상 컨테이너가 없습니다.",
            instances=(),
            evidence={"instances": 0},
        )

    blocking = [v for v in verdicts if v.state == "violation" and v.external_project is None]
    external_violations = [
        v for v in verdicts if v.state == "violation" and v.external_project is not None
    ]
    unknown = [v for v in verdicts if v.state == "unknown"]
    evidence: dict[str, object] = {
        "instances": len(verdicts),
        "violations": [v.container_id for v in blocking + external_violations],
        "unknown": [v.container_id for v in unknown],
        "checked": [v.container_id for v in verdicts],
    }
    if blocking:
        return PostureVerdict(
            state="missing",
            detail=(
                "살아있는 pg_hba가 TCP 경로에 trust를 허용합니다 — 비밀번호 없이 "
                f"접속·복제가 가능합니다: {', '.join(v.container_id for v in blocking)}"
            ),
            instances=tuple(verdicts),
            evidence=evidence,
        )
    if unknown:
        return PostureVerdict(
            state="unknown",
            detail=(
                "일부 인스턴스의 살아있는 pg_hba를 확인하지 못했습니다: "
                f"{', '.join(v.container_id for v in unknown)}"
            ),
            instances=tuple(verdicts),
            evidence=evidence,
        )
    if external_violations:
        return PostureVerdict(
            state="warn",
            detail=(
                "다른 프로젝트가 소유한 인스턴스가 TCP 경로에 trust를 허용합니다 — "
                "조치는 그 저장소가 합니다: "
                f"{', '.join(v.container_id for v in external_violations)}"
            ),
            instances=tuple(verdicts),
            evidence=evidence,
        )
    return PostureVerdict(
        state="ok",
        detail=(
            f"{len(verdicts)}개 PostgreSQL 인스턴스의 살아있는 pg_hba에 TCP trust 규칙이 "
            "없습니다."
        ),
        instances=tuple(verdicts),
        evidence=evidence,
    )


def _decide_one(probe: InstanceProbe) -> InstanceVerdict:
    base = {
        "container_id": probe.container_id,
        "container_name": probe.container_name,
        "external_project": probe.external_project,
    }
    if probe.unknown_reason is not None:
        return InstanceVerdict(state="unknown", detail=probe.unknown_reason, **base)
    if probe.declared_postgres and probe.live_postgres is False:
        # `role`은 UI 문자열이고 아무것도 이것을 강제하지 않는다. 선언과 라이브가
        # 어긋나면 **그 불일치 자체가 소견**이다 — 그래야 이 검사가 항진명제에서
        # 빠지고, 동시에 이름 결박이 없어진다.
        return InstanceVerdict(
            state="unknown",
            detail=(
                "선언은 PostgreSQL인데 살아있는 컨테이너가 postgres 서버를 돌리지 "
                "않습니다."
            ),
            **base,
        )
    if not probe.declared_postgres and probe.live_postgres:
        return InstanceVerdict(
            state="unknown",
            detail=(
                "살아있는 컨테이너는 postgres 서버인데 targets 선언의 role이 "
                "PostgreSQL이 아닙니다."
            ),
            **base,
        )
    if not probe.rules:
        # 0행은 "규칙이 없다"가 아니라 "못 읽었다"다. 항진명제를 만들지 않는다.
        return InstanceVerdict(
            state="unknown", detail="pg_hba_file_rules가 한 행도 없습니다.", **base
        )
    parse_errors = [rule for rule in probe.rules if rule.error != _MISSING]
    if parse_errors:
        return InstanceVerdict(
            state="unknown",
            detail="pg_hba 파일 파싱 오류가 있어 판정할 수 없습니다.",
            evidence={"errors": [rule.to_evidence() for rule in parse_errors[:5]]},
            **base,
        )
    if probe.file_newer_than_reload is None:
        return InstanceVerdict(
            state="unknown",
            detail="pg_hba 파일과 로드 시각을 비교할 수 없습니다.",
            **base,
        )
    if probe.file_newer_than_reload:
        # `pg_hba_file_rules`는 **파일**을 파싱한 결과다. 파일이 reload보다 새로우면
        # 살아있는 동작과 갈렸다는 뜻이므로 초록불을 켜지 않는다.
        return InstanceVerdict(
            state="unknown",
            detail=(
                "pg_hba 파일이 마지막 설정 로드 이후에 바뀌었습니다 — 살아있는 동작이 "
                "파일과 다를 수 있습니다."
            ),
            evidence={
                "hba_file": probe.hba_file or _MISSING,
                "conf_load_time": probe.conf_load_time or _MISSING,
                "hba_modified_at": probe.hba_modified_at or _MISSING,
            },
            **base,
        )
    offending = [rule for rule in probe.rules if rule.grants_trust_over_tcp]
    if offending:
        return InstanceVerdict(
            state="violation",
            detail=(
                f"TCP 경로에 trust를 허용하는 규칙 {len(offending)}건이 있습니다."
            ),
            evidence={"rules": [rule.to_evidence() for rule in offending]},
            **base,
        )
    return InstanceVerdict(
        state="ok",
        detail=f"규칙 {len(probe.rules)}건 중 TCP trust는 없습니다.",
        evidence={"rules_total": len(probe.rules)},
        **base,
    )


def read_posture() -> PostureVerdict:
    """관측 + 판정. **절대 예외를 던지지 않는다.**"""

    try:
        return decide(probe_instances())
    except Exception:  # noqa: BLE001 - 진단 패널이 500을 내면 볼 창을 잃는다
        return PostureVerdict(
            state="unknown",
            detail="살아있는 pg_hba를 확인하는 중 예상하지 못한 오류가 발생했습니다.",
            instances=(),
        )
