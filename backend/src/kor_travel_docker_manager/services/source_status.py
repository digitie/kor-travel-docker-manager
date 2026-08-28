"""배포 provenance 관측 카드 — "지금 뭐가 돌고 있나"를 사람 말로 보여 준다.

설계 정본: ``docs/ktdctl-ui-migration.md`` P3·P4.

이 모듈은 **관측만** 한다. git과 docker를 읽기 전용으로 부르고 아무것도 쓰지 않으며,
어떤 수집 실패도 예외가 아니라 그 행 하나의 ``확인할 수 없습니다``로 떨어진다. 새로운
실패 모드가 카드 전체를 500으로 만들면 운영자는 나머지 다섯 행도 함께 잃는다.

목적은 감사가 아니라 **번역**이다. MATCH/DRIFT/unknown 같은 영어 토큰과 raw SHA 비교는
비전문 관리자에게 무의미하므로, 각 행은 "최신 상태입니다 / 업데이트가 필요합니다 /
확인할 수 없습니다"와 다음 행동으로 옮긴다. 원시 값은 접힌 상세로만 남는다.

**redaction 규약**: payload에 git·docker의 stderr를 절대 싣지 않는다(절대 경로가 그대로
들어 있다). 호스트 계정 배치(uid/gid)도 싣지 않는다 — 운영자에게 행동 지침을 주지
않으면서 공격자에게는 정보다. 그리고 이 모듈은 **실제 ``.env``를 절대 열지 않는다**.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from kor_travel_docker_manager.services.c6c_deployment import (
    MAP_API_IMMUTABLE_COMMAND,
    MAP_API_IMMUTABLE_ENTRYPOINT,
    DeploymentContractError,
    inspect_c6c_image_source_revision,
)
from kor_travel_docker_manager.services.compose_service import get_compose_path
from kor_travel_docker_manager.services.registry import get_project_root
from kor_travel_docker_manager.services.runtime_pin_registry import (
    read_published_runtime_pins,
    utc_timestamp,
)

SOURCE_STATUS_SCHEMA: Final = "ktdm.source-status.v1"
CACHE_TTL_SECONDS: Final = 60.0
_GIT_TIMEOUT_SECONDS: Final = 5.0
_DOCKER_TIMEOUT_SECONDS: Final = 10.0
_MAX_COMPOSE_BYTES: Final = 4 * 1024 * 1024
_MAX_ENV_EXAMPLE_BYTES: Final = 1024 * 1024
_MAX_PROVENANCE_BYTES: Final = 64 * 1024

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_FROM_LINE = re.compile(r"^FROM(?:\s|$)", re.IGNORECASE)
_PINNED_STAGE = re.compile(r"^FROM (python@sha256:[0-9a-f]{64}) AS (builder|runtime)$")

# map/pinvi 체크아웃은 **일부러 뺀다.** pinned rebuild는 sibling 체크아웃이 아니라 자기
# materialization에서 소스를 얻으므로, 여기서 보여 주면 권위 없는 값을 권위처럼 보이게
# 만든다. Geo/Concierge는 반대로 sibling 체크아웃이 곧 배포 소스라 의미가 있다.
_SIBLING_ROLES: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("geo", "KOR_TRAVEL_GEO_REPO_DIR", "../kor-travel-geo", "Kor Travel Geo"),
    ("concierge", "KOR_TRAVEL_CONCIERGE_REPO_DIR", "../kor-travel-concierge", "Concierge"),
)
_RUNNING_IMAGE_TARGETS: Final = (
    ("map", "kor-travel-map-api-latest", "지도 API"),
    ("pinvi", "pinvi-api-latest", "PinVi API"),
)
_MAP_REPO_ENV: Final = "KOR_TRAVEL_MAP_REPO_DIR"
_MAP_REPO_DEFAULT: Final = "../kor-travel-map"

# compose가 요구하지만 **운영자가 공급하지 않는** 변수들. pinned rebuild가 fence/permit
# 디렉터리와 계약 digest를 실행 시점에 주입하므로 `.env.example`에 없는 것이 정상이다.
# 이것을 "누락"으로 세면 카드가 영구 빨간불이 되고, 그러면 사람은 카드를 보지 않게 된다
# — 그 순간 진짜 누락(운영자 공급 값)도 함께 묻힌다.
_REBUILD_INJECTED_SUFFIXES: Final = ("_FENCE_DIR", "_PERMIT_DIR", "_SHA256", "_IMAGE_ID")


def _is_rebuild_injected(name: str) -> bool:
    return name.endswith(_REBUILD_INJECTED_SUFFIXES)

_HUMAN_TEXT: Final[Mapping[str, str]] = {
    "ok": "최신 상태입니다",
    "action_required": "업데이트가 필요합니다",
    "unverified": "확인할 수 없습니다",
}
_LEVEL_BY_STATE: Final[Mapping[str, str]] = {
    "clean": "ok",
    "match": "ok",
    "complete": "ok",
    "recorded": "ok",
    "dirty": "action_required",
    "drift": "action_required",
    "incomplete": "action_required",
    "inconsistent": "action_required",
    "unknown": "unverified",
    "unverified_pin": "unverified",
}

_VERIFY_COMMAND: Final = "sudo -n backend/.venv/bin/ktdctl source-status"


# --- 프로세스 실행 프리미티브 -------------------------------------------------


def _git_environment() -> dict[str, str]:
    """``pinned_runtime_sources``의 하드닝을 그대로 쓰되 네트워크는 뺀다.

    이 모듈은 fetch하지 않으므로 ``GIT_ALLOW_PROTOCOL``을 주지 않는다.
    ``GIT_OPTIONAL_LOCKS=0``은 조회가 사이드카 체크아웃의 인덱스를 갱신(=쓰기)하지
    못하게 막는다.
    """

    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _run_read_only(
    argv: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str] | None:
    """실패는 예외가 아니라 ``None``이다.

    실패 원인 문자열은 절대 payload로 흘리지 않는다 — git/docker stderr에는 절대
    경로가 그대로 들어 있다.
    """

    try:
        return subprocess.run(
            argv,
            cwd="/",
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _humanize(level: str, *, detail: str | None = None, next_action: str = "") -> dict[str, str]:
    return {
        "level": level,
        "text": _HUMAN_TEXT[level] + (f" — {detail}" if detail else ""),
        "next_action": next_action,
    }


def _level_for(state: str) -> str:
    return _LEVEL_BY_STATE.get(state, "unverified")


def _resolve_repository(env_name: str, default_relpath: str) -> Path:
    raw = os.environ.get(env_name, "").strip() or default_relpath
    path = Path(raw)
    if not path.is_absolute():
        path = Path(get_project_root()) / path
    return path.resolve(strict=False)


# --- (1) installer provenance -------------------------------------------------


def read_installer_provenance(*, root: Path | None = None) -> dict[str, Any]:
    """trusted installer가 이미 남기는 두 파일을 읽는다.

    새 provenance를 **기록**하는 것이 아니라 **읽는** 것이다 — installer가 이미
    root:root 0644로 쓰고 있어 비-root backend가 그대로 읽을 수 있다.
    """

    base = root or Path(get_project_root())
    revision_path = base / ".ktdm-source-revision"
    try:
        raw = revision_path.read_bytes()[: _MAX_PROVENANCE_BYTES + 1]
    except FileNotFoundError:
        # legacy rsync 배포본에는 설치 기록이 없다. 오류가 아니라 정상 결과다.
        return {
            "state": "unknown",
            "revision": None,
            "manifest": None,
            "detail": "이 배포본에는 설치 기록이 없습니다(직접 복사한 배포본일 수 있습니다).",
            "human": _humanize("unverified"),
        }
    except OSError:
        return _provenance_unreadable()
    if len(raw) > _MAX_PROVENANCE_BYTES:
        return _provenance_unreadable()
    try:
        revision = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return _provenance_unreadable()
    if _REVISION.fullmatch(revision) is None:
        return _provenance_unreadable()

    manifest_path = base / ".ktdm-release-manifest.json"
    manifest: dict[str, Any] | None = None
    try:
        manifest_raw = manifest_path.read_bytes()[: _MAX_PROVENANCE_BYTES + 1]
        if len(manifest_raw) <= _MAX_PROVENANCE_BYTES:
            document = json.loads(manifest_raw.decode("utf-8"))
            if isinstance(document, dict):
                manifest = document
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        manifest = None

    if manifest is None:
        return {
            "state": "recorded",
            "revision": revision,
            "manifest": None,
            "detail": "설치 기록은 있으나 릴리스 매니페스트가 없습니다.",
            "human": _humanize("ok"),
        }
    manifest_revision = manifest.get("manager_source_revision")
    if manifest_revision != revision:
        # 어느 쪽이 맞는지 고르지 않는다 — 부분 덮어쓰기를 의심해야 하는 상황이다.
        return {
            "state": "inconsistent",
            "revision": revision,
            "manifest_revision": (
                manifest_revision if isinstance(manifest_revision, str) else None
            ),
            "manifest": None,
            "detail": "설치 기록 두 파일이 서로 다른 커밋을 가리킵니다 — 부분 덮어쓰기 의심.",
            "human": _humanize(
                "action_required",
                next_action="sudo -n backend/.venv/bin/ktdctl pin verify",
            ),
        }
    # uid/gid는 의도적으로 버린다: 운영자에게 행동 지침을 주지 않으면서 공격자에게는
    # 호스트 계정 배치를 알려 주는 값이다.
    return {
        "state": "recorded",
        "revision": revision,
        "manifest": {
            "installed_at": manifest.get("installed_at"),
            "backend_distribution": manifest.get("backend_distribution"),
            "backend_wheel_sha256": manifest.get("backend_wheel_sha256"),
        },
        "detail": None,
        "human": _humanize("ok"),
    }


def _provenance_unreadable() -> dict[str, Any]:
    return {
        "state": "unknown",
        "revision": None,
        "manifest": None,
        "detail": "설치 기록을 읽을 수 없거나 형식이 올바르지 않습니다.",
        "human": _humanize("unverified"),
    }


# --- (2) sibling checkout -----------------------------------------------------


def sibling_checkout_row(
    role: str, env_name: str, default_relpath: str, label: str
) -> dict[str, Any]:
    path = _resolve_repository(env_name, default_relpath)
    base_args = [
        "/usr/bin/git",
        "--no-optional-locks",
        "-c",
        "core.hooksPath=/dev/null",
        # backend uid와 체크아웃 소유자가 다르면 git이 dubious ownership으로 거부한다.
        # global config를 /dev/null로 막아 뒀으므로 이 한 경로만 명시 허용한다.
        "-c",
        f"safe.directory={path}",
        "-C",
        str(path),
    ]
    unknown = {
        "role": role,
        "label": label,
        "state": "unknown",
        "revision": None,
        "detail": "작업 사본을 확인할 수 없습니다(경로가 없거나 git 저장소가 아닙니다).",
        "human": _humanize("unverified", next_action=_VERIFY_COMMAND),
    }

    head = _run_read_only(
        [*base_args, "rev-parse", "HEAD"], timeout=_GIT_TIMEOUT_SECONDS, env=_git_environment()
    )
    if head is None or head.returncode != 0:
        return unknown
    revision = head.stdout.strip()
    if _REVISION.fullmatch(revision) is None:
        return unknown

    # untracked는 세지 않는다 — 빌드 산출물 노이즈로 상시 dirty가 되면 배지가 무의미하다.
    status = _run_read_only(
        [*base_args, "status", "--porcelain", "--untracked-files=no"],
        timeout=_GIT_TIMEOUT_SECONDS,
        env=_git_environment(),
    )
    if status is None or status.returncode != 0:
        # revision은 읽었지만 그것이 실제로 도는 코드인지 모른다. 아는 척하지 않는다.
        return {**unknown, "detail": "작업 사본의 변경 여부를 확인할 수 없습니다."}
    dirty = status.stdout.strip() != ""
    state = "dirty" if dirty else "clean"
    return {
        "role": role,
        "label": label,
        "state": state,
        "revision": revision,
        "detail": "커밋되지 않은 변경이 있습니다." if dirty else None,
        "human": _humanize(
            _level_for(state),
            next_action="git -C <체크아웃> status" if dirty else "",
        ),
    }


# --- (3) running image revision ----------------------------------------------


def running_image_row(
    role: str,
    container_name: str,
    label: str,
    *,
    pinned_revision: str | None,
    pin_trustworthy: bool,
) -> dict[str, Any]:
    """실행 중 이미지의 source revision. **어떤 실패도 예외가 아니다.**

    Geo/Concierge 이미지에는 OCI revision 라벨이 아예 없다(그 라벨은 pinned 빌드
    경로만 붙인다). 그래서 ``unknown``을 정상 결과로 취급하지 않으면 멀쩡한
    호스트에서 카드가 늘 실패한다.
    """

    def unknown(detail: str) -> dict[str, Any]:
        return {
            "role": role,
            "label": label,
            "state": "unknown",
            "image_id": None,
            "revision": None,
            "pinned_revision": pinned_revision if pin_trustworthy else None,
            "detail": detail,
            "human": _humanize("unverified", next_action=_VERIFY_COMMAND),
        }

    inspected = _run_read_only(
        ["docker", "container", "inspect", "--format={{.Image}}", "--", container_name],
        timeout=_DOCKER_TIMEOUT_SECONDS,
        env=None,
    )
    if inspected is None or inspected.returncode != 0:
        return unknown("컨테이너가 실행 중이 아니거나 조회할 수 없습니다.")
    image_id = inspected.stdout.strip()
    if _IMAGE_ID.fullmatch(image_id) is None:
        return unknown("컨테이너가 실행 중이 아니거나 조회할 수 없습니다.")

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        # 기본 경로의 docker 조회는 timeout을 걸지 않는다 — daemon이 물리면 요청
        # 스레드가 영구히 잡힌다. 읽기 카드가 그 대가를 치를 이유가 없다.
        return subprocess.run(
            argv,
            cwd=get_project_root(),
            text=True,
            capture_output=True,
            check=False,
            timeout=_DOCKER_TIMEOUT_SECONDS,
        )

    try:
        revision = inspect_c6c_image_source_revision(
            image_id,
            label="org.opencontainers.image.revision",
            cwd=get_project_root(),
            runner=runner,
        )
    except (DeploymentContractError, OSError, subprocess.SubprocessError, ValueError):
        # TimeoutExpired는 SubprocessError이지 OSError가 아니라서 반드시 함께 잡아야 한다.
        return {
            **unknown("실행 중 이미지에 source revision 라벨이 없거나 조회에 실패했습니다."),
            "image_id": image_id,
        }

    if not pin_trustworthy or pinned_revision is None:
        return {
            "role": role,
            "label": label,
            "state": "unverified_pin",
            "image_id": image_id,
            "revision": revision,
            "pinned_revision": None,
            "detail": "고정 revision이 권위 있는 값이 아니라 대조할 수 없습니다.",
            "human": _humanize(
                "unverified", next_action="sudo -n backend/.venv/bin/ktdctl pin verify"
            ),
        }
    state = "match" if revision == pinned_revision else "drift"
    return {
        "role": role,
        "label": label,
        "state": state,
        "image_id": image_id,
        "revision": revision,
        "pinned_revision": pinned_revision,
        "detail": None if state == "match" else "실행 중 이미지가 고정 revision과 다릅니다.",
        "human": _humanize(
            _level_for(state),
            next_action=(
                "" if state == "match" else "sudo -n backend/.venv/bin/ktdctl pin show"
            ),
        ),
    }


# --- (4) 계약 drift: Map 실행 경계 --------------------------------------------


def map_execution_boundary_row() -> dict[str, Any]:
    """이미지의 실행 경계가 강제 계약과 같은지 본다.

    ``{{json .Config}}`` 전체를 뜨지 않는다 — 거기에는 Env가 통째로 들어 있고 Map
    API의 runtime DSN·서명 secret이 전부 그 안에 있다. 필요한 두 필드만 따로
    조회해 애초에 메모리에 넣지 않는다.
    """

    title = "지도 API 실행 경계"
    expected = {
        "entrypoint": list(MAP_API_IMMUTABLE_ENTRYPOINT),
        "command": MAP_API_IMMUTABLE_COMMAND,
    }

    def query(field: str) -> tuple[bool, Any]:
        completed = _run_read_only(
            [
                "docker",
                "container",
                "inspect",
                f"--format={{{{json .Config.{field}}}}}",
                "--",
                "kor-travel-map-api-latest",
            ],
            timeout=_DOCKER_TIMEOUT_SECONDS,
            env=None,
        )
        if completed is None or completed.returncode != 0:
            return False, None
        try:
            value = json.loads(completed.stdout.strip() or "null")
        except json.JSONDecodeError:
            return False, None
        if value is not None and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            return False, None
        return True, value

    entrypoint_ok, entrypoint = query("Entrypoint")
    command_ok, command = query("Cmd")
    if not entrypoint_ok or not command_ok:
        return {
            "id": "map_execution_boundary",
            "title": title,
            "state": "unknown",
            "expected": expected,
            "observed": {"entrypoint": None, "command": None},
            "detail": "컨테이너가 실행 중이 아니거나 조회할 수 없습니다.",
            "human": _humanize("unverified", next_action=_VERIFY_COMMAND),
        }
    matches = list(entrypoint or []) == list(MAP_API_IMMUTABLE_ENTRYPOINT) and (
        command == MAP_API_IMMUTABLE_COMMAND
    )
    state = "match" if matches else "drift"
    return {
        "id": "map_execution_boundary",
        "title": title,
        "state": state,
        "expected": expected,
        "observed": {"entrypoint": entrypoint, "command": command},
        "detail": None if matches else "실행 중 이미지의 실행 경계가 기대 계약과 다릅니다.",
        "human": _humanize(
            _level_for(state),
            next_action=(
                ""
                if matches
                else "지도 저장소가 Dockerfile 실행 경계를 바꿨습니다. 재구축 전에 Map PR을 확인하세요."
            ),
        ),
    }


# --- (5) 계약 drift: Map Dockerfile 구조 --------------------------------------


def _dockerfile_base_contract(text: str) -> tuple[str, str | None]:
    """rebuild가 쓰는 판독 규칙과 **같은** 조건을 본다."""

    from_lines = tuple(
        line.strip() for line in text.splitlines() if _FROM_LINE.match(line.strip())
    )
    if len(from_lines) != 2:
        return "drift", None
    stages = tuple(_PINNED_STAGE.fullmatch(line) for line in from_lines)
    if any(stage is None for stage in stages):
        return "drift", None
    resolved = tuple(stage.groups() for stage in stages if stage is not None)
    if {name for _, name in resolved} != {"builder", "runtime"}:
        return "drift", None
    digests = {digest for digest, _ in resolved}
    if len(digests) != 1:
        return "drift", None
    return "ok", digests.pop()


def map_dockerfile_structure_row(
    *,
    pinned_revision: str | None = None,
    pin_trustworthy: bool = False,
) -> dict[str, Any]:
    """**사이드카 체크아웃만** 본다 — 고정된 트리는 root 전용이라 읽을 수 없다.

    강제 지점은 materialized pinned source를 읽는다. 읽기 전용 카드는 그것을
    materialize할 수 없으므로 이 행은 사이드카 체크아웃을 보며, 그 사실을 payload에
    ``scope``로 명시한다. 고정 candidate를 검증했다고 주장하지 않는다.

    **체크아웃이 고정 revision일 때만 판정한다.** 개발 호스트의 사이드카는 대개
    작업 브랜치에 있고 거기서 나온 ``drift``는 "고정된 Dockerfile이 계약을 어겼다"는
    뜻이 아니다. 그런 빨간불은 사람이 없는 문제를 쫓게 만든다.
    """

    title = "지도 Dockerfile 구조 계약"
    map_root = _resolve_repository(_MAP_REPO_ENV, _MAP_REPO_DEFAULT)

    def unverified(detail: str) -> dict[str, Any]:
        return {
            "id": "map_dockerfile_structure",
            "title": title,
            "scope": "sibling_checkout",
            "state": "unknown",
            "files": [],
            "detail": detail,
            "human": _humanize("unverified", next_action=_VERIFY_COMMAND),
        }

    if not pin_trustworthy or pinned_revision is None:
        return unverified("고정 revision이 권위 있는 값이 아니라 대조할 수 없습니다.")
    head = _run_read_only(
        [
            "/usr/bin/git",
            "--no-optional-locks",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            f"safe.directory={map_root}",
            "-C",
            str(map_root),
            "rev-parse",
            "HEAD",
        ],
        timeout=_GIT_TIMEOUT_SECONDS,
        env=_git_environment(),
    )
    if head is None or head.returncode != 0:
        return unverified(
            "지도 저장소 작업 사본이 없어 확인할 수 없습니다(운영 호스트에서는 정상입니다)."
        )
    head_revision = head.stdout.strip()
    if _REVISION.fullmatch(head_revision) is None:
        return unverified("지도 저장소 작업 사본의 HEAD를 읽을 수 없습니다.")
    if head_revision != pinned_revision:
        return {
            **unverified(
                f"작업 사본 HEAD({head_revision[:12]})가 고정 revision"
                f"({pinned_revision[:12]})과 달라 판정하지 않습니다."
            ),
            "head_revision": head_revision,
            "pinned_revision": pinned_revision,
        }

    files: list[dict[str, Any]] = []
    states: list[str] = []
    for name in ("api.Dockerfile", "dagster.Dockerfile"):
        path = map_root / "docker" / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            files.append({"name": name, "state": "unknown", "base_digest": None})
            states.append("unknown")
            continue
        state, digest = _dockerfile_base_contract(text)
        files.append(
            {"name": name, "state": "match" if state == "ok" else "drift", "base_digest": digest}
        )
        states.append("match" if state == "ok" else "drift")

    if all(state == "unknown" for state in states):
        row_state = "unknown"
        detail = "지도 저장소 작업 사본이 없어 확인할 수 없습니다(운영 호스트에서는 정상입니다)."
    elif any(state == "drift" for state in states):
        row_state = "drift"
        detail = "Dockerfile의 base image 구조가 재구축이 요구하는 계약과 다릅니다."
    elif any(state == "unknown" for state in states):
        row_state = "unknown"
        detail = "일부 Dockerfile을 읽을 수 없습니다."
    else:
        row_state = "match"
        detail = None
    return {
        "id": "map_dockerfile_structure",
        "title": title,
        "scope": "sibling_checkout",
        "state": row_state,
        "files": files,
        "detail": detail,
        "human": _humanize(
            _level_for(row_state),
            next_action=(
                "" if row_state == "match" else "지도 저장소의 docker/ Dockerfile을 확인하세요."
            ),
        ),
    }


# --- (6) 환경 변수 완결성 -----------------------------------------------------


def environment_completeness_card() -> dict[str, Any]:
    """compose가 요구하는 필수 변수와 ``.env.example``의 문서화를 대조한다.

    **실제 ``.env``는 절대 열지 않는다.** 그 파일에는 machine secret이 전부 들어
    있고, 이 카드가 필요한 정보는 "이름의 집합"뿐이라 ``.env.example``로 완결된다.
    """

    title = "환경 변수 완결성"
    unknown = {
        "id": "environment_completeness",
        "title": title,
        "state": "unknown",
        "required_count": 0,
        "missing": [],
        "documented_but_unused": [],
        "detail": "Compose 파일 또는 .env.example을 읽을 수 없습니다.",
        "human": _humanize("unverified"),
    }
    try:
        raw = Path(get_compose_path()).read_bytes()[: _MAX_COMPOSE_BYTES + 1]
        if len(raw) > _MAX_COMPOSE_BYTES:
            return unknown
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return unknown

    # Compose에서 `$$`는 리터럴 `$`의 이스케이프다 — 먼저 지워야 `$${FOO:?}`를 필수
    # 변수로 오인하지 않는다.
    scanned = text.replace("$$", "")
    required = sorted({match.group(1) for match in _REQUIRED_VAR.finditer(scanned)})

    try:
        example_raw = (Path(get_project_root()) / ".env.example").read_bytes()[
            : _MAX_ENV_EXAMPLE_BYTES + 1
        ]
        if len(example_raw) > _MAX_ENV_EXAMPLE_BYTES:
            return unknown
        example_text = example_raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return unknown

    documented: set[str] = set()
    for line in example_text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ENV_ASSIGNMENT.match(line)
        if match:
            # 이름만 담는다. 값 쪽은 읽지도 저장하지도 않는다.
            documented.add(match.group(1))

    undocumented = sorted(set(required) - documented)
    # 조치 가능한 것과 정상인 것을 나눈다 — 합쳐 세면 카드가 늘 빨간불이라 무의미해진다.
    missing = [name for name in undocumented if not _is_rebuild_injected(name)]
    injected = [name for name in undocumented if _is_rebuild_injected(name)]
    # 많은 .env.example 항목은 compose가 아니라 사이드카 저장소가 읽는다 — 이 목록만으로
    # drift를 단정하지 않고 참고로만 준다.
    advisory = sorted(name for name in documented if f"${{{name}" not in text)
    state = "complete" if not missing else "incomplete"
    return {
        "id": "environment_completeness",
        "title": title,
        "state": state,
        "required_count": len(required),
        "missing": missing,
        "injected_at_rebuild": injected,
        "documented_but_unused": advisory,
        "detail": (
            None
            if state == "complete"
            else f"운영자가 공급해야 하는 변수 {len(missing)}개가 .env.example에 없습니다."
        ),
        "human": _humanize(
            _level_for(state),
            next_action="" if state == "complete" else ".env.example에 누락 변수를 추가하세요.",
        ),
    }


# --- 요약과 캐시 --------------------------------------------------------------


def _summarize(rows: list[dict[str, Any]]) -> dict[str, str]:
    """확인 불가보다 조치 필요가 먼저다 — 후자에는 지금 누를 수 있는 행동이 있다."""

    levels = [row.get("human", {}).get("level", "unverified") for row in rows]
    if "action_required" in levels:
        next_action = next(
            (
                row["human"]["next_action"]
                for row in rows
                if row.get("human", {}).get("level") == "action_required"
                and row.get("human", {}).get("next_action")
            ),
            "",
        )
        return _humanize("action_required", next_action=next_action)
    if "unverified" in levels:
        return _humanize("unverified", next_action=_VERIFY_COMMAND)
    return _humanize("ok")


def _row_stub(identifier: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "state": "unknown",
        "detail": "이 항목을 수집하지 못했습니다.",
        "human": _humanize("unverified", next_action=_VERIFY_COMMAND),
    }


def _safe(identifier: str, collector) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """새 실패 모드가 카드 전체를 죽이지 않게 행 단위로 격리한다."""

    try:
        return collector()
    except Exception:  # noqa: BLE001 - 관측 카드는 어떤 이유로도 500이 되면 안 된다
        return _row_stub(identifier)


def _collect_uncached() -> dict[str, Any]:
    pins = _safe("pins", read_published_runtime_pins)
    pin_trustworthy = pins.get("status") == "ok"
    pinned_by_role = {
        source.get("role"): source.get("revision")
        for source in pins.get("sources", [])
        if isinstance(source, dict)
    }

    manager = _safe("manager", read_installer_provenance)
    checkouts = [
        _safe(role, lambda r=role, e=env, d=default, la=label: sibling_checkout_row(r, e, d, la))
        for role, env, default, label in _SIBLING_ROLES
    ]
    running_images = [
        _safe(
            role,
            lambda r=role, c=container, la=label: running_image_row(
                r,
                c,
                la,
                pinned_revision=pinned_by_role.get(r),
                pin_trustworthy=pin_trustworthy,
            ),
        )
        for role, container, label in _RUNNING_IMAGE_TARGETS
    ]
    contracts = [
        _safe("map_execution_boundary", map_execution_boundary_row),
        _safe(
            "map_dockerfile_structure",
            lambda: map_dockerfile_structure_row(
                pinned_revision=pinned_by_role.get("map"),
                pin_trustworthy=pin_trustworthy,
            ),
        ),
    ]
    environment = _safe("environment_completeness", environment_completeness_card)

    rows = [manager, *checkouts, *running_images, *contracts, environment]
    return {
        "schema": SOURCE_STATUS_SCHEMA,
        "collected_at": utc_timestamp(),
        "cached": False,
        "cache_ttl_seconds": int(CACHE_TTL_SECONDS),
        "manager": manager,
        "checkouts": checkouts,
        "running_images": running_images,
        "contracts": contracts,
        "environment": environment,
        "summary": _summarize(rows),
    }


_cache_lock = threading.Lock()
_inflight_lock = threading.Lock()
_cache: tuple[float, dict[str, Any]] | None = None


def clear_source_status_cache() -> None:
    """테스트와 수동 새로고침용."""

    global _cache
    with _cache_lock:
        _cache = None


def collect_source_status(*, force_refresh: bool = False) -> dict[str, Any]:
    """TTL 캐시 + single-flight. 캐시 사전은 깊은 복사로만 내보낸다.

    그대로 돌려주면 라우터·직렬화 단계의 변형이 캐시를 오염시킨다. 동시 새로고침이
    N번의 git/docker 호출로 증폭되지 않게 단일 비행으로 묶는다.
    """

    global _cache
    requested_at = time.monotonic()
    with _cache_lock:
        cached = _cache
    if cached is not None and not force_refresh and requested_at - cached[0] < CACHE_TTL_SECONDS:
        return copy.deepcopy(cached[1]) | {"cached": True}

    with _inflight_lock:
        with _cache_lock:
            cached = _cache
        # 기다리는 동안 다른 스레드가 새로 채웠다면 force_refresh라도 그 결과를 받는다.
        if cached is not None and (
            cached[0] > requested_at
            or (not force_refresh and time.monotonic() - cached[0] < CACHE_TTL_SECONDS)
        ):
            return copy.deepcopy(cached[1]) | {"cached": True}
        payload = _collect_uncached()
        with _cache_lock:
            _cache = (time.monotonic(), payload)
        return copy.deepcopy(payload) | {"cached": False}
