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
from types import SimpleNamespace
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
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    component_ui_candidate_for_target,
    load_targets_config,
)
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


def _merged_source(source: Path, sha: str, service: str, *, canonical_url: str | None = None) -> None:
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
    expected_url = CANONICAL_RUNTIME_SOURCE_URLS.get(service) if canonical_url is None else canonical_url
    if git("remote", "get-url", "origin") != expected_url:
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
        mounts = record.get("Mounts")
        if isinstance(mounts, list):
            # Docker의 Mounts 열거 순서만 제외한다. 전체 객체·중복·내부 배열은 보존한다.
            mounts = sorted(mounts, key=lambda item: json.dumps(item, sort_keys=True))
        result[record["Id"]] = [record["Image"], state.get("StartedAt"), state.get("Running"),
                                record.get("RestartCount"), record.get("Config"), ("Mounts" in record, mounts),
                                record.get("HostConfig"),
                                (record.get("NetworkSettings") or {}).get("Networks"), state.get("Pid")]
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



def _paired_companions(status: Any, resolved: dict[str, Any], client: Any) -> None:
    topology = runtime_topology()
    image_ids = {slot: status.images[topology.service(slot) or topology.families[slot_project(slot)].carrier]
                 for slot in RUNTIME_SLOTS}
    services = {name: deepcopy(value) for name, value in resolved["services"].items()
                if name not in topology.retired_services}
    # paired slot의 frozen reference 관계를 보존한다. live drift로 기대 역할을 지우지 않는다.
    references = {}
    for slot in RUNTIME_SLOTS:
        name = topology.service(slot)
        if name is not None:
            reference = services[name]["image"]
            if reference in references and references[reference] != image_ids[slot]:
                raise ServiceReleaseError("paired frozen image reference가 모호합니다")
            references[reference] = image_ids[slot]
    for definition in services.values():
        reference = definition.get("image")
        if not isinstance(reference, str):
            continue
        if reference in references:
            definition["image"] = references[reference]
        elif not re.fullmatch(r"sha256:[0-9a-f]{64}", reference):
            try:
                definition["image"] = release._image_id(client.images.get(reference).attrs["Id"])
            except docker.errors.ImageNotFound:
                # 현지에 없는 다른 앱 reference를 pull하거나 live 값으로 추정하지 않는다.
                continue
    companions = generation_companion_services({"services": services}, image_ids,
                                               excluded_services=_PINNED_RUNTIME_ONESHOT_WRITERS,
                                               topology=topology)
    if any(status.images.get(name) != image_ids[owner] for name, owner in companions.items()):
        raise ServiceReleaseError("committed 기준선의 paired companion 이미지가 누락되거나 owner와 다릅니다")
    for name in set(topology.runtime_services) | companions.keys():
        resolved["services"][name]["image"] = status.images[name]


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



def _candidate_plan(service: str, group: str, *, root: Path,
                    config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pinvi 최초 등록 전 Map 후보는 원래 pair 검사를 유지한 채 관측한다."""
    planning = config
    if service == "map" and "pinvi" in config.get("service_releases", {}):
        current = release._root(root) / "current"
        release._directory(current)
        try:
            (current / "pinvi").lstat()
        except FileNotFoundError as missing:
            declaration = entry._declaration("pinvi", config)
            try:
                runtime, url = component_ui_candidate_for_target("pinvi", config)
            except TargetsConfigError as exc:
                raise ServiceReleaseError(str(exc)) from exc
            ui = declaration["groups"].get("ui", {})
            if (set(declaration["groups"]) != {"ui"}
                    or ui.get("runtime_services") != [runtime] or ui.get("oneshot_services")
                    or runtime != runtime_topology().service("pinvi_web")
                    or url != CANONICAL_RUNTIME_SOURCE_URLS["pinvi"]):
                raise ServiceReleaseError("초기 Pinvi web catalog만 기존 Map pair 관측과 호환됩니다") from missing
            # 실제 Pinvi 이미지가 receipt에서 전진했다면 아래 inventory 검사가 그대로 거절한다.
            planning = deepcopy(config)
            del planning["service_releases"]["pinvi"]
    return entry._compose_plan(service, group, root=root, config=planning)


def _paired_current_hooks(plan: dict[str, Any], manifests: dict[str, Any], client: Any,
                          baseline: tuple[Any, ...]):
    """양 방향에서 검증된 비선택 UI current만 pair 관측 기준에 반영한다."""
    topology = runtime_topology()
    current_images = {}
    for service, slot in [("map", "map_ui"), ("pinvi", "pinvi_web")]:
        manifest = manifests.get(service)
        if manifest is None:
            continue
        runtime = topology.service(slot)
        if (set(manifest["images"]) != {runtime}
                or release.verify_release_manifest(manifest, client=client)["result"] != "healthy"):
            raise ServiceReleaseError("검증된 단일 UI component current가 필요합니다")
        image = manifest["images"][runtime]
        if release._metadata(client, image["id"]) != image:
            raise ServiceReleaseError("component current source metadata와 immutable 이미지가 다릅니다")
        current_images[runtime] = image["id"]
    runtime = plan["runtime_services"][0]
    # 선택 UI의 최초 receipt 일치 검사는 덮어쓰지 않는다.
    effective = SimpleNamespace(images={**baseline[3].images,
                                       **{name: image for name, image in current_images.items()
                                          if name != runtime}})

    def inventory(records):
        _receipt_images(effective, records, plan["project"], runtime, manifests[plan["service"]])

    def bind(materialized):
        _paired_companions(baseline[3], materialized, client)
        for name, image in current_images.items():
            materialized["services"][name]["image"] = image

    return inventory, bind


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
    if service == "pinvi":
        try:
            registered, source_url = component_ui_candidate_for_target(service, config)
        except TargetsConfigError as exc:
            raise ServiceReleaseError(str(exc)) from exc
        if (registered != runtime_topology().service("pinvi_web")
                or source_url != CANONICAL_RUNTIME_SOURCE_URLS["pinvi"]):
            raise ServiceReleaseError("Pinvi 후보는 등록된 실제 web 역할과 canonical source만 허용합니다")
        ui_roles = {registered}
    if (group != "ui" or len(runtime) != 1 or runtime[0] not in ui_roles
            or selected.get("oneshot_services")):
        raise ServiceReleaseError("명시한 UI-only catalog 그룹만 후보 적용할 수 있습니다")
    runtime = runtime[0]
    _merged_source(source_clone, source_sha, service)
    with manager_mutation_lock():
        _merged_source(source_clone, source_sha, service)
        plan, manifests = _candidate_plan(service, group, root=root, config=config)
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
    if plan["service"] == "pinvi":
        from kor_travel_docker_manager.services.pinvi_web_candidate import apply_current

        return apply_current(plan, manifests, image_id, source_sha, root, wait, execute, client, baseline)
    # pair pin/receipt와 companion 검사는 보존하고 검증된 UI current만 양방향으로 반영한다.
    service, group = plan["service"], plan["group"]
    inventory, bind = _paired_current_hooks(plan, manifests, client, baseline)
    return _apply_current(
        plan, manifests, image_id, source_sha, root, wait, execute, client,
        canonical_url=CANONICAL_RUNTIME_SOURCE_URLS.get(service),
        extra_conditions=[("baseline_unchanged", lambda: _unchanged_baseline(
            baseline, manifests[service]["groups"][group]))],
        inventory_check=inventory, bind_images=bind,
    )


def _apply_current(plan: dict[str, Any], manifests: dict[str, Any], image_id: str, source_sha: str,
                   root: Path, wait: int, execute: bool, client: Any, *,
                   canonical_url: str | None,
                   extra_conditions: list[tuple[str, Callable[[], None]]],
                   inventory_check: Callable[[list[dict[str, Any]]], None] | None = None,
                   bind_images: Callable[[dict[str, Any]], None] | None = None,
                   extra_secrets: list[tuple[str, str | None]] | None = None) -> dict[str, Any]:
    """기존 current를 기준으로 단일 UI만 적용한다. 승인·선택은 각 공개 진입점이 제한한다."""
    service, group = plan["service"], plan["group"]
    runtime = plan["runtime_services"][0]
    project, working_dir = plan["project"], manifests[service]["groups"][group]["working_dir"]
    metadata = release._metadata(client, image_id)
    if metadata["source_sha"] != source_sha:
        raise ServiceReleaseError("UI 후보 OCI revision과 승인 source SHA가 다릅니다")
    labels = (client.images.get(image_id).attrs.get("Config") or {}).get("Labels") or {}
    source_url = labels.get("org.opencontainers.image.source")
    if source_url != canonical_url:
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
    if inventory_check is not None:
        inventory_check(records)
    try:
        secrets = [*frozen._secrets(manifests), *(extra_secrets or [])]
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
        if bind_images is not None:
            bind_images(materialized)
        materialized["services"][runtime]["image"] = image_id
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, ServiceReleaseError):
            raise
        raise ServiceReleaseError("UI 후보 Compose 구성을 고정할 수 없습니다") from exc
    candidate = deepcopy(manifests[service])
    candidate["images"][runtime] = metadata
    conditions = [
        *extra_conditions,
        ("current_unchanged", lambda: _unchanged_pointers(manifests, root)),
        ("nonselected_unchanged", lambda: _unchanged_others(client, others, project, runtime)),
    ]
    def admission():
        for key, check in conditions:
            try:
                check()
                result.setdefault(key, True)
            except Exception as exc:
                result[key] = False
                result.setdefault("postcondition_errors", {})[key] = type(exc).__name__
                raise
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
        for key, check in conditions:
            try:
                check()
                result.setdefault(key, True)
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
    checks = [result.get(key) is True for key, _ in conditions]
    observed = result.get("post_verification", {})
    healthy_observation = bool(observed) and all(
        isinstance(value, dict) and value.get("result") == "healthy" for value in observed.values())
    if not all(checks) or not healthy_observation:
        result["stderr"] = "UI 후보 사후 불변식 또는 health 관측이 실패했습니다"
        return result
    def healthy():
        admission()
        expected = {**manifests, service: candidate}
        verified = frozen._verify(expected, client)
        result["post_verification"] = verified
        if any(value.get("result") != "healthy" for value in verified.values()):
            raise ServiceReleaseError("UI 후보가 healthy 또는 immutable ID 조건을 만족하지 않습니다")
    try:
        healthy()
        current = _publish(candidate, manifests, root, healthy)
    except Exception as exc:
        # mutation 이후에는 늦은 guard/발행 오류도 이미 모은 진단과 함께 실패로 반환한다.
        result["error_type"] = type(exc).__name__
        result["stderr"] = (frozen._detail(str(exc), secrets) if isinstance(exc, ServiceReleaseError)
                            else "UI 후보 사후 검증 또는 발행을 마치지 못했습니다")
        observe_after()
        return result
    result.update(success=True, result="healthy", current=current, current_unchanged=False, nonselected_unchanged=True)
    return result
