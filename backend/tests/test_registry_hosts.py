"""ADR-58: 원격 Docker 없이 host-local target 집합과 연결 좌표를 파생한다."""

import copy
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import registry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config(monkeypatch, tmp_path):
    value = yaml.safe_load((ROOT / "config/docker-targets.yml").read_text())
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("KTDM_HOST", raising=False)
    return value


@pytest.mark.parametrize("defaults", [0, 2])
def test_exactly_one_default(config, defaults):
    for index, spec in enumerate(config["hosts"].values()):
        spec["default"] = index < defaults
    with pytest.raises(registry.TargetsConfigError, match="exactly one"):
        registry._validate_targets_config(config, label="test")


@pytest.mark.parametrize("addr", ["server.internal", "::1", "10.0.0.999", "", None, 123])
def test_literal_ipv4_only(config, addr):
    config["hosts"]["odroid"]["addr"] = addr
    with pytest.raises(registry.TargetsConfigError, match="IPv4"):
        registry._validate_targets_config(config, label="test")


@pytest.mark.parametrize("host", ["mars", None, ["odroid"], ""])
def test_unknown_target_host(config, host):
    config["targets"]["geo"]["host"] = host
    with pytest.raises(registry.TargetsConfigError, match="host"):
        registry._validate_targets_config(config, label="test")


def test_v2_cannot_omit_hosts(config):
    del config["hosts"]
    with pytest.raises(registry.TargetsConfigError, match="hosts"):
        registry._validate_targets_config(config, label="test")


def test_installed_release_requires_self_identification(config, monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset({config["hosts"]["odroid"]["addr"]}))
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    with pytest.raises(registry.TargetsConfigError, match="KTDM_HOST"):
        registry.current_host(config)
    monkeypatch.setenv("KTDM_HOST", "odroid")
    assert registry.current_host(config) == "odroid"
    monkeypatch.setenv("KTDM_HOST", "mars")
    with pytest.raises(registry.TargetsConfigError, match="KTDM_HOST"):
        registry.current_host(config)


def test_installed_release_rejects_another_hosts_identity(config, monkeypatch, tmp_path):
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.setenv("KTDM_HOST", "odroid")
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset({config["hosts"]["n150"]["addr"]}))
    with pytest.raises(registry.TargetsConfigError, match="not assigned"):
        registry.current_host(config)


def test_legacy_development_fixture_is_local_only(config, monkeypatch, tmp_path):
    del config["hosts"]
    config["version"] = 1
    for spec in config["targets"].values():
        spec.pop("host", None)
    assert registry.current_host(config) == "local"
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.setenv("KTDM_HOST", "local")
    with pytest.raises(registry.TargetsConfigError, match="requires hosts"):
        registry.current_host(config)


def test_all_filters_transitive_remote_dependencies(config, monkeypatch):
    # 실제 registry는 Geo도 이동했다. 이동 전→후 필터 회귀는 fixture로 명시한다.
    config["targets"]["geo"]["host"] = "n150"
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    monkeypatch.setenv("KTDM_HOST", "odroid")
    # Geo 이동 전에도 cadv의 gra 의존도 로컬 all에 끼지 않는다.
    assert set(registry.target_sequence_for_target("all")) == {"storage", "cadv", "dagster", "weather"}
    config["targets"]["geo"]["host"] = "odroid"
    result = registry.target_sequence_for_target("all")
    assert "geo" in result
    assert "prom" not in result and "gra" not in result


def test_connection_tracks_direct_owner_union_and_keeps_raw_config(config, monkeypatch):
    config["targets"]["geo"]["host"] = "odroid"
    config["containers"]["kor-travel-geo-api"]["connection"] = "http://{host}:12501"
    before = copy.deepcopy(config)
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    monkeypatch.setenv("KTDM_HOST", "n150")
    assert registry._managed_containers()["kor-travel-geo-api"]["connection"] == "http://" + config["hosts"]["odroid"]["addr"] + ":12501"
    assert registry.container_hosts("kor-travel-shared-postgresql") == frozenset({"n150", "odroid"})
    monkeypatch.setenv("KTDM_HOST", "odroid")
    assert registry._managed_containers()["kor-travel-geo-api"]["connection"] == "http://127.0.0.1:12501"
    assert config == before


def test_current_tenant_layout_preserves_moved_tenants(config, monkeypatch):
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    assert registry.current_host() == "n150"
    for tenant in ["map", "pinvi", "conc"]:
        assert registry.get_target(tenant)["host"] == "n150"
    for tenant in ["geo", "weather", "transport"]:
        assert registry.get_target(tenant)["host"] == "odroid"


@pytest.mark.parametrize("version", [0, 3, True, "2", None])
def test_unsupported_versions_are_rejected(config, version):
    config["version"] = version
    with pytest.raises(registry.TargetsConfigError, match="version"):
        registry._validate_targets_config(config, label="test")


def test_v1_cannot_inject_host_dimension(config):
    config["version"] = 1
    with pytest.raises(registry.TargetsConfigError, match="version 2"):
        registry._validate_targets_config(config, label="test")


def test_trusted_wheel_does_not_derive_mode_from_project_root(config, monkeypatch, tmp_path):
    from kor_travel_docker_manager.services import trusted_install

    monkeypatch.setattr(trusted_install, "running_from_trusted_install_root", lambda: True)
    monkeypatch.setattr(trusted_install, "TRUSTED_INSTALL_ROOT", tmp_path)
    # 실제 trusted wheel의 기본 get_project_root는 site-packages 상위일 수 있다.
    monkeypatch.delenv(registry.PROJECT_ROOT_ENV, raising=False)
    with pytest.raises(registry.TargetsConfigError, match="requires hosts and KTDM_HOST"):
        registry.current_host(config)
    monkeypatch.setenv("KTDM_HOST", "odroid")
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset({config["hosts"]["n150"]["addr"]}))
    with pytest.raises(registry.TargetsConfigError, match="not assigned"):
        registry.current_host(config)
    # 캐시된 config를 인자로 전달해도 설치 경로 env를 다시 검증한다.
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path / "foreign"))
    with pytest.raises(registry.TargetsConfigError, match="trusted"):
        registry.current_host(config)


def test_one_snapshot_per_mapping_traversal():
    count = 0

    def load():
        nonlocal count
        count += 1
        return {str(i): i for i in range(37)}

    mapping = registry._LazyMapping(load)
    assert len(list(mapping.items())) == 37
    assert count == 1
    assert len(list(mapping.values())) == 37
    assert count == 2


def test_local_address_probe_includes_secondary_ipv4(monkeypatch):
    import json
    import subprocess

    def probe(command, **kwargs):
        assert command == ["/usr/sbin/ip", "-j", "-4", "address", "show"]
        assert kwargs["timeout"] == 5
        data = [{"addr_info": [{"family": "inet", "local": "10.1.1.1"},
                                {"family": "inet", "local": "10.1.1.2"}]}]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data).encode())

    monkeypatch.setattr(subprocess, "run", probe)
    assert registry._local_ipv4_addresses() == frozenset({"10.1.1.1", "10.1.1.2"})

def test_cached_config_rechecks_self_identity(config, monkeypatch, tmp_path):
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.setenv("KTDM_HOST", "n150")
    addresses = {config["hosts"]["n150"]["addr"]}
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset(addresses))
    monkeypatch.setattr(registry, "_load_targets_config_cached", lambda: config)
    assert registry.load_targets_config() is config
    addresses.clear()
    for read in (registry.load_targets_config, lambda: registry.services_for_target("geo"), registry.list_targets):
        with pytest.raises(registry.TargetsConfigError, match="not assigned"):
            read()


def test_target_aggregate_bounds_identity_probes(config, monkeypatch, tmp_path):
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.setenv("KTDM_HOST", "odroid")
    monkeypatch.setattr(registry, "_load_targets_config_cached", lambda: config)
    calls = []
    def addresses():
        calls.append(True)
        return frozenset({config["hosts"]["odroid"]["addr"]})
    monkeypatch.setattr(registry, "_local_ipv4_addresses", addresses)
    for read in (lambda: registry.target_sequence_for_target("all"), lambda: registry.get_target("all"), registry.list_targets,
                 lambda: registry.services_for_target("all"), lambda: registry.runtime_services_for_target("all"),
                 lambda: registry.init_steps_for_target("all"), lambda: registry.service_groups_for_target("all")):
        calls.clear()
        read()
        assert 1 <= len(calls) <= 2

def test_public_target_readers_preserve_case_insensitive_names(config, monkeypatch):
    config["targets"]["GEO"] = config["targets"].pop("geo")
    config["dependency_order"] = ["GEO" if name == "geo" else name for name in config["dependency_order"]]
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    assert registry.resolve_target_name("geo") == "GEO"
    assert registry.get_target(" geo ")["id"] == "GEO"
    sequence = registry.target_sequence_for_target("gEo")
    assert "GEO" in sequence
    assert registry.services_for_target("geo") == registry.get_target("GEO")["resolved_services"]
    assert registry.runtime_services_for_target("geo") == registry.get_target("GEO")["resolved_runtime_services"]
    assert registry.init_steps_for_target("geo") == registry.get_target("GEO")["resolved_init_steps"]
    assert registry.service_groups_for_target("geo")


# --- 2026-10-15 n150: ktdctl은 root `.env`를 로드하지 않는다 -------------------------------
# backend는 `main.py`의 `load_dotenv`로 `.env`의 KTDM_HOST를 프로세스 env에 올리지만
# ktdctl·설치 래퍼·launcher는 그러지 않아, `.env`에 `KTDM_HOST=n150`이 있어도 운영 모드의
# 모든 ktdctl 명령이 "requires hosts and KTDM_HOST"로 죽었다(#477 리뷰 차단 2번).
# 아래는 CLI 진입점(`main`)에서 실제 targets 문서·운영 표지·root `.env`로 확인한다.


@pytest.fixture
def operating_cli(monkeypatch, tmp_path):
    from kor_travel_docker_manager.services import trusted_install

    monkeypatch.setattr(trusted_install, "running_from_trusted_install_root", lambda: False)
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(registry.TARGETS_FILE_ENV, str(ROOT / "config/docker-targets.yml"))
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", raising=False)
    monkeypatch.delenv(registry.HOST_ENV, raising=False)
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    addresses = yaml.safe_load((ROOT / "config/docker-targets.yml").read_text())["hosts"]
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset({addresses["odroid"]["addr"]}))
    registry.load_targets_config.cache_clear()
    yield tmp_path
    registry.load_targets_config.cache_clear()


def _validate_json(capsys):
    import json

    from kor_travel_docker_manager.cli import main

    try:
        code = main(["targets", "validate", "--json"])
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else None), captured.err


def test_cli_reads_host_from_root_env_file(operating_cli, capsys):
    (operating_cli / ".env").write_text("OTHER=x\nKTDM_HOST=odroid\n")
    code, document, _ = _validate_json(capsys)
    assert code == 0
    assert document["host"] == "odroid"


def test_cli_rejects_host_missing_from_env_and_root_env_file(operating_cli, capsys):
    (operating_cli / ".env").write_text("OTHER=x\nKTDM_HOST=\n")
    code, _, err = _validate_json(capsys)
    assert code != 0
    assert "requires hosts and KTDM_HOST" in err


def test_cli_process_env_overrides_root_env_file(operating_cli, monkeypatch, capsys):
    # `.env`의 값은 로컬이 아닌 호스트다. 프로세스 env가 이기면 통과, `.env`가 이기면 거부된다.
    (operating_cli / ".env").write_text("KTDM_HOST=n150\n")
    monkeypatch.setenv(registry.HOST_ENV, "odroid")
    code, document, _ = _validate_json(capsys)
    assert code == 0
    assert document["host"] == "odroid"
    monkeypatch.delenv(registry.HOST_ENV)
    code, _, err = _validate_json(capsys)
    assert code != 0
    assert "not assigned" in err


@pytest.mark.parametrize("value", ["mars", "all", "ODROID"])
def test_cli_rejects_invalid_host_in_root_env_file(operating_cli, capsys, value):
    (operating_cli / ".env").write_text(f"KTDM_HOST={value}\n")
    code, _, err = _validate_json(capsys)
    assert code != 0
    assert "KTDM_HOST must be a declared hosts key" in err


def test_trusted_release_reads_installed_root_env_file(config, monkeypatch, tmp_path):
    from kor_travel_docker_manager.services import trusted_install

    install = tmp_path / "opt"
    install.mkdir()
    monkeypatch.setattr(trusted_install, "running_from_trusted_install_root", lambda: True)
    monkeypatch.setattr(trusted_install, "TRUSTED_INSTALL_ROOT", install)
    monkeypatch.delenv(registry.PROJECT_ROOT_ENV, raising=False)
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset({config["hosts"]["n150"]["addr"]}))
    # 설치 root 밖의 `.env`(env-file override)는 trusted 자기 식별의 근거가 아니다.
    foreign = tmp_path / "foreign.env"
    foreign.write_text("KTDM_HOST=n150\n")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(foreign))
    with pytest.raises(registry.TargetsConfigError, match="requires hosts and KTDM_HOST"):
        registry.current_host(config)
    (install / ".env").write_text("KTDM_HOST=n150\n")
    assert registry.current_host(config) == "n150"
