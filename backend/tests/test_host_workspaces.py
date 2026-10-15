"""호스트별 workspace는 registry 배치에서 파생하고 SSL 미지정 의미를 보존한다."""

import copy
import hashlib
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


def test_all_committed_hosts_are_derived_and_alias_is_default(documents):
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
    assert (ROOT / "config/dagster-shared/workspace.yaml").read_bytes() == (
        ROOT / "config/dagster-shared" / f"workspace.{default_host(targets)}.yaml"
    ).read_bytes()
    assert documents == documents_before


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
