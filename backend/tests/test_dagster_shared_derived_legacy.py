"""공용 Dagster plane(ADR-54) 전환이 코드 literal에 막히지 않는다 — C6c·보존·M05·옛 override 이관.

Map·PinVi·geo의 옛 webserver·daemon 이름은 이제 `runtime_topology`가 렌더된 모델과 `dagster.control_plane`
스위치에서 파생한다. 두 가지를 보인다.

- **모두 `own`이면 글자까지 종전 그대로다.** 아래 `_PRE_ADR54_*`는 파생 이전 코드의 literal을 옮겨 적은
  것이다 — 파생이 이것과 한 글자라도 다르면 C6c 후보 판정·배포 기록·tag가 조용히 바뀐다.
- **전환하면 옛 서비스는 실행·필수 집합에서 빠지고 code-server가 그 자리를 잇는다.** env 계약(DSN·Geo key
  값 고정, UI 잠금)은 profile로 내려간 옛 서비스에도 그대로 걸린다 — 그 profile을 켜면 그 값으로 돈다.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c
from kor_travel_docker_manager.services import legacy_override_retirement as retirement
from kor_travel_docker_manager.services import m05_isolated_harness as m05
from kor_travel_docker_manager.services import runtime_topology as topology_module
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    validate_resolved_compose_candidate_protected_values,
)
from kor_travel_docker_manager.services.runtime_topology import (
    DagsterFamily,
    derive_dagster_families,
    installed_dagster_families,
    runtime_topology,
)

_PRE_ADR54_REQUIRED_ORDER = (
    "kor-travel-map-api",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
    "kor-travel-map-dagster-storage-migrate",
    "kor-travel-map-db-role-bootstrap",
    "kor-travel-map-application-schema",
    "pinvi-api",
    "pinvi-admin-bootstrap",
    "kor-travel-map-ui",
)
_PRE_ADR54_HOST_NETWORK = frozenset(
    {
        "kor-travel-map-api",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "kor-travel-map-dagster-storage-migrate",
        "kor-travel-map-db-role-bootstrap",
        "kor-travel-map-application-schema",
    }
)
_PRE_ADR54_MAP_RUNTIME = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_PRE_ADR54_PINVI_DSN_SERVICES = (
    "pinvi-api",
    "pinvi-dagster",
    "pinvi-dagster-code-server",
    "pinvi-dagster-daemon",
    "pinvi-admin-bootstrap",
)
_PRE_ADR54_PINVI_DAGSTER_PG_URL_SERVICES = (
    "pinvi-dagster",
    "pinvi-dagster-code-server",
    "pinvi-dagster-daemon",
)
_PRE_ADR54_MAP_DAGSTER_PROCESSES = (
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-code-server",
    "kor-travel-map-dagster-daemon",
)
_PRE_ADR54_SECRET_ISOLATION_CONTAINERS = (
    "kor-travel-map-dagster-latest",
    "kor-travel-map-dagster-daemon-latest",
    "kor-travel-map-dagster-code-server-latest",
)
_PRE_ADR54_GEO_OVERRIDE_SERVICES = (
    "kor-travel-geo-api",
    "kor-travel-geo-dagster",
    "kor-travel-geo-dagster-daemon",
)


def _flipped_families(*targets: str) -> Mapping[str, DagsterFamily]:
    from test_dagster_shared_workspace_is_derived import _documents, _flip

    compose, targets_document = _documents()
    for target_id in targets:
        _flip(compose, targets_document, target_id)
    return derive_dagster_families(compose, targets_document)


def _use(monkeypatch: pytest.MonkeyPatch, families: Mapping[str, DagsterFamily]) -> None:
    monkeypatch.setattr(topology_module, "installed_dagster_families", lambda: families)


# ── 모두 `own`: 파생 이전 literal과 같다 ─────────────────────────────────────


def test_every_target_is_own_in_the_installed_model() -> None:
    """이 파일의 `own` 단언이 뜻을 가지려면 설치된 모델이 실제로 모두 `own`이어야 한다."""

    families = installed_dagster_families()
    assert sorted(families) == ["geo", "map", "pinvi", "weather"]
    assert {family.control_plane for family in families.values()} == {"own"}


def test_own_c6c_sets_are_the_pre_adr54_literals() -> None:
    assert c6c._candidate_protected_service_order() == _PRE_ADR54_REQUIRED_ORDER
    assert set(c6c._CANDIDATE_REQUIRED_PROTECTED_SERVICES) == set(_PRE_ADR54_REQUIRED_ORDER)
    assert set(c6c._CANDIDATE_KNOWN_SERVICE_NAMES) == (
        set(_PRE_ADR54_REQUIRED_ORDER) | c6c._CANDIDATE_NAMEABLE_SERVICE_NAMES
    )
    assert c6c._map_database_host_network_services() == _PRE_ADR54_HOST_NETWORK
    assert tuple(c6c._MAP_RUNTIME_SERVICES) == _PRE_ADR54_MAP_RUNTIME
    assert tuple(c6c._PINVI_DSN_SERVICE_CREDENTIALS) == tuple(
        (name, "PINVI_APP_DB_USER", "PINVI_APP_DB_PASSWORD")
        for name in _PRE_ADR54_PINVI_DSN_SERVICES
    )
    assert tuple(c6c._PINVI_DATABASE_URL_RAW_VALUES) == _PRE_ADR54_PINVI_DSN_SERVICES
    assert tuple(c6c._PINVI_DAGSTER_PG_URL_SERVICES) == _PRE_ADR54_PINVI_DAGSTER_PG_URL_SERVICES
    assert tuple(c6c._PINVI_DAGSTER_PG_URL_RAW_VALUES) == _PRE_ADR54_PINVI_DAGSTER_PG_URL_SERVICES
    assert c6c._map_dagster_secret_isolation_containers() == _PRE_ADR54_SECRET_ISOLATION_CONTAINERS


def test_own_env_contract_tables_keep_their_pre_adr54_rows_in_order() -> None:
    """DSN·Geo key 행은 Map Dagster family 세 서비스에 종전 순서로 붙는다."""

    database_rows = list(c6c._MAP_DATABASE_CANONICAL_ENV_VALUES)
    dagster_pg_url = [
        service for service, name in database_rows if name == "KOR_TRAVEL_MAP_DAGSTER_PG_URL"
    ]
    assert dagster_pg_url == [
        "kor-travel-map-db-role-bootstrap",
        *_PRE_ADR54_MAP_DAGSTER_PROCESSES,
        "kor-travel-map-dagster-storage-migrate",
    ]
    api_rows = list(c6c._CANDIDATE_CANONICAL_API_ENV_VALUES)
    geo_key = [
        service
        for service, name in api_rows
        if name == "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY"
    ]
    assert geo_key == ["kor-travel-map-api", *_PRE_ADR54_MAP_DAGSTER_PROCESSES]
    for service in (*_PRE_ADR54_MAP_DAGSTER_PROCESSES, *_PRE_ADR54_PINVI_DAGSTER_PG_URL_SERVICES):
        assert c6c.contract_locked_env_names(service), service


def test_own_m05_and_override_retirement_names() -> None:
    assert tuple(retirement._GEO_SERVICES) == _PRE_ADR54_GEO_OVERRIDE_SERVICES
    # M05 격리 project의 이미지 역할은 Manager 서비스 이름이 아니다 — receipt 필드는 그대로다.
    assert set(m05._RUNTIME_IMAGE_ROLES) == {
        "map-admin",
        "map-api",
        "map-frontend",
        "pinvi-api",
        "pinvi-app-dagster",
        "pinvi-web",
    }


# ── 전환 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "old", "code_server"),
    [
        ("map", ("kor-travel-map-dagster", "kor-travel-map-dagster-daemon"), "kor-travel-map-dagster-code-server"),
        ("pinvi", ("pinvi-dagster", "pinvi-dagster-daemon"), "pinvi-dagster-code-server"),
        ("geo", ("kor-travel-geo-dagster", "kor-travel-geo-dagster-daemon"), "kor-travel-geo-dagster-code-server"),
    ],
)
def test_a_flip_takes_the_old_services_out_of_every_running_set(
    monkeypatch: pytest.MonkeyPatch, target: str, old: tuple[str, ...], code_server: str
) -> None:
    _use(monkeypatch, _flipped_families(target))
    topology = runtime_topology()
    running: set[str] = {
        *topology.runtime_services,
        *c6c._CANDIDATE_REQUIRED_PROTECTED_SERVICES,
        *c6c._candidate_protected_service_order(),
        *c6c._map_database_host_network_services(),
        *c6c._MAP_RUNTIME_SERVICES,
    }
    assert running.isdisjoint(old)
    assert set(topology.retired_services) == set(old)
    if target == "map":
        assert code_server in c6c._CANDIDATE_REQUIRED_PROTECTED_SERVICES
        assert code_server in c6c._map_database_host_network_services()
        assert c6c._map_dagster_secret_isolation_containers() == (
            "kor-travel-map-dagster-code-server-latest",
        )
    if target in ("map", "pinvi"):
        assert code_server in topology.runtime_services
    # env 계약은 스위치와 무관하다 — profile로 내려간 옛 서비스를 켜도 그 값으로 돈다.
    assert tuple(c6c._PINVI_DSN_SERVICE_CREDENTIALS)[1][0] == "pinvi-dagster"
    # 옛 override 이관은 과거 파일의 모양이다 — geo가 합류해도 그 이름을 그대로 알아본다.
    assert tuple(retirement._GEO_SERVICES) == _PRE_ADR54_GEO_OVERRIDE_SERVICES


def test_the_frozen_render_never_carries_a_flipped_targets_old_dagster(tmp_path: Path) -> None:
    """frozen render는 `bootstrap` profile만 켠다 — 넷 다 전환한 compose에서 옛 서비스는 하나도 해석되지 않고
    code-server는 모두 남는다. 이 profile 목록에 `legacy-dagster`가 들어가면 빨갛다."""

    import json
    import os
    import re
    import shutil
    import subprocess

    import yaml
    from test_dagster_shared_workspace_is_derived import (
        _dagster_targets,
        _documents,
        _flip,
        _legacy,
    )

    from kor_travel_docker_manager.services import compose_service

    assert compose_service._FROZEN_COMPOSE_PROFILES == ("bootstrap",)
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose가 없어 frozen render를 해석할 수 없음")
    compose, targets_document = _documents()
    legacy: set[str] = set()
    for target_id in _dagster_targets():
        legacy |= _legacy(compose, targets_document["targets"][target_id])
        _flip(compose, targets_document, target_id)
    families = derive_dagster_families(compose, targets_document)
    # frozen render처럼 **보간한다**. n150 Compose v5.2.0 실측: `--no-interpolate`와 `--services`는 profile을
    # 거르지 않는다(꺼진 profile의 서비스도 적는다). 필수 변수는 자리값으로 채우고, 이 검사와 무관한 env_file은 뺀다.
    text = yaml.safe_dump(compose, sort_keys=False)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        **{name: "1" for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?", text)},
    }
    for service in compose["services"].values():
        service.pop("env_file", None)
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for profile in compose_service._FROZEN_COMPOSE_PROFILES:
        command += ["--profile", profile]
    completed = subprocess.run(
        [*command, "--project-directory", str(Path(__file__).resolve().parents[2]), "-f", "-",
         "config", "--format", "json"],
        input=yaml.safe_dump(compose, sort_keys=False),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = set(json.loads(completed.stdout)["services"])
    assert legacy and rendered.isdisjoint(legacy), sorted(rendered & legacy)
    assert {family.code_server for family in families.values()} <= rendered


def test_the_pinvi_build_provenance_follows_the_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    """PinVi Dagster 이미지를 빌드하는 서비스의 build 배선을 본다 — 합류하면 code-server다."""

    from test_dagster_shared_workspace_is_derived import _documents, _flip

    compose, targets_document = _documents()
    c6c.validate_c6c_build_source_wiring(compose)
    _flip(compose, targets_document, "pinvi")
    _use(monkeypatch, derive_dagster_families(compose, targets_document))
    c6c.validate_c6c_build_source_wiring(compose)
    compose["services"]["pinvi-dagster-code-server"]["build"]["dockerfile"] = "apps/api/Dockerfile"
    with pytest.raises(c6c.DeploymentContractError, match="pinvi-dagster-code-server provenance"):
        c6c.validate_c6c_build_source_wiring(compose)


def test_a_frozen_render_without_the_old_map_dagster_passes_only_after_the_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """frozen render(`--profile bootstrap`만)는 `legacy-dagster`의 옛 webserver·daemon을 싣지 않는다.
    literal 필수 집합이면 그 render를 거부해 전환된 target의 재구축이 서지 못한다(빨간 대조군: 전환 전 모델)."""

    from test_f1d_compose_contract import (
        _MAP_DATABASE_ONESHOT_SERVICES,
        _bootstrap_candidate,
        _resolved_compose,
    )

    _candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved: dict[str, Any] = _resolved_compose(
        "kor-travel-shared-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster-code-server",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        environment_update={
            name: environment[name]
            for name in ("KOR_TRAVEL_MAP_PGDATA", "KOR_TRAVEL_MAP_REPO_DIR", "PINVI_REPO_DIR")
        },
    )
    assert "kor-travel-map-dagster" not in resolved["services"]

    def validate() -> None:
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(Path(__file__).resolve().parents[2] / "docker-compose.yml"),
            root_env_path=str(root_env),
        )

    with pytest.raises(ComposeCandidateContractError, match="missing required protected service"):
        validate()
    _use(monkeypatch, _flipped_families("map"))
    validate()
