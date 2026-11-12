"""Transport core의 정상 immutable 이미지와 기존 state를 한 G 세션에서 적용한다.

새 일반 배포 framework가 아니다. 기존 current/catalog/frozen/FD/publication을
재사용하며, 세 runtime 외 migrate·DB·admin·공용 plane은 실행하지 않는다.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
import sys
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import docker

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services import service_release_compose as frozen
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock
from kor_travel_docker_manager.services.compose_service import (
    _capture_compose_environment_snapshot,
    _compose_wait_timeout_seconds,
    _escape_materialized_compose_environment_values,
    _revalidate_compose_environment_snapshot,
    get_env_path,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import _root_git_environment
from kor_travel_docker_manager.services.registry import (
    load_targets_config,
    transport_core_candidate_for_target,
)
from kor_travel_docker_manager.services.runtime_topology import listen_address
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

RUNTIME = {"backend", "frontend", "dagster-code-server"}
LOCATION = "kor-travel-transport"
_ACTIVE = ["NOT_STARTED", "QUEUED", "STARTING", "STARTED", "CANCELING"]
_SCHEMA_PROBE = r"""
import asyncio,json,os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
async def main():
 e=create_async_engine(os.environ['DATABASE_URL'])
 try:
  async with e.connect() as c:
   await c.execute(text('SET TRANSACTION READ ONLY'))
   await c.execute(text("SET LOCAL statement_timeout='5s'"))
   print(json.dumps(sorted((await c.execute(text('SELECT version_num FROM alembic_version'))).scalars().all())))
 finally: await e.dispose()
asyncio.run(main())
"""
_WORKER_PROBE = r"""
import os,pathlib,json
rows=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid():continue
 try:
  args=(p/'cmdline').read_bytes().split(b'\0')
  if b'execute_run' in args or any(a.startswith(b'from multiprocessing.spawn import spawn_main') for a in args):rows.append(int(p.name))
 except (FileNotFoundError,ProcessLookupError):pass
print(json.dumps(rows))
"""


def _git(source: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(source),
            *args,
        ],
        env=_root_git_environment(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ServiceReleaseError("승인 core Git 소스를 읽을 수 없습니다")
    return result.stdout


def _source(source: Path, sha: str, url: str) -> tuple[str, list[str]]:
    candidate._merged_source(source, sha, "transport", canonical_url=url)
    if (
        _git(source, "rev-parse", "HEAD").strip() != sha
        or _git(source, "status", "--porcelain", "--untracked-files=normal").strip()
    ):
        raise ServiceReleaseError("core 소스는 승인 M의 clean HEAD여야 합니다")
    tree = _git(source, "rev-parse", sha + "^{tree}").strip()
    paths = _git(
        source, "ls-tree", "-r", "--name-only", sha, "backend/alembic/versions"
    ).splitlines()
    revisions, parents = set(), set()
    for path in paths:
        if not path.endswith(".py"):
            continue
        module = ast.parse(_git(source, "show", sha + ":" + path))
        values = {}
        for node in module.body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    if target.id in values:
                        raise ServiceReleaseError("중복 Alembic revision 선언입니다")
                    values[target.id] = ast.literal_eval(node.value)
        if not values:
            continue
        revision, down = values["revision"], values["down_revision"]
        if (
            not isinstance(revision, str)
            or not re.fullmatch(r"[a-z0-9_]+", revision)
            or revision in revisions
        ):
            raise ServiceReleaseError("Alembic revision 선언이 올바르지 않습니다")
        revisions.add(revision)
        refs = [] if down is None else list(down) if isinstance(down, tuple) else [down]
        if any(not isinstance(ref, str) for ref in refs):
            raise ServiceReleaseError("Alembic parent 선언이 올바르지 않습니다")
        parents.update(refs)
    if not revisions or parents - revisions:
        raise ServiceReleaseError("승인 소스의 Alembic head를 증명할 수 없습니다")
    return tree, sorted(revisions - parents)


def _lease_contract(source: Path, sha: str, path: Path) -> None:
    # 기존 shell 배포기와 동일 파일을 잠근다. 선언 차이는 Docker 생성 전에 거부한다.
    body = _git(source, "show", sha + ":scripts/deploy-prod-remote.sh")
    declarations = re.findall(r'^release_lock_path="([^"]+)"$', body, flags=re.MULTILINE)
    if declarations != [str(path)]:
        raise ServiceReleaseError("승인 Transport 소스와 catalog의 기존 project lease가 다릅니다")


def _receipt(path: Path, checksum: str) -> dict:
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ServiceReleaseError("승인 receipt SHA256이 필요합니다")
    path = Path(path)
    release._trusted_ancestors(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != release._OWNER_UID
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or before.st_size > 32 * 1024 * 1024
        ):
            raise ServiceReleaseError("receipt 파일 소유권·권한·크기가 안전하지 않습니다")
        raw = stream.read(32 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    fields = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_gid",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    current = path.lstat()
    if (
        any(getattr(before, k) != getattr(after, k) for k in fields)
        or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
        or hashlib.sha256(raw).hexdigest() != checksum
    ):
        raise ServiceReleaseError("receipt가 변경되었거나 승인 checksum과 다릅니다")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ServiceReleaseError("receipt 객체가 필요합니다")
    return value


def _receipts(normal: dict, runtime: dict, sha: str, tree: str, heads: list[str]) -> None:
    try:
        if (
            type(normal["version"]) is not int
            or normal["version"] != 1
            or normal["kind"] != "transport-normal-build"
            or normal["normalDockerBuild"] is not True
            or normal["mode"] != "NORMAL_BUILD_ONLY"
            or normal["actualApplyAllowed"] is not False
            or normal["deployReady"] is not False
            or normal["release_sha"] != sha
            or normal["tree"] != tree
            or normal["output"] != "docker"
            or normal["architecture"] != "arm64"
        ):
            raise ValueError
        if (
            type(runtime["version"]) is not int
            or runtime["version"] != 1
            or runtime["release_sha"] != sha
            or set(runtime["images"]) != RUNTIME
            or runtime["schema_revisions"] != heads
            or not heads
            or {k: normal["images"][k] for k in RUNTIME} != runtime["images"]
        ):
            raise ValueError
        if runtime["images"]["backend"] != runtime["images"]["dagster-code-server"]:
            raise ValueError
        if (
            "migrate" in normal["images"]
            and normal["images"]["migrate"] != runtime["images"]["backend"]
        ):
            raise ValueError
        for image in runtime["images"].values():
            release._image_id(image)
        fingerprint = runtime["state"]["fingerprint"]
        state.validate_fingerprint(fingerprint)
        if (
            not re.fullmatch(r"[0-9a-f]{64}", runtime["state"]["source_container"])
            or state.state_manifest(fingerprint) != runtime["state"]["manifest"]
        ):
            raise ValueError
        _instigators(runtime["instigators"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceReleaseError(
            "normal build와 runtime-v1 receipt projection이 다릅니다"
        ) from exc


def _images(client: Any, normal: dict, runtime: dict, sha: str, tree: str, url: str) -> dict:
    if normal.get("source") != url:
        raise ServiceReleaseError("normal build canonical source가 다릅니다")
    metadata = {}
    for role, image_id in runtime["images"].items():
        metadata[role] = release._metadata(client, image_id)
        image = client.images.get(image_id).attrs
        cfg = image.get("Config") or {}
        labels = cfg.get("Labels") or {}
        build_role = "backend" if role == "dagster-code-server" else role
        artifact = normal["artifacts"][build_role]
        if (
            metadata[role]["source_sha"] != sha
            or image.get("Architecture") != "arm64"
            or image.get("Os", image.get("OS")) != "linux"
            or artifact["imageID"] != image_id
            or artifact["loaded"] is not True
            or artifact["normalDockerBuild"] is not True
            or artifact["architecture"] != "arm64"
            or labels.get("org.opencontainers.image.source") != url
            or labels.get("io.kor-travel-transport.source-tree") != tree
            or labels.get("io.kor-travel-transport.build-role") != build_role
            or labels.get("io.kor-travel-transport.copy-sha256") != artifact["COPY"]["sha256"]
        ):
            raise ServiceReleaseError("core 후보의 정상 build/OCI/ID 증명이 다릅니다")
        if role == "frontend" and cfg.get("User") != "node":
            raise ServiceReleaseError("정상 public web USER 계약이 다릅니다")
        if not isinstance(artifact["COPY"]["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", artifact["COPY"]["sha256"]
        ):
            raise ServiceReleaseError("정상 COPY digest가 올바르지 않습니다")
        defaults = component._environment_values(cfg.get("Env") or [])
        if defaults.get("RELEASE_SHA") != sha:
            raise ServiceReleaseError("backend/CS 기본 release 환경이 승인 M과 다릅니다")
    return metadata


def _preserve_baked_public_inputs(client: Any, old_image: str, new_image: str, normal: dict):
    """기존 이미지의 client 기본값을 승인 기준으로 삼는다. live ENV는 별도 보존한다."""
    keys = {"NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_API_PORT"}

    def defaults(image_id):
        image = client.images.get(image_id).attrs
        values = component._environment_values((image.get("Config") or {}).get("Env") or [])
        if image.get("Id") != image_id or not keys <= values.keys():
            raise ServiceReleaseError("public client 기본값 증명이 불완전합니다")
        return {key: values[key] for key in keys}

    approved = defaults(old_image)
    approved_hashes = {
        key: hashlib.sha256(value.encode()).hexdigest() for key, value in approved.items()
    }

    def unchanged():
        artifact = normal.get("artifacts", {}).get("frontend", {})
        hashes = artifact.get("publicInputHashes")
        labels = (client.images.get(new_image).attrs.get("Config") or {}).get("Labels") or {}
        # runtime override는 Next.js 빌드 때 고정된 client 입력의 승인이 아니다.
        if (
            defaults(old_image) != approved
            or defaults(new_image) != approved
            or not isinstance(hashes, dict)
            or hashes != approved_hashes
            or labels.get("io.kor-travel-transport.public-input-sha256")
            != hashlib.sha256(
                json.dumps(approved_hashes, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        ):
            raise ServiceReleaseError("기존 baked public client와 후보 build 입력 증명이 다릅니다")

    unchanged()
    return unchanged


def _instigators(rows: list[dict]) -> dict:
    if not isinstance(rows, list):
        raise ServiceReleaseError("instigator catalog가 필요합니다")
    result = {}
    for row in rows:
        key = (row["location"], row["repository"], row["kind"], row["name"])
        if (
            any(not isinstance(v, str) or not v for v in key)
            or key in result
            or key[2] not in {"schedule", "sensor"}
            or row["status"] not in {"RUNNING", "STOPPED"}
        ):
            raise ServiceReleaseError("instigator catalog가 올바르지 않습니다")
        result[key] = row["status"]
    if len({k[1] for k in result if k[0] == LOCATION}) != 1:
        raise ServiceReleaseError("Transport repository가 없거나 모호합니다")
    return result


def _graphql(query: str, records: list[dict], config: dict) -> dict:
    names = [
        v["compose_service"]
        for v in config["containers"].values()
        if v.get("role") == "dagster-shared-webserver"
    ]
    rows = [
        r
        for r in records
        if r.get("Config", {}).get("Labels", {}).get("com.docker.compose.service") in names
    ]
    if len(names) != 1 or len(rows) != 1:
        raise ServiceReleaseError("등록 공용 webserver를 하나로 확인할 수 없습니다")
    host, port = listen_address({"command": rows[0]["Config"]["Cmd"]})
    if host not in {"127.0.0.1", "localhost"}:
        raise ServiceReleaseError("공용 GraphQL의 loopback 주소가 필요합니다")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ServiceReleaseError("공용 GraphQL 응답 상한을 넘었습니다")
    payload = json.loads(raw)
    if payload.get("errors") or not isinstance(payload.get("data"), dict):
        raise ServiceReleaseError("공용 GraphQL 조회가 실패했습니다")
    return payload["data"]


def _probe(cid: str, script: str, lease_fd: int) -> Any:
    completed, interrupted = frozen._run_mutation(
        ["docker", "exec", cid, "python", "-I", "-c", script],
        additional_fds=(lease_fd,),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=frozen._environment(),
    )
    if completed.returncode or interrupted or len(completed.stdout) > 32 * 1024 * 1024:
        raise ServiceReleaseError("Transport 읽기 전용 probe가 실패했습니다")
    return json.loads(completed.stdout)


def _quiet(runtime: dict, cid: str, records: list[dict], config: dict, lease_fd: int) -> None:
    repos = _graphql(
        "{repositoriesOrError{__typename ... on RepositoryConnection{nodes{name location{name} schedules{name scheduleState{status}} sensors{name sensorState{status}}}}}}",
        records,
        config,
    )["repositoriesOrError"]
    if repos["__typename"] != "RepositoryConnection":
        raise ServiceReleaseError("instigator catalog 조회가 실패했습니다")
    if len([r for r in repos["nodes"] if r["location"]["name"] == LOCATION]) != 1:
        raise ServiceReleaseError("Transport repository가 없거나 모호합니다")
    rows = []
    for repo in repos["nodes"]:
        for kind in ("schedule", "sensor"):
            rows.extend(
                {
                    "location": repo["location"]["name"],
                    "repository": repo["name"],
                    "kind": kind,
                    "name": item["name"],
                    "status": item[kind + "State"]["status"],
                }
                for item in repo[kind + "s"]
            )
    expected = {
        k: "STOPPED" if k[0] == LOCATION else v
        for k, v in _instigators(runtime["instigators"]).items()
    }
    if _instigators(rows) != expected:
        raise ServiceReleaseError("Transport가 pause되지 않았거나 다른 instigator가 변경되었습니다")
    runs = _graphql(
        "{runsOrError(filter:{statuses:["
        + ",".join(_ACTIVE)
        + "]},limit:1000){__typename ... on Runs{results{runId repositoryOrigin{repositoryLocationName} tags{key value}}}}}",
        records,
        config,
    )["runsOrError"]
    if runs["__typename"] != "Runs" or len(runs["results"]) >= 1000:
        raise ServiceReleaseError("active run 관측이 불완전합니다")
    for row in runs["results"]:
        origin = (row.get("repositoryOrigin") or {}).get("repositoryLocationName")
        tags = [t["value"] for t in row.get("tags", []) if t["key"] == "dagster/code_location"]
        if (
            len(tags) > 1
            or not (origin or tags)
            or (origin and tags and origin != tags[0])
            or (origin or tags[0]) == LOCATION
        ):
            raise ServiceReleaseError("Transport active run 또는 불명확한 run이 있습니다")
    if _probe(cid, _WORKER_PROBE, lease_fd) != []:
        raise ServiceReleaseError("Transport worker가 실행 중입니다")


def _fingerprint(cid: str, lease_fd: int) -> dict:
    script = (
        "import json\nfrom pathlib import Path\n"
        + inspect.getsource(state.scan_state)
        + "\nprint(json.dumps(scan_state(Path('/opt/dagster/state'))))"
    )
    return _probe(cid, script, lease_fd)


def _selected(records: list[dict], project: str, runtime: set[str]) -> dict:
    result = {}
    for name in runtime:
        rows = [r for r in records if candidate._selected(r, project, name)]
        if len(rows) != 1:
            raise ServiceReleaseError("선택 runtime singleton이 필요합니다")
        result[name] = rows[0]
    return result


def _others(records: list[dict], project: str) -> dict:
    return candidate._nonselected(
        [r for r in records if not any(candidate._selected(r, project, n) for n in RUNTIME)],
        project,
        "",
    )


def _mounts(record: dict) -> list:
    mounts = record.get("Mounts")
    if not isinstance(mounts, list):
        raise ServiceReleaseError("선택 runtime Mounts를 읽을 수 없습니다")
    return sorted(mounts, key=lambda row: json.dumps(row, sort_keys=True))


def _mount_admission(
    definition: dict, resolved: dict, record: dict, image: dict, project: str
) -> None:
    actual = _mounts(record)
    expected = []
    for volume in definition.get("volumes", []):
        if not isinstance(volume, dict) or volume.get("type") not in {"bind", "volume"}:
            raise ServiceReleaseError("선택 mount 선언이 올바르지 않습니다")
        source, target = volume["source"], volume["target"]
        if volume["type"] == "volume":
            declared = resolved.get("volumes", {}).get(source, {})
            source = declared.get("name", project + "_" + source)
        expected.append(
            (
                volume["type"],
                source,
                target,
                not volume.get("read_only", False),
                (volume.get("bind") or {}).get("propagation", "rprivate")
                if volume["type"] == "bind"
                else "",
            )
        )
    observed = [
        (
            v["Type"],
            v.get("Name") if v["Type"] == "volume" else v["Source"],
            v["Destination"],
            v["RW"],
            v.get("Propagation", ""),
        )
        for v in actual
    ]
    if sorted(expected) != sorted(observed):
        raise ServiceReleaseError("등록 Compose mount와 실제 mount graph가 다릅니다")
    declared_image = set((image.get("Config") or {}).get("Volumes") or {})
    if declared_image - {v["Destination"] for v in actual}:
        raise ServiceReleaseError("후보 이미지가 새 anonymous volume을 선언합니다")
    for mount in actual:
        dest = mount["Destination"]
        if (
            dest == state.STATE_PATH
            or state.STATE_PATH.startswith(dest.rstrip("/") + "/")
            or dest.startswith(state.STATE_PATH + "/")
        ):
            raise ServiceReleaseError("고정 state 경로는 writable layer여야 합니다")


def _protected(paths: set[Path]) -> dict:
    result = {}
    for p in paths:
        if not p.exists() or not p.is_file():
            raise ServiceReleaseError("보호할 운영 입력 파일이 없습니다")
        real = p.resolve(strict=True)
        before = real.stat()
        if before.st_size > 32 * 1024 * 1024:
            raise ServiceReleaseError("보호 입력 파일이 너무 큽니다")
        raw = real.read_bytes()
        after = real.stat()
        if any(
            getattr(before, k) != getattr(after, k)
            for k in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ):
            raise ServiceReleaseError("운영 입력을 읽는 동안 변경되었습니다")
        result[str(p)] = (
            str(real),
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            hashlib.sha256(raw).hexdigest(),
        )
    return result


def _state_child(
    operation: str, cid: str, image_id: str, stage: Path, fingerprint: dict, lease_fd: int
) -> None:
    completed, interrupted = frozen._run_mutation(
        [sys.executable, "-I", "-m", "kor_travel_docker_manager.services.transport_core_state"],
        additional_fds=(lease_fd,),
        env=frozen._environment(),
        capture_output=True,
        text=True,
        check=False,
        input=json.dumps(
            {
                "operation": operation,
                "cid": cid,
                "image_id": image_id,
                "stage": str(stage),
                "fingerprint": fingerprint,
                "lease_fd": lease_fd,
            }
        ),
    )
    if (
        completed.returncode
        or interrupted
        or json.loads(completed.stdout).get("success") is not True
    ):
        raise ServiceReleaseError("state 인계 자식이 실패하거나 중단되었습니다")


def apply_transport_core_candidate(
    service: str,
    *,
    source_clone: Path,
    source_sha: str,
    receipt_path: Path,
    receipt_sha256: str,
    build_receipt_path: Path,
    build_receipt_sha256: str,
    root: Path,
    config: dict | None = None,
    execute: bool = False,
    client_factory: Any = None,
) -> dict:
    """Transport 전용 공개 SDK. 원문 입력·오류의 secret 값을 반환하지 않는다."""
    try:
        return _apply_transport_core_candidate(
            service,
            source_clone=Path(source_clone),
            source_sha=source_sha,
            receipt_path=Path(receipt_path),
            receipt_sha256=receipt_sha256,
            build_receipt_path=Path(build_receipt_path),
            build_receipt_sha256=build_receipt_sha256,
            root=Path(root),
            config=config,
            execute=execute,
            client_factory=client_factory,
        )
    except ServiceReleaseError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        SyntaxError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise ServiceReleaseError(
            "core source/receipt/lease 입력을 안전하게 검증할 수 없습니다"
        ) from exc


def _apply_transport_core_candidate(
    service: str,
    *,
    source_clone: Path,
    source_sha: str,
    receipt_path: Path,
    receipt_sha256: str,
    build_receipt_path: Path,
    build_receipt_sha256: str,
    root: Path,
    config: dict | None = None,
    execute: bool = False,
    client_factory: Any = None,
) -> dict:
    """승인한 Transport core 세 runtime만 적용한다. dry-run은 Docker/파일을 쓰지 않는다."""
    if (
        service != "transport"
        or type(execute) is not bool
        or not isinstance(source_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
    ):
        raise ServiceReleaseError("Transport core와 승인 merged SHA가 필요합니다")
    if os.geteuid() != 0:
        raise ServiceReleaseError("Transport core 후보는 root로 실행해야 합니다")
    release._root(root)
    config = load_targets_config() if config is None else config
    group, lease_path = transport_core_candidate_for_target(service, config)
    declaration = entry._declaration(service, config)["groups"][group]
    if set(declaration["runtime_services"]) != RUNTIME or declaration["oneshot_services"] != [
        "migrate"
    ]:
        raise ServiceReleaseError("Transport core runtime/one-shot catalog가 다릅니다")
    url = config["targets"][service]["ui_candidate"]["source_url"]
    with manager_mutation_lock(), state.project_lease(lease_path) as lease_fd:
        state.check_lease(lease_fd, lease_path)
        tree, heads = _source(source_clone, source_sha, url)
        _lease_contract(source_clone, source_sha, lease_path)
        normal = _receipt(build_receipt_path, build_receipt_sha256)
        runtime = _receipt(receipt_path, receipt_sha256)
        _receipts(normal, runtime, source_sha, tree, heads)
        plan, manifests = entry._compose_plan(service, group, root=root, config=config)
        client = (client_factory or (lambda: docker.from_env(timeout=10)))()
        try:
            return _apply(
                plan,
                manifests,
                normal,
                runtime,
                source_sha,
                tree,
                url,
                source_clone,
                receipt_path,
                receipt_sha256,
                build_receipt_path,
                build_receipt_sha256,
                root,
                config,
                lease_fd,
                lease_path,
                client,
                execute,
            )
        finally:
            client.close()


def _apply(
    plan,
    manifests,
    normal,
    runtime,
    sha,
    tree,
    url,
    source,
    receipt_path,
    receipt_hash,
    build_path,
    build_hash,
    root,
    config,
    lease_fd,
    lease_path,
    client,
    execute,
):
    own = manifests["transport"]
    group = own["groups"]["core"]
    project, cwd = group["project"], group["working_dir"]
    metadata = _images(client, normal, runtime, sha, tree, url)
    if any(v.get("result") != "healthy" for v in frozen._verify(manifests, client).values()):
        raise ServiceReleaseError("기존 모든 current runtime이 healthy여야 합니다")
    records = candidate._inventory(client)
    selected = _selected(records, project, RUNTIME)
    if selected["dagster-code-server"]["Id"] != runtime["state"]["source_container"]:
        raise ServiceReleaseError("receipt source CS identity가 현재와 다릅니다")
    public_inputs_unchanged = _preserve_baked_public_inputs(
        client, selected["frontend"]["Image"], runtime["images"]["frontend"], normal
    )
    old_ids = {
        k: (v["Id"], v["Image"], v["State"]["StartedAt"], v.get("RestartCount"))
        for k, v in selected.items()
    }
    others = _others(records, project)
    pins = candidate.current_pinned_runtime_release().to_payload()
    secrets = frozen._secrets(manifests)
    environments, bindings = {}, []
    for role in sorted(RUNTIME):
        oldenv = component._environment_values(selected[role]["Config"].get("Env"))
        if role != "frontend" and oldenv.get("RUN_DB_MIGRATIONS") != "false":
            raise ServiceReleaseError(
                "backend/CS migration은 기존 환경에서 명시적 false여야 합니다"
            )
        bind, unchanged, values = component._preserve_environment(
            client,
            project,
            role,
            runtime["images"][role],
            release_env=config["targets"]["transport"]["core_candidate"]["release_env"],
            source_sha=sha,
        )
        secrets.extend(values)
        bindings.append(bind)
        environments[role] = unchanged
    rendered = candidate._render(
        [*plan["argv"], "config", "--format", "json"],
        cwd=cwd,
        env=frozen._environment(),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if rendered.returncode:
        raise ServiceReleaseError("등록 core Compose config를 읽을 수 없습니다")
    materialized = json.loads(rendered.stdout)
    secrets.extend(frozen._resolved_secrets(materialized))
    for manifest in manifests.values():
        for name, image in manifest["images"].items():
            if (
                client.images.get(materialized["services"][name]["image"]).attrs["Id"]
                != image["id"]
            ):
                raise ServiceReleaseError("rendered current image identity가 다릅니다")
            materialized["services"][name]["image"] = image["id"]
    for role in RUNTIME:
        _mount_admission(
            materialized["services"][role],
            materialized,
            selected[role],
            client.images.get(runtime["images"][role]).attrs,
            project,
        )
        materialized["services"][role]["image"] = runtime["images"][role]
    materialized["services"]["migrate"]["image"] = runtime["images"]["backend"]
    for bind in bindings:
        bind(materialized)
    wait = _compose_wait_timeout_seconds({k: materialized["services"][k] for k in RUNTIME})
    # start에도 의존 one-shot이 들어올 수 없게 실행 문서는 runtime 세 개로 한정한다.
    materialized["services"] = {k: materialized["services"][k] for k in sorted(RUNTIME)}
    for definition in materialized["services"].values():
        definition.pop("depends_on", None)
        definition.pop("build", None)
        if any(definition.get(k) for k in ("links", "volumes_from", "extends")):
            raise ServiceReleaseError("선택 runtime 밖의 실행 참조를 허용하지 않습니다")
    for resource in ("volumes", "networks", "configs", "secrets"):
        used = set()
        for definition in materialized["services"].values():
            references = definition.get(resource, [])
            if resource == "networks":
                used.update(references)
            else:
                used.update(
                    row["source"] if isinstance(row, dict) else row
                    for row in references
                    if resource != "volumes"
                    or isinstance(row, dict)
                    and row.get("type") == "volume"
                )
        if resource in materialized:
            materialized[resource] = {
                key: value for key, value in materialized[resource].items() if key in used
            }
    protected = {Path(get_env_path()), Path(group["env_file"]), receipt_path, build_path}
    for manifest in manifests.values():
        for spec in manifest["groups"].values():
            protected.update(map(Path, spec["config_files"]))
            if spec["env_file"]:
                protected.add(Path(spec["env_file"]))
    for record in selected.values():
        protected.update(
            Path(m["Source"])
            for m in record["Mounts"]
            if m["Type"] == "bind" and Path(m["Source"]).is_file()
        )
    files = _protected(protected)
    env_snapshot = _capture_compose_environment_snapshot(
        environment_override=None,
        env_path=Path(group["env_file"]),
        compose_path=Path(group["config_files"][0]),
        override_path=Path(cwd) / "docker-compose.override.yml",
        include_process_environment=False,
        interpolate_env_file=False,
    )
    candidate_manifest = deepcopy(own)
    candidate_manifest["images"].update(metadata)
    candidate_manifest["images"]["migrate"] = metadata["backend"]
    candidate_manifest["groups"]["core"]["oneshot_images"]["migrate"] = runtime["images"]["backend"]
    old_cs = selected["dagster-code-server"]["Id"]
    result = {
        "service": "transport",
        "group": "core",
        "source_sha": sha,
        "images": runtime["images"],
        "success": False,
        "result": "failed",
    }

    def common():
        public_inputs_unchanged()
        state.check_lease(lease_fd, lease_path)
        candidate._unchanged_pointers(manifests, root)
        if (
            _protected(protected) != files
            or candidate.current_pinned_runtime_release().to_payload() != pins
        ):
            raise ServiceReleaseError("보호 파일 또는 pin이 변경되었습니다")
        _revalidate_compose_environment_snapshot(env_snapshot)
        if _others(candidate._inventory(client), project) != others:
            raise ServiceReleaseError("비선택 runtime identity 또는 설정이 변경되었습니다")
        for check in environments.values():
            check()

    def admission():
        common()
        _lease_contract(source, sha, lease_path)
        if _source(source, sha, url) != (tree, runtime["schema_revisions"]):
            raise ServiceReleaseError("승인 source가 변경되었습니다")
        _receipts(
            _receipt(build_path, build_hash),
            _receipt(receipt_path, receipt_hash),
            sha,
            tree,
            runtime["schema_revisions"],
        )
        current = _selected(candidate._inventory(client), project, RUNTIME)
        identities = {
            k: (v["Id"], v["Image"], v["State"]["StartedAt"], v.get("RestartCount"))
            for k, v in current.items()
        }
        if identities != old_ids:
            raise ServiceReleaseError("교체 전 선택 runtime identity가 변경되었습니다")
        _quiet(runtime, old_cs, candidate._inventory(client), config, lease_fd)
        if _probe(current["backend"]["Id"], _SCHEMA_PROBE, lease_fd) != runtime["schema_revisions"]:
            raise ServiceReleaseError("live schema와 승인 source head가 다릅니다")
        if _fingerprint(old_cs, lease_fd) != runtime["state"]["fingerprint"]:
            raise ServiceReleaseError("이전 CS state가 snapshot 뒤 변경되었습니다")
        _quiet(runtime, old_cs, candidate._inventory(client), config, lease_fd)

    admission()
    if not execute:
        return {
            **result,
            "success": True,
            "result": "planned",
            "state_handoff": "required_before_start",
        }
    stage = root / ("transport-core-state-" + uuid4().hex)
    stage.mkdir(mode=0o700)
    result["private_state_directory"] = str(stage)
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        cwd,
        "--env-file",
        "/dev/null",
        "-f",
        "-",
    ]
    frozen_input = json.dumps(
        _escape_materialized_compose_environment_values(materialized), ensure_ascii=False
    )

    def compose(args):
        completed, interrupted = frozen._run_mutation(
            [*command, *args],
            additional_fds=(lease_fd,),
            cwd=cwd,
            env=frozen._environment(),
            text=True,
            capture_output=True,
            check=False,
            input=frozen_input,
        )
        if completed.returncode or interrupted:
            result.update(
                returncode=completed.returncode,
                interrupted_signals=interrupted,
                stderr=frozen._detail(completed.stderr, secrets),
            )
            raise ServiceReleaseError("선택 core Compose 단계가 실패하거나 중단되었습니다")

    try:
        _state_child(
            "snapshot",
            old_cs,
            selected["dagster-code-server"]["Image"],
            stage,
            runtime["state"]["fingerprint"],
            lease_fd,
        )
        admission()
        result["phase"] = "create"
        compose(
            [
                "up",
                "--no-start",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--force-recreate",
                *sorted(RUNTIME),
            ]
        )
        common()
        created = _selected(candidate._inventory(client), project, RUNTIME)
        for role, record in created.items():
            if (
                record["Image"] != runtime["images"][role]
                or record["State"].get("Status") != "created"
                or record["State"].get("Running") is not False
                or _mounts(record) != _mounts(selected[role])
            ):
                raise ServiceReleaseError(
                    "선택 runtime의 미시작/이미지/mount 보존 검사가 실패했습니다"
                )
        new_cs = created["dagster-code-server"]["Id"]
        result["phase"] = "restore_before_start"
        _state_child(
            "restore",
            new_cs,
            runtime["images"]["dagster-code-server"],
            stage,
            runtime["state"]["fingerprint"],
            lease_fd,
        )
        common()
        result["phase"] = "start"
        # start는 이미 만든 컨테이너만 시작한다. up 재호출로 state를 다시 지우지 않는다.
        compose(["start", "--wait", "--wait-timeout", str(wait), *sorted(RUNTIME)])

        def healthy():
            common()
            actual = _selected(candidate._inventory(client), project, RUNTIME)
            if any(
                actual[k]["Id"] != created[k]["Id"] or _mounts(actual[k]) != _mounts(selected[k])
                for k in RUNTIME
            ):
                raise ServiceReleaseError("시작 뒤 선택 CID 또는 mount가 변경되었습니다")
            verified = frozen._verify({**manifests, "transport": candidate_manifest}, client)
            if any(v.get("result") != "healthy" for v in verified.values()):
                raise ServiceReleaseError("선택 core가 healthy current 후보가 아닙니다")
            if (
                _probe(actual["backend"]["Id"], _SCHEMA_PROBE, lease_fd)
                != runtime["schema_revisions"]
            ):
                raise ServiceReleaseError("시작 뒤 schema가 변경되었습니다")
            actual_state = _fingerprint(new_cs, lease_fd)
            if state.semantic(actual_state) != state.semantic(runtime["state"]["fingerprint"]):
                raise ServiceReleaseError("시작 뒤 state 의미가 보존되지 않았습니다")
            _quiet(runtime, new_cs, candidate._inventory(client), config, lease_fd)
            _images(client, normal, runtime, sha, tree, url)

        result["phase"] = "postproof"
        healthy()
        current = candidate._publish(candidate_manifest, manifests, root, healthy)
        result.update(
            success=True,
            result="healthy",
            phase="published",
            current=current,
            nonselected_unchanged=True,
            state_preserved=True,
        )
    except Exception as exc:
        result.update(
            error_type=type(exc).__name__,
            message=frozen._detail(str(exc), secrets)
            if isinstance(exc, ServiceReleaseError)
            else "core 후보 단계가 실패했습니다. current는 발행하지 않았습니다",
        )
    return result
