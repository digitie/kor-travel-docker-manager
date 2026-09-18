"""살아있는 `pg_hba` 자세 검사 — 판정은 순수 함수라 형상으로 직접 태운다.

이 검사가 존재하는 이유는 **compose 문서 검사가 충분하지 않다는 것이 실측으로
드러났기** 때문이다. `POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256`은 initdb 시점에만
적용되므로, 그 값이 계약에 들어오기 전에 만들어진 PGDATA는 그대로 남는다. 2026-09-18에
적대 리뷰가 n150의 여섯 인스턴스 전부에서 `host replication all 127.0.0.1/32 trust`를
실측했고, 빈 `PGPASSWORD`로 `IDENTIFY_SYSTEM`이 응답했다.

아래 검사들은 이 저장소가 반복해서 지적받은 함정 셋을 피하도록 짰다:

1. **항진명제 금지** — 0행·파싱 오류·시각 불일치는 `ok`가 아니라 `unknown`이다.
   그 형상을 각각 태운다.
2. **이름이 아니라 효과** — `type = 'host'`가 아니라 `type <> 'local'`이다.
   `hostssl`·`hostnossl`·`hostgssenc`를 파라미터로 태운다.
3. **관측 경로도 태운다** — `decide`만 검사하면 `probe_*`의 포트·role 유도가 무증거로
   남는다. 가짜 `_read_only_text`로 argv까지 본다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from kor_travel_docker_manager.services import (
    postgres_hba_posture as posture_module,
)
from kor_travel_docker_manager.services.postgres_hba_posture import (
    HbaRule,
    InstanceProbe,
    decide,
    probe_instances,
    read_posture,
)


def _rule(
    connection_type: str = "host",
    auth_method: str = "scram-sha-256",
    *,
    address: str = "127.0.0.1/32",
    database: str = "all",
    error: str = "-",
    line_number: str = "119",
) -> HbaRule:
    return HbaRule(
        line_number=line_number,
        connection_type=connection_type,
        database=database,
        user_name="all",
        address=address,
        auth_method=auth_method,
        error=error,
    )


def _probe(**overrides: Any) -> InstanceProbe:
    base: dict[str, Any] = {
        "container_id": "kor-travel-map-postgresql",
        "container_name": "kor-travel-map-postgres",
        "external_project": None,
        "declared_postgres": True,
        "live_postgres": True,
        "rules": (_rule(), _rule(connection_type="local", auth_method="trust")),
        "hba_file": "/var/lib/postgresql/data/pg_hba.conf",
        "conf_load_time": "2026-09-18T03:28:37+00:00",
        "hba_modified_at": "2026-09-18T03:28:30+00:00",
        "file_newer_than_reload": False,
        "unknown_reason": None,
    }
    base.update(overrides)
    return InstanceProbe(**base)


# ── 효과에 결박한다: `host`가 아니라 "local이 아닌 것" ───────────────────


@pytest.mark.parametrize(
    "connection_type",
    ["host", "hostssl", "hostnossl", "hostgssenc", "hostnogssenc"],
)
def test_every_tcp_connection_type_counts(connection_type: str) -> None:
    """**`type = 'host'`는 틀린 술어다.**

    PostgreSQL은 작성된 키워드를 그대로 싣고, 이 다섯은 전부 TCP다. `host`만 보면
    `hostnossl all all 0.0.0.0/0 trust` 한 줄이 통과한다. 지금 여섯 인스턴스에는
    `host`와 `local`만 있어서 **오늘은 차이가 없다 — 그래서 더 위험하다.**
    """

    verdict = decide(
        [_probe(rules=(_rule(connection_type=connection_type, auth_method="trust"),))]
    )
    assert verdict.state == "missing", verdict.detail


def test_local_trust_is_not_a_violation() -> None:
    """`local … trust`는 **건드리지 않는다.**

    컨테이너 내부 unix socket/peer 접속이고 entrypoint·healthcheck·db-init one-shot이
    전부 그것에 의존한다 — 이 검사 자신도 그것으로 붙는다. 여기서 위반으로 세면
    "고치라"는 지시가 곧 프로덕션을 깨는 지시가 된다.
    """

    verdict = decide(
        [
            _probe(
                rules=(
                    _rule(connection_type="local", auth_method="trust"),
                    _rule(connection_type="local", database="replication", auth_method="trust"),
                    _rule(),
                )
            )
        ]
    )
    assert verdict.state == "ok", verdict.detail


def test_the_replication_shape_that_was_actually_open_is_caught() -> None:
    """n150에서 실측된 그 형상을 그대로 태운다."""

    verdict = decide(
        [
            _probe(
                rules=(
                    _rule(),
                    _rule(
                        database="replication",
                        auth_method="trust",
                        line_number="125",
                    ),
                    _rule(
                        database="replication",
                        auth_method="trust",
                        address="::1/128",
                        line_number="126",
                    ),
                )
            )
        ]
    )
    assert verdict.state == "missing"
    rules = verdict.instances[0].evidence["rules"]
    assert [rule["line"] for rule in rules] == ["125", "126"]
    assert all(rule["auth_method"] == "trust" for rule in rules)


# ── "확인 불가"는 "안전"이 아니다 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        pytest.param({"rules": ()}, "한 행도 없습니다", id="no-rows"),
        pytest.param(
            {"rules": (_rule(error="invalid connection type"),)},
            "파싱 오류",
            id="parse-error",
        ),
        pytest.param(
            {"file_newer_than_reload": None}, "비교할 수 없습니다", id="no-load-time"
        ),
        pytest.param(
            {"file_newer_than_reload": True}, "마지막 설정 로드 이후", id="file-newer"
        ),
        pytest.param(
            {"unknown_reason": "pg_hba_file_rules를 조회할 수 없다"},
            "조회할 수 없다",
            id="probe-failed",
        ),
    ],
)
def test_unverifiable_is_not_green(overrides: dict[str, Any], reason: str) -> None:
    """**항진명제를 만들지 않는다.**

    0행은 "규칙이 없다"가 아니라 "못 읽었다"다. 그리고 `pg_hba_file_rules`는 **파일**을
    파싱한 결과이고 로드된 설정이 아니므로, 파일이 reload보다 새로우면 살아있는 동작과
    갈렸다는 뜻이다 — 그때 초록불을 켜면 그것이 거짓 안심이다.
    """

    verdict = decide([_probe(**overrides)])
    assert verdict.state == "unknown", verdict.detail
    assert reason in verdict.instances[0].detail


def test_docker_unreachable_is_unknown_not_ok() -> None:
    """daemon 접근 불가는 `ok`도 `missing`도 아니다.

    게이트가 없으면 image store가 멀쩡한 호스트에서 비-root backend가 **거짓 차단**을
    보고한다 — `deployment_readiness._docker_daemon_reachable`이 같은 이유로 있다.
    """

    verdict = decide(None)
    assert verdict.state == "unknown"
    assert verdict.instances == ()


def test_an_unknown_instance_holds_the_whole_row_back() -> None:
    """한 인스턴스라도 확인 못 하면 전체가 초록이 아니다.

    "판정 근거를 하나라도 잃으면 초록불을 켜지 않는다"(`docs/dashboard-ui.md`).
    """

    verdict = decide(
        [
            _probe(),
            _probe(
                container_id="pinvi-postgresql",
                unknown_reason="서버 포트를 유도할 수 없다",
            ),
        ]
    )
    assert verdict.state == "unknown"
    assert verdict.evidence["unknown"] == ["pinvi-postgresql"]


# ── 등급을 소유권으로 나눈다 ─────────────────────────────────────────────


def test_an_external_projects_violation_is_a_warning_not_a_blocker() -> None:
    """**차단이 아닌 것을 차단으로 만들지 않는다.**

    `missing`은 `pinned_rebuild_preflight`가 재구축 blocker로 승격시킨다. 외부 프로젝트
    인스턴스의 소견으로 그것을 켜면 **남의 DB 때문에 Map 재구축이 막힌다** — Manager는
    남의 compose를 고칠 수단이 없고(`ensure_target`이 외부 target을 거부한다) 조치
    주체가 그 저장소다.
    """

    verdict = decide(
        [
            _probe(),
            _probe(
                container_id="kor-travel-weather-db",
                container_name="kor-travel-weather-db-1",
                external_project="kor-travel-weather",
                rules=(_rule(auth_method="trust"),),
            ),
        ]
    )
    assert verdict.state == "warn", verdict.detail
    assert "kor-travel-weather" in verdict.detail


def test_a_manager_owned_violation_blocks() -> None:
    """Manager 소유는 차단이다 — 고칠 수단이 있는 쪽이다."""

    verdict = decide([_probe(rules=(_rule(auth_method="trust"),))])
    assert verdict.state == "missing"


def test_a_manager_violation_outranks_an_external_one() -> None:
    """둘이 함께 있으면 차단이 이긴다 — 경고로 묻히지 않는다."""

    verdict = decide(
        [
            _probe(rules=(_rule(auth_method="trust"),)),
            _probe(
                container_id="kor-travel-airport-postgresql",
                external_project="kor-travel-airport-db",
                rules=(_rule(auth_method="trust"),),
            ),
        ]
    )
    assert verdict.state == "missing"
    assert "kor-travel-map-postgresql" in verdict.detail


# ── 선언과 라이브의 불일치 자체가 소견이다 ──────────────────────────────


def test_a_declared_postgres_that_is_not_live_is_unknown() -> None:
    """`role`은 UI 문자열이고 아무것도 이것을 강제하지 않는다.

    선언과 라이브가 어긋나면 **그 불일치 자체가 소견**이다 — 그래야 이 검사가
    항진명제에서 빠지고 동시에 이름 결박이 없어진다.
    """

    verdict = decide([_probe(live_postgres=False, rules=())])
    assert verdict.state == "unknown"
    assert "postgres 서버를 돌리지 않습니다" in verdict.instances[0].detail


def test_a_live_postgres_that_is_not_declared_is_unknown() -> None:
    """반대 방향 — 여섯째 postgres를 `role: db`로 선언해도 새어나가지 않는다."""

    verdict = decide([_probe(declared_postgres=False)])
    assert verdict.state == "unknown"
    assert "role이 PostgreSQL이 아닙니다" in verdict.instances[0].detail


def test_containers_that_are_neither_declared_nor_live_are_out_of_scope() -> None:
    """**범위 필터.** 조회 실패한 비-postgres 컨테이너가 전체를 물들이지 않는다.

    첫 판은 `live_postgres is False`만 걸렀는데 조회 실패는 `None`이라, 비-postgres
    컨테이너 스무 개가 전부 `unknown`으로 올라와 전체를 `unknown`으로 만들었다(실측).
    """

    verdict = decide(
        [
            _probe(),
            _probe(
                container_id="kor-travel-map-api",
                declared_postgres=False,
                live_postgres=None,
                unknown_reason="컨테이너를 조회할 수 없다",
            ),
        ]
    )
    assert verdict.state == "ok", verdict.detail
    assert verdict.evidence["checked"] == ["kor-travel-map-postgresql"]


# ── 관측 경로: 포트·role 유도와 argv ────────────────────────────────────


class _FakeDocker:
    """`_read_only_text`를 대신해 argv를 기록하고 정해진 답을 준다."""

    def __init__(self, config: dict[str, Any], rules: str, load: str) -> None:
        self.config = config
        self.rules = rules
        self.load = load
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], *, timeout: float) -> str | None:
        self.calls.append(list(command))
        if command[:2] == ["docker", "version"]:
            return "28.0.0\n"
        if command[:2] == ["docker", "inspect"]:
            return json.dumps(self.config)
        if "pg_hba_file_rules" in command[-1]:
            return self.rules
        return self.load

    @property
    def psql_argv(self) -> list[str]:
        return next(
            call for call in self.calls if "psql" in call and "pg_hba_file_rules" in call[-1]
        )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any],
    *,
    rules: str = "1|/x/pg_hba.conf|119|host|all|all|127.0.0.1/32|scram-sha-256|-",
    load: str = "/x/pg_hba.conf|2026-09-18T03:28:37+00:00|2026-09-18T03:28:30+00:00|f",
) -> _FakeDocker:
    fake = _FakeDocker(config, rules + "\n", load + "\n")
    monkeypatch.setattr(posture_module, "_read_only_text", fake)
    monkeypatch.setattr(
        posture_module,
        "MANAGED_CONTAINERS",
        {
            "kor-travel-map-postgresql": {
                "name": "kor-travel-map-postgres",
                "role": "map-postgresql",
                "compose_service": "kor-travel-map-postgres",
            }
        },
    )
    monkeypatch.setattr(
        posture_module, "external_project_for_container", lambda _cid: None
    )
    return fake


def test_the_port_comes_from_the_live_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--port`는 **소켓 접속에도 필수**다.

    unix socket 파일 이름이 `.s.PGSQL.<port>`라서 포트가 틀리면 소켓 자체를 못 찾는다.
    하드코딩 5432는 내부 넷에서 전부 실패한다(실측 포트가 12500/12600/12700/12800이다).
    """

    fake = _install(
        monkeypatch,
        {
            "Cmd": ["postgres", "-c", "listen_addresses=127.0.0.1", "-p", "12700"],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": ["POSTGRES_USER=kor_travel_map"],
        },
    )
    probes = probe_instances()
    assert probes is not None
    assert probes[0].unknown_reason is None, probes[0].unknown_reason
    argv = fake.psql_argv
    assert argv[argv.index("--port") + 1] == "12700"
    assert argv[argv.index("--username") + 1] == "kor_travel_map"
    # 자격증명을 넘기지 않는다 — 컨테이너 안 소켓이 `local … trust`에 매칭된다.
    assert not any("PGPASSWORD" in token for token in argv)
    assert "--no-psqlrc" in argv, "~/.psqlrc가 출력을 오염시키면 파싱이 깨진다"


def test_the_port_falls_back_for_an_external_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**`standalone_backup._discover_port`를 재사용하면 안 되는 이유.**

    그 함수는 `-p` 토큰만 보고 없으면 예외를 던진다. 실측으로 `weather-db`·`airport-db`의
    `Cmd`는 정확히 `["postgres"]`이므로, 재사용하면 **지금 문제가 있는 두 인스턴스가
    검사에서 사라진다**(소견이 아니라 하드 에러가 된다).
    """

    fake = _install(
        monkeypatch,
        {
            "Cmd": ["postgres"],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": ["POSTGRES_USER=weather"],
            "ExposedPorts": {"5432/tcp": {}},
        },
    )
    probes = probe_instances()
    assert probes is not None
    assert probes[0].unknown_reason is None, probes[0].unknown_reason
    argv = fake.psql_argv
    assert argv[argv.index("--port") + 1] == "5432"


def test_a_one_shot_on_the_same_image_is_not_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Cmd[0] == "sh"`인 one-shot은 서버가 아니다.

    `kor-travel-concierge-db-init`이 같은 postgis 이미지로 `createdb`를 돌린다(실측).
    """

    fake = _install(
        monkeypatch,
        {
            "Cmd": ["sh", "-ec", "createdb x"],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": ["POSTGRES_USER=x"],
        },
    )
    probes = probe_instances()
    assert probes is not None
    assert probes[0].live_postgres is False
    assert not any("psql" in call for call in fake.calls), "one-shot에 질의하지 않는다"


def test_the_public_entry_point_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """진단 패널이 500을 내면 운영자는 상태를 볼 유일한 창을 잃는다."""

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(posture_module, "probe_instances", explode)
    verdict = read_posture()
    assert verdict.state == "unknown"


def test_the_evidence_carries_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """증거에 비밀이 섞이지 않는다 — 조회 컬럼에 애초에 없다.

    `pg_hba_file_rules`의 컬럼은 rule/파일/줄/type/database/user_name/address/netmask/
    auth_method/options/error뿐이고 `pg_authid.rolpassword`는 조회하지 않는다.
    """

    verdict = decide([_probe(rules=(_rule(auth_method="trust"),))])
    serialized = json.dumps(
        {
            "detail": verdict.detail,
            "evidence": dict(verdict.evidence),
            "instances": [dict(i.evidence) for i in verdict.instances],
        },
        ensure_ascii=False,
    )
    for forbidden in ("PGPASSWORD", "rolpassword", "POSTGRES_PASSWORD", "/run/secrets"):
        assert forbidden not in serialized, forbidden
