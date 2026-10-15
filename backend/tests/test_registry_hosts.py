"""ADR-58: 원격 Docker 없이 host-local target 집합과 연결 좌표를 파생한다."""

import copy
import os
import sys
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

ROOT = Path(__file__).resolve().parents[2]


def _load_registry() -> dict:
    # 실제 로더와 같은 중복 키 거부 로더 — safe_load는 중복 블록의 마지막만 보고 초록이 된다(#477 리뷰 1번).
    return load_yaml_rejecting_duplicate_keys((ROOT / "config/docker-targets.yml").read_text(encoding="utf-8"))


# 주소 probe 캐시는 conftest의 autouse `_fresh_local_address_probe`가 테스트마다 비운다.


@pytest.fixture
def config(monkeypatch, tmp_path):
    value = _load_registry()
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


def test_missing_hosts_is_one_implicit_default_host(config):
    # `hosts:` 없음 = 암묵 default 하나(#477 리뷰 3번). 버전 값과 무관하다.
    del config["hosts"]
    for spec in config["targets"].values():
        if spec.get("host") != "all":
            spec.pop("host", None)
    for version in (1, 2, None):
        config["version"] = version
        registry._validate_targets_config(config, label="test")
        assert registry.current_host(config) == registry.IMPLICIT_HOST == registry.default_host(config)
    config["targets"]["geo"]["host"] = "odroid"
    with pytest.raises(registry.TargetsConfigError, match="unknown host"):
        registry._validate_targets_config(config, label="test")


@pytest.mark.parametrize("version", [1, 2, None, "anything"])
def test_host_dimension_is_not_gated_by_version(config, version):
    config["version"] = version
    registry._validate_targets_config(config, label="test")
    assert registry.target_hosts("geo", config) == frozenset({"odroid"})


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


def test_operating_release_does_not_accept_the_implicit_host(config, monkeypatch, tmp_path):
    del config["hosts"]
    for spec in config["targets"].values():
        spec.pop("host", None)
    assert registry.current_host(config) == registry.IMPLICIT_HOST
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.setenv("KTDM_HOST", registry.IMPLICIT_HOST)
    with pytest.raises(registry.TargetsConfigError, match="requires hosts"):
        registry.current_host(config)


def test_all_filters_transitive_remote_dependencies(config, monkeypatch):
    # 실제 registry는 Geo도 이동했다. 이동 전→후 필터 회귀는 fixture로 명시한다.
    config["targets"]["geo"]["host"] = "n150"
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    monkeypatch.setenv("KTDM_HOST", "odroid")
    # Geo 이동 전에도 cadv의 gra 의존도 로컬 all에 끼지 않는다. 공용 plane(dagster)은 default 호스트뿐이다.
    assert set(registry.target_sequence_for_target("all")) == {"storage", "cadv", "weather"}
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

def _fake_ip(monkeypatch, addresses):
    """`/usr/sbin/ip` subprocess만 바꾼다 — 프로세스 캐시(`_local_ipv4_addresses`)는 진짜를 쓴다."""
    import json
    import subprocess

    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        data = [{"addr_info": [{"family": "inet", "local": address} for address in sorted(addresses)]}]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data).encode())

    monkeypatch.setattr(subprocess, "run", probe)
    return calls


def test_cached_config_rechecks_identity_but_probes_addresses_once(config, monkeypatch, tmp_path):
    """의도(2026-10-16 변경): **자기 식별(`KTDM_HOST`·`.env`)은 매 조회 다시 읽고, 인터페이스 주소는 프로세스당
    한 번**이다(#477 리뷰 5번 — `_LazyMapping` 단건 조회마다 `ip` subprocess가 떴다). 종전 이 테스트는 주소 집합까지
    매 조회 재검증을 고정했다. 주소는 고정 LAN 주소이고 바뀌면 재기동으로 반영된다(`_local_ipv4_addresses` docstring)."""
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.setenv("KTDM_HOST", "n150")
    calls = _fake_ip(monkeypatch, {config["hosts"]["n150"]["addr"]})
    monkeypatch.setattr(registry, "_load_targets_config_cached", lambda: config)
    assert registry.load_targets_config() is config
    for _ in range(50):
        registry.MANAGED_CONTAINERS["kor-travel-map-api"]
        registry.services_for_target("geo")
    registry.list_targets()
    assert len(calls) == 1
    # 이름은 매 조회 다시 본다 — 바꾸면 다음 조회가 거부한다.
    monkeypatch.setenv("KTDM_HOST", "odroid")
    for read in (registry.load_targets_config, lambda: registry.services_for_target("geo"), registry.list_targets):
        with pytest.raises(registry.TargetsConfigError, match="not assigned"):
            read()
    assert len(calls) == 1


def test_failed_address_probe_is_not_cached(monkeypatch):
    import subprocess

    def broken(command, **kwargs):
        raise OSError("no ip")

    monkeypatch.setattr(subprocess, "run", broken)
    with pytest.raises(registry.TargetsConfigError, match="could not verify"):
        registry._local_ipv4_addresses()
    _fake_ip(monkeypatch, {"10.9.9.9"})
    assert registry._local_ipv4_addresses() == frozenset({"10.9.9.9"})


def test_self_identification_requires_exactly_one_local_declared_host(config, monkeypatch, tmp_path):
    # 보조 IPv4/VIP로 선언 주소 둘이 이 장비에 있으면 어느 이름이든 "로컬"이다 — 이름을 고를 근거가 없다.
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    both = {spec["addr"] for spec in config["hosts"].values()}
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset(both))
    for name in config["hosts"]:
        monkeypatch.setenv("KTDM_HOST", name)
        with pytest.raises(registry.TargetsConfigError, match="more than one declared host"):
            registry.current_host(config)
    monkeypatch.setattr(registry, "_local_ipv4_addresses", lambda: frozenset({config["hosts"]["odroid"]["addr"]}))
    monkeypatch.setenv("KTDM_HOST", "odroid")
    assert registry.current_host(config) == "odroid"


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
    addresses = _load_registry()["hosts"]
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


_ROOT_USER = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.skipif(_ROOT_USER, reason="root는 0000 파일도 읽는다")
def test_cli_names_unreadable_root_env_file_instead_of_missing_host(operating_cli, monkeypatch, capsys):
    # n150의 root `.env`는 root:root 0600이고 백업 cron은 비-root 계정이다. 빈 값으로 넘기면
    # "requires KTDM_HOST"가 나와, `.env`에 값이 있는 운영자를 엉뚱한 곳으로 보낸다.
    env_file = operating_cli / ".env"
    env_file.write_text("SECRET_TOKEN=do-not-print\nKTDM_HOST=odroid\n")
    env_file.chmod(0)
    try:
        code, _, err = _validate_json(capsys)
        assert code != 0
        assert "Manager root .env is unreadable by this user" in err
        assert str(env_file) in err
        assert "set KTDM_HOST in the process environment" in err
        assert "do-not-print" not in err and "requires hosts" not in err
        # 안내대로 프로세스 env에 두면 파일을 읽지 않고 통과한다.
        monkeypatch.setenv(registry.HOST_ENV, "odroid")
        code, document, _ = _validate_json(capsys)
        assert code == 0 and document["host"] == "odroid"
    finally:
        env_file.chmod(0o600)


@pytest.mark.skipif(_ROOT_USER, reason="root는 0000 파일도 읽는다")
def test_development_checkout_ignores_unreadable_root_env_file(config, monkeypatch, tmp_path):
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("KTDM_HOST=odroid\n")
    env_file.chmod(0)
    try:
        assert registry.current_host(config) == registry.default_host(config)
    finally:
        env_file.chmod(0o600)


# --- 2026-10-16 T-MGR-01a-F: #477 리뷰 잔여 ---------------------------------------------------


def test_multi_owner_remote_container_is_a_config_error_not_the_first_name(config):
    # 종전: `sorted(owners)[0]` — 두 원격 호스트가 가진 컨테이너가 이름순 첫 호스트 주소로 조용히 풀렸다.
    config["hosts"]["mars"] = {"addr": "192.0.2.7"}
    config["targets"]["conc"]["host"] = "mars"
    config["targets"]["map"]["host"] = "odroid"
    # map-api를 map(odroid)와 conc(mars) 둘의 실행 service로 만든다 — n150에서 보면 주소가 둘이다.
    config["targets"]["conc"]["services"] = [*config["targets"]["conc"]["services"], "kor-travel-map-api"]
    _cut_cross_host_edges(config)
    with pytest.raises(registry.TargetsConfigError, match="kor-travel-map-api.*no single address.*mars, odroid"):
        registry._validate_targets_config(config, label="test")


def test_unowned_container_follows_the_target_that_lists_it(config, monkeypatch):
    # 옛 profile 컨테이너(weather webserver·gateway)는 실행 service가 아니다. 종전에는 loopback으로 그려
    # 원격(odroid) weather의 옛 gateway가 n150에 있는 것처럼 보였다.
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    monkeypatch.setenv("KTDM_HOST", "n150")
    assert registry.container_hosts("kor-travel-weather-dagster-gateway", config) == frozenset({"odroid"})
    odroid = config["hosts"]["odroid"]["addr"]
    assert registry._managed_containers()["kor-travel-weather-dagster-gateway"]["connection"] == f"http://{odroid}:14102"
    monkeypatch.setenv("KTDM_HOST", "odroid")
    assert registry._managed_containers()["kor-travel-weather-dagster-gateway"]["connection"] == "http://127.0.0.1:14102"


def test_container_in_no_target_cannot_render_a_host(config):
    config["containers"]["orphan"] = {
        **config["containers"]["kor-travel-map-api"], "compose_service": "orphan-service", "name": "orphan",
    }
    with pytest.raises(registry.TargetsConfigError, match="orphan.*no target"):
        registry._validate_targets_config(config, label="test")
    config["containers"]["orphan"]["connection"] = "내부 전용 — 포트 없음"
    registry._validate_targets_config(config, label="test")


@pytest.mark.parametrize(
    "connection",
    ["내부 전용({host}:14107) — loopback만", "{host}:11000", "postgresql://x@{host}:1", "http:/{host}:1"],
)
def test_host_placeholder_only_as_url_host(config, connection):
    config["containers"]["kor-travel-map-api"]["connection"] = connection
    with pytest.raises(registry.TargetsConfigError, match="only allowed as a URL host"):
        registry._validate_targets_config(config, label="test")


def test_loopback_only_descriptions_stay_loopback_from_every_host(config, monkeypatch):
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    seen = 0
    for host in config["hosts"]:
        monkeypatch.setenv("KTDM_HOST", host)
        for name, spec in registry._managed_containers().items():
            connection = str(spec["connection"])
            if "loopback" in connection or "내부 전용" in connection:
                seen += 1
                for other in config["hosts"].values():
                    assert other["addr"] not in connection, (host, name, connection)
    assert seen >= 3


def test_shared_dagster_plane_is_on_the_default_host_only(config):
    # `host: all`이면 다른 호스트의 `ensure all`이 workspace 없는 두 번째 plane을 띄운다(#477 리뷰 8번).
    assert registry.target_hosts("dagster", config) == frozenset({registry.default_host(config)})


@pytest.mark.parametrize("kind", ["directory", "fifo", "latin1", "dotenv"])
def test_operating_mode_names_an_unusable_root_env_file(config, monkeypatch, tmp_path, kind):
    (tmp_path / ".ktdm-source-revision").write_text("a" * 40)
    monkeypatch.delenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", raising=False)
    env_file = tmp_path / ".env"
    if kind == "directory":
        env_file.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("mkfifo 없음")
        os.mkfifo(env_file)
    elif kind == "latin1":
        env_file.write_bytes("KTDM_HOST=n150\nNOTE=café\n".encode("latin-1"))
    else:
        env_file.write_text("KTDM_HOST=n150\n")

        def broken(**_):
            raise ValueError("bad")

        monkeypatch.setattr("dotenv.dotenv_values", broken)
    with pytest.raises(
        registry.RootEnvUnreadableError,
        match=r"Manager root \.env is .*set KTDM_HOST in the process environment",
    ):
        registry.current_host(config)
    # 개발 checkout은 종전처럼 "값 없음" → default.
    (tmp_path / ".ktdm-source-revision").unlink()
    assert registry.current_host(config) == registry.default_host(config)


def _cut_cross_host_edges(config) -> None:
    """R7: target을 옮기면 그 target에 닿는 교차 호스트 간선을 끊는다(모든 호스트에 있는 infra로 가는 간선은 남는다)."""
    for name, spec in config["targets"].items():
        spec["depends_on"] = [
            d for d in spec.get("depends_on") or []
            if registry.target_hosts(name, config) <= registry.target_hosts(d, config)
        ]


def _derived(config, monkeypatch, host):
    """한 호스트에서 본 런타임 파생물 전부: 연결 좌표(R1)·`all` 필터·target 호스트."""
    monkeypatch.setattr(registry, "load_targets_config", lambda: config)
    monkeypatch.setenv("KTDM_HOST", host)
    return {
        "connections": {name: spec["connection"] for name, spec in registry._managed_containers().items()},
        "all": registry.target_sequence_for_target("all"),
        "hosts": {target["id"]: target["host"] for target in registry.list_targets()},
    }


def test_third_virtual_host_derives_without_code_changes(config, monkeypatch):
    """T-MGR-NH 수용 기준의 **오늘 있는 런타임 파생물(R1 연결 좌표·`all` 필터·target host)** 부분: 세 번째 호스트를
    더하고 target 하나를 옮기면 그 셋이 새 호스트를 반영하고, 옮긴 target이 혼자 갖지 않은 것은 원래 두 호스트에서 byte
    불변이다. 코드는 그대로 — registry만 바꾼다. 커밋된 파생물(workspace·R2 소비자 기본값·Prometheus)은 아직 registry에서
    계산되지 않아 여기서 보지 않는다(T-MGR-02·01c·04)."""
    import json

    before = {host: _derived(config, monkeypatch, host) for host in list(config["hosts"])}
    moved = "conc"
    mars = "192.0.2.7"  # TEST-NET-1
    config["hosts"]["mars"] = {"addr": mars}
    config["targets"][moved]["host"] = "mars"
    _cut_cross_host_edges(config)  # 플립 PR이 하는 일(R7, T-MGR-01b)
    registry._validate_targets_config(config, label="test")
    after = {host: _derived(config, monkeypatch, host) for host in config["hosts"]}

    moved_containers = {
        name for name in config["containers"] if registry.container_hosts(name, config) == frozenset({"mars"})
    }
    assert moved_containers  # 옮긴 target이 혼자 가진 컨테이너가 있어야 이 테스트가 무언가를 본다

    def dump(connections):
        kept = {name: value for name, value in connections.items() if name not in moved_containers}
        return json.dumps(kept, ensure_ascii=False, sort_keys=True)

    for host in before:
        assert dump(after[host]["connections"]) == dump(before[host]["connections"])
        for name in moved_containers:
            old, new = str(before[host]["connections"][name]), str(after[host]["connections"][name])
            if "://" in old:
                assert f"://{mars}:" in new, (host, name, new)
        assert after[host]["all"] == [name for name in before[host]["all"] if name != moved]
        assert after[host]["hosts"] == {**before[host]["hosts"], moved: "mars"}
    for name in moved_containers:
        new = str(after["mars"]["connections"][name])
        assert "://" not in new or "://127.0.0.1:" in new, (name, new)
    # mars의 all = 모든 호스트에 있는 infra + 옮긴 target.
    everywhere = {t for t, spec in config["targets"].items() if spec.get("host") == "all" and not spec.get("include")}
    assert set(after["mars"]["all"]) == {*everywhere, moved}


#: 호스트 리터럴 검사의 **범위**: `backend/src`와 `scripts/`의 Python(확장자 `.py` 또는 python shebang) 문자열 상수
#: (f-string 조각 포함, docstring 제외)와 `config/prometheus/prometheus.yml`의 주석 아닌 줄. 범위 밖(명시): 셸 스크립트
#: (`scripts/*.sh`·bash shebang — 주석·사람용 안내가 대부분이라 단어 검사가 잡음뿐이다), `docker-compose.yml`(R2 소비자
#: 기본값은 커밋된 파생물 — `test_consumer_origin_defaults.py`가 registry와 대조한다), 문서·주석.
#:
#: 허용 목록은 (파일, 리터럴) 정확 일치다 — 같은 파일에 새 리터럴이 생기면 빨갛다. 항목이 더 이상 보이지 않아도 빨갛다
#: (낡은 허용이 남지 않게).
_HOST_LITERAL_ALLOWED: dict[tuple[str, str], str] = {
    ("scripts/m05_isolated_e2e.py", "scripts/n150-playwright-runner.sh"):
        "PinVi 저장소의 파일 이름이다(호스트 선택이 아니다).",
    ("config/prometheus/prometheus.yml", "- job_name: cadvisor-odroid"):
        "잠정 odroid exporter job(ADR-58 잠정 시행 ⅵ) — T-MGR-04가 registry 파생물(B-8)로 대체한다.",
    ("config/prometheus/prometheus.yml", "- 192.168.1.13:12103"): "같은 잠정 job(T-MGR-04).",
    ("config/prometheus/prometheus.yml", "host: odroid"): "같은 잠정 job들의 label(T-MGR-04).",
    ("config/prometheus/prometheus.yml", "- job_name: node-odroid"): "같은 잠정 job(T-MGR-04).",
    ("config/prometheus/prometheus.yml", "- 192.168.1.13:12106"): "같은 잠정 job(T-MGR-04).",
}


def _python_sources() -> list[Path]:
    files = sorted((ROOT / "backend/src").rglob("*.py"))
    for path in sorted((ROOT / "scripts").rglob("*")):
        if not path.is_file():
            continue
        head = path.read_bytes()[:80]
        if path.suffix == ".py" or (not path.suffix and head.startswith(b"#!") and b"python" in head.split(b"\n")[0]):
            files.append(path)
    return files


def _string_constants(path: Path) -> list[tuple[int, str]]:
    """docstring을 뺀 문자열 상수. f-string의 리터럴 조각도 `ast.Constant`로 나온다."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def _host_literal_hits(hosts: dict) -> list[tuple[str, int, str]]:
    import re

    names = "|".join(re.escape(name) for name in hosts)
    word = re.compile(rf"(?<![A-Za-z0-9_])(?:{names})(?![A-Za-z0-9_])")
    addresses = [spec["addr"] for spec in hosts.values()]

    def carries(text: str) -> bool:
        return word.search(text) is not None or any(address in text for address in addresses)

    hits = []
    for path in _python_sources():
        relative = path.relative_to(ROOT).as_posix()
        hits.extend((relative, line, value) for line, value in _string_constants(path) if carries(value))
    prometheus = ROOT / "config/prometheus/prometheus.yml"
    for line, text in enumerate(prometheus.read_text(encoding="utf-8").splitlines(), start=1):
        code = text.split("#", 1)[0].strip()
        if code and carries(code):
            hits.append(("config/prometheus/prometheus.yml", line, code))
    return hits


def test_code_has_no_host_name_literals():
    """T-MGR-NH: 호스트 이름·주소는 registry에만 — 코드가 들고 있으면 호스트를 더할 때 코드를 고친다. 범위·허용은
    `_HOST_LITERAL_ALLOWED` 주석."""
    hits = _host_literal_hits(_load_registry()["hosts"])
    unexpected = [f"{path}:{line}: {value[:80]!r}" for path, line, value in hits if (path, value) not in _HOST_LITERAL_ALLOWED]
    assert unexpected == []
    seen = {(path, value) for path, _, value in hits}
    assert set(_HOST_LITERAL_ALLOWED) <= seen, sorted(set(_HOST_LITERAL_ALLOWED) - seen)


def test_host_literal_scan_sees_words_fstrings_and_skips_docstrings(tmp_path, monkeypatch):
    # 검사기 자체를 빨갛게: 단어 안·f-string 조각·주소는 잡고, docstring·다른 단어의 일부는 넘긴다.
    source = tmp_path / "probe.py"
    source.write_text(
        '"""모듈 설명 n150."""\n'
        'def f(x):\n'
        '    """함수 설명 odroid."""\n'
        '    a = f"ssh {x}@n150 now"\n'
        '    b = "target on odroid"\n'
        '    c = "http://10.0.0.9:1"\n'
        '    d = "n1500 xodroid odroid_x"\n',
        encoding="utf-8",
    )
    found = [value for _, value in _string_constants(source)]
    hosts = {"n150": {"addr": "10.0.0.1"}, "odroid": {"addr": "10.0.0.9"}}
    monkeypatch.setattr(sys.modules[__name__], "_python_sources", lambda: [source])
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    (tmp_path / "config/prometheus").mkdir(parents=True)
    (tmp_path / "config/prometheus/prometheus.yml").write_text("# n150 주석\n- 10.0.0.1:9\n", encoding="utf-8")
    hits = {(path, value) for path, _, value in _host_literal_hits(hosts)}
    assert "모듈 설명 n150." not in found and "함수 설명 odroid." not in found
    assert hits == {
        ("probe.py", "@n150 now"),
        ("probe.py", "target on odroid"),
        ("probe.py", "http://10.0.0.9:1"),
        ("config/prometheus/prometheus.yml", "- 10.0.0.1:9"),
    }
