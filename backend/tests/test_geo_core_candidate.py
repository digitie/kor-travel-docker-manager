"""Geo API/CS 후보의 source/native·quiet·rollback 경계만 검증한다."""
# ruff: noqa: F811
import argparse
import copy
import hashlib
import json
import subprocess

import pytest
import test_service_release_candidate as paired
from test_transport_core_candidate import core_case, scenario  # noqa: F401

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import service_release_geo_candidate as geo
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.errors import ManagerMutationActiveError
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    geo_core_candidate_for_target,
    load_targets_config,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


@pytest.fixture
def geo_case(core_case, monkeypatch):
    s = core_case
    cfg = copy.deepcopy(load_targets_config())
    s.config = {"targets": {"geo": copy.deepcopy(cfg["targets"]["geo"])},
                "containers": cfg["containers"],
                "service_releases": {"geo": copy.deepcopy(cfg["service_releases"]["geo"])}}
    group = s.config["service_releases"]["geo"]["groups"]["main"]
    group.update(working_dir=str(s.source.parent), config_files=[str(s.source.parent / "compose.yml")],
                 env_file=str(s.source.parent / ".env.prod"))
    (s.source.parent / "compose.yml").write_text("services: {}\n")
    s.project = group["project"]
    s.lease = s.source.parent / "project.lock"
    for i, name in enumerate([*geo.RUNTIME, geo.UI], 1000):
        record = copy.deepcopy(s.records["backend"])
        record["Id"] = f"{i:064x}"
        record["Config"]["Labels"] = {"com.docker.compose.project": s.project,
                                     "com.docker.compose.service": name}
        record["Config"]["Env"] = ["KTG_PG_DSN=postgresql+psycopg://fixture@localhost/test",
                                   "PASSWORD=fixture$secret", "LITERAL=a$$b"]
        s.records[name] = record
    paired.git(s.source, "remote", "set-url", "origin", geo.URL)
    versions = s.source / "alembic/versions"
    versions.mkdir(parents=True)
    (versions / "0029.py").write_text("revision='0029_geo'\ndown_revision=None\n")
    (s.source / "pyproject.toml").write_text(
        "[project]\nname='kor-travel-geo'\ndependencies=['psycopg[binary,pool]==3.3.9']\n")
    paired.git(s.source, "add", "alembic", "pyproject.toml")
    paired.git(s.source, "commit", "-qm", "approved Geo driver fixture")
    s.sha = paired.git(s.source, "rev-parse", "HEAD")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", s.sha)
    s.tree = paired.git(s.source, "rev-parse", "HEAD^{tree}")
    images = {"api": "sha256:" + "8" * 64, "code-server": "sha256:" + "9" * 64}
    s.normal = {"version": 1, "kind": "geo-normal-build", "normalDockerBuild": True,
                "mode": "NORMAL_BUILD_ONLY", "actualApplyAllowed": False, "deployReady": False,
                "source": geo.URL, "release_sha": s.sha, "tree": s.tree, "output": "docker",
                "architecture": "arm64", "images": images, "artifacts": {}}
    for role, image in images.items():
        digest = hashlib.sha256(role.encode()).hexdigest()
        s.normal["artifacts"][role] = {"imageID": image, "loaded": True, "normalDockerBuild": True,
                                      "architecture": "arm64", "COPY": {"sha256": digest}}
        s.metadata[image] = {"Id": image, "Architecture": "arm64", "Os": "linux",
                            "Created": "2026-11-19T00:00:00Z", "Config": {"Env": [], "Labels": {
                                "org.opencontainers.image.revision": s.sha,
                                "org.opencontainers.image.source": geo.URL,
                                "io.kor-travel-geo.source-tree": s.tree,
                                "io.kor-travel-geo.build-role": role,
                                "io.kor-travel-geo.copy-sha256": digest}}}
    s.runtime = {"version": 1, "release_sha": s.sha, "images": images.copy(),
                 "schema_revisions": ["0029_geo"],
                 "source_container": s.records[geo.ROLES["code-server"]]["Id"],
                 "instigators": [
                     {"location": geo.LOCATION, "repository": "geo-repo", "kind": "schedule",
                      "name": "collect", "status": "RUNNING"},
                     {"location": "unselected", "repository": "other", "kind": "sensor",
                      "name": "keep", "status": "RUNNING"}],
                 "native": {"status": "PASS", "release_sha": s.sha, "tree": s.tree,
                            "images": images.copy(), "drivers": {
                                role: {"psycopg": "3.3.9", "psycopg-binary": "3.3.9",
                                       "implementation": "binary"} for role in geo.ROLES}}}
    s.live_instigators = copy.deepcopy(s.runtime["instigators"])
    s.live_instigators[0]["status"] = "STOPPED"
    s.schema = ["0029_geo"]
    s.resolved = {"services": {n: {"image": r["Image"], "environment": {}, "volumes": []}
                              for n, r in s.records.items()}}
    profile = entry._profile("geo", entry._declaration("geo", s.config), [])
    s.current = release.adopt_release(profile, root=s.root, client=s.client)
    monkeypatch.setattr(geo, "manager_mutation_lock", core.manager_mutation_lock)
    monkeypatch.setattr(geo, "get_env_path", core.get_env_path)
    monkeypatch.setattr(geo, "geo_core_candidate_for_target", lambda *a: ("main", s.lease))

    def probe(cid, script, fd):
        state.check_lease(fd, s.lease)
        return list(s.schema) if script == geo._SCHEMA_PROBE else list(s.workers)
    monkeypatch.setattr(core, "_probe", probe)
    s.before = copy.deepcopy(s.records)
    s.mutations = []
    s.fail_at = None

    def mutate(argv, *, additional_fds, **kwargs):
        assert s.locked and len(additional_fds) == 1
        state.check_lease(additional_fds[0], s.lease)
        assert "--no-build" in argv and "--no-deps" in argv and "never" in argv
        names = argv[argv.index("--wait-timeout") + 2:]
        assert set(names) <= geo.RUNTIME and geo.UI not in names
        document = json.loads(kwargs["input"])
        s.mutations.append(names)
        for n in names:
            record = s.records[n]
            record["Id"] = f"{2000 + len(s.mutations) + len(n):064x}"
            record["Image"] = document["services"][n]["image"]
            record["State"].update(Running=True, Status="running", StartedAt="new", Health={"Status": "healthy"})
            record["Config"]["Env"] = [k + "=" + v.replace("$$", "$")
                                       for k, v in document["services"][n]["environment"].items()]
        if hook := s.hooks.get("after_mutation"):
            hook()
        code = 1 if len(s.mutations) == s.fail_at else 0
        return subprocess.CompletedProcess(argv, code, "", "fixture$secret"), []
    monkeypatch.setattr(geo.frozen, "_run_mutation", mutate)
    return s


def apply_geo(s, **kwargs):
    s.receipt_path.write_text(json.dumps(s.runtime))
    s.build_path.write_text(json.dumps(s.normal))
    return geo.apply_geo_core_candidate(
        kwargs.pop("service", "geo"), source_clone=s.source, source_sha=s.sha, root=s.root,
        config=s.config, receipt_path=s.receipt_path,
        receipt_sha256=hashlib.sha256(s.receipt_path.read_bytes()).hexdigest(),
        build_receipt_path=s.build_path,
        build_receipt_sha256=hashlib.sha256(s.build_path.read_bytes()).hexdigest(),
        client_factory=s.factory, **kwargs)


def test_registry_geo_exact_existing_roles_and_lease():
    cfg = copy.deepcopy(load_targets_config())
    group, lease = geo_core_candidate_for_target("geo", cfg)
    assert group == "main" and str(lease).endswith("/geo-core-deploy.lock")
    cfg["service_releases"]["geo"]["groups"]["main"]["runtime_services"].append("extra")
    with pytest.raises(TargetsConfigError):
        geo_core_candidate_for_target("geo", cfg)


def test_geo_success_preserves_ui_other_environment_and_instigators(geo_case):
    s = geo_case
    original_states = copy.deepcopy(s.live_instigators)
    result = apply_geo(s, execute=True)
    assert result["success"] and result["schemaUnchanged"] and result["nonselectedUnchanged"]
    assert s.mutations == [[geo.ROLES["api"]], [geo.ROLES["code-server"]]]
    current = release.read_current_release("geo", root=s.root)
    for role, name in geo.ROLES.items():
        assert current["images"][name]["id"] == s.normal["images"][role]
        assert s.records[name]["Config"]["Env"] == s.before[name]["Config"]["Env"]
    for name in s.before.keys() - geo.RUNTIME:
        assert s.records[name] == s.before[name]
    assert s.live_instigators == original_states
    assert "fixture$secret" not in json.dumps(result)


def test_geo_dry_does_not_select_or_publish_new_images(geo_case):
    s = geo_case
    previous = release.read_current_release("geo", root=s.root)
    result = apply_geo(s, execute=False)
    assert result["success"] and result["result"] == "planned"
    assert s.records == s.before and not s.mutations
    assert release.read_current_release("geo", root=s.root) == previous


@pytest.mark.parametrize("field", ["wrong_source", "native_image", "native_driver", "native_impl",
                                  "extra_role", "schema", "copy_hash", "normal_mode"])
def test_geo_bad_tuple_fails_before_client_and_mutation(geo_case, field):
    s = geo_case
    if field == "wrong_source":
        s.runtime["release_sha"] = "0" * 40
    elif field == "native_image":
        s.runtime["native"]["images"]["api"] = "sha256:" + "1" * 64
    elif field == "native_driver":
        s.runtime["native"]["drivers"]["api"]["psycopg-binary"] = "3.3.6"
    elif field == "native_impl":
        s.runtime["native"]["drivers"]["api"]["implementation"] = "python"
    elif field == "extra_role":
        s.normal["images"]["ui"] = "sha256:" + "1" * 64
    elif field == "schema":
        s.runtime["schema_revisions"] = ["0000_old"]
    elif field == "copy_hash":
        s.normal["artifacts"]["api"]["COPY"]["sha256"] = "bad"
    else:
        s.normal["normalDockerBuild"] = False
    with pytest.raises(ServiceReleaseError):
        apply_geo(s, execute=True)
    assert not s.steps and not s.mutations and s.records == s.before


@pytest.mark.parametrize("service", ["map", "weather", "transport", "conc"])
def test_other_targets_cannot_reach_docker(geo_case, service):
    s = geo_case
    with pytest.raises(ServiceReleaseError):
        apply_geo(s, service=service)
    assert not s.steps and not s.mutations


def test_unmerged_geo_source_rejected(geo_case):
    s = geo_case
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", "HEAD~1")
    with pytest.raises(ServiceReleaseError):
        apply_geo(s, execute=True)
    assert not s.steps and not s.mutations


def test_g_busy_retains_typed_code_before_client(geo_case, monkeypatch):
    s = geo_case
    error = ManagerMutationActiveError("busy")
    def busy(): raise error
    monkeypatch.setattr(geo, "manager_mutation_lock", busy)
    with pytest.raises(ManagerMutationActiveError) as caught:
        apply_geo(s, execute=True)
    assert caught.value is error and caught.value.code == "MANAGER_MUTATION_ACTIVE"
    assert not s.steps and not s.mutations


@pytest.mark.parametrize("kind", ["active", "running_instigator", "ambiguous", "worker"])
def test_geo_not_quiet_cannot_select_images_or_mutate(geo_case, monkeypatch, kind):
    s = geo_case
    if kind == "active":
        s.runs = [{"repositoryOrigin": {"repositoryLocationName": geo.LOCATION}, "tags": []}]
    elif kind == "running_instigator":
        s.live_instigators[0]["status"] = "RUNNING"
    elif kind == "worker":
        s.workers = [123]
    else:
        s.runs = [{"repositoryOrigin": None, "tags": []}]
    def forbidden(*a, **k): raise AssertionError("selection before quiet")
    monkeypatch.setattr(geo, "_images", forbidden)
    with pytest.raises(ServiceReleaseError):
        apply_geo(s, execute=True)
    assert not s.mutations


@pytest.mark.parametrize("failure_at", [1, 2])
def test_geo_failed_switch_rolls_back_captured_images_only(geo_case, failure_at):
    s = geo_case
    previous = release.read_current_release("geo", root=s.root)
    s.fail_at = failure_at
    original_states = copy.deepcopy(s.live_instigators)
    result = apply_geo(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert set(s.mutations[-1]) == ({geo.ROLES["api"]} if failure_at == 1 else geo.RUNTIME)
    for n in geo.RUNTIME:
        assert s.records[n]["Image"] == s.before[n]["Image"]
        assert s.records[n]["Config"]["Env"] == s.before[n]["Config"]["Env"]
    for n in s.before.keys() - geo.RUNTIME:
        assert s.records[n] == s.before[n]
    assert release.read_current_release("geo", root=s.root)["images"] == previous["images"]
    assert s.live_instigators == original_states


def test_geo_live_schema_mismatch_refuses_mutation(geo_case):
    s = geo_case
    s.schema = ["different"]
    with pytest.raises(ServiceReleaseError):
        apply_geo(s, execute=True)
    assert not s.mutations


def test_late_geo_run_never_recreates_unchanged_cs_during_api_rollback(geo_case):
    s = geo_case
    def started():
        s.runs = [{"repositoryOrigin": {"repositoryLocationName": geo.LOCATION}, "tags": []}]
    s.hooks["after_mutation"] = started
    result = apply_geo(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert s.mutations == [[geo.ROLES["api"]], [geo.ROLES["api"]]]
    assert s.records[geo.ROLES["code-server"]] == s.before[geo.ROLES["code-server"]]


def test_post_health_failure_restores_old_pair(geo_case):
    s = geo_case
    def unhealthy():
        if len(s.mutations) == 2:
            s.records[geo.ROLES["code-server"]]["State"]["Health"] = {"Status": "unhealthy"}
    s.hooks["after_mutation"] = unhealthy
    result = apply_geo(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert all(s.records[n]["Image"] == s.before[n]["Image"] for n in geo.RUNTIME)


def test_native_image_label_tamper_never_mutates(geo_case):
    s = geo_case
    s.metadata[s.normal["images"]["api"]]["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    with pytest.raises(ServiceReleaseError):
        apply_geo(s, execute=True)
    assert not s.mutations


def test_receipt_checksum_tamper_rejected_before_client(geo_case):
    s = geo_case
    s.receipt_path.write_text(json.dumps(s.runtime))
    checksum = hashlib.sha256(s.receipt_path.read_bytes()).hexdigest()
    s.receipt_path.write_text(json.dumps({**s.runtime, "source_container": "0" * 64}))
    s.build_path.write_text(json.dumps(s.normal))
    with pytest.raises(ServiceReleaseError):
        geo.apply_geo_core_candidate(
            "geo", source_clone=s.source, source_sha=s.sha, root=s.root, config=s.config,
            receipt_path=s.receipt_path, receipt_sha256=checksum, build_receipt_path=s.build_path,
            build_receipt_sha256=hashlib.sha256(s.build_path.read_bytes()).hexdigest(),
            execute=True, client_factory=s.factory)
    assert not s.steps and not s.mutations


def test_cli_dispatches_explicit_geo_adapter(monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    entry.add_service_release_parser(parser.add_subparsers())
    args = parser.parse_args(["service-release", "apply-core-candidate", "geo",
        "--source-clone", "/approved/source", "--source-sha", "a" * 40,
        "--build-receipt", "/approved/normal.json", "--build-sha256", "b" * 64,
        "--receipt", "/approved/runtime.json", "--sha256", "c" * 64, "--dry-run"])
    monkeypatch.setattr(entry, "current_host", lambda config: "odroid")
    monkeypatch.setattr(entry, "target_hosts", lambda service, config: frozenset({"odroid"}))
    called = []
    monkeypatch.setattr(geo, "apply_geo_core_candidate",
                        lambda service, **kwargs: called.append(service) or {"success": True})
    assert entry._run(args) == 0
    assert called == ["geo"] and "true" in capsys.readouterr().out


def test_new_cs_worker_defers_cs_rollback_without_termination(geo_case):
    s = geo_case
    def started():
        if len(s.mutations) == 2:
            s.runs = [{"repositoryOrigin": {"repositoryLocationName": geo.LOCATION}, "tags": []}]
    s.hooks["after_mutation"] = started
    result = apply_geo(s, execute=True)
    assert not result["success"] and not result["rollbackImagesRestored"]
    assert result["rollbackDeferredForQuiet"] and result["rollbackErrorType"] == "ServiceReleaseError"
    assert s.mutations == [[geo.ROLES["api"]], [geo.ROLES["code-server"]], [geo.ROLES["api"]]]
    assert s.records[geo.ROLES["api"]]["Image"] == s.before[geo.ROLES["api"]]["Image"]
    assert s.records[geo.ROLES["code-server"]]["Image"] == s.normal["images"]["code-server"]
    assert s.runs


def test_cancel_after_partial_api_switch_restores_and_propagates(geo_case):
    s = geo_case
    def cancel():
        if len(s.mutations) == 1:
            raise KeyboardInterrupt
    s.hooks["after_mutation"] = cancel
    with pytest.raises(KeyboardInterrupt):
        apply_geo(s, execute=True)
    assert s.mutations == [[geo.ROLES["api"]], [geo.ROLES["api"]]]
    assert all(s.records[n]["Image"] == s.before[n]["Image"] for n in geo.RUNTIME)
