"""Pinvi API/CS 후보의 source/native·quiet·rollback 경계만 검증한다."""
# ruff: noqa: F811
import argparse
import copy
import hashlib
import json
import subprocess
from types import SimpleNamespace

import docker
import pytest
import test_service_release_candidate as paired
from test_transport_core_candidate import core_case, scenario  # noqa: F401

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import service_release_pinvi_candidate as pinvi
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.errors import ManagerMutationActiveError
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    load_targets_config,
    pinvi_core_candidate_for_target,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


@pytest.fixture
def pinvi_case(core_case, monkeypatch):
    s = core_case
    cfg = copy.deepcopy(load_targets_config())
    s.config = {"targets": {"pinvi": copy.deepcopy(cfg["targets"]["pinvi"])},
                "containers": cfg["containers"],
                "service_releases": {"pinvi": copy.deepcopy(cfg["service_releases"]["pinvi"])}}
    group = s.config["service_releases"]["pinvi"]["groups"]["ui"]
    group.update(working_dir=str(s.source.parent), config_files=[str(s.source.parent / "compose.yml")],
                 env_file=str(s.source.parent / ".env.prod"))
    (s.source.parent / "compose.yml").write_text("services: {}\n")
    for other in s.config["service_releases"]["pinvi"]["groups"].values():
        for key in ("working_dir", "config_files", "env_file"):
            other[key] = copy.deepcopy(group[key])
    s.project = group["project"]
    s.lease = s.source.parent / "project.lock"
    for i, name in enumerate([*pinvi.RUNTIME, pinvi.UI], 1000):
        record = copy.deepcopy(s.records["backend"])
        record["Id"] = f"{i:064x}"
        record["Config"]["Labels"] = {"com.docker.compose.project": s.project,
                                     "com.docker.compose.service": name}
        record["Config"]["Env"] = ["PINVI_DATABASE_URL=postgresql+asyncpg://fixture@localhost/test",
                                   "PINVI_SOURCE_REVISION=" + "0" * 40,
                                   "PASSWORD=fixture$secret", "LITERAL=a$$b"]
        s.records[name] = record
    paired.git(s.source, "remote", "set-url", "origin", pinvi.URL)
    versions = s.source / "apps/api/alembic/versions"
    versions.mkdir(parents=True)
    (versions / "0110.py").write_text("revision='0110_pinvi'\ndown_revision=None\n")
    s.expected = {"versions": {}, "common": {}}
    common = "6" * 40
    for role, package in (("api", "api"), ("code-server", "etl")):
        root = s.source / "apps" / package
        root.mkdir(parents=True, exist_ok=True)
        pins = {"sqlalchemy": "2.1.9", "asyncpg": "0.32.0", "pydantic": "2.14.4",
                "pydantic-core": "2.50.5", "httpx": "0.28.1"}
        if role == "code-server":
            pins["psycopg2-binary"] = "2.9.13"
        s.expected["versions"][role] = pins
        s.expected["common"][role] = common
        (root / "uv.lock").write_text("\n".join(
            f"[[package]]\nname='{n}'\nversion='{v}'\n" for n, v in pins.items()))
        dep = f"kor-travel-common[http] @ git+https://github.com/digitie/kor-travel-common.git@{common}#subdirectory=packages/py/kor-travel-common"
        (root / "pyproject.toml").write_text(f"[project]\nname='pinvi-{package}'\ndependencies=['{dep}']\n")
    paired.git(s.source, "add", "apps")
    paired.git(s.source, "commit", "-qm", "approved Pinvi source fixture")
    s.sha = paired.git(s.source, "rev-parse", "HEAD")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", s.sha)
    s.tree = paired.git(s.source, "rev-parse", "HEAD^{tree}")
    images = {"api": "sha256:" + "8" * 64, "code-server": "sha256:" + "9" * 64}
    s.normal = {"version": 1, "kind": "pinvi-normal-build", "normalDockerBuild": True,
                "mode": "NORMAL_BUILD_ONLY", "actualApplyAllowed": False, "deployReady": False,
                "source": pinvi.URL, "release_sha": s.sha, "tree": s.tree, "output": "docker",
                "architecture": "amd64", "images": images, "artifacts": {}}
    for role, image in images.items():
        digest = hashlib.sha256(role.encode()).hexdigest()
        s.normal["artifacts"][role] = {"imageID": image, "loaded": True, "normalDockerBuild": True,
                                      "architecture": "amd64", "COPY": {"sha256": digest}}
        s.metadata[image] = {"Id": image, "Architecture": "amd64", "Os": "linux",
                            "Created": "2026-11-19T00:00:00Z", "Config": {"Env": ["PINVI_SOURCE_REVISION=" + s.sha], "Labels": {
                                "org.opencontainers.image.revision": s.sha,
                                "org.opencontainers.image.source": pinvi.URL,
                                "io.pinvi.source-tree": s.tree,
                                "io.pinvi.build-role": role,
                                "io.pinvi.copy-sha256": digest}}}
    s.runtime = {"version": 1, "release_sha": s.sha, "images": images.copy(),
                 "schema_revisions": ["0110_pinvi"],
                 "source_container": s.records[pinvi.ROLES["code-server"]]["Id"],
                 "instigators": [
                     {"location": pinvi.LOCATION, "repository": "pinvi-repo", "kind": "schedule",
                      "name": "collect", "status": "RUNNING"},
                     {"location": "unselected", "repository": "other", "kind": "sensor",
                      "name": "keep", "status": "RUNNING"}],
                 "native": {"status": "PASS", "release_sha": s.sha, "tree": s.tree,
                            "images": images.copy(), **copy.deepcopy(s.expected),
                            "unusedPsycopg3Absent": True}}
    s.live_instigators = copy.deepcopy(s.runtime["instigators"])
    s.live_instigators[0]["status"] = "STOPPED"
    s.schema = ["0110_pinvi"]
    s.resolved = {"services": {n: {"image": r["Image"], "environment": {}, "volumes": []}
                              for n, r in s.records.items()}}
    legacy_config = copy.deepcopy(s.config)
    del legacy_config["service_releases"]["pinvi"]["groups"]["core"]
    profile = entry._profile("pinvi", entry._declaration("pinvi", legacy_config), [])
    s.current = release.adopt_release(profile, root=s.root, client=s.client)
    slots = {"map_ui": "kor-travel-map-ui", "map_api": "kor-travel-map-api",
             "map_dagster": "kor-travel-map-dagster-code-server",
             "map_dagster_daemon": "kor-travel-map-dagster-daemon", "pinvi_web": pinvi.UI,
             "pinvi_api": pinvi.ROLES["api"], "pinvi_dagster": pinvi.ROLES["code-server"]}
    for index, name in enumerate([slots[k] for k in ("map_ui", "map_api", "map_dagster", "map_dagster_daemon")] + ["shared-daemon"], 3000):
        record = copy.deepcopy(s.records[pinvi.UI])
        record["Id"] = f"{index:064x}"
        record["Config"]["Labels"]["com.docker.compose.service"] = name
        image = "sha256:" + f"{index:064x}"
        record["Image"] = image
        record["Config"]["Healthcheck"] = {"Test": ["CMD", "true"]}
        record["State"].update(Pid=index, Running=True, Health={"Status": "healthy"})
        s.metadata[image] = {"Id": image, "Created": "2026-11-19T00:00:00Z", "Config": {"Env": [], "Labels": {
            "org.opencontainers.image.revision": "0" * 40,
            "org.opencontainers.image.source": "https://github.com/digitie/kor-travel-map.git"}}}
        s.records[name] = record
        s.resolved["services"][name] = {"image": record["Image"], "environment": {}}
    monkeypatch.setattr(pinvi.candidate, "runtime_topology", lambda: SimpleNamespace(
        service=lambda slot: slots[slot], runtime_services=list(slots.values())))
    s.pair_images = {name: s.records[name]["Image"] for name in slots.values()}
    s.pair_path = s.source.parent / "pair-committed.json"
    s.pair_path.write_text(json.dumps({"images": s.pair_images, "pinset": "unchanged"}))
    s.pair_before = s.pair_path.read_bytes()
    s.config["targets"]["map"] = copy.deepcopy(cfg["targets"]["map"])
    map_group = copy.deepcopy(group)
    map_group["runtime_services"] = [slots["map_ui"]]
    s.config["service_releases"]["map"] = {"groups": {"ui": map_group}}
    pair_status = SimpleNamespace(images=s.pair_images, map_revision="0" * 40)
    monkeypatch.setattr(pinvi.candidate, "_baseline", lambda group:
        (s.pair_path, s.pair_path.read_bytes(), {"pin": "unchanged"}, pair_status))
    monkeypatch.setattr(pinvi.candidate, "_paired_companions", lambda status, document, client:
        [document["services"][name].update(image=image) for name, image in status.images.items()])
    monkeypatch.setattr(pinvi, "manager_mutation_lock", core.manager_mutation_lock)
    monkeypatch.setattr(pinvi, "get_env_path", core.get_env_path)
    monkeypatch.setattr(pinvi, "pinvi_core_candidate_for_target", lambda *a: ("core", s.lease))

    def probe(cid, script, fd):
        state.check_lease(fd, s.lease)
        return list(s.schema) if script == pinvi._SCHEMA_PROBE else list(s.workers)
    monkeypatch.setattr(core, "_probe", probe)
    s.before = copy.deepcopy(s.records)
    s.mutations = []
    s.fail_at = None

    def mutate(argv, *, additional_fds, **kwargs):
        assert s.locked and len(additional_fds) == 1
        state.check_lease(additional_fds[0], s.lease)
        assert "--no-build" in argv and "--no-deps" in argv and "never" in argv
        names = argv[argv.index("--wait-timeout") + 2:]
        assert set(names) <= pinvi.RUNTIME and pinvi.UI not in names
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
    monkeypatch.setattr(pinvi.frozen, "_run_mutation", mutate)
    original_render = pinvi.candidate._render
    def render(argv, **kwargs):
        import yaml
        result = original_render(argv, **kwargs)
        document = json.loads(result.stdout)
        for index, arg in enumerate(argv[:-1]):
            if arg == "-f" and argv[index + 1].endswith("/images.yml"):
                overlay = yaml.safe_load(__import__("pathlib").Path(argv[index + 1]).read_text())
                for name, values in overlay["services"].items():
                    document["services"][name].update(values)
        return subprocess.CompletedProcess(argv, result.returncode, json.dumps(document), result.stderr)
    monkeypatch.setattr(pinvi.candidate, "_render", render)
    return s


def apply_pinvi(s, **kwargs):
    s.receipt_path.write_text(json.dumps(s.runtime))
    s.build_path.write_text(json.dumps(s.normal))
    return pinvi.apply_pinvi_core_candidate(
        kwargs.pop("service", "pinvi"), source_clone=s.source, source_sha=s.sha, root=s.root,
        config=s.config, receipt_path=s.receipt_path,
        receipt_sha256=hashlib.sha256(s.receipt_path.read_bytes()).hexdigest(),
        build_receipt_path=s.build_path,
        build_receipt_sha256=hashlib.sha256(s.build_path.read_bytes()).hexdigest(),
        client_factory=s.factory, **kwargs)


def test_registry_pinvi_exact_existing_roles_and_lease():
    cfg = copy.deepcopy(load_targets_config())
    group, lease = pinvi_core_candidate_for_target("pinvi", cfg)
    assert group == "core" and str(lease).endswith("/pinvi-core-deploy.lock")
    cfg["service_releases"]["pinvi"]["groups"]["core"]["runtime_services"].append("extra")
    with pytest.raises(TargetsConfigError):
        pinvi_core_candidate_for_target("pinvi", cfg)


def test_pinvi_success_preserves_ui_other_environment_and_instigators(pinvi_case):
    s = pinvi_case
    original_states = copy.deepcopy(s.live_instigators)
    result = apply_pinvi(s, execute=True)
    assert result["success"] and result["schemaUnchanged"] and result["nonselectedUnchanged"]
    assert s.mutations == [[pinvi.ROLES["api"]], [pinvi.ROLES["code-server"]]]
    current = release.read_current_release("pinvi", root=s.root)
    for role, name in pinvi.ROLES.items():
        assert current["images"][name]["id"] == s.normal["images"][role]
        assert dict(v.split("=", 1) for v in s.records[name]["Config"]["Env"])["PINVI_SOURCE_REVISION"] == s.sha
        before_env = dict(v.split("=", 1) for v in s.before[name]["Config"]["Env"])
        after_env = dict(v.split("=", 1) for v in s.records[name]["Config"]["Env"])
        before_env.pop("PINVI_SOURCE_REVISION")
        after_env.pop("PINVI_SOURCE_REVISION")
        assert before_env == after_env
    for name in s.before.keys() - pinvi.RUNTIME:
        assert s.records[name] == s.before[name]
    assert s.live_instigators == original_states
    assert s.pair_path.read_bytes() == s.pair_before
    assert not (s.root / "current/map").exists()
    assert set(current["groups"]) == {"ui", "core"}
    assert current["images"][pinvi.UI] == s.current["images"][pinvi.UI]
    assert current["source_sha"] is None
    assert "fixture$secret" not in json.dumps(result)


def test_pinvi_dry_does_not_select_or_publish_new_images(pinvi_case):
    s = pinvi_case
    previous = release.read_current_release("pinvi", root=s.root)
    result = apply_pinvi(s, execute=False)
    assert result["success"] and result["result"] == "planned"
    assert s.records == s.before and not s.mutations
    assert release.read_current_release("pinvi", root=s.root) == previous


@pytest.mark.parametrize("field", ["wrong_source", "native_image", "native_driver", "native_impl",
                                  "extra_role", "schema", "copy_hash", "normal_mode"])
def test_pinvi_bad_tuple_fails_before_client_and_mutation(pinvi_case, field):
    s = pinvi_case
    if field == "wrong_source":
        s.runtime["release_sha"] = "0" * 40
    elif field == "native_image":
        s.runtime["native"]["images"]["api"] = "sha256:" + "1" * 64
    elif field == "native_driver":
        s.runtime["native"]["versions"]["api"]["asyncpg"] = "0.30.0"
    elif field == "native_impl":
        s.runtime["native"]["unusedPsycopg3Absent"] = False
    elif field == "extra_role":
        s.normal["images"]["ui"] = "sha256:" + "1" * 64
    elif field == "schema":
        s.runtime["schema_revisions"] = ["0000_old"]
    elif field == "copy_hash":
        s.normal["artifacts"]["api"]["COPY"]["sha256"] = "bad"
    else:
        s.normal["normalDockerBuild"] = False
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.steps and not s.mutations and s.records == s.before


@pytest.mark.parametrize("service", ["map", "weather", "transport", "conc"])
def test_other_targets_cannot_reach_docker(pinvi_case, service):
    s = pinvi_case
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, service=service)
    assert not s.steps and not s.mutations


def test_unmerged_pinvi_source_rejected(pinvi_case):
    s = pinvi_case
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", "HEAD~1")
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.steps and not s.mutations


def test_g_busy_retains_typed_code_before_client(pinvi_case, monkeypatch):
    s = pinvi_case
    error = ManagerMutationActiveError("busy")
    def busy(): raise error
    monkeypatch.setattr(pinvi, "manager_mutation_lock", busy)
    with pytest.raises(ManagerMutationActiveError) as caught:
        apply_pinvi(s, execute=True)
    assert caught.value is error and caught.value.code == "MANAGER_MUTATION_ACTIVE"
    assert not s.steps and not s.mutations


@pytest.mark.parametrize("kind", ["active", "running_instigator", "ambiguous", "worker"])
def test_pinvi_not_quiet_cannot_select_images_or_mutate(pinvi_case, monkeypatch, kind):
    s = pinvi_case
    if kind == "active":
        s.runs = [{"repositoryOrigin": {"repositoryLocationName": pinvi.LOCATION}, "tags": []}]
    elif kind == "running_instigator":
        s.live_instigators[0]["status"] = "RUNNING"
    elif kind == "worker":
        s.workers = [123]
    else:
        s.runs = [{"repositoryOrigin": None, "tags": []}]
    def forbidden(*a, **k): raise AssertionError("selection before quiet")
    monkeypatch.setattr(pinvi, "_images", forbidden)
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations


@pytest.mark.parametrize("failure_at", [1, 2])
def test_pinvi_failed_switch_rolls_back_captured_images_only(pinvi_case, failure_at):
    s = pinvi_case
    previous = release.read_current_release("pinvi", root=s.root)
    s.fail_at = failure_at
    original_states = copy.deepcopy(s.live_instigators)
    result = apply_pinvi(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert set(s.mutations[-1]) == ({pinvi.ROLES["api"]} if failure_at == 1 else pinvi.RUNTIME)
    for n in pinvi.RUNTIME:
        assert s.records[n]["Image"] == s.before[n]["Image"]
        assert s.records[n]["Config"]["Env"] == s.before[n]["Config"]["Env"]
    for n in s.before.keys() - pinvi.RUNTIME:
        assert s.records[n] == s.before[n]
    assert release.read_current_release("pinvi", root=s.root)["images"] == previous["images"]
    assert s.live_instigators == original_states


def test_pinvi_live_schema_mismatch_refuses_mutation(pinvi_case):
    s = pinvi_case
    s.schema = ["different"]
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations


def test_late_pinvi_run_never_recreates_unchanged_cs_during_api_rollback(pinvi_case):
    s = pinvi_case
    def started():
        s.runs = [{"repositoryOrigin": {"repositoryLocationName": pinvi.LOCATION}, "tags": []}]
    s.hooks["after_mutation"] = started
    result = apply_pinvi(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert s.mutations == [[pinvi.ROLES["api"]], [pinvi.ROLES["api"]]]
    assert s.records[pinvi.ROLES["code-server"]] == s.before[pinvi.ROLES["code-server"]]


def test_post_health_failure_restores_old_pair(pinvi_case):
    s = pinvi_case
    def unhealthy():
        if len(s.mutations) == 2:
            s.records[pinvi.ROLES["code-server"]]["State"]["Health"] = {"Status": "unhealthy"}
    s.hooks["after_mutation"] = unhealthy
    result = apply_pinvi(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert all(s.records[n]["Image"] == s.before[n]["Image"] for n in pinvi.RUNTIME)


def test_native_image_label_tamper_never_mutates(pinvi_case):
    s = pinvi_case
    s.metadata[s.normal["images"]["api"]]["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations


def test_receipt_checksum_tamper_rejected_before_client(pinvi_case):
    s = pinvi_case
    s.receipt_path.write_text(json.dumps(s.runtime))
    checksum = hashlib.sha256(s.receipt_path.read_bytes()).hexdigest()
    s.receipt_path.write_text(json.dumps({**s.runtime, "source_container": "0" * 64}))
    s.build_path.write_text(json.dumps(s.normal))
    with pytest.raises(ServiceReleaseError):
        pinvi.apply_pinvi_core_candidate(
            "pinvi", source_clone=s.source, source_sha=s.sha, root=s.root, config=s.config,
            receipt_path=s.receipt_path, receipt_sha256=checksum, build_receipt_path=s.build_path,
            build_receipt_sha256=hashlib.sha256(s.build_path.read_bytes()).hexdigest(),
            execute=True, client_factory=s.factory)
    assert not s.steps and not s.mutations


def test_cli_dispatches_explicit_pinvi_adapter(monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    entry.add_service_release_parser(parser.add_subparsers())
    args = parser.parse_args(["service-release", "apply-core-candidate", "pinvi",
        "--source-clone", "/approved/source", "--source-sha", "a" * 40,
        "--build-receipt", "/approved/normal.json", "--build-sha256", "b" * 64,
        "--receipt", "/approved/runtime.json", "--sha256", "c" * 64, "--dry-run"])
    monkeypatch.setattr(entry, "current_host", lambda config: "odroid")
    monkeypatch.setattr(entry, "target_hosts", lambda service, config: frozenset({"odroid"}))
    called = []
    monkeypatch.setattr(pinvi, "apply_pinvi_core_candidate",
                        lambda service, **kwargs: called.append(service) or {"success": True})
    assert entry._run(args) == 0
    assert called == ["pinvi"] and "true" in capsys.readouterr().out


def test_new_cs_worker_defers_cs_rollback_without_termination(pinvi_case):
    s = pinvi_case
    def started():
        if len(s.mutations) == 2:
            s.runs = [{"repositoryOrigin": {"repositoryLocationName": pinvi.LOCATION}, "tags": []}]
    s.hooks["after_mutation"] = started
    result = apply_pinvi(s, execute=True)
    assert not result["success"] and not result["rollbackImagesRestored"]
    assert result["rollbackDeferredForQuiet"] and result["rollbackErrorType"] == "ServiceReleaseError"
    assert s.mutations == [[pinvi.ROLES["api"]], [pinvi.ROLES["code-server"]], [pinvi.ROLES["api"]]]
    assert s.records[pinvi.ROLES["api"]]["Image"] == s.before[pinvi.ROLES["api"]]["Image"]
    assert s.records[pinvi.ROLES["code-server"]]["Image"] == s.normal["images"]["code-server"]
    assert s.runs


def test_cancel_after_partial_api_switch_restores_and_propagates(pinvi_case):
    s = pinvi_case
    def cancel():
        if len(s.mutations) == 1:
            raise KeyboardInterrupt
    s.hooks["after_mutation"] = cancel
    with pytest.raises(KeyboardInterrupt):
        apply_pinvi(s, execute=True)
    assert s.mutations == [[pinvi.ROLES["api"]], [pinvi.ROLES["api"]]]
    assert all(s.records[n]["Image"] == s.before[n]["Image"] for n in pinvi.RUNTIME)


def test_published_core_supports_subsequent_pinvi_and_map_ui_observation(pinvi_case):
    s = pinvi_case
    assert apply_pinvi(s, execute=True)["success"]
    from kor_travel_docker_manager.services import service_release_candidate as candidate
    profile = entry._profile("map", entry._declaration("map", s.config), [])
    with pinvi.manager_mutation_lock():
        release.adopt_release(profile, root=s.root, client=s.client)
        plan, manifests = candidate._candidate_plan("map", "ui", root=s.root, config=s.config)
        inventory, bind = candidate._paired_current_hooks(plan, manifests, s.client,
                                                         candidate._baseline(s.config["service_releases"]["map"]["groups"]["ui"]))
        inventory(candidate._inventory(s.client))
        rendered = copy.deepcopy(s.resolved)
        bind(rendered)
    for role, name in pinvi.ROLES.items():
        assert rendered["services"][name]["image"] == s.normal["images"][role]
    old_ui = release.read_current_release("pinvi", root=s.root)["images"][pinvi.UI]
    s.runtime["source_container"] = s.records[pinvi.ROLES["code-server"]]["Id"]
    assert apply_pinvi(s, execute=True)["success"]
    assert release.read_current_release("pinvi", root=s.root)["images"][pinvi.UI] == old_ui
    assert s.pair_path.read_bytes() == s.pair_before


@pytest.mark.parametrize("name", ["kor-travel-map-api", "kor-travel-map-dagster-code-server", "pinvi-web"])
def test_initial_map_or_ui_image_drift_refuses_all_mutation(pinvi_case, name):
    s = pinvi_case
    s.records[name]["Image"] = s.normal["images"]["api"]
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations
    assert s.pair_path.read_bytes() == s.pair_before


@pytest.mark.parametrize("name", ["kor-travel-map-api", "shared-daemon"])
def test_late_nonselected_change_never_recreates_it_or_publishes_core(pinvi_case, name):
    s = pinvi_case
    def change():
        s.records[name]["RestartCount"] += 1
    s.hooks["after_mutation"] = change
    result = apply_pinvi(s, execute=True)
    assert not result["success"]
    assert all(set(names) <= pinvi.RUNTIME for names in s.mutations)
    assert s.records[name]["Id"] == s.before[name]["Id"]
    assert release.read_current_release("pinvi", root=s.root) == s.current
    assert s.pair_path.read_bytes() == s.pair_before


@pytest.mark.parametrize("field", ["architecture", "common", "valid_copy_mismatch"])
def test_pinvi_specific_native_shape_mismatch_refuses_mutation(pinvi_case, field):
    s = pinvi_case
    if field == "architecture":
        s.normal["architecture"] = "arm64"
    elif field == "common":
        s.runtime["native"]["common"]["api"] = "f" * 40
    else:
        s.normal["artifacts"]["api"]["COPY"]["sha256"] = "f" * 64
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations


def test_project_lease_contention_precedes_client_and_mutation(pinvi_case):
    s = pinvi_case
    with state.project_lease(s.lease), pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations and not s.steps


def test_changed_pair_file_after_api_switch_is_never_rewritten(pinvi_case):
    s = pinvi_case
    replacement = b"changed-by-external-owner"
    def replace():
        s.pair_path.write_bytes(replacement)
    s.hooks["after_mutation"] = replace
    result = apply_pinvi(s, execute=True)
    assert not result["success"]
    assert s.pair_path.read_bytes() == replacement
    assert all(set(names) <= pinvi.RUNTIME for names in s.mutations)


def test_map_current_appearance_is_not_accepted_as_legacy_absence(pinvi_case):
    s = pinvi_case
    def appear():
        if len(s.mutations) == 1:
            (s.root / "current/map").symlink_to(s.root / "not-our-map")
    s.hooks["after_mutation"] = appear
    result = apply_pinvi(s, execute=True)
    assert not result["success"]
    assert (s.root / "current/map").is_symlink()
    assert all(set(names) <= pinvi.RUNTIME for names in s.mutations)


def test_failure_after_atomic_publication_restores_real_images_and_ui_only_current(pinvi_case, monkeypatch):
    s = pinvi_case
    publish = pinvi.candidate._publish
    calls = []
    def fail_after_first_commit(*args, **kwargs):
        value = publish(*args, **kwargs)
        calls.append(value)
        if len(calls) == 1:
            raise RuntimeError("controlled post-publication observer failure")
        return value
    monkeypatch.setattr(pinvi.candidate, "_publish", fail_after_first_commit)
    result = apply_pinvi(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert len(calls) == 2
    current = release.read_current_release("pinvi", root=s.root)
    assert current["images"] == s.current["images"]
    assert set(current["groups"]) == {"ui"}
    for name in pinvi.RUNTIME:
        assert s.records[name]["Image"] == s.before[name]["Image"]
    assert s.pair_path.read_bytes() == s.pair_before
    assert all(set(names) <= pinvi.RUNTIME for names in s.mutations)


@pytest.mark.parametrize("change", ["role", "coordinates", "oneshot", "lease"])
def test_pinvi_catalog_refuses_scope_widening_before_execution(change):
    cfg = copy.deepcopy(load_targets_config())
    core_group = cfg["service_releases"]["pinvi"]["groups"]["core"]
    if change == "role":
        core_group["runtime_services"].append("kor-travel-map-api")
    elif change == "coordinates":
        core_group["working_dir"] += "/other"
    elif change == "oneshot":
        core_group["oneshot_services"] = ["migrate"]
    else:
        cfg["targets"]["pinvi"]["core_candidate"]["project_lease"] += ".wrong"
    with pytest.raises(TargetsConfigError):
        pinvi_core_candidate_for_target("pinvi", cfg)


@pytest.mark.parametrize("change", ["health", "image"])
def test_publication_reobserves_selected_runtime_after_health_before_pointer(pinvi_case, monkeypatch, change):
    s = pinvi_case
    publish = pinvi.candidate._publish
    def change_at_publish(*args, **kwargs):
        if change == "health":
            s.records[pinvi.ROLES["code-server"]]["State"]["Health"] = {"Status": "unhealthy"}
        else:
            s.records[pinvi.ROLES["api"]]["Image"] = s.before[pinvi.ROLES["api"]]["Image"]
        return publish(*args, **kwargs)
    monkeypatch.setattr(pinvi.candidate, "_publish", change_at_publish)
    result = apply_pinvi(s, execute=True)
    assert not result["success"] and result["rollbackImagesRestored"]
    assert release.read_current_release("pinvi", root=s.root) == s.current
    assert not list(s.root.glob("pinvi-candidate-*"))
    assert s.pair_path.read_bytes() == s.pair_before


@pytest.mark.parametrize("execute", [False, True])
def test_missing_rendered_core_tags_use_verified_pair_ids(pinvi_case, monkeypatch, execute):
    s = pinvi_case
    unavailable = {name: f"fixture-unavailable/{name}:latest-main" for name in pinvi.RUNTIME}
    for name, tag in unavailable.items():
        s.resolved["services"][name]["image"] = tag
    lookups = []
    get = s.client.images.get

    def lookup(ref):
        lookups.append(ref)
        if ref in unavailable.values():
            raise docker.errors.ImageNotFound("fixture image tag absent")
        return get(ref)

    monkeypatch.setattr(s.client.images, "get", lookup)
    result = apply_pinvi(s, execute=execute)
    assert result["success"]
    assert not set(lookups).intersection(unavailable.values())
    assert s.pair_path.read_bytes() == s.pair_before
    for name in s.before.keys() - pinvi.RUNTIME:
        assert s.records[name] == s.before[name]
    current = release.read_current_release("pinvi", root=s.root)
    assert current["images"][pinvi.UI] == s.current["images"][pinvi.UI]
    if execute:
        assert s.mutations == [[pinvi.ROLES["api"]], [pinvi.ROLES["code-server"]]]
        for role, name in pinvi.ROLES.items():
            assert current["images"][name]["id"] == s.normal["images"][role]
    else:
        assert not s.mutations and s.records == s.before and current == s.current


def test_missing_core_tag_does_not_hide_pair_live_drift(pinvi_case):
    s = pinvi_case
    for name in pinvi.RUNTIME:
        s.resolved["services"][name]["image"] = f"fixture-unavailable/{name}:latest-main"
    s.records[pinvi.ROLES["api"]]["Image"] = s.records["kor-travel-map-api"]["Image"]
    with pytest.raises(ServiceReleaseError):
        apply_pinvi(s, execute=True)
    assert not s.mutations
    assert release.read_current_release("pinvi", root=s.root) == s.current
    assert s.pair_path.read_bytes() == s.pair_before
