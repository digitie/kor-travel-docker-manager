"""UI 후보의 명시 승인·고정 입력·비선택 보존·health 후 current 발행 회귀."""
from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services.c6c_deployment import ManagerMutationActiveError
from kor_travel_docker_manager.services.registry import load_targets_config

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64
DATABASE = "sha256:" + "e" * 64
SOURCE_URL = "https://github.com/digitie/kor-travel-map.git"
UI = "kor-travel-map-ui"


def candidate_module():
    return importlib.import_module("kor_travel_docker_manager.services.service_release_candidate")


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "file").write_text("approved")
    git(source, "add", "file")
    git(source, "commit", "-qm", "approved")
    sha = git(source, "rev-parse", "HEAD")
    git(source, "remote", "add", "origin", SOURCE_URL)
    git(source, "update-ref", "refs/remotes/origin/main", sha)
    root = tmp_path / "releases"
    root.mkdir(mode=0o755)
    monkeypatch.setattr(release, "_OWNER_UID", os.getuid())
    monkeypatch.setattr(release, "_trusted_ancestors", release._directory)
    profile = {"service": "map", "groups": {"ui": {
        "project": "manager", "working_dir": str(tmp_path),
        "config_files": ["compose.yml"], "env_file": str(tmp_path / ".env"),
        "runtime_services": [UI], "oneshot_images": {},
    }}}
    config = {"targets": {"map": {"host": "test"}}, "containers": {
        UI: {"compose_service": UI, "role": "map-ui"},
    }, "service_releases": {"map": {"groups": {"ui": {
        **profile["groups"]["ui"], "oneshot_services": [],
    }}}}}
    del config["service_releases"]["map"]["groups"]["ui"]["oneshot_images"]
    from kor_travel_docker_manager import service_release_cli as entry
    monkeypatch.setattr(entry, "current_host", lambda config: "test")
    monkeypatch.setattr(entry, "target_hosts", lambda service, config: frozenset({"test"}))

    def container(name, image=OTHER):
        return {"Id": "cid-" + name, "Image": image, "RestartCount": 0,
                "Config": {"Labels": {"com.docker.compose.project": "manager",
                                      "com.docker.compose.service": name},
                           "Healthcheck": {"Test": ["CMD", "probe"]}},
                "State": {"Running": True, "StartedAt": "2026-11-12T00:00:00Z",
                          "Health": {"Status": "healthy"}}}
    records = {name: container(name) for name in ["api", "db", "dagster"]}
    records["db"] = container("db", DATABASE)
    records[UI] = container(UI, OLD)
    metadata = {value: {"Id": value, "Created": "2026-11-12T00:00:00Z",
                       "Config": {"Labels": {"org.opencontainers.image.revision": sha,
                                              "org.opencontainers.image.source": SOURCE_URL}}}
                for value in [OLD, NEW, OTHER]}
    metadata[OLD]["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    client = SimpleNamespace(close=Mock())

    def listing(*, all, filters=None):
        assert all is True
        names = list(records)
        if filters:
            names = [name for name in names if all_label_match(records[name], filters["label"])]
        return [SimpleNamespace(attrs=copy.deepcopy(records[name])) for name in names]
    client.containers = SimpleNamespace(list=listing)
    client.images = SimpleNamespace(get=lambda value: SimpleNamespace(attrs=copy.deepcopy(metadata[value])))
    @contextmanager
    def initial_lock():
        yield
    monkeypatch.setattr(release, "manager_mutation_lock", initial_lock)
    current = release.adopt_release(profile, root=root, client=client)
    resolved = {"services": {name: {"image": value["Image"],
                                    "environment": {"PASSWORD": "fixture$secret"}}
                             for name, value in records.items()}}
    return SimpleNamespace(root=root, source=source, sha=sha, profile=profile, config=config,
                           records=records, metadata=metadata, client=client, current=current,
                           resolved=resolved, monkeypatch=monkeypatch, calls=[], locked=[])


def all_label_match(record, labels):
    actual = record["Config"]["Labels"]
    return all(actual.get(key) == value for key, value in (label.split("=", 1) for label in labels))


def prepare(s):
    m = candidate_module()
    s.monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    @contextmanager
    def lock():
        s.locked.append(True)
        try:
            yield
        finally:
            s.locked.pop()
    s.monkeypatch.setattr(m, "manager_mutation_lock", lock)
    s.monkeypatch.setattr(m.frozen, "_mutation_lock_fd", lambda: 123)
    s.monkeypatch.setattr(m.frozen, "load_secret_environment", lambda path: [("PASSWORD", "fixture$secret")])
    original_list = s.client.containers.list
    def listing(**kwargs):
        assert s.locked
        return original_list(**kwargs)
    s.client.containers.list = listing
    def factory():
        assert s.locked
        s.calls.append("factory")
        return s.client
    s.factory = factory
    status = SimpleNamespace(images={UI: OLD, "api": OTHER, "dagster": OTHER})
    s.monkeypatch.setattr(m, "runtime_topology", lambda: SimpleNamespace(
        runtime_services=[UI, "api", "dagster"], retired_services=(),
        service=lambda slot: UI if slot == "map_ui" else "dagster" if "dagster" in slot else "api",
        families={"map": SimpleNamespace(carrier="dagster"), "pinvi": SimpleNamespace(carrier="dagster")},
    ))
    baseline_path = s.root / "baseline"
    if not baseline_path.exists():
        baseline_path.write_bytes(b"committed")
    s.monkeypatch.setattr(m, "_baseline", lambda *args: (baseline_path, baseline_path.read_bytes(), {"pin": "unchanged"}, status))
    def run(argv, **kwargs):
        s.calls.append((argv, kwargs))
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(s.resolved), "")
        assert s.locked
        assert kwargs["pass_fds"] == (123,)
        assert argv[-1] == UI
        s.records[UI]["Image"] = json.loads(kwargs["input"])["services"][UI]["image"]
        s.records[UI]["Id"] = "new-ui"
        if getattr(s, "on_mutation", None):
            s.on_mutation()
        return subprocess.CompletedProcess(argv, getattr(s, "mutation_exit", 0), "", "fixture$secret failure")
    s.monkeypatch.setattr(m, "_render", lambda argv, **kwargs: run(argv, **kwargs))
    s.monkeypatch.setattr(m.frozen, "_run_mutation", lambda argv, **kwargs:
                         (run(argv, pass_fds=(123,), **kwargs), []))
    return m


def apply(s, **kwargs):
    m = prepare(s)
    options = dict(service="map", group="ui", image_id=NEW, source_sha=s.sha,
                   source_clone=s.source, root=s.root, config=s.config,
                   client_factory=s.factory, execute=True)
    options.update(kwargs)
    return m.apply_ui_candidate(**options)


def test_map_catalog_declares_only_ui_group(monkeypatch):
    from kor_travel_docker_manager import service_release_cli as entry
    monkeypatch.setattr(entry, "current_host", lambda config: "n150")
    config = load_targets_config()
    declaration = entry._declaration("map", config)
    assert declaration["groups"]["ui"]["runtime_services"] == [UI]
    assert declaration["groups"]["ui"]["oneshot_services"] == []


def test_cli_has_distinct_candidate_operation_and_explicit_approval():
    parser = cli.build_parser()
    args = parser.parse_args(["service-release", "apply-candidate", "map", "--group", "ui",
                              "--image-id", NEW, "--source-sha", "d" * 40,
                              "--source-clone", "/trusted/map", "--dry-run"])
    assert args.release_action == "apply-candidate" and args.dry_run
    with pytest.raises(SystemExit):
        parser.parse_args(["service-release", "apply-candidate", "map", "--image-id", NEW,
                           "--source-sha", "d" * 40, "--source-clone", "/trusted/map"])


def test_apply_changes_only_ui_and_publishes_observed_current(scenario):
    s = scenario
    before = copy.deepcopy(s.records)
    result = apply(s)
    current = release.read_current_release("map", root=s.root)
    assert result["result"] == "healthy" and result["success"] is True
    assert current["images"][UI]["id"] == NEW
    assert current["images"][UI]["source_sha"] == s.sha
    assert current["previous"] == s.current["release_directory"]
    assert {k: v for k, v in s.records.items() if k != UI} == {k: v for k, v in before.items() if k != UI}
    command, kw = next(c for c in s.calls if isinstance(c, tuple) and "up" in c[0])
    assert command[command.index("up"):] == ["up", "-d", "--no-deps", "--no-build", "--pull", "never",
                                             "--force-recreate", "--wait", "--wait-timeout", "120", UI]
    assert "-" == command[command.index("-f") + 1]
    assert "timeout" not in kw
    frozen = json.loads(kw["input"])
    assert frozen["services"][UI]["image"] == NEW
    assert frozen["services"][UI]["environment"]["PASSWORD"] == "fixture$$secret"
    assert "fixture$secret" not in json.dumps(result)
    assert s.client.close.call_count == 1


def test_dry_run_keeps_runtime_and_current_identical(scenario):
    s = scenario
    before = copy.deepcopy(s.records)
    result = apply(s, execute=False)
    assert result["result"] == "planned"
    assert s.records == before
    assert release.read_current_release("map", root=s.root) == s.current
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


@pytest.mark.parametrize("override", [{"image_id": "latest"}, {"source_sha": "main"},
                                       {"wait_timeout": 0}, {"wait_timeout": 601}, {"wait_timeout": True}])
def test_invalid_input_cannot_construct_docker(scenario, override):
    with pytest.raises(release.ServiceReleaseError):
        apply(scenario, **override)
    assert "factory" not in scenario.calls


def test_non_ui_catalog_cannot_reach_docker(scenario):
    s = scenario
    s.config["containers"][UI]["role"] = "map-api"
    with pytest.raises(release.ServiceReleaseError, match="UI"):
        apply(s)
    assert "factory" not in s.calls


def test_image_revision_mismatch_cannot_mutate_or_publish(scenario):
    s = scenario
    s.metadata[NEW]["Config"]["Labels"]["org.opencontainers.image.revision"] = "f" * 40
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))
    assert release.read_current_release("map", root=s.root) == s.current


def test_unmerged_source_cannot_construct_docker(scenario):
    s = scenario
    (s.source / "file").write_text("unmerged")
    git(s.source, "commit", "-qam", "unmerged")
    with pytest.raises(release.ServiceReleaseError):
        apply(s, source_sha=git(s.source, "rev-parse", "HEAD"))
    assert "factory" not in s.calls


def test_wrong_repository_cannot_construct_docker(scenario):
    git(scenario.source, "remote", "set-url", "origin", "https://example.invalid/wrong.git")
    with pytest.raises(release.ServiceReleaseError):
        apply(scenario)
    assert "factory" not in scenario.calls


def test_g_contention_cannot_construct_docker_or_publish(scenario):
    s = scenario
    m = prepare(s)
    @contextmanager
    def busy():
        raise ManagerMutationActiveError("busy")
        yield
    s.monkeypatch.setattr(m, "manager_mutation_lock", busy)
    with pytest.raises(ManagerMutationActiveError):
        m.apply_ui_candidate("map", group="ui", image_id=NEW, source_sha=s.sha,
                             source_clone=s.source, root=s.root, config=s.config,
                             client_factory=s.factory, execute=True)
    assert "factory" not in s.calls
    assert release.read_current_release("map", root=s.root) == s.current


@pytest.mark.parametrize("kind", ["api", "db", "dagster"])
def test_nonselected_identity_change_never_publishes(scenario, kind):
    s = scenario
    s.on_mutation = lambda: s.records[kind].update(Id="changed")
    result = apply(s)
    assert result["success"] is False and result["nonselected_unchanged"] is False
    assert release.read_current_release("map", root=s.root) == s.current


@pytest.mark.parametrize("failure", ["exit", "health"])
def test_failed_ui_keeps_current_and_redacts_failure(scenario, failure):
    s = scenario
    if failure == "exit":
        s.mutation_exit = 1
    else:
        s.on_mutation = lambda: s.records[UI]["State"]["Health"].update(Status="unhealthy")
    result = apply(s)
    assert result["success"] is False
    assert release.read_current_release("map", root=s.root) == s.current
    assert "fixture$secret" not in json.dumps(result)


def test_current_pointer_race_before_mutation_is_rejected(scenario):
    s = scenario
    m = prepare(s)
    original = m._render
    def raced(*args, **kwargs):
        result = original(*args, **kwargs)
        s.monkeypatch.setattr(m, "_unchanged_pointers", Mock(side_effect=release.ServiceReleaseError("pointer changed")))
        return result
    s.monkeypatch.setattr(m, "_render", raced)
    with pytest.raises(release.ServiceReleaseError):
        m.apply_ui_candidate("map", group="ui", image_id=NEW, source_sha=s.sha,
                             source_clone=s.source, root=s.root, config=s.config,
                             client_factory=s.factory, execute=True)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def invoke_prepared(s, m, **kwargs):
    options = dict(group="ui", image_id=NEW, source_sha=s.sha, source_clone=s.source,
                   root=s.root, config=s.config, client_factory=s.factory, execute=True)
    options.update(kwargs)
    return m.apply_ui_candidate("map", **options)


def test_ui_restart_during_render_cannot_reach_mutation(scenario):
    s = scenario
    m = prepare(s)
    original = m._render
    def raced(*args, **kwargs):
        rendered = original(*args, **kwargs)
        s.records[UI]["RestartCount"] += 1
        return rendered
    s.monkeypatch.setattr(m, "_render", raced)
    with pytest.raises(release.ServiceReleaseError, match="UI identity"):
        invoke_prepared(s, m)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_receipt_race_during_render_cannot_reach_mutation(scenario):
    s = scenario
    m = prepare(s)
    original = m._render
    def raced(*args, **kwargs):
        rendered = original(*args, **kwargs)
        (s.root / "baseline").write_bytes(b"other committed receipt")
        return rendered
    s.monkeypatch.setattr(m, "_render", raced)
    with pytest.raises(release.ServiceReleaseError, match="기준선"):
        invoke_prepared(s, m)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_initial_component_cannot_hide_receipt_ui_mismatch(scenario):
    s = scenario
    m = prepare(s)
    original = m._baseline
    def mismatch(*args):
        path, raw, pin, status = original(*args)
        return path, raw, pin, SimpleNamespace(images={UI: OTHER, "api": OTHER, "dagster": OTHER})
    s.monkeypatch.setattr(m, "_baseline", mismatch)
    with pytest.raises(release.ServiceReleaseError, match="최초 component"):
        invoke_prepared(s, m)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_nonselected_receipt_image_must_match(scenario):
    s = scenario
    m = prepare(s)
    original = m._baseline
    def mismatch(*args):
        path, raw, pin, status = original(*args)
        return path, raw, pin, SimpleNamespace(images={UI: OLD, "api": NEW, "dagster": OTHER})
    s.monkeypatch.setattr(m, "_baseline", mismatch)
    with pytest.raises(release.ServiceReleaseError, match="비선택 이미지"):
        invoke_prepared(s, m)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_second_component_candidate_preserves_paired_receipt(scenario):
    s = scenario
    first = apply(s)
    assert (s.root / "baseline").read_bytes() == b"committed"
    s.resolved["services"][UI]["image"] = NEW
    newer = "sha256:" + "d" * 64
    s.metadata[newer] = {**copy.deepcopy(s.metadata[NEW]), "Id": newer}
    second = apply(s, image_id=newer)
    assert second["result"] == "healthy"
    assert second["current"]["previous"] == first["current"]["release_directory"]
    assert (s.root / "baseline").read_bytes() == b"committed"


def test_non_root_cannot_construct_docker(scenario):
    s = scenario
    m = prepare(s)
    s.monkeypatch.setattr(m.os, "geteuid", lambda: 1000)
    with pytest.raises(release.ServiceReleaseError, match="root"):
        invoke_prepared(s, m)
    assert "factory" not in s.calls


@pytest.mark.parametrize("kind", ["writable", "symlink", "alternates"])
def test_untrusted_source_cannot_construct_docker(scenario, kind):
    s = scenario
    if kind == "writable":
        (s.source / ".git/config").chmod(0o666)
    elif kind == "symlink":
        (s.source / ".git/bad").symlink_to(s.root)
    else:
        (s.source / ".git/objects/info/alternates").write_text(str(s.root))
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert "factory" not in s.calls


def test_ui_current_cannot_publish_when_pointer_changes_after_health(scenario):
    s = scenario
    m = prepare(s)
    original = m._unchanged_pointers
    calls = []
    def check(*args):
        calls.append(True)
        if len(calls) >= 4:
            raise release.ServiceReleaseError("current raced before publication")
        return original(*args)
    s.monkeypatch.setattr(m, "_unchanged_pointers", check)
    with pytest.raises(release.ServiceReleaseError, match="raced"):
        invoke_prepared(s, m)
    assert release.read_current_release("map", root=s.root) == s.current


def test_cli_forwards_approval_without_changing_existing_actions(scenario, capsys):
    from kor_travel_docker_manager import service_release_cli as entry
    s = scenario
    m = candidate_module()
    call = Mock(return_value={"success": True, "result": "planned"})
    s.monkeypatch.setattr(m, "apply_ui_candidate", call)
    s.monkeypatch.setattr(entry, "load_targets_config", lambda: s.config)
    args = cli.build_parser().parse_args([
        "service-release", "apply-candidate", "map", "--image-id", NEW,
        "--source-sha", s.sha, "--source-clone", str(s.source), "--dry-run"])
    assert args.func(args) == 0
    assert call.call_args.kwargs["execute"] is False
    assert json.loads(capsys.readouterr().out)["result"] == "planned"


def test_real_baseline_parser_requires_committed_matching_pair(scenario):
    from kor_travel_docker_manager.services import deploy_status as deploy
    s = scenario
    m = candidate_module()
    status = deploy.begin_deploy(None, run_id="00000000-0000-0000-0000-000000000001",
                                 started_at="2026-11-12T00:00:00Z", manager_revision="e" * 40,
                                 map_revision="a" * 40, pinvi_revision="b" * 40, pinset_sha256="c" * 64)
    databases = {role: deploy.DeployedDatabase("fixture", 1, "123") for role in ["map_application", "pinvi"]}
    committed = deploy.commit_deploy(status, committed_at="2026-11-12T00:01:00Z",
                                    images={UI: OLD, "api": OTHER, "dagster": OTHER}, schema_heads={"map_application": "300", "pinvi": "1"},
                                    databases=databases)
    path = s.root / "deploy-status.json"
    path.write_text(json.dumps(committed.to_payload()))
    path.chmod(0o600)
    pin = SimpleNamespace(pinset_sha256="c" * 64,
                          source_for=lambda role: SimpleNamespace(revision={"map": "a" * 40, "pinvi": "b" * 40}[role]),
                          to_payload=lambda: {"pinset_sha256": "c" * 64})
    s.monkeypatch.setattr(m, "_capture_compose_environment_snapshot", lambda **kw: SimpleNamespace(effective={}))
    s.monkeypatch.setattr(m, "pinned_runtime_state_root", lambda values: s.root)
    s.monkeypatch.setattr(m, "current_pinned_runtime_release", lambda: pin)
    s.monkeypatch.setattr(m, "runtime_topology", lambda: SimpleNamespace(
        runtime_services=[UI, "api", "dagster"], retired_services=(),
        service=lambda slot: UI if slot == "map_ui" else "dagster" if "dagster" in slot else "api",
        families={"map": SimpleNamespace(carrier="dagster"), "pinvi": SimpleNamespace(carrier="dagster")},
    ))
    selected = s.profile["groups"]["ui"]
    assert m._baseline(selected)[3] == committed
    before = path.read_bytes()
    pin.pinset_sha256 = "d" * 64
    with pytest.raises(release.ServiceReleaseError, match="pair pin"):
        m._baseline(s.profile["groups"]["ui"])
    assert path.read_bytes() == before
    pin.pinset_sha256 = "c" * 64
    path.write_text(json.dumps(status.to_payload()))
    with pytest.raises(release.ServiceReleaseError, match="committed"):
        m._baseline(s.profile["groups"]["ui"])


@pytest.mark.parametrize("kind", ["api-role", "multiple", "oneshot"])
def test_scope_cannot_expand_beyond_single_ui(scenario, kind):
    s = scenario
    group = s.config["service_releases"]["map"]["groups"]["ui"]
    if kind == "api-role":
        group["runtime_services"] = ["api"]
    elif kind == "multiple":
        group["runtime_services"].append("api")
    else:
        group["oneshot_services"] = ["migrate"]
    with pytest.raises(release.ServiceReleaseError, match="UI"):
        apply(s)
    assert "factory" not in s.calls


def test_candidate_wrong_oci_repository_cannot_mutate(scenario):
    s = scenario
    s.metadata[NEW]["Config"]["Labels"]["org.opencontainers.image.source"] = "https://example.invalid/other"
    with pytest.raises(release.ServiceReleaseError, match="OCI source"):
        apply(s)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_unreadable_redaction_input_cannot_mutate(scenario):
    s = scenario
    m = prepare(s)
    s.monkeypatch.setattr(m.frozen, "load_secret_environment", Mock(side_effect=PermissionError("private path")))
    with pytest.raises(release.ServiceReleaseError, match="비밀 가림 입력") as error:
        invoke_prepared(s, m)
    assert "private path" not in str(error.value)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_render_failure_is_redacted_without_mutation(scenario):
    s = scenario
    m = prepare(s)
    s.monkeypatch.setattr(m, "_render", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "fixture$secret error"))
    result = invoke_prepared(s, m)
    assert result["success"] is False
    assert "fixture$secret" not in json.dumps(result)
    assert release.read_current_release("map", root=s.root) == s.current


def test_interrupted_child_never_publishes_current(scenario):
    s = scenario
    m = prepare(s)
    run = m.frozen._run_mutation
    s.monkeypatch.setattr(m.frozen, "_run_mutation", lambda *a, **kw: (run(*a, **kw)[0], ["SIGTERM"]))
    result = invoke_prepared(s, m)
    assert result["success"] is False
    assert result["interrupted_signals"] == ["SIGTERM"]
    assert release.read_current_release("map", root=s.root) == s.current


def test_missing_source_clone_cannot_construct_docker(scenario):
    with pytest.raises(release.ServiceReleaseError, match="source clone"):
        apply(scenario, source_clone=scenario.root / "missing")
    assert "factory" not in scenario.calls


def test_review_b_rejects_external_common_directory(scenario):
    import shutil
    s = scenario
    external = s.root / "external.git"
    shutil.copytree(s.source / ".git", external)
    (external / "config").chmod(0o666)
    (s.source / ".git/commondir").write_text(str(external))
    with pytest.raises(release.ServiceReleaseError):
        candidate_module()._merged_source(s.source, s.sha, "map")


def test_review_b_rejects_nonselected_hostconfig_update(scenario):
    s = scenario
    s.records["db"]["HostConfig"] = {"Binds": [], "Memory": 1024, "RestartPolicy": {"Name": "always"}}
    s.on_mutation = lambda: s.records["db"]["HostConfig"].update(Memory=64)
    result = apply(s)
    assert result["success"] is False and result["nonselected_unchanged"] is False
    assert release.read_current_release("map", root=s.root) == s.current


def test_review_b_rejects_incomplete_paired_images(scenario):
    s = scenario
    m = prepare(s)
    s.monkeypatch.setattr(m, "_baseline", lambda *args: (s.root / "baseline", b"committed", {}, SimpleNamespace(images={UI: OLD})))
    with pytest.raises(release.ServiceReleaseError, match="필수 paired"):
        invoke_prepared(s, m)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_review_b_process_environment_cannot_select_shadow_receipt(scenario):
    from pathlib import Path

    from kor_travel_docker_manager.services import compose_service as compose
    from kor_travel_docker_manager.services import deploy_status as deploy
    s = scenario
    m = candidate_module()
    canonical_root, shadow_root = s.root / "canonical", s.root / "shadow"
    canonical = canonical_root / "manager"
    shadow = shadow_root / "shadow"
    canonical.mkdir(parents=True)
    shadow.mkdir(parents=True)
    status = deploy.begin_deploy(None, run_id="00000000-0000-0000-0000-000000000001",
                                 started_at="2026-11-12T00:00:00Z", manager_revision="e" * 40,
                                 map_revision="a" * 40, pinvi_revision="b" * 40, pinset_sha256="c" * 64)
    dbs = {role: deploy.DeployedDatabase("fixture", 1, "123") for role in ["map_application", "pinvi"]}
    committed = deploy.commit_deploy(status, committed_at="2026-11-12T00:01:00Z",
                                    images={UI: OLD, "api": OTHER, "dagster": OTHER},
                                    schema_heads={"map_application": "300", "pinvi": "1"}, databases=dbs)
    for directory, value in [(canonical, status), (shadow, committed)]:
        path = directory / "deploy-status.json"
        path.write_text(json.dumps(value.to_payload()))
        path.chmod(0o600)
    env = Path(s.profile["groups"]["ui"]["env_file"])
    env.write_text("COMPOSE_PROJECT_NAME=manager\nKTDM_PINNED_RUNTIME_STATE_ROOT=" + str(canonical_root) + "\n")
    s.monkeypatch.setattr(compose, "get_env_path", lambda: str(env))
    s.monkeypatch.setattr(compose, "get_compose_path", lambda: str(s.root / "missing"))
    s.monkeypatch.setenv("COMPOSE_PROJECT_NAME", "shadow")
    s.monkeypatch.setenv("KTDM_PINNED_RUNTIME_STATE_ROOT", str(shadow_root))
    pin = SimpleNamespace(pinset_sha256="c" * 64,
                          source_for=lambda role: SimpleNamespace(revision={"map": "a" * 40, "pinvi": "b" * 40}[role]),
                          to_payload=lambda: {"pinset_sha256": "c" * 64})
    s.monkeypatch.setattr(m, "current_pinned_runtime_release", lambda: pin)
    with pytest.raises(release.ServiceReleaseError, match="committed"):
        m._baseline(s.profile["groups"]["ui"])


@pytest.mark.parametrize("kind", ["exit", "interrupted", "launch"])
def test_review_a_failed_child_always_reports_post_observation(scenario, kind):
    s = scenario
    m = prepare(s)
    if kind == "exit":
        s.mutation_exit = 1
        s.on_mutation = lambda: s.records["db"].update(Id="external-change")
    elif kind == "interrupted":
        run = m.frozen._run_mutation
        s.monkeypatch.setattr(m.frozen, "_run_mutation", lambda *a, **kw: (run(*a, **kw)[0], ["SIGTERM"]))
    else:
        s.monkeypatch.setattr(m.frozen, "_run_mutation", Mock(side_effect=OSError("fixture$secret")))
    result = invoke_prepared(s, m)
    assert result["success"] is False
    assert "post_verification" in result
    assert result["nonselected_unchanged"] is (kind != "exit")
    assert result["current_unchanged"] is True
    assert result["baseline_unchanged"] is True
    assert release.read_current_release("map", root=s.root) == s.current
    assert "fixture$secret" not in json.dumps(result)


def test_missing_companion_image_cannot_reach_ui_mutation(scenario):
    s = scenario
    record = copy.deepcopy(s.records["api"])
    record["Id"] = "cid-companion"
    record["Config"]["Labels"]["com.docker.compose.service"] = "companion"
    s.records["companion"] = record
    s.resolved["services"]["companion"] = {"image": OTHER}
    with pytest.raises(release.ServiceReleaseError, match="companion"):
        apply(s)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_recheck_rejects_drifted_missing_companion(scenario):
    s = scenario
    record = copy.deepcopy(s.records["api"])
    record["Id"] = "cid-companion"
    record["Image"] = NEW
    record["Config"]["Labels"]["com.docker.compose.service"] = "companion"
    s.records["companion"] = record
    s.resolved["services"]["companion"] = {"image": OTHER}
    with pytest.raises(release.ServiceReleaseError, match="companion"):
        apply(s)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_recheck_observed_post_guard_failure_is_terminal(scenario):
    s = scenario
    m = prepare(s)
    s.records["db"]["HostConfig"] = {"Memory": 1024}
    s.on_mutation = lambda: s.records["db"]["HostConfig"].update(Memory=2048)
    original = m._unchanged_others
    observed = []
    def detect_then_restore(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except release.ServiceReleaseError:
            observed.append(True)
            s.records["db"]["HostConfig"]["Memory"] = 1024
            raise
    s.monkeypatch.setattr(m, "_unchanged_others", detect_then_restore)
    result = invoke_prepared(s, m)
    assert observed
    assert result["success"] is False
    assert result["nonselected_unchanged"] is False
    assert release.read_current_release("map", root=s.root) == s.current


@pytest.mark.parametrize("state", ["normal", "drift", "absent"])
def test_canonical_companion_reference_requires_receipt_in_all_live_states(scenario, state):
    s = scenario
    s.resolved["services"]["companion"] = {"image": "fixture:paired"}
    get = s.client.images.get
    s.client.images.get = lambda ref: get(OTHER if ref == "fixture:paired" else ref)
    if state != "absent":
        record = copy.deepcopy(s.records["api"])
        record["Id"] = "cid-companion"
        record["Config"]["Labels"]["com.docker.compose.service"] = "companion"
        record["Image"] = OTHER if state == "normal" else NEW
        s.records["companion"] = record
    with pytest.raises(release.ServiceReleaseError, match="companion"):
        apply(s)
    assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))


def test_late_guard_failure_keeps_failed_diagnostic_flag(scenario):
    s = scenario
    m = prepare(s)
    original = m._unchanged_pointers
    calls = []
    def fail_late(*args):
        calls.append(True)
        if len(calls) == 3:
            raise release.ServiceReleaseError("late current failure")
        return original(*args)
    s.monkeypatch.setattr(m, "_unchanged_pointers", fail_late)
    result = invoke_prepared(s, m)
    assert result["success"] is False
    assert result["current_unchanged"] is False
    assert release.read_current_release("map", root=s.root) == s.current


@pytest.mark.parametrize("image", [OTHER, NEW])
def test_companion_receipt_owner_relation_is_checked(scenario, image):
    s = scenario
    m = prepare(s)
    record = copy.deepcopy(s.records["api"])
    record["Id"] = "cid-companion"
    record["Image"] = image
    record["Config"]["Labels"]["com.docker.compose.service"] = "companion"
    s.records["companion"] = record
    s.resolved["services"]["companion"] = {"image": OTHER}
    baseline = m._baseline(s.profile["groups"]["ui"])
    baseline[3].images["companion"] = image
    if image == OTHER:
        result = invoke_prepared(s, m)
        assert result["success"] is True
        assert set(result["current"]["images"]) == {UI}
    else:
        with pytest.raises(release.ServiceReleaseError, match="companion"):
            invoke_prepared(s, m)
        assert not any("up" in c[0] for c in s.calls if isinstance(c, tuple))
