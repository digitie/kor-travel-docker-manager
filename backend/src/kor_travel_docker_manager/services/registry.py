import os
import posixpath
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import yaml

from kor_travel_docker_manager.services.yaml_strict import (
    load_yaml_rejecting_duplicate_keys,
)

#: 이 둘이 targets 파일의 **자리**를 바꾼다. 개발 checkout에서는 정당한 편의이고,
#: trusted 설치본에서는 그렇지 않다 — 아래 `get_targets_config_path`가 가른다.
TARGETS_FILE_ENV: Final = "KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE"
PROJECT_ROOT_ENV: Final = "KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT"

#: targets 문서의 상한. 이 파일은 손으로 편집하는 수백 줄짜리 선언이고, 그보다
#: 크다면 다른 것을 읽고 있다는 뜻이다 — 읽기 전에 멈춘다.
_MAX_TARGETS_BYTES: Final = 1 << 20

_REQUIRED_CONTAINER_FIELDS = (
    "compose_service",
    "name",
    "display_name",
    "role",
    "connection",
    "expected_ports",
)


#: 외부 compose 프로젝트를 가리키는 target의 선언 필드.
_EXTERNAL_PROJECT_FIELDS: Final = ("project", "working_dir", "config_files")


@dataclass(frozen=True)
class ExternalProject:
    """Manager 자신의 compose가 아닌 **형제 프로젝트**의 좌표.

    `docker compose -p <project> --project-directory <working_dir> -f <f1> -f <f2>`를
    그대로 재구성할 수 있는 최소 집합이다. 실행 중인 컨테이너의
    `com.docker.compose.*` 라벨에서 읽은 값과 같은 모양이라, 살아 있는 프로젝트를
    그대로 선언에 옮길 수 있다.
    """

    project: str
    working_dir: str
    config_files: tuple[str, ...]

    def compose_file_arguments(self) -> list[str]:
        """`-f` 인자열. 경로는 `working_dir` 기준 상대 경로다."""

        arguments: list[str] = []
        for name in self.config_files:
            arguments.extend(["-f", name])
        return arguments


@dataclass(frozen=True)
class ServiceGroup:
    """한 번의 `docker compose` 호출로 다룰 수 있는 서비스 묶음.

    `external`이 `None`이면 Manager 자신의 프로젝트다. target이 여러 프로젝트에
    걸치면(예: `airport`가 `airport-db`에 의존) 묶음이 여럿 나오고, 호출하는 쪽이
    **묶음마다 한 번씩** 명령을 돌려야 한다 — 그것이 단일 프로젝트 전제를 깨는
    지점이고, 평평한 이름 목록으로는 표현할 수 없다.
    """

    external: ExternalProject | None
    services: tuple[str, ...]

    @property
    def project_label(self) -> str:
        return self.external.project if self.external is not None else "kor-travel-docker-manager"


class TargetsConfigError(ValueError):
    """`config/docker-targets.yml` 스키마·참조 무결성 검증 실패 전용 타입.

    적대적 리뷰 2건(item2-targets-validate 재검토) 반영: `cli.py`의 `main()`은
    이 타입만 잡아 "config가 깨졌으니 깔끔한 한 줄 메시지로 안내"하는 fail-open
    경로를 태운다. 예전에는 `main()`이 bare `ValueError`를 통째로 잡았는데,
    `compose_service.py`/`c6c_deployment.py`의 내부 불변식 위반(스테이지 값 오류,
    재시도 횟수 음수 등, 버그이지 config 오타가 아님)까지 같이 삼켜 "config
    오류처럼 보이는 exit 1"로 둔갑시킬 위험이 있었다 — 오늘은 그 두 사이트가 이미
    자기 자신의 좁은 `except`로 먼저 잡혀 실제로 새지는 않지만, 새 명령이 추가될
    때마다 반복 확인할 근거가 없었다. `ValueError`를 상속하므로 기존
    `except ValueError:` 호출부(레지스트리 자신의 `resolve_target_name`/
    `container_id_to_compose_service` 같은 "잘못된 사용자 입력" 오류를 잡던
    `_cmd_status`/`_cmd_ensure`/`_cmd_action` 등)는 전혀 바뀌지 않는다 — 이
    서브클래스는 오직 `main()`/`_cmd_targets_validate`/
    `MetricsCollector.__init__`이 정확히 무엇을 fail-open으로 삼키는지 좁히기
    위한 것이다.
    """


def get_project_root() -> str:
    configured = os.environ.get("KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT", "").strip()
    if configured:
        return os.path.abspath(configured)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current_dir, "../../../../"))


def get_targets_config_path() -> str:
    """targets 문서의 자리. **trusted 설치본에서는 env로 옮길 수 없다.**

    이 파일은 컨테이너 이름·compose 서비스·기대 포트를 정하고, GM-17이 bind
    allowlist까지 여기로 옮기려 한다 — 그 순간 이 파일은 **"어떤 host 경로가
    production 컨테이너에 마운트돼도 되는가"를 결정하는 보안 경계**가 된다.
    종전에는 `KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE` 하나로 그 자리를 아무 데로나
    돌릴 수 있었고 소유권·권한 검증은 하나도 없었다(GM-17 검증 노트 (b)).

    그래서 trusted 설치본에서는 자리를 **핀으로 고정**한다. 자리를 옮기려는 시도를
    조용히 무시하지 않고 거절하는 이유: 무시하면 "왜 내 설정이 안 먹지"가 되고,
    그 물음이 운영자를 다시 env로 데려간다. 거절은 그 자리에서 이유를 말한다.

    개발 checkout에서는 종전과 같다 — 거기서 이 override는 정당한 편의다.
    """

    from kor_travel_docker_manager.services.trusted_install import (
        TRUSTED_INSTALL_ROOT,
        running_from_trusted_install_root,
    )

    if not running_from_trusted_install_root():
        return os.environ.get(
            TARGETS_FILE_ENV,
            os.path.join(get_project_root(), "config", "docker-targets.yml"),
        )

    pinned = TRUSTED_INSTALL_ROOT / "config" / "docker-targets.yml"
    # 같은 자리를 가리키는 override는 무해하므로 막지 않는다 — installer나 launcher가
    # 명시적으로 넘기는 경우가 있고, 그것까지 거절하면 정당한 호출을 깨뜨린다.
    for name, expected in ((TARGETS_FILE_ENV, pinned), (PROJECT_ROOT_ENV, TRUSTED_INSTALL_ROOT)):
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            redirects = Path(raw).resolve() != expected.resolve()
        except OSError:
            redirects = True
        if redirects:
            raise TargetsConfigError(
                f"{name}은 trusted 설치본에서 docker target config의 자리를 바꿀 수 없다 "
                f"(요청: {raw}, 고정: {expected}). 이 문서는 컨테이너 정체와 "
                "bind 허용 범위를 정하므로 설치본 밖에서 주입될 수 없다"
            )
    return str(pinned)


def _assert_trusted_parents(path: Path) -> None:
    """설치본에서 targets 문서의 **조상 디렉터리**가 root 소유·비쓰기·비심링크인가.

    `O_NOFOLLOW`는 마지막 조각만 막는다. `config/`가 심링크이거나 다른 사용자가 쓸 수
    있으면, 파일 자체를 아무리 검증해도 통째로 갈아끼울 수 있다 — 검증이 자기완결적이지
    않고 검증하지 않는 불변식에 전부를 거는 상태가 된다. 이 파일이 "운영자가 고치는
    설정"이 된 이상 누군가 `config/`의 소유권을 backend 계정으로 옮기는 것이 개연성
    있는 동작이 됐고, 그 순간 아무 오류 없이 보호가 사라진다.
    """

    from kor_travel_docker_manager.services.trusted_install import TRUSTED_INSTALL_ROOT

    root = TRUSTED_INSTALL_ROOT.resolve()
    for parent in path.resolve().parents:
        try:
            metadata = parent.lstat()
        except OSError as exc:
            raise TargetsConfigError(
                f"docker target config의 상위 디렉터리를 확인할 수 없다: {parent} ({exc})"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise TargetsConfigError(
                f"docker target config의 상위가 심링크다: {parent}"
            )
        if metadata.st_uid != 0:
            raise TargetsConfigError(
                f"docker target config의 상위가 root 소유가 아니다: {parent} "
                f"(uid {metadata.st_uid})"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise TargetsConfigError(
                f"docker target config의 상위가 group/other 쓰기 가능이다: {parent} "
                f"(mode {stat.S_IMODE(metadata.st_mode):04o})"
            )
        if parent == root:
            # 설치 루트까지만 본다. 그 위(`/opt`, `/`)는 이 프로그램이 소유하지 않는
            # 호스트의 몫이고, 거기까지 요구하면 정당한 호스트 구성을 거부하게 된다.
            return


def _read_targets_bytes(path: str) -> bytes:
    """targets 문서를 **검증된 descriptor**로 읽는다.

    `legacy_override_retirement._read_legacy_import_bytes`에서 **열기 방식만** 가져왔다:
    `O_NOFOLLOW`로 열고 경로가 아니라 **열린 fd를** `fstat`한다. 경로를 두 번
    보면(검사 한 번, 열기 한 번) 그 사이에 바꿔치기할 수 있다.

    **그쪽과 같은 모양은 아니다**(GM-17 A 적대 리뷰 M-1 정정). 그 함수는 `nlink != 1`과
    `S_IMODE == 0o600`을 **무조건** 강제한다. 여기서는 trusted 설치본에서만 건다 —
    개발 checkout의 파일은 사용자 소유가 정상이고 거기서 root를 요구하면 이 로더를
    부르는 모든 명령이 죽기 때문이다. 즉 보호 강도가 실행 형태에 따라 다르다.

    **부모 디렉터리도 함께 본다.** `O_NOFOLLOW`는 경로의 **마지막 조각**에만 걸리므로,
    파일만 검증하면 `/opt/kor-travel-docker-manager/config`가 통째로 바꿔치기된 경우를
    놓친다. 저장소의 선례 셋(`runtime_execution_registry._assert_registry_parent`,
    `trusted_manager_source_revision`, `map_application_300_candidate._validate_parent_metadata`)
    과 `docs/decisions.md`의 typed path 기준이 전부 부모를 본다.
    """

    from kor_travel_docker_manager.services.trusted_install import (
        running_from_trusted_install_root,
    )

    trusted = running_from_trusted_install_root()
    if trusted:
        _assert_trusted_parents(Path(path))
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TargetsConfigError(
            f"docker target config를 안전하게 열 수 없다: {path} ({exc})"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TargetsConfigError(
                f"docker target config가 평범한 파일이 아니다: {path}"
            )
        if metadata.st_size > _MAX_TARGETS_BYTES:
            raise TargetsConfigError(
                f"docker target config가 지원 크기를 넘는다: {path} "
                f"({metadata.st_size} > {_MAX_TARGETS_BYTES})"
            )
        if trusted:
            if metadata.st_uid != 0:
                raise TargetsConfigError(
                    f"docker target config가 root 소유가 아니다: {path} "
                    f"(uid {metadata.st_uid}). 설치본의 데이터만 신뢰한다"
                )
            if metadata.st_nlink != 1:
                # 하드링크가 있으면 다른 이름으로 같은 내용을 바꿔 쓸 수 있다.
                raise TargetsConfigError(
                    f"docker target config에 하드링크가 있다: {path} "
                    f"(nlink {metadata.st_nlink})"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                raise TargetsConfigError(
                    f"docker target config가 group/other 쓰기 가능이다: {path} "
                    f"(mode {stat.S_IMODE(metadata.st_mode):04o})"
                )
        payload = bytearray()
        while len(payload) <= _MAX_TARGETS_BYTES:
            chunk = os.read(descriptor, min(65_536, _MAX_TARGETS_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_TARGETS_BYTES:
            raise TargetsConfigError(f"docker target config가 지원 크기를 넘는다: {path}")
        return bytes(payload)
    except OSError as exc:
        raise TargetsConfigError(
            f"docker target config를 안전하게 읽을 수 없다: {path} ({exc})"
        ) from exc
    finally:
        os.close(descriptor)


@lru_cache(maxsize=1)
def load_targets_config() -> dict[str, Any]:
    # 캐시는 프로세스당 한 번이다 — 첫 로드 이후 파일이 바뀌어도 다시 읽지 않는다.
    #
    # **이것이 무해하지 않다**(GM-17 A 적대 리뷰 M-2). 종전에는 이 캐시에 컨테이너
    # 정체만 있었지만 지금은 bind allowlist가 들어 있고, 상주 root uvicorn backend는
    # installer가 **재기동하지 않는다**(`deploy/systemd/ktdm-backend.service`). 그래서
    # 운영자가 위험한 bind를 설정에서 지워도 그 프로세스는 재기동 전까지 옛 목록으로
    # candidate를 통과시킨다 — 취소가 즉시 반영되지 않는다. 그 창을 닫는 것은
    # 별도 작업이고(`docs/tasks.md` 후속), 여기서는 최소한 파생 캐시를 없애
    # "두 캐시가 어긋나 더 낡은 값을 본다"는 층은 제거했다.
    path = get_targets_config_path()
    try:
        text = _read_targets_bytes(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TargetsConfigError(f"docker target config가 UTF-8이 아니다: {path}") from exc
    config = load_yaml_rejecting_duplicate_keys(text) or {}

    if not isinstance(config.get("containers"), dict):
        raise TargetsConfigError("docker target config must define containers")
    if not isinstance(config.get("targets"), dict):
        raise TargetsConfigError("docker target config must define targets")
    if not isinstance(config.get("dependency_order"), list):
        raise TargetsConfigError("docker target config must define dependency_order")
    _validate_targets_config(config, label=os.path.basename(path))
    return config


def _require_list_field(
    spec: dict[str, Any], field: str, *, target_id: str, label: str
) -> list[Any]:
    """`field`가 리스트가 아니라 스칼라(예: 대괄호를 빼먹은 `depends_on: geo`)면
    글자 단위로 순회돼 `unknown target 'g'`처럼 원인을 짐작할 수 없는 메시지가
    나간다 — 리스트 여부를 먼저 검사해 그 자리에서 바로 지목한다."""

    value = spec.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise TargetsConfigError(
            f"{label} targets.{target_id}.{field}: must be a list, got "
            f"{type(value).__name__}"
        )
    return value


#: `(compose service, container_path, read_only) -> compose 문서에 적힌 host source`
ComposeBindAllowlist = Mapping[tuple[str, str, bool], str]

_BIND_FIELDS: Final = ("container_path", "read_only", "source")


def _validate_compose_binds(config: dict[str, Any], *, label: str) -> None:
    """`compose_binds` 절의 **구조**를 검증한다 (GM-17 본작업 A).

    이 절은 보안 경계다 — 여기 없는 bind는 candidate 검증이 거부한다. 그래서
    형태가 어긋난 항목을 조용히 건너뛰면 **경계에 구멍이 뚫리는 게 아니라 그 반대로,
    있어야 할 항목이 사라져 정상 배포가 거부된다.** 어느 쪽이든 조용하면 안 되므로
    fail-close한다.

    **여기서 값의 정책은 보지 않는다.** 이관은 자리만 옮기는 일이고, 새 규칙을
    더하면 지금 유효한 항목이 거부될 수 있다(예: `rustfs-init`은 manager 설치 경로를
    container target으로 쓴다 — "manager 경로 금지" 같은 규칙을 순진하게 넣으면
    그것이 깨진다). 정책 강화는 별도 작업으로 남긴다.
    """

    # **절이 통째로 없는 것을 통과시키지 않는다**(GM-17 A 적대 리뷰 H-2).
    # 종전에는 `if raw is None: return`이었는데, 그러면 `compose_bind:`(단수) 같은
    # 오타 하나로 `ktdctl targets validate`는 OK를 찍고 그 뒤 **모든 배포가**
    # `bind is not in the canonical baseline`으로 죽는다 — 이 함수 자신의 docstring이
    # 금지한 바로 그 모양이고, 절 전체가 사라지는 가장 큰 경우에만 규칙이 빠져 있었다.
    if "compose_binds" not in config:
        raise TargetsConfigError(
            f"{label}: compose_binds 절이 없다 — 이 절이 없으면 모든 operator bind가 "
            "baseline 밖이 되어 배포가 전부 거부된다. 절 이름 오타를 의심하라"
        )
    containers = config.get("containers") or {}
    manager_services = {
        str(spec["compose_service"])
        for spec in containers.values()
        if isinstance(spec, dict) and not spec.get("external_project")
    }
    external_only_services = {
        str(spec["compose_service"])
        for spec in containers.values()
        if isinstance(spec, dict) and spec.get("external_project")
    } - manager_services
    raw = config.get("compose_binds")
    if not isinstance(raw, dict) or not raw:
        raise TargetsConfigError(
            f"{label} compose_binds: must be a non-empty mapping"
        )
    seen: set[tuple[str, str, bool]] = set()
    for service, entries in raw.items():
        if not isinstance(service, str) or not service.strip():
            raise TargetsConfigError(f"{label} compose_binds: service name must be a string")
        if service != service.strip():
            # `"rustfs "`는 validate를 통과하고 배포에서 baseline 밖으로 죽는다 —
            # 그때 나오는 메시지는 bind를 가리키는데 실제 원인은 공백이다.
            raise TargetsConfigError(
                f"{label} compose_binds: service name has surrounding whitespace: {service!r}"
            )
        if not isinstance(entries, list) or not entries:
            raise TargetsConfigError(
                f"{label} compose_binds.{service}: must be a non-empty list"
            )
        for index, entry in enumerate(entries):
            where = f"{label} compose_binds.{service}[{index}]"
            if not isinstance(entry, dict):
                raise TargetsConfigError(f"{where}: must be a mapping")
            unknown = sorted(set(entry) - set(_BIND_FIELDS))
            if unknown:
                # 오타 난 키를 조용히 무시하면 그 항목이 의도와 다른 bind가 된다.
                raise TargetsConfigError(f"{where}: unknown fields {unknown}")
            missing = [field for field in _BIND_FIELDS if field not in entry]
            if missing:
                raise TargetsConfigError(f"{where}: missing fields {missing}")
            container_path = entry["container_path"]
            if not isinstance(container_path, str) or not container_path.startswith("/"):
                raise TargetsConfigError(
                    f"{where}.container_path: must be an absolute path"
                )
            if container_path != posixpath.normpath(container_path):
                # `/a/../b`·`/a//b`·후행 슬래시는 서로 다른 allowlist 키가 되는데
                # 조회는 `mount.target`과 문자열 그대로 비교한다. 방향은 fail-close라
                # 사고는 아니지만, 경계 파일에 "읽을 때와 인가할 때가 다른 경로"가
                # 남는다.
                raise TargetsConfigError(
                    f"{where}.container_path: must be normalized "
                    f"({container_path!r} -> {posixpath.normpath(container_path)!r})"
                )
            read_only = entry["read_only"]
            if not isinstance(read_only, bool):
                # YAML의 `read_only: "false"`는 참인 문자열이다 — 읽기 전용이어야 할
                # bind가 쓰기 가능으로 등재되는 조용한 경로다.
                raise TargetsConfigError(f"{where}.read_only: must be a boolean")
            source = entry["source"]
            if not isinstance(source, str) or not source.strip():
                raise TargetsConfigError(f"{where}.source: must be a non-empty string")
            if service in external_only_services:
                # **이 절은 Manager 자신의 보안 경계다.** 키가 (compose service,
                # container_path, read_only)뿐이라 프로젝트 차원이 없다 — 형제
                # 프로젝트를 겨냥해 쓴 한 줄이 Manager의 production bind allowlist를
                # 넓힌다(적대 리뷰 2026-09-18 B-F8). 두 프로젝트에 다 있는 이름
                # (`prometheus`)은 Manager 쪽 정당한 항목이므로 막지 않는다 —
                # **외부에만 있는 이름**이 여기 나타나는 것이 오설정의 신호다.
                raise TargetsConfigError(
                    f"{where}: '{service}' only exists in an external compose "
                    "project; this allowlist governs the Manager's own candidate"
                )
            key = (service, container_path, read_only)
            if key in seen:
                raise TargetsConfigError(
                    f"{where}: duplicate bind key {key!r} — 뒤엣것이 조용히 이긴다"
                )
            seen.add(key)


def load_compose_bind_allowlist() -> ComposeBindAllowlist:
    """production compose candidate가 허용하는 host bind의 정본.

    종전에는 `c6c_deployment._CANDIDATE_ALLOWED_OPERATOR_BINDS` 상수였다 —
    새 bind 하나에 backend 수정 + trusted release 재설치가 필요했고, 그것이 GM-17이
    지목한 범용성의 실질 병목이었다. 자리를 설정으로 옮기고 코드에는 검증 규칙만
    남긴다. 이 문서의 신뢰는 `get_targets_config_path`/`_read_targets_bytes`가
    받친다(trusted 설치본에서 env redirect 거부 + root 소유·비쓰기 강제).
    """

    config = load_targets_config()
    raw = config.get("compose_binds") or {}
    allowlist: dict[tuple[str, str, bool], str] = {}
    for service, entries in raw.items():
        for entry in entries:
            allowlist[(service, entry["container_path"], entry["read_only"])] = entry[
                "source"
            ]
    # 캐시하지 않지만 호출자끼리 같은 객체를 공유하는 실수를 애초에 막는다 —
    # 보안 경계를 한 줄로 오염시킬 수 있는 가변 dict를 돌려줄 이유가 없다.
    return MappingProxyType(allowlist)


def _validate_targets_config(config: dict[str, Any], *, label: str) -> None:
    """GM-11: 오타 하나가 raw KeyError로 죽거나 조용히 무시되지 않게 fail-close한다.

    `containers`가 depends_on 폐포에서 기계적으로 유도 가능하다는 원래 개선안의
    전제는 틀렸다 — 모니터링 target(gra/cadv/prom)이 앱 target의 `depends_on`
    폐포에 들어가지만 실제 `containers` 목록에는 없다(기동 순서 선형화일 뿐
    논리적 의존이 아니기 때문). 그래서 여기서는 유도를 시도하지 않고, 이미 적힌
    `containers`/`depends_on`/`include`/`aliases`가 서로 참조 무결성을 지키는지만
    검증한다.
    """

    containers = config["containers"]
    targets = config["targets"]

    for container_id, spec in containers.items():
        if not isinstance(spec, dict):
            raise TargetsConfigError(f"{label} containers.{container_id}: must be a mapping")
        for field in _REQUIRED_CONTAINER_FIELDS:
            if field not in spec:
                raise TargetsConfigError(
                    f"{label} containers.{container_id}: missing required field '{field}'"
                )

    seen_aliases: dict[str, str] = {}
    for target_id, spec in targets.items():
        if not isinstance(spec, dict):
            raise TargetsConfigError(f"{label} targets.{target_id}: must be a mapping")

        for dep in _require_list_field(spec, "depends_on", target_id=target_id, label=label):
            if dep not in targets:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.depends_on: unknown target '{dep}'"
                )
        for included in _require_list_field(spec, "include", target_id=target_id, label=label):
            if included not in targets:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.include: unknown target '{included}'"
                )
        for container_id in _require_list_field(
            spec, "containers", target_id=target_id, label=label
        ):
            if container_id not in containers:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.containers: unknown container '{container_id}'"
                )

        _validate_external_project(spec, target_id=target_id, label=label)

        aliases = _require_list_field(spec, "aliases", target_id=target_id, label=label)
        for alias in [target_id, *aliases]:
            normalized = str(alias).strip().lower()
            owner = seen_aliases.get(normalized)
            if owner is not None and owner != target_id:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.aliases: alias '{alias}' already used "
                    f"by target '{owner}'"
                )
            seen_aliases[normalized] = target_id

    for name in config["dependency_order"]:
        if name not in targets:
            raise TargetsConfigError(f"{label} dependency_order: unknown target '{name}'")

    _validate_external_wiring(config, label=label)

    # `ktdctl targets validate`가 bind 절도 함께 본다 — 배포 도중이 아니라 그 전에
    # 형태 오류를 잡는 것이 이 명령의 존재 이유다.
    _validate_compose_binds(config, label=label)


def _targets() -> dict[str, dict[str, Any]]:
    return load_targets_config()["targets"]


def _dependency_order() -> list[str]:
    return list(load_targets_config()["dependency_order"])


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for target, spec in _targets().items():
        for alias in [target, *spec.get("aliases", [])]:
            aliases[str(alias).strip().lower()] = target
    return aliases


class _LazyMapping(Mapping[str, Any]):
    """`loader`를 최초 실제 접근(구독/순회/`in`/`.items()` 등) 시점에만 호출하는
    지연 dict-like 객체.

    module import 시점에는 loader를 실행하지 않으므로, 이 객체를 그저 import만
    하고 실제로 사용하지 않는 경로(예: `ktdctl --help`가 registry.py를 참조하는
    다른 모듈을 거쳐 import될 때)는 설정 파일이 깨져 있어도 import 자체는 깨지지
    않는다. `loader`가 내부적으로 참조하는 `load_targets_config()`가 이미
    `@lru_cache`이므로 최초 접근 이후 재계산 비용은 없다.
    """

    def __init__(self, loader: Callable[[], dict[str, Any]]) -> None:
        self._loader = loader

    def _resolve(self) -> dict[str, Any]:
        return self._loader()

    def __getitem__(self, key: str) -> Any:
        return self._resolve()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __repr__(self) -> str:
        return repr(self._resolve())


# `docker-targets.yml`을 읽다 날 수 있는 오류 전체. `TargetsConfigError`만 잡으면
# **손편집 시 가장 흔한 실수**가 전부 raw traceback으로 샌다 — 중복 키는
# `yaml.constructor.ConstructorError`(→`yaml.YAMLError`), 들여쓰기 오류는
# `yaml.scanner.ScannerError`, 파일 부재·권한은 `OSError`이고 셋 다 `ValueError`가
# 아니다(적대 리뷰 2인 실측: 각각 52·61·26줄 traceback).
TARGETS_CONFIG_ERRORS: Final = (TargetsConfigError, OSError, yaml.YAMLError)


MANAGED_CONTAINERS: Mapping[str, dict[str, Any]] = _LazyMapping(
    lambda: load_targets_config()["containers"]
)
MANAGED_TARGETS: Mapping[str, dict[str, Any]] = _LazyMapping(_targets)
TARGET_ALIASES: Mapping[str, str] = _LazyMapping(_build_aliases)


def resolve_target_name(target: str | None) -> str:
    normalized = (target or "all").strip().lower()
    if normalized in TARGET_ALIASES:
        return TARGET_ALIASES[normalized]
    raise ValueError(f"unknown target: {target}")


def _dependency_closure(target_name: str) -> set[str]:
    """target의 transitive `depends_on` 폐포(자기 자신 포함)를 반환한다."""
    closure: set[str] = set()
    stack = [target_name]
    while stack:
        name = stack.pop()
        if name in closure:
            continue
        closure.add(name)
        for dep in _targets().get(name, {}).get("depends_on", []):
            stack.append(str(dep))
    return closure


def target_sequence_for_target(target: str | None) -> list[str]:
    target_name = resolve_target_name(target)
    target_spec = _targets()[target_name]
    if target_spec.get("include"):
        included: list[str] = []
        for included_target in target_spec["include"]:
            included.extend(target_sequence_for_target(included_target))
        return _dedupe(included)

    # 선형 슬라이스 대신 `depends_on` DAG의 위상정렬을 사용한다.
    # dependency_order는 DAG의 유효한 linearization이므로, 폐포를 그 순서로 정렬하면
    # 의존성(부모)이 항상 먼저 오는 결정적 순서가 된다. depends_on이 없으면 단일 target.
    order = _dependency_order()
    closure = _dependency_closure(target_name)
    if not _targets()[target_name].get("depends_on") and target_name not in order:
        return [target_name]
    return sorted(closure, key=lambda t: order.index(t) if t in order else len(order))


def get_target(target: str | None) -> dict[str, Any]:
    target_name = resolve_target_name(target)
    spec = _targets()[target_name]
    return {
        "id": target_name,
        **spec,
        "resolved_sequence": target_sequence_for_target(target_name),
        "resolved_services": services_for_target(target_name),
        "resolved_runtime_services": runtime_services_for_target(target_name),
        "resolved_init_steps": init_steps_for_target(target_name),
    }


def list_targets() -> list[dict[str, Any]]:
    ordered_ids = _dedupe([*_dependency_order(), *list(_targets().keys())])
    return [get_target(target) for target in ordered_ids if target in _targets()]


def services_for_target(target: str | None) -> list[str]:
    services: list[str] = []
    for target_name in target_sequence_for_target(target):
        services.extend(_targets()[target_name].get("services", []))
    return _dedupe(services)


#: 컨테이너 절이 쓸 수 있는 필드. target 절과 **같은 등급으로** 닫는다 — 오타 한
#: 글자가 조용히 무시되면 그 컨테이너는 Manager 소유로 취급된다.
_ALLOWED_CONTAINER_FIELDS: Final = frozenset(
    {
        "name",
        "compose_service",
        "role",
        "display_name",
        "connection",
        "prod_url_env",
        "expected_ports",
        "external_project",
    }
)


def _validate_external_wiring(config: dict[str, Any], *, label: str) -> None:
    """선언들 **사이의** 무결성. 개별 절의 형태는 `_validate_external_project`가 본다.

    이 검사들은 한때 `test_registry_targets_config.py`의 assert였다. 그 자리에서는
    저장소의 `config/docker-targets.yml`만 봤으므로 설치본이나 env로 지정된 설정에는
    아무 효력이 없었고 `ktdctl targets validate`도 잡지 못했다(적대 리뷰 2026-09-18
    H-1). 검증기 안이 원래 자리다.
    """

    containers = config["containers"]
    targets = config["targets"]

    # H-3: project 이름 하나에 좌표 하나. `service_groups_for_target`의 묶음 키는
    # 좌표 전체지만 `external_project_for_container`는 이름으로 첫 매치를 고른다 —
    # 둘이 갈리면 컨테이너가 어느 좌표에 속하는지가 **파일 순서**로 정해진다.
    coordinates: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    owning_target: dict[str, str] = {}
    for target_id, spec in targets.items():
        external = spec.get("external_project")
        if not external:
            continue
        coordinate = (
            str(external["project"]),
            str(external["working_dir"]),
            tuple(str(name) for name in external["config_files"]),
        )
        project = coordinate[0]
        previous = coordinates.get(project)
        if previous is not None and previous != coordinate:
            raise TargetsConfigError(
                f"{label} targets.{target_id}.external_project: project "
                f"'{project}' is already declared by target "
                f"'{owning_target[project]}' with different coordinates "
                "(working_dir/config_files must match for the same project)"
            )
        coordinates[project] = coordinate
        owning_target.setdefault(project, target_id)

    # H-1: 컨테이너 절도 같은 등급으로 본다.
    for container_id, spec in containers.items():
        unknown = sorted(set(spec) - _ALLOWED_CONTAINER_FIELDS)
        if unknown:
            # target 절에는 unknown-field 검사가 있는데 컨테이너 절에는 없었다.
            # 그 비대칭 때문에 `external_projct` 오타가 조용히 무시되고 컨테이너가
            # **Manager 소유**로 취급됐다 — C-3의 세 증상이 그대로 복원되는
            # 경로다(적대 리뷰 2026-09-18 B-F2).
            raise TargetsConfigError(
                f"{label} containers.{container_id}: unknown fields {unknown}"
            )
        if "external_project" not in spec:
            continue
        where = f"{label} containers.{container_id}.external_project"
        project = spec["external_project"]
        if not isinstance(project, str) or not project.strip() or project != project.strip():
            raise TargetsConfigError(
                f"{where}: must be a non-empty string without surrounding whitespace"
            )
        if project not in coordinates:
            # 오타는 `external_project_for_container`에서 조용히 `None`이 되고,
            # 그러면 그 컨테이너는 Manager 소속으로 취급된다 — 남의 컨테이너
            # 자리에 Manager 설정이 뜨고, 편집이 Manager compose로 간다.
            raise TargetsConfigError(
                f"{where}: no target declares project '{project}' "
                f"(declared projects: {sorted(coordinates)})"
            )

    # H-1(계속) + H-2: target이 자기 것이라 적은 컨테이너와 소속이 맞는가, 그리고
    # 그 컨테이너의 `compose_service`가 target의 `services`에 실재하는가.
    for target_id, spec in targets.items():
        external = spec.get("external_project")
        target_project = str(external["project"]) if external else None
        declared_services = set(spec.get("services") or [])
        for container_id in spec.get("containers") or []:
            container_spec = containers.get(container_id)
            if not isinstance(container_spec, dict):
                continue  # 참조 무결성은 호출자가 이미 본다
            container_project = container_spec.get("external_project") or None
            if container_project != target_project:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.containers: container "
                    f"'{container_id}' belongs to project "
                    f"{container_project!r} but the target declares "
                    f"{target_project!r}"
                )
            if target_project is None:
                continue
            # H-2: 외부 target의 `services`는 저장소 밖 compose를 열지 않고는 실재를
            # 확인할 수 없다. 대신 **선언끼리** 묶는다. 반대 방향은 강제하지 않는다 —
            # one-shot(migrate 등)은 `services`에만 있고 `containers:`에는 없는 것이
            # 이 저장소의 규칙이다.
            compose_service_name = str(container_spec["compose_service"])
            if compose_service_name not in declared_services:
                raise TargetsConfigError(
                    f"{label} targets.{target_id}.services: container "
                    f"'{container_id}' declares compose_service "
                    f"'{compose_service_name}' which the target does not list"
                )

    # M-4: Manager target이 외부 target에 의존하면 **Manager target의** 배포가
    # 막힌다(`ensure`가 의존 폐포를 보고 거부하고, 메시지는 Manager target을
    # 탓한다). 선언 시점에 막는 편이 훨씬 싸다.
    external_targets = {
        target_id for target_id, spec in targets.items() if spec.get("external_project")
    }
    for target_id, spec in targets.items():
        if target_id in external_targets:
            continue
        for field in ("depends_on", "include"):
            for referenced in spec.get(field) or []:
                if referenced in external_targets:
                    raise TargetsConfigError(
                        f"{label} targets.{target_id}.{field}: '{referenced}' is an "
                        "external compose project; a Manager target that reaches it "
                        "can no longer be deployed by `ensure`"
                    )

    # `all`이 무엇을 담는지 못박는다. 새 Manager target을 `dependency_order`에만
    # 넣고 `all.include`에서 빠뜨리면 `ensure all`이 조용히 그것을 건너뛴다 —
    # 지금 `dependency_order` 12개 대 `all.include` 9개의 차이가 정확히 외부 셋인
    # 것이 **우연이 아니라 규칙**임을 여기서 말한다.
    all_spec = targets.get("all")
    if isinstance(all_spec, dict):
        # 외부 target이 `all`에 들어가는 쪽은 위의 M-4 검사가 이미 막는다(`all`은
        # Manager target이므로 그 `include`가 외부를 가리키면 거기서 걸린다).
        # 여기서는 **빠뜨림**만 본다 — 같은 규칙을 두 번 쓰면 한쪽을 지워도 아무
        # 검사가 빨개지지 않는다.
        included = set(all_spec.get("include") or [])
        for name in config["dependency_order"]:
            if name in external_targets or name == "all":
                continue
            if targets[name].get("excluded_from_all"):
                # **의도를 말할 자리를 둔다.** 이 검사는 안전 규칙이 아니라 관례
                # 검사인데 `load_targets_config()` 안에서 도므로, 탈출구가 없으면
                # 의도적 제외 하나가 모든 CLI 명령과 라우트를 함께 죽인다(적대 리뷰
                # 2026-09-18 B-F9). 빠뜨림은 계속 잡고, 의도는 한 줄로 적게 한다.
                continue
            if name not in included:
                raise TargetsConfigError(
                    f"{label} targets.all.include: missing Manager target "
                    f"'{name}' declared in dependency_order "
                    "(set `excluded_from_all: true` if that is deliberate)"
                )


def _validate_external_project(spec: dict[str, Any], *, target_id: str, label: str) -> None:
    """`external_project` 절의 형태를 검증한다 — 없으면 아무것도 하지 않는다.

    오타 하나가 "Manager 자신의 프로젝트"로 조용히 해석되면 다른 프로젝트를
    대상으로 명령이 돌아간다. fail-close한다.
    """

    if "external_project" not in spec:
        return
    where = f"{label} targets.{target_id}.external_project"
    external = spec["external_project"]
    if not isinstance(external, dict):
        raise TargetsConfigError(f"{where}: must be a mapping")
    unknown = sorted(set(external) - set(_EXTERNAL_PROJECT_FIELDS))
    if unknown:
        raise TargetsConfigError(f"{where}: unknown fields {unknown}")
    missing = [field for field in _EXTERNAL_PROJECT_FIELDS if field not in external]
    if missing:
        raise TargetsConfigError(f"{where}: missing fields {missing}")

    project = external["project"]
    if not isinstance(project, str) or not project.strip() or project != project.strip():
        raise TargetsConfigError(
            f"{where}.project: must be a non-empty string without surrounding whitespace"
        )
    working_dir = external["working_dir"]
    if not isinstance(working_dir, str) or not working_dir.startswith("/"):
        raise TargetsConfigError(f"{where}.working_dir: must be an absolute path")
    if working_dir != posixpath.normpath(working_dir):
        raise TargetsConfigError(
            f"{where}.working_dir: must be normalized (got {working_dir!r})"
        )

    config_files = external["config_files"]
    if not isinstance(config_files, list) or not config_files:
        raise TargetsConfigError(f"{where}.config_files: must be a non-empty list")
    seen_config_files: set[str] = set()
    for index, name in enumerate(config_files):
        if not isinstance(name, str) or not name.strip():
            raise TargetsConfigError(
                f"{where}.config_files[{index}]: must be a non-empty string"
            )
        if name.startswith("/"):
            # 절대 경로를 허용하면 `working_dir`이 뜻을 잃고, 선언을 읽는 사람이
            # 어느 디렉터리가 기준인지 알 수 없게 된다.
            raise TargetsConfigError(
                f"{where}.config_files[{index}]: must be relative to working_dir"
            )
        if name != posixpath.normpath(name) or ".." in name.split("/"):
            # 경로 **구성요소**로 본다. `startswith("..")`는 `..hidden/compose.yml`
            # 처럼 탈출이 아닌 이름을 거부하는 오탐이었다.
            raise TargetsConfigError(
                f"{where}.config_files[{index}]: must be normalized and stay inside "
                f"working_dir (got {name!r})"
            )
        if name in seen_config_files:
            # compose는 `-f`를 순서대로 병합한다. 같은 파일을 두 번 적으면 뒤엣것이
            # 조용히 이겨서, 선언을 읽는 사람이 예상하지 못한 병합이 된다.
            raise TargetsConfigError(
                f"{where}.config_files: duplicate entry {name!r}"
            )
        seen_config_files.add(name)

    if spec.get("init_steps"):
        # init_steps는 Manager compose의 `exec`로 돈다. 외부 프로젝트에 그대로
        # 적용하면 엉뚱한 컨테이너를 잡으므로, 지원 전에는 선언 자체를 막는다.
        raise TargetsConfigError(
            f"{where}: external targets cannot declare init_steps yet "
            "(they would run against the Manager project)"
        )


def external_project_for_target(target: str | None) -> ExternalProject | None:
    """target이 선언한 외부 프로젝트. 선언이 없으면 `None`(= Manager 자신)."""

    target_name = resolve_target_name(target)
    spec = _targets()[target_name]
    external = spec.get("external_project")
    if not external:
        return None
    return ExternalProject(
        project=str(external["project"]),
        working_dir=str(external["working_dir"]),
        config_files=tuple(str(name) for name in external["config_files"]),
    )


def service_groups_for_target(
    target: str | None, *, runtime_only: bool = False
) -> list[ServiceGroup]:
    """target의 서비스를 **프로젝트별로 묶어** 의존성 순서대로 돌려준다.

    `services_for_target`의 평평한 목록은 프로젝트가 하나일 때만 성립한다. 여러
    프로젝트에 걸친 target은 묶음마다 별도 `docker compose` 호출이 필요하므로,
    호출하는 쪽이 그 사실을 볼 수 있어야 한다.

    같은 프로젝트의 서비스는 한 묶음으로 합친다 — 의존성 순서상 떨어져 있어도
    호출은 한 번으로 족하고, 그것이 compose가 기대하는 사용법이다.
    """

    field = "runtime_services" if runtime_only else "services"
    grouped: dict[tuple[str, str, tuple[str, ...]] | None, list[str]] = {}
    order: list[tuple[str, str, tuple[str, ...]] | None] = []
    for target_name in target_sequence_for_target(target):
        spec = _targets()[target_name]
        external = external_project_for_target(target_name)
        key = (
            (external.project, external.working_dir, external.config_files)
            if external is not None
            else None
        )
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        for service in spec.get(field, []):
            if service not in grouped[key]:
                grouped[key].append(service)

    groups: list[ServiceGroup] = []
    for key in order:
        services = tuple(grouped[key])
        if not services:
            continue
        external = (
            ExternalProject(project=key[0], working_dir=key[1], config_files=key[2])
            if key is not None
            else None
        )
        groups.append(ServiceGroup(external=external, services=services))
    return groups


def target_is_external(target: str | None) -> bool:
    """이 target(또는 그 의존 폐포)이 Manager 밖의 프로젝트를 건드리는가."""

    return any(group.external is not None for group in service_groups_for_target(target))


def runtime_services_for_target(target: str | None) -> list[str]:
    services: list[str] = []
    for target_name in target_sequence_for_target(target):
        services.extend(_targets()[target_name].get("runtime_services", []))
    return _dedupe(services)


def init_steps_for_target(target: str | None) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for target_name in target_sequence_for_target(target):
        for step in _targets()[target_name].get("init_steps", []):
            steps.append({"target": target_name, **step})
    return steps


def external_project_for_container(container_id: str) -> ExternalProject | None:
    """이 컨테이너가 속한 외부 프로젝트. Manager 자신의 것이면 `None`.

    컨테이너 선언의 `external_project`는 **프로젝트 이름**만 담는다(좌표 전체를
    컨테이너마다 반복하면 target 선언과 갈라진다). 그래서 이름으로 target을 찾아
    그 target의 좌표를 쓴다 — 정본이 한 곳이다.
    """

    spec = MANAGED_CONTAINERS.get(container_id)
    if spec is None:
        return None
    project = spec.get("external_project")
    if not project:
        return None
    for target_name in _targets():
        external = external_project_for_target(target_name)
        if external is not None and external.project == project:
            return external
    # 스키마 검증이 이 경우를 막는다(선언되지 않은 프로젝트). 방어로만 남긴다.
    return None


def container_id_to_compose_service(container_id: str) -> str:
    if container_id not in MANAGED_CONTAINERS:
        raise ValueError(f"unknown container: {container_id}")
    return str(MANAGED_CONTAINERS[container_id]["compose_service"])


def is_known_target(name: str) -> bool:
    return name.strip().lower() in TARGET_ALIASES
