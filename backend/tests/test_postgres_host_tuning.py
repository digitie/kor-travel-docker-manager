"""호스트별 PG 수치·이미지·메모리 정책(ADR-58 결정 3·8·9, R6, T-MGR-03).

n150 무변경이 핵심이다: env 재정의가 없으면(오늘 n150 root `.env`) 모든 값이 종전 값이거나 효과 0이어야 한다. 렌더 차이는
ADR-58 결정 1의 허용 diff ⑥(oom·memswap)·⑦(서버 기본값 키 6개)·⑧(db-init-geo 확장)뿐이다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_shared_postgres_runtime_contract import compose_default
from test_workspace_digest_validate import project  # noqa: F401 - fixture

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.runtime_topology import derive_shared_dagster_plane
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

ROOT = Path(__file__).resolve().parents[2]
_PG = "kor-travel-shared-postgres"

#: PG 16 서버 기본값(문서 `runtime-config-*`) — n150 compose에 없던 키라 기본값이 이것이어야 n150 효과가 0이다(결정 1 ⑦).
_PG16_SERVER_DEFAULTS = {
    "max_parallel_workers": "8",
    "max_parallel_workers_per_gather": "2",
    "effective_io_concurrency": "1",
    "checkpoint_timeout": "5min",
    "wal_compression": "off",
    "autovacuum_max_workers": "3",
}


def _compose() -> dict[str, Any]:
    return load_yaml_rejecting_duplicate_keys((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _settings(service: dict[str, Any]) -> dict[str, str]:
    command = service["command"]
    pairs = [command[i + 1] for i, token in enumerate(command) if token == "-c"]
    return dict(pair.partition("=")[::2] for pair in pairs)


def test_new_server_keys_default_to_the_pg16_server_defaults():
    settings = _settings(_compose()["services"][_PG])
    for key, default in _PG16_SERVER_DEFAULTS.items():
        assert compose_default(settings[key]) == default, key
        assert settings[key].startswith(f"${{KTDM_PG_{key.upper()}:-"), key


def test_max_connections_is_not_set():
    # compose에 없다(서버 기본 100) — 결정 8 ⅰ은 추가하지 않는다.
    assert "max_connections" not in _settings(_compose()["services"][_PG])


def test_postgres_images_are_host_overridable_with_todays_defaults():
    services = _compose()["services"]
    assert compose_default(services[_PG]["image"]).startswith("postgis/postgis:16-3.5@sha256:")
    init = [name for name, service in services.items()
            if name.startswith("kor-travel-shared-db-init-")]
    assert len(init) >= 6
    for name in init:
        assert services[name]["image"] == "${KTDM_POSTGRES_CLIENT_IMAGE:-postgis/postgis:16-3.5}", name


_ENV_LIMIT = re.compile(r"\$\{(KTDM_[A-Z0-9_]+_MEM_LIMIT):-([^}]+)\}")


def _host_limited(services: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`mem_limit`가 호스트별 env(`KTDM_*_MEM_LIMIT`)에서 오는 서비스 — memswap 규칙의 범위다."""
    return {name: service for name, service in services.items()
            if _ENV_LIMIT.fullmatch(str(service.get("mem_limit", "")))}


def test_every_host_limited_service_pins_memswap_to_the_same_value():
    """`mem_limit`가 호스트별 env인 서비스마다 `memswap_limit == mem_limit`(같은 식) — 상한을 두는 호스트에서 swap으로 새지
    않게. 범위는 PG·geo cs·weather cs다. concierge scheduler·cs(고정 `mem_limit: 2g`, n150 전용)는 이 규칙 **밖**이다 —
    거기에 memswap을 걸면 n150에서 swap 0 효과가 새로 생긴다(PR 본문)."""
    services = _compose()["services"]
    limited = _host_limited(services)
    assert {_PG, "kor-travel-geo-dagster-code-server", "kor-travel-weather-dagster-code-server"} <= set(limited)
    for name, service in limited.items():
        assert service.get("memswap_limit") == service["mem_limit"], name
    concierge = {name: s for name, s in services.items() if name.startswith("kor-travel-concierge") and s.get("mem_limit")}
    assert concierge and all("memswap_limit" not in s for s in concierge.values())


def test_a_missing_memswap_is_red():
    services = _compose()["services"]
    limited = _host_limited(services)
    name = next(iter(limited))
    del services[name]["memswap_limit"]
    assert any(s.get("memswap_limit") != s["mem_limit"] for s in _host_limited(services).values())


@pytest.mark.parametrize("name", [_PG, "kor-travel-geo-dagster-code-server"])
def test_pg_and_geo_code_server_have_no_effective_limit_on_the_default_host(name):
    # 기본 `0` = 상한 없음(compose가 렌더에서 지운다) — n150에서 실효 mem_limit가 생기면 빨강.
    service = _compose()["services"][name]
    assert compose_default(service["mem_limit"]) == "0"
    assert compose_default(service["memswap_limit"]) == "0"


def test_weather_code_server_keeps_todays_limit_by_default():
    service = _compose()["services"]["kor-travel-weather-dagster-code-server"]
    assert compose_default(service["mem_limit"]) == "4g"
    assert service["oom_score_adj"] == -500


def test_oom_priorities():
    compose = _compose()
    services = compose["services"]
    assert services[_PG]["oom_score_adj"] == -800
    # plane은 렌더된 모델에서 파생한다(`compose config`처럼 기본값으로 보간한 문서 — 이름을 적지 않는다).
    rendered = re.sub(r"\$\{[A-Z0-9_]+:-([^${}]*)\}", r"\1", yaml.safe_dump(compose))
    plane = derive_shared_dagster_plane(yaml.safe_load(rendered))
    for name in plane.services:
        assert services[name]["oom_score_adj"] == -600, name


def test_db_init_geo_creates_the_four_extensions_in_x_extension():
    script = _compose()["services"]["kor-travel-shared-db-init-geo"]["command"][-1]
    assert 'CREATE SCHEMA IF NOT EXISTS x_extension' in script
    assert 'for extension in postgis pg_trgm unaccent pg_stat_statements; do' in script
    assert 'CREATE EXTENSION IF NOT EXISTS $$extension WITH SCHEMA x_extension' in script
    assert 'GRANT USAGE ON SCHEMA x_extension TO' in script
    # 확장 생성이 role·DB·CONNECT 뒤에 온다(DB가 있어야 한다).
    assert script.index("createdb") < script.index("CREATE SCHEMA IF NOT EXISTS x_extension")


@pytest.mark.parametrize(
    ("image", "refused"),
    [
        (None, False),  # 기본값(n150 digest 핀)
        ("imresamu/postgis:16-3.5@sha256:" + "a" * 64, False),  # odroid 형태
        ("postgis/postgis:17-3.5@sha256:" + "a" * 64, True),  # 다른 major — PGDATA(16)를 거부한다
        ("imresamu/postgis:16-3.5", True),  # floating tag
        ("postgis/postgis:latest@sha256:" + "a" * 64, True),  # major가 태그에 없다
    ],
)
def test_the_server_image_must_be_a_digest_pinned_pgdata_major(project, capsys, image, refused):  # noqa: F811
    """`.env`의 `KTDM_POSTGRES_IMAGE`가 다른 major·floating tag면 재생성된 공용 PG가 전 테넌트를 끊는다 — `targets validate`
    (설치기 flip 전)와 `ensure` 프리플라이트가 같은 규칙으로 거부한다. 대상은 registry가 PostgreSQL로 선언한 서비스다."""
    from test_workspace_digest_validate import _validate

    from kor_travel_docker_manager.services.ensure_preflight import ensure_preflight
    from kor_travel_docker_manager.services.errors import DeploymentContractError
    from kor_travel_docker_manager.services.postgres_image import (
        declared_postgres_services,
        postgres_image_violations,
    )

    if image is not None:
        (project / ".env").write_text(f"KTDM_POSTGRES_IMAGE={image}\n", encoding="utf-8")
    code, _out, err = _validate(capsys)
    assert (code == 1 and "digest-pinned major" in err) if refused else code == 0, err

    declared = declared_postgres_services(registry.load_targets_config())
    assert declared == {_PG}
    default = compose_default(_compose()["services"][_PG]["image"])
    rendered = {"services": {_PG: {"image": image or default}}}
    no_docker = lambda _command: pytest.fail("이미지 가드는 docker를 부르기 전에 끝난다")  # noqa: E731
    if refused:
        with pytest.raises(DeploymentContractError, match="digest-pinned major"):
            ensure_preflight(rendered, [_PG], {}, run=no_docker, postgres_services=declared)
    else:
        assert postgres_image_violations(rendered["services"], declared) == []


def test_no_service_disables_the_oom_killer():
    # -1000은 커널 OOM killer에서 아예 빠진다(호스트 전체가 대신 죽는다) — 우선순위는 -999까지만.
    for name, service in _compose()["services"].items():
        assert int(service.get("oom_score_adj", 0)) >= -999, name
