"""C7 prod runner가 읽는 `compatible-pair-v4.json`을 비파괴로 기록한다.

Map 저장소 런북 `docs/runbooks/c7-prod-live-e2e.md` §2.1 step 8이 부르는
`ktdctl pinvi-pair capture --verified-compatible --build`의 구현이다.

이 모듈은 **컨테이너를 절대 건드리지 않는다**. 내보내는 docker argv는 세 종류의
읽기 전용 조회뿐이고(`compose --project-directory ... ps -q`, `inspect --`,
`image inspect --format=... --`), `up`/`stop`/`start`/`rm`/`build`/`restart`는 코드
경로에 존재하지 않는다. 따라서 어떤 실패 경로도 rollback을 수행하지 않는다 —
되돌릴 mutation이 없기 때문이다. 런북 §2.1 step 1의 maintenance fence는 실패
시에도 닫힌 채 유지하며, 어떤 실패 메시지도 fence 해제를 제안하지 않는다.

산출물의 소비자는 사람이 아니라 Map 저장소의 C7 runner
(`scripts/lib/c7_prod_attestation.py`)다. 1차 산출물은 그 runner가 raw bytes로
해시하고 exact shape로 검증하는 manifest 파일 자체이며, 풍부한 receipt는
`--json` stdout 전용이다(파일로 쓰지 않는다 — runner의 `_exact_dict`가 manifest에
추가 키를 금지한다).

state root 정책은 새로 만들지 않는다. runner가 `E2E_C7_COMPATIBLE_PAIR_MANIFEST`로
operator 지정 절대경로를 받아 `_read_secure_file`로 여는 것과 똑같이, capture도
`--manifest-path` 절대경로를 받아 **같은 술어**로 부모 체인을 검증한다. 정책의
정본은 여전히 runner 한 곳이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import (
    _COMPOSE_PROJECT_PATTERN,
    _CONTRACT_GENERATION_PATTERN,
    _IMAGE_ID_PATTERN,
    _MAP_API_SERVICE,
    _MAP_DAGSTER_DAEMON_SERVICE,
    _MAP_DAGSTER_SERVICE,
    _MAP_UI_SERVICE,
    _PINVI_API_SERVICE,
    _SOURCE_REVISION_PATTERN,
    PAIR_MANIFEST_FILENAME,
    C6cCommandRunner,
    CompatiblePairManifest,
    DeploymentContractError,
    PairManifestCommitIndeterminateError,
    _canonical_absolute_path,
    assert_runner_readable_parent,
    effective_environment,
    initial_pair_manifest,
    inspect_c6c_image_source_revision,
    manifest_with_active_pair,
    new_image_pair,
    parse_pair_manifest,
    require_local_c6c_image,
    write_pair_manifest,
)
from kor_travel_docker_manager.services.compose_service import (
    c6c_deployment_lock_from_environment,
    get_env_path,
    get_project_root,
)

# runner의 role → compose service → manifest field 삼중 결박.
# `c7_prod_attestation.py` 30-36행 `PAIR_RUNTIME_IMAGE_FIELDS`와 같은 이름/순서다.
CAPTURE_ROLES: tuple[tuple[str, str, str], ...] = (
    ("map_api", _MAP_API_SERVICE, "map_image_id"),
    ("map_ui", _MAP_UI_SERVICE, "map_ui_image_id"),
    ("map_dagster_web", _MAP_DAGSTER_SERVICE, "map_dagster_image_id"),
    ("map_dagster_daemon", _MAP_DAGSTER_DAEMON_SERVICE, "map_dagster_daemon_image_id"),
    ("pinvi_api", _PINVI_API_SERVICE, "pinvi_image_id"),
)
_PINVI_ROLE = "pinvi_api"
_MAP_ROLES = tuple(role for role, _service, _field in CAPTURE_ROLES if role != _PINVI_ROLE)
_PINVI_BUILD_ENVIRONMENT = "production"
_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")

CAPTURE_COMMITTED = "capture_committed"
CAPTURE_REFUSED_PRECONDITION = "capture_refused_precondition"
CAPTURE_REFUSED_LOCK_CONTENDED = "capture_refused_lock_contended"
CAPTURE_REFUSED_RUNTIME = "capture_refused_runtime"
CAPTURE_WRITE_INDETERMINATE = "capture_write_indeterminate"

CAPTURE_EXIT_CODES: dict[str, int] = {
    CAPTURE_COMMITTED: 0,
    CAPTURE_REFUSED_PRECONDITION: 2,
    CAPTURE_REFUSED_LOCK_CONTENDED: 2,
    CAPTURE_REFUSED_RUNTIME: 1,
    CAPTURE_WRITE_INDETERMINATE: 1,
}

FENCE_NOTICE = "maintenance fence stays closed; no container was stopped, started, or recreated."
BUILD_FLAG_NOTICE = "capture builds nothing; building is the host compose deploy's responsibility"
# `c6c_deployment.c6c_deployment_lock`이 flock 경합에서 내는 exact 메시지.
LOCK_CONTENTION_MESSAGE = "another C6c compatible-pair operation is already active"

# capture가 **보장하지 않는** 것. receipt와 문서가 같은 문구를 쓴다.
NOT_GUARANTEED: tuple[str, ...] = (
    "that the recorded images were built from the recorded revisions; "
    "capture neither builds nor rebuild-compares anything",
    "that --build built anything; capture builds nothing",
    "that the rollback pair is restorable; only its shape is validated",
    "that the recorded revisions are reachable from any published branch",
    "that the runtime still matches after capture returns; this is an observation "
    "taken while the mutation lock was held",
)

# receipt는 전부 비민감값이다. 이 집합이 회귀 게이트다.
CAPTURE_RECEIPT_KEYS = frozenset(
    {
        "build_flag_accepted_no_op",
        "checkout_uid",
        "compose_project",
        "compose_project_directory",
        "contract_generation",
        "images",
        "manifest",
        "manifest_sha256",
        "map_source_checkout",
        "map_source_revision",
        "not_guaranteed",
        "operator_asserted_verified_compatible",
        "pinvi_source_checkout",
        "pinvi_source_revision",
        "returncode",
        "rollback_images_present",
        "side_effects",
        "state",
        "stderr",
        "stdout",
        "success",
    }
)

# root:root 0600 산출물은 root만 만들 수 있다. 추측 실패 대신 명시 실패한다.
# 검증에서는 이 네 상수를 실행 사용자 값으로 monkeypatch한다
# (`c6c_deployment._C6C_PRODUCTION_STATE_ROOT`와 같은 저장소 관례).
# `RUNNER_ANCESTOR_FLOOR`는 production에서 항상 None이며, 그때 ancestor 체인은
# runner와 똑같이 `/`까지 걷는다.
REQUIRED_EUID = 0
RUNNER_FILE_UID = 0
RUNNER_FILE_GID = 0
RUNNER_ANCESTOR_FLOOR: Path | None = None


class PairCaptureRefusal(Exception):
    """typed terminal state를 가진 capture 거부. 값은 노출하지 않는다."""

    def __init__(self, state: str, reason: str) -> None:
        super().__init__(f"{reason}; {FENCE_NOTICE}")
        self.state = state
        self.reason = reason

    @property
    def returncode(self) -> int:
        return CAPTURE_EXIT_CODES[self.state]


def _refuse(state: str, reason: str) -> PairCaptureRefusal:
    return PairCaptureRefusal(state, reason)


@dataclass(frozen=True)
class RuntimeObservation:
    """다섯 role의 실행 중 container·image 관측 한 벌."""

    containers: dict[str, str]
    images: dict[str, str]
    compose_project: str


def _default_runner(cwd: str) -> C6cCommandRunner:
    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)

    return run


def _run(
    runner: C6cCommandRunner,
    argv: list[str],
    failure: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(argv)
    except OSError as exc:
        raise _refuse(CAPTURE_REFUSED_RUNTIME, failure) from exc


def _observe_runtime(
    *,
    runner: C6cCommandRunner,
    project_directory: str,
    compose_project: str,
) -> RuntimeObservation:
    """C-7~C-10. runner `_compose_container`(277-302행)의 argv를 그대로 미러링한다."""

    containers: dict[str, str] = {}
    images: dict[str, str] = {}
    project_labels: set[str] = set()
    for role, service, _field in CAPTURE_ROLES:
        completed = _run(
            runner,
            [
                "docker",
                "compose",
                "--project-directory",
                project_directory,
                "ps",
                "-q",
                service,
            ],
            f"compose service {service} could not be resolved",
        )
        if completed.returncode != 0:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                f"compose service {service} could not be resolved",
            )
        ids = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if len(ids) != 1 or _CONTAINER_ID_PATTERN.fullmatch(ids[0]) is None:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                f"compose service {service} did not resolve to exactly one container",
            )
        container_id = ids[0]
        record = _inspect_container(runner, container_id, service=service)
        _assert_container_is_healthy(record, service=service)
        labels = _record_labels(record, service=service)
        if labels.get("com.docker.compose.service") != service:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                f"container compose service label does not match {service}",
            )
        project_label = labels.get("com.docker.compose.project")
        if not isinstance(project_label, str) or not project_label:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                f"container compose project label is missing for {service}",
            )
        project_labels.add(project_label)
        image_id = record.get("Image")
        if not isinstance(image_id, str) or _IMAGE_ID_PATTERN.fullmatch(image_id) is None:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                f"container image is not an immutable sha256 ID for {service}",
            )
        containers[role] = container_id
        images[role] = image_id

    if len(set(containers.values())) != len(CAPTURE_ROLES):
        raise _refuse(CAPTURE_REFUSED_RUNTIME, "compose services share a container")
    if len(project_labels) != 1:
        raise _refuse(CAPTURE_REFUSED_RUNTIME, "compose services span more than one project")
    observed_project = project_labels.pop()
    if observed_project != compose_project:
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            "compose project label does not match COMPOSE_PROJECT_NAME",
        )
    return RuntimeObservation(
        containers=containers,
        images=images,
        compose_project=observed_project,
    )


def _inspect_container(
    runner: C6cCommandRunner,
    container_id: str,
    *,
    service: str,
) -> Mapping[str, Any]:
    completed = _run(
        runner,
        ["docker", "inspect", "--", container_id],
        f"container inspect failed for {service}",
    )
    if completed.returncode != 0:
        raise _refuse(CAPTURE_REFUSED_RUNTIME, f"container inspect failed for {service}")
    try:
        records = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            f"container inspect output is invalid for {service}",
        ) from exc
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            f"container inspect shape is invalid for {service}",
        )
    record: Mapping[str, Any] = records[0]
    if record.get("Id") != container_id:
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            f"container identity changed during inspect for {service}",
        )
    return record


def _assert_container_is_healthy(record: Mapping[str, Any], *, service: str) -> None:
    """C-8. runner 501-508행과 동일 술어."""

    state = record.get("State")
    if not isinstance(state, dict):
        raise _refuse(CAPTURE_REFUSED_RUNTIME, f"container state is missing for {service}")
    health = state.get("Health")
    if (
        state.get("Running") is not True
        or state.get("Paused") is True
        or state.get("Restarting") is True
        or (isinstance(health, dict) and health.get("Status") != "healthy")
    ):
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            f"container is not running and healthy: {service}",
        )


def _record_labels(record: Mapping[str, Any], *, service: str) -> Mapping[str, Any]:
    config = record.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise _refuse(CAPTURE_REFUSED_RUNTIME, f"container labels are missing for {service}")
    return labels


def _observed_source_revisions(
    observation: RuntimeObservation,
    *,
    runner: C6cCommandRunner,
) -> tuple[str, str]:
    """C-11/C-12. image의 로컬 실재와 OCI revision label을 확인한다."""

    revisions: dict[str, str] = {}
    for role, service, _field in CAPTURE_ROLES:
        image_id = observation.images[role]
        try:
            require_local_c6c_image(image_id, runner=runner)
            revisions[role] = inspect_c6c_image_source_revision(
                image_id,
                label=service,
                expected_build_environment=(
                    _PINVI_BUILD_ENVIRONMENT if role == _PINVI_ROLE else None
                ),
                runner=runner,
            )
        except DeploymentContractError as exc:
            raise _refuse(CAPTURE_REFUSED_RUNTIME, str(exc)) from exc
    map_revisions = {revisions[role] for role in _MAP_ROLES}
    if len(map_revisions) != 1:
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            "the four Map images do not declare a single OCI source revision",
        )
    return map_revisions.pop(), revisions[_PINVI_ROLE]


def _assert_revision_exists_in_checkout(
    runner: C6cCommandRunner,
    *,
    checkout: Path,
    revision: str,
    label: str,
) -> None:
    """C-14/C-15. git ownership 정책을 우회하지 않는다 (`-c safe.directory` 미사용)."""

    existence = _run(
        runner,
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(checkout),
            "cat-file",
            "-e",
            f"{revision}^{{commit}}",
        ],
        f"{label} source checkout could not be queried",
    )
    if existence.returncode != 0:
        raise _refuse(
            CAPTURE_REFUSED_RUNTIME,
            f"{label} source revision is not a commit object in the named checkout",
        )
    status = _run(
        runner,
        ["git", "--no-optional-locks", "-C", str(checkout), "status", "--porcelain=v1"],
        f"{label} source checkout could not be queried",
    )
    if status.returncode != 0 or status.stdout != "":
        raise _refuse(CAPTURE_REFUSED_RUNTIME, f"{label} source checkout is not clean")


def _read_runner_secure_bytes(
    path: Path,
    *,
    mode: int = 0o600,
    expected_uid: int,
    expected_gid: int,
) -> bytes:
    """C7 runner `_read_secure_file`(112-164행) 술어를 그대로 옮긴 읽기."""

    assert_runner_readable_parent(
        path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        ancestor_floor=RUNNER_ANCESTOR_FLOOR,
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != expected_uid
            or observed.st_gid != expected_gid
            or stat.S_IMODE(observed.st_mode) != mode
        ):
            raise DeploymentContractError("compatible pair manifest is not runner-readable")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _existing_manifest(manifest_path: Path) -> CompatiblePairManifest | None:
    """C-6. 부재이거나 정규 v4여야 한다. 그 밖은 precondition 거부."""

    try:
        observed = manifest_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "existing compatible pair manifest cannot be inspected",
        ) from exc
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
        or observed.st_uid != RUNNER_FILE_UID
        or observed.st_gid != RUNNER_FILE_GID
        or stat.S_IMODE(observed.st_mode) != 0o600
    ):
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "existing compatible pair manifest type, owner, or mode is foreign",
        )
    try:
        payload = _read_runner_secure_bytes(
            manifest_path,
            expected_uid=RUNNER_FILE_UID,
            expected_gid=RUNNER_FILE_GID,
        )
        return parse_pair_manifest(payload)
    except (OSError, DeploymentContractError) as exc:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "existing compatible pair manifest is not a canonical v4 document",
        ) from exc


def _assert_runner_reparse(payload_bytes: bytes) -> None:
    """C-18(ii)(iii). manager의 느슨한 `_is_iso8601` 대신 runner 술어를 쓴다."""

    manifest = parse_pair_manifest(payload_bytes)
    for pair in (manifest.active, manifest.rollback):
        observed_at = datetime.fromisoformat(pair.recorded_at)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise DeploymentContractError("recorded_at has no UTC offset for the C7 runner")


def _stdout_block(receipt: Mapping[str, Any]) -> str:
    lines = [
        f"manifest={receipt['manifest']}",
        f"manifest_sha256={receipt['manifest_sha256']}",
        f"contract_generation={receipt['contract_generation']}",
        f"map_source_revision={receipt['map_source_revision']}",
        f"pinvi_source_revision={receipt['pinvi_source_revision']}",
        f"compose_project={receipt['compose_project']}",
        f"compose_project_directory={receipt['compose_project_directory']}",
    ]
    images = receipt["images"]
    lines.extend(f"{role}_image_id={images[role]}" for role, _service, _field in CAPTURE_ROLES)
    return "\n".join(lines) + "\n"


def _checkout_uid(path: Path) -> int:
    try:
        return path.lstat().st_uid
    except OSError as exc:
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, "source checkout cannot be inspected") from exc


def _required_absolute_path(value: str | None, option: str) -> Path:
    if not value:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            f"pinvi-pair capture requires {option} as a canonical absolute path",
        )
    try:
        return _canonical_absolute_path(value, option)
    except DeploymentContractError as exc:
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, str(exc)) from exc


def _required_directory(path: Path, option: str) -> Path:
    if not path.is_dir():
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, f"{option} is not an existing directory")
    return path


def _side_effects(manifest: Path, lock_path: object) -> list[str]:
    """정직한 자백: manifest 외에 mutation lock 디렉터리/파일 하나를 만질 수 있다."""

    effects = [f"replaced (atomic): {manifest}"]
    if isinstance(lock_path, str) and lock_path:
        effects.append(f"created if absent (mutation lock, 0600 in a 0700 directory): {lock_path}")
    return effects


def _rollback_images_present(
    manifest: CompatiblePairManifest,
    *,
    runner: C6cCommandRunner,
) -> bool:
    """rollback pair image의 로컬 실재를 보고만 한다. capture를 실패시키지 않는다."""

    for _role, _service, field in CAPTURE_ROLES:
        try:
            require_local_c6c_image(getattr(manifest.rollback, field), runner=runner)
        except DeploymentContractError:
            return False
    return True


def capture_compatible_pair(
    *,
    verified_compatible: bool,
    manifest_path: str | None,
    map_source_checkout: str | None,
    pinvi_source_checkout: str | None,
    expect_active_map_revision: str | None = None,
    build_flag: bool = False,
    project_directory: str | None = None,
    environment: Mapping[str, str] | None = None,
    runner: C6cCommandRunner | None = None,
    lock: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """실행 중인 다섯 컨테이너를 관측해 v4 manifest를 원자적으로 갱신한다.

    성공하면 `cli._emit_process_result` 규약을 만족하는 receipt dict를 돌려준다.
    실패는 전부 `PairCaptureRefusal`이며 컨테이너에는 손대지 않는다.
    """

    # --- C-1: operator assertion. docker/git을 한 번도 부르지 않는다.
    if not verified_compatible:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "capturing a rollback pair requires --verified-compatible",
        )

    # --- C-2: manifest path와 operator가 지목한 두 checkout.
    manifest = _required_absolute_path(manifest_path, "--manifest-path")
    if manifest.name != PAIR_MANIFEST_FILENAME:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            f"--manifest-path basename must be {PAIR_MANIFEST_FILENAME}",
        )
    map_checkout = _required_directory(
        _required_absolute_path(map_source_checkout, "--map-source-checkout"),
        "--map-source-checkout",
    )
    pinvi_checkout = _required_directory(
        _required_absolute_path(pinvi_source_checkout, "--pinvi-source-checkout"),
        "--pinvi-source-checkout",
    )
    if expect_active_map_revision is not None and (
        _SOURCE_REVISION_PATTERN.fullmatch(expect_active_map_revision) is None
    ):
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "--expect-active-map-revision must be an exact lowercase 40-hex commit",
        )

    # --- C-3: identity. root:root 0600 파일은 root만 만들 수 있다.
    if os.geteuid() != REQUIRED_EUID:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "pinvi-pair capture must run as root to write a root-owned runner artifact",
        )

    # --- C-4: ancestor policy. capture는 절대 mkdir하지 않는다.
    try:
        assert_runner_readable_parent(
            manifest,
            expected_uid=RUNNER_FILE_UID,
            expected_gid=RUNNER_FILE_GID,
            ancestor_floor=RUNNER_ANCESTOR_FLOOR,
        )
    except DeploymentContractError as exc:
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, str(exc)) from exc

    # --- C-5: env 최소 계약. runtime mutation이 없으므로
    #          `_validate_mutation_environment`는 호출하지 않는다.
    values = effective_environment(get_env_path()) if environment is None else environment
    generation = values.get("KTDM_C6C_CONTRACT_GENERATION", "").strip().lower()
    if _CONTRACT_GENERATION_PATTERN.fullmatch(generation) is None:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "KTDM_C6C_CONTRACT_GENERATION must be an explicit stable identifier",
        )
    compose_project = values.get("COMPOSE_PROJECT_NAME", "").strip().lower()
    if _COMPOSE_PROJECT_PATTERN.fullmatch(compose_project) is None:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "COMPOSE_PROJECT_NAME must be explicit and canonical for capture",
        )

    resolved_project_directory = (
        get_project_root() if project_directory is None else project_directory
    )
    command_runner = _default_runner(resolved_project_directory) if runner is None else runner
    lock_factory = c6c_deployment_lock_from_environment if lock is None else lock

    with ExitStack() as stack:
        try:
            lock_snapshot = stack.enter_context(lock_factory())
        except DeploymentContractError as exc:
            state = (
                CAPTURE_REFUSED_LOCK_CONTENDED
                if str(exc) == LOCK_CONTENTION_MESSAGE
                else CAPTURE_REFUSED_PRECONDITION
            )
            raise _refuse(state, str(exc)) from exc

        # --- C-6: 기존 파일 사전 검증.
        existing = _existing_manifest(manifest)

        # --- C-7~C-12: 1차 관측.
        first = _observe_runtime(
            runner=command_runner,
            project_directory=resolved_project_directory,
            compose_project=compose_project,
        )
        map_revision, pinvi_revision = _observed_source_revisions(first, runner=command_runner)

        # --- C-13: 의도한 배포 commit 결박.
        if expect_active_map_revision is not None and map_revision != expect_active_map_revision:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                "observed Map source revision does not match --expect-active-map-revision",
            )

        # --- C-14/C-15: commit 실재와 checkout cleanliness.
        _assert_revision_exists_in_checkout(
            command_runner,
            checkout=map_checkout,
            revision=map_revision,
            label="Map",
        )
        _assert_revision_exists_in_checkout(
            command_runner,
            checkout=pinvi_checkout,
            revision=pinvi_revision,
            label="PinVi",
        )

        active = new_image_pair(
            first.images["map_api"],
            first.images["pinvi_api"],
            generation,
            map_ui_image_id=first.images["map_ui"],
            map_dagster_image_id=first.images["map_dagster_web"],
            map_dagster_daemon_image_id=first.images["map_dagster_daemon"],
            map_source_revision=map_revision,
            pinvi_source_revision=pinvi_revision,
        )
        next_manifest = (
            initial_pair_manifest(active)
            if existing is None
            else manifest_with_active_pair(existing, active)
        )
        rollback_present = _rollback_images_present(next_manifest, runner=command_runner)

        # --- C-16: 쓰기 직전 2차 관측.
        second = _observe_runtime(
            runner=command_runner,
            project_directory=resolved_project_directory,
            compose_project=compose_project,
        )
        if second.containers != first.containers or second.images != first.images:
            raise _refuse(CAPTURE_REFUSED_RUNTIME, "runtime changed between the two observations")

        # --- C-17: 원자적 커밋.
        try:
            write_pair_manifest(
                str(manifest),
                next_manifest,
                owner_uid=RUNNER_FILE_UID,
                owner_gid=RUNNER_FILE_GID,
                ancestor_floor=RUNNER_ANCESTOR_FLOOR,
            )
        except PairManifestCommitIndeterminateError as exc:
            raise _refuse(CAPTURE_WRITE_INDETERMINATE, str(exc)) from exc
        except DeploymentContractError as exc:
            raise _refuse(CAPTURE_REFUSED_RUNTIME, str(exc)) from exc

        # --- C-18: 자기 출력 재검증. 해시는 디스크에서 되읽은 bytes의 것이다.
        try:
            committed_bytes = _read_runner_secure_bytes(
                manifest,
                expected_uid=RUNNER_FILE_UID,
                expected_gid=RUNNER_FILE_GID,
            )
            _assert_runner_reparse(committed_bytes)
        except (OSError, ValueError, DeploymentContractError) as exc:
            raise _refuse(
                CAPTURE_WRITE_INDETERMINATE,
                "committed compatible pair manifest failed the C7 runner re-read",
            ) from exc

        receipt: dict[str, Any] = {
            "state": CAPTURE_COMMITTED,
            "success": True,
            "returncode": CAPTURE_EXIT_CODES[CAPTURE_COMMITTED],
            "stderr": "",
            "manifest": str(manifest),
            "manifest_sha256": hashlib.sha256(committed_bytes).hexdigest(),
            "contract_generation": generation,
            "map_source_revision": map_revision,
            "pinvi_source_revision": pinvi_revision,
            "compose_project": compose_project,
            "compose_project_directory": resolved_project_directory,
            "images": dict(first.images),
            "map_source_checkout": str(map_checkout),
            "pinvi_source_checkout": str(pinvi_checkout),
            "checkout_uid": {
                "map": _checkout_uid(map_checkout),
                "pinvi": _checkout_uid(pinvi_checkout),
            },
            "operator_asserted_verified_compatible": True,
            "build_flag_accepted_no_op": bool(build_flag),
            "rollback_images_present": rollback_present,
            "not_guaranteed": list(NOT_GUARANTEED),
            "side_effects": _side_effects(manifest, getattr(lock_snapshot, "lock_path", None)),
        }
        receipt["stdout"] = _stdout_block(receipt)
        return receipt
    raise AssertionError("capture exit stack must not swallow control flow")  # pragma: no cover
