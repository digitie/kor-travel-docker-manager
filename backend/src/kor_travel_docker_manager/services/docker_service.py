import logging
import os
import re
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import docker
import yaml
from docker.errors import DockerException, NotFound
from kor_travel_docker_manager.services.c6c_deployment import (
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    _PINVI_POSTGRES_INITDB_ARGS,
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    assert_contract_locked_env_unchanged,
    assert_manager_mutation_allowed,
    compose_volume_graph_hash,
    contract_locked_env_names,
    revalidate_candidate_system_bind_snapshots,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeEnvironmentSnapshot,
    ComposeTransactionSnapshot,
    ValidatedComposeCandidate,
    _capture_compose_environment_snapshot,
    assert_environment_snapshot_matches_c6c_lock,
    c6c_deployment_lock_from_environment,
    compose_service,
    get_compose_path,
)
from kor_travel_docker_manager.services.registry import MANAGED_CONTAINERS

logger = logging.getLogger(__name__)


def _get_compose_path() -> str:
    return get_compose_path()


def _locked_env_present(service_name: str, svc_config: Mapping[str, Any]) -> list[str]:
    """이 service의 env 중 배포 계약이 값을 고정한 이름.

    화면이 처음부터 잠그기 위한 값이다. 계약에는 있으나 이 Compose 파일에 없는 이름은
    보내지 않는다 — 편집기에 존재하지도 않는 항목을 "잠김"으로 표시하면 안 된다.
    """

    environment = svc_config.get("environment")
    if not isinstance(environment, Mapping):
        return []
    return [name for name in contract_locked_env_names(service_name) if name in environment]


def _public_url(spec: dict[str, Any]) -> str | None:
    """컨테이너의 운영(prod) 공개 URL을 환경변수에서 해석한다.

    docker-targets.yml의 `prod_url_env`가 가리키는 환경변수(KTDM_PROD_URL_*)에서
    실제 도메인을 읽는다. 미설정이면 None을 반환해 대시보드가 로컬 connection만 표시한다.
    실제 도메인은 저장소에 커밋하지 않고 gitignore된 .env에만 둔다.
    """
    env_key = spec.get("prod_url_env")
    if not env_key:
        return None
    value = os.environ.get(str(env_key), "").strip()
    return value or None


def get_compose_config(path: str | None = None) -> dict[str, Any]:
    path = path or _get_compose_path()
    if not os.path.exists(path):
        logger.error(f"docker-compose.yml not found at {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Error reading docker-compose.yml: {e}")
        return {}


def _atomic_write(path: str, payload: bytes, *, mode: int | None = None) -> None:
    destination = Path(path)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _save_compose_config_unlocked(
    config: dict[str, Any],
    *,
    compose_path: str | None = None,
) -> None:
    payload = yaml.safe_dump(
        config,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    path = compose_path or _get_compose_path()
    mode = Path(path).stat().st_mode & 0o777
    _atomic_write(path, payload, mode=mode)


def _validate_compose_candidate(
    config: dict[str, Any],
    *,
    environment_snapshot: ComposeEnvironmentSnapshot | None = None,
) -> ValidatedComposeCandidate:
    return compose_service.capture_compose_candidate_transaction(
        config,
        environment_snapshot=environment_snapshot,
    )


def save_compose_config(config: dict[str, Any]) -> None:
    """검증·host lock을 거친 manager compose 파일 변경 진입점."""

    with c6c_deployment_lock_from_environment() as lock_snapshot:
        environment_snapshot = _capture_compose_environment_snapshot(
            environment_override=None
        )
        assert_environment_snapshot_matches_c6c_lock(
            environment_snapshot,
            lock_snapshot,
        )
        assert_manager_mutation_allowed(
            environment=environment_snapshot.effective
        )
        compose_path = Path(environment_snapshot.compose_path)
        original_bytes = compose_path.read_bytes()
        current = get_compose_config(str(compose_path))
        if not current or (
            compose_volume_graph_hash(config) != compose_volume_graph_hash(current)
        ):
            raise ComposeCandidateContractError(
                "compose candidate volume configuration is immutable through the Manager API"
            )
        validation = _validate_compose_candidate(
            config,
            environment_snapshot=environment_snapshot,
        )
        revalidate_candidate_system_bind_snapshots(
            validation.system_bind_snapshots
        )
        if compose_path.read_bytes() != original_bytes:
            raise ComposeCandidateContractError(
                "compose candidate source changed during the config request"
            )
        candidate_transaction = validation.transaction_snapshot
        if candidate_transaction is None:
            raise ComposeCandidateContractError(
                "compose candidate transaction was not captured"
            )
        _atomic_write(
            str(compose_path),
            candidate_transaction.compose_source_bytes,
            mode=candidate_transaction.compose_source_mode,
        )


# inspect 응답의 env·label에서 가릴 key 조각.
#
# `API_KEY`는 `ACCESS_KEY`에 걸리지 않는다. 이 저장소만 해도 provider API 키가 여럿 있어
# (`KOR_TRAVEL_MAP_OPINET_API_KEY`, `KOR_TRAVEL_MAP_KREX_*_API_KEY`,
# `KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY`, `KOR_TRAVEL_GEO_VWORLD_API_KEY`)
# 빠뜨리면 그대로 노출된다. T-012가 inspect를 대시보드 UI에 연결하면서 이 경로가
# API/CLI 뿐 아니라 브라우저 한 번의 클릭으로 열리게 됐다.
#
# 과다 redaction은 안전한 방향이므로(값을 못 보는 불편) 의심스러우면 포함한다.
# 예: `..._API_KEY_CACHE_TTL_S`(숫자)나 공개용 `NEXT_PUBLIC_*_API_KEY`도 함께 가려진다.
SENSITIVE_KEY_PARTS = (
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "TOKEN",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "API_KEY",
    "APIKEY",
    "CREDENTIAL",
)


# key 이름만 보는 방식은 값 안에 박힌 credential을 못 잡는다. DSN/URL은 이름이
# `..._PG_DSN`·`..._DATABASE_URL`처럼 위 목록에 걸리지 않으면서 값에
# `postgresql+asyncpg://user:password@host/db` 형태로 비밀번호를 담는다.
#
# key 전체를 가리는 대신 userinfo의 비밀번호 구간만 치환한다. `..._BASE_URL`,
# `..._ENDPOINT_URL`처럼 비밀이 아닌 URL은 그대로 읽을 수 있어야 패널이 쓸모 있다.
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/?#@\s]+):(?P<password>[^@/?#\s]+)@"
)


def _is_sensitive_key(key: str) -> bool:
    upper_key = key.upper()
    return any(part in upper_key for part in SENSITIVE_KEY_PARTS)


def _redact_value_credentials(value: str) -> str:
    """값 안의 `scheme://user:password@` 비밀번호 구간을 가린다."""
    return _URL_USERINFO_RE.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:<redacted>@", value
    )


def _redact_env_pair(raw_pair: str) -> str:
    if "=" not in raw_pair:
        return raw_pair
    key, value = raw_pair.split("=", 1)
    if _is_sensitive_key(key):
        return f"{key}=<redacted>"
    return f"{key}={_redact_value_credentials(value)}"


def _redact_argv(argv: list[str] | None) -> list[str] | None:
    """command/entrypoint argv에서 URL credential을 가린다.

    env·label과 달리 cmd/entrypoint는 그동안 아무 필터도 거치지 않았다. 지금 compose의
    command에는 credential이 없지만(모두 environment로 주입), 이미지에 내장된 CMD나
    `mc alias set <ep> <access> <secret>` 같은 관용구가 그대로 노출될 수 있는 통로다.
    """
    if not argv:
        return argv
    return [_redact_value_credentials(str(item)) for item in argv]


def _sanitize_labels(labels: dict[str, str] | None) -> dict[str, str]:
    if not labels:
        return {}
    return {key: "<redacted>" if _is_sensitive_key(key) else value for key, value in labels.items()}


def _format_mount(mount: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": mount.get("Type"),
        "name": mount.get("Name"),
        "source": mount.get("Source"),
        "destination": mount.get("Destination"),
        "mode": mount.get("Mode"),
        "rw": mount.get("RW"),
    }


# --- 컨테이너 설정 변경(ports/env/networks) 입력 검증 (T-011) ------------------------
#
# volumes는 여기서 검증하지 않는다 — compose_volume_graph_hash 비교가 이미 어떤 변경도
# 첫 container mutation 전에 거부하는 강한 불변 계약이다(update_container_config 안,
# `_update_container_config_unlocked`의 `compose_volume_graph_hash(compose_cfg) !=
# baseline_volume_hash` 검사). 그 외 세 필드는 지금까지 어떤 형식 검증도 없었다.


class ContainerConfigValidationError(ValueError):
    """ports/env/networks 사용자 입력이 형식이나 보안 정책을 위반했다."""


_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INTERPOLATED_VALUE_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}$")
_INTERPOLATED_VALUE_PARTS_RE = re.compile(
    r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-(?P<default>[^}]*))?\}$"
)
_NETWORK_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


def _interpolated_default(value: str) -> str | None:
    """`${VAR:-default}`의 default 부분을 반환한다. 보간이 아니거나 default가 없으면 None."""
    match = _INTERPOLATED_VALUE_PARTS_RE.match(value)
    return match.group("default") if match else None

# 포트 토큰: 리터럴 숫자(범위 포함) 또는 `${VAR:-default}` 보간. docker-compose.yml의
# 모든 ports 항목이 `${VAR:-12101}:${VAR:-12101}` 형태를 쓰므로(전수 확인), 보간
# 토큰은 opaque하게 신뢰하고 리터럴 숫자만 1~65535 범위와 형식을 검사한다.
_PORT_TOKEN = r"\d{1,5}(?:-\d{1,5})?|\$\{[^{}]+\}"
_PORT_IP = r"\d{1,3}(?:\.\d{1,3}){3}|\$\{[^{}]+\}"
_PORT_PROTO = r"(?:/(?:tcp|udp))?"
_PORT_PATTERNS = (
    re.compile(rf"^(?P<container>{_PORT_TOKEN}){_PORT_PROTO}$"),
    re.compile(rf"^(?P<host>{_PORT_TOKEN}):(?P<container>{_PORT_TOKEN}){_PORT_PROTO}$"),
    re.compile(
        rf"^(?P<ip>{_PORT_IP}):(?P<host>{_PORT_TOKEN}):(?P<container>{_PORT_TOKEN}){_PORT_PROTO}$"
    ),
)


def _validate_port_token(token: str, *, raw: str) -> None:
    if token.startswith("${"):
        return
    values: list[int] = []
    for part in token.split("-"):
        if not part.isdigit():
            raise ContainerConfigValidationError(
                f"포트 매핑 형식이 올바르지 않습니다: '{raw}'"
            )
        value = int(part)
        if not (1 <= value <= 65535):
            raise ContainerConfigValidationError(
                f"포트 번호는 1~65535 범위여야 합니다: '{raw}'"
            )
        values.append(value)
    if len(values) == 2 and values[0] > values[1]:
        raise ContainerConfigValidationError(
            f"포트 범위의 시작이 끝보다 큽니다: '{raw}'"
        )


def validate_port_mapping(raw: str) -> None:
    """Compose ports 항목 형식을 검증한다."""
    entry = raw.strip()
    if not entry:
        raise ContainerConfigValidationError("포트 매핑 값이 비어 있습니다.")
    for pattern in _PORT_PATTERNS:
        match = pattern.match(entry)
        if not match:
            continue
        groups = match.groupdict()
        if groups.get("host"):
            _validate_port_token(groups["host"], raw=raw)
        _validate_port_token(groups["container"], raw=raw)
        return
    raise ContainerConfigValidationError(
        f"포트 매핑 형식이 올바르지 않습니다: '{raw}' "
        "(예: '5432:5432' 또는 '${VAR:-5432}:${VAR:-5432}')"
    )


def validate_network_name(raw: str) -> None:
    entry = raw.strip()
    if not entry:
        raise ContainerConfigValidationError("네트워크 이름이 비어 있습니다.")
    if entry != raw:
        raise ContainerConfigValidationError(
            f"네트워크 이름 앞뒤에 공백이 있습니다: '{raw}'"
        )
    if not _NETWORK_NAME_RE.match(entry):
        raise ContainerConfigValidationError(
            f"네트워크 이름 형식이 올바르지 않습니다: '{raw}' "
            "(영문/숫자로 시작하고 영문·숫자·'_'·'.'·'-'만 허용)"
        )


def _value_has_literal_url_credential(value: str) -> bool:
    """`scheme://user:password@` 중 password 부분이 literal이면 True.

    password 토큰 자체가 `${VAR}`/`${VAR:-default}` 보간이면(둘러싼 scheme·user·host가
    literal이어도) 안전하다고 본다 — 실제 비밀은 이미 `.env`로 분리돼 있기 때문이다.

    ⚠️ `${...}` 블록 전체를 지우고 남는 부분만 스캔하지 않는다. 그렇게 하면
    `${FAKE_NAME:-literal-secret}`처럼 지어낸 이름으로 감싸기만 해도 스캔을 통째로
    우회할 수 있다 — 적대적 리뷰에서 실제로 재현됐다(이미 보호되던 key도 예외가 아니었다:
    "보간 형태를 유지하라"는 요구는 `${SOME_UNRELATED_NAME:-literal-secret}`도 통과시킨다).
    이 함수는 값을 그대로(어떤 `${...}` 블록 안에 있든) 스캔한다. baseline과 완전히
    같은 값만 `validate_env_entry`에서 예외로 통과시킨다.
    """
    for match in _URL_USERINFO_RE.finditer(value):
        if not _INTERPOLATED_VALUE_RE.match(match.group("password")):
            return True
    return False


def validate_env_entry(
    key: str, value: str, *, baseline_value: str | None = None
) -> None:
    """env key/value를 검증한다.

    docker-compose.yml은 git에 커밋되는 파일이다. 세 겹으로 막는다.

    1. 이 key의 기존(baseline) 값이 이미 `${...}` 보간 형태였다면, 새 값도 보간
       형태여야 한다 — 이미 `.env`로 분리돼 있던 비밀 참조를 UI 편집 중 실수로
       리터럴로 되돌리는 것을 막는다. `_is_sensitive_key` 같은 정적 key-이름
       휴리스틱만 쓰면 `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`처럼 이름에
       "API_KEY"가 들어가지만 원래부터 리터럴 불리언인 값까지 오탐으로 막는다
       (docker-compose.yml 전수 검증에서 실제로 걸렸다). baseline이 이미 보간이
       아니었다면(불리언·DB명 등) 새 값도 자유롭다.
       이 key가 `_is_sensitive_key`이면, 여기서 한 겹 더 막는다: 새 값도 `${...}`
       형태이기만 하면 통과시키지 않고, `:-default` 리터럴이 baseline과 달라졌으면
       거부한다 — 그렇지 않으면 `${REAL_VAR:-old}` → `${FAKE_VAR:-new-secret}`처럼
       지어낸 변수명으로 감싸기만 해도 "여전히 보간 형태"라는 이유로 통과해 버린다
       (적대적 리뷰에서 실제로 재현됐다; DSN이 아닌 단일 값 비밀— 예:
       `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}` 같은 키에서는
       3번 규칙의 URL 자격증명 스캔이 적용되지 않으므로 이 겹이 없으면 그대로
       뚫린다). default를 완전히 제거하는 것(`${OTHER_NAME}`, default 없음)은 git에
       아무 리터럴도 남기지 않으므로 허용한다 — 그래서 "새 default가 있고 그것이
       baseline과 다를 때"만 거부한다.
    2. baseline을 모르는 경우(오늘의 UI에서는 발생하지 않지만, 이 함수는 새 key를
       추가하는 미래의 호출자에도 방어적이어야 한다) key 이름이 비밀스러워 보이면
       (`_is_sensitive_key`) 안전한 쪽으로 기울어 보간을 요구한다.
    3. **값이 baseline과 완전히 같지 않다면**, key 이름과 무관하게 literal 접속
       자격증명(`scheme://user:pass@`, password가 보간이 아닌 경우)이 있으면 거부한다.
       baseline과 동일한 값(수정 없이 그대로 제출)은 예외다 — 이미 git에 있던 값이라
       새로운 노출이 아니다. **"보간으로 감싸면 통과"가 아니다** — 지어낸 변수명으로
       감싼 `${FAKE_NAME:-literal-secret}`도 값이 바뀌었으면 그대로 거부된다. 1번
       규칙(재보간 요구)만으로는 이 우회를 막지 못한다: baseline이 `${REAL_VAR:-old}`이고
       새 값이 `${FAKE_VAR:-new-secret}`이면 둘 다 "보간 형태"라 1번을 통과하기 때문이다.
    """
    if not key or not _ENV_VAR_NAME_RE.match(key):
        raise ContainerConfigValidationError(
            f"환경변수 이름이 올바르지 않습니다: '{key}' "
            "(영문 또는 '_'로 시작하고 영문·숫자·'_'만 허용)"
        )
    # 보간 판정은 앞뒤 공백을 무시한다. 터미널/.env에서 복붙하면 흔히 붙는 공백 때문에
    # "리터럴로 바꿨다"는 오해의 소지가 있는 메시지가 뜨는 것을 막는다. 저장되는 값
    # 자체(`value`)는 그대로 두고, 오직 이 판정에서만 trim한 사본을 쓴다.
    stripped_value = value.strip()
    if baseline_value is not None:
        baseline_stripped = baseline_value.strip()
        if _INTERPOLATED_VALUE_RE.match(baseline_stripped):
            if not _INTERPOLATED_VALUE_RE.match(stripped_value):
                raise ContainerConfigValidationError(
                    f"'{key}'는 원래 '${{...}}' 참조로 분리되어 있었습니다. 리터럴로 바꾸면 "
                    "docker-compose.yml(git 추적 파일)에 실제 값이 그대로 저장됩니다. "
                    f"'${{{key}}}' 또는 '${{{key}:-기본값}}' 형태를 유지하세요."
                )
            if _is_sensitive_key(key):
                new_default = _interpolated_default(stripped_value)
                baseline_default = _interpolated_default(baseline_stripped)
                if new_default and new_default != baseline_default:
                    raise ContainerConfigValidationError(
                        f"'{key}'는 비밀 성격 값으로 판단됩니다. 참조하는 변수 이름을 바꾸거나 "
                        "새 기본값을 지정해도, 그 기본값이 docker-compose.yml(git 추적 파일)에 "
                        f"그대로 저장됩니다. 실제 비밀은 gitignore된 .env에 두고 "
                        f"'${{{key}}}' 또는 기존과 동일한 기본값으로만 참조하세요."
                    )
    elif _is_sensitive_key(key) and not _INTERPOLATED_VALUE_RE.match(stripped_value):
        raise ContainerConfigValidationError(
            f"'{key}'는 비밀 성격 값으로 판단됩니다. docker-compose.yml은 git에 "
            "커밋되므로 실제 값을 여기 직접 적지 말고, gitignore된 .env에 정의한 뒤 "
            f"'${{{key}}}' 또는 '${{{key}:-기본값}}' 형태로 참조하세요."
        )
    if value != baseline_value and _value_has_literal_url_credential(value):
        raise ContainerConfigValidationError(
            f"'{key}' 값에 접속 문자열 형태의 자격증명이 그대로 포함되어 있습니다. "
            "비밀번호 부분만이라도 '${VAR}' 보간으로 바꿔서 저장하세요."
        )


def validate_container_config_update(
    *,
    ports: list[Any],
    env: dict[str, Any],
    networks: list[Any],
    baseline_env: dict[str, Any] | None = None,
    service_name: str | None = None,
) -> None:
    """`update_container_config` 저장 전 사용자 입력을 검증한다.

    실패하면 ContainerConfigValidationError를 던진다. lock 획득이나 Docker 접근보다
    먼저 호출해, 형식이 잘못된 입력이 아무 mutation도 건드리지 않게 한다.
    """
    for port in ports:
        validate_port_mapping(str(port))
    for network in networks:
        validate_network_name(str(network))
    baseline_env = baseline_env or {}
    for key, value in env.items():
        baseline_value = baseline_env.get(key)
        validate_env_entry(
            str(key),
            "" if value is None else str(value),
            baseline_value=(
                None if baseline_value is None else str(baseline_value)
            ),
        )
    if service_name == "pinvi-postgres" and "POSTGRES_INITDB_ARGS" in env:
        if env["POSTGRES_INITDB_ARGS"] != _PINVI_POSTGRES_INITDB_ARGS:
            raise ContainerConfigValidationError(
                "PinVi PostgreSQL initdb authentication policy is immutable."
            )
    if service_name is not None:
        # candidate 계약이 값을 고정한 env는 저장 시점에 막는다. 재구축까지 미루면
        # 실패가 조작에서 멀어져 원인이 화면 조작이었다는 사실이 드러나지 않는다.
        try:
            assert_contract_locked_env_unchanged(
                service_name=service_name,
                env=env,
                baseline_env=baseline_env,
            )
        except ComposeCandidateContractError as exc:
            raise ContainerConfigValidationError(str(exc)) from exc


class DockerService:
    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None
        self._initialized = False
        self._default_compose_config: dict[str, Any] | None = None
        self._backup_default_config()

    def _backup_default_config(self) -> None:
        try:
            cfg = get_compose_config()
            import copy

            self._default_compose_config = copy.deepcopy(cfg)
            logger.info("Successfully backed up default docker-compose.yml config.")
        except Exception as e:
            logger.error(f"Failed to backup default compose config: {e}")

    def _get_client(self) -> docker.DockerClient:
        """Lazily initialize Docker client to prevent startup failures when Docker is down."""
        if not self._initialized:
            try:
                self._client = docker.from_env()
                self._initialized = True
            except DockerException as e:
                logger.error(f"Failed to connect to Docker daemon: {e}")
                raise RuntimeError("Docker daemon is not accessible.") from e
        if self._client is None:
            raise RuntimeError("Docker client initialization is inconsistent.")
        return self._client

    def get_containers_status(self) -> list[dict[str, Any]]:
        """Fetch the statuses of all managed containers."""
        status_list = []
        compose_cfg = get_compose_config()
        services = compose_cfg.get("services", {})

        # 순환 참조 방지를 위해 로컬 임포트 수행
        from kor_travel_docker_manager.services.metrics_collector import metrics_collector

        try:
            client = self._get_client()
        except RuntimeError:
            for key, spec in MANAGED_CONTAINERS.items():
                svc_name = spec["compose_service"]
                svc_config = services.get(svc_name, {})
                status_list.append(
                    {
                        "id": key,
                        "name": spec["name"],
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "status": "offline",
                        "state": "Docker daemon unavailable",
                        "ports": [],
                        "metrics": {
                            "cpu_pct": 0.0,
                            "mem_pct": 0.0,
                            "mem_usage": 0,
                            "mem_limit": 0,
                            "io_read": 0,
                            "io_write": 0,
                        },
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                            "locked_env": _locked_env_present(svc_name, svc_config),
                        },
                    }
                )
            return status_list

        for key, spec in MANAGED_CONTAINERS.items():
            cname = spec["name"]
            svc_name = spec["compose_service"]
            svc_config = services.get(svc_name, {})
            metric = metrics_collector.get_latest_metric(key)

            try:
                container = client.containers.get(cname)
                # Parse exposed ports
                ports = []
                port_bindings = container.attrs.get("HostConfig", {}).get("PortBindings", {})
                for container_port, host_ports in port_bindings.items():
                    if host_ports:
                        ports.append(
                            f"{host_ports[0].get('HostPort')}:{container_port.split('/')[0]}"
                        )

                image = container.image
                image_tags = image.tags if image is not None else []
                status_list.append(
                    {
                        "id": key,
                        "name": cname,
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "image": (
                            image_tags[0]
                            if image_tags
                            else image.short_id if image is not None else "unknown"
                        ),
                        "status": container.status,  # e.g., 'running', 'exited', 'paused'
                        "state": container.attrs.get("State", {}).get("Status", "unknown"),
                        "ports": ports,
                        "metrics": metric,
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                            "locked_env": _locked_env_present(svc_name, svc_config),
                        },
                    }
                )
            except NotFound:
                status_list.append(
                    {
                        "id": key,
                        "name": cname,
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "status": "not_created",
                        "state": "Container not found",
                        "ports": [],
                        "metrics": {
                            "cpu_pct": 0.0,
                            "mem_pct": 0.0,
                            "mem_usage": 0,
                            "mem_limit": 0,
                            "io_read": 0,
                            "io_write": 0,
                        },
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                            "locked_env": _locked_env_present(svc_name, svc_config),
                        },
                    }
                )
            except Exception as e:
                logger.error(f"Error querying container {cname}: {e}")
                status_list.append(
                    {
                        "id": key,
                        "name": cname,
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "status": "error",
                        "state": str(e),
                        "ports": [],
                        "metrics": {
                            "cpu_pct": 0.0,
                            "mem_pct": 0.0,
                            "mem_usage": 0,
                            "mem_limit": 0,
                            "io_read": 0,
                            "io_write": 0,
                        },
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                            "locked_env": _locked_env_present(svc_name, svc_config),
                        },
                    }
                )
        return status_list

    def control_container(self, container_id: str, action: str) -> dict[str, Any]:
        """Perform start/stop/restart action on a container."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}
        if action not in {"start", "stop", "restart"}:
            return {"success": False, "error": f"Invalid action: {action}"}
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            assert_environment_snapshot_matches_c6c_lock(
                environment_snapshot,
                lock_snapshot,
            )
            assert_manager_mutation_allowed(
                environment=environment_snapshot.effective
            )
            return self._control_container_unlocked(
                container_id,
                action,
                environment_snapshot=environment_snapshot,
            )

    def _control_container_unlocked(
        self,
        container_id: str,
        action: str,
        *,
        environment_snapshot: ComposeEnvironmentSnapshot,
    ) -> dict[str, Any]:
        """검증과 host lock을 이미 확보한 container SDK 변경 구현."""

        cname = MANAGED_CONTAINERS[container_id]["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)

            if action == "start":
                container.start()
            elif action == "stop":
                container.stop()
            elif action == "restart":
                container.restart()
            return {"success": True, "message": f"Successfully performed '{action}' on {cname}."}
        except NotFound:
            if action == "start":
                logger.info(
                    f"Container {cname} not found. Attempting to create and start it from docker-compose.yml settings."
                )
                try:
                    compose_cfg = get_compose_config(
                        environment_snapshot.compose_path
                    )
                    services = compose_cfg.get("services", {})
                    svc_name = MANAGED_CONTAINERS[container_id]["compose_service"]
                    svc_config = services.get(svc_name, {})

                    ports = svc_config.get("ports", [])
                    env = svc_config.get("environment", {})
                    volumes = svc_config.get("volumes", [])
                    networks = svc_config.get("networks", [])

                    res = self._update_container_config_unlocked(
                        container_id,
                        ports,
                        env,
                        volumes,
                        networks,
                        environment_snapshot=environment_snapshot,
                    )
                    if res.get("success"):
                        return {
                            "success": True,
                            "message": f"Container {cname} was not found, so it was created and started from compose configuration.",
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"Container {cname} not found, and failed to create: {res.get('error')}",
                            "command": res.get("command"),
                            "returncode": res.get("returncode"),
                            "stdout": res.get("stdout"),
                            "stderr": res.get("stderr"),
                            "restoration": res.get("restoration"),
                        }
                except (
                    ComposePostMutationContractError,
                    ComposeCandidateContractError,
                ):
                    raise
                except Exception as create_err:
                    return {
                        "success": False,
                        "error": f"Container {cname} not found, and failed during creation process: {str(create_err)}",
                    }
            else:
                return {
                    "success": False,
                    "error": f"Container {cname} not found. Please start it first to create it.",
                }
        except (
            ComposePostMutationContractError,
            ComposeCandidateContractError,
        ):
            raise
        except Exception as e:
            logger.error(f"Failed to {action} container {cname}: {e}")
            return {"success": False, "error": str(e)}

    def get_container_logs(self, container_id: str, tail: int = 100) -> dict[str, Any]:
        """Retrieve the recent stdout/stderr logs of a container."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

        cname = MANAGED_CONTAINERS[container_id]["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)
            logs = container.logs(tail=tail, stdout=True, stderr=True).decode(
                "utf-8", errors="ignore"
            )
            return {"success": True, "logs": logs}
        except NotFound:
            return {"success": False, "error": f"Container {cname} not found."}
        except Exception as e:
            logger.error(f"Failed to fetch logs for {cname}: {e}")
            return {"success": False, "error": str(e)}

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        """Return a safe, UI-oriented subset of Docker inspect data."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

        # 순환 참조 방지를 위해 메서드 안에서 최신 stats 캐시를 읽는다.
        from kor_travel_docker_manager.services.metrics_collector import metrics_collector

        spec = MANAGED_CONTAINERS[container_id]
        cname = spec["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)
            attrs = container.attrs
            config = attrs.get("Config", {})
            host_config = attrs.get("HostConfig", {})
            network_settings = attrs.get("NetworkSettings", {})
            state = attrs.get("State", {})

            image_id = attrs.get("Image")
            image_tags: list[str] = []
            try:
                image = container.image
                if image is not None:
                    image_tags = [str(tag) for tag in (image.tags or []) if tag]
                if not image_id and image is not None:
                    image_id = getattr(image, "short_id", None)
            except Exception:
                # inspect 본문 전체를 실패시키지 않고 Docker inspect의 image ID만 사용한다.
                pass

            env = [_redact_env_pair(pair) for pair in config.get("Env", [])]
            networks = {
                name: {
                    "network_id": details.get("NetworkID"),
                    "ip_address": details.get("IPAddress"),
                    "gateway": details.get("Gateway"),
                    "mac_address": details.get("MacAddress"),
                    "aliases": details.get("Aliases") or [],
                }
                for name, details in (network_settings.get("Networks") or {}).items()
            }

            return {
                "success": True,
                "container": {
                    "id": container_id,
                    "docker_id": attrs.get("Id"),
                    "name": cname,
                    "display_name": spec["display_name"],
                    "role": spec["role"],
                    "image": config.get("Image"),
                    "image_id": image_id,
                    "image_tags": image_tags,
                    "created": attrs.get("Created"),
                    "status": container.status,
                    "restart_count": attrs.get("RestartCount", 0),
                    "metrics": metrics_collector.get_latest_metric(container_id),
                    "state": {
                        "status": state.get("Status"),
                        "running": state.get("Running"),
                        "paused": state.get("Paused"),
                        "restarting": state.get("Restarting"),
                        "oom_killed": state.get("OOMKilled"),
                        "dead": state.get("Dead"),
                        "exit_code": state.get("ExitCode"),
                        "error": state.get("Error"),
                        "started_at": state.get("StartedAt"),
                        "finished_at": state.get("FinishedAt"),
                        "health": state.get("Health", {}),
                    },
                    "config": {
                        "hostname": config.get("Hostname"),
                        "env": env,
                        "cmd": _redact_argv(config.get("Cmd")),
                        "entrypoint": _redact_argv(config.get("Entrypoint")),
                        "labels": _sanitize_labels(config.get("Labels")),
                        "working_dir": config.get("WorkingDir"),
                    },
                    "host_config": {
                        "restart_policy": host_config.get("RestartPolicy"),
                        "network_mode": host_config.get("NetworkMode"),
                        "port_bindings": host_config.get("PortBindings"),
                        "binds": host_config.get("Binds") or [],
                    },
                    "mounts": [_format_mount(mount) for mount in attrs.get("Mounts", [])],
                    "network": {
                        "ports": network_settings.get("Ports") or {},
                        "networks": networks,
                    },
                },
            }
        except NotFound:
            return {"success": False, "error": f"Container {cname} not found."}
        except Exception as e:
            logger.error(f"Failed to inspect container {cname}: {e}")
            return {"success": False, "error": str(e)}

    def update_container_config(
        self,
        container_id: str,
        new_ports: list[str],
        new_env: dict[str, str],
        new_volumes: list[Any],
        new_networks: list[str],
    ) -> dict[str, Any]:
        """Update docker-compose.yml configuration and recreate the service through Compose."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}
        # lock 획득이나 Docker 접근보다 먼저 검증한다. compose 파일을 읽는 것은 로컬 YAML
        # 읽기라 lock이 필요 없다 — env의 기존(baseline) 값이 이미 `${...}` 보간이었는지
        # 알아야 "보간 → 리터럴 되돌리기"만 정확히 잡고 원래부터 리터럴이던 값(불리언
        # flag 등)은 건드리지 않는다.
        svc_name = MANAGED_CONTAINERS[container_id]["compose_service"]
        baseline_env = (
            get_compose_config().get("services", {}).get(svc_name, {}).get("environment")
        )
        validate_container_config_update(
            ports=new_ports,
            env=new_env,
            networks=new_networks,
            baseline_env=baseline_env if isinstance(baseline_env, dict) else {},
            service_name=svc_name,
        )
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            assert_environment_snapshot_matches_c6c_lock(
                environment_snapshot,
                lock_snapshot,
            )
            assert_manager_mutation_allowed(
                environment=environment_snapshot.effective
            )
            return self._update_container_config_unlocked(
                container_id,
                new_ports,
                new_env,
                new_volumes,
                new_networks,
                environment_snapshot=environment_snapshot,
            )

    def _update_container_config_unlocked(
        self,
        container_id: str,
        new_ports: list[str],
        new_env: dict[str, str],
        new_volumes: list[Any],
        new_networks: list[str],
        *,
        replacement_service_config: dict[str, Any] | None = None,
        environment_snapshot: ComposeEnvironmentSnapshot,
    ) -> dict[str, Any]:
        """검증과 host lock을 이미 확보한 config transaction 구현."""

        spec = MANAGED_CONTAINERS[container_id]
        cname = spec["name"]
        svc_name = spec["compose_service"]

        original_bytes: bytes | None = None
        original_mode: int | None = None
        write_attempted = False
        mutation_succeeded = False
        baseline_transaction: ComposeTransactionSnapshot | None = None
        validation = None
        try:
            compose_path = Path(environment_snapshot.compose_path)
            baseline_transaction, baseline_validation = (
                compose_service._capture_transaction_unlocked(
                    environment_snapshot=environment_snapshot,
                )
            )
            original_bytes = baseline_transaction.compose_source_bytes
            original_mode = baseline_transaction.compose_source_mode
            # 1. Load current docker-compose.yml
            loaded = yaml.safe_load(original_bytes.decode("utf-8")) or {}
            if not isinstance(loaded, dict) or not loaded:
                return {"success": False, "error": "Failed to read docker-compose.yml."}
            locked_services = loaded.get("services", {})
            locked_service = (
                locked_services.get(svc_name, {})
                if isinstance(locked_services, dict)
                else {}
            )
            locked_baseline_env = (
                locked_service.get("environment", {})
                if isinstance(locked_service, dict)
                else {}
            )
            validate_container_config_update(
                ports=new_ports,
                env=new_env,
                networks=new_networks,
                baseline_env=(
                    locked_baseline_env
                    if isinstance(locked_baseline_env, dict)
                    else {}
                ),
                service_name=svc_name,
            )
            compose_cfg = deepcopy(loaded)
            baseline_volume_hash = compose_volume_graph_hash(compose_cfg)

            if "services" not in compose_cfg:
                compose_cfg["services"] = {}
            if svc_name not in compose_cfg["services"]:
                compose_cfg["services"][svc_name] = {}

            if replacement_service_config is not None:
                compose_cfg["services"][svc_name] = deepcopy(
                    replacement_service_config
                )
            else:
                svc_config = compose_cfg["services"][svc_name]

                # 2. Update service settings inside dict
                svc_config["ports"] = new_ports
                svc_config["environment"] = new_env
                svc_config["volumes"] = new_volumes
                if new_networks:
                    svc_config["networks"] = new_networks
                    svc_config.pop("network_mode", None)
                else:
                    svc_config.pop("networks", None)

            # 3. Candidate 전체를 검증한 뒤 docker-compose.yml을 저장한다.
            if compose_volume_graph_hash(compose_cfg) != baseline_volume_hash:
                raise ComposeCandidateContractError(
                    "compose candidate volume configuration is immutable through the Manager API"
                )
            validation = compose_service._capture_candidate_transaction_unlocked(
                compose_cfg,
                baseline_transaction=baseline_transaction,
                baseline_validation=baseline_validation,
            )
            candidate_transaction = validation.transaction_snapshot
            if candidate_transaction is None:
                raise ComposeCandidateContractError(
                    "compose candidate transaction was not captured"
                )
            if compose_path.read_bytes() != original_bytes:
                raise ComposeCandidateContractError(
                    "compose candidate source changed during the config request"
                )
            revalidate_candidate_system_bind_snapshots(
                validation.system_bind_snapshots
            )
            write_attempted = True
            _atomic_write(
                str(compose_path),
                candidate_transaction.compose_source_bytes,
                mode=candidate_transaction.compose_source_mode,
            )
            logger.info(f"Updated docker-compose.yml for service {svc_name}.")

            recreate_result = compose_service.run(
                ["up", "-d", "--force-recreate", svc_name],
                capture_output=True,
                mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                expected_system_bind_snapshots=validation.system_bind_snapshots,
                expected_raw_volume_graph_hash=validation.raw_volume_graph_hash,
                expected_resolved_volume_graph_hash=(
                    validation.resolved_volume_graph_hash
                ),
                expected_environment_snapshot=validation.environment_snapshot,
                expected_external_input_snapshot=(
                    validation.external_input_snapshot
                ),
                transaction=candidate_transaction,
            )
            if not recreate_result.get("success"):
                restoration = self._restore_compose_transaction(
                    original_bytes,
                    original_mode,
                    svc_name,
                    baseline_transaction,
                )
                return {
                    "success": False,
                    "error": (
                        "docker compose recreate failed: "
                        f"{recreate_result.get('stderr') or recreate_result.get('stdout')}"
                    ),
                    "command": recreate_result.get("command"),
                    "returncode": recreate_result.get("returncode"),
                    "stdout": recreate_result.get("stdout"),
                    "stderr": recreate_result.get("stderr"),
                    "restoration": restoration,
                }
            mutation_succeeded = True

            # RustFS 재생성 후에는 compose에 정의된 init service를 그대로 실행해 bucket을 보정한다.
            if container_id == "rustfs":
                init_result = compose_service.run(
                    ["run", "--rm", "rustfs-init"],
                    capture_output=True,
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    expected_system_bind_snapshots=validation.system_bind_snapshots,
                    expected_raw_volume_graph_hash=(
                        validation.raw_volume_graph_hash
                    ),
                    expected_resolved_volume_graph_hash=(
                        validation.resolved_volume_graph_hash
                    ),
                    expected_environment_snapshot=(
                        validation.environment_snapshot
                    ),
                    expected_external_input_snapshot=(
                        validation.external_input_snapshot
                    ),
                    transaction=candidate_transaction,
                )
                if not init_result.get("success"):
                    restoration = self._restore_compose_transaction(
                        original_bytes,
                        original_mode,
                        svc_name,
                        baseline_transaction,
                    )
                    return {
                        "success": False,
                        "error": (
                            "rustfs bucket initialization failed: "
                            f"{init_result.get('stderr') or init_result.get('stdout')}"
                        ),
                        "command": init_result.get("command"),
                        "returncode": init_result.get("returncode"),
                        "stdout": init_result.get("stdout"),
                        "stderr": init_result.get("stderr"),
                        "restoration": restoration,
                    }

            return {
                "success": True,
                "message": f"Successfully updated config and recreated {cname}.",
            }
        except ComposeCandidateContractError as exc:
            restore_required = write_attempted
            if original_bytes is not None and original_mode is not None:
                try:
                    compose_path = Path(environment_snapshot.compose_path)
                    restore_required = restore_required or (
                        compose_path.read_bytes() != original_bytes
                        or compose_path.stat().st_mode & 0o777 != original_mode
                    )
                except OSError:
                    restore_required = True
            if restore_required and original_bytes is not None and original_mode is not None:
                if mutation_succeeded:
                    try:
                        restoration = self._restore_compose_transaction(
                            original_bytes,
                            original_mode,
                            svc_name,
                            baseline_transaction,
                        )
                        recovery_succeeded = bool(
                            restoration.get("config_restored")
                            and restoration.get("runtime_restored")
                        )
                        recovery_error = restoration.get("error")
                    except Exception as recovery_exc:
                        restoration = {
                            "config_restored": False,
                            "runtime_restored": False,
                            "error": str(recovery_exc),
                        }
                        recovery_succeeded = False
                        recovery_error = str(recovery_exc)
                    raise ComposePostMutationContractError(
                        exc,
                        recovery_attempted=True,
                        recovery_succeeded=recovery_succeeded,
                        recovery_error=(
                            None
                            if recovery_succeeded
                            else str(recovery_error or "recovery failed")
                        ),
                        restoration=restoration,
                    ) from exc
                try:
                    _atomic_write(
                        environment_snapshot.compose_path,
                        original_bytes,
                        mode=original_mode,
                    )
                except Exception as recovery_exc:
                    restoration = {
                        "config_restored": False,
                        "runtime_restored": False,
                        "runtime_recovery_attempted": False,
                        "durable_config_mutation": True,
                        "error": str(recovery_exc),
                    }
                    raise ComposePostMutationContractError(
                        exc,
                        recovery_attempted=True,
                        recovery_succeeded=False,
                        recovery_error=str(recovery_exc),
                        restoration=restoration,
                    ) from exc
            raise
        except Exception as e:
            logger.error(f"Failed to update config for {cname}: {e}")
            restoration = None
            if write_attempted and original_bytes is not None and original_mode is not None:
                restoration = self._restore_compose_transaction(
                    original_bytes,
                    original_mode,
                    svc_name,
                    baseline_transaction,
                )
            return {"success": False, "error": str(e), "restoration": restoration}

    @staticmethod
    def _restore_compose_transaction(
        original_bytes: bytes,
        original_mode: int,
        svc_name: str,
        transaction: ComposeTransactionSnapshot | None = None,
    ) -> dict[str, Any]:
        try:
            compose_path = (
                transaction.environment.compose_path
                if transaction is not None
                else _get_compose_path()
            )
            _atomic_write(compose_path, original_bytes, mode=original_mode)
        except Exception as exc:
            logger.error("Failed to restore compose config for %s: %s", svc_name, exc)
            return {
                "config_restored": False,
                "runtime_restored": False,
                "error": str(exc),
            }
        try:
            if transaction is None:
                raise ComposeCandidateContractError(
                    "compose restoration has no baseline transaction"
                )
            recreate_result = compose_service._run_frozen_recovery(
                ["up", "-d", "--force-recreate", svc_name],
                capture_output=True,
                mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                transaction=transaction,
            )
            return {
                "config_restored": True,
                "runtime_restored": bool(recreate_result.get("success")),
                "command": recreate_result.get("command"),
                "returncode": recreate_result.get("returncode"),
                "stdout": recreate_result.get("stdout"),
                "stderr": recreate_result.get("stderr"),
                "error": (
                    None
                    if recreate_result.get("success")
                    else str(
                        recreate_result.get("stderr")
                        or recreate_result.get("stdout")
                        or "docker compose runtime restoration failed"
                    )
                ),
            }
        except Exception as exc:
            logger.error("Failed to restore compose runtime for %s: %s", svc_name, exc)
            return {
                "config_restored": True,
                "runtime_restored": False,
                "error": str(exc),
            }

    def reset_container_config(self, container_id: str) -> dict[str, Any]:
        """Reset container configuration in docker-compose.yml to default and recreate it."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}
        with c6c_deployment_lock_from_environment() as lock_snapshot:
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            assert_environment_snapshot_matches_c6c_lock(
                environment_snapshot,
                lock_snapshot,
            )
            assert_manager_mutation_allowed(
                environment=environment_snapshot.effective
            )
            return self._reset_container_config_unlocked(
                container_id,
                environment_snapshot=environment_snapshot,
            )

    def _reset_container_config_unlocked(
        self,
        container_id: str,
        *,
        environment_snapshot: ComposeEnvironmentSnapshot,
    ) -> dict[str, Any]:
        """기본값 계산부터 재생성까지 한 config transaction으로 수행한다."""

        if not self._default_compose_config:
            return {"success": False, "error": "No default config backup available."}

        spec = MANAGED_CONTAINERS[container_id]
        svc_name = spec["compose_service"]

        default_services = self._default_compose_config.get("services", {})
        if svc_name not in default_services:
            return {
                "success": False,
                "error": f"Service {svc_name} not found in default config backup.",
            }

        default_svc_config = deepcopy(default_services[svc_name])

        # Recreate container with default settings
        ports = default_svc_config.get("ports", [])
        env = default_svc_config.get("environment", {})
        volumes = default_svc_config.get("volumes", [])
        networks = default_svc_config.get("networks", [])

        return self._update_container_config_unlocked(
            container_id,
            ports,
            env,
            volumes,
            networks,
            replacement_service_config=default_svc_config,
            environment_snapshot=environment_snapshot,
        )


docker_service = DockerService()
