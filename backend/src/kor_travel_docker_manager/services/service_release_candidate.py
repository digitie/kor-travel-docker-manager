"""UI 후보만 적용하고 관측 성공 뒤 기존 component current를 발행한다.

paired pin/배포 상태는 읽기 전용 기준선이다. recreate/adopt의 의미를 바꾸지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import docker
import yaml

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import deploy_status
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_compose as frozen
from kor_travel_docker_manager.services.c6c_deployment import manager_mutation_lock
from kor_travel_docker_manager.services.compose_service import (
    _PINNED_RUNTIME_ONESHOT_WRITERS,
    _capture_compose_environment_snapshot,
    _escape_materialized_compose_environment_values,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import pinned_runtime_state_root
from kor_travel_docker_manager.services.pinned_runtime_rebuild import generation_companion_services
from kor_travel_docker_manager.services.pinned_runtime_release import (
    CANONICAL_RUNTIME_SOURCE_URLS,
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import _root_git_environment
from kor_travel_docker_manager.services.registry import load_targets_config
from kor_travel_docker_manager.services.runtime_topology import (
    RUNTIME_SLOTS,
    runtime_topology,
    slot_project,
)
from kor_travel_docker_manager.services.secure_state_file import (
    atomic_write_bytes,
    atomic_write_json,
    fsync_directory,
)
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


def _merged_source(source: Path, sha: str, service: str) -> None:
    source = Path(source)
    try:
        release._trusted_ancestors(source)
        release._directory(source / ".git")
    except OSError as exc:
        raise ServiceReleaseError("승인 source clone을 안전하게 읽을 수 없습니다") from exc
    for directory, directories, files in os.walk(source / ".git", followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            info = path.lstat()
            if (info.st_uid != release._OWNER_UID or info.st_mode & 0o022
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                raise ServiceReleaseError("승인 Git clone 내부 소유권·권한이 안전하지 않습니다")
    if (source / ".git/commondir").exists():
        raise ServiceReleaseError("승인 Git clone은 외부 common directory를 참조할 수 없습니다")
    if (source / ".git/objects/info/alternates").exists():
        raise ServiceReleaseError("승인 Git clone은 외부 object 저장소를 참조할 수 없습니다")
    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", str(source), *args],
                env=_root_git_environment(), capture_output=True, text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ServiceReleaseError("승인 소스 Git 조회에 실패했습니다") from exc
        if result.returncode:
            raise ServiceReleaseError("승인 SHA가 canonical main에 병합된 소스가 아닙니다")
        return result.stdout.strip()
    if git("remote", "get-url", "origin") != CANONICAL_RUNTIME_SOURCE_URLS.get(service):
        raise ServiceReleaseError("승인 소스 저장소가 canonical URL과 다릅니다")
    if git("rev-parse", "--verify", sha + "^{commit}") != sha:
        raise ServiceReleaseError("승인 소스 SHA가 commit과 다릅니다")
    git("merge-base", "--is-ancestor", sha, "refs/remotes/origin/main")


def _baseline(selected: dict[str, Any]) -> tuple[Path, bytes, dict[str, Any], Any]:
    base = Path(selected["working_dir"])
    snapshot = _capture_compose_environment_snapshot(
        environment_override=None, env_path=Path(selected["env_file"] or base / ".env"),
        compose_path=Path(selected["config_files"][0]), override_path=base / "docker-compose.override.yml",
        include_process_environment=False, interpolate_env_file=False,
    )
    values = dict(snapshot.effective)
    values.setdefault("COMPOSE_PROJECT_NAME", selected["project"])
    if values["COMPOSE_PROJECT_NAME"] != selected["project"]:
        raise ServiceReleaseError("기준선 project와 UI catalog project가 다릅니다")
    path = deploy_status.deploy_status_path(pinned_runtime_state_root(values))
    raw = release._read_file(path)
    status = deploy_status._status_from_payload(json.loads(raw))
    pin = current_pinned_runtime_release()
    if (status.state != "committed" or status.pinset_sha256 != pin.pinset_sha256
            or status.map_revision != pin.source_for("map").revision
            or status.pinvi_revision != pin.source_for("pinvi").revision):
        raise ServiceReleaseError("현재 committed 배포 기준선과 pair pin이 다릅니다")
    if set(runtime_topology().runtime_services) - status.images.keys():
        raise ServiceReleaseError("committed 기록의 필수 paired runtime 이미지가 누락되었습니다")
    return path, raw, {"pin": pin.to_payload(), "environment": values}, status


def _unchanged_baseline(baseline: tuple[Any, ...], selected: dict[str, Any]) -> None:
    if _baseline(selected)[:3] != baseline[:3]:
        raise ServiceReleaseError("관측 중 committed 배포 기준선 또는 pin이 변경되었습니다")


def _unchanged_pointers(manifests: dict[str, Any], root: Path) -> None:
    if any(release.read_current_release(name, root=root) != manifest
           for name, manifest in manifests.items()):
        raise ServiceReleaseError("관측 중 current pointer 또는 metadata가 변경되었습니다")


def _inventory(client: Any) -> list[dict[str, Any]]:
    records = [deepcopy(container.attrs) for container in client.containers.list(all=True)]
    ids = [record.get("Id") for record in records]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ServiceReleaseError("전체 컨테이너 identity를 완전하게 관측할 수 없습니다")
    return records


def _selected(record: dict[str, Any], project: str, runtime: str) -> bool:
    labels = (record.get("Config") or {}).get("Labels") or {}
    return (labels.get("com.docker.compose.project") == project
            and labels.get("com.docker.compose.service") == runtime)


def _nonselected(records: list[dict[str, Any]], project: str, runtime: str) -> dict[str, Any]:
    result = {}
    for record in records:
        if _selected(record, project, runtime):
            continue
        state = record.get("State") or {}
        if not isinstance(record.get("Image"), str) or not isinstance(state.get("StartedAt"), str):
            raise ServiceReleaseError("비선택 컨테이너 identity 관측이 불완전합니다")
        result[record["Id"]] = [record["Image"], state.get("StartedAt"), state.get("Running"),
                                record.get("RestartCount"), record.get("Config"), record.get("Mounts"),
                                record.get("HostConfig"),
                                (record.get("NetworkSettings") or {}).get("Networks")]
    return result


def _unchanged_others(client: Any, expected: dict[str, Any], project: str, runtime: str) -> None:
    if _nonselected(_inventory(client), project, runtime) != expected:
        raise ServiceReleaseError("비선택 API/DB/Dagster 컨테이너 identity 또는 설정이 변경되었습니다")


def _receipt_images(status: Any, records: list[dict[str, Any]], project: str,
                    runtime: str, own: dict[str, Any]) -> None:
    if set(runtime_topology().runtime_services) - status.images.keys():
        raise ServiceReleaseError("committed 기준선의 필수 paired runtime 이미지가 누락되었습니다")
    for name, image_id in status.images.items():
        if name == runtime:
            # 첫 component 등록은 정상 paired 기준선의 현재 UI에서 시작한다.
            if own["previous"] is None and own["images"][name]["id"] != image_id:
                raise ServiceReleaseError("최초 component current와 기준선 UI 이미지가 다릅니다")
            continue
        matches = [record for record in records if _selected(record, project, name)]
        if len(matches) != 1 or matches[0].get("Image") != image_id:
            raise ServiceReleaseError("committed 비선택 이미지와 실행 이미지가 다릅니다")



def _paired_companions(status: Any, resolved: dict[str, Any], records: list[dict[str, Any]], project: str) -> None:
    topology = runtime_topology()
    identities = {((record.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.service"): record["Image"]
                  for record in records
                  if ((record.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project") == project}
    services = {name: deepcopy(value) for name, value in resolved["services"].items()
                if name not in topology.retired_services}
    for name, definition in services.items():
        if name in identities:
            definition["image"] = identities[name]
    image_ids = {slot: status.images[topology.service(slot) or topology.families[slot_project(slot)].carrier]
                 for slot in RUNTIME_SLOTS}
    companions = generation_companion_services({"services": services}, image_ids,
                                               excluded_services=_PINNED_RUNTIME_ONESHOT_WRITERS,
                                               topology=topology)
    if set(companions) - status.images.keys():
        raise ServiceReleaseError("committed 기준선에 필수 paired companion 이미지가 누락되었습니다")


def _render(argv: list[str], **kwargs: Any) -> Any:
    return subprocess.run(argv, **kwargs)


def _publish(candidate: dict[str, Any], manifests: dict[str, Any], root: Path,
             guard: Callable[[], None]) -> dict[str, Any]:
    root = release._root(root)
    own = manifests[candidate["service"]]
    manifest = {key: value for key, value in candidate.items() if key != "release_directory"}
    now = datetime.now(UTC).isoformat()
    manifest.update(adopted_at=now, previous=own["release_directory"],
                    source_sha=release._common_sha(manifest["images"]),
                    health={"checked_at": now, "result": "healthy"})
    release._manifest(manifest, candidate["service"], root)
    guard()
    leaf = root / f"{candidate['service']}-candidate-{uuid4().hex}"
    leaf.mkdir(mode=0o711)
    os.chmod(leaf, 0o711)
    overlay = {"services": {name: {"image": image["id"]} for name, image in manifest["images"].items()}}
    atomic_write_bytes(leaf / "images.yml", yaml.safe_dump(overlay, sort_keys=True).encode(), mode=0o644)
    atomic_write_json(leaf / "manifest.json", manifest, mode=0o644)
    temporary = root / "current" / f".{candidate['service']}-{uuid4().hex}.tmp"
    try:
        guard()
        temporary.symlink_to(leaf)
        os.replace(temporary, root / "current" / candidate["service"])
        fsync_directory(root / "current")
    finally:
        temporary.unlink(missing_ok=True)
    return {**manifest, "release_directory": str(leaf)}


def apply_ui_candidate(service: str, *, group: str, image_id: str, source_sha: str,
                       source_clone: Path, root: Path, config: dict[str, Any] | None = None,
                       wait_timeout: int = 120, execute: bool = False,
                       client_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """승인된 merged UI image를 dry-run하거나 적용한다. Docker 생성 전 root/G를 확인한다.

    source clone은 root 소유 canonical origin과 origin/main 병합 관계를 갖춰야 한다.
    pair pin/receipt는 읽기만 하며 component manifest만 health 관측 뒤 발행한다.
    """
    release._image_id(image_id)
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
            or type(wait_timeout) is not int or not 1 <= wait_timeout <= 600
            or type(execute) is not bool):
        raise ServiceReleaseError("승인 source SHA 또는 대기 시간이 올바르지 않습니다")
    if os.geteuid() != 0:
        raise ServiceReleaseError("UI 후보 적용은 root로 실행해야 합니다")
    config = load_targets_config() if config is None else config
    declaration = entry._declaration(service, config)
    selected = declaration["groups"].get(group, {})
    runtime = selected.get("runtime_services", [])
    ui_roles = {item.get("compose_service") for item in config.get("containers", {}).values()
                if item.get("role") == service + "-ui"}
    if (group != "ui" or len(runtime) != 1 or runtime[0] not in ui_roles
            or selected.get("oneshot_services")):
        raise ServiceReleaseError("명시한 UI-only catalog 그룹만 후보 적용할 수 있습니다")
    runtime = runtime[0]
    _merged_source(source_clone, source_sha, service)
    with manager_mutation_lock():
        _merged_source(source_clone, source_sha, service)
        plan, manifests = entry._compose_plan(service, group, root=root, config=config)
        baseline = _baseline(manifests[service]["groups"][group])
        factory = client_factory or (lambda: docker.from_env(timeout=10))
        client = factory()
        try:
            return _apply(plan, manifests, image_id, source_sha, root, wait_timeout,
                          execute, client, baseline)
        finally:
            client.close()


def _apply(plan: dict[str, Any], manifests: dict[str, Any], image_id: str, source_sha: str,
           root: Path, wait: int, execute: bool, client: Any, baseline: tuple[Any, ...]) -> dict[str, Any]:
    service, group = plan["service"], plan["group"]
    runtime = plan["runtime_services"][0]
    project, working_dir = plan["project"], manifests[service]["groups"][group]["working_dir"]
    metadata = release._metadata(client, image_id)
    if metadata["source_sha"] != source_sha:
        raise ServiceReleaseError("UI 후보 OCI revision과 승인 source SHA가 다릅니다")
    labels = (client.images.get(image_id).attrs.get("Config") or {}).get("Labels") or {}
    source_url = labels.get("org.opencontainers.image.source")
    if source_url != CANONICAL_RUNTIME_SOURCE_URLS.get(service):
        raise ServiceReleaseError("UI 후보 OCI source가 canonical 저장소와 다릅니다")
    if any(result.get("result") != "healthy" for result in frozen._verify(manifests, client).values()):
        raise ServiceReleaseError("현재 component release가 healthy여야 합니다")
    records = _inventory(client)
    others = _nonselected(records, project, runtime)
    def ui_identity(items: list[dict[str, Any]]) -> list[Any]:
        return [(item["Id"], item.get("Image"), item.get("State", {}).get("StartedAt"),
                 item.get("RestartCount")) for item in items if _selected(item, project, runtime)]
    initial_ui = ui_identity(records)
    if len(initial_ui) != 1:
        raise ServiceReleaseError("현재 UI 컨테이너는 정확히 하나여야 합니다")
    _receipt_images(baseline[3], records, project, runtime, manifests[service])
    try:
        secrets = frozen._secrets(manifests)
    except (OSError, UnicodeError) as exc:
        raise ServiceReleaseError("UI 환경의 비밀 가림 입력을 읽을 수 없습니다") from exc
    result: dict[str, Any] = {"success": False, "result": "failed", "service": service,
                              "group": group, "image_id": image_id, "source_sha": source_sha}
    environment = frozen._environment()
    try:
        rendered = _render([*plan["argv"], "config", "--format", "json"], cwd=working_dir,
                           env=environment, capture_output=True, text=True, check=False, timeout=30)
        if rendered.returncode:
            result.update(returncode=rendered.returncode, stderr=frozen._detail(rendered.stderr, secrets))
            return result
        materialized = json.loads(rendered.stdout)
        secrets.extend(frozen._resolved_secrets(materialized))
        for manifest in manifests.values():
            for name, image in manifest["images"].items():
                reference = materialized["services"][name]["image"]
                if client.images.get(reference).attrs["Id"] != image["id"]:
                    raise ServiceReleaseError("resolved 이미지와 현재 component release가 다릅니다")
                materialized["services"][name]["image"] = image["id"]
        _paired_companions(baseline[3], materialized, records, project)
        materialized["services"][runtime]["image"] = image_id
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, ServiceReleaseError):
            raise
        raise ServiceReleaseError("UI 후보 Compose 구성을 고정할 수 없습니다") from exc
    candidate = deepcopy(manifests[service])
    candidate["images"][runtime] = metadata
    def admission():
        _unchanged_baseline(baseline, manifests[service]["groups"][group])
        _unchanged_pointers(manifests, root)
        _unchanged_others(client, others, project, runtime)
    admission()
    if ui_identity(_inventory(client)) != initial_ui:
        raise ServiceReleaseError("실행 전에 현재 UI identity가 변경되었습니다")
    if not execute:
        result.update(success=True, result="planned")
        return result
    command = ["docker", "compose", "--project-name", project, "--project-directory", working_dir,
               "--env-file", "/dev/null", "-f", "-", "up", "-d", "--no-deps", "--no-build",
               "--pull", "never", "--force-recreate", "--wait", "--wait-timeout", str(wait), runtime]
    def observe_after():
        try:
            result["post_verification"] = frozen._verify({**manifests, service: candidate}, client)
        except Exception as exc:
            result["post_verification"] = {"observed": False, "error_type": type(exc).__name__}
        for key, check in [
            ("baseline_unchanged", lambda: _unchanged_baseline(baseline, manifests[service]["groups"][group])),
            ("current_unchanged", lambda: _unchanged_pointers(manifests, root)),
            ("nonselected_unchanged", lambda: _unchanged_others(client, others, project, runtime)),
        ]:
            try:
                check()
                result[key] = True
            except Exception as exc:
                result[key] = False
                result.setdefault("postcondition_errors", {})[key] = type(exc).__name__
    try:
        completed, interrupted = frozen._run_mutation(
            command, cwd=working_dir, env=environment, text=True, capture_output=True, check=False,
            input=json.dumps(_escape_materialized_compose_environment_values(materialized), ensure_ascii=False),
        )
    except OSError as exc:
        result.update(error_type=type(exc).__name__, stderr=frozen._detail(str(exc), secrets))
        observe_after()
        return result
    observe_after()
    result["returncode"] = completed.returncode
    if completed.returncode or interrupted:
        result.update(stderr=frozen._detail(completed.stderr, secrets), interrupted_signals=interrupted)
        return result
    def healthy():
        admission()
        expected = {**manifests, service: candidate}
        verified = frozen._verify(expected, client)
        result["post_verification"] = verified
        result.update(baseline_unchanged=True, current_unchanged=True, nonselected_unchanged=True)
        if any(value.get("result") != "healthy" for value in verified.values()):
            raise ServiceReleaseError("UI 후보가 healthy 또는 immutable ID 조건을 만족하지 않습니다")
    try:
        healthy()
    except ServiceReleaseError as exc:
        if "비선택" in str(exc):
            raise
        result.update(stderr=str(exc))
        return result
    current = _publish(candidate, manifests, root, healthy)
    result.update(success=True, result="healthy", current=current, current_unchanged=False, nonselected_unchanged=True)
    return result
