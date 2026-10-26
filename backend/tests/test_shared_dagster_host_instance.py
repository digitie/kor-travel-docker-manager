"""호스트 보호 상한·정본 bytes·명시 준비 및 외부 caller 계약을 검증한다."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from kor_travel_docker_manager.services import shared_dagster_instance as instance
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.registry import TargetsConfigError, _host_specs

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "config/dagster-shared/dagster.yaml"


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path / "release"
    base = root / "config/dagster-shared/dagster.yaml"
    base.parent.mkdir(parents=True)
    base.write_bytes(BASE.read_bytes())
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    targets = {"hosts": {"n150": {"dagster_max_concurrent_runs": 6},
                          "odroid": {"dagster_max_concurrent_runs": 5}}}
    selected = ["odroid"]
    monkeypatch.setattr(instance, "get_project_root", lambda: str(root))
    monkeypatch.setattr(instance, "load_targets_config", lambda: targets)
    monkeypatch.setattr(instance, "current_host", lambda config: selected[0])
    monkeypatch.setattr(instance, "TRUSTED_STATE_ROOT", state)
    monkeypatch.setattr(instance, "_OWNER_UID", os.geteuid())
    from kor_travel_docker_manager.services import c6c_deployment
    entries = []
    @contextlib.contextmanager
    def lock():
        entries.append("G")
        yield
    monkeypatch.setattr(c6c_deployment, "manager_mutation_lock", lock)
    return root, state, targets, selected, entries


def test_default_host_preserves_original_bytes_path_digest_without_state(host):
    root, state, _, selected, locks = host
    selected[0] = "n150"
    before = BASE.read_bytes()
    assert instance._configuration(before, 6) is before
    result = instance.shared_dagster_instance()
    assert result.path == root / "config/dagster-shared/dagster.yaml"
    assert result.sha256 == hashlib.sha256(before).hexdigest()
    assert result.max_concurrent_runs == 6 and result.uses_base
    assert instance.instance_environment({"other": "unchanged"}) == {"other": "unchanged"}
    assert list(state.iterdir()) == [] and locks == []


def test_nondefault_show_does_not_implicitly_prepare(host):
    _, state, _, _, locks = host
    with pytest.raises(DeploymentContractError, match="unsafe"):
        instance.shared_dagster_instance()
    assert list(state.iterdir()) == [] and locks == []


def test_prepare_publishes_one_complete_readonly_file_and_reuses_inode(host):
    _, state, _, _, locks = host
    prepared = instance.prepare_shared_dagster_instance()
    expected = BASE.read_bytes().replace(b"max_concurrent_runs: 6", b"max_concurrent_runs: 5", 1)
    assert prepared.path.read_bytes() == expected
    assert prepared.path == state / "dagster-instance" / (hashlib.sha256(expected).hexdigest() + ".yaml")
    assert prepared.path.stat().st_mode & 0o777 == 0o444
    assert prepared.path.stat().st_nlink == 1
    old = prepared.path.stat()
    assert instance.prepare_shared_dagster_instance() == prepared
    after = prepared.path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (old.st_ino, old.st_mtime_ns)
    assert instance.shared_dagster_instance() == prepared and locks == ["G", "G"]
    assert set(prepared.as_dict()) == {"host", "max_concurrent_runs", "path", "sha256"}
    assert list(prepared.path.parent.iterdir()) == [prepared.path]


@pytest.mark.parametrize("bad", [True, False, 0, -1, "5", None, 5.0])
def test_host_policy_rejects_invalid_scalar(bad):
    config = {"version": 2, "hosts": {"local": {"addr": "127.0.0.1", "default": True,
                                                "dagster_max_concurrent_runs": bad}}}
    with pytest.raises(TargetsConfigError, match="positive integer"):
        _host_specs(config)


def test_operating_release_requires_explicit_policy(host):
    root, _, targets, _, _ = host
    (root / ".ktdm-source-revision").write_text("test")
    targets["hosts"]["odroid"].clear()
    with pytest.raises(DeploymentContractError, match="cannot be read"):
        instance.shared_dagster_instance()


@pytest.mark.parametrize("fault", ["content", "writable", "hardlink", "symlink"])
def test_prepared_file_tampering_is_not_overwritten(host, fault):
    config = instance.prepare_shared_dagster_instance()
    path = config.path
    if fault == "content":
        path.chmod(0o644)
        path.write_bytes(b"corrupt")
        path.chmod(0o444)
    elif fault == "writable":
        path.chmod(0o644)
    elif fault == "hardlink":
        os.link(path, path.with_suffix(".other"))
    else:
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    before = path.read_bytes()
    with pytest.raises(DeploymentContractError):
        instance.prepare_shared_dagster_instance()
    with pytest.raises(DeploymentContractError):
        instance.shared_dagster_instance()
    assert path.read_bytes() == before


def test_unsafe_state_directory_and_symlink_are_rejected(host, tmp_path):
    _, state, _, _, _ = host
    state.chmod(0o777)
    with pytest.raises(DeploymentContractError, match="directory"):
        instance.prepare_shared_dagster_instance()
    state.chmod(0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (state / "dagster-instance").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(DeploymentContractError, match="directory"):
        instance.prepare_shared_dagster_instance()
    assert list(elsewhere.iterdir()) == []


def test_partial_write_failure_never_publishes_expected_name(host, monkeypatch):
    _, state, _, _, _ = host
    original = OSError("write failed")
    def fail(*args, **kwargs):
        raise original
    monkeypatch.setattr(instance, "atomic_write_bytes", fail)
    with pytest.raises(OSError) as error:
        instance.prepare_shared_dagster_instance()
    assert error.value is original
    assert list((state / "dagster-instance").iterdir()) == []


def test_competing_publisher_is_never_overwritten(host, monkeypatch):
    original_link = os.link
    def competing(source, target, **kwargs):
        Path(target).write_bytes(b"other publisher")
        Path(target).chmod(0o444)
        return original_link(source, target, **kwargs)
    monkeypatch.setattr(instance.os, "link", competing)
    with pytest.raises(DeploymentContractError):
        instance.prepare_shared_dagster_instance()
    _, state, _, _, _ = host
    files = list((state / "dagster-instance").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"other publisher"


def test_host_and_independent_overrides_fail_closed(host):
    config = instance.prepare_shared_dagster_instance()
    environment = instance.instance_environment({"KTDM_HOST": "odroid"})
    assert environment[instance.CONFIG_ENV] == str(config.path)
    assert environment[instance.DIGEST_ENV] == config.sha256[:16]
    for wrong in [{"KTDM_HOST": "n150"}, {instance.CONFIG_ENV: "/tmp/other.yaml"},
                  {instance.DIGEST_ENV: "0" * 16}]:
        with pytest.raises(DeploymentContractError):
            instance.instance_environment(wrong)


def _render_consumers(config):
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    carriers = {}
    for name, service in compose["services"].items():
        env = service.get("environment") or {}
        if "KOR_TRAVEL_DAGSTER_SHARED_PG_URL" not in env:
            continue
        own = deepcopy(service)
        env = own["environment"]
        if "KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST" in env:
            env["KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST"] = config.sha256[:16]
        from kor_travel_docker_manager.services.runtime_topology import _volume_source
        from kor_travel_docker_manager.services.workspace_digest import interpolate
        variables = {instance.CONFIG_ENV: str(config.path)}
        mounts = []
        for volume in own.get("volumes", []):
            source = _volume_source(volume)
            destination, _, access = volume[len(source) + 1:].rpartition(":")
            mounts.append(dict(type="bind", source=interpolate(source, variables.get),
                               target=destination, read_only=access == "ro"))
        own["volumes"] = mounts
        carriers[name] = own
    return {"services": carriers}


def test_every_shared_carrier_uses_one_effective_file_and_digest(host):
    config = instance.prepare_shared_dagster_instance()
    resolved = _render_consumers(config)
    assert len(resolved["services"]) == 8
    instance.validate_instance_consumers(resolved)
    for name in resolved["services"]:
        tampered = deepcopy(resolved)
        mount = next(v for v in tampered["services"][name]["volumes"]
                     if v["target"].endswith("/dagster.yaml"))
        mount["source"] = "/tmp/other.yaml"
        with pytest.raises(DeploymentContractError, match="mount"):
            instance.validate_instance_consumers(tampered)
    tampered = deepcopy(resolved)
    one = next(s for s in tampered["services"].values()
               if "KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST" in s["environment"])
    one["environment"]["KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST"] = "0" * 16
    with pytest.raises(DeploymentContractError, match="digest"):
        instance.validate_instance_consumers(tampered)


def test_cli_show_fixed_four_keys_and_failure_has_no_raw_exception(host, monkeypatch, capsys):
    from kor_travel_docker_manager import cli
    config = instance.prepare_shared_dagster_instance()
    assert cli.main(["dagster-instance-config", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == config.as_dict()
    def fail():
        raise OSError("SECRET-SENTINEL")
    monkeypatch.setattr(instance, "shared_dagster_instance", fail)
    assert cli.main(["dagster-instance-config", "show", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "SECRET-SENTINEL" not in captured.err


def test_installer_prepares_before_preflight_without_new_mount_or_service_condition():
    source = (ROOT / "scripts/install-ktdm-trusted-release").read_text()
    assert source.index('main(["dagster-instance-config", "prepare", "--json"])') < source.index("compose_service.validate_host_compose_preflight()")
    block = source[source.index("# 비기본 host instance"):source.index("# 새 release의 정본 host-local 서비스")]
    assert "KTDM_PINNED_REBUILD_GLOBAL_LOCK_FD=9" in block
    assert not any(word in block for word in ["mountpoint", "findmnt", "systemctl", "9>&-"])


@pytest.mark.parametrize("fault", ["writable", "symlink", "oversize"])
def test_default_source_file_safety_is_checked_before_show(host, fault):
    root, _, _, selected, _ = host
    selected[0] = "n150"
    base = root / "config/dagster-shared/dagster.yaml"
    if fault == "writable":
        base.chmod(0o666)
    elif fault == "symlink":
        original = base.with_suffix(".original")
        base.rename(original)
        base.symlink_to(original)
    else:
        base.write_bytes(b"x" * ((1 << 20) + 1))
    with pytest.raises(DeploymentContractError, match="cannot be read"):
        instance.shared_dagster_instance()


def test_frozen_environment_rejects_late_independent_override(host, tmp_path):
    from kor_travel_docker_manager.services import compose_service as compose

    prepared = instance.prepare_shared_dagster_instance()
    snapshot = compose._capture_compose_environment_snapshot(
        environment_override=None, env_path=tmp_path / "absent.env",
        compose_path=ROOT / "docker-compose.yml", include_process_environment=False,
    )
    assert snapshot.effective[instance.CONFIG_ENV] == str(prepared.path)
    with pytest.raises(DeploymentContractError, match="override"):
        compose._effective_snapshot_environment(snapshot, {instance.DIGEST_ENV: "0" * 16})


@pytest.mark.parametrize("module_name,helper_name", [
    ("test_dagster_shared_storage_integration", "_carried"),
    ("test_dagster_shared_plane_integration", "_carry"),
])
def test_isolated_fixture_resolves_default_instance_bind_before_rebasing(module_name, helper_name):
    # 실제 fixture helper만 호출한다. fixture 본문과 Docker는 실행하지 않는다.
    import importlib

    module = importlib.import_module(module_name)
    service = {"volumes": [
        "${KTDM_DAGSTER_INSTANCE_CONFIG:-./config/dagster-shared/dagster.yaml}:/home/dagster/dagster.yaml:ro",
    ]}
    helper = getattr(module, helper_name)
    result = helper(service) if helper_name == "_carried" else helper(service, "owned-pg")
    source, target, mode = result["volumes"][0].rsplit(":", 2)
    assert Path(source) == ROOT / "config/dagster-shared/dagster.yaml"
    assert Path(source).is_file()
    assert target == "/home/dagster/dagster.yaml" and mode == "ro"
    assert service["volumes"][0].startswith("${KTDM_DAGSTER_INSTANCE_CONFIG:-")


def test_readonly_config_uses_prepared_host_environment_without_implicit_write(host, monkeypatch, tmp_path):
    from kor_travel_docker_manager.services import compose_service as compose

    prepared = instance.prepare_shared_dagster_instance()
    _, state, _, _, locks = host
    before = {path: path.stat().st_mtime_ns for path in state.rglob("*")}
    before_locks = list(locks)
    monkeypatch.setattr(compose, "get_compose_path", lambda: str(ROOT / "docker-compose.yml"))
    monkeypatch.setattr(compose, "get_env_path", lambda: str(tmp_path / "absent.env"))
    monkeypatch.setattr(compose.ComposeService, "_assert_host_mutation_scope", lambda *args, **kwargs: None)
    captured = {}
    def collect(self, args, **kwargs):
        captured.update(kwargs)
        return {"success": True}
    monkeypatch.setattr(compose.ComposeService, "_run_unlocked", collect)
    assert compose.ComposeService().run(["config", "--format", "json"])["success"]
    effective = captured["environment_snapshot"].effective
    assert effective[instance.CONFIG_ENV] == str(prepared.path)
    assert effective[instance.DIGEST_ENV] == prepared.sha256[:16]
    assert {path: path.stat().st_mtime_ns for path in state.rglob("*")} == before
    assert locks == before_locks


@pytest.mark.parametrize("action", ["show", "prepare"])
def test_instance_command_dispatch_preserves_fixed_failure_before_target_aliases(host, monkeypatch, capsys, action):
    from kor_travel_docker_manager import cli

    calls = []
    def broken_aliases():
        calls.append("alias")
        raise TargetsConfigError("SECRET-REGISTRY-SENTINEL")
    def broken_registry():
        raise TargetsConfigError("SECRET-HOST-SENTINEL")
    monkeypatch.setattr(cli, "list_targets", broken_aliases)
    monkeypatch.setattr(instance, "load_targets_config", broken_registry)
    assert cli.main(["dagster-instance-config", action, "--json"]) == 2
    captured = capsys.readouterr()
    assert calls == []
    assert captured.out == ""
    assert captured.err == "shared Dagster instance configuration is unavailable\n"
