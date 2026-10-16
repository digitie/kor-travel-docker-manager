"""T-MGR-NH NH-2: 교차 호스트 compose 간선(R7)·무소유 서비스·plane 없는 합류를 `targets validate`가 거부한다.

세 번째 가상 호스트(mars)로 target을 옮겨 빨강을 만든다. 공용 PG 간선은 빨강을 주장하지 않는다 — PG는 소비 target이
직접 소유해 구성상 같은 호스트다(`docs/multi-host.md` §3.1).
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.host_edges import host_edge_violations
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

ROOT = Path(__file__).resolve().parents[2]


def _documents() -> tuple[dict, dict]:
    read = lambda name: load_yaml_rejecting_duplicate_keys((ROOT / name).read_text(encoding="utf-8"))  # noqa: E731
    return read("docker-compose.yml"), read("config/docker-targets.yml")


def _with_mars(config: dict, *moved: str) -> dict:
    grown = copy.deepcopy(config)
    grown["hosts"]["mars"] = {"addr": "192.0.2.7"}  # TEST-NET-1
    for target in moved:
        grown["targets"][target]["host"] = "mars"
    return grown


def test_the_committed_layout_has_no_violations() -> None:
    compose, config = _documents()
    assert host_edge_violations(compose, config) == []


def test_moving_pinvi_alone_breaks_its_loopback_edge_to_map() -> None:
    # C6c가 PinVi→Map을 `127.0.0.1:12701`로 고정한다 — compose에도 `pinvi-api → kor-travel-map-api`로 있다.
    compose, config = _documents()
    violations = host_edge_violations(compose, _with_mars(config, "pinvi"))
    assert any(line.startswith("pinvi-api (mars) depends_on kor-travel-map-api (n150)") for line in violations), violations


def test_moving_map_alone_breaks_its_edge_to_concierge() -> None:
    # `kor-travel-map-api → kor-travel-concierge-api`가 Map과 concierge를 묶는다.
    compose, config = _documents()
    violations = host_edge_violations(compose, _with_mars(config, "map"))
    assert any(line.startswith("kor-travel-map-api (mars) depends_on kor-travel-concierge-api (n150)")
               for line in violations), violations


def test_moving_the_bound_group_together_passes() -> None:
    # 공용 PG는 소비 target(map·pinvi·conc)이 직접 소유해 mars에도 함께 온다 — PG 간선은 실패하지 않는다(구성상).
    compose, config = _documents()
    assert host_edge_violations(compose, _with_mars(config, "map", "pinvi", "conc")) == []


def test_an_unowned_service_is_refused_and_a_stale_allowance_is_red() -> None:
    compose, config = _documents()
    grown = copy.deepcopy(compose)
    grown["services"]["kor-travel-orphan"] = {"image": "busybox"}
    assert any("kor-travel-orphan is owned by no target" in line for line in host_edge_violations(grown, config))

    shrunk = copy.deepcopy(compose)
    gone = next(iter(config["unowned_services"]))
    del shrunk["services"][gone]
    assert any(f"unowned_services lists {gone}, which compose no longer declares" in line for line in host_edge_violations(shrunk, config))


@pytest.mark.parametrize("value", [["pinvi-dagster"], {"pinvi-dagster": ""}, {"pinvi-dagster": None}])
def test_unowned_services_needs_a_reason_per_name(value: object) -> None:
    compose, config = _documents()
    broken = copy.deepcopy(config)
    broken["unowned_services"] = value
    with pytest.raises(registry.TargetsConfigError, match="unowned_services"):
        host_edge_violations(compose, broken)


def test_init_step_services_count_as_owned() -> None:
    # `dagster`의 init step이 `run`하는 db-init·storage-migrate는 소유자가 있다 — 지우면 무소유로 빨강.
    compose, config = _documents()
    stripped = copy.deepcopy(config)
    stripped["targets"]["dagster"]["init_steps"] = []
    violations = host_edge_violations(compose, stripped)
    assert any("kor-travel-dagster-storage-migrate is owned by no target" in line for line in violations), violations


def test_a_shared_dagster_target_needs_a_plane_on_its_host() -> None:
    compose, config = _documents()
    narrowed = copy.deepcopy(config)
    narrowed["targets"]["dagster"]["host"] = registry.default_host(config)
    violations = host_edge_violations(compose, narrowed)
    moved = sorted(
        t for t, spec in config["targets"].items()
        if (spec.get("dagster") or {}).get("control_plane") == "shared"
        and registry.default_host(config) not in registry.target_hosts(t, config)
    )
    assert moved  # 전제: default가 아닌 호스트에 합류한 target이 있다(오늘 weather·transport)
    for target in moved:
        assert any(line.startswith(f"target {target} joins the shared Dagster plane on") for line in violations), violations


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`test_workspace_digest_validate.py`와 같은 개발 checkout 모양(비운영 — 주소 검사 없음)."""
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config/docker-targets.yml", tmp_path / "config/docker-targets.yml")
    shutil.copytree(ROOT / "config/dagster-shared", tmp_path / "config/dagster-shared")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    for name in (registry.TARGETS_FILE_ENV, registry.HOST_ENV, "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
                 "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE"):
        monkeypatch.delenv(name, raising=False)
    registry.load_targets_config.cache_clear()
    yield tmp_path
    registry.load_targets_config.cache_clear()


def test_targets_validate_refuses_a_cross_host_edge(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from test_workspace_digest_validate import _validate

    code, out, _err = _validate(capsys)
    assert code == 0 and json.loads(out)["status"] == "ok"

    path = project / "config/docker-targets.yml"
    config = load_yaml_rejecting_duplicate_keys(path.read_text(encoding="utf-8"))
    config["targets"]["pinvi"]["host"] = "odroid"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    registry.load_targets_config.cache_clear()
    code, out, err = _validate(capsys)
    assert code == 1 and out == ""
    assert "pinvi-api (odroid) depends_on kor-travel-map-api" in err
