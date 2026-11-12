"""Transport 전용 core 후보 CLI의 before/after 계약."""

import argparse

from kor_travel_docker_manager import service_release_cli as entry


def test_core_cli_requires_separate_build_and_runtime_receipts():
    parser = argparse.ArgumentParser()
    entry.add_service_release_parser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "service-release",
            "apply-core-candidate",
            "transport",
            "--source-clone",
            "/approved/source",
            "--source-sha",
            "a" * 40,
            "--build-receipt",
            "/approved/build.json",
            "--build-sha256",
            "b" * 64,
            "--receipt",
            "/approved/runtime.json",
            "--sha256",
            "c" * 64,
            "--dry-run",
        ]
    )
    assert args.release_action == "apply-core-candidate"
    assert args.dry_run and not args.confirm


import copy
import fcntl
import hashlib
import io
import json
import stat
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import docker
import pytest
import test_service_release_candidate as paired

from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    load_targets_config,
    transport_core_candidate_for_target,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

scenario = paired.scenario
URL = "https://github.com/digitie/kor-travel-transport.git"
BACKEND = "sha256:" + "e" * 64
NEW_FRONTEND = "sha256:" + "f" * 64


def archive_bytes(path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as t:
        t.add(path, arcname="state")
    return stream.getvalue()


@pytest.fixture
def core_case(scenario, monkeypatch):
    s = scenario
    cfg = load_targets_config()
    target = copy.deepcopy(cfg["targets"]["transport"])
    target["external_project"]["working_dir"] = str(s.source.parent)
    lease = s.source.parent / "project.lock"
    lease.write_text("")
    lease.chmod(0o600)
    target["core_candidate"]["project_lease"] = str(lease)
    s.config = {
        "targets": {"transport": target},
        "containers": cfg["containers"],
        "service_releases": {"transport": copy.deepcopy(cfg["service_releases"]["transport"])},
    }
    for group in s.config["service_releases"]["transport"]["groups"].values():
        group["env_file"] = str(s.source.parent / ".env.prod")
    (s.source.parent / ".env.prod").write_text("PASSWORD=fixture$secret\n")
    rootenv = s.source.parent / "manager.env"
    rootenv.write_text("ROOT_KEY=private-root-fixture\n")
    monkeypatch.setattr(core, "get_env_path", lambda: str(rootenv))
    for name in [
        "docker-compose.yml",
        "docker-compose.shared.yml",
        "docker-compose.transport-admin.yml",
    ]:
        (s.source.parent / name).write_text("services: {}\n")
    override = s.source.parent / "transport.override.yml"
    override.write_text("services: {}\n")
    s.config["service_releases"]["transport"]["groups"]["core"]["config_files"][-1] = str(override)
    for old, new in [
        (paired.UI, "frontend"),
        ("api", "backend"),
        ("dagster", "dagster-code-server"),
    ]:
        s.records[new] = s.records.pop(old)
    for name in ["transport-admin-web", "transport-api-gateway", "migrate"]:
        s.records[name] = copy.deepcopy(s.records["backend"])
    for i, (name, record) in enumerate(s.records.items(), 1):
        record["Id"] = f"{i:064x}"
        record["Config"]["Labels"] = {
            "com.docker.compose.project": "kor-travel-transport",
            "com.docker.compose.service": name,
        }
        record["Config"]["Env"] = [
            "PASSWORD=fixture$secret",
            "RELEASE_SHA=" + "0" * 40,
            "RUN_DB_MIGRATIONS=false",
            "COLLECTOR_MODE=preserve",
        ]
        record["Mounts"] = []
        record["HostConfig"] = {"Binds": ["ordered-first", "ordered-second"]}
        record["State"]["Status"] = "running"
    s.records["migrate"]["State"]["Running"] = False
    s.records["migrate"]["State"]["Status"] = "exited"
    paired.git(s.source, "remote", "set-url", "origin", URL)
    versions = s.source / "backend/alembic/versions"
    versions.mkdir(parents=True)
    (versions / "0042.py").write_text(
        "revision = '0042_reference_export_artifacts'\ndown_revision = None\n"
    )
    scripts = s.source / "scripts"
    scripts.mkdir()
    (scripts / "deploy-prod-remote.sh").write_text('release_lock_path="' + str(lease) + '"\n')
    paired.git(s.source, "add", "scripts")
    paired.git(s.source, "add", "backend")
    paired.git(s.source, "commit", "-qm", "approved core migration head only")
    s.sha = paired.git(s.source, "rev-parse", "HEAD")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", s.sha)
    s.tree = paired.git(s.source, "rev-parse", "HEAD^{tree}")
    for metadata in s.metadata.values():
        metadata["Config"]["Labels"]["org.opencontainers.image.source"] = URL
    s.metadata[BACKEND] = {
        "Id": BACKEND,
        "Architecture": "arm64",
        "Os": "linux",
        "Created": "2026-11-12T00:00:00Z",
        "Config": {"Env": ["RELEASE_SHA=" + s.sha], "Labels": {}},
    }
    s.metadata[NEW_FRONTEND] = copy.deepcopy(s.metadata[BACKEND])
    s.metadata[NEW_FRONTEND]["Id"] = NEW_FRONTEND
    s.metadata[NEW_FRONTEND]["Config"]["User"] = "node"
    s.old_state = s.source.parent / "old/dagster/state"
    (s.old_state / "artifacts").mkdir(parents=True)
    (s.old_state / "compute_logs").mkdir()
    (s.old_state / "artifacts/value.bin").write_bytes(b"actual-fixture-state\x00\xff")
    (s.old_state / "compute_logs/run.out").write_text("retained old compute output")
    (s.old_state / "artifacts/value.bin").chmod(0o640)
    s.new_state = s.source.parent / "new/dagster/state"
    s.fingerprint = state.scan_state(s.old_state)
    images = {"backend": BACKEND, "frontend": NEW_FRONTEND, "dagster-code-server": BACKEND}
    s.runtime = {
        "version": 1,
        "release_sha": s.sha,
        "images": images,
        "schema_revisions": ["0042_reference_export_artifacts"],
        "state": {
            "source_container": s.records["dagster-code-server"]["Id"],
            "fingerprint": s.fingerprint,
            "manifest": state.state_manifest(s.fingerprint),
        },
        "instigators": [
            {
                "location": core.LOCATION,
                "repository": "transport-repo",
                "kind": "schedule",
                "name": "collect",
                "status": "RUNNING",
            },
            {
                "location": "other-project",
                "repository": "other-repo",
                "kind": "sensor",
                "name": "keep",
                "status": "RUNNING",
            },
        ],
    }
    s.normal = {
        "version": 1,
        "kind": "transport-normal-build",
        "normalDockerBuild": True,
        "mode": "NORMAL_BUILD_ONLY",
        "actualApplyAllowed": False,
        "deployReady": False,
        "release_sha": s.sha,
        "tree": s.tree,
        "source": URL,
        "architecture": "arm64",
        "output": "docker",
        "images": {**images, "migrate": BACKEND},
        "artifacts": {},
    }
    for role, image_id in [("backend", BACKEND), ("frontend", NEW_FRONTEND)]:
        digest = hashlib.sha256(role.encode()).hexdigest()
        s.normal["artifacts"][role] = {
            "imageID": image_id,
            "loaded": True,
            "normalDockerBuild": True,
            "architecture": "arm64",
            "COPY": {"sha256": digest},
        }
        s.metadata[image_id]["Config"]["Labels"] = {
            "org.opencontainers.image.revision": s.sha,
            "org.opencontainers.image.source": URL,
            "io.kor-travel-transport.source-tree": s.tree,
            "io.kor-travel-transport.build-role": role,
            "io.kor-travel-transport.copy-sha256": digest,
        }
    # 실제 normal producer처럼 public 두 입력의 명시 hash와 runner 기본값을 둔다.
    public = {"NEXT_PUBLIC_API_BASE_URL": "https://old-api.invalid", "NEXT_PUBLIC_API_PORT": "8000"}
    hashes = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in public.items()}
    public_label = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    old_frontend = s.records["frontend"]["Image"]
    s.metadata[old_frontend]["Config"]["Env"] = [key + "=" + value for key, value in public.items()]
    s.metadata[NEW_FRONTEND]["Config"]["Env"].extend(
        key + "=" + value for key, value in public.items()
    )
    s.records["frontend"]["Config"]["Env"].extend(
        key + "=" + value for key, value in public.items()
    )
    s.normal["artifacts"]["frontend"]["publicInputHashes"] = hashes
    s.metadata[NEW_FRONTEND]["Config"]["Labels"]["io.kor-travel-transport.public-input-sha256"] = (
        public_label
    )
    profile = entry._profile(
        "transport", entry._declaration("transport", s.config), ["migrate=" + paired.OTHER]
    )
    (s.root / "current/map").unlink()
    s.current = release.adopt_release(profile, root=s.root, client=s.client)
    s.resolved = {
        "services": {
            name: {"image": r["Image"], "environment": {}, "volumes": []}
            for name, r in s.records.items()
        }
    }
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)

    @contextmanager
    def lock():
        s.locked.append(True)
        try:
            yield
        finally:
            s.locked.pop()

    monkeypatch.setattr(core, "manager_mutation_lock", lock)
    # Private 0700 /dev/shm fixture; production ancestor validation stays strict.
    monkeypatch.setattr(state, "_lease_ancestors", lambda p: release._directory(p.parent))
    monkeypatch.setattr(
        candidate,
        "current_pinned_runtime_release",
        lambda: SimpleNamespace(to_payload=lambda: {"pins": "unchanged"}),
    )
    monkeypatch.setattr(
        core.frozen,
        "_secrets",
        lambda manifests: [("PASSWORD", "fixture$secret"), ("ROOT_KEY", "private-root-fixture")],
    )
    s.steps, s.hooks = [], {}
    s.schema = list(s.runtime["schema_revisions"])
    s.live_instigators = copy.deepcopy(s.runtime["instigators"])
    s.live_instigators[0]["status"] = "STOPPED"
    s.runs, s.workers = [], []

    def graphql(query, records, config):
        if "repositoriesOrError" in query:
            repos = []
            for row in s.live_instigators:
                repos.append(
                    {
                        "name": row["repository"],
                        "location": {"name": row["location"]},
                        "schedules": [
                            {"name": row["name"], "scheduleState": {"status": row["status"]}}
                        ]
                        if row["kind"] == "schedule"
                        else [],
                        "sensors": [{"name": row["name"], "sensorState": {"status": row["status"]}}]
                        if row["kind"] == "sensor"
                        else [],
                    }
                )
            return {"repositoriesOrError": {"__typename": "RepositoryConnection", "nodes": repos}}
        return {"runsOrError": {"__typename": "Runs", "results": copy.deepcopy(s.runs)}}

    monkeypatch.setattr(core, "_graphql", graphql)

    def fingerprint(cid, lease_fd):
        if cid == s.runtime["state"]["source_container"]:
            return state.scan_state(s.old_state)
        return state.scan_state(s.new_state)

    monkeypatch.setattr(core, "_fingerprint", fingerprint)

    def probe(cid, script, lease_fd):
        state.check_lease(lease_fd, lease)
        return list(s.schema) if script == core._SCHEMA_PROBE else list(s.workers)

    monkeypatch.setattr(core, "_probe", probe)

    def get_archive(cid, path):
        source = s.old_state if cid == s.runtime["state"]["source_container"] else s.new_state
        if path == "/opt":
            return iter([]), {"mode": 0x800001ED, "linkTarget": ""}
        if path == "/opt/dagster":
            raise docker.errors.NotFound("fixture parent absent")
        assert path == state.STATE_PATH
        if not source.exists():
            raise docker.errors.NotFound("fixture state absent")
        return iter([archive_bytes(source)]), {"name": "state", "mode": 0x800001ED}

    def put_archive(cid, path, stream):
        assert path == "/opt" and cid == s.records["dagster-code-server"]["Id"]
        assert not s.records["dagster-code-server"]["State"]["Running"]
        s.new_state.parent.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=stream, mode="r:") as t:
            t.extractall(s.new_state.parent.parent, filter="data")
        return True

    s.client.api = SimpleNamespace(
        get_archive=get_archive,
        put_archive=put_archive,
        inspect_container=lambda cid: copy.deepcopy(
            next(r for r in s.records.values() if r["Id"] == cid)
        ),
    )

    def state_child(operation, cid, image_id, stage, fingerprint, lease_fd):
        assert s.locked
        state.check_lease(lease_fd, lease)
        s.steps.append(operation)
        if hook := s.hooks.get(operation):
            hook()
        snapshot = stage / "state.tar"
        if operation == "snapshot":
            state.save_archive(s.client, cid, snapshot)
            assert state.archive_semantic(snapshot) == state.semantic(fingerprint)
        else:
            state.restore_state(s.client, cid, image_id, snapshot, state.semantic(fingerprint))

    monkeypatch.setattr(core, "_state_child", state_child)
    monkeypatch.setattr(
        core.candidate,
        "_render",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(s.resolved), ""),
    )

    def mutation(argv, *, additional_fds, **kwargs):
        assert s.locked and len(additional_fds) == 1
        state.check_lease(additional_fds[0], lease)
        s.calls.append((argv, kwargs))
        phase = "create" if "--no-start" in argv else "start"
        s.steps.append(phase)
        frozen = json.loads(kwargs["input"])
        assert argv[-3:] == sorted(core.RUNTIME)
        assert "migrate" not in argv
        assert set(frozen["services"]) == core.RUNTIME
        assert all("depends_on" not in v for v in frozen["services"].values())
        if phase == "create":
            assert "--no-deps" in argv and "--no-build" in argv and "never" in argv
            for i, role in enumerate(sorted(core.RUNTIME), 100):
                record = s.records[role]
                record["Id"], record["Image"] = f"{i:064x}", frozen["services"][role]["image"]
                record["State"].update(Status="created", Running=False)
                record["Config"]["Env"] = [
                    k + "=" + v.replace("$$", "$")
                    for k, v in frozen["services"][role]["environment"].items()
                ]
        else:
            assert state.semantic(state.scan_state(s.new_state)) == state.semantic(s.fingerprint)
            for role in core.RUNTIME:
                s.records[role]["State"].update(Status="running", Running=True)
        if hook := s.hooks.get(phase):
            hook()
        return subprocess.CompletedProcess(
            argv, getattr(s, "exit_" + phase, 0), "", "fixture$secret failed"
        ), getattr(s, "signals_" + phase, [])

    monkeypatch.setattr(core.frozen, "_run_mutation", mutation)

    def factory():
        assert s.locked
        s.steps.append("factory")
        return s.client

    s.factory = factory
    s.receipt_path = s.source.parent / "runtime.json"
    s.build_path = s.source.parent / "normal.json"
    return s


def apply_core(s, *, execute=True, service="transport"):
    s.receipt_path.write_text(json.dumps(s.runtime))
    s.build_path.write_text(json.dumps(s.normal))
    return core.apply_transport_core_candidate(
        service,
        source_clone=s.source,
        source_sha=s.sha,
        root=s.root,
        config=s.config,
        receipt_path=s.receipt_path,
        receipt_sha256=hashlib.sha256(s.receipt_path.read_bytes()).hexdigest(),
        build_receipt_path=s.build_path,
        build_receipt_sha256=hashlib.sha256(s.build_path.read_bytes()).hexdigest(),
        execute=execute,
        client_factory=s.factory,
    )


@pytest.mark.parametrize("execute", [False, True])
def test_full_core_transaction_preserves_state_and_all_nonselected(core_case, execute):
    s = core_case
    original = copy.deepcopy(s.records)
    normal = copy.deepcopy(s.normal)
    result = apply_core(s, execute=execute)
    assert result["success"] is True, result
    assert s.normal == normal and not normal["actualApplyAllowed"] and not normal["deployReady"]
    assert "fixture$secret" not in json.dumps(result) and "private-root-fixture" not in json.dumps(
        result
    )
    current = release.read_current_release("transport", root=s.root)
    for role in set(original) - core.RUNTIME:
        assert s.records[role] == original[role]
    if not execute:
        assert current == s.current and s.steps == ["factory"] and not s.new_state.exists()
        assert not list(s.root.glob("transport-core-state-*"))
        return
    assert s.steps == ["factory", "snapshot", "create", "restore", "start"]
    assert state.semantic(state.scan_state(s.new_state)) == state.semantic(s.fingerprint)
    for role in core.RUNTIME | {"migrate"}:
        assert current["images"][role]["id"] == s.runtime["images"].get(role, BACKEND)
    assert current["groups"]["core"]["oneshot_images"]["migrate"] == BACKEND
    assert current["groups"]["admin"] == s.current["groups"]["admin"]
    assert (
        stat.S_IMODE((Path(result["private_state_directory"]) / "state.tar").stat().st_mode)
        == 0o600
    )
    assert s.client.close.call_count == 1
    assert len(s.calls) == 2


def test_plain_replacement_negative_control_loses_old_writable_state(core_case):
    s = core_case
    assert state.scan_state(s.old_state)["entries"]
    with pytest.raises(ValueError, match="state root absent"):
        state.scan_state(s.new_state)
    assert apply_core(s)["state_preserved"] is True


@pytest.mark.parametrize(
    "change",
    [
        "wrong_alias",
        "wrong_roles",
        "schema",
        "runtime_sha",
        "build_sha",
        "tree",
        "false_flags",
        "no_normal",
        "arch",
        "not_loaded",
        "image_oci",
        "default_sha",
        "source_url",
        "state_manifest",
        "state_timestamp",
    ],
)
def test_bad_tuple_admission_changes_nothing(core_case, change):
    s = core_case
    if change == "wrong_alias":
        s.runtime["images"]["dagster-code-server"] = NEW_FRONTEND
    elif change == "wrong_roles":
        s.runtime["images"]["admin"] = BACKEND
    elif change == "schema":
        s.runtime["schema_revisions"] = ["0036"]
    elif change == "runtime_sha":
        s.runtime["release_sha"] = "0" * 40
    elif change == "build_sha":
        s.normal["release_sha"] = "0" * 40
    elif change == "tree":
        s.normal["tree"] = "0" * 40
    elif change == "false_flags":
        s.normal["actualApplyAllowed"] = True
    elif change == "no_normal":
        s.normal["normalDockerBuild"] = False
    elif change == "arch":
        s.metadata[BACKEND]["Architecture"] = "amd64"
    elif change == "not_loaded":
        s.normal["artifacts"]["backend"]["loaded"] = False
    elif change == "image_oci":
        s.metadata[BACKEND]["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    elif change == "default_sha":
        s.metadata[BACKEND]["Config"]["Env"] = ["RELEASE_SHA=" + "0" * 40]
    elif change == "source_url":
        s.normal["source"] = "https://example.invalid/repo.git"
    elif change == "state_manifest":
        s.runtime["state"]["manifest"] = {}
    elif change == "state_timestamp":
        del s.runtime["state"]["fingerprint"]["root"]["ctime_ns"]
    before = copy.deepcopy(s.records)
    with pytest.raises((ServiceReleaseError, KeyError)):
        apply_core(s)
    assert s.records == before and not s.calls
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize(
    "change",
    [
        "source_cid",
        "live_schema",
        "active_run",
        "unknown_run",
        "worker",
        "schedule",
        "other_sensor",
        "migrations",
        "mount",
        "new_anonymous",
        "stale_state",
        "dirty_source",
    ],
)
def test_live_admission_refuses_without_snapshot_or_mutation(core_case, change):
    s = core_case
    if change == "source_cid":
        s.runtime["state"]["source_container"] = "a" * 64
    elif change == "live_schema":
        s.schema = ["0036"]
    elif change == "active_run":
        s.runs = [{"repositoryOrigin": {"repositoryLocationName": core.LOCATION}, "tags": []}]
    elif change == "unknown_run":
        s.runs = [{"tags": []}]
    elif change == "worker":
        s.workers = [1]
    elif change == "schedule":
        s.live_instigators[0]["status"] = "RUNNING"
    elif change == "other_sensor":
        s.live_instigators[1]["status"] = "STOPPED"
    elif change == "migrations":
        s.records["backend"]["Config"]["Env"][2] = "RUN_DB_MIGRATIONS=true"
    elif change == "mount":
        s.resolved["services"]["dagster-code-server"]["volumes"] = [
            {"type": "bind", "source": "/wrong", "target": state.STATE_PATH}
        ]
    elif change == "new_anonymous":
        s.metadata[BACKEND]["Config"]["Volumes"] = {state.STATE_PATH: {}}
    elif change == "stale_state":
        (s.old_state / "compute_logs/run.out").write_text("changed")
    elif change == "dirty_source":
        (s.source / "file").write_text("dirty")
    with pytest.raises(ServiceReleaseError):
        apply_core(s)
    assert "snapshot" not in s.steps and not s.calls
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize("phase", ["snapshot", "create", "restore", "start"])
def test_phase_failure_keeps_old_current_and_private_snapshot(core_case, phase):
    s = core_case
    if phase in {"snapshot", "restore"}:
        s.hooks[phase] = lambda: (_ for _ in ()).throw(ServiceReleaseError("fixture$secret"))
    else:
        setattr(s, "exit_" + phase, 17)
    result = apply_core(s)
    assert result["success"] is False
    assert "fixture$secret" not in json.dumps(result)
    assert release.read_current_release("transport", root=s.root) == s.current
    assert stat.S_IMODE(Path(result["private_state_directory"]).stat().st_mode) == 0o700
    if phase != "snapshot":
        assert (Path(result["private_state_directory"]) / "state.tar").exists()


@pytest.mark.parametrize(
    "change",
    [
        "foreign_restart",
        "foreign_env",
        "foreign_bind_order",
        "foreign_mount_dup",
        "health",
        "schema",
        "state",
        "env",
        "mount",
        "rootenv",
        "late_input",
    ],
)
def test_postconditions_refuse_current_publication(core_case, change):
    s = core_case

    def mutate():
        if change == "foreign_restart":
            s.records["db"]["RestartCount"] = 1
        elif change == "foreign_env":
            s.records["db"]["Config"]["Env"].append("NEW=value")
        elif change == "foreign_bind_order":
            s.records["db"]["HostConfig"]["Binds"].reverse()
        elif change == "foreign_mount_dup":
            s.records["db"]["Mounts"].append({"Destination": "/new"})
        elif change == "health":
            s.records["backend"]["State"]["Health"]["Status"] = "unhealthy"
        elif change == "schema":
            s.schema = ["9999"]
        elif change == "state":
            (s.new_state / "compute_logs/run.out").write_text("lost")
        elif change == "env":
            s.records["backend"]["Config"]["Env"].append("SECRET=new")
        elif change == "mount":
            s.records["backend"]["Mounts"].append({"Destination": "/new"})
        elif change == "rootenv":
            Path(core.get_env_path()).write_text("changed")
        elif change == "late_input":
            s.build_path.write_text("{}")

    s.hooks["start"] = mutate
    result = apply_core(s)
    assert result["success"] is False, result
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize("phase", ["create", "start"])
def test_interruption_no_automatic_retry_or_publish(core_case, phase):
    s = core_case
    setattr(s, "signals_" + phase, ["SIGTERM"])
    result = apply_core(s)
    assert not result["success"] and result["interrupted_signals"] == ["SIGTERM"]
    assert len(s.calls) == (1 if phase == "create" else 2)
    assert release.read_current_release("transport", root=s.root) == s.current


def test_global_lock_contention_precedes_client(core_case, monkeypatch):
    s = core_case

    @contextmanager
    def busy():
        raise ServiceReleaseError("busy G")
        yield

    monkeypatch.setattr(core, "manager_mutation_lock", busy)
    with pytest.raises(ServiceReleaseError, match="busy G"):
        apply_core(s)
    assert s.steps == []


def test_same_project_lease_contention_precedes_client(core_case):
    s = core_case
    p = Path(s.config["targets"]["transport"]["core_candidate"]["project_lease"])
    with p.open("r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ServiceReleaseError):
            apply_core(s)
    assert s.steps == []


def test_unrelated_services_rejected_before_client(core_case):
    for name in ["map", "pinvi", "geo", "weather", "transport-admin"]:
        with pytest.raises(ServiceReleaseError):
            apply_core(core_case, service=name)
    assert core_case.steps == []


def test_explicit_catalog_registration_only(core_case):
    s = core_case
    group, path = transport_core_candidate_for_target("transport", s.config)
    assert group == "core" and path.name == "project.lock"
    for field, value in [
        ("group", "admin"),
        ("project_lease", "../bad"),
        ("release_env", "PASSWORD"),
    ]:
        cfg = copy.deepcopy(s.config)
        cfg["targets"]["transport"]["core_candidate"][field] = value
        with pytest.raises(TargetsConfigError):
            transport_core_candidate_for_target("transport", cfg)


def test_source_project_lease_mismatch_is_rejected_before_client(core_case):
    s = core_case
    other = s.source.parent / "wrong-project.lock"
    other.write_text("")
    other.chmod(0o600)
    s.config["targets"]["transport"]["core_candidate"]["project_lease"] = str(other)
    with pytest.raises(ServiceReleaseError, match="project lease"):
        apply_core(s)
    assert not s.steps


def test_identical_bytes_rewrite_after_snapshot_refuses_create(core_case):
    s = core_case
    path = s.old_state / "artifacts/value.bin"
    before = path.read_bytes()
    s.hooks["snapshot"] = lambda: path.write_bytes(before)
    result = apply_core(s)
    assert not result["success"] and not s.calls
    assert s.steps == ["factory", "snapshot"]
    assert (Path(result["private_state_directory"]) / "state.tar").exists()
    assert release.read_current_release("transport", root=s.root) == s.current


def test_existing_named_mount_and_config_bind_are_preserved(core_case):
    s = core_case
    config = s.source.parent / "dagster.yaml"
    config.write_text("local_artifact_storage: preserved")
    s.resolved["volumes"] = {"checkpoints": {"name": "existing-checkpoint-volume"}}
    for role in ["backend", "dagster-code-server"]:
        s.records[role]["Mounts"] = [
            {
                "Type": "volume",
                "Name": "existing-checkpoint-volume",
                "Source": "/existing/volume",
                "Destination": "/app/collector-state",
                "RW": True,
                "Propagation": "",
                "Driver": "local",
            },
            {
                "Type": "bind",
                "Source": str(config),
                "Destination": "/app/dagster_home/dagster.yaml",
                "RW": False,
                "Propagation": "rprivate",
                "Mode": "ro",
            },
        ]
        s.resolved["services"][role]["volumes"] = [
            {"type": "volume", "source": "checkpoints", "target": "/app/collector-state"},
            {
                "type": "bind",
                "source": str(config),
                "target": "/app/dagster_home/dagster.yaml",
                "read_only": True,
            },
        ]
    s.hooks["start"] = lambda: s.records["backend"]["Mounts"].reverse()
    assert apply_core(s)["success"] is True


def test_nonselected_mount_order_only_is_equivalent(core_case):
    s = core_case
    s.records["db"]["Mounts"] = [
        {"Destination": "/a", "RW": False},
        {"Destination": "/b", "RW": True},
    ]
    s.hooks["start"] = lambda: s.records["db"]["Mounts"].reverse()
    assert apply_core(s)["success"] is True


def test_late_publication_guard_keeps_original_current(core_case, monkeypatch):
    s = core_case
    original = candidate._publish

    def publish(*args):
        s.records["db"]["RestartCount"] += 1
        return original(*args)

    monkeypatch.setattr(candidate, "_publish", publish)
    result = apply_core(s)
    assert not result["success"]
    assert release.read_current_release("transport", root=s.root) == s.current


def test_missing_lease_is_not_created(core_case):
    s = core_case
    path = Path(s.config["targets"]["transport"]["core_candidate"]["project_lease"])
    path.unlink()
    with pytest.raises(ServiceReleaseError):
        apply_core(s)
    assert not path.exists() and not s.steps


def test_malformed_receipt_is_redacted_at_public_sdk_boundary(core_case, monkeypatch):
    s = core_case

    def malformed(*args):
        raise ValueError("fixture$secret")

    monkeypatch.setattr(core, "_receipt", malformed)
    with pytest.raises(ServiceReleaseError) as result:
        apply_core(s)
    assert "fixture$secret" not in str(result.value)
    assert not s.steps


def test_cli_dispatches_both_approved_receipts_without_other_selector(
    core_case, monkeypatch, capsys
):
    s = core_case
    monkeypatch.setattr(entry, "load_targets_config", lambda: s.config)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "result": "planned"}

    monkeypatch.setattr(core, "apply_transport_core_candidate", run)
    parser = argparse.ArgumentParser()
    entry.add_service_release_parser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "service-release",
            "apply-core-candidate",
            "transport",
            "--source-clone",
            str(s.source),
            "--source-sha",
            s.sha,
            "--build-receipt",
            str(s.build_path),
            "--build-sha256",
            "b" * 64,
            "--receipt",
            str(s.receipt_path),
            "--sha256",
            "c" * 64,
            "--dry-run",
        ]
    )
    assert entry._run(args) == 0
    assert calls[0][0] == ("transport",)
    assert calls[0][1]["build_receipt_sha256"] == "b" * 64
    assert calls[0][1]["receipt_sha256"] == "c" * 64
    assert calls[0][1]["execute"] is False
    assert "fixture$secret" not in capsys.readouterr().out


def test_pax_long_names_cannot_override_fixed_restore_prefix(core_case):
    s = core_case
    directory = s.old_state / ("a" * 90) / ("b" * 90)
    directory.mkdir(parents=True)
    (directory / "retained").write_bytes(b"PAX long-path content")
    s.fingerprint = state.scan_state(s.old_state)
    s.runtime["state"].update(
        fingerprint=s.fingerprint, manifest=state.state_manifest(s.fingerprint)
    )
    put = s.client.api.put_archive

    def checked(cid, path, stream):
        content = stream.read()
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            assert all(m.name == "dagster" or m.name.startswith("dagster/state") for m in archive)
        return put(cid, path, io.BytesIO(content))

    s.client.api.put_archive = checked
    assert apply_core(s)["success"] is True
    assert (
        s.new_state / directory.relative_to(s.old_state) / "retained"
    ).read_bytes() == b"PAX long-path content"
