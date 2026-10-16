"""호스트별 workspace는 registry 배치에서 파생하고 SSL 미지정 의미를 보존한다."""

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import runtime_topology as topology
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.registry import default_host, target_hosts

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def documents():
    return (
        yaml.safe_load((ROOT / "docker-compose.yml").read_text()),
        yaml.safe_load((ROOT / "config/docker-targets.yml").read_text()),
    )


def test_all_committed_hosts_are_derived(documents):
    compose, targets = documents
    documents_before = copy.deepcopy(documents)
    seen = []
    for host in targets["hosts"]:
        derived = topology.derive_host_workspace(compose, targets, host)
        path = ROOT / "config/dagster-shared" / f"workspace.{host}.yaml"
        assert yaml.safe_load(path.read_bytes()) == derived
        names = topology.workspace_location_names(derived)
        assert len(names) == 3
        assert all(set(entry["grpc_server"]) == {"host", "port", "location_name"} for entry in derived["load_from"])
        seen.append(set(names))
    assert not seen[0] & seen[1]
    assert len(set.union(*seen)) == 6
    # 옛 별칭 `workspace.yaml`은 없다(NH-4) — 호스트 파일만 정본이다.
    assert not (ROOT / "config/dagster-shared/workspace.yaml").exists()
    assert documents == documents_before


def _default_host_values(compose_text: str, targets_text: str) -> tuple[list[str], list[str]]:
    hosts = re.findall(r"\$\{KTDM_HOST:-([^}]*)\}", compose_text + targets_text)
    digests = re.findall(r"\$\{KTDM_DAGSTER_WORKSPACE_DIGEST:-([^}]*)\}", compose_text + targets_text)
    return hosts, digests


def test_default_host_values_are_the_default_hosts_derivation(documents):
    """T-MGR-NH NH-4: compose·registry의 `${KTDM_HOST:-…}`·`${KTDM_DAGSTER_WORKSPACE_DIGEST:-…}` 기본값은 손으로 쓴 값이
    아니라 default 호스트의 파생값이다 — 호스트 이름 = registry default, digest = `workspace.<default>.yaml`의 sha256[:16].
    `.env`에 값이 없는 호스트(default)가 쓰는 값이라, default를 바꾸거나 workspace가 바뀌면 함께 바뀌어야 한다."""
    _compose, targets = documents
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    targets_text = (ROOT / "config/docker-targets.yml").read_text(encoding="utf-8")
    hosts, digests = _default_host_values(compose_text, targets_text)
    assert len(hosts) >= 4 and len(digests) >= 2  # 전제: compose bind 2·registry bind source 2, digest env 2
    default = default_host(targets)
    workspace = ROOT / "config/dagster-shared" / f"workspace.{default}.yaml"
    assert set(hosts) == {default}
    assert set(digests) == {hashlib.sha256(workspace.read_bytes()).hexdigest()[:16]}


def test_a_changed_default_host_leaves_the_compose_defaults_stale(documents):
    """빨간 대조군 — registry default를 다른 호스트로 바꾸면 기본값이 옛 호스트·옛 digest를 가리켜 어긋난다."""
    _compose, targets = documents
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    targets_text = (ROOT / "config/docker-targets.yml").read_text(encoding="utf-8")
    hosts, digests = _default_host_values(compose_text, targets_text)
    other = next(name for name in targets["hosts"] if name != default_host(targets))
    workspace = ROOT / "config/dagster-shared" / f"workspace.{other}.yaml"
    assert set(hosts) != {other}
    assert set(digests) != {hashlib.sha256(workspace.read_bytes()).hexdigest()[:16]}


def test_third_host_move_changes_only_affected_workspaces(documents):
    compose, targets = documents
    original = {host: topology.derive_host_workspace(compose, targets, host) for host in targets["hosts"]}
    targets["hosts"]["third"] = {"addr": "192.0.2.3"}
    old_host = next(iter(target_hosts("geo", targets)))
    targets["targets"]["geo"]["host"] = "third"
    third = topology.derive_host_workspace(compose, targets, "third")
    moved = topology.workspace_location_names(third)
    assert len(moved) == 1
    for host, expected in original.items():
        actual = topology.derive_host_workspace(compose, targets, host)
        if host == old_host:
            assert set(topology.workspace_location_names(actual)) == set(topology.workspace_location_names(expected)) - set(moved)
        else:
            assert actual == expected


def test_host_all_and_own_switch_are_honored(documents):
    compose, targets = documents
    targets["targets"]["geo"]["host"] = "all"
    for host in targets["hosts"]:
        assert any(entry["grpc_server"]["port"] == 12503 for entry in topology.derive_host_workspace(compose, targets, host)["load_from"])
    targets["targets"]["geo"]["dagster"]["control_plane"] = "own"
    for host in targets["hosts"]:
        assert all(entry["grpc_server"]["port"] != 12503 for entry in topology.derive_host_workspace(compose, targets, host)["load_from"])


def test_unknown_host_rejected(documents):
    with pytest.raises(DeploymentContractError, match="host"):
        topology.derive_host_workspace(*documents, "missing")


def test_instance_bytes_unchanged():
    assert hashlib.sha256((ROOT / "config/dagster-shared/dagster.yaml").read_bytes()).hexdigest() == "7925bcdea8472b069b40b7585b6ab8eb8f7cd03d2bfbb5e252755ff60bacef4f"


@pytest.fixture
def workspace_files(documents, tmp_path, monkeypatch):
    _, targets = documents
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", str(tmp_path))
    selected = default_host(targets)
    monkeypatch.setenv("KTDM_HOST", selected)
    directory = tmp_path / "config/dagster-shared"
    directory.mkdir(parents=True)
    path = directory / f"workspace.{selected}.yaml"
    path.write_bytes(b"load_from: []\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return targets, tmp_path, path, digest


def test_frozen_workspace_uses_same_bytes_for_digest_and_parse(workspace_files, monkeypatch):
    targets, root, path, digest = workspace_files
    original = Path.read_bytes
    reads = []

    def read_once(value):
        reads.append(value)
        return original(value)

    monkeypatch.setattr(Path, "read_bytes", read_once)
    assert topology.read_host_workspace(str(path), root=root, targets=targets, digests=[digest, digest]) == ()
    assert reads == [path]


@pytest.mark.parametrize("which", [0, 1])
def test_either_frozen_plane_digest_drift_rejected(workspace_files, which):
    targets, root, path, digest = workspace_files
    digests = [digest, digest]
    digests[which] = "0" * 16
    with pytest.raises(DeploymentContractError, match="digests"):
        topology.read_host_workspace(str(path), root=root, targets=targets, digests=digests)


@pytest.mark.parametrize("name", ["workspace.other.yaml", "application/workspace.yaml", "../workspace.yaml"])
def test_wrong_host_or_noncanonical_workspace_path_rejected(workspace_files, name):
    targets, root, _, digest = workspace_files
    with pytest.raises(DeploymentContractError, match="source"):
        topology.read_host_workspace(str(root / "config/dagster-shared" / name), root=root, targets=targets, digests=[digest, digest])


@pytest.mark.parametrize("raw", [b"[bad", b"load_from: []\nload_from: []\n", b"load_from: nope\n", b"\xff"])
def test_invalid_workspace_is_not_empty_healthy(workspace_files, raw):
    targets, root, path, _ = workspace_files
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    with pytest.raises(DeploymentContractError):
        topology.read_host_workspace(str(path), root=root, targets=targets, digests=[digest, digest])


@pytest.fixture
def real_host_render(tmp_path, monkeypatch, documents):
    """제품 Compose를 실제 plugin으로 정적 렌더한다. daemon/외부 HTTP는 쓰지 않는다."""
    if shutil.which("docker") is None:
        pytest.skip("실제 Compose config가 필요하다")
    source = (ROOT / "docker-compose.yml").read_text()
    directory = tmp_path / "config/dagster-shared"
    directory.mkdir(parents=True)
    (tmp_path / "docker-compose.yml").write_text(source)
    for file in (ROOT / "config/dagster-shared").glob("*.yaml"):
        (directory / file.name).write_bytes(file.read_bytes())
    targets = documents[1]
    targets["hosts"]["third"] = {"addr": "192.0.2.3"}
    (directory / "workspace.third.yaml").write_text("load_from: []\n")
    (tmp_path / "config/docker-targets.yml").write_text(yaml.safe_dump(targets))
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", str(tmp_path))

    def render(host):
        path = directory / f"workspace.{host}.yaml"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        # 실제 값이나 개발자의 .env를 상속하지 않는다. config만 호출하므로 endpoint를 읽지 않는다.
        environment = {name: "fixture-only" for name in re.findall(r"\$\{([A-Z0-9_]+):\?", source)}
        environment.update(PATH=os.defpath, HOME=str(tmp_path), KTDM_HOST=host,
                           KTDM_DAGSTER_WORKSPACE_DIGEST=digest,
                           DOCKER_HOST="unix:///tmp/manager481-host-render-no-daemon.sock")
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "--project-directory", str(tmp_path),
             "-f", str(tmp_path / "docker-compose.yml"), "config", "--format", "json"],
            env=environment, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, "정적 Compose 렌더 실패(원문 환경은 출력하지 않음)"
        return json.loads(result.stdout), path, digest

    return tmp_path, targets, render


@pytest.mark.parametrize("host", ["n150", "odroid", "third"])
def test_actual_compose_host_selection_reaches_workspace_reader(real_host_render, monkeypatch, host):
    root, targets, render = real_host_render
    monkeypatch.setenv("KTDM_HOST", host)
    document, expected_path, digest = render(host)
    plane = topology.derive_shared_dagster_plane(document)
    services = [document["services"][name] for name in plane.services]
    sources = [topology.shared_workspace_source(service) for service in services]
    assert sources == [str(expected_path)] * 2
    digests = [service["environment"]["KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST"] for service in services]
    assert digests == [digest] * 2
    actual = topology.read_host_workspace(sources[0], root=root, targets=targets, digests=digests)
    expected = topology.workspace_location_names(yaml.safe_load(expected_path.read_bytes()))
    assert actual == expected


@pytest.mark.parametrize("host", ["n150", "odroid"])
@pytest.mark.parametrize("drift", [None, "source", "daemon_digest", "webserver_digest"])
def test_public_ensure_dagster_validates_frozen_workspace_before_mutation(
    real_host_render, monkeypatch, host, drift,
):
    from test_docker_manager_cli import _ensure_dagster_with

    root, _, render = real_host_render
    monkeypatch.setenv("KTDM_HOST", host)
    document, _, _ = render(host)
    plane = topology.derive_shared_dagster_plane(document)
    if drift == "source":
        foreign = "odroid" if host == "n150" else "n150"
        for name in plane.services:
            service = document["services"][name]
            for volume in service["volumes"]:
                if volume.get("target") == "/opt/dagster/dagster_home/workspace.yaml":
                    volume["source"] = str(root / "config/dagster-shared" / f"workspace.{foreign}.yaml")
    elif drift:
        name = plane.daemon if drift == "daemon_digest" else plane.webserver
        document["services"][name]["environment"]["KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST"] = "0" * 16
    calls = []
    if drift:
        with pytest.raises(DeploymentContractError, match="workspace"):
            _ensure_dagster_with(root, None, resolved=document, calls=calls)
        assert calls == [], "workspace 거절 전에 실제 mutation argv가 전달되면 안 된다"
    else:
        result, _ = _ensure_dagster_with(root, None, resolved=document, calls=calls)
        assert result["success"] and calls
