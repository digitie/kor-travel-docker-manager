"""호스트 Compose 보간 경계는 선택 서비스의 검증만 좁힌다."""
from __future__ import annotations

import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import compose_service as module
from kor_travel_docker_manager.services.c6c_deployment import ComposeCandidateContractError


@pytest.fixture
def authority(monkeypatch):
    owners = {"app": frozenset({"local"}), "db": frozenset({"local"}),
              "other": frozenset({"remote"})}
    monkeypatch.setattr(module, "_scope_service_owners", lambda: ("local", owners))
    monkeypatch.setattr(module, "_candidate_release_scope_owners", lambda root: ("local", owners))
    return owners


def document():
    return {
        "name": "scope-regression",
        "x-unused": {"value": "${OFFHOST_ONLY:?missing offhost}"},
        "services": {
            "app": {"image": "busybox", "depends_on": ["db"],
                    "environment": {"REQUIRED": "${LOCAL_ONLY:?missing local}"},
                    "volumes": ["data:/data"], "secrets": ["selected"]},
            "db": {"image": "busybox"},
            "other": {"image": "busybox", "environment": {
                "REQUIRED": "${OFFHOST_ONLY:?missing offhost}"},
                "env_file": ["/not-read/offhost.env"]},
        },
        "volumes": {"data": {}, "unused": {"name": "${UNUSED_VOLUME:?missing}"}},
        "secrets": {"selected": {"environment": "LOCAL_ONLY"},
                    "unused": {"environment": "OFFHOST_ONLY"}},
    }


def test_projection_closes_dependencies_and_only_reachable_resources(authority):
    original = document()
    before = deepcopy(original)
    scope = module._compose_service_scope(original, ("app",), {"LOCAL_ONLY": "synthetic"})
    result = module._project_compose_service_scope(original, scope, {"LOCAL_ONLY": "synthetic"})
    assert scope == ("app", "db")
    assert set(result["services"]) == {"app", "db"}
    assert result["volumes"] == {"data": {}}
    assert result["secrets"] == {"selected": {"environment": "LOCAL_ONLY"}}
    assert "x-unused" not in result
    assert original == before


@pytest.mark.parametrize("field,value", [
    ("links", []), ("external_links", None), ("volumes_from", []), ("extends", {}),
    ("build", {"additional_contexts": {}}),
])
def test_unsupported_reference_fields_are_rejected_even_empty(authority, field, value):
    candidate = document()
    candidate["services"]["app"][field] = value
    with pytest.raises(ComposeCandidateContractError, match="unsupported"):
        module._compose_service_scope(candidate, ("app",), {})


@pytest.mark.parametrize("key", ["network_mode", "ipc", "pid"])
@pytest.mark.parametrize("value", ["service:other", "service:unknown", "container:foreign"])
def test_namespace_references_cannot_escape_host(authority, key, value):
    candidate = document()
    candidate["services"]["app"][key] = "${NAMESPACE}"
    with pytest.raises(ComposeCandidateContractError):
        module._compose_service_scope(candidate, ("app",), {"NAMESPACE": value})


def test_unknown_dependency_is_rejected_before_external_inputs(authority, monkeypatch):
    candidate = document()
    candidate["services"]["app"]["depends_on"] = ["unknown"]
    monkeypatch.setattr(module, "_capture_compose_external_input_snapshot",
                        lambda *a, **k: pytest.fail("external file read before closure"))
    with pytest.raises(ComposeCandidateContractError, match="local owner"):
        module._compose_service_scope(candidate, ("app",), {})


def test_resolved_namespace_is_rechecked(authority):
    resolved = {"services": {"app": {"network_mode": "service:other"}}}
    with pytest.raises(ComposeCandidateContractError):
        module._validate_resolved_service_scope(resolved, ("app", "db"))


@pytest.mark.parametrize("network_mode,explicit,expected", [
    (None, None, {"default"}), ("host", None, set()),
    (None, ["selected"], {"selected"}), (None, [], {"default"}),
    (None, {}, {"default"}),
])
def test_only_implicit_default_network_is_retained(authority, network_mode, explicit, expected):
    candidate = {"services": {"app": {"image": "busybox"}},
                 "networks": {"default": {}, "selected": {}}}
    spec = candidate["services"]["app"]
    if network_mode is not None:
        spec["network_mode"] = network_mode
    if explicit is not None:
        spec["networks"] = explicit
    result = module._project_compose_service_scope(candidate, ("app",), {})
    assert set(result["networks"]) == expected


def _resolve(service, candidate, snapshot):
    external = module._capture_compose_external_input_snapshot(
        candidate, environment_snapshot=snapshot, environment_override=None)
    return service._resolve_compose_candidate_unlocked(
        candidate, environment=snapshot.effective, expected_system_bind_snapshots=(),
        environment_snapshot=snapshot, environment_override=None,
        external_input_snapshot=external)


def _docker_compose():
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True,
                              timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _docker_compose(), reason="Docker Compose 없음")
def test_actual_compose_selected_required_and_offhost_file_boundary(authority, tmp_path):
    candidate = document()
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(candidate))
    env_path = tmp_path / ".env"
    env_path.write_text("LOCAL_ONLY=synthetic\n")
    snapshot = module._capture_compose_environment_snapshot(
        environment_override=None, env_path=env_path, compose_path=path,
        override_path=tmp_path / "docker-compose.override.yml",
        include_process_environment=False, interpolate_env_file=False)
    service = module.ComposeService()
    scope = module._compose_service_scope(candidate, ("app",), snapshot.effective)
    selected = module._project_compose_service_scope(candidate, scope, snapshot.effective)
    resolved = _resolve(service, selected, snapshot)
    module._validate_resolved_service_scope(resolved, scope)
    assert set(resolved["services"]) == {"app", "db"}
    # 같은 전체 문서는 offhost env_file을 실제로 읽으려 하므로 통과하지 못한다.
    with pytest.raises(ComposeCandidateContractError):
        _resolve(service, candidate, snapshot)
    missing = module._capture_compose_environment_snapshot(
        environment_override={"LOCAL_ONLY": ""}, env_path=env_path, compose_path=path,
        override_path=tmp_path / "docker-compose.override.yml",
        include_process_environment=False, interpolate_env_file=False)
    with pytest.raises(ComposeCandidateContractError):
        _resolve(service, selected, missing)

def test_install_preflight_all_local_roots_not_pair_and_candidate_root_only(authority, tmp_path, monkeypatch):
    candidate = {"services": {"app": {"image": "busybox"},
                              "db": {"image": "busybox"},
                              "other": {"image": "busybox",
                                        "env_file": ["/must-not-read/offhost.env"]}}}
    (tmp_path / "docker-compose.yml").write_text(yaml.safe_dump(candidate))
    (tmp_path / ".env").write_text("LOCAL_ONLY=synthetic\n")
    monkeypatch.setattr(module, "get_project_root", lambda: str(tmp_path))
    # ambient compose/env/override를 받지 않고 candidate root의 세 파일을 고정한다.
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE", "/must-not-read/compose.yml")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", "/must-not-read/.env")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE", "/must-not-read/override.yml")
    observed = []
    def resolve(self, selected, **kwargs):
        observed.append((selected, kwargs))
        return selected
    monkeypatch.setattr(module.ComposeService, "_resolve_compose_candidate_unlocked", resolve)
    monkeypatch.setattr(module, "validate_compose_candidate_protected_values",
                        lambda *a, **k: pytest.fail("installer must remain config-only"))
    module.ComposeService().validate_host_compose_preflight()
    selected, kwargs = observed.pop()
    assert set(selected["services"]) == {"app", "db"}
    assert kwargs["expected_system_bind_snapshots"] == ()
    snapshot = kwargs["environment_snapshot"]
    assert snapshot.compose_path == str(tmp_path / "docker-compose.yml")
    assert snapshot.env_path == str(tmp_path / ".env")
    assert snapshot.override_path == str(tmp_path / "docker-compose.override.yml")


@pytest.mark.parametrize("drift", ["source", "environment", "external"])
def test_install_preflight_revalidates_source_environment_and_selected_files(
    authority, tmp_path, monkeypatch, drift,
):
    selected_env = tmp_path / "selected.env"
    selected_env.write_text("VALUE=synthetic\n")
    candidate = {"services": {
        "app": {"image": "busybox", "env_file": ["selected.env"]},
        "db": {"image": "busybox"}}}
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(candidate))
    root_env = tmp_path / ".env"
    root_env.write_text("LOCAL_ONLY=synthetic\n")
    monkeypatch.setattr(module, "get_project_root", lambda: str(tmp_path))
    def resolve(self, selected, **kwargs):
        if drift == "source":
            path.write_text(path.read_text() + "\n# changed\n")
        elif drift == "environment":
            root_env.write_text("LOCAL_ONLY=changed\n")
        else:
            selected_env.write_text("VALUE=changed\n")
        return {"services": {"app": {"image": "busybox"}, "db": {"image": "busybox"}}}
    monkeypatch.setattr(module.ComposeService, "_resolve_compose_candidate_unlocked", resolve)
    with pytest.raises(ComposeCandidateContractError):
        module.ComposeService().validate_host_compose_preflight()

@pytest.mark.skipif(not _docker_compose(), reason="Docker Compose 없음")
@pytest.mark.parametrize("networks", [[], {}])
def test_actual_compose_empty_networks_keeps_default_required_gate(
    authority, tmp_path, networks,
):
    candidate = {"name": "default-scope-regression",
                 "services": {"app": {"image": "busybox", "networks": networks}},
                 "networks": {"default": {"name": "${DEFAULT_NETWORK:?required default}"}}}
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(candidate))
    env_path = tmp_path / ".env"
    env_path.write_text("DEFAULT_NETWORK=synthetic-default\n")
    snapshot = module._capture_compose_environment_snapshot(
        environment_override=None, env_path=env_path, compose_path=path,
        override_path=tmp_path / "docker-compose.override.yml",
        include_process_environment=False, interpolate_env_file=False)
    projected = module._project_compose_service_scope(candidate, ("app",), snapshot.effective)
    actual = _resolve(module.ComposeService(), projected, snapshot)
    full = _resolve(module.ComposeService(), candidate, snapshot)
    assert actual["services"]["app"]["networks"] == full["services"]["app"]["networks"]
    assert actual["networks"] == full["networks"]
    missing = module._capture_compose_environment_snapshot(
        environment_override={"DEFAULT_NETWORK": ""}, env_path=env_path, compose_path=path,
        override_path=tmp_path / "docker-compose.override.yml",
        include_process_environment=False, interpolate_env_file=False)
    with pytest.raises(ComposeCandidateContractError):
        _resolve(module.ComposeService(), projected, missing)

@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.parametrize("cli_only", [True, False])
def test_installer_modes_use_candidate_isolated_python_and_sanitize_failure(
    tmp_path, accepted, cli_only,
):
    import os

    release = tmp_path / "candidate"
    python = release / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True)
    marker = tmp_path / "observed"
    python.write_text(
        "#!/bin/bash\n"
        'test "$1" = -I || exit 10\n'
        'test "$2" = -c || exit 11\n'
        'test "$KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT" = "$EXPECTED_REL" || exit 12\n'
        'test -z "${KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE+x}" || exit 13\n'
        'test -z "${KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE+x}" || exit 14\n'
        'test -z "${KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE+x}" || exit 15\n'
        'printf called > "$MARKER"\n'
        + ('exit 0\n' if accepted else 'echo do-not-leak-sensitive-value >&2; exit 1\n')
    )
    python.chmod(0o700)
    installer = Path(__file__).resolve().parents[2] / "scripts/install-ktdm-trusted-release"
    text = installer.read_text()
    block = text[text.index("# >>> preflight"):text.index("# <<< preflight")]
    script = (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        'REL="$EXPECTED_REL"\n' + f"CLI_ONLY={str(cli_only).lower()}\n" + block + "\necho FLIPPABLE\n"
    )
    env = dict(os.environ, EXPECTED_REL=str(release), MARKER=str(marker),
               KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE="/ambient/.env",
               KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE="/ambient/compose.yml",
               KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE="/ambient/override.yml")
    result = subprocess.run(["bash", "-c", script], env=env,
                            text=True, capture_output=True, check=False)
    assert marker.read_text() == "called"
    assert result.returncode == (0 if accepted else 126)
    assert ("FLIPPABLE" in result.stdout) == accepted
    assert "do-not-leak-sensitive-value" not in result.stdout + result.stderr
    if not accepted:
        assert "not flipping" in result.stderr


def test_canonical_pair_roots_include_support_bootstrap_and_shared_plane(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", str(root))
    monkeypatch.setattr(module, "current_host", lambda config: "n150")
    candidate = yaml.safe_load((root / "docker-compose.yml").read_text())
    scope = module._pinned_runtime_compose_scope(candidate, {})
    assert set(module._PINNED_RUNTIME_ONESHOT_WRITERS) <= set(scope)
    assert set(module._PINNED_RUNTIME_EXTERNAL_PREREQUISITES) <= set(scope)
    assert set(module.installed_shared_dagster_plane().services) <= set(scope)
    assert "kor-travel-map-valkey" in scope
    assert all("weather" not in name for name in scope)

@pytest.mark.skipif(not _docker_compose(), reason="Docker Compose 없음")
def test_public_capture_projects_before_external_read_and_frozen_rechecks_scope(
    authority, tmp_path, monkeypatch,
):
    import ctypes
    import os

    # 이 WSL의 standalone CPython에는 os wrapper가 빠져 있어 같은 Linux syscall을 직접 쓴다.
    if not hasattr(os, "memfd_create"):
        libc = ctypes.CDLL(None, use_errno=True)
        libc.memfd_create.argtypes = (ctypes.c_char_p, ctypes.c_uint)
        libc.memfd_create.restype = ctypes.c_int
        def create_memfd(name, flags=0):
            fd = libc.memfd_create(name.encode(), flags)
            if fd < 0:
                raise OSError(ctypes.get_errno(), "memfd_create")
            return fd
        monkeypatch.setattr(os, "memfd_create", create_memfd, raising=False)
    candidate = document()
    candidate["services"]["app"]["env_file"] = [
        {"path": "selected.env", "required": True, "format": "raw"}]
    (tmp_path / "selected.env").write_text("SELECTED=synthetic\n")
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(candidate))
    env_path = tmp_path / ".env"
    env_path.write_text("LOCAL_ONLY=synthetic\n")
    snapshot = module._capture_compose_environment_snapshot(
        environment_override=None, env_path=env_path, compose_path=path,
        override_path=tmp_path / "docker-compose.override.yml",
        include_process_environment=False, interpolate_env_file=False)
    monkeypatch.setattr(module, "get_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(module, "_pinned_runtime_compose_scope",
                        lambda doc, env: module._compose_service_scope(doc, ("app",), env))
    # C6C 자격증명 판정 자체는 기존 회귀가 맡는다. 이 시험은 실제 config/memfd 경계를 통과한다.
    monkeypatch.setattr(module, "validate_compose_candidate_protected_values", lambda *a, **k: ())
    monkeypatch.setattr(module, "validate_resolved_compose_candidate_protected_values",
                        lambda *a, **k: ())
    service = module.ComposeService()
    transaction, validation = service.capture_transaction_unlocked(
        environment_snapshot=snapshot, pinned_runtime_scope=True)
    assert transaction.compose_source_bytes == path.read_bytes()
    assert transaction.service_scope == ("app", "db")
    assert {ref.service for ref in transaction.external_inputs.references} == {"app"}
    assert set(validation.resolved["services"]) == {"app", "db"}
    assert set(service._validate_frozen_transaction_unlocked(transaction)["services"]) == {"app", "db"}
    with pytest.raises(ComposeCandidateContractError):
        service.capture_transaction_unlocked(environment_snapshot=snapshot)
    candidate["services"]["app"]["depends_on"] = ["unknown"]
    path.write_text(yaml.safe_dump(candidate))
    monkeypatch.setattr(module, "_capture_compose_external_input_snapshot",
                        lambda *a, **k: pytest.fail("external read before closure"))
    with pytest.raises(ComposeCandidateContractError, match="local owner"):
        service.capture_transaction_unlocked(environment_snapshot=snapshot, pinned_runtime_scope=True)


@pytest.mark.skipif(not _docker_compose(), reason="Docker Compose 없음")
def test_public_host_preflight_requires_all_local_fields(authority, tmp_path, monkeypatch):
    candidate = document()
    candidate["services"]["db"]["environment"] = {"DB_REQUIRED": "${LOCAL_DB:?required db}"}
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(candidate))
    env_path = tmp_path / ".env"
    env_path.write_text("LOCAL_ONLY=synthetic\n")
    monkeypatch.setattr(module, "get_project_root", lambda: str(tmp_path))
    service = module.ComposeService()
    with pytest.raises(ComposeCandidateContractError):
        service.validate_host_compose_preflight()
    env_path.write_text("LOCAL_ONLY=synthetic\nLOCAL_DB=synthetic-db\n")
    service.validate_host_compose_preflight()

@pytest.mark.skipif(not _docker_compose(), reason="Docker Compose 없음")
def test_preflip_installed_package_layout_uses_candidate_reference_not_checkout(tmp_path):
    import os
    import shutil
    import sys

    source = Path(__file__).resolve().parents[2]
    candidate = tmp_path / "candidate-release"
    installed = candidate / "backend/.venv/lib/python3.13/site-packages"
    installed.mkdir(parents=True)
    shutil.copytree(source / "backend/src/kor_travel_docker_manager",
                    installed / "kor_travel_docker_manager",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (candidate / "config").mkdir()
    shutil.copyfile(source / "config/docker-targets.yml", candidate / "config/docker-targets.yml")
    shutil.copyfile(source / "docker-compose.yml", candidate / ".ktdm-release-compose.yml")
    (candidate / ".ktdm-source-revision").write_text("a" * 40)
    (candidate / ".env").write_text("KTDM_HOST=n150\n")
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(installed)!r})\n"
        "from pathlib import Path\nimport yaml\n"
        "from kor_travel_docker_manager.services import compose_service as m\n"
        "from kor_travel_docker_manager.services import registry as r\n"
        "from kor_travel_docker_manager.services import runtime_topology as t\n"
        "from kor_travel_docker_manager.services import trusted_install as trusted\n"
        f"candidate=Path({str(candidate)!r})\n"
        # 주소 probe만 격리한다. registry/정본 family/소유권/실제 config는 실제 함수를 쓴다.
        "config=yaml.safe_load((candidate/'config/docker-targets.yml').read_text())\n"
        "r._local_ipv4_addresses=lambda: frozenset({config['hosts']['n150']['addr']})\n"
        "assert not trusted.running_from_trusted_install_root()\n"
        "assert t._CHECKOUT_ROOT != candidate\n"
        "try:\n    m._scope_service_owners()\n"
        "except m.DeploymentContractError:\n    pass\n"
        "else:\n    raise AssertionError('preflip installed lookup unexpectedly succeeded')\n"
        "selected,owners=m._candidate_release_scope_owners(candidate)\n"
        "services={name:{'image':'busybox'} for name,hosts in owners.items() if selected in hosts}\n"
        "(candidate/'docker-compose.yml').write_text(yaml.safe_dump({'name':'preflip-scope','services':services}))\n"
        "m.ComposeService().validate_host_compose_preflight()\n"
        "print('candidate-only PASS')\n"
    )
    env = dict(os.environ, KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT=str(candidate))
    for name in ("KTDM_HOST", "KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE",
                 "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE",
                 "KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE"):
        env.pop(name, None)
    completed = subprocess.run([sys.executable, "-I", "-c", script], env=env,
                               cwd=tmp_path, text=True, capture_output=True, check=False,
                               timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "candidate-only PASS"

@pytest.mark.parametrize("dependency", ["unknown", "other"])
def test_host_install_preflight_rejects_dependency_before_external_read(
    authority, tmp_path, monkeypatch, dependency,
):
    candidate = document()
    candidate["services"]["app"]["depends_on"] = [dependency]
    (tmp_path / "docker-compose.yml").write_text(yaml.safe_dump(candidate))
    (tmp_path / ".env").write_text("LOCAL_ONLY=synthetic\n")
    monkeypatch.setattr(module, "get_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        module, "_capture_compose_external_input_snapshot",
        lambda *a, **k: pytest.fail("external read before host closure"),
    )
    with pytest.raises(ComposeCandidateContractError, match="local owner"):
        module.ComposeService().validate_host_compose_preflight()
