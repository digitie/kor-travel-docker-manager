"""Geo의 정상 API/CS 두 이미지만 같은 G·lease에서 적용한다.

UI·공용 DB·Map·schema는 변경하지 않는다. instigator capture/pause/drain/복원은
외부 operator가 소유하며 이 진입점은 실제 quiet와 승인한 source/native tuple을 검증한다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import docker

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services import service_release_compose as frozen
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock
from kor_travel_docker_manager.services.compose_service import (
    _compose_wait_timeout_seconds,
    _escape_materialized_compose_environment_values,
    get_env_path,
)
from kor_travel_docker_manager.services.errors import ManagerMutationActiveError
from kor_travel_docker_manager.services.registry import (
    geo_core_candidate_for_target,
    load_targets_config,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

URL = "https://github.com/digitie/kor-travel-geo.git"
ROLES = {"api": "kor-travel-geo-api", "code-server": "geo-dagster-code-server"}
RUNTIME = set(ROLES.values())
UI = "kor-travel-geo-ui"
LOCATION = "kortravelgeo_dagster.definitions"
_SCHEMA_PROBE = """
import asyncio,json,os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def main():
 url=os.environ["KTG_PG_DSN"]
 if url.startswith("postgresql://"):url="postgresql+psycopg://"+url[len("postgresql://"):]
 e=create_async_engine(url)
 try:
  async with e.connect() as c:
   await c.execute(text("SET TRANSACTION READ ONLY"))
   await c.execute(text("SET LOCAL statement_timeout='5s'"))
   print(json.dumps(sorted((await c.execute(text("SELECT version_num FROM public.alembic_version"))).scalars().all())))
 finally:await e.dispose()
asyncio.run(main())
"""


def _source(source: Path, sha: str):
    tree, heads = core._source(source, sha, URL, service="geo", migration_path="alembic/versions")
    project = tomllib.loads(core._git(source, "show", sha + ":pyproject.toml"))["project"]
    pins = [re.fullmatch(r"psycopg\[([a-z,]+)\]==([0-9]+\.[0-9]+\.[0-9]+)", value)
            for value in project["dependencies"] if value.startswith("psycopg[")]
    if len(heads) != 1 or len(pins) != 1 or not pins[0] or "binary" not in pins[0][1].split(","):
        raise ServiceReleaseError("Geo의 단일 schema와 정확한 psycopg binary 선언이 필요합니다")
    return tree, heads, pins[0][2]


def _receipts(normal, runtime, sha, tree, heads, version):
    try:
        if (not isinstance(normal["version"], int) or isinstance(normal["version"], bool) or normal["version"] != 1
                or normal["kind"] != "geo-normal-build" or normal["normalDockerBuild"] is not True
                or normal["mode"] != "NORMAL_BUILD_ONLY" or normal["actualApplyAllowed"] is not False
                or normal["deployReady"] is not False or normal["source"] != URL
                or normal["release_sha"] != sha or normal["tree"] != tree
                or normal["output"] != "docker" or normal["architecture"] != "arm64"
                or set(normal["images"]) != set(ROLES) or set(normal["artifacts"]) != set(ROLES)):
            raise ValueError
        native = runtime["native"]
        if (not isinstance(runtime["version"], int) or isinstance(runtime["version"], bool) or runtime["version"] != 1
                or runtime["release_sha"] != sha or runtime["images"] != normal["images"]
                or runtime["schema_revisions"] != heads
                or native["status"] != "PASS" or native["release_sha"] != sha or native["tree"] != tree
                or native["images"] != normal["images"] or set(native["drivers"]) != set(ROLES)
                or not re.fullmatch(r"[0-9a-f]{64}", runtime["source_container"])):
            raise ValueError
        for role, image in normal["images"].items():
            release._image_id(image)
            artifact, driver = normal["artifacts"][role], native["drivers"][role]
            if (artifact["imageID"] != image or artifact["loaded"] is not True
                    or artifact["normalDockerBuild"] is not True or artifact["architecture"] != "arm64"
                    or not re.fullmatch(r"[0-9a-f]{64}", artifact["COPY"]["sha256"])
                    or driver["psycopg"] != version or driver["psycopg-binary"] != version
                    or driver["implementation"] not in {"binary", "c"}):
                raise ValueError
        core._instigators(runtime["instigators"], location=LOCATION)
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceReleaseError("Geo normal/runtime/native tuple이 다릅니다") from exc


def _images(client, normal, sha, tree):
    metadata = {}
    for role, name in ROLES.items():
        image = client.images.get(normal["images"][role]).attrs
        labels = (image.get("Config") or {}).get("Labels") or {}
        expected = {"org.opencontainers.image.revision": sha, "org.opencontainers.image.source": URL,
                    "io.kor-travel-geo.source-tree": tree, "io.kor-travel-geo.build-role": role,
                    "io.kor-travel-geo.copy-sha256": normal["artifacts"][role]["COPY"]["sha256"]}
        if (image.get("Id") != normal["images"][role] or image.get("Architecture") != "arm64"
                or image.get("Os") != "linux" or any(labels.get(k) != v for k, v in expected.items())):
            raise ServiceReleaseError("Geo 이미지 source/native/build 출처가 다릅니다")
        metadata[name] = release._metadata(client, image["Id"])
    return metadata


def apply_geo_core_candidate(service: str, *, source_clone: Path, source_sha: str,
                             receipt_path: Path, receipt_sha256: str, build_receipt_path: Path,
                             build_receipt_sha256: str, root: Path, config: dict | None = None,
                             execute: bool = False, client_factory: Any = None) -> dict:
    """Geo만 허용한다. G 경합의 전용 타입/code와 원래 원인을 보존한다."""
    try:
        if (service != "geo" or not isinstance(execute, bool) or not isinstance(source_sha, str)
                or not re.fullmatch(r"[0-9a-f]{40}", source_sha)):
            raise ServiceReleaseError("Geo API/CS와 승인 merged SHA만 허용합니다")
        if os.geteuid() != 0:
            raise ServiceReleaseError("Geo core는 root로 실행해야 합니다")
        release._root(root)
        config = load_targets_config() if config is None else config
        group, lease = geo_core_candidate_for_target(service, config)
        with manager_mutation_lock(), state.project_lease(lease) as fd:
            source_clone = Path(source_clone)
            tree, heads, version = _source(source_clone, source_sha)
            normal = core._receipt(Path(build_receipt_path), build_receipt_sha256)
            runtime = core._receipt(Path(receipt_path), receipt_sha256)
            _receipts(normal, runtime, source_sha, tree, heads, version)
            plan, manifests = entry._compose_plan(service, group, root=root, config=config)
            client = (client_factory or (lambda: docker.from_env(timeout=10)))()
            try:
                return _apply(plan, manifests, normal, runtime, source_sha, tree, heads, source_clone,
                              receipt_path, receipt_sha256, build_receipt_path, build_receipt_sha256,
                              version, root, config, fd, lease, client, execute)
            finally:
                client.close()
    except (ServiceReleaseError, ManagerMutationActiveError):
        raise
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, subprocess.TimeoutExpired,
            docker.errors.DockerException) as exc:
        raise ServiceReleaseError("Geo source/receipt/lease 입력을 안전하게 검증할 수 없습니다") from exc


def _apply(plan, manifests, normal, runtime, sha, tree, heads, source, receipt_path, receipt_hash,
           build_path, build_hash, version, root, config, fd, lease, client, execute):
    own = manifests["geo"]
    group = own["groups"]["main"]
    project = group["project"]
    if set(group["runtime_services"]) != RUNTIME | {UI} or group["oneshot_images"]:
        raise ServiceReleaseError("Geo main의 기존 API/CS/UI 역할이 다릅니다")
    records = candidate._inventory(client)
    # quiet 거부 뒤에는 이미지 선택·Compose mutation으로 진행하지 않는다.
    core._quiet(runtime, runtime["source_container"], records, config, fd, location=LOCATION)
    selected = core._selected(records, project, RUNTIME)
    cs = selected[ROLES["code-server"]]
    if cs["Id"] != runtime["source_container"]:
        raise ServiceReleaseError("Geo 승인 source CS와 현재 CID가 다릅니다")
    if any(v.get("result") != "healthy" for v in frozen._verify(manifests, client).values()):
        raise ServiceReleaseError("기존 current가 healthy여야 합니다")
    metadata = _images(client, normal, sha, tree)
    def others(rows):
        return candidate._nonselected(
            [r for r in rows if not any(candidate._selected(r, project, n) for n in RUNTIME)], project, "")
    untouched = others(records)
    identities = {n: (r["Id"], r["Image"], r["State"]["StartedAt"], r.get("RestartCount"))
                  for n, r in selected.items()}
    bindings, environments = [], []
    secrets = frozen._secrets(manifests)
    for role, name in ROLES.items():
        bind, check, values = component._preserve_environment(client, project, name, normal["images"][role])
        bindings.append(bind)
        environments.append(check)
        secrets.extend(values)
    rendered = candidate._render([*plan["argv"], "config", "--format", "json"],
                                 cwd=group["working_dir"], env=frozen._environment(),
                                 capture_output=True, text=True, check=False, timeout=30)
    if rendered.returncode:
        raise ServiceReleaseError("Geo 등록 Compose를 해석할 수 없습니다")
    baseline = json.loads(rendered.stdout)
    secrets.extend(frozen._resolved_secrets(baseline))
    for manifest in manifests.values():
        for name, image in manifest["images"].items():
            if client.images.get(baseline["services"][name]["image"]).attrs["Id"] != image["id"]:
                raise ServiceReleaseError("Geo shared-project current 이미지가 다릅니다")
            baseline["services"][name]["image"] = image["id"]
    materialized = deepcopy(baseline)
    for role, name in ROLES.items():
        core._mount_admission(materialized["services"][name], materialized, selected[name],
                              client.images.get(normal["images"][role]).attrs, project, state_path=None)
        materialized["services"][name]["image"] = normal["images"][role]
    for bind in bindings:
        bind(materialized)
    # rollback도 live의 정확한 환경을 보존한다. root/env 파일을 다시 보간하지 않는다.
    for name in RUNTIME:
        baseline["services"][name]["environment"] = {
            k: v.replace("$", "$$") for k, v in component._environment_values(selected[name]["Config"]["Env"]).items()}
        baseline["services"][name].pop("env_file", None)
    protected = {Path(get_env_path()), Path(receipt_path), Path(build_path)}
    for manifest in manifests.values():
        for spec in manifest["groups"].values():
            protected.update(map(Path, spec["config_files"]))
            if spec["env_file"]:
                protected.add(Path(spec["env_file"]))
    for record in selected.values():
        protected.update(Path(m["Source"]) for m in record["Mounts"]
                         if m["Type"] == "bind" and Path(m["Source"]).is_file())
    files = core._protected(protected)
    wait = _compose_wait_timeout_seconds({n: materialized["services"][n] for n in RUNTIME})
    argv = ["docker", "compose", "--project-name", project, "--project-directory", group["working_dir"],
            "--env-file", "/dev/null", "-f", "-"]

    def common():
        state.check_lease(fd, lease)
        if (_source(source, sha) != (tree, heads, version) or core._protected(protected) != files
                or others(candidate._inventory(client)) != untouched):
            raise ServiceReleaseError("Geo 승인 source/보호 파일/비선택 runtime이 변경되었습니다")
        candidate._unchanged_pointers({k: v for k, v in manifests.items() if k != "geo"}, root)
        _receipts(core._receipt(Path(build_path), build_hash),
                  core._receipt(Path(receipt_path), receipt_hash), sha, tree, heads, version)
        _images(client, normal, sha, tree)
        for check in environments:
            check()

    def quiet(cid):
        rows = candidate._inventory(client)
        core._quiet(runtime, cid, rows, config, fd, location=LOCATION)
        if core._probe(cid, _SCHEMA_PROBE, fd) != heads:
            raise ServiceReleaseError("Geo schema는 변경 없이 승인 head와 같아야 합니다")

    def compose(document, names):
        result, signals = frozen._run_mutation(
            [*argv, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
             "--force-recreate", "--wait", "--wait-timeout", str(wait), *names],
            additional_fds=(fd,), cwd=group["working_dir"], env=frozen._environment(),
            text=True, capture_output=True, check=False,
            input=json.dumps(_escape_materialized_compose_environment_values(document), ensure_ascii=False))
        if result.returncode or signals:
            raise ServiceReleaseError("Geo 선택 Compose 단계가 실패하거나 중단되었습니다")

    common()
    candidate._unchanged_pointers(manifests, root)
    current = core._selected(candidate._inventory(client), project, RUNTIME)
    if {n: (r["Id"], r["Image"], r["State"]["StartedAt"], r.get("RestartCount"))
            for n, r in current.items()} != identities:
        raise ServiceReleaseError("Geo 교체 전 singleton identity가 변경되었습니다")
    quiet(cs["Id"])
    result = {"service": "geo", "source_sha": sha, "images": normal["images"], "success": True,
              "result": "planned", "geoInstigatorsMustRemainPaused": True, "schemaMigration": False}
    if not execute:
        return result
    result.update(success=False, result="failed")
    try:
        for name in ROLES.values():
            common()
            candidate._unchanged_pointers(manifests, root)
            quiet(core._selected(candidate._inventory(client), project, RUNTIME)[ROLES["code-server"]]["Id"])
            compose(materialized, [name])
        common()
        live = core._selected(candidate._inventory(client), project, RUNTIME)
        quiet(live[ROLES["code-server"]]["Id"])
        expected = deepcopy(own)
        expected["images"].update(metadata)
        if any(v.get("result") != "healthy" for v in
               frozen._verify({**manifests, "geo": expected}, client).values()):
            raise ServiceReleaseError("Geo 후보의 health/image 검증이 실패했습니다")
        release.adopt_release({"service": "geo", "groups": own["groups"]}, root=root,
                              client=client, replace_current=True)
        common()
        if release.read_current_release("geo", root=root)["images"] != expected["images"]:
            raise ServiceReleaseError("Geo 관측 image와 발행 CURRENT가 다릅니다")
        result.update(success=True, result="healthy", schemaUnchanged=True, nonselectedUnchanged=True)
    except BaseException as exc:
        result.update(errorType=type(exc).__name__, rollbackImagesRestored=False)
        try:
            rows = candidate._inventory(client)
            rollback_names = []
            for name in sorted(RUNTIME):
                matches = [r for r in rows if candidate._selected(r, project, name)]
                if (len(matches) != 1 or matches[0]["Image"] != selected[name]["Image"]
                        or matches[0]["State"].get("Running") is not True
                        or component._environment_values(matches[0]["Config"]["Env"])
                        != component._environment_values(selected[name]["Config"]["Env"])):
                    rollback_names.append(name)
            live_cs = [r for r in rows if candidate._selected(r, project, ROLES["code-server"])]
            if (ROLES["code-server"] in rollback_names and len(live_cs) == 1
                    and live_cs[0]["State"].get("Running") is True):
                try:
                    core._quiet(runtime, live_cs[0]["Id"], rows, config, fd, location=LOCATION)
                except ServiceReleaseError:
                    # 새 worker가 이미 생겼다면 죽이지 않는다. API만 복구하고 외부 drain으로 인계한다.
                    rollback_names.remove(ROLES["code-server"])
                    result["rollbackDeferredForQuiet"] = True
            if rollback_names:
                compose(baseline, rollback_names)
            common()
            if result.get("rollbackDeferredForQuiet"):
                raise ServiceReleaseError("Geo CS 복구는 새 실행의 자연 종료가 필요합니다")
            restored = core._selected(candidate._inventory(client), project, RUNTIME)
            if core._probe(restored[ROLES["code-server"]]["Id"], _SCHEMA_PROBE, fd) != heads:
                raise ServiceReleaseError("Geo 복구 뒤 schema가 승인 head와 다릅니다")
            if any(v.get("result") != "healthy" for v in frozen._verify(manifests, client).values()):
                raise ServiceReleaseError("Geo 원래 이미지 복구를 확인할 수 없습니다")
            release.adopt_release({"service": "geo", "groups": own["groups"]}, root=root,
                                  client=client, replace_current=True)
            result["rollbackImagesRestored"] = True
        except BaseException as rollback:
            result["rollbackErrorType"] = type(rollback).__name__
        if not isinstance(exc, Exception):
            raise
    return result
