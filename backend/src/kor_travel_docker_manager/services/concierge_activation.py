"""canonical single-file Compose에서 Concierge runtime만 검증·재생성한다.

운영 rollback(직전 이미지 재활성화)과 배포 뒤 재생성의 공식 경로다. Manager root `.env`의
Concierge 이미지 변수가 가리키는 이미 빌드된 이미지로 API·MCP·Dagster code server·UI 네
서비스만 ``--no-deps --force-recreate --no-build`` 한다. 공용 PostgreSQL·공용 Dagster plane은
건드리지 않는다.

2026-08-26의 1회성 legacy override 이관(stage/retire)은 2026-11-02에 제거했다.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    DeploymentContractError,
    c6c_deployment_lock,
    manager_mutation_lock_path,
    validate_concierge_ui_canonical_compose_boundary,
)
from kor_travel_docker_manager.services.compose_service import get_project_root
from kor_travel_docker_manager.services.trusted_install import TRUSTED_INSTALL_ROOT

# GM-09: 경로 상수의 정본은 services/trusted_install.py다.
_TRUSTED_PRODUCTION_PROJECT_ROOT = TRUSTED_INSTALL_ROOT
_FORBIDDEN_COMPOSE_AMBIENT_ENV_NAMES = frozenset(
    {
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_PATH_SEPARATOR",
        "KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE",
        "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT",
    }
)


class ConciergeActivationError(RuntimeError):
    """canonical Concierge 재생성의 사전 조건·검증·실행 실패."""


@dataclass(frozen=True)
class ProjectContext:
    """trusted canonical Compose 실행 경계."""

    root: Path
    env_path: Path
    compose_path: Path


@dataclass(frozen=True)
class ComposeConfigResult:
    """비밀을 출력하지 않고 Compose resolution만 전달하는 결과."""

    returncode: int
    stdout: str


ComposeConfigRunner = Callable[[list[str], Path, Mapping[str, str]], ComposeConfigResult]
ComposeUpRunner = Callable[[list[str], Path, Mapping[str, str]], int]

# C6c projection의 고정 allowlist다. registry와 집합 동등성은
# test_canonical_activation_matches_default_concierge_runtime이 결박한다.
_CONCIERGE_RECREATE_SERVICES = (
    "kor-travel-concierge-api",
    "kor-travel-concierge-mcp",
    "kor-travel-concierge-dagster-code-server",
    "kor-travel-concierge-ui",
)
_CANONICAL_COMPOSE_PROJECTION_ENTITY_KEYS = (
    "configs",
    "networks",
    "secrets",
    "volumes",
)


def activate_canonical_concierge(
    *,
    project_root: Path | None = None,
    compose_config_runner: ComposeConfigRunner | None = None,
    compose_up_runner: ComposeUpRunner | None = None,
    lock_path: str | None = None,
    require_root: bool = True,
) -> None:
    """Manager 변경 lock G 아래에서 canonical Concierge 네 runtime을 검증·재생성한다.

    ``project_root``·runner·``lock_path``·``require_root`` 인수는 단위 테스트 격리용이다 —
    root 실행에서는 셋 다 고정이고 caller가 바꿀 수 없다.
    """

    context = _prepare_project_context(project_root=project_root, require_root=require_root)
    initial_values = _read_dotenv_values(
        _read_regular_bytes(context.env_path), "Manager root environment"
    )
    selected_lock_path = _select_lock_path(
        initial_values,
        project_root=context.root,
        lock_path=lock_path,
        require_root=require_root,
    )
    with ExitStack() as held:
        # lock 획득 실패만 "lock"으로 부른다 — 안쪽 계약 위반을 lock 경합으로 오인하게 하지 않는다.
        try:
            held.enter_context(c6c_deployment_lock(selected_lock_path))
        except DeploymentContractError as exc:
            raise ConciergeActivationError(
                f"cannot acquire the Manager mutation lock: {exc}"
            ) from exc
        try:
            # lock 확보 뒤 다시 읽어 검증과 재생성이 같은 lease의 `.env`를 쓰게 한다.
            _assert_safe_regular_file(context.env_path, require_root=require_root, exact_mode=0o600)
            validation_environment = _read_dotenv_values(
                _read_regular_bytes(context.env_path), "Manager root environment"
            )
            _activate_canonical_concierge_locked(
                context.root,
                context.compose_path,
                context.env_path,
                validation_environment,
                compose_config_runner or _run_canonical_compose_config,
                compose_up_runner or _run_canonical_concierge_recreate,
            )
        except DeploymentContractError as exc:
            raise ConciergeActivationError(
                f"canonical Concierge contract check failed: {exc}"
            ) from exc


def _prepare_project_context(
    *, project_root: Path | None, require_root: bool
) -> ProjectContext:
    if require_root and os.geteuid() != 0:
        raise ConciergeActivationError("Concierge activation requires root execution")
    if require_root:
        if project_root is not None:
            raise ConciergeActivationError("production canonical project root is fixed")
        # installed shim의 ambient project-root 값은 authority가 아니다. production mutation은
        # root-owned trusted release location 하나에서만 canonical Compose를 읽고 실행한다.
        raw_root = _TRUSTED_PRODUCTION_PROJECT_ROOT
    else:
        raw_root = project_root or Path(get_project_root())
    try:
        # 설치 root는 현재 release를 가리키는 symlink다(ADR-51 D). 따라가서 본다.
        raw_root_metadata = raw_root.stat()
    except OSError as exc:
        raise ConciergeActivationError("required directory cannot be inspected") from exc
    if not stat.S_ISDIR(raw_root_metadata.st_mode):
        raise ConciergeActivationError("required directory has unsafe ownership or mode")
    _assert_safe_directory(raw_root.resolve(), require_root=require_root)
    # 반환하는 root는 resolve하지 않는다 — Compose cwd·`--file`·projection 임시 파일이
    # 모두 이 경로를 쓰므로, 풀린 release 경로가 들어가면 상대 bind가 거기 묶인다.
    root = Path(os.path.abspath(raw_root))
    env_path = root / ".env"
    compose_path = root / "docker-compose.yml"
    _assert_safe_regular_file(env_path, require_root=require_root, exact_mode=0o600)
    _assert_safe_regular_file(compose_path, require_root=require_root, exact_mode=None)
    return ProjectContext(root=root, env_path=env_path, compose_path=compose_path)


def _select_lock_path(
    values: Mapping[str, str],
    *,
    project_root: Path,
    lock_path: str | None,
    require_root: bool,
) -> str:
    if require_root and lock_path is not None:
        raise ConciergeActivationError("production activation lock path is fixed")
    if lock_path is not None:
        return lock_path
    if require_root:
        deployment_environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
        if deployment_environment == "production":
            return manager_mutation_lock_path(values)
        if deployment_environment == "rehearsal":
            required = {
                "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
                "PINVI_ENVIRONMENT": "production",
                "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            }
            if any(values.get(name, "").strip().lower() != expected for name, expected in required.items()):
                raise ConciergeActivationError(
                    "Concierge activation requires the canonical rehearsal/rebuildable environment"
                )
            # pinned rebuild·pin 회전·M05·installer와 같은 host 변경 lock ``G``를 쓴다(ADR-51 C-2).
            # user-home rehearsal lock을 쓰면 다른 root launcher와 직렬화되지 않아 C6c 경계를 우회한다.
            return manager_mutation_lock_path(values)
        raise ConciergeActivationError(
            "Concierge activation requires production or canonical rehearsal/rebuildable environment"
        )
    return str((project_root / ".concierge-activation.lock").resolve())


def _validate_canonical_compose_projection(
    project_root: Path,
    projection_path: Path,
    env_path: Path,
    raw_document: Mapping[str, Any],
    environment: Mapping[str, str],
    runner: ComposeConfigRunner,
) -> None:
    result = runner(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_path),
            "--file",
            str(projection_path),
            "config",
            "--format",
            "json",
        ],
        project_root,
        environment,
    )
    if result.returncode != 0:
        raise ConciergeActivationError("canonical Compose validation failed")
    try:
        resolved_document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConciergeActivationError("canonical Compose resolved output is invalid") from exc
    if not isinstance(resolved_document, Mapping):
        raise ConciergeActivationError("canonical Compose resolved output is invalid")
    try:
        validate_concierge_ui_canonical_compose_boundary(
            raw_document,
            resolved_document,
            environment=environment,
        )
    except ComposeCandidateContractError as exc:
        raise ConciergeActivationError("canonical Compose C6c contract is invalid") from exc


def _activate_canonical_concierge_locked(
    project_root: Path,
    compose_path: Path,
    env_path: Path,
    environment: Mapping[str, str],
    config_runner: ComposeConfigRunner,
    up_runner: ComposeUpRunner,
) -> None:
    with _canonical_concierge_compose_projection(compose_path) as (
        projection_path,
        raw_document,
    ):
        _validate_canonical_compose_projection(
            project_root,
            projection_path,
            env_path,
            raw_document,
            environment,
            config_runner,
        )
        command = [
            "docker",
            "compose",
            "--env-file",
            str(env_path),
            "--file",
            str(projection_path),
            "up",
            "--detach",
            "--no-deps",
            "--force-recreate",
            # 재생성은 이미 빌드된 이미지(root `.env`의 Concierge 이미지 변수)만 쓴다. 이미지가 없을 때
            # Compose가 운영 source 디렉터리를 context로 조용히 빌드하지 않게 한다 — owner-only mode가
            # 이미지에 들어가 비-root code server가 깨진다(build_context_export.py).
            "--no-build",
            *_CONCIERGE_RECREATE_SERVICES,
        ]
        if up_runner(command, project_root, environment) != 0:
            raise ConciergeActivationError("canonical Concierge recreation failed")


@contextmanager
def _canonical_concierge_compose_projection(
    compose_path: Path,
) -> Iterator[tuple[Path, Mapping[str, Any]]]:
    """Concierge 재생성에 필요한 trusted Compose 부분만 일시적으로 고정한다.

    full Manager Compose에는 이번 재생성이 건드리지 않는 Map/PinVi 후보의 explicit
    credential guard가 함께 있다. 그 전체 문서를 해석하면 Concierge 검증이 unrelated
    candidate의 미준비 값에 의존한다. 이 projection은 trusted canonical source에서
    Concierge runtime과 그 transitive ``depends_on`` 서비스만 복사해 config와 recreate의
    입력을 동일하게 제한한다.
    """

    try:
        raw_document = yaml.safe_load(_read_regular_bytes(compose_path).decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ConciergeActivationError("canonical Compose cannot be parsed") from exc
    if not isinstance(raw_document, Mapping):
        raise ConciergeActivationError("canonical Compose is invalid")
    projection = _concierge_compose_projection_document(raw_document)
    try:
        payload = yaml.safe_dump(projection, sort_keys=False).encode("utf-8")
    except yaml.YAMLError as exc:
        raise ConciergeActivationError("canonical Compose projection cannot be serialized") from exc

    # GM-10: services/secure_state_file.py에 이 패턴의 정본이 있다. 이 자리는
    # 개별 소유권 정책 검토 없이 옮기지 않기로 결정돼 아직 남아 있다(docs/tasks.md).
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".concierge-c6c-",
            suffix=".yml",
            dir=compose_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        yield temporary_path, projection
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError as exc:
                raise ConciergeActivationError(
                    "canonical Compose projection cleanup failed"
                ) from exc


def _concierge_compose_projection_document(
    raw_document: Mapping[str, Any],
) -> dict[str, Any]:
    services = raw_document.get("services")
    if not isinstance(services, Mapping):
        raise ConciergeActivationError("canonical Compose has no valid services mapping")

    selected_names: set[str] = set()
    pending_names = list(_CONCIERGE_RECREATE_SERVICES)
    while pending_names:
        service_name = pending_names.pop()
        if service_name in selected_names:
            continue
        service = services.get(service_name)
        if not isinstance(service, Mapping):
            raise ConciergeActivationError(
                "canonical Concierge projection has an invalid service dependency"
            )
        selected_names.add(service_name)
        dependencies = service.get("depends_on", {})
        dependency_names: Iterable[object]
        if isinstance(dependencies, Mapping):
            dependency_names = dependencies.keys()
        elif isinstance(dependencies, list):
            dependency_names = dependencies
        elif dependencies is None:
            dependency_names = ()
        else:
            raise ConciergeActivationError(
                "canonical Concierge projection has an invalid service dependency"
            )
        for dependency_name in dependency_names:
            if not isinstance(dependency_name, str):
                raise ConciergeActivationError(
                    "canonical Concierge projection has an invalid service dependency"
                )
            pending_names.append(dependency_name)

    selected_services = {
        service_name: service
        for service_name, service in services.items()
        if service_name in selected_names
    }
    projection: dict[str, Any] = {"services": selected_services}
    name = raw_document.get("name")
    if isinstance(name, str):
        projection["name"] = name
    for entity_key in _CANONICAL_COMPOSE_PROJECTION_ENTITY_KEYS:
        definitions = raw_document.get(entity_key)
        if definitions is None:
            continue
        if not isinstance(definitions, Mapping):
            raise ConciergeActivationError(
                "canonical Concierge projection has invalid top-level definitions"
            )
        references = _compose_entity_references(selected_services, entity_key)
        selected_definitions = {
            name: definition for name, definition in definitions.items() if name in references
        }
        if selected_definitions:
            projection[entity_key] = selected_definitions
    return projection


def _compose_entity_references(
    services: Mapping[Any, Any], entity_key: str
) -> set[str]:
    references: set[str] = set()
    for service in services.values():
        if not isinstance(service, Mapping):  # pragma: no cover - caller has already verified this.
            raise ConciergeActivationError(
                "canonical Concierge projection has an invalid service dependency"
            )
        declarations = service.get(entity_key)
        if declarations is None:
            continue
        if isinstance(declarations, Mapping):
            if entity_key == "networks":
                references.update(name for name in declarations if isinstance(name, str))
                continue
            declaration_items: Iterable[object] = declarations.values()
        elif isinstance(declarations, list):
            declaration_items = declarations
        else:
            raise ConciergeActivationError(
                "canonical Concierge projection has an invalid top-level reference"
            )
        for declaration in declaration_items:
            source = _compose_entity_reference_name(declaration, entity_key)
            if source is not None:
                references.add(source)
    return references


def _compose_entity_reference_name(declaration: object, entity_key: str) -> str | None:
    if isinstance(declaration, Mapping):
        source = declaration.get("source")
        if source is None:
            return None
        if isinstance(source, str):
            return source
        raise ConciergeActivationError(
            "canonical Concierge projection has an invalid top-level reference"
        )
    if not isinstance(declaration, str):
        raise ConciergeActivationError(
            "canonical Concierge projection has an invalid top-level reference"
        )
    if entity_key != "volumes":
        return declaration
    source = declaration.split(":", 1)[0]
    if not source or source.startswith(("/", ".")):
        return None
    return source


def _read_dotenv_values(payload: bytes, label: str) -> dict[str, str]:
    try:
        values = dotenv_values(stream=StringIO(payload.decode("utf-8")), interpolate=False)
    except (UnicodeError, ValueError) as exc:
        raise ConciergeActivationError(f"{label} cannot be parsed") from exc
    return {key: value or "" for key, value in values.items() if isinstance(key, str)}


def _run_canonical_compose_config(
    command: list[str], project_root: Path, values: Mapping[str, str]
) -> ComposeConfigResult:
    environment = _canonical_compose_environment(values)
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return ComposeConfigResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
    )


def _run_canonical_concierge_recreate(
    command: list[str], project_root: Path, values: Mapping[str, str]
) -> int:
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=_canonical_compose_environment(values),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


def _canonical_compose_environment(values: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    for name in _FORBIDDEN_COMPOSE_AMBIENT_ENV_NAMES:
        environment.pop(name, None)
    environment.update(values)
    for name in _FORBIDDEN_COMPOSE_AMBIENT_ENV_NAMES:
        environment.pop(name, None)
    return environment


def _assert_safe_directory(
    path: Path, *, require_root: bool, exact_mode: int = 0o755
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConciergeActivationError("required directory cannot be inspected") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != exact_mode
        or (require_root and metadata.st_uid != 0)
    ):
        raise ConciergeActivationError("required directory has unsafe ownership or mode")


def _assert_safe_regular_file(
    path: Path,
    *,
    require_root: bool,
    exact_mode: int | None,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConciergeActivationError("required activation input cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (require_root and metadata.st_uid != 0)
        or (exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ConciergeActivationError("required activation input has unsafe ownership or mode")


def _read_regular_bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ConciergeActivationError("required activation input cannot be read") from exc
