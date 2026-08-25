"""C7 prod runner가 읽는 `compatible-pair-v4.json`을 비파괴로 기록한다.

Map 저장소 런북 `docs/runbooks/c7-prod-live-e2e.md` §2.1 step 8이 부르는
`ktdctl pinvi-pair capture --verified-compatible --build`의 구현이다.

**이름이 같은 옛 명령이 아직 n150에 설치돼 있다.** 설치본 revision
`4191582779be47e9605a324ea27adbb99b438439`(2026-08-19 실측)의 `pinvi-pair capture`는 Map 넷과
PinVi API를 내리고 candidate image로 force-recreate하는 **파괴형**이며 이 파일 자체가 없다.
그래서 이 구현은 `CAPTURE_CONTRACT`로 자기를 식별하고, `--help`·비-JSON stdout·`--json`
receipt 셋 다 같은 문자열을 낸다. 실행 전 확인 절차는 `docs/docker-management.md` §7.5에 있다.

이 모듈은 **컨테이너를 절대 건드리지 않는다**. 내보내는 docker argv는 세 종류의
읽기 전용 조회뿐이고(`compose --project-directory ... ps -q`, `inspect --`,
`image inspect --format=... --`), `up`/`stop`/`start`/`rm`/`build`/`restart`는 코드
경로에 존재하지 않는다. 따라서 어떤 실패 경로도 rollback을 수행하지 않는다 —
되돌릴 mutation이 없기 때문이다. 런북 §2.1 step 1의 maintenance fence는 실패
시에도 닫힌 채 유지하며, 어떤 실패 메시지도 fence 해제를 제안하지 않는다.

산출물의 소비자는 사람이 아니라 Map 저장소의 C7 runner
(`scripts/lib/c7_prod_attestation.py`)다. 1차 산출물은 그 runner가 raw bytes로
해시하고 exact shape로 검증하는 manifest 파일 자체이며, receipt는 파일로 쓰지 않는다
(runner의 `_exact_dict`가 manifest에 추가 키를 금지한다). receipt의 증거값은 `--json`
전용이 아니다 — 런북이 `--json` 없이 부르므로 비-JSON stdout 블록도 pre-image·
`rollback_images_present`·`side_effects`·`input_sources`를 그대로 낸다.

state root 규칙은 새로 만들지 않는다. 기본 manifest 경로는 이 저장소가 이미 가진
`c6c_state_paths(frozen env)[0]`에서 유도한다. n150에 설치된 shim
(`/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl`)이
`KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT=/opt/kor-travel-docker-manager`를 하드코딩하므로
`get_env_path()`가 읽는 frozen env는 `/opt/.../.env`(`KTDM_DEPLOYMENT_ENVIRONMENT=
production`, `COMPOSE_PROJECT_NAME=kor-travel-docker-manager`)이고, 유도값은
`/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json` —
root:root 0600으로 **이미 존재하는** 그 파일이다(2026-08-19 실측).

operator가 다른 파일을 정본으로 쓰려면 runner가 읽는 env 이름
`E2E_C7_COMPATIBLE_PAIR_MANIFEST`(또는 `--manifest-path`)로 지목한다. basename은
강제하지 않는다 — runner(`run-c7-prod-live-e2e.sh` 607행)는 절대경로만 요구하고 파일명
제약이 없으며, 오늘 C7 lane 스크립트는 `/etc/kor-travel-map/c7-compatible-pair-v4.json`을
쓴다. `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST`는 fallback으로 **읽지 않는다**: production
frozen env에 그 키가 있으면 `c6c_state_paths`가 raise해 capture만이 아니라
`c6c_deployment_lock_from_environment()`를 잡는 모든 Manager mutation이 함께 죽는다.
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
    CompatibleImagePair,
    CompatiblePairManifest,
    DeploymentContractError,
    PairManifestCommitIndeterminateError,
    _canonical_absolute_path,
    assert_runner_readable_parent,
    c6c_state_paths,
    effective_environment,
    initial_pair_manifest,
    inspect_c6c_image_source_revision,
    manifest_with_active_pair,
    new_image_pair,
    pair_manifest_bytes,
    parse_pair_manifest,
    require_local_c6c_image,
    restore_pair_manifest_snapshot,
    write_pair_manifest,
)
from kor_travel_docker_manager.services.compose_service import (
    c6c_deployment_lock_from_environment,
    get_env_path,
    get_project_root,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeGeneration,
    f1d_legacy_artifact_paths,
    pinned_runtime_manifest_path,
    pinned_runtime_state_root,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    manifest_from_payload as pinned_manifest_from_payload,
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

# 같은 사실을 두 번 적는 v6 pinned generation과의 대조표. 값이 어긋나도 거부하지
# 않는다 — capture는 별도 production runtime의 읽기 전용 관측기이며 rebuild authority가
# 아니다. 다만 불일치를 침묵시키지도 않는다.
PINNED_GENERATION_IMAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("map_api", "map_api_image_id"),
    ("map_ui", "map_ui_image_id"),
    ("map_dagster_web", "map_dagster_image_id"),
    ("map_dagster_daemon", "map_dagster_daemon_image_id"),
    ("pinvi_api", "pinvi_api_image_id"),
)
_PINNED_MANIFEST_MAX_BYTES = 64 * 1024

CAPTURE_COMMITTED = "capture_committed"
CAPTURE_REFUSED_PRECONDITION = "capture_refused_precondition"
CAPTURE_REFUSED_LOCK_CONTENDED = "capture_refused_lock_contended"
CAPTURE_REFUSED_CHECKOUT_OWNERSHIP = "capture_refused_checkout_ownership"
CAPTURE_REFUSED_RUNTIME = "capture_refused_runtime"
CAPTURE_WRITE_ROLLED_BACK = "capture_write_rolled_back"
CAPTURE_WRITE_INDETERMINATE = "capture_write_indeterminate"

CAPTURE_EXIT_CODES: dict[str, int] = {
    CAPTURE_COMMITTED: 0,
    CAPTURE_REFUSED_PRECONDITION: 2,
    CAPTURE_REFUSED_LOCK_CONTENDED: 2,
    CAPTURE_REFUSED_CHECKOUT_OWNERSHIP: 2,
    CAPTURE_REFUSED_RUNTIME: 1,
    CAPTURE_WRITE_ROLLED_BACK: 1,
    CAPTURE_WRITE_INDETERMINATE: 1,
}

FENCE_NOTICE = "maintenance fence stays closed; no container was stopped, started, or recreated."
BUILD_FLAG_NOTICE = "capture builds nothing; building is the host compose deploy's responsibility"
# `c6c_deployment.c6c_deployment_lock`이 flock 경합에서 내는 exact 메시지.
LOCK_CONTENTION_MESSAGE = "another C6c compatible-pair operation is already active"

# 세 입력의 해결 순서. manifest는 flag → runner env → `c6c_state_paths` 유도값이고,
# 두 checkout은 flag → frozen env다. CLI flag는 어디까지나 override다.
MANIFEST_PATH_OPTION = "--manifest-path"
MAP_CHECKOUT_OPTION = "--map-source-checkout"
PINVI_CHECKOUT_OPTION = "--pinvi-source-checkout"
MANIFEST_PATH_ENV_NAMES: tuple[str, ...] = ("E2E_C7_COMPATIBLE_PAIR_MANIFEST",)
# flag도 runner env도 없을 때 쓰는 유도 기본값의 출처 이름(receipt `input_sources`).
MANIFEST_PATH_DERIVED_SOURCE = "c6c_state_paths"
# 이 키는 **일부러 읽지 않는다**. production frozen env에 넣는 순간 `c6c_state_paths`가
# "production C6c manifest and global lock paths are fixed"로 raise하고, capture만이
# 아니라 `c6c_deployment_lock_from_environment()`를 잡는 모든 Manager mutation이 죽는다.
MANIFEST_PATH_FORBIDDEN_ENV_NAME = "KTDM_C6C_COMPATIBLE_PAIR_MANIFEST"
MAP_CHECKOUT_ENV_NAMES: tuple[str, ...] = ("KTDM_C7_MAP_SOURCE_CHECKOUT",)
PINVI_CHECKOUT_ENV_NAMES: tuple[str, ...] = ("KTDM_C7_PINVI_SOURCE_CHECKOUT",)

# git 하위 프로세스에서 반드시 제거하는 상속 변수. 이 중 하나라도 남으면 `-C
# <checkout>`이 다른 저장소를 가리켜 결박이 동어반복이 된다.
GIT_ENVIRONMENT_OVERRIDES: tuple[str, ...] = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
)
GIT_DUBIOUS_OWNERSHIP_MARKER = "dubious ownership"

# 이 구현의 자기 식별자. `pinvi-pair capture --help`와 모든 성공 출력(비-JSON stdout,
# `--json` receipt)이 **같은 문자열**을 낸다.
#
# 왜 필요한가: n150에 설치된 Manager는 이 브랜치보다 **앞선 revision**
# (`4191582779be47e9605a324ea27adbb99b438439`, 2026-08-19 실측)이고, 그 설치본의
# `pinvi-pair capture`는 여기 구현과 **이름만 같은 파괴형 명령**이다 — Map 넷 + PinVi API를
# 내리고 candidate image로 force-recreate한 뒤 smoke를 돈다. 옛 구현에는 이 문자열이
# 어디에도 없으므로, `--help` 한 번으로 "지금 이 호스트에 설치된 것이 관측기인가"를
# 실행 없이 판정할 수 있다. 문서(`docs/docker-management.md` §7.5)의 실행 전 확인 절차가
# 이 문자열을 근거로 삼는다.
CAPTURE_CONTRACT = "pair-capture-v1"
CAPTURE_CONTRACT_LINE = f"capture_contract={CAPTURE_CONTRACT}"

# C7 runner가 attestation과 대조하는 manifest 유래 값. `c7_prod_attestation.py`
# 443-448행이 이 네 값을 한 `if`에서 함께 보고, 하나라도 어긋나면
# `AttestationError("compatible pair mismatch")`다. `recorded_at`이 새로 찍히면
# `manifest_sha256`은 **반드시** 바뀌고, runtime이 실제로 움직여서 새로 찍힌 것이라면
# 두 revision도 함께 바뀐다. 그래서 "capture는 멱등"이라는 문장은 identity가 같을 때만
# 참이며, 그 밖의 모든 경우에는 런북 §2.3 attestation 재생성이 **필수**다.
ATTESTATION_BOUND_FIELDS: tuple[str, ...] = (
    "manifest_sha256",
    "active.map_source_revision",
    "active.pinvi_source_revision",
    "active.contract_generation",
)
ATTESTATION_REGENERATION_NOTICE = (
    "regenerate the runbook 2.3 attestation before the C7 runner runs: this capture "
    "stamped a new recorded_at, so the manifest bytes and their sha256 changed. "
    "c7_prod_attestation.py (lines 443-448) compares "
    + ", ".join(ATTESTATION_BOUND_FIELDS)
    + " against the attestation together, so a stale attestation fails with "
    "`compatible pair mismatch`"
)

# capture가 **보장하지 않는** 것. receipt와 문서가 같은 문구를 쓴다.
NOT_GUARANTEED: tuple[str, ...] = (
    "that the recorded images were built from the recorded revisions; "
    "capture neither builds nor rebuild-compares anything",
    "that --build built anything; capture builds nothing",
    "that the rollback pair is restorable; only its shape is validated",
    "that the recorded revisions are reachable from any published branch",
    "that the runtime still matches after capture returns; this is an observation "
    "taken while the mutation lock was held",
    "that the v6 pinned generation manifest describes this runtime; capture only "
    "reports whether the two records agree and never edits the v6 file",
)

# receipt는 전부 비민감값이다. 이 집합이 회귀 게이트다.
CAPTURE_RECEIPT_KEYS = frozenset(
    {
        "allow_generation_change",
        "attestation_action",
        "build_flag_accepted_no_op",
        "capture_contract",
        "checkout_uid",
        "compose_project",
        "compose_project_directory",
        "contract_generation",
        "images",
        "input_sources",
        "manifest",
        "manifest_sha256",
        "map_source_checkout",
        "map_source_revision",
        "not_guaranteed",
        "operator_asserted_verified_compatible",
        "pinned_generation_agrees",
        "pinned_generation_divergent_roles",
        "pinned_generation_manifest",
        "pinvi_source_checkout",
        "pinvi_source_revision",
        "previous_active",
        "previous_manifest_sha256",
        "previous_recorded_at",
        "recorded_at_preserved",
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


@dataclass(frozen=True)
class ResolvedInput:
    """flag override 또는 frozen env fallback으로 결정된 입력 하나."""

    value: str
    source: str


@dataclass(frozen=True)
class ExistingManifest:
    """교체 대상 manifest의 pre-image. 이 값이 receipt의 증거가 된다."""

    manifest: CompatiblePairManifest
    payload_bytes: bytes
    mode: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload_bytes).hexdigest()


@dataclass(frozen=True)
class PinnedGenerationComparison:
    """v5 pinned generation과 관측값의 대조 결과. 거부 사유가 아니다."""

    manifest_path: str | None
    agrees: bool | None
    divergent_roles: tuple[str, ...]


def capture_command_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """하위 프로세스 env에서 git redirection 변수를 제거한 사본을 만든다.

    상속된 ``GIT_DIR``/``GIT_WORK_TREE``는 ``git -C <checkout>``을 조용히 무력화해
    "그 commit이 그 checkout에 있다"는 유일한 비-동어반복 결박을 우회시킨다.
    """

    values = dict(os.environ if base is None else base)
    for name in GIT_ENVIRONMENT_OVERRIDES:
        values.pop(name, None)
    return values


def _default_runner(cwd: str) -> C6cCommandRunner:
    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=capture_command_environment(),
            text=True,
            capture_output=True,
            check=False,
        )

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
    """C-9~C-12. runner `_compose_container`(285-310행)의 argv를 그대로 미러링한다."""

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
    """C-10. runner `c7_prod_attestation.py` 508-518행과 동일 술어.

    술어를 **똑같이** 옮겼다는 뜻은 그 구멍까지 옮겼다는 뜻이다. `State.Health`가 없는
    컨테이너 — 즉 healthcheck를 선언하지 않은 서비스 — 는 `isinstance(health, dict)`가
    거짓이라 health 항목을 **통과로 본다**. 오늘 n150에서
    `kor-travel-map-dagster-daemon-latest`가 그렇다(2026-08-19 실측). 그래서 "다섯이
    running·healthy"라는 문장은 정확히는 "다섯이 running이고, healthcheck를 선언한 것은
    healthy"다. runner와 다르게 만들면 capture가 통과시킨 runtime을 runner가 거부하거나
    그 반대가 되므로 여기서 더 엄격하게 굴지 않는다.
    """

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
    """C-13/C-14. image의 로컬 실재와 OCI revision label을 확인한다."""

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


def _assert_no_dubious_ownership(
    completed: subprocess.CompletedProcess[str],
    *,
    checkout: Path,
    label: str,
) -> None:
    """git ownership 거부를 "commit 없음"으로 뭉개지 않고 별도 상태로 구분한다."""

    stderr = completed.stderr or ""
    if completed.returncode == 0 or GIT_DUBIOUS_OWNERSHIP_MARKER not in stderr.lower():
        return
    raise _refuse(
        CAPTURE_REFUSED_CHECKOUT_OWNERSHIP,
        (
            f"git refused the {label} checkout {checkout} as dubious ownership, so the "
            "revision binding could not be evaluated at all; this is not a missing commit. "
            "chown the checkout to the capture user, or record it in the system-wide "
            "git config safe.directory (capture never passes -c safe.directory itself)"
        ),
    )


def _assert_revision_exists_in_checkout(
    runner: C6cCommandRunner,
    *,
    checkout: Path,
    revision: str,
    label: str,
) -> None:
    """C-16/C-17. git ownership 정책을 우회하지 않는다 (`-c safe.directory` 미사용)."""

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
    _assert_no_dubious_ownership(existence, checkout=checkout, label=label)
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
    _assert_no_dubious_ownership(status, checkout=checkout, label=label)
    if status.returncode != 0 or status.stdout != "":
        raise _refuse(CAPTURE_REFUSED_RUNTIME, f"{label} source checkout is not clean")


def _read_runner_secure_bytes(
    path: Path,
    *,
    mode: int = 0o600,
    expected_uid: int,
    expected_gid: int,
) -> bytes:
    """C7 runner `_read_secure_file`(111-162행) 술어를 그대로 옮긴 읽기."""

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


def _existing_manifest(manifest_path: Path) -> ExistingManifest | None:
    """C-8. 부재이거나 정규 v4여야 한다. 그 밖은 precondition 거부."""

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
        return ExistingManifest(
            manifest=parse_pair_manifest(payload),
            payload_bytes=payload,
            mode=stat.S_IMODE(observed.st_mode),
        )
    except (OSError, DeploymentContractError) as exc:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "existing compatible pair manifest is not a canonical v4 document",
        ) from exc


def _assert_runner_reparse(payload_bytes: bytes) -> None:
    """C-20(ii)(iii). manager의 느슨한 `_is_iso8601` 대신 runner 술어를 쓴다."""

    manifest = parse_pair_manifest(payload_bytes)
    for pair in (manifest.active, manifest.rollback):
        observed_at = datetime.fromisoformat(pair.recorded_at)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise DeploymentContractError("recorded_at has no UTC offset for the C7 runner")


def _pair_identity(pair: CompatibleImagePair) -> dict[str, str]:
    """runner가 강제하는 9필드를 그대로 담은 비민감 identity."""

    return {
        "contract_generation": pair.contract_generation,
        "map_dagster_daemon_image_id": pair.map_dagster_daemon_image_id,
        "map_dagster_image_id": pair.map_dagster_image_id,
        "map_image_id": pair.map_image_id,
        "map_source_revision": pair.map_source_revision,
        "map_ui_image_id": pair.map_ui_image_id,
        "pinvi_image_id": pair.pinvi_image_id,
        "pinvi_source_revision": pair.pinvi_source_revision,
        "recorded_at": pair.recorded_at,
    }


def _identity_without_recorded_at(pair: CompatibleImagePair) -> dict[str, str]:
    identity = _pair_identity(pair)
    del identity["recorded_at"]
    return identity


def _observed_identity(
    *,
    images: Mapping[str, str],
    generation: str,
    map_revision: str,
    pinvi_revision: str,
) -> dict[str, str]:
    """관측값이 만들 active pair의 identity(runner 9필드 중 `recorded_at` 제외)."""

    return {
        "contract_generation": generation,
        "map_dagster_daemon_image_id": images["map_dagster_daemon"],
        "map_dagster_image_id": images["map_dagster_web"],
        "map_image_id": images["map_api"],
        "map_source_revision": map_revision,
        "map_ui_image_id": images["map_ui"],
        "pinvi_image_id": images["pinvi_api"],
        "pinvi_source_revision": pinvi_revision,
    }


def _preserved_recorded_at(
    existing: ExistingManifest | None,
    observed: Mapping[str, str],
) -> str | None:
    """동일 runtime 재capture를 **byte-멱등**으로 만든다. 그 외에는 만들지 못한다.

    C7 runner는 `manifest_sha256 == attestation["compatible_pair_manifest_sha256"]`를
    포함해 `ATTESTATION_BOUND_FIELDS` 네 값을 한 `if`에서 함께 강제한다
    (`c7_prod_attestation.py` 443-448행, sha256 비교는 444행). `recorded_at`을 매번
    `now()`로 찍으면 아무것도 바뀌지 않은 재capture도 파일 해시를 바꿔 이미 발급된
    attestation을 깨뜨린다. 그래서 관측 identity가 기존 active와 완전히 같을 때만 기존
    시각을 보존하고, 한 필드라도 다르면 새 시각을 찍는다.

    **멱등은 좁은 특수 경우다.** 첫 capture(`existing is None`)와, runtime이 바뀐 뒤의
    capture는 정의상 새 `recorded_at`을 찍으므로 `manifest_sha256`이 바뀐다. 그리고
    runtime이 바뀌었다면 `active.map_source_revision`·`active.pinvi_source_revision`도
    함께 바뀌는데, runner는 그 둘도 attestation의 `source_commits`와 대조한다(446-447행).
    그러므로 그 두 경우에는 §2.3 attestation을 **반드시 다시 만들어야** 하며,
    `None`을 돌려준 사실이 receipt의 `recorded_at_preserved=false`와 stdout의
    `attestation_action=…` 한 줄로 호출자에게 그대로 전달된다.
    """

    if existing is None:
        return None
    if _identity_without_recorded_at(existing.manifest.active) != dict(observed):
        return None
    return existing.manifest.active.recorded_at


def _read_pinned_generation(path: Path) -> PinnedRuntimeGeneration | None:
    """v6 manifest를 읽기 전용·no-mkdir로 연다. 실패는 전부 ``None``이다.

    ``pinned_runtime_generation.read_manifest``는 부모 디렉터리를 만들 수 있으므로
    (``_validate_state_parent``의 mkdir) capture 경로에서는 쓰지 않는다.
    """

    try:
        observed = path.lstat()
    except OSError:
        return None
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o600
        or observed.st_size > _PINNED_MANIFEST_MAX_BYTES
    ):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino) != (observed.st_dev, observed.st_ino):
            return None
        raw = os.read(descriptor, _PINNED_MANIFEST_MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    if len(raw) > _PINNED_MANIFEST_MAX_BYTES:
        return None
    try:
        return pinned_manifest_from_payload(json.loads(raw.decode("utf-8"))).active_generation
    except (ValueError, UnicodeDecodeError, DeploymentContractError):
        return None


def _compare_pinned_generation(
    values: Mapping[str, str],
    *,
    images: Mapping[str, str],
    map_revision: str,
    pinvi_revision: str,
) -> PinnedGenerationComparison:
    """C-15. 같은 사실을 두 번 적는 v6 기록과 관측값을 대조한다(거부하지 않는다)."""

    try:
        path = pinned_runtime_manifest_path(values)
    except DeploymentContractError:
        return PinnedGenerationComparison(manifest_path=None, agrees=None, divergent_roles=())
    generation = _read_pinned_generation(path)
    if generation is None:
        return PinnedGenerationComparison(
            manifest_path=str(path),
            agrees=None,
            divergent_roles=(),
        )
    divergent = [
        role
        for role, field_name in PINNED_GENERATION_IMAGE_FIELDS
        if getattr(generation, field_name) != images[role]
    ]
    if generation.map_source_revision != map_revision:
        divergent.append("map_source_revision")
    if generation.pinvi_source_revision != pinvi_revision:
        divergent.append("pinvi_source_revision")
    return PinnedGenerationComparison(
        manifest_path=str(path),
        agrees=not divergent,
        divergent_roles=tuple(sorted(divergent)),
    )


def _pinned_generation_line(receipt: Mapping[str, Any]) -> str:
    agrees = receipt["pinned_generation_agrees"]
    label = "unknown" if agrees is None else ("true" if agrees else "false")
    line = f"pinned_generation_agrees={label}"
    divergent = receipt["pinned_generation_divergent_roles"]
    if divergent:
        line += f" divergent={','.join(divergent)}"
    return line


def _stdout_block(receipt: Mapping[str, Any]) -> str:
    lines = [
        # 첫 줄은 자기 식별이다. 옛 파괴형 capture가 설치된 호스트에서는 이 줄이 없다.
        CAPTURE_CONTRACT_LINE,
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
    lines.append(_pinned_generation_line(receipt))
    lines.extend(_evidence_lines(receipt))
    return "\n".join(lines) + "\n"


def _optional(value: object) -> str:
    return "none" if value is None else str(value)


def _evidence_lines(receipt: Mapping[str, Any]) -> list[str]:
    """`--json` 없이 부르는 런북 호출에서 사라지면 안 되는 증거값.

    특히 `rollback_images_present=false`는 "기록한 rollback pair를 복원할 수 없다"는
    뜻이라 사람이 읽는 기본 출력에 반드시 보여야 한다. 마찬가지로
    `recorded_at_preserved=false`는 "§2.3 attestation을 다시 만들어야 한다"는 뜻이므로
    그 뒤에 `attestation_action=` 한 줄이 무엇을 해야 하는지 문장으로 말한다.
    여기 나오는 값은 전부 비민감값이며 `--json` receipt와 같은 사실이다.
    """

    sources = receipt["input_sources"]
    lines = [
        f"input_source.manifest_path={sources['manifest_path']}",
        f"input_source.map_source_checkout={sources['map_source_checkout']}",
        f"input_source.pinvi_source_checkout={sources['pinvi_source_checkout']}",
        f"previous_manifest_sha256={_optional(receipt['previous_manifest_sha256'])}",
        f"previous_recorded_at={_optional(receipt['previous_recorded_at'])}",
    ]
    preserved = "true" if receipt["recorded_at_preserved"] else "false"
    lines.append(f"recorded_at_preserved={preserved}")
    action = receipt["attestation_action"]
    if action is not None:
        lines.append(f"attestation_action={action}")
    previous_active = receipt["previous_active"]
    if previous_active is None:
        lines.append("previous_active=none")
    else:
        lines.extend(
            f"previous_active.{name}={value}"
            for name, value in sorted(previous_active.items())
        )
    present = "true" if receipt["rollback_images_present"] else "false"
    lines.append(f"rollback_images_present={present}")
    lines.extend(f"side_effect={effect}" for effect in receipt["side_effects"])
    return lines


def _checkout_uid(path: Path) -> int:
    try:
        return path.lstat().st_uid
    except OSError as exc:
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, "source checkout cannot be inspected") from exc


def _missing_input_reason(option: str, env_names: tuple[str, ...], description: str) -> str:
    """막다른 길 금지. 어디에 무엇을 넣어야 하는지를 문장으로 적는다."""

    joined = " (or ".join(env_names) + ")" * (len(env_names) - 1)
    return (
        f"pinvi-pair capture has no {description}: either pass "
        f"{option} <canonical absolute path>, or set {joined} to that path in the frozen "
        "environment that capture reads (the Manager env-file or the process environment). "
        "nothing was observed and nothing was written"
    )


def _frozen_environment() -> Mapping[str, str]:
    """Manager frozen environment 읽기를 typed refusal로 감싼다.

    `get_env_path()`/`effective_environment()`는 `DeploymentContractError`
    (`ValueError` 하위 — 예: curation service principal 유도 실패)와 `OSError`를 낼 수
    있다. 이 호출이 try 밖에 있으면 raw traceback이 나가고 fence 문구도 붙지 않는다.
    """

    try:
        return effective_environment(get_env_path())
    except (OSError, ValueError) as exc:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            f"the frozen Manager environment could not be read: {exc}",
        ) from exc


def _resolve_input(
    flag_value: str | None,
    *,
    option: str,
    env_names: tuple[str, ...],
    values: Mapping[str, str],
    description: str,
) -> ResolvedInput:
    """CLI flag를 override로, frozen env를 정본 fallback으로 읽는다."""

    if flag_value is not None and flag_value.strip():
        return ResolvedInput(value=flag_value.strip(), source=option)
    for name in env_names:
        candidate = values.get(name, "").strip()
        if candidate:
            return ResolvedInput(value=candidate, source=name)
    raise _refuse(
        CAPTURE_REFUSED_PRECONDITION,
        _missing_input_reason(option, env_names, description),
    )


def _resolve_manifest_input(
    flag_value: str | None,
    *,
    values: Mapping[str, str],
) -> ResolvedInput:
    """manifest 경로: flag → runner env → `c6c_state_paths` 유도 기본값.

    세 번째 state root 규칙을 만들지 않으려고 기본값을 이 저장소가 이미 가진
    `c6c_state_paths`에서 유도한다. 설치본이 읽는 frozen env가 production이므로
    유도값은 runner가 실제로 읽어 온 root:root 0600 파일과 같다. 값이 없어서 거부하는
    경로는 존재하지 않는다 — 유도가 실패할 때만 거부하며 그때도 어디에 무엇을 넣어야
    하는지, 그리고 무엇을 넣으면 **안 되는지**를 함께 말한다.
    """

    if flag_value is not None and flag_value.strip():
        return ResolvedInput(value=flag_value.strip(), source=MANIFEST_PATH_OPTION)
    for name in MANIFEST_PATH_ENV_NAMES:
        candidate = values.get(name, "").strip()
        if candidate:
            return ResolvedInput(value=candidate, source=name)
    try:
        derived, _lock = c6c_state_paths(values)
    except DeploymentContractError as exc:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            (
                "the default compatible pair manifest path could not be derived from the "
                f"frozen environment ({exc}); pass {MANIFEST_PATH_OPTION} <canonical "
                f"absolute path>, or set {MANIFEST_PATH_ENV_NAMES[0]} to that path in the "
                f"frozen environment. do not add {MANIFEST_PATH_FORBIDDEN_ENV_NAME} to a "
                "production env-file to work around this: that key makes c6c_state_paths "
                "raise for every Manager mutation that takes the global deployment lock, "
                "not only for capture. nothing was observed and nothing was written"
            ),
        ) from exc
    return ResolvedInput(value=derived, source=MANIFEST_PATH_DERIVED_SOURCE)


def _canonical_input_path(resolved: ResolvedInput) -> Path:
    try:
        return _canonical_absolute_path(resolved.value, resolved.source)
    except DeploymentContractError as exc:
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, str(exc)) from exc


def _required_directory(path: Path, source: str) -> Path:
    if not path.is_dir():
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, f"{source} is not an existing directory")
    return path


def _assert_manifest_outlives_rebuild_pinned(
    manifest: Path,
    *,
    values: Mapping[str, str],
    source: str,
) -> None:
    """``rebuild-pinned``가 쓸어가는 state root 안을 manifest 자리로 쓰지 못하게 한다.

    ``rebuild-pinned``는 pinned runtime state root 아래에서
    ``f1d_legacy_artifact_paths()``(``compatible-pair-v4.json`` 포함)를 퇴역시킨다.
    그 root 안을 runner의 read target으로 삼으면 rehearsal rebuild 한 번이
    attestation 입력을 지운다. n150 rehearsal에서는 두 root가 실제로 같은 디렉터리다.
    """

    try:
        state_root = pinned_runtime_state_root(values)
    except DeploymentContractError as exc:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            (
                "the pinned runtime state root could not be resolved, so capture cannot "
                f"prove {source} is outside the directory `ktdctl rebuild-pinned` sweeps "
                f"({exc})"
            ),
        ) from exc
    if manifest != state_root and state_root not in manifest.parents:
        return
    raise _refuse(
        CAPTURE_REFUSED_PRECONDITION,
        (
            f"{source} points inside the pinned runtime state root {state_root}, where "
            f"`ktdctl rebuild-pinned` retires {PAIR_MANIFEST_FILENAME} together with the "
            f"other {len(f1d_legacy_artifact_paths())} F1D legacy artifacts; put the C7 "
            f"runner's read target in a root-owned 0700 directory outside that root and "
            f"point {MANIFEST_PATH_ENV_NAMES[0]} at it"
        ),
    )


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


def _restore_after_failed_reread(
    manifest: Path,
    *,
    existing: ExistingManifest | None,
) -> PairCaptureRefusal:
    """커밋 후 재읽기 실패. 직전 bytes(또는 부재)로 되돌리고 결과를 상태로 구분한다."""

    try:
        restore_pair_manifest_snapshot(
            manifest,
            previous_bytes=None if existing is None else existing.payload_bytes,
            previous_mode=None if existing is None else existing.mode,
            owner_uid=RUNNER_FILE_UID,
            owner_gid=RUNNER_FILE_GID,
        )
    except OSError:
        return _refuse(
            CAPTURE_WRITE_INDETERMINATE,
            "committed compatible pair manifest failed the C7 runner re-read and the "
            "previous manifest state could not be restored",
        )
    return _refuse(
        CAPTURE_WRITE_ROLLED_BACK,
        "committed compatible pair manifest failed the C7 runner re-read; the previous "
        "manifest state was restored",
    )


def capture_compatible_pair(
    *,
    verified_compatible: bool,
    manifest_path: str | None,
    map_source_checkout: str | None,
    pinvi_source_checkout: str | None,
    expect_active_map_revision: str | None = None,
    allow_generation_change: bool = False,
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

    # --- C-2: env 최소 계약. runtime mutation이 없으므로
    #          `_validate_mutation_environment`는 호출하지 않는다. 세 입력의 fallback을
    #          여기서 읽으므로 경로 결정보다 먼저 온다.
    values = _frozen_environment() if environment is None else environment
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

    # --- C-3: manifest path와 operator가 지목한 두 checkout. manifest는 flag →
    #          runner env → `c6c_state_paths` 유도값이라 "없어서" 거부되지 않는다.
    #          두 checkout은 flag → frozen env이며, 없으면 넣을 곳을 알려주고 거부한다.
    # basename은 강제하지 않는다. runner(`run-c7-prod-live-e2e.sh` 607행)는 절대경로만
    # 요구하고 파일명 제약이 없으며, 오늘 C7 lane은 `c7-compatible-pair-v4.json`을 쓴다.
    # manager가 runner에 없는 제약을 만들 이유가 없다.
    manifest_input = _resolve_manifest_input(manifest_path, values=values)
    manifest = _canonical_input_path(manifest_input)
    _assert_manifest_outlives_rebuild_pinned(
        manifest,
        values=values,
        source=manifest_input.source,
    )
    map_input = _resolve_input(
        map_source_checkout,
        option=MAP_CHECKOUT_OPTION,
        env_names=MAP_CHECKOUT_ENV_NAMES,
        values=values,
        description="Map source checkout to bind the observed Map revision to",
    )
    map_checkout = _required_directory(_canonical_input_path(map_input), map_input.source)
    pinvi_input = _resolve_input(
        pinvi_source_checkout,
        option=PINVI_CHECKOUT_OPTION,
        env_names=PINVI_CHECKOUT_ENV_NAMES,
        values=values,
        description="PinVi source checkout to bind the observed PinVi revision to",
    )
    pinvi_checkout = _required_directory(_canonical_input_path(pinvi_input), pinvi_input.source)
    if expect_active_map_revision is not None and (
        _SOURCE_REVISION_PATTERN.fullmatch(expect_active_map_revision) is None
    ):
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "--expect-active-map-revision must be an exact lowercase 40-hex commit",
        )

    # --- C-4: identity. root:root 0600 파일은 root만 만들 수 있다.
    if os.geteuid() != REQUIRED_EUID:
        raise _refuse(
            CAPTURE_REFUSED_PRECONDITION,
            "pinvi-pair capture must run as root to write a root-owned runner artifact",
        )

    # --- C-5: ancestor policy. capture는 절대 mkdir하지 않는다.
    try:
        assert_runner_readable_parent(
            manifest,
            expected_uid=RUNNER_FILE_UID,
            expected_gid=RUNNER_FILE_GID,
            ancestor_floor=RUNNER_ANCESTOR_FLOOR,
        )
    except DeploymentContractError as exc:
        raise _refuse(CAPTURE_REFUSED_PRECONDITION, str(exc)) from exc

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

        # --- C-6~C-8: 기존 파일 사전 검증과 pre-image 증거.
        existing = _existing_manifest(manifest)
        if (
            existing is not None
            and existing.manifest.active.contract_generation != generation
            and not allow_generation_change
        ):
            raise _refuse(
                CAPTURE_REFUSED_PRECONDITION,
                (
                    "the existing manifest was recorded under contract generation "
                    f"{existing.manifest.active.contract_generation} but the frozen "
                    f"KTDM_C6C_CONTRACT_GENERATION is {generation}; capture will not "
                    "silently move the C7 runner across generations. re-run with "
                    "--allow-generation-change only if this switch is intended"
                ),
            )

        # --- C-9~C-14: 1차 관측.
        first = _observe_runtime(
            runner=command_runner,
            project_directory=resolved_project_directory,
            compose_project=compose_project,
        )
        map_revision, pinvi_revision = _observed_source_revisions(first, runner=command_runner)

        # --- C-15: v6 pinned generation과의 대조(보고 전용).
        pinned = _compare_pinned_generation(
            values,
            images=first.images,
            map_revision=map_revision,
            pinvi_revision=pinvi_revision,
        )

        # --- C-16: 의도한 배포 commit 결박.
        if expect_active_map_revision is not None and map_revision != expect_active_map_revision:
            raise _refuse(
                CAPTURE_REFUSED_RUNTIME,
                "observed Map source revision does not match --expect-active-map-revision",
            )

        # --- C-17/C-18: commit 실재와 checkout cleanliness.
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

        observed_identity = _observed_identity(
            images=first.images,
            generation=generation,
            map_revision=map_revision,
            pinvi_revision=pinvi_revision,
        )
        # 보존 여부는 receipt·stdout이 그대로 보고한다. `None`이면 새 `recorded_at`이
        # 찍히고, 그때는 §2.3 attestation 재생성이 필수다.
        preserved_recorded_at = _preserved_recorded_at(existing, observed_identity)
        active = new_image_pair(
            first.images["map_api"],
            first.images["pinvi_api"],
            generation,
            map_ui_image_id=first.images["map_ui"],
            map_dagster_image_id=first.images["map_dagster_web"],
            map_dagster_daemon_image_id=first.images["map_dagster_daemon"],
            map_source_revision=map_revision,
            pinvi_source_revision=pinvi_revision,
            recorded_at=preserved_recorded_at,
        )
        next_manifest = (
            initial_pair_manifest(active)
            if existing is None
            else manifest_with_active_pair(existing.manifest, active)
        )
        rollback_present = _rollback_images_present(next_manifest, runner=command_runner)

        # --- C-19: 쓰기 **전에** runner 술어로 검증한다. `os.replace`는 되돌릴 수 없다.
        try:
            next_bytes = pair_manifest_bytes(next_manifest)
            _assert_runner_reparse(next_bytes)
        except (ValueError, DeploymentContractError) as exc:
            raise _refuse(
                CAPTURE_REFUSED_PRECONDITION,
                "the compatible pair manifest that capture would write fails the C7 runner "
                "re-parse; nothing was written",
            ) from exc

        # --- C-20: 쓰기 직전 2차 관측.
        second = _observe_runtime(
            runner=command_runner,
            project_directory=resolved_project_directory,
            compose_project=compose_project,
        )
        if second.containers != first.containers or second.images != first.images:
            raise _refuse(CAPTURE_REFUSED_RUNTIME, "runtime changed between the two observations")

        # --- C-21: 원자적 커밋.
        try:
            written_bytes = write_pair_manifest(
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
        if written_bytes != next_bytes:
            raise _refuse(
                CAPTURE_WRITE_INDETERMINATE,
                "the committed bytes differ from the bytes validated before the write",
            )

        # --- C-22: 자기 출력 재검증. 해시는 디스크에서 되읽은 bytes의 것이다.
        try:
            committed_bytes = _read_runner_secure_bytes(
                manifest,
                expected_uid=RUNNER_FILE_UID,
                expected_gid=RUNNER_FILE_GID,
            )
            _assert_runner_reparse(committed_bytes)
        except (OSError, ValueError, DeploymentContractError) as exc:
            raise _restore_after_failed_reread(manifest, existing=existing) from exc

        receipt: dict[str, Any] = {
            "state": CAPTURE_COMMITTED,
            "success": True,
            "returncode": CAPTURE_EXIT_CODES[CAPTURE_COMMITTED],
            "stderr": "",
            "capture_contract": CAPTURE_CONTRACT,
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
            "input_sources": {
                "manifest_path": manifest_input.source,
                "map_source_checkout": map_input.source,
                "pinvi_source_checkout": pinvi_input.source,
            },
            "checkout_uid": {
                "map": _checkout_uid(map_checkout),
                "pinvi": _checkout_uid(pinvi_checkout),
            },
            "previous_manifest_sha256": None if existing is None else existing.sha256,
            "previous_active": (
                None if existing is None else _pair_identity(existing.manifest.active)
            ),
            "previous_recorded_at": (
                None if existing is None else existing.manifest.active.recorded_at
            ),
            "recorded_at_preserved": preserved_recorded_at is not None,
            "attestation_action": (
                None if preserved_recorded_at is not None else ATTESTATION_REGENERATION_NOTICE
            ),
            "allow_generation_change": bool(allow_generation_change),
            "operator_asserted_verified_compatible": True,
            "build_flag_accepted_no_op": bool(build_flag),
            "rollback_images_present": rollback_present,
            "pinned_generation_manifest": pinned.manifest_path,
            "pinned_generation_agrees": pinned.agrees,
            "pinned_generation_divergent_roles": list(pinned.divergent_roles),
            "not_guaranteed": list(NOT_GUARANTEED),
            "side_effects": _side_effects(manifest, getattr(lock_snapshot, "lock_path", None)),
        }
        receipt["stdout"] = _stdout_block(receipt)
        return receipt
    raise AssertionError("capture exit stack must not swallow control flow")  # pragma: no cover
