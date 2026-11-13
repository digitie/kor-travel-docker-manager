"""Weather 기존 main의 정상 이미지3개와 명시 migration만 적용한다.

Transport entrypoint/paired 경로는 바꾸지 않는다. 공용 plane은 관측만 하며,
기존 Transport와 같이 Weather instigator pause/drain을 실행 전 전제로 요구한다.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import docker

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services import service_release_compose as frozen
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import standalone_backup as backup
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock
from kor_travel_docker_manager.services.compose_service import (
    _compose_wait_timeout_seconds,
    _escape_materialized_compose_environment_values,
    get_env_path,
)
from kor_travel_docker_manager.services.registry import (
    load_targets_config,
    weather_core_candidate_for_target,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

ROLES = {"api": "kor-travel-weather-api", "code-server": "kor-travel-weather-dagster-code-server",
         "frontend": "kor-travel-weather-web"}
RUNTIME = set(ROLES.values())
MIGRATE = "kor-travel-weather-migrate"
LOCATION = "kortravelweather_dagster.definitions"
PUBLIC = {"NEXT_PUBLIC_VWORLD_API_KEY", "NEXT_PUBLIC_DAGSTER_URL"}
HEAD = "0040_region_locations"
URL = "https://github.com/digitie/kor-travel-weather.git"


def _project(config: dict) -> str:
    return entry._declaration("weather", config)["groups"]["main"]["project"]


def _source(source: Path, sha: str) -> tuple[str, tuple[str, ...]]:
    candidate._merged_source(source, sha, "weather", canonical_url=URL)
    if (core._git(source, "rev-parse", "HEAD").strip() != sha
            or core._git(source, "rev-parse", "refs/remotes/origin/main").strip() != sha
            or core._git(source, "status", "--porcelain").strip()):
        raise ServiceReleaseError("Weather actual merged M의 clean HEAD가 필요합니다")
    paths = core._git(source, "ls-tree", "-r", "--name-only", sha, "alembic/versions").splitlines()
    graph = {}
    for path in paths:
        if not path.endswith(".py"):
            continue
        values = {}
        for node in ast.parse(core._git(source, "show", sha + ":" + path)).body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    if target.id in values:
                        raise ServiceReleaseError("중복 Weather Alembic 선언입니다")
                    values[target.id] = ast.literal_eval(node.value)
        if not values:
            continue
        rev, parent = values["revision"], values["down_revision"]
        if not isinstance(rev, str) or not re.fullmatch(r"[a-z0-9_]+", rev) or rev in graph:
            raise ServiceReleaseError("Weather Alembic revision이 잘못되었습니다")
        if parent is not None and not isinstance(parent, str):
            raise ServiceReleaseError("Weather 단일 migration lineage만 허용합니다")
        graph[rev] = parent
    parents = {v for v in graph.values() if v is not None}
    heads = set(graph) - parents
    if len(heads) != 1 or parents - set(graph) or HEAD not in graph:
        raise ServiceReleaseError("Weather0040을 포함한 실제 merged Git의 단일 head가 필요합니다")
    lineage, current = [], next(iter(heads))
    while current is not None:
        if current in lineage:
            raise ServiceReleaseError("Weather migration cycle입니다")
        lineage.append(current)
        current = graph[current]
    if set(lineage) != set(graph):
        raise ServiceReleaseError("Weather 연결되지 않은 migration입니다")
    return core._git(source, "rev-parse", sha + "^{tree}").strip(), tuple(lineage)


def _normal_receipt(normal: dict, runtime: dict, sha: str, tree: str, lineage: tuple[str, ...]) -> None:
    try:
        if (type(normal["version"]) is not int or normal["version"] != 1
                or normal["kind"] != "weather-normal-build" or normal["mode"] != "NORMAL_BUILD_ONLY"
                or normal["normalDockerBuild"] is not True or normal["actualApplyAllowed"] is not False
                or normal["deployReady"] is not False or normal["output"] != "docker"
                or normal["architecture"] != "arm64" or normal["source"] != URL
                or normal["release_sha"] != sha or normal["tree"] != tree
                or set(normal["images"]) - {"migrate"} != set(ROLES)
                or set(normal["artifacts"]) != set(ROLES)):
            raise ValueError
        if "migrate" in normal["images"] and normal["images"]["migrate"] != normal["images"]["api"]:
            raise ValueError
        if (type(runtime["version"]) is not int or runtime["version"] != 1
                or runtime["release_sha"] != sha or set(runtime["images"]) != set(ROLES)
                or any(runtime["images"][k] != normal["images"][k] for k in ROLES)
                or runtime["schema_target"] != [lineage[0]]
                or not isinstance(runtime["schema_before"], list) or len(runtime["schema_before"]) != 1
                or runtime["schema_before"][0] not in lineage):
            raise ValueError
        for role in ROLES:
            release._image_id(runtime["images"][role])
            artifact = normal["artifacts"][role]
            if (artifact["imageID"] != runtime["images"][role] or artifact["loaded"] is not True
                    or artifact["normalDockerBuild"] is not True or artifact["architecture"] != "arm64"
                    or not re.fullmatch(r"[0-9a-f]{64}", artifact["COPY"]["sha256"])):
                raise ValueError
        state.validate_fingerprint(runtime["state"]["fingerprint"])
        if (not re.fullmatch(r"[0-9a-f]{64}", runtime["state"]["source_container"])
                or state.state_manifest(runtime["state"]["fingerprint"]) != runtime["state"]["manifest"]):
            raise ValueError
        baseline = runtime["public_baseline"]
        release._image_id(baseline["image_id"])
        hashes = baseline["hashes"]
        if set(hashes) != PUBLIC or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()):
            raise ValueError
        if normal["artifacts"]["frontend"]["publicInputHashes"] != hashes:
            raise ValueError
        core._instigators(runtime["instigators"], location=LOCATION)
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceReleaseError("Weather normal/runtime receipt tuple이 다릅니다") from exc


def _images(client: Any, normal: dict, runtime: dict, sha: str, tree: str) -> dict:
    result = {}
    for role, service in ROLES.items():
        image_id = runtime["images"][role]
        result[service] = release._metadata(client, image_id)
        image = client.images.get(image_id).attrs
        config = image.get("Config") or {}
        labels = config.get("Labels") or {}
        artifact = normal["artifacts"][role]
        if (image.get("Architecture") != "arm64" or image.get("Os", image.get("OS")) != "linux"
                or result[service]["source_sha"] != sha
                or labels.get("org.opencontainers.image.source") != URL
                or labels.get("io.kor-travel-weather.source-tree") != tree
                or labels.get("io.kor-travel-weather.build-role") != role
                or labels.get("io.kor-travel-weather.copy-sha256") != artifact["COPY"]["sha256"]):
            raise ServiceReleaseError("Weather 정상 이미지/OCI/role/COPY 증명이 다릅니다")
        env = component._environment_values(config.get("Env") or [])
        if role != "frontend" and env.get("KOR_TRAVEL_WEATHER_GIT_COMMIT") != sha:
            raise ServiceReleaseError("Python baked revision이 승인 M과 다릅니다")
        if role == "frontend":
            if config.get("User") != "node":
                raise ServiceReleaseError("Weather frontend USER node가 필요합니다")
            for key, digest in runtime["public_baseline"]["hashes"].items():
                if labels.get("io.kor-travel-weather.public-input-" + key.lower()) != digest:
                    raise ServiceReleaseError("Weather baked public 입력이 승인 baseline과 다릅니다")
    return result


def _baked_baseline(client: Any, record: dict, baseline: dict) -> None:
    """ENV가 아닌 기존 UI의 승인 compiled byte 구간에서 public 기준을 확인한다."""
    ranges = baseline.get("byte_ranges")
    if not isinstance(ranges, dict) or set(ranges) != PUBLIC or baseline["image_id"] != record["Image"]:
        raise ServiceReleaseError("현재 UI compiled public byte 증명이 필요합니다")
    for key, proof in ranges.items():
        path = proof.get("path", "")
        offset, length = proof.get("offset"), proof.get("length")
        if (not isinstance(path, str) or not path.startswith("/app/.next/static/chunks/")
                or not path.endswith(".js") or ".." in Path(path).parts
                or type(offset) is not int or type(length) is not int or offset < 0 or not 0 < length <= 8192):
            raise ServiceReleaseError("UI compiled public 구간이 올바르지 않습니다")
        chunks, _ = client.api.get_archive(record["Id"], path)
        try:
            raw = bytearray()
            for chunk in chunks:
                raw.extend(chunk)
                if len(raw) > 16 * 1024 * 1024:
                    raise ServiceReleaseError("UI compiled public 파일 상한을 넘었습니다")
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].size > 8 * 1024 * 1024:
                raise ServiceReleaseError("UI compiled public 원문이 일반 파일이 아닙니다")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise ServiceReleaseError("UI compiled public 파일을 읽을 수 없습니다")
            data = stream.read(8 * 1024 * 1024 + 1)
        if (hashlib.sha256(data).hexdigest() != proof.get("file_sha256") or offset + length > len(data)
                or hashlib.sha256(data[offset:offset + length]).hexdigest() != baseline["hashes"][key]):
            raise ServiceReleaseError("현재 UI 실제 compiled public 입력과 승인 hash가 다릅니다")


def _schema(lease_fd: int) -> list[str]:
    container, database = backup._role_config("weather")
    port, admin = backup._discover_port(container), backup._discover_admin_role(container)
    completed, interrupted = frozen._run_mutation(
        ["docker", "exec", "--user", "postgres", container, "psql", "--username", admin,
         "--port", str(port), "--dbname", database, "--no-psqlrc", "--quiet", "--tuples-only", "--no-align",
         "--command", "BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SELECT version_num FROM public.alembic_version; COMMIT;"],
        additional_fds=(lease_fd,), env=frozen._environment(), text=True, capture_output=True, check=False, timeout=60,
    )
    rows = completed.stdout.strip().splitlines()
    if completed.returncode or interrupted or len(completed.stdout) > 16384 or len(rows) != 1 or not re.fullmatch(r"[a-z0-9_]+", rows[0]):
        raise ServiceReleaseError("Weather actual schema의 단일 revision을 확인할 수 없습니다")
    return rows


def _database_scope(environment: dict) -> None:
    container, database = backup._role_config("weather")
    parsed = urlsplit(environment.get("KOR_TRAVEL_WEATHER_DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://"))
    if (parsed.scheme != "postgresql" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.path != "/" + database or parsed.port != backup._discover_port(container)):
        raise ServiceReleaseError("Weather migrate와 backup의 등록 tenant DB가 다릅니다")


def _child(operation: str, *, stage: Path, lease_fd: int, **payload) -> dict:
    completed, interrupted = frozen._run_mutation(
        [sys.executable, "-I", "-m", "kor_travel_docker_manager.services.service_release_weather_candidate"],
        additional_fds=(lease_fd,), env=frozen._environment(), text=True, capture_output=True, check=False,
        input=json.dumps({"operation": operation, "stage": str(stage), "lease_fd": lease_fd, **payload}),
    )
    if completed.returncode or interrupted:
        raise ServiceReleaseError("Weather backup/state 자식이 실패하거나 중단되었습니다")
    result = json.loads(completed.stdout)
    if result.get("success") is not True:
        raise ServiceReleaseError("Weather backup/state 결과가 실패했습니다")
    return result


def apply_weather_core_candidate(service: str, *, source_clone: Path, source_sha: str,
                                 receipt_path: Path, receipt_sha256: str, build_receipt_path: Path,
                                 build_receipt_sha256: str, root: Path, config: dict | None = None,
                                 execute: bool = False, client_factory: Any = None) -> dict:
    """Weather만 허용한다. 원문/credential을 오류에 노출하지 않는다."""
    try:
        if service != "weather" or type(execute) is not bool or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise ServiceReleaseError("Weather와 승인 actual merged M만 허용합니다")
        if os.geteuid() != 0:
            raise ServiceReleaseError("Weather core는 root로 실행해야 합니다")
        release._root(root)
        config = load_targets_config() if config is None else config
        group, lease = weather_core_candidate_for_target(service, config)
        with manager_mutation_lock(), state.project_lease(lease) as fd:
            tree, lineage = _source(Path(source_clone), source_sha)
            normal = core._receipt(Path(build_receipt_path), build_receipt_sha256)
            runtime = core._receipt(Path(receipt_path), receipt_sha256)
            _normal_receipt(normal, runtime, source_sha, tree, lineage)
            plan, manifests = entry._compose_plan(service, group, root=root, config=config)
            client = (client_factory or (lambda: docker.from_env(timeout=10)))()
            try:
                return _apply(plan, manifests, normal, runtime, source_sha, tree, Path(source_clone), lineage,
                              Path(receipt_path), receipt_sha256, Path(build_receipt_path), build_receipt_sha256,
                              root, config, fd, lease, client, execute)
            finally:
                client.close()
    except ServiceReleaseError:
        raise
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, subprocess.TimeoutExpired,
            tarfile.TarError, docker.errors.DockerException) as exc:
        raise ServiceReleaseError("Weather source/receipt/lease 입력을 안전하게 검증할 수 없습니다") from exc


def _document(resolved: dict, names: set[str]) -> dict:
    """해석을 끝낸 동일 문서에서 선택 역할만 실행 가능하게 남긴다."""
    result = deepcopy(resolved)
    result["services"] = {n: result["services"][n] for n in sorted(names)}
    for service in result["services"].values():
        service.pop("build", None)
        service.pop("depends_on", None)
        if any(service.get(k) for k in ("links", "volumes_from", "extends")):
            raise ServiceReleaseError("Weather 선택 밖 실행 참조는 허용하지 않습니다")
    for resource in ("volumes", "networks", "configs", "secrets"):
        used = set()
        for definition in result["services"].values():
            refs = definition.get(resource, [])
            if resource == "networks":
                used.update(refs)
            else:
                used.update(v["source"] if isinstance(v, dict) else v for v in refs
                            if resource != "volumes" or isinstance(v, dict) and v.get("type") == "volume")
        if resource in result:
            result[resource] = {k: v for k, v in result[resource].items() if k in used}
    return result


def _apply(plan, manifests, normal, runtime, sha, tree, source, lineage,
           receipt_path, receipt_hash, build_path, build_hash, root, config, fd, lease, client, execute):
    own = manifests["weather"]
    group = own["groups"]["main"]
    project = _project(config)
    if (group["project"] != project or set(group["runtime_services"]) != RUNTIME
            or set(group["oneshot_images"]) != {MIGRATE}):
        raise ServiceReleaseError("Weather 기존 main current의 역할이 다릅니다")
    if any(v.get("result") != "healthy" for v in frozen._verify(manifests, client).values()):
        raise ServiceReleaseError("기존 current runtime이 healthy여야 합니다")
    records = candidate._inventory(client)
    existing_migration = [r for r in records if candidate._selected(r, project, MIGRATE)]
    if len(existing_migration) > 1 or any(r.get("State", {}).get("Running") is not False for r in existing_migration):
        raise ServiceReleaseError("Weather 기존 migration은 미실행 단일 oneshot이어야 합니다")
    selected = core._selected(records, project, RUNTIME)
    cs = selected[ROLES["code-server"]]
    if cs["Id"] != runtime["state"]["source_container"]:
        raise ServiceReleaseError("Weather state source CID가 현재와 다릅니다")
    if selected[ROLES["frontend"]]["Image"] != runtime["public_baseline"]["image_id"]:
        raise ServiceReleaseError("승인 baked public baseline은 현재 UI imageID여야 합니다")
    _baked_baseline(client, selected[ROLES["frontend"]], runtime["public_baseline"])
    metadata = _images(client, normal, runtime, sha, tree)
    expected_ids = {k: (v["Id"], v["Image"], v["State"]["StartedAt"], v.get("RestartCount")) for k, v in selected.items()}
    excluded = RUNTIME | {MIGRATE}

    def others(rows):
        return candidate._nonselected([r for r in rows if not any(candidate._selected(r, project, n) for n in excluded)], project, "")

    untouched = others(records)
    pins = candidate.current_pinned_runtime_release().to_payload()
    secrets = frozen._secrets(manifests)
    bindings, environments = [], []
    for role, name in ROLES.items():
        bind, unchanged, values = component._preserve_environment(
            client, project, name, runtime["images"][role],
            release_env=None if role == "frontend" else "KOR_TRAVEL_WEATHER_GIT_COMMIT", source_sha=sha)
        bindings.append(bind)
        environments.append(unchanged)
        secrets.extend(values)
    rendered = candidate._render([*plan["argv"], "config", "--format", "json"], cwd=group["working_dir"],
                                 env=frozen._environment(), text=True, capture_output=True, check=False, timeout=30)
    if rendered.returncode:
        raise ServiceReleaseError("Weather 등록 Compose를 해석할 수 없습니다")
    materialized = json.loads(rendered.stdout)
    secrets.extend(frozen._resolved_secrets(materialized))
    for manifest in manifests.values():
        for name, image in manifest["images"].items():
            if client.images.get(materialized["services"][name]["image"]).attrs["Id"] != image["id"]:
                raise ServiceReleaseError("Weather shared-project current 이미지가 다릅니다")
            materialized["services"][name]["image"] = image["id"]
    for role, name in ROLES.items():
        core._mount_admission(materialized["services"][name], materialized, selected[name],
                              client.images.get(runtime["images"][role]).attrs, project)
        materialized["services"][name]["image"] = runtime["images"][role]
    migration = materialized["services"][MIGRATE]
    if migration.get("command") != ["alembic", "upgrade", "head"] or migration.get("restart", "no") != "no":
        raise ServiceReleaseError("등록된 Weather migration oneshot command만 허용합니다")
    migration["image"] = runtime["images"]["api"]
    api_env = component._environment_values(selected[ROLES["api"]]["Config"].get("Env"))
    migration_env = migration.get("environment")
    if not isinstance(migration_env, dict) or any(migration_env.get(k) != api_env.get(k) for k in (
            "KOR_TRAVEL_WEATHER_DATABASE_URL", "PGPASSWORD")):
        raise ServiceReleaseError("migration과 live Weather DB 입력이 다릅니다")
    _database_scope(api_env)
    # config JSON의 raw dollar도 runtime 보존과 똑같이 정확히 한 번 인코딩한다.
    if any(not isinstance(value, str) for value in migration_env.values()):
        raise ServiceReleaseError("Weather migration 환경은 완전한 문자열이어야 합니다")
    migration["environment"] = {key: value.replace("$", "$$") for key, value in migration_env.items()}
    migration.pop("env_file", None)
    for bind in bindings:
        bind(materialized)
    runtime_document = _document(materialized, RUNTIME)
    migration_document = _document(materialized, {MIGRATE})
    wait = _compose_wait_timeout_seconds(runtime_document["services"])
    protected = {Path(get_env_path()), receipt_path, build_path}
    for manifest in manifests.values():
        for spec in manifest["groups"].values():
            protected.update(map(Path, spec["config_files"]))
            if spec["env_file"]:
                protected.add(Path(spec["env_file"]))
    for path in [Path(group["working_dir"]) / ".env", Path(group["working_dir"]) / "docker-compose.override.yml"]:
        if path.exists():
            protected.add(path)
    for record in selected.values():
        protected.update(Path(m["Source"]) for m in record["Mounts"] if m["Type"] == "bind" and Path(m["Source"]).is_file())
    files = core._protected(protected)
    new_manifest = deepcopy(own)
    new_manifest["images"].update(metadata)
    new_manifest["images"][MIGRATE] = metadata[ROLES["api"]]
    new_manifest["groups"]["main"]["oneshot_images"][MIGRATE] = runtime["images"]["api"]
    result = {"service": "weather", "group": "main", "source_sha": sha, "images": runtime["images"],
              "success": False, "result": "failed", "phase": "admission"}

    def common():
        state.check_lease(fd, lease)
        if _source(source, sha) != (tree, lineage):
            raise ServiceReleaseError("Weather 승인 source가 변경되었습니다")
        if core._protected(protected) != files or candidate.current_pinned_runtime_release().to_payload() != pins:
            raise ServiceReleaseError("Weather 보호 파일/pin이 변경되었습니다")
        if others(candidate._inventory(client)) != untouched:
            raise ServiceReleaseError("Weather 비선택 runtime이 변경되었습니다")
        _images(client, normal, runtime, sha, tree)
        for check in environments:
            check()

    def admission():
        common()
        if _source(source, sha) != (tree, lineage):
            raise ServiceReleaseError("Weather 승인 Git이 변경되었습니다")
        _normal_receipt(core._receipt(build_path, build_hash), core._receipt(receipt_path, receipt_hash), sha, tree, lineage)
        now = core._selected(candidate._inventory(client), project, RUNTIME)
        if {k: (v["Id"], v["Image"], v["State"]["StartedAt"], v.get("RestartCount")) for k, v in now.items()} != expected_ids:
            raise ServiceReleaseError("Weather 교체 전 singleton identity가 변경되었습니다")
        core._quiet(runtime, cs["Id"], candidate._inventory(client), config, fd, location=LOCATION)
        if _schema(fd) != runtime["schema_before"] or core._fingerprint(cs["Id"], fd) != runtime["state"]["fingerprint"]:
            raise ServiceReleaseError("Weather live schema/state가 승인 입력과 다릅니다")
        backup.require_local_backup_role("weather")

    admission()
    if not execute:
        return {**result, "success": True, "result": "planned", "migrationRequired": runtime["schema_before"] != runtime["schema_target"],
                "stateHandoffRequired": True, "weatherInstigatorsMustRemainPaused": True}
    stage = root / ("weather-core-state-" + uuid4().hex)
    stage.mkdir(mode=0o700)
    result["private_state_directory"] = str(stage)
    argv = ["docker", "compose", "--project-name", project, "--project-directory", group["working_dir"], "--env-file", "/dev/null", "-f", "-"]

    def compose(args, document=runtime_document):
        completed, interrupted = frozen._run_mutation([*argv, *args], additional_fds=(fd,),
            cwd=group["working_dir"], env=frozen._environment(), text=True, capture_output=True, check=False,
            input=json.dumps(_escape_materialized_compose_environment_values(document), ensure_ascii=False))
        if completed.returncode or interrupted:
            result.update(returncode=completed.returncode, interrupted_signals=interrupted,
                          stderr=frozen._detail(completed.stderr, secrets))
            raise ServiceReleaseError("Weather 선택 Compose 단계가 실패하거나 중단되었습니다")

    try:
        result["phase"] = "stop_selected"
        compose(["stop", *sorted(RUNTIME)])
        common()
        stopped = core._selected(candidate._inventory(client), project, RUNTIME)
        if any(stopped[n]["Id"] != selected[n]["Id"] or stopped[n]["State"].get("Running") is not False for n in RUNTIME):
            raise ServiceReleaseError("Weather 기존 세 runtime 정지를 확인할 수 없습니다")
        result["phase"] = "snapshot_and_backup"
        _child("snapshot", stage=stage, lease_fd=fd, cid=cs["Id"], image_id=cs["Image"], fingerprint=runtime["state"]["fingerprint"])
        backed = _child("backup", stage=stage, lease_fd=fd)["manifest"]
        if (backed.get("role") != "weather" or backed.get("alembic_head") != runtime["schema_before"][0]
                or backed.get("byte_size", 0) <= 0 or backed.get("toc_entry_count", 0) <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", backed.get("sha256", ""))):
            raise ServiceReleaseError("Weather 검증 backup과 이전 schema가 다릅니다")
        result["backup"] = backed
        common()
        if _schema(fd) != runtime["schema_before"]:
            raise ServiceReleaseError("Weather backup 뒤 schema가 바뀌었습니다")
        result["phase"] = "migrate"
        if runtime["schema_before"] != runtime["schema_target"]:
            compose(["up", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                     "--abort-on-container-exit", "--exit-code-from", MIGRATE, MIGRATE], migration_document)
            migrated = core._selected(candidate._inventory(client), project, {MIGRATE})[MIGRATE]
            if (migrated["Image"] != runtime["images"]["api"] or migrated["State"].get("Running") is not False
                    or migrated["State"].get("ExitCode") != 0):
                raise ServiceReleaseError("Weather exact API-image migration이 정상 종료하지 않았습니다")
        if _schema(fd) != runtime["schema_target"]:
            raise ServiceReleaseError("Weather migration 뒤 실제 merged Git head와 다릅니다")
        common()
        result["phase"] = "create"
        compose(["up", "--no-start", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", *sorted(RUNTIME)])
        common()
        created = core._selected(candidate._inventory(client), project, RUNTIME)
        for role, name in ROLES.items():
            record = created[name]
            if (record["Image"] != runtime["images"][role] or record["State"].get("Status") != "created"
                    or record["State"].get("Running") is not False or core._mounts(record) != core._mounts(selected[name])):
                raise ServiceReleaseError("Weather 미시작 이미지/mount 보존 검사가 실패했습니다")
        new_cs = created[ROLES["code-server"]]["Id"]
        result["phase"] = "restore_before_start"
        _child("restore", stage=stage, lease_fd=fd, cid=new_cs, image_id=runtime["images"]["code-server"], fingerprint=runtime["state"]["fingerprint"])
        common()
        result["phase"] = "start"
        compose(["start", "--wait", "--wait-timeout", str(wait), *sorted(RUNTIME)])

        def healthy():
            common()
            actual = core._selected(candidate._inventory(client), project, RUNTIME)
            if any(actual[n]["Id"] != created[n]["Id"] or core._mounts(actual[n]) != core._mounts(selected[n]) for n in RUNTIME):
                raise ServiceReleaseError("Weather 사후 CID/mount가 다릅니다")
            if any(v.get("result") != "healthy" for v in frozen._verify({**manifests, "weather": new_manifest}, client).values()):
                raise ServiceReleaseError("Weather 후보가 healthy하지 않습니다")
            if _schema(fd) != runtime["schema_target"] or state.semantic(core._fingerprint(new_cs, fd)) != state.semantic(runtime["state"]["fingerprint"]):
                raise ServiceReleaseError("Weather 사후 schema/state가 다릅니다")
            core._quiet(runtime, new_cs, candidate._inventory(client), config, fd, location=LOCATION)

        result["phase"] = "postproof"
        healthy()
        current = candidate._publish(new_manifest, manifests, root, healthy)
        result.update(success=True, result="healthy", phase="published", current=current,
                      nonselected_unchanged=True, state_preserved=True, schema_revisions=runtime["schema_target"],
                      weather_instigators_paused=True, scheduler_resume_required=True)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, message=frozen._detail(str(exc), secrets)
                      if isinstance(exc, ServiceReleaseError) else "Weather 후보 단계 실패: current는 발행하지 않았습니다")
    return result


def _worker() -> None:
    """공식 모듈의 고정 Weather backup/state 작업만 실행한다."""
    frozen._mutation_lock_fd()
    request = json.loads(sys.stdin.read(32 * 1024 * 1024 + 1))
    config = load_targets_config()
    project = _project(config)
    _, lease = weather_core_candidate_for_target("weather", config)
    state.check_lease(request["lease_fd"], lease)
    stage = Path(request["stage"])
    release._trusted_ancestors(stage)
    if os.geteuid() != 0 or stat.S_IMODE(stage.stat().st_mode) != 0o700:
        raise ServiceReleaseError("Weather private state stage가 안전하지 않습니다")
    with state._transfer_signals() as interrupted:
        if request["operation"] == "backup":
            manifest = backup.create_standalone_backup("weather", backup_root=stage / "database-backups")
            result = {"success": True, "manifest": manifest.to_json()}
        else:
            state.validate_fingerprint(request["fingerprint"])
            expected = state.semantic(request["fingerprint"])
            client = docker.from_env(timeout=None)
            try:
                if request["operation"] == "snapshot":
                    record = client.api.inspect_container(request["cid"])
                    labels = record.get("Config", {}).get("Labels") or {}
                    if (record.get("Id") != request["cid"] or record.get("Image") != request["image_id"]
                            or record.get("State", {}).get("Running") is not False
                            or labels.get("com.docker.compose.project") != project
                            or labels.get("com.docker.compose.service") != ROLES["code-server"]):
                        raise ServiceReleaseError("Weather snapshot source는 승인된 정지 CS여야 합니다")
                    state.save_archive(client, request["cid"], stage / "state.tar")
                    if state.archive_semantic(stage / "state.tar") != expected:
                        raise ServiceReleaseError("Weather snapshot과 승인 state가 다릅니다")
                elif request["operation"] == "restore":
                    state.restore_state(client, request["cid"], request["image_id"], stage / "state.tar", expected,
                                        project=project, service=ROLES["code-server"])
                else:
                    raise ServiceReleaseError("알 수 없는 Weather state 작업입니다")
                result = {"success": True}
            finally:
                client.close()
        state.check_lease(request["lease_fd"], lease)
        if interrupted:
            raise ServiceReleaseError("Weather 자식 완료 뒤 중단 요청을 확인했습니다")
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        _worker()
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
