import json
from contextlib import contextmanager, nullcontext
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager import dagster_compute_log_retention as retention
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


@pytest.fixture
def config():
    return {
        "targets": {
            "example": {
                "core_candidate": {"group": "main", "project_lease": "/run/lock/example.lock"},
            },
        },
        "service_releases": {
            "example": {"groups": {"main": {
                "project": "the-project", "runtime_services": ["the-code-server"],
            }}},
        },
        "dagster_compute_log_retention": {"example": {
            "service": "the-code-server",
            "python": "/opt/venv/bin/python",
            "module": "application.compute_log_retention",
        }},
    }


@pytest.fixture
def daemon(monkeypatch):
    api = Mock()
    lease = Mock(return_value=nullcontext())
    monkeypatch.setattr(retention, "manager_mutation_lock", lambda: nullcontext())
    monkeypatch.setattr(retention, "project_lease", lease)
    monkeypatch.setattr(retention.docker, "DockerClient", lambda **kw: Mock(api=api))
    live = {
        "Id": "the-cid",
        "Config": {"Labels": {
            "com.docker.compose.project": "the-project",
            "com.docker.compose.service": "the-code-server",
        }},
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Mounts": [{
            "Type": "volume", "Name": "the-project-state", "RW": True,
            "Destination": "/opt/dagster/state", "Source": "/trusted-volume",
        }],
    }
    api.inventory = [live]
    monkeypatch.setattr(retention.candidate_service, "_inventory", lambda _client: api.inventory)
    monkeypatch.setattr(retention.persistent, "policy_for", Mock(return_value="the-policy"))
    monkeypatch.setattr(retention.persistent, "observe_volume_proof", Mock(return_value={"name": "proof"}))
    monkeypatch.setattr(retention.persistent, "verify_volume", api.verify_volume)
    api.exec_create.return_value = {"Id": "the-exec"}
    api.exec_inspect.return_value = {"ExitCode": 0}
    return api


@pytest.mark.parametrize("apply", [False, True])
def test_registered_worker_uses_exact_container_and_mode(config, daemon, apply):
    mode = "apply" if apply else "dry-run"
    daemon.exec_start.return_value = (
        json.dumps({"ok": True, "schema_version": 1, "mode": mode}).encode(), b"",
    )
    assert retention.run(config, "example", apply=apply) == {
        "ok": True, "schema_version": 1, "mode": mode, "target": "example",
    }
    daemon.exec_create.assert_called_once_with("the-cid", [
        "/usr/bin/timeout", "--kill-after=5s", "1200s", "/opt/venv/bin/python",
        "-I", "-m", "application.compute_log_retention", f"--{mode}",
    ])
    daemon.exec_start.assert_called_once_with("the-exec", demux=True)
    daemon.verify_volume.assert_called_once()


def test_failed_volume_proof_stops_before_running_the_worker(config, daemon):
    daemon.verify_volume.side_effect = ServiceReleaseError("wrong volume")
    with pytest.raises(ServiceReleaseError):
        retention.run(config, "example", apply=True)
    daemon.exec_create.assert_not_called()


def test_ambiguous_running_code_servers_stop_before_mutation(config, daemon):
    daemon.inventory.append(daemon.inventory[0] | {"Id": "other-cid"})
    with pytest.raises(ServiceReleaseError):
        retention.run(config, "example", apply=True)
    daemon.exec_create.assert_not_called()


def test_worker_protocol_must_agree_with_exit_code(config, daemon):
    daemon.exec_start.return_value = (
        b'{"ok": true, "schema_version": 1, "mode": "apply"}', b"",
    )
    daemon.exec_inspect.return_value = {"ExitCode": 1}
    with pytest.raises(ServiceReleaseError):
        retention.run(config, "example", apply=True)


def test_unregistered_project_never_contacts_docker(config, daemon):
    with pytest.raises(ServiceReleaseError):
        retention.run(config, "unregistered", apply=True)
    daemon.exec_create.assert_not_called()


def test_worker_timeout_is_reported_separately(config, daemon):
    daemon.exec_start.return_value = (None, None)
    daemon.exec_inspect.return_value = {"ExitCode": 124}
    with pytest.raises(retention.RetentionWorkerTimeout):
        retention.run(config, "example", apply=False)


def test_only_project_lease_is_held_during_the_sweep(config, daemon, monkeypatch):
    held = []

    @contextmanager
    def lock(name):
        held.append(name)
        try:
            yield
        finally:
            held.remove(name)

    monkeypatch.setattr(retention, "manager_mutation_lock", lambda: lock("global"))
    monkeypatch.setattr(retention, "project_lease", lambda _path: lock("project"))

    def create(*_args):
        assert held == ["global", "project"]
        return {"Id": "the-exec"}

    def start(*_args, **_kwargs):
        assert held == ["project"]
        return b'{"ok": true, "schema_version": 1, "mode": "apply"}', b""

    daemon.exec_create.side_effect = create
    daemon.exec_start.side_effect = start
    assert retention.run(config, "example", apply=True)["ok"] is True
    assert held == []
