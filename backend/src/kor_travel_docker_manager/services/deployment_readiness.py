"""재구축을 누르기 전에 실패를 아는 읽기 전용 사전 점검.

설계 정본: ``docs/ktdctl-ui-migration.md`` 1부 진단 5 / §1.3 P10-4.

이 모듈은 "지금 재구축을 실행하면 실패하는가"만 답한다. 아무것도 바꾸지 않고,
root를 요구하지 않으며, 공개 진입점은 절대 예외를 던지지 않는다. **판단 근거가
없으면 ``unknown``을 반환하고 절대 추측하지 않는다** — 잘못된 초록불은 사람이
pinset 하나와 반나절을 태우게 만든다(terminal candidate는 재시도 금지이므로
실패한 pinset은 되돌릴 수 없다).

관측된 실제 blocker 셋 중 둘을 여기서 사전에 잡는다. 세 번째(오프라인 wheelhouse
완결성)는 정직하게 검사할 수 없어 ``unavailable_checks``로 선언만 한다 —
없는 근거로 초록불을 켜느니 검사하지 않는다고 말한다.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    effective_environment,
)
from kor_travel_docker_manager.services.compose_service import (
    get_compose_path,
    get_env_path,
    get_override_path,
    map_application_300_python_base_references_from_root,
)
from kor_travel_docker_manager.services.runtime_pin_registry import (
    read_published_runtime_pins,
)

DEPLOYMENT_READINESS_SCHEMA: Final = "kor-travel-docker-manager.deployment-readiness.v1"
READINESS_TTL_SECONDS: Final = 30.0
_PROBE_LOCK_WAIT_SECONDS: Final = 5.0
_GIT_TIMEOUT_SECONDS: Final = 10.0
_DOCKER_TIMEOUT_SECONDS: Final = 10.0
_MAX_BASE_REFERENCES: Final = 4
_REVISION = re.compile(r"^[0-9a-f]{40}$")

# ``c6c_deployment._assert_candidate_single_file_boundary``가 실제로 거부하는 두 이름만
# 차단으로 분류한다. 나머지는 Compose 해석을 바꾸지만 rebuild gate가 거부하지는
# 않으므로 warn이다 — 막지 않는 것을 막힌다고 말하면 사람이 엉뚱한 것을 고친다.
_BLOCKING_AMBIENT_ENV_NAMES: Final = (
    "COMPOSE_FILE",
    "KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE",
)
# `COMPOSE_PROJECT_NAME`과 `KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT`는 **운영에서 반드시
# 설정돼 있어야 하는 값**이다(전자는 C6c state 경로가 요구하고, 후자는 trusted installer가
# ktdctl shim에 박는다). 이것을 "확인이 필요한 변수"로 분류하면 정상 호스트가 영구히
# 노란불이 되고, 해소 방법도 없다 — 지워지지 않는 경고는 패널을 안 읽게 만든다.
_ADVISORY_AMBIENT_ENV_NAMES: Final = (
    "COMPOSE_PROFILES",
    "COMPOSE_PATH_SEPARATOR",
)

# docker-compose.yml의 bind-mount source 계약. 이 파일들이 없으면 DB bootstrap이
# 컨테이너 안에서 실패하는데, 그 실패는 rebuild가 한참 진행된 뒤에야 드러난다.
_SIBLING_REQUIRED_FILES: Final = (
    ("PINVI_REPO_DIR", "../pinvi", "infra/postgres/bootstrap-pinvi-runtime-role.sh"),
    ("KOR_TRAVEL_MAP_REPO_DIR", "../kor-travel-map", "docker/postgres-role-bootstrap.sh"),
    ("KOR_TRAVEL_MAP_REPO_DIR", "../kor-travel-map", "scripts/database-credential-preflight.sh"),
)

_PINVI_ROLE_BOOTSTRAP_SCRIPT: Final = "infra/postgres/bootstrap-pinvi-runtime-role.sh"
# rebuild가 이 스크립트를 부르는 두 가지 특수 모드. Manager는 둘 다 `-e`로 주입하는데,
# 스크립트가 그 이름을 모르면 변수는 **조용히 무시되고 일반 부트스트랩이 돌아간다**.
# 그 뒤 Manager가 결과를 읽고 fail-close하므로 데이터가 깨지지는 않지만, 의도하지 않은
# 부트스트랩이 한 번 실행되고 pinset 하나가 소모된다. 실행 전에 알 수 있는 일이다.
_PINVI_ROLE_BOOTSTRAP_REQUIRED_MODES: Final = (
    ("PINVI_ROLE_TOPOLOGY_VERIFY_ONLY", "역할 토폴로지 검증(읽기 전용)"),
    ("PINVI_ROLE_CATALOG_RESET_ONLY", "fresh DB 역할 카탈로그 reset"),
    ("PINVI_ROLE_CATALOG_RESET_PERMIT_FILE", "reset permit 파일 경로"),
    ("PINVI_ROLE_CATALOG_RESET_RESULT_FILE", "reset 결과 파일 경로"),
)
_MAX_SCRIPT_BYTES: Final = 512 * 1024

_CHECK_LABELS: Final = {
    "compose_single_file": "Compose 입력이 단일 파일인가",
    "sibling_bootstrap_scripts": "사이드카 저장소 필수 스크립트",
    "pinvi_role_bootstrap_modes": "고정된 PinVi revision의 역할 부트스트랩 계약",
    "map_python_base_images": "Map 후보 빌드의 고정 Python base image",
}

_CHECK_ORDER: Final = (
    "compose_single_file",
    "sibling_bootstrap_scripts",
    "pinvi_role_bootstrap_modes",
    "map_python_base_images",
)

_UNAVAILABLE_CHECKS: Final = (
    {
        "id": "offline_wheelhouse",
        "label_ko": "오프라인 wheelhouse 완결성",
        "reason": (
            "installer 입력 wheelhouse는 release마다 다른 경로이고, 그 상위 디렉터리는 "
            "installer가 설치할 때마다 0700 root:root로 되돌린다 — 비-root backend는 "
            "traverse조차 못 한다. 요구 wheel 집합도 설치 시점 의존성 해석의 결과라 "
            "실행 없이 재현할 수 없다. 추측한 값을 보여주느니 검사하지 않는다."
        ),
    },
)

# state 의미:
#   ok      = 이 항목은 재구축을 막지 않는다
#   missing = 재구축을 확실히 막는 결손·위반(차단)
#   warn    = 막지는 않지만 사람이 확인해야 한다
#   unknown = 판단 근거가 부족하다
CheckState = Literal["ok", "warn", "unknown", "missing"]
CheckSource = Literal["project_root", "sibling_checkout", "docker_cli", "none"]


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    state: CheckState
    label_ko: str
    detail: str
    source: CheckSource
    evidence: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "label_ko": self.label_ko,
            "detail": self.detail,
            "source": self.source,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class _CachedReadiness:
    payload: dict[str, Any]
    monotonic_at: float


# 모듈 상태는 프로세스별이다. uvicorn worker가 여럿이면 worker마다 자기 캐시를 갖고
# 독립적으로 탐침하므로 실제 탐침 빈도는 worker 수 × (1/TTL)이다. 진단 패널로는
# 수용 가능하며, 공유 캐시로 "고치려" 들면 프로세스 간 락이 필요해진다.
_CACHE: _CachedReadiness | None = None
_LOCK = threading.Lock()


def clear_deployment_readiness_cache() -> None:
    """테스트와 수동 새로고침용."""

    global _CACHE
    _CACHE = None


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _child_environment() -> dict[str, str]:
    """자식 프로세스에 넘길 최소 환경. 호출자의 env를 그대로 물려주지 않는다."""

    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    for name in ("DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR"):
        value = os.environ.get(name, "").strip()
        if value:
            environment[name] = value
    return environment


def _run_read_only(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: str = "/",
) -> subprocess.CompletedProcess[bytes] | None:
    """프로세스를 띄우는 유일한 지점. 실패·타임아웃은 예외가 아니라 ``None``이다.

    ``compose_service._run_git_read``를 재사용하지 않는다 — 거기에는 ``timeout``이
    없어서 UI가 폴링하는 route에서 쓰면 멈춘 git 하나가 anyio worker를 무한히 문다.
    """

    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=_child_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_text(repository: Path, args: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *args],
            cwd="/",
            env=_child_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _git_blob_text(repository: Path, revision_path: str, *, max_bytes: int) -> str | None:
    """`git show <rev>:<path>`의 내용을 **크기 상한 안에서** 텍스트로 읽는다.

    `_git_text`를 쓰지 않는 이유가 둘이다. (1) 그쪽은 `text=True`라 non-UTF-8 blob에서
    `UnicodeDecodeError`를 던지는데 그것은 `OSError`가 아니어서 밖으로 새고, 결국 패널
    전체가 `unknown`이 된다 — 한 행만 degrade해야 한다. (2) `capture_output`은 blob을
    통째로 버퍼링하므로 "다 읽은 뒤 크기를 재는" 상한은 장식이다. 여기서는 파이프에서
    `max_bytes + 1`만 읽고 나머지를 버린다.
    """

    try:
        process = subprocess.Popen(  # noqa: S603 - 인자 배열, shell 없음
            ["git", "-C", str(repository), "show", revision_path],
            cwd="/",
            env=_child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        assert process.stdout is not None
        raw = process.stdout.read(max_bytes + 1)
    except OSError:
        process.kill()
        process.wait(timeout=_GIT_TIMEOUT_SECONDS)
        return None
    finally:
        if process.stdout is not None:
            process.stdout.close()
    try:
        returncode = process.wait(timeout=_GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        return None
    if returncode != 0 or len(raw) > max_bytes:
        return None
    # 선언 여부만 보므로 손상된 바이트는 버려도 판정이 달라지지 않는다.
    return raw.decode("utf-8", "replace")


def _docker_daemon_reachable() -> bool | None:
    """daemon 접근 가능 여부. 이 게이트는 load-bearing이다.

    ``docker image inspect``는 "image가 없다"와 "daemon에 접근할 수 없다"를 **같은**
    비정상 종료로 알린다. 게이트가 없으면 image store가 멀쩡한 호스트에서 비-root
    backend가 거짓 차단을 보고한다.
    """

    completed = _run_read_only(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )
    if completed is None:
        return None
    return completed.returncode == 0


def _local_image_present(reference: str) -> bool | None:
    """local image store 보유 여부. **절대 pull하지 않는다.**"""

    completed = _run_read_only(
        ["docker", "image", "inspect", reference],
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )
    if completed is None:
        return None
    return completed.returncode == 0


def _effective_values() -> dict[str, str] | None:
    """Compose 우선순위가 적용된 실효 환경. 읽을 수 없으면 ``None``이다."""

    try:
        return effective_environment(get_env_path())
    except (DeploymentContractError, OSError, UnicodeError, ValueError):
        # 무관한 `.env` 결함(예: 다른 계약의 principal 쌍) 하나가 사전 점검 전체를
        # 500으로 만들면 안 된다. 근거가 없으면 unknown으로 떨어뜨린다.
        return None


def _compose_directory() -> Path:
    return Path(get_compose_path()).resolve().parent


def _repository_root(values: Mapping[str, str], env_name: str, default: str) -> Path | None:
    raw = (values.get(env_name) or default).strip() or default
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _compose_directory() / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _file_row(path: Path) -> dict[str, Any]:
    """bind-mount source 파일 한 개의 상태."""

    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "state": "missing", "detail": "파일이 없습니다", "mode": None}
    except OSError:
        return {
            "path": str(path),
            "state": "unknown",
            "detail": "확인할 수 없습니다(권한 또는 경로)",
            "mode": None,
        }
    mode = file_stat.st_mode
    octal = f"{stat.S_IMODE(mode):04o}"
    if stat.S_ISLNK(mode):
        try:
            path.resolve(strict=True)
        except OSError:
            return {
                "path": str(path),
                "state": "missing",
                "detail": "끊어진 심볼릭 링크",
                "mode": octal,
            }
        return {
            "path": str(path),
            "state": "warn",
            "detail": "심볼릭 링크입니다",
            "mode": octal,
        }
    if stat.S_ISDIR(mode):
        # compose가 short bind syntax(`src:dst:ro`)를 쓰므로 source가 없으면 Docker가
        # 그 자리에 **빈 디렉터리**를 만든다. 그러면 존재 여부만 보는 점검은 초록이
        # 되고 컨테이너 안의 `sh <script>`만 실패한다.
        return {
            "path": str(path),
            "state": "missing",
            "detail": "파일이 아니라 디렉터리입니다(bind mount source 부재 시 Docker가 만든 빈 디렉터리일 수 있습니다)",
            "mode": octal,
        }
    if not stat.S_ISREG(mode):
        return {
            "path": str(path),
            "state": "missing",
            "detail": "일반 파일이 아닙니다",
            "mode": octal,
        }
    if file_stat.st_size == 0:
        return {"path": str(path), "state": "missing", "detail": "0바이트입니다", "mode": octal}
    if stat.S_IMODE(mode) & 0o022:
        return {
            "path": str(path),
            "state": "warn",
            "detail": f"그룹/기타 쓰기 가능({octal})",
            "mode": octal,
        }
    return {"path": str(path), "state": "ok", "detail": "정상", "mode": octal}


_STATE_RANK: Final[dict[str, int]] = {"ok": 0, "warn": 1, "unknown": 2, "missing": 3}


def _worst_state(states: Sequence[str]) -> CheckState:
    """확실히 아는 차단이 unknown을 이긴다.

    unknown이 missing을 덮으면 사람이 실제로 막고 있는 것부터 고치지 못한다.
    """

    worst = "ok"
    for state in states:
        if _STATE_RANK.get(state, 0) > _STATE_RANK[worst]:
            worst = state
    return worst  # type: ignore[return-value]


def _check_compose_single_file(values: Mapping[str, str] | None) -> ReadinessCheck:
    label = "Compose 입력이 단일 파일인가"
    if values is None:
        return ReadinessCheck(
            id="compose_single_file",
            state="unknown",
            label_ko=label,
            detail=".env를 읽을 수 없어 Compose ambient 변수를 확인하지 못했습니다.",
            source="none",
            evidence={},
        )
    override_path = Path(get_override_path())
    contributions: list[str] = []
    details: list[str] = []
    override_present = False
    try:
        override_path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        contributions.append("unknown")
        details.append("override 경로를 확인할 수 없습니다.")
    else:
        override_present = True
        try:
            override_path.resolve(strict=True)
        except OSError:
            # `build_command`는 `os.path.exists`로 판정하므로 끊어진 링크는 실제로
            # 합성되지 않는다 — 막지는 않지만 정리 대상이다.
            contributions.append("warn")
            details.append("끊어진 override 심볼릭 링크가 있습니다.")
        else:
            contributions.append("missing")
            details.append(
                "docker-compose.override.yml이 존재합니다. single-file Compose 계약 "
                "위반이며 승인된 재구축 전체를 막습니다. legacy override 퇴역 절차로 "
                "이관하세요."
            )
    blocking = [name for name in _BLOCKING_AMBIENT_ENV_NAMES if (values.get(name) or "").strip()]
    if blocking:
        contributions.append("missing")
        details.append(f"Compose 합성을 바꾸는 환경변수가 설정돼 있습니다: {', '.join(blocking)}")
    advisory = [name for name in _ADVISORY_AMBIENT_ENV_NAMES if (values.get(name) or "").strip()]
    if advisory:
        contributions.append("warn")
        details.append(f"확인이 필요한 Compose 환경변수: {', '.join(advisory)}")
    state = _worst_state(contributions)
    return ReadinessCheck(
        id="compose_single_file",
        state=state,
        label_ko=label,
        detail=" ".join(details) if details else "Compose 입력이 단일 파일입니다.",
        source="project_root",
        evidence={
            "override_path": str(override_path),
            "override_present": override_present,
            "ambient_blocking": blocking,
            "ambient_advisory": advisory,
        },
    )


def _check_sibling_bootstrap_scripts(values: Mapping[str, str] | None) -> ReadinessCheck:
    label = "사이드카 저장소 필수 스크립트"
    if values is None:
        return ReadinessCheck(
            id="sibling_bootstrap_scripts",
            state="unknown",
            label_ko=label,
            detail=".env를 읽을 수 없어 사이드카 경로를 해석하지 못했습니다.",
            source="none",
            evidence={},
        )
    rows: list[dict[str, Any]] = []
    for env_name, default, relative in _SIBLING_REQUIRED_FILES:
        root = _repository_root(values, env_name, default)
        if root is None:
            rows.append(
                {
                    "path": f"{(values.get(env_name) or default)}/{relative}",
                    "state": "unknown",
                    "detail": f"{env_name} 경로를 해석할 수 없습니다",
                    "mode": None,
                }
            )
            continue
        rows.append(_file_row(root / relative))
    state = _worst_state([row["state"] for row in rows])
    usable = sum(1 for row in rows if row["state"] in {"ok", "warn"})
    detail = f"{len(rows)}개 중 {usable}개 확인됨."
    if state != "ok":
        first_bad = next(row for row in rows if row["state"] != "ok")
        detail = f"{detail} {first_bad['path']}: {first_bad['detail']}."
    return ReadinessCheck(
        id="sibling_bootstrap_scripts",
        state=state,
        label_ko=label,
        detail=detail,
        source="sibling_checkout",
        evidence={"required": len(rows), "present": usable, "files": rows},
    )


def _unknown_pinvi_mode_check(
    detail: str, *, source: CheckSource, evidence: Mapping[str, Any] | None = None
) -> ReadinessCheck:
    """모른다고 말할 때도 **무엇을 보려 했는지**는 남긴다.

    "읽지 못했습니다"만 있고 어느 revision을 어느 체크아웃에서 찾았는지가 없으면,
    운영자는 진단을 화면에서 시작할 수 없다.
    """

    return ReadinessCheck(
        id="pinvi_role_bootstrap_modes",
        state="unknown",
        label_ko="고정된 PinVi revision의 역할 부트스트랩 계약",
        detail=detail,
        source=source,
        evidence=dict(evidence or {}),
    )


def _check_pinvi_role_bootstrap_modes(values: Mapping[str, str] | None) -> ReadinessCheck:
    """고정된 PinVi revision이 Manager가 부르는 두 모드를 실제로 구현하는지 본다.

    Map base image 점검과 달리 체크아웃 HEAD를 보지 않고 **고정 revision의 blob을
    직접 읽는다**(``git show <rev>:<path>``). 재구축이 실제로 쓰는 것이 그 tree이고,
    체크아웃이 어느 브랜치에 있든 답이 달라지면 안 되기 때문이다.
    """

    label = "고정된 PinVi revision의 역할 부트스트랩 계약"
    if values is None:
        return _unknown_pinvi_mode_check(
            ".env를 읽을 수 없어 PinVi 체크아웃 경로를 해석하지 못했습니다.", source="none"
        )
    pins = read_published_runtime_pins()
    status = pins.get("status")
    if status != "ok":
        return _unknown_pinvi_mode_check(
            f"고정 revision이 권위 있는 값이 아닙니다(status={status}). "
            "무엇을 대조해야 하는지 알 수 없습니다.",
            source="none",
        )
    pinned = next(
        (
            source.get("revision")
            for source in pins.get("sources", [])
            if source.get("role") == "pinvi"
        ),
        None,
    )
    if not isinstance(pinned, str) or _REVISION.fullmatch(pinned) is None:
        return _unknown_pinvi_mode_check("고정된 PinVi revision을 읽지 못했습니다.", source="none")
    pinvi_root = _repository_root(values, "PINVI_REPO_DIR", "../pinvi")
    if pinvi_root is None:
        return _unknown_pinvi_mode_check(
            "PinVi 체크아웃 경로를 해석할 수 없습니다.",
            source="sibling_checkout",
            evidence={"pinned_revision": pinned},
        )
    looked_for = {
        "pinvi_root": str(pinvi_root),
        "pinned_revision": pinned,
        "script_path": _PINVI_ROLE_BOOTSTRAP_SCRIPT,
    }
    script = _git_blob_text(
        pinvi_root,
        f"{pinned}:{_PINVI_ROLE_BOOTSTRAP_SCRIPT}",
        max_bytes=_MAX_SCRIPT_BYTES,
    )
    if script is None:
        # revision이 로컬에 없거나 그 tree에 파일이 없다. 둘 다 "모른다"이지 "없다"가
        # 아니다 — fetch되지 않은 revision을 결손으로 보고하면 거짓 차단이 된다.
        return _unknown_pinvi_mode_check(
            f"고정 revision({pinned[:12]})의 {_PINVI_ROLE_BOOTSTRAP_SCRIPT}를 읽지 "
            "못했습니다. 체크아웃에 그 revision이 fetch돼 있는지 확인하세요.",
            source="sibling_checkout",
            evidence=looked_for,
        )
    missing = [
        (name, description)
        for name, description in _PINVI_ROLE_BOOTSTRAP_REQUIRED_MODES
        if name not in script
    ]
    evidence = {
        "pinvi_root": str(pinvi_root),
        "pinned_revision": pinned,
        "script_path": _PINVI_ROLE_BOOTSTRAP_SCRIPT,
        "required": len(_PINVI_ROLE_BOOTSTRAP_REQUIRED_MODES),
        "present": len(_PINVI_ROLE_BOOTSTRAP_REQUIRED_MODES) - len(missing),
        "missing": [name for name, _ in missing],
    }
    if missing:
        return ReadinessCheck(
            id="pinvi_role_bootstrap_modes",
            state="missing",
            label_ko=label,
            detail=(
                "고정된 PinVi revision의 역할 부트스트랩 스크립트가 재구축이 요구하는 "
                f"모드를 모릅니다: {', '.join(description for _, description in missing)}. "
                "이 상태로 실행하면 주입한 설정이 무시된 채 일반 부트스트랩이 돌고, "
                "재구축은 그 뒤에 실패합니다. 해당 모드를 구현한 PinVi revision으로 "
                "회전하세요."
            ),
            source="sibling_checkout",
            evidence=evidence,
        )
    return ReadinessCheck(
        id="pinvi_role_bootstrap_modes",
        state="ok",
        label_ko=label,
        detail=(
            f"고정 revision({pinned[:12]})이 재구축이 요구하는 모드 "
            f"{len(_PINVI_ROLE_BOOTSTRAP_REQUIRED_MODES)}종을 모두 선언합니다."
        ),
        source="sibling_checkout",
        evidence=evidence,
    )


def _unknown_base_image_check(detail: str, *, source: CheckSource) -> ReadinessCheck:
    return ReadinessCheck(
        id="map_python_base_images",
        state="unknown",
        label_ko="Map 후보 빌드의 고정 Python base image",
        detail=detail,
        source=source,
        evidence={},
    )


def _check_map_python_base_images(values: Mapping[str, str] | None) -> ReadinessCheck:
    label = "Map 후보 빌드의 고정 Python base image"
    if values is None:
        return _unknown_base_image_check(
            ".env를 읽을 수 없어 Map 체크아웃 경로를 해석하지 못했습니다.", source="none"
        )
    pins = read_published_runtime_pins()
    status = pins.get("status")
    if status != "ok":
        # degraded/stale도 값은 있지만, 이 점검은 "체크아웃이 그 pin인가"의 비교다.
        # 비교 기준을 신뢰할 수 없으면 답도 신뢰할 수 없다.
        return _unknown_base_image_check(
            f"고정 revision이 권위 있는 값이 아닙니다(status={status}). "
            "사이드카 체크아웃이 그 revision인지 대조할 수 없습니다.",
            source="none",
        )
    pinned = next(
        (
            source.get("revision")
            for source in pins.get("sources", [])
            if source.get("role") == "map"
        ),
        None,
    )
    if not isinstance(pinned, str) or _REVISION.fullmatch(pinned) is None:
        return _unknown_base_image_check("고정된 Map revision을 읽지 못했습니다.", source="none")
    map_root = _repository_root(values, "KOR_TRAVEL_MAP_REPO_DIR", "../kor-travel-map")
    if map_root is None:
        return _unknown_base_image_check(
            "Map 체크아웃 경로를 해석할 수 없습니다.", source="sibling_checkout"
        )
    head = _git_text(map_root, ["rev-parse", "--verify", "HEAD"])
    if head is None or _REVISION.fullmatch(head) is None:
        return _unknown_base_image_check(
            "Map 체크아웃의 HEAD를 읽을 수 없습니다.", source="sibling_checkout"
        )
    if head != pinned:
        # 살아 있는 체크아웃은 고정된 트리가 아니다. 여기서 docker를 부르면 재구축이
        # 실제로 쓸 Dockerfile이 아닌 다른 것을 관측해 초록불을 켜게 된다.
        return ReadinessCheck(
            id="map_python_base_images",
            state="unknown",
            label_ko=label,
            detail=(
                f"사이드카 체크아웃 HEAD({head[:12]})가 고정 revision({pinned[:12]})과 "
                "다릅니다. 재구축이 실제로 쓸 Dockerfile을 관측할 수 없습니다."
            ),
            source="sibling_checkout",
            evidence={
                "map_root": str(map_root),
                "head_revision": head,
                "pinned_revision": pinned,
            },
        )
    dirty = _git_text(map_root, ["status", "--porcelain=v1", "--untracked-files=normal"])
    if dirty is None:
        return _unknown_base_image_check(
            "Map 체크아웃의 상태를 읽을 수 없습니다.", source="sibling_checkout"
        )
    if dirty:
        return _unknown_base_image_check(
            "Map 체크아웃이 clean하지 않습니다. 고정 revision의 Dockerfile이라고 볼 수 없습니다.",
            source="sibling_checkout",
        )
    try:
        references = map_application_300_python_base_references_from_root(map_root)
    except DeploymentContractError as exc:
        return _unknown_base_image_check(str(exc), source="sibling_checkout")
    if not references or len(references) > _MAX_BASE_REFERENCES:
        return _unknown_base_image_check(
            "Dockerfile에서 읽은 base image 수가 예상 범위를 벗어났습니다.",
            source="sibling_checkout",
        )
    reachable = _docker_daemon_reachable()
    if reachable is not True:
        return _unknown_base_image_check(
            "Docker daemon에 접근할 수 없어 image 보유 여부를 확인하지 못했습니다.",
            source="docker_cli",
        )
    observed: list[dict[str, Any]] = []
    for reference in references:
        observed.append({"reference": reference, "present": _local_image_present(reference)})
    evidence = {
        "map_root": str(map_root),
        "head_revision": head,
        "pinned_revision": pinned,
        "references": observed,
        "required": len(references),
        "present": sum(1 for row in observed if row["present"] is True),
    }
    if any(row["present"] is None for row in observed):
        return ReadinessCheck(
            id="map_python_base_images",
            state="unknown",
            label_ko=label,
            detail="docker image inspect를 완료하지 못했습니다.",
            source="docker_cli",
            evidence=evidence,
        )
    absent = [row["reference"] for row in observed if row["present"] is False]
    if absent:
        return ReadinessCheck(
            id="map_python_base_images",
            state="missing",
            label_ko=label,
            detail=(
                f"필수 base image {len(references)}개 중 {evidence['present']}개만 있습니다. "
                f"없는 것: {', '.join(absent)}. 재구축은 exact base를 요구하므로 지금 "
                "실행하면 실패합니다."
            ),
            source="docker_cli",
            evidence=evidence,
        )
    return ReadinessCheck(
        id="map_python_base_images",
        state="ok",
        label_ko=label,
        detail=f"필수 base image {len(references)}개가 모두 있습니다.",
        source="docker_cli",
        evidence=evidence,
    )


def _summarize(checks: Sequence[ReadinessCheck]) -> dict[str, Any]:
    blocking = sum(1 for check in checks if check.state == "missing")
    warn = sum(1 for check in checks if check.state == "warn")
    unknown = sum(1 for check in checks if check.state == "unknown")
    if blocking:
        state = "blocked"
        text = "지금 재구축을 실행하면 실패합니다. 아래 차단 항목을 먼저 해소하세요."
    elif unknown:
        state = "unverified"
        text = (
            "일부 항목을 확인하지 못했습니다. 화면 값만으로 재구축 성공을 보장할 수 없습니다."
        )
    else:
        state = "ok"
        text = "사전 점검 항목에서 차단 요인을 찾지 못했습니다(성공을 보장하지는 않습니다)."
    return {
        "state": state,
        "blocking_count": blocking,
        "warn_count": warn,
        "unknown_count": unknown,
        "text": text,
    }


def _unknown_payload(detail: str) -> dict[str, Any]:
    """실제 payload와 **같은 모양**을 낸다 — UI가 코드 경로를 하나만 갖게."""

    checks = [
        ReadinessCheck(
            id=check_id,
            state="unknown",
            # 한국어 화면에 `compose_single_file` 같은 내부 id를 그대로 띄우지 않는다.
            label_ko=_CHECK_LABELS.get(check_id, check_id),
            detail=detail,
            source="none",
            evidence={},
        )
        for check_id in _CHECK_ORDER
    ]
    return {
        "schema": DEPLOYMENT_READINESS_SCHEMA,
        "generated_at": _utc_now(),
        "cached": False,
        "cache_age_seconds": 0.0,
        "summary": _summarize(checks),
        "checks": [check.to_payload() for check in checks],
        "unavailable_checks": [dict(entry) for entry in _UNAVAILABLE_CHECKS],
    }


def _probe_deployment_readiness() -> dict[str, Any]:
    values = _effective_values()
    checks = [
        _check_compose_single_file(values),
        _check_sibling_bootstrap_scripts(values),
        _check_pinvi_role_bootstrap_modes(values),
        _check_map_python_base_images(values),
    ]
    return {
        "schema": DEPLOYMENT_READINESS_SCHEMA,
        "generated_at": _utc_now(),
        "cached": False,
        "cache_age_seconds": 0.0,
        "summary": _summarize(checks),
        "checks": [check.to_payload() for check in checks],
        "unavailable_checks": [dict(entry) for entry in _UNAVAILABLE_CHECKS],
    }


def read_deployment_readiness(*, force_refresh: bool = False) -> dict[str, Any]:
    """공개 진입점. **절대 예외를 던지지 않는다.**

    진단 패널이 500을 내면 운영자는 상태를 볼 유일한 창을 잃는다 — 호스트를 읽지
    못하면 ``unknown`` 행으로 정직하게 떨어뜨린다.
    """

    global _CACHE
    cached = _CACHE
    now = time.monotonic()
    # 강제 새로고침이 없으면 운영자는 조치 뒤에도 같은 차단 문구를 계속 본다 — 고쳤는데
    # 화면이 안 바뀌면 조치가 실패한 줄 알게 된다.
    if (
        not force_refresh
        and cached is not None
        and now - cached.monotonic_at < READINESS_TTL_SECONDS
    ):
        return dict(cached.payload) | {
            "cached": True,
            "cache_age_seconds": round(now - cached.monotonic_at, 1),
        }
    # 무한 대기는 금물이다 — 느린 docker 탐침 하나 뒤로 anyio worker가 줄줄이 묶인다.
    if not _LOCK.acquire(timeout=_PROBE_LOCK_WAIT_SECONDS):
        stale = _CACHE
        if stale is not None:
            return dict(stale.payload) | {
                "cached": True,
                "stale": True,
                "cache_age_seconds": round(time.monotonic() - stale.monotonic_at, 1),
            }
        return _unknown_payload("사전 점검이 이미 실행 중입니다. 잠시 후 다시 조회하세요.")
    try:
        cached = _CACHE
        now = time.monotonic()
        if (
            not force_refresh
            and cached is not None
            and now - cached.monotonic_at < READINESS_TTL_SECONDS
        ):
            return dict(cached.payload) | {
                "cached": True,
                "cache_age_seconds": round(now - cached.monotonic_at, 1),
            }
        try:
            payload = _probe_deployment_readiness()
        except Exception as exc:  # noqa: BLE001 - 진단 패널은 절대 500이 되면 안 된다
            return _unknown_payload(f"사전 점검을 수행하지 못했습니다: {exc}")
        _CACHE = _CachedReadiness(payload=payload, monotonic_at=time.monotonic())
        return dict(payload) | {"cached": False, "cache_age_seconds": 0.0}
    finally:
        _LOCK.release()
