"""Weather 전체 스택 경계: 기존 fake Engine만 사용하고 실제 Docker는 호출하지 않는다."""

import argparse
import copy
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_transport_core_candidate import core_case as core_case
from test_transport_core_candidate import paired
from test_transport_core_candidate import scenario as scenario

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import service_release_weather_candidate as weather
from kor_travel_docker_manager.services import standalone_backup as backup
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    load_targets_config,
    weather_core_candidate_for_target,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


@pytest.fixture
def weather_case(core_case, monkeypatch):
    s = core_case
    cfg = load_targets_config()
    s.lease = Path(s.config["targets"]["transport"]["core_candidate"]["project_lease"])
    s.config = {
        "targets": {"weather": copy.deepcopy(cfg["targets"]["weather"])},
        "containers": cfg["containers"],
        "service_releases": {"weather": copy.deepcopy(cfg["service_releases"]["weather"])},
    }
    group = s.config["service_releases"]["weather"]["groups"]["main"]
    s.project = weather._project(s.config)
    group.update(working_dir=str(s.source.parent), config_files=[str(s.source.parent / "compose.yml")])
    (s.source.parent / "compose.yml").write_text("services: {}\n")
    for i, (role, name) in enumerate(weather.ROLES.items(), 1000):
        record = copy.deepcopy(s.records["frontend" if role == "frontend" else "backend"])
        record["Id"] = f"{i:064x}"
        record["Config"]["Labels"] = {
            "com.docker.compose.project": s.project,
            "com.docker.compose.service": name,
        }
        record["Config"]["Env"] = [
            "PASSWORD=fixture$secret",
            "KOR_TRAVEL_WEATHER_GIT_COMMIT=" + "0" * 40,
            "KOR_TRAVEL_WEATHER_DATABASE_URL=postgresql+psycopg://weather@127.0.0.1:11000/kor_travel_weather",
            "PGPASSWORD=fixture$secret",
        ]
        s.records[name] = record
    paired.git(s.source, "remote", "set-url", "origin", weather.URL)
    versions = s.source / "alembic/versions"
    versions.mkdir(parents=True)
    (versions / "0039.py").write_text("revision='0039_page_liveness'\ndown_revision=None\n")
    (versions / "0040.py").write_text("revision='0040_region_locations'\ndown_revision='0039_page_liveness'\n")
    paired.git(s.source, "add", "alembic")
    paired.git(s.source, "commit", "-qm", "approved weather0040 fixture")
    s.sha = paired.git(s.source, "rev-parse", "HEAD")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", s.sha)
    s.tree = paired.git(s.source, "rev-parse", "HEAD^{tree}")
    images = {role: "sha256:" + str(i) * 64 for i, role in enumerate(weather.ROLES, 1)}
    hashes = {k: hashlib.sha256(("approved-" + k).encode()).hexdigest() for k in weather.PUBLIC}
    s.normal = {
        "version": 1, "kind": "weather-normal-build", "normalDockerBuild": True,
        "mode": "NORMAL_BUILD_ONLY", "actualApplyAllowed": False, "deployReady": False,
        "source": weather.URL, "release_sha": s.sha, "tree": s.tree, "output": "docker",
        "architecture": "arm64", "images": images, "artifacts": {},
    }
    for role, image in images.items():
        digest = hashlib.sha256(role.encode()).hexdigest()
        artifact = {
            "imageID": image, "loaded": True, "normalDockerBuild": True,
            "architecture": "arm64", "COPY": {"sha256": digest},
        }
        labels = {
            "org.opencontainers.image.revision": s.sha,
            "org.opencontainers.image.source": weather.URL,
            "io.kor-travel-weather.source-tree": s.tree,
            "io.kor-travel-weather.build-role": role,
            "io.kor-travel-weather.copy-sha256": digest,
        }
        if role == "frontend":
            artifact["publicInputHashes"] = hashes.copy()
            labels.update({"io.kor-travel-weather.public-input-" + k.lower(): v for k, v in hashes.items()})
        s.normal["artifacts"][role] = artifact
        s.metadata[image] = {
            "Id": image, "Architecture": "arm64", "Os": "linux", "Created": "2026-11-13T00:00:00Z",
            "Config": {"Labels": labels, "User": "node" if role == "frontend" else "",
                       "Env": [] if role == "frontend" else ["KOR_TRAVEL_WEATHER_GIT_COMMIT=" + s.sha]},
        }
    s.runtime = {
        "version": 1, "release_sha": s.sha, "images": images.copy(),
        "schema_before": ["0039_page_liveness"], "schema_target": [weather.HEAD],
        "state": {"source_container": s.records[weather.ROLES["code-server"]]["Id"],
                  "fingerprint": s.fingerprint, "manifest": state.state_manifest(s.fingerprint)},
        "public_baseline": {"image_id": s.records[weather.ROLES["frontend"]]["Image"], "hashes": hashes.copy()},
        "instigators": [
            {"location": weather.LOCATION, "repository": "weather-repo", "kind": "schedule", "name": "collect", "status": "RUNNING"},
            {"location": "unselected", "repository": "other-repo", "kind": "sensor", "name": "keep", "status": "RUNNING"},
        ],
    }
    public_bytes = b""
    ranges = {}
    for key in sorted(weather.PUBLIC):
        value = ("approved-" + key).encode()
        ranges[key] = {"path": "/app/.next/static/chunks/fixture-public.js", "offset": len(public_bytes), "length": len(value)}
        public_bytes += value + b";"
    for value in ranges.values():
        value["file_sha256"] = hashlib.sha256(public_bytes).hexdigest()
    s.runtime["public_baseline"]["byte_ranges"] = ranges
    old_get_archive = s.client.api.get_archive

    def public_archive(cid, path):
        if path == "/app/.next/static/chunks/fixture-public.js":
            assert cid == s.records[weather.ROLES["frontend"]]["Id"]
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as archive:
                info = tarfile.TarInfo("fixture-public.js")
                info.size = len(public_bytes)
                archive.addfile(info, io.BytesIO(public_bytes))
            return iter([data.getvalue()]), {}
        return old_get_archive(cid, path)

    s.client.api.get_archive = public_archive
    s.live_instigators = copy.deepcopy(s.runtime["instigators"])
    s.live_instigators[0]["status"] = "STOPPED"
    s.schema = s.runtime["schema_before"].copy()
    s.resolved = {"services": {name: {"image": v["Image"], "environment": {}, "volumes": []} for name, v in s.records.items()}}
    migration_env = {
        k: v.replace("$", "$$") for k, v in (x.split("=", 1) for x in s.records[weather.ROLES["api"]]["Config"]["Env"])
        if k in {"KOR_TRAVEL_WEATHER_DATABASE_URL", "PGPASSWORD"}
    }
    s.resolved["services"][weather.MIGRATE] = {
        "image": s.records[weather.ROLES["api"]]["Image"], "command": ["alembic", "upgrade", "head"],
        "restart": "no", "environment": migration_env, "depends_on": {"db": {"condition": "service_healthy"}}, "volumes": [],
    }
    profile = entry._profile("weather", entry._declaration("weather", s.config),
                             [weather.MIGRATE + "=" + s.records[weather.ROLES["api"]]["Image"]])
    s.current = release.adopt_release(profile, root=s.root, client=s.client)
    monkeypatch.setattr(weather, "get_env_path", core.get_env_path)
    monkeypatch.setattr(weather, "manager_mutation_lock", core.manager_mutation_lock)
    monkeypatch.setattr(weather, "weather_core_candidate_for_target", lambda service, config: ("main", s.lease))
    monkeypatch.setattr(backup, "require_local_backup_role", lambda role: None)
    monkeypatch.setattr(backup, "_discover_port", lambda name: 11000)
    monkeypatch.setattr(weather, "_schema", lambda fd: list(s.schema))
    return _engine(s, monkeypatch)


def _engine(s, monkeypatch):
    def put_archive(cid, path, stream):
        assert path == "/opt" and cid == s.records[weather.ROLES["code-server"]]["Id"]
        assert not s.records[weather.ROLES["code-server"]]["State"]["Running"]
        s.new_state.parent.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            archive.extractall(s.new_state.parent.parent, filter="data")
        return True

    s.client.api.put_archive = put_archive

    def child(operation, *, stage, lease_fd, **payload):
        assert s.locked
        state.check_lease(lease_fd, s.lease)
        s.steps.append(operation)
        if hook := s.hooks.get(operation):
            hook()
        if operation == "backup":
            return {"success": True, "manifest": {
                "role": "weather", "alembic_head": s.runtime["schema_before"][0],
                "byte_size": 200, "toc_entry_count": 4, "sha256": "e" * 64,
            }}
        if operation == "snapshot":
            state.save_archive(s.client, payload["cid"], stage / "state.tar")
            assert state.archive_semantic(stage / "state.tar") == state.semantic(payload["fingerprint"])
        else:
            state.restore_state(s.client, payload["cid"], payload["image_id"], stage / "state.tar",
                                state.semantic(payload["fingerprint"]),
                                project=s.project, service=weather.ROLES["code-server"])
        return {"success": True}

    monkeypatch.setattr(weather, "_child", child)

    def mutation(argv, *, additional_fds, **kwargs):
        assert s.locked and len(additional_fds) == 1
        state.check_lease(additional_fds[0], s.lease)
        doc = json.loads(kwargs["input"])
        phase = "migrate" if weather.MIGRATE in argv else "stop" if "stop" in argv else "create" if "--no-start" in argv else "start"
        s.steps.append(phase)
        s.calls.append((argv, kwargs))
        assert set(doc["services"]) == ({weather.MIGRATE} if phase == "migrate" else weather.RUNTIME)
        assert all("depends_on" not in d and "build" not in d for d in doc["services"].values())
        if phase == "stop":
            for name in weather.RUNTIME:
                s.records[name]["State"].update(Status="exited", Running=False)
        elif phase == "migrate":
            assert "--no-deps" in argv and "--no-build" in argv and "never" in argv
            assert doc["services"][weather.MIGRATE]["command"] == ["alembic", "upgrade", "head"]
            migrated_env = doc["services"][weather.MIGRATE]["environment"]
            old_env = dict(v.split("=", 1) for v in s.records[weather.ROLES["api"]]["Config"]["Env"])
            assert migrated_env["PGPASSWORD"].replace("$$", "$") == old_env["PGPASSWORD"]
            record = copy.deepcopy(s.records[weather.ROLES["api"]])
            record.update(Id="b" * 64, Image=s.runtime["images"]["api"])
            record["Config"]["Labels"]["com.docker.compose.service"] = weather.MIGRATE
            record["Config"]["Cmd"] = list(doc["services"][weather.MIGRATE]["command"])
            record["State"].update(Status="exited", Running=False, ExitCode=0)
            s.records[weather.MIGRATE] = record
            s.schema = list(s.runtime["schema_target"])
        elif phase == "create":
            for i, (_role, name) in enumerate(weather.ROLES.items(), 2000):
                record = s.records[name]
                record.update(Id=f"{i:064x}", Image=doc["services"][name]["image"])
                record["State"].update(Status="created", Running=False)
                record["Config"]["Env"] = [k + "=" + value.replace("$$", "$")
                                           for k, value in doc["services"][name]["environment"].items()]
        else:
            assert state.semantic(state.scan_state(s.new_state)) == state.semantic(s.fingerprint)
            for name in weather.RUNTIME:
                s.records[name]["State"].update(Status="running", Running=True)
        if hook := s.hooks.get(phase):
            hook()
        return subprocess.CompletedProcess(argv, getattr(s, "exit_" + phase, 0), "", "fixture$secret failure"), getattr(s, "signals_" + phase, [])

    monkeypatch.setattr(core.frozen, "_run_mutation", mutation)
    return s


def apply_weather(s, execute=True):
    s.receipt_path.write_text(json.dumps(s.runtime))
    s.build_path.write_text(json.dumps(s.normal))
    return weather.apply_weather_core_candidate(
        "weather", source_clone=s.source, source_sha=s.sha, root=s.root, config=s.config,
        receipt_path=s.receipt_path, receipt_sha256=hashlib.sha256(s.receipt_path.read_bytes()).hexdigest(),
        build_receipt_path=s.build_path, build_receipt_sha256=hashlib.sha256(s.build_path.read_bytes()).hexdigest(),
        execute=execute, client_factory=s.factory,
    )


@pytest.mark.parametrize("execute", [False, True])
def test_weather_transaction_migrates_only_approved_role_and_preserves_nonselected(weather_case, execute):
    s = weather_case
    before = copy.deepcopy(s.records)
    old_other_current = (s.root / "current/transport").readlink()
    result = apply_weather(s, execute)
    assert result["success"], result
    assert "fixture$secret" not in json.dumps(result)
    assert (s.root / "current/transport").readlink() == old_other_current
    for name in set(before) - weather.RUNTIME:
        assert s.records[name] == before[name]
    if not execute:
        assert s.steps == ["factory"] and not s.calls
        assert release.read_current_release("weather", root=s.root) == s.current
    else:
        assert s.steps == ["factory", "stop", "snapshot", "backup", "migrate", "create", "restore", "start"]
        current = release.read_current_release("weather", root=s.root)
        for role, name in weather.ROLES.items():
            assert current["images"][name]["id"] == s.runtime["images"][role]
        assert current["groups"]["main"]["oneshot_images"][weather.MIGRATE] == s.runtime["images"]["api"]
        assert state.semantic(state.scan_state(s.new_state)) == state.semantic(s.fingerprint)
        assert result["scheduler_resume_required"] is True


@pytest.mark.parametrize("kind", [
    "source", "tree", "architecture", "flag", "api-id", "cs-id", "public", "schema-before", "target",
    "state-cid", "worker", "active-run", "instigator", "migration-command", "db", "password",
    "normal-COPY", "old-public-image",
])
def test_weather_bad_admission_has_no_backup_stop_ddl_or_publish(weather_case, kind):
    s = weather_case
    if kind == "source":
        s.normal["source"] = "https://example.invalid/other.git"
    elif kind == "tree":
        s.normal["tree"] = "0" * 40
    elif kind == "architecture":
        s.normal["architecture"] = "amd64"
    elif kind == "flag":
        s.normal["deployReady"] = True
    elif kind == "api-id":
        s.normal["images"]["api"] = "sha256:" + "8" * 64
    elif kind == "cs-id":
        s.runtime["images"]["code-server"] = "sha256:" + "9" * 64
    elif kind == "public":
        s.normal["artifacts"]["frontend"]["publicInputHashes"] = {k: "9" * 64 for k in weather.PUBLIC}
    elif kind == "schema-before":
        s.schema = [weather.HEAD]
    elif kind == "target":
        s.runtime["schema_target"] = ["0039_page_liveness"]
    elif kind == "state-cid":
        s.runtime["state"]["source_container"] = "0" * 64
    elif kind == "worker":
        s.workers = [42]
    elif kind == "active-run":
        s.runs = [{"runId": "own", "repositoryOrigin": {"repositoryLocationName": weather.LOCATION}, "tags": []}]
    elif kind == "instigator":
        s.live_instigators[1]["status"] = "STOPPED"
    elif kind == "migration-command":
        s.resolved["services"][weather.MIGRATE]["command"] = ["alembic", "stamp", "head"]
    elif kind == "db":
        s.resolved["services"][weather.MIGRATE]["environment"]["KOR_TRAVEL_WEATHER_DATABASE_URL"] = "postgresql://other"
    elif kind == "password":
        s.resolved["services"][weather.MIGRATE]["environment"]["PGPASSWORD"] = "other"
    elif kind == "normal-COPY":
        s.normal["artifacts"]["api"]["COPY"]["sha256"] = "wrong"
    elif kind == "old-public-image":
        s.runtime["public_baseline"]["image_id"] = s.runtime["images"]["frontend"]
    with pytest.raises(ServiceReleaseError):
        apply_weather(s)
    assert not s.calls and not s.new_state.exists()
    assert release.read_current_release("weather", root=s.root) == s.current


@pytest.mark.parametrize("phase", ["snapshot", "backup", "migrate", "create", "restore", "start"])
def test_weather_failure_retains_snapshot_and_never_publishes_or_retries(weather_case, phase):
    s = weather_case

    def fail():
        raise ServiceReleaseError("fixture$secret private failure")

    s.hooks[phase] = fail
    result = apply_weather(s)
    assert result["success"] is False and "fixture$secret" not in json.dumps(result)
    assert s.steps.count(phase) == 1
    assert release.read_current_release("weather", root=s.root) == s.current
    assert list(s.root.glob("weather-core-state-*"))


def test_weather_catalog_is_narrow_and_transport_remains_rejected():
    cfg = load_targets_config()
    assert weather_core_candidate_for_target("weather", cfg)[0] == "main"
    for service in ["transport", "map", "pinvi", "geo", "conc"]:
        with pytest.raises(TargetsConfigError):
            weather_core_candidate_for_target(service, cfg)
    assert backup._role_config("weather")[1] == "kor_travel_weather"


@pytest.mark.parametrize("change", ["hash", "offset", "path", "missing", "length", "image"])
def test_weather_approved_compiled_baseline_is_not_runtime_env_or_self_asserted_hash(weather_case, change):
    s = weather_case
    baseline = s.runtime["public_baseline"]
    key = sorted(weather.PUBLIC)[0]
    proof = baseline["byte_ranges"][key]
    if change == "hash":
        # Both supplied receipts and candidate label can agree on a wrong public input;
        # the old compiled bytes must still reject it before any mutation.
        baseline["hashes"][key] = "9" * 64
        s.normal["artifacts"]["frontend"]["publicInputHashes"][key] = "9" * 64
        s.metadata[s.runtime["images"]["frontend"]]["Config"]["Labels"]["io.kor-travel-weather.public-input-" + key.lower()] = "9" * 64
    elif change == "offset":
        proof["offset"] += 1
    elif change == "path":
        proof["path"] = "/app/.env"
    elif change == "missing":
        del baseline["byte_ranges"][key]
    elif change == "length":
        proof["length"] = True
    else:
        baseline["image_id"] = s.runtime["images"]["frontend"]
    with pytest.raises(ServiceReleaseError):
        apply_weather(s)
    assert not s.calls


def test_weather_already_exact_head_never_executes_migration(weather_case):
    s = weather_case
    s.runtime["schema_before"] = [weather.HEAD]
    s.schema = [weather.HEAD]
    result = apply_weather(s)
    assert result["success"], result
    assert "migrate" not in s.steps
    assert "backup" not in s.steps
    assert result["backup_skipped"] is True
    assert result["migration_required"] is False
    assert all(weather.MIGRATE not in argv for argv, _ in s.calls)


@pytest.fixture
def unpaired_pin_case(tmp_path, monkeypatch):
    private, public = tmp_path / "private.json", tmp_path / "public.json"
    monkeypatch.setattr(core.pin_registry, "runtime_pin_registry_path", lambda: private)
    monkeypatch.setattr(core.pin_registry, "runtime_pin_registry_public_path", lambda: public)
    monkeypatch.setattr(core, "current_host", lambda config: "odroid")
    monkeypatch.setattr(core, "target_hosts", lambda target, config: frozenset({"n150"}))
    for key in (core.pin_registry.RUNTIME_PINS_FILE_ENV, core.pin_registry.RUNTIME_PINS_PUBLIC_FILE_ENV):
        monkeypatch.delenv(key, raising=False)

    def missing():
        raise core.pin_registry.RuntimePinRegistryError("missing") from FileNotFoundError()

    monkeypatch.setattr(core.candidate, "current_pinned_runtime_release", missing)
    config = {"targets": {"map": {}, "pinvi": {}}, "containers": {
        "map-api": {"role": "map-api", "name": "map-api-live", "compose_service": "map-api"},
        "pinvi-api": {"role": "pinvi-api", "name": "pinvi-api-live", "compose_service": "pinvi-api"},
    }}
    return config, private, public


def test_unpaired_host_preserves_original_both_pin_files_absent(unpaired_pin_case):
    config, private, public = unpaired_pin_case
    before = core._paired_pin_baseline(config, [])
    assert before == core._paired_pin_baseline(config, [])
    assert before["unpaired_host"] == "odroid"
    assert not private.exists() and not public.exists()


@pytest.mark.parametrize("kind", ["map-api", "pinvi-api"])
def test_missing_pins_refused_for_even_stopped_paired_runtime(unpaired_pin_case, kind):
    config, _, _ = unpaired_pin_case
    records = [{"Name": "/unrelated-name", "State": {"Running": False},
                "Config": {"Labels": {"com.docker.compose.service": kind}}}]
    with pytest.raises(ServiceReleaseError):
        core._paired_pin_baseline(config, records)


def test_paired_owner_host_still_refuses_missing_registry(unpaired_pin_case, monkeypatch):
    config, _, _ = unpaired_pin_case
    monkeypatch.setattr(core, "current_host", lambda config: "n150")
    with pytest.raises(core.pin_registry.RuntimePinRegistryError):
        core._paired_pin_baseline(config, [])


@pytest.mark.parametrize("kind", ["malformed-private", "public-only", "dangling-private", "dangling-public", "explicit-missing"])
def test_present_or_configured_pin_failure_never_becomes_absence(unpaired_pin_case, monkeypatch, kind):
    config, private, public = unpaired_pin_case
    if kind == "malformed-private":
        private.write_text("not-json")
    elif kind == "public-only":
        public.write_text("{}")
    elif kind == "dangling-private":
        private.symlink_to(private.with_name("missing-target"))
    elif kind == "dangling-public":
        public.symlink_to(public.with_name("missing-target"))
    else:
        monkeypatch.setenv(core.pin_registry.RUNTIME_PINS_FILE_ENV, str(private))
    with pytest.raises(core.pin_registry.RuntimePinRegistryError):
        core._paired_pin_baseline(config, [])


def test_present_valid_pin_payload_and_bytes_remain_protected(unpaired_pin_case, monkeypatch):
    config, private, _ = unpaired_pin_case
    private.write_text("valid-authority-fixture")
    monkeypatch.setattr(core.candidate, "current_pinned_runtime_release", lambda: SimpleNamespace(to_payload=lambda: {"fixture": "strict-reader-success"}))
    before = core._paired_pin_baseline(config, [])
    assert before["release"] == {"fixture": "strict-reader-success"}
    private.write_text("changed-authority-fixture")
    assert before != core._paired_pin_baseline(config, [])


def test_weather_claimed_target_head_still_requires_actual_live_head(weather_case):
    s = weather_case
    s.runtime["schema_before"] = [weather.HEAD]
    with pytest.raises(ServiceReleaseError):
        apply_weather(s)
    assert s.steps == ["factory"]
    assert release.read_current_release("weather", root=s.root) == s.current


@pytest.mark.parametrize("value", ["fixture$secret", "fixture$$secret"])
def test_weather_migration_preserves_canonical_config_dollars(weather_case, value):
    s = weather_case
    for name in weather.RUNTIME:
        s.records[name]["Config"]["Env"] = [
            "PGPASSWORD=" + value if v.startswith("PGPASSWORD=") else v
            for v in s.records[name]["Config"]["Env"]
        ]
    s.resolved["services"][weather.MIGRATE]["environment"]["PGPASSWORD"] = value.replace("$", "$$")
    result = apply_weather(s)
    assert result["success"], result


@pytest.mark.parametrize("query", [
    "host=other.invalid", "port=5433", "dbname=other_database", "host=%2Ffake_socket",
    "service=other", "hostaddr=192.0.2.55", "database=other", "conninfo=host%3Dother.invalid",
    "%68ost=other.invalid", "HOST=other.invalid", "host=127.0.0.1&host=other.invalid",
])
def test_weather_dsn_routing_query_is_refused_before_mutation(weather_case, query):
    s = weather_case
    dsn = "postgresql+psycopg://weather@127.0.0.1:11000/kor_travel_weather?" + query
    # Neutralize dollar handling here so it cannot mask a routing failure.
    for name in weather.RUNTIME:
        s.records[name]["Config"]["Env"] = [
            "KOR_TRAVEL_WEATHER_DATABASE_URL=" + dsn if v.startswith("KOR_TRAVEL_WEATHER_DATABASE_URL=")
            else "PGPASSWORD=fixture-plain" if v.startswith("PGPASSWORD=") else v
            for v in s.records[name]["Config"]["Env"]
        ]
    env = s.resolved["services"][weather.MIGRATE]["environment"]
    env["KOR_TRAVEL_WEATHER_DATABASE_URL"] = dsn
    env["PGPASSWORD"] = "fixture-plain"
    with pytest.raises(ServiceReleaseError):
        apply_weather(s, execute=False)
    assert not s.calls


@pytest.mark.parametrize("host,query", [
    ("127.0.0.1", ""), ("127.0.0.1", "?connect_timeout=5"), ("LOCALHOST", ""), ("LocalHost", ""),
])
def test_weather_standard_dsn_and_harmless_timeout_remain_supported(weather_case, host, query):
    s = weather_case
    dsn = "postgresql+psycopg://weather@" + host + ":11000/kor_travel_weather" + query
    for name in weather.RUNTIME:
        s.records[name]["Config"]["Env"] = [
            "KOR_TRAVEL_WEATHER_DATABASE_URL=" + dsn if v.startswith("KOR_TRAVEL_WEATHER_DATABASE_URL=") else v
            for v in s.records[name]["Config"]["Env"]
        ]
    s.resolved["services"][weather.MIGRATE]["environment"]["KOR_TRAVEL_WEATHER_DATABASE_URL"] = dsn
    result = apply_weather(s, execute=False)
    assert result["success"], result
    assert not s.calls


def test_weather_new_reviewed_main_and_new_source_head_are_not_a_static_sha_or_0040_allowlist(weather_case):
    s = weather_case
    path = s.source / "alembic/versions/0041.py"
    path.write_text("revision='0041_next_reviewed_feature'\ndown_revision='0040_region_locations'\n")
    paired.git(s.source, "add", "alembic")
    paired.git(s.source, "commit", "-qm", "new reviewed main fixture")
    s.sha = paired.git(s.source, "rev-parse", "HEAD")
    s.tree = paired.git(s.source, "rev-parse", "HEAD^{tree}")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", s.sha)
    s.runtime["release_sha"] = s.sha
    s.runtime["schema_target"] = ["0041_next_reviewed_feature"]
    s.normal.update(release_sha=s.sha, tree=s.tree)
    for role, image_id in s.runtime["images"].items():
        config = s.metadata[image_id]["Config"]
        config["Labels"].update({"org.opencontainers.image.revision": s.sha, "io.kor-travel-weather.source-tree": s.tree})
        if role != "frontend":
            config["Env"] = ["KOR_TRAVEL_WEATHER_GIT_COMMIT=" + s.sha]
    result = apply_weather(s)
    assert result["success"], result
    assert result["schema_revisions"] == ["0041_next_reviewed_feature"]


def test_weather_older_commit_than_fetched_main_ref_is_rejected_before_factory(weather_case):
    s = weather_case
    (s.source / "next-main-feature").write_text("later reviewed feature\n")
    paired.git(s.source, "add", "next-main-feature")
    paired.git(s.source, "commit", "-qm", "fetched newer main fixture")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", paired.git(s.source, "rev-parse", "HEAD"))
    paired.git(s.source, "checkout", "--detach", s.sha)
    with pytest.raises(ServiceReleaseError):
        apply_weather(s)
    assert not s.calls and not s.steps


@pytest.mark.parametrize("phase", ["migrate", "start"])
def test_weather_child_exit_or_signal_has_no_second_attempt_or_current(weather_case, phase):
    s = weather_case
    setattr(s, "signals_" + phase, ["SIGTERM"])
    result = apply_weather(s)
    assert not result["success"] and result["interrupted_signals"] == ["SIGTERM"]
    assert s.steps.count(phase) == 1
    assert release.read_current_release("weather", root=s.root) == s.current


def test_weather_same_shared_project_geo_drift_refuses_current(weather_case):
    s = weather_case
    # A nonselected container in the same Compose project must not be ignored.
    s.records["backend"]["Config"]["Labels"]["com.docker.compose.project"] = s.project

    def change():
        s.records["backend"]["RestartCount"] += 1

    s.hooks["start"] = change
    result = apply_weather(s)
    assert not result["success"]
    assert release.read_current_release("weather", root=s.root) == s.current


@pytest.mark.parametrize("duplicate", [False, True])
def test_weather_preexisting_running_or_duplicate_migration_is_never_replaced(weather_case, duplicate):
    s = weather_case
    for i in range(2 if duplicate else 1):
        row = copy.deepcopy(s.records[weather.ROLES["api"]])
        row["Id"] = f"{4000 + i:064x}"
        row["Config"]["Labels"]["com.docker.compose.service"] = weather.MIGRATE
        row["State"].update(Running=not duplicate, Status="exited" if duplicate else "running")
        s.records["old-migration-" + str(i)] = row
    with pytest.raises(ServiceReleaseError):
        apply_weather(s)
    assert not s.calls


@pytest.mark.parametrize("change", ["id", "image", "started", "running", "command", "environment", "hostconfig", "mount", "duplicate", "removed"])
def test_weather_late_migration_drift_never_publishes_current(weather_case, change):
    s = weather_case

    def drift():
        row = s.records[weather.MIGRATE]
        if change == "id":
            row["Id"] = "9" * 64
        elif change == "image":
            row["Image"] = "sha256:" + "8" * 64
        elif change == "started":
            row["State"]["StartedAt"] = "2026-11-13T22:00:00Z"
        elif change == "running":
            row["State"]["Running"] = True
        elif change == "command":
            row["Config"]["Cmd"] = ["alembic", "stamp", "head"]
        elif change == "environment":
            row["Config"]["Env"].append("ADDED=must-refuse")
        elif change == "hostconfig":
            row["HostConfig"]["Binds"].reverse()
        elif change == "mount":
            row["Mounts"].append({"Type": "bind", "Source": "/fixture", "Destination": "/other", "RW": True})
        elif change == "duplicate":
            extra = copy.deepcopy(row)
            extra["Id"] = "9" * 64
            s.records["foreign-migration"] = extra
        else:
            del s.records[weather.MIGRATE]

    s.hooks["start"] = drift
    result = apply_weather(s)
    assert not result["success"], result
    assert release.read_current_release("weather", root=s.root) == s.current


def test_weather_cli_dispatches_separate_normal_and_runtime_receipts(monkeypatch, capsys):
    cfg = load_targets_config()
    monkeypatch.setattr(entry, "load_targets_config", lambda: cfg)
    monkeypatch.setattr(entry, "current_host", lambda config: "odroid")
    monkeypatch.setattr(entry, "target_hosts", lambda service, config: frozenset({"odroid"}))
    seen = []
    monkeypatch.setattr(weather, "apply_weather_core_candidate", lambda *a, **kw: seen.append((a, kw)) or {"success": True})
    parser = argparse.ArgumentParser()
    entry.add_service_release_parser(parser.add_subparsers())
    args = parser.parse_args([
        "service-release", "apply-core-candidate", "weather", "--source-clone", "/approved/source",
        "--source-sha", "a" * 40, "--build-receipt", "/approved/build", "--build-sha256", "b" * 64,
        "--receipt", "/approved/runtime", "--sha256", "c" * 64, "--dry-run",
    ])
    assert entry._run(args) == 0
    assert seen[0][0] == ("weather",) and seen[0][1]["execute"] is False
    assert json.loads(capsys.readouterr().out)["success"] is True


@pytest.mark.parametrize("fault", [None, "missing", "cycle", "disconnected", "multiple_heads"])
def test_weather_actual_merge_revision_graph(monkeypatch, tmp_path, fault):
    graph = {
        "0035_base": None,
        "0036_snapshot_kind_projection": "0035_base",
        "0036_regional_alert_events": "0035_base",
        "0037_release_merge": ("0036_snapshot_kind_projection", "0036_regional_alert_events"),
        "0039_page_liveness": "0037_release_merge",
        "0040_region_locations": "0039_page_liveness",
    }
    if fault == "missing":
        graph.pop("0036_regional_alert_events")
    elif fault == "cycle":
        graph["0035_base"] = "0037_release_merge"
    elif fault == "disconnected":
        graph.update({"cycle_a": "cycle_b", "cycle_b": "cycle_a"})
    elif fault == "multiple_heads":
        graph["unmerged_branch"] = "0035_base"
    sha = "a" * 40
    tree = "b" * 40
    files = {"alembic/versions/" + k + ".py": "revision=" + repr(k) + "\ndown_revision=" + repr(v) + "\n" for k, v in graph.items()}
    def git(source, *args):
        if args[0] == "status":
            return ""
        if args[0] == "ls-tree":
            return "\n".join(files)
        if args[0] == "show":
            return files[args[1].split(":", 1)[1]]
        return tree if args[-1].endswith("^{tree}") else sha
    monkeypatch.setattr(core, "_git", git)
    monkeypatch.setattr(weather.candidate, "_merged_source", lambda *a, **kw: None)
    if fault:
        with pytest.raises(ServiceReleaseError):
            weather._source(tmp_path, sha)
    else:
        actual_tree, lineage = weather._source(tmp_path, sha)
        assert actual_tree == tree
        assert lineage[0] == "0040_region_locations"
        assert set(lineage) == set(graph)


@pytest.mark.parametrize("same_head,running", [(True, False), (True, True), (False, False), (False, True)])
def test_weather_same_head_preserves_completed_historical_migrations(weather_case, same_head, running):
    s = weather_case
    if same_head:
        s.runtime["schema_before"] = [weather.HEAD]
        s.schema = [weather.HEAD]
    historical = {}
    for i in range(2):
        row = copy.deepcopy(s.records[weather.ROLES["api"]])
        row["Id"] = f"{7000 + i:064x}"
        row["Config"]["Labels"]["com.docker.compose.service"] = weather.MIGRATE
        row["State"].update(Running=running and i == 0, Status="running" if running and i == 0 else "exited", ExitCode=0)
        name = "historical-migration-" + str(i)
        s.records[name] = row
        historical[name] = copy.deepcopy(row)
    if same_head and not running:
        result = apply_weather(s)
        assert result["success"], result
        assert result["backup_skipped"] is True and result["migration_required"] is False
        assert "migrate" not in s.steps and "backup" not in s.steps
        assert all(weather.MIGRATE not in argv for argv, _ in s.calls)
        assert {name: s.records[name] for name in historical} == historical
    else:
        with pytest.raises(ServiceReleaseError):
            apply_weather(s)
        assert not s.calls
        assert {name: s.records[name] for name in historical} == historical
