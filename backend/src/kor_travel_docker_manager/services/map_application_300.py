"""Pure contract artifacts for Map application fresh ``300``.

The orchestration layer owns Docker, volumes, credentials, and command
execution.  This module owns only the secret-free JSON contracts that make
those effects resumable: strict parsing, canonical bytes, SHA-256 binding, and
owner-only host artifact writes.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID

#: application active graph의 유일한 root. `0236 → 300` handoff의 stamp 목적지이자
#: "Dagster metadata DB는 application raw revision을 갖지 않는다"는 격리 선언이 가리키는
#: 값이다. **현재 head가 아니라 역사적 좌표**이므로 migration이 쌓여도 바뀌지 않는다.
BASELINE_ROOT_REVISION: Final = "300"

#: candidate가 선언한 application head의 문법. Map `PinnedRuntimeGeneration`의
#: `_SCHEMA_HEAD`와 같은 패턴을 쓴다 — 값을 고정하지 않고 형식만 본다.
_SCHEMA_HEAD: Final = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")


def _require_schema_head(value: Any, field: str) -> str:
    """application head가 문법에 맞는 revision 문자열인지 확인한다.

    **값을 고정하지 않는다.** 종전에는 `"300"`과의 exact 비교였는데, 그러면 Map이
    migration을 하나만 더해도 Manager가 candidate를 거절한다. 이 값이 신뢰되는 근거는
    리터럴 일치가 아니라 `_canonical_digest(contract)`가 head를 **포함해** 해시되고
    그 digest가 paired receipt → candidate evidence → journal로 전파돼 재대조된다는
    점이다(`map_application_candidate.py`). 즉 결박은 이미 암호학적으로 존재하고,
    리터럴 비교는 그 위에 얹힌 값 고정일 뿐이었다.
    """
    if not isinstance(value, str) or not _SCHEMA_HEAD.fullmatch(value):
        raise MapApplication300ContractError(f"{field} is not a valid alembic revision")
    return value
APPLICATION_DATABASE_OWNER: Final = "ktm_feature_schema_owner"

DAGSTER_STORAGE_PERMIT_SCHEMA: Final = (
    "kor-travel-map.dagster-storage-database-permit.v2"
)
DAGSTER_STORAGE_PERMIT_AUTHORITY: Final = "docker-manager"

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_DATABASE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_ROLE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_DAGSTER_STORAGE_PERMIT_FIELDS: Final = frozenset(
    {
        "schema",
        "authority",
        "operation_id",
        "candidate",
        "dagster_database",
        "application_database",
    }
)
_DAGSTER_STORAGE_CANDIDATE_FIELDS: Final = frozenset(
    {"dagster_image_id", "dagster_config_sha256"}
)
_DAGSTER_DATABASE_FIELDS: Final = frozenset(
    {
        "system_identifier",
        "name",
        "oid",
        "owner",
        "login_role",
        "login_role_attributes",
    }
)
_DAGSTER_LOGIN_ROLE_ATTRIBUTE_FIELDS: Final = frozenset(
    {
        "can_login",
        "inherit",
        "superuser",
        "create_database",
        "create_role",
        "replication",
        "bypass_rls",
        "connection_limit",
        "valid_until_is_null",
        "role_config_count",
        "database_role_setting_count",
        "granted_role_count",
        "member_role_count",
    }
)
_DAGSTER_APPLICATION_DATABASE_FIELDS: Final = frozenset(
    {"system_identifier", "name", "oid", "owner"}
)


class MapApplication300ContractError(ValueError):
    """Raised when a Map application ``300`` artifact is not exact."""


@dataclass(frozen=True)
class JsonArtifact:
    """Canonical JSON bytes plus the SHA-256 needed for journal binding."""

    payload: Mapping[str, Any]
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class HostArtifactReceipt:
    """Result of an owner-only host artifact write."""

    path: Path
    sha256: str
    size: int



def expected_application_300_source_commit() -> str:
    """application ``300`` candidate가 가져야 하는 Map source commit.

    이전에는 이 값이 ``MAP_APPLICATION_300_SOURCE_COMMIT`` 상수로 여기 한 번, pinned
    release pin으로 또 한 번 이원 관리됐다. 두 값이 어긋나도 런타임 비교가 없어
    candidate admission에서야 늦게 실패했고 유일한 방어선이 테스트 한 줄이었다.
    이제 단일 registry에서만 읽으므로 그 hazard 자체가 소멸한다.

    import는 함수 안에서 한다 — ``c6c_deployment``가 이 모듈을 import하므로
    module-level import는 순환이 된다.
    """

    from kor_travel_docker_manager.services.pinned_runtime_release import (
        current_map_source_revision,
    )

    return current_map_source_revision()


@dataclass(frozen=True)
class Application300Candidate:
    """Map application ``300`` API/Dagster image identity."""

    map_source_commit: str
    api_image_id: str
    dagster_image_id: str

    def __post_init__(self) -> None:
        commit = _require_commit(self.map_source_commit, "map_source_commit")
        if commit != expected_application_300_source_commit():
            raise MapApplication300ContractError(
                "Map application 300 source commit is not the fixed release candidate"
            )
        _require_image_id(self.api_image_id, "api_image_id")
        _require_image_id(self.dagster_image_id, "dagster_image_id")


@dataclass(frozen=True)
class ApplicationDatabaseIdentity:
    """Non-secret identity of the Map application database."""

    name: str
    oid: int
    owner: str
    system_identifier: str

    def __post_init__(self) -> None:
        _require_database_name(self.name, "database name")
        _require_positive_int(self.oid, "database oid")
        _require_role_name(self.owner, "database owner")
        if self.owner != APPLICATION_DATABASE_OWNER:
            raise MapApplication300ContractError("application database owner is invalid")
        _require_system_identifier(self.system_identifier, "postgres system identifier")

    def to_dagster_permit_application_payload(self) -> dict[str, Any]:
        return {
            "system_identifier": self.system_identifier,
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
        }


@dataclass(frozen=True)
class DagsterLoginRoleAttributes:
    """Privileges that must stay absent from the Dagster metadata login role."""

    superuser: bool = False
    create_database: bool = False
    create_role: bool = False
    replication: bool = False
    bypass_rls: bool = False
    granted_role_count: int = 0
    member_role_count: int = 0
    can_login: bool = True
    inherit: bool = False
    connection_limit: int = -1
    valid_until_is_null: bool = True
    role_config_count: int = 0
    database_role_setting_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "superuser",
            "create_database",
            "create_role",
            "replication",
            "bypass_rls",
        ):
            if getattr(self, name) is not False:
                raise MapApplication300ContractError(
                    "Dagster metadata login role has unsafe privileges"
                )
        if self.can_login is not True or self.inherit is not False:
            raise MapApplication300ContractError(
                "Dagster metadata login role has unsafe login attributes"
            )
        if self.connection_limit != -1 or self.valid_until_is_null is not True:
            raise MapApplication300ContractError(
                "Dagster metadata login role has persistent connection limits"
            )
        for name in ("role_config_count", "database_role_setting_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                raise MapApplication300ContractError(
                    "Dagster metadata login role has persistent settings"
                )
        for name in ("granted_role_count", "member_role_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                raise MapApplication300ContractError(
                    "Dagster metadata login role has role memberships"
                )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> DagsterLoginRoleAttributes:
        payload = _require_exact_fields(
            value,
            _DAGSTER_LOGIN_ROLE_ATTRIBUTE_FIELDS,
            "Dagster login role attributes",
        )
        return cls(
            can_login=_require_bool(payload["can_login"], "can_login"),
            inherit=_require_bool(payload["inherit"], "inherit"),
            superuser=_require_bool(payload["superuser"], "superuser"),
            create_database=_require_bool(
                payload["create_database"], "create_database"
            ),
            create_role=_require_bool(payload["create_role"], "create_role"),
            replication=_require_bool(payload["replication"], "replication"),
            bypass_rls=_require_bool(payload["bypass_rls"], "bypass_rls"),
            connection_limit=_require_connection_limit(
                payload["connection_limit"], "connection_limit"
            ),
            valid_until_is_null=_require_bool(
                payload["valid_until_is_null"], "valid_until_is_null"
            ),
            role_config_count=_require_non_negative_int(
                payload["role_config_count"], "role_config_count"
            ),
            database_role_setting_count=_require_non_negative_int(
                payload["database_role_setting_count"],
                "database_role_setting_count",
            ),
            granted_role_count=_require_non_negative_int(
                payload["granted_role_count"], "granted_role_count"
            ),
            member_role_count=_require_non_negative_int(
                payload["member_role_count"], "member_role_count"
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "can_login": self.can_login,
            "inherit": self.inherit,
            "superuser": self.superuser,
            "create_database": self.create_database,
            "create_role": self.create_role,
            "replication": self.replication,
            "bypass_rls": self.bypass_rls,
            "connection_limit": self.connection_limit,
            "valid_until_is_null": self.valid_until_is_null,
            "role_config_count": self.role_config_count,
            "database_role_setting_count": self.database_role_setting_count,
            "granted_role_count": self.granted_role_count,
            "member_role_count": self.member_role_count,
        }


@dataclass(frozen=True)
class DagsterDatabaseIdentity:
    """Non-secret identity of the Dagster metadata database."""

    system_identifier: str
    name: str
    oid: int
    owner: str
    login_role: str
    login_role_attributes: DagsterLoginRoleAttributes

    def __post_init__(self) -> None:
        _require_system_identifier(self.system_identifier, "postgres system identifier")
        _require_database_name(self.name, "Dagster database name")
        _require_positive_int(self.oid, "Dagster database oid")
        _require_role_name(self.owner, "Dagster database owner")
        _require_role_name(self.login_role, "Dagster login role")
        if self.owner != self.login_role:
            raise MapApplication300ContractError(
                "Dagster metadata owner must equal login role"
            )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> DagsterDatabaseIdentity:
        payload = _require_exact_fields(
            value, _DAGSTER_DATABASE_FIELDS, "Dagster database identity"
        )
        return cls(
            system_identifier=_require_system_identifier(
                payload["system_identifier"], "system_identifier"
            ),
            name=_require_database_name(payload["name"], "name"),
            oid=_require_positive_int(payload["oid"], "oid"),
            owner=_require_role_name(payload["owner"], "owner"),
            login_role=_require_role_name(payload["login_role"], "login_role"),
            login_role_attributes=DagsterLoginRoleAttributes.from_payload(
                _require_mapping(
                    payload["login_role_attributes"], "login_role_attributes"
                )
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "system_identifier": self.system_identifier,
            "name": self.name,
            "oid": self.oid,
            "owner": self.owner,
            "login_role": self.login_role,
            "login_role_attributes": self.login_role_attributes.to_payload(),
        }


@dataclass(frozen=True)
class DagsterStorageCandidate:
    """Candidate inputs for the Dagster metadata permit."""

    dagster_image_id: str
    dagster_config_sha256: str

    def __post_init__(self) -> None:
        _require_image_id(self.dagster_image_id, "dagster_image_id")
        _require_sha256(self.dagster_config_sha256, "dagster_config_sha256")

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> DagsterStorageCandidate:
        payload = _require_exact_fields(
            value, _DAGSTER_STORAGE_CANDIDATE_FIELDS, "Dagster storage candidate"
        )
        return cls(
            dagster_image_id=_require_image_id(
                payload["dagster_image_id"], "dagster_image_id"
            ),
            dagster_config_sha256=_require_sha256(
                payload["dagster_config_sha256"], "dagster_config_sha256"
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "dagster_image_id": self.dagster_image_id,
            "dagster_config_sha256": self.dagster_config_sha256,
        }


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the canonical JSON form used for Manager-owned receipts."""

    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def json_artifact(payload: Mapping[str, Any]) -> JsonArtifact:
    raw = canonical_json_bytes(payload)
    return JsonArtifact(payload=payload, raw=raw, sha256=sha256_bytes(raw))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


#: `application_database_identity_sha256`의 domain separation 접두사.
#:
#: 값은 옛 이름(`...application-final-permit-database.v1`)을 그대로 둔다. final permit
#: 자체는 ADR-101에서 사라졌지만 이 digest는 저널과 metadata permit에 실려 계속 쓰이고,
#: 문자열을 바꾸면 같은 데이터베이스에 대해 다른 digest가 나온다.
APPLICATION_DATABASE_IDENTITY_DIGEST_DOMAIN: Final = (
    "kor-travel-map.application-final-permit-database.v1"
)


def application_database_identity_sha256(identity: ApplicationDatabaseIdentity) -> str:
    value = (
        f"{APPLICATION_DATABASE_IDENTITY_DIGEST_DOMAIN}\0"
        f"{identity.system_identifier}\0{identity.name}\0{identity.oid}\0{identity.owner}"
    )
    return sha256_bytes(value.encode("utf-8"))


def build_dagster_metadata_permit(
    *,
    candidate: DagsterStorageCandidate,
    dagster_database: DagsterDatabaseIdentity,
    application_database: ApplicationDatabaseIdentity,
    operation_id: str,
) -> JsonArtifact:
    canonical_operation_id = _require_uuid(operation_id, "operation_id")
    _require_metadata_database_isolation(
        dagster_database=dagster_database, application_database=application_database
    )
    payload = {
        "schema": DAGSTER_STORAGE_PERMIT_SCHEMA,
        "authority": DAGSTER_STORAGE_PERMIT_AUTHORITY,
        "operation_id": canonical_operation_id,
        "candidate": candidate.to_payload(),
        "dagster_database": dagster_database.to_payload(),
        "application_database": application_database.to_dagster_permit_application_payload(),
    }
    validate_dagster_metadata_permit(
        json_artifact(payload).raw,
        expected_candidate=candidate,
        application_database=application_database,
        expected_operation_id=canonical_operation_id,
    )
    return json_artifact(payload)


def validate_dagster_metadata_permit(
    raw: bytes,
    *,
    expected_candidate: DagsterStorageCandidate,
    application_database: ApplicationDatabaseIdentity,
    expected_operation_id: str | None = None,
) -> Mapping[str, Any]:
    payload = _load_exact_json(
        raw, _DAGSTER_STORAGE_PERMIT_FIELDS, "Dagster metadata permit"
    )
    if (
        payload["schema"] != DAGSTER_STORAGE_PERMIT_SCHEMA
        or payload["authority"] != DAGSTER_STORAGE_PERMIT_AUTHORITY
    ):
        raise MapApplication300ContractError("Dagster metadata permit identity is invalid")
    operation_id = _require_uuid(payload["operation_id"], "operation_id")
    if expected_operation_id is not None and operation_id != _require_uuid(
        expected_operation_id, "expected_operation_id"
    ):
        raise MapApplication300ContractError(
            "Dagster metadata permit operation binding is invalid"
        )
    candidate = DagsterStorageCandidate.from_payload(
        _require_mapping(payload["candidate"], "candidate")
    )
    if candidate != expected_candidate:
        raise MapApplication300ContractError("Dagster metadata candidate is invalid")
    dagster_database = DagsterDatabaseIdentity.from_payload(
        _require_mapping(payload["dagster_database"], "dagster_database")
    )
    app_identity = _require_exact_fields(
        payload["application_database"],
        _DAGSTER_APPLICATION_DATABASE_FIELDS,
        "Dagster permit application database",
    )
    observed_application = ApplicationDatabaseIdentity(
        system_identifier=_require_system_identifier(
            app_identity["system_identifier"], "system_identifier"
        ),
        name=_require_database_name(app_identity["name"], "name"),
        oid=_require_positive_int(app_identity["oid"], "oid"),
        owner=_require_role_name(app_identity["owner"], "owner"),
    )
    if observed_application != application_database:
        raise MapApplication300ContractError(
            "Dagster metadata permit application identity is invalid"
        )
    _require_metadata_database_isolation(
        dagster_database=dagster_database, application_database=observed_application
    )
    return payload


def publish_root_read_only_artifact(path: Path, raw: bytes) -> HostArtifactReceipt:
    """Publish a root-owned mode ``0444`` fixed-mount artifact atomically."""

    if os.geteuid() != 0:
        raise MapApplication300ContractError(
            "fixed artifact publishing requires root"
        )
    _require_artifact_path(path)
    parent = path.parent
    _require_fixed_artifact_directory(parent)
    if path.exists() or path.is_symlink():
        return _verify_existing_fixed_artifact(path, raw)

    digest = sha256_bytes(raw)
    # GM-10: services/secure_state_file.py에 이 패턴의 정본이 있다. 이 자리는
    # 개별 소유권 정책 검토 없이 옮기지 않기로 결정돼 아직 남아 있다(docs/tasks.md).
    descriptor, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(descriptor, 0o444)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp_path, path, follow_symlinks=False)
        except FileExistsError:
            return _verify_existing_fixed_artifact(path, raw)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                return _verify_existing_fixed_artifact(path, raw)
            raise
        finally:
            _safe_unlink(tmp_path)
        _fsync_directory(parent)
        _verify_existing_fixed_artifact(path, raw)
        return HostArtifactReceipt(path=path, sha256=digest, size=len(raw))
    except Exception:
        _safe_unlink(tmp_path)
        raise


def _require_metadata_database_isolation(
    *,
    dagster_database: DagsterDatabaseIdentity,
    application_database: ApplicationDatabaseIdentity,
) -> None:
    if dagster_database.system_identifier != application_database.system_identifier:
        raise MapApplication300ContractError(
            "Dagster metadata database must share the selected PostgreSQL system"
        )
    if dagster_database.owner == application_database.owner or (
        dagster_database.name,
        dagster_database.oid,
    ) == (application_database.name, application_database.oid):
        raise MapApplication300ContractError(
            "Dagster metadata database must not target the application database"
        )


def _require_exact_fields(
    value: object, expected: frozenset[str], label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise MapApplication300ContractError(f"{label} field set is invalid")
    return value


def _require_at_least_fields(
    value: object, required: frozenset[str], label: str
) -> Mapping[str, Any]:
    """required ⊆ observed — result/receipt 전용.

    **일어난 일을 서술하는 문서**(result/receipt)는 미지의 추가 필드를 거부하지 않는다.
    무결성은 `payload_sha256`이 전체 바이트를 결박하므로(미지 필드도 해시에 포함) 위조
    불가이고, exact-set을 유지하면 emitter(Map image)와 parser(Manager host)가 필드
    하나마다 lockstep 배포돼야 한다 — receipt 필드 2개 추가가 2-repo 원자 배포를 요구한
    것이 실측이다(감사 I-9). **쓰기를 인가하는 문서**(fence·permit)는 exact-set을
    유지한다 — 그쪽은 미지 필드가 인가 범위를 넓힐 수 있다.
    """
    if not isinstance(value, Mapping) or not required.issubset(value):
        raise MapApplication300ContractError(f"{label} field set is invalid")
    return value


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MapApplication300ContractError(f"{label} must be an object")
    return value


def _load_exact_json(
    raw: bytes,
    expected: frozenset[str],
    label: str,
    *,
    canonical_line: bool = False,
    forbid_extra: bool = True,
) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > 64 * 1024:
        raise MapApplication300ContractError(f"{label} JSON is invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapApplication300ContractError(f"{label} JSON is invalid") from exc
    if forbid_extra:
        payload = _require_exact_fields(value, expected, label)
    else:
        payload = _require_at_least_fields(value, expected, label)
    if canonical_line and raw != canonical_json_bytes(payload) + b"\n":
        raise MapApplication300ContractError(f"{label} JSON is not canonical")
    return payload


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} digest is invalid")
    return value


def _require_image_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _IMAGE_ID_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} image id is invalid")
    return value


def _require_commit(value: object, label: str) -> str:
    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} commit is invalid")
    return value


def _require_uuid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise MapApplication300ContractError(f"{label} UUID is invalid") from exc


def _require_positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MapApplication300ContractError(f"{label} must be a positive integer")
    return value


def _require_non_negative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MapApplication300ContractError(f"{label} must be a non-negative integer")
    return value


def _require_connection_limit(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < -1:
        raise MapApplication300ContractError(f"{label} must be a connection limit")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise MapApplication300ContractError(f"{label} must be boolean")
    return value


def _require_database_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _DATABASE_NAME_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} database name is invalid")
    return value


def _require_role_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _ROLE_NAME_PATTERN.fullmatch(value) is None:
        raise MapApplication300ContractError(f"{label} role name is invalid")
    return value


def _require_system_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isdigit():
        raise MapApplication300ContractError(f"{label} is invalid")
    return value


def _require_artifact_path(path: Path) -> None:
    if not path.is_absolute():
        raise MapApplication300ContractError("artifact path must be absolute")
    if path.name in {"", ".", ".."}:
        raise MapApplication300ContractError("artifact path is invalid")
    if path.is_symlink():
        raise MapApplication300ContractError("artifact path is unsafe")
    if path != path.resolve(strict=False):
        raise MapApplication300ContractError("artifact path must be canonical")


def _require_fixed_artifact_directory(path: Path) -> None:
    """root만 쓰고 비-root가 읽을 수 있는 fixed mount를 검증한다.

    **호출자의 euid는 보지 않는다.** 이 헬퍼가 지키는 것은 "이 디렉터리를 root만 쓸 수
    있었는가"이고, 그 답은 디렉터리의 소유자와 mode(`uid 0`, `0755`)에 전부 들어 있다.
    여기에 euid 조건을 얹으면 **읽기까지 root 전용이 된다** — 컨테이너와 진단 경로가
    fence·permit을 읽지 못하게 되는데, 그 아티팩트는 애초에 `0444`로 세상에 읽히라고
    만든 것이다.

    쓰기는 진입점 ``publish_root_read_only_artifact`` 하나가 root를 요구하므로,
    이 조건이 빠져도
    발행 권한은 좁아진 채로 남는다.
    """

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MapApplication300ContractError(
            "fixed artifact directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise MapApplication300ContractError(
            "fixed artifact directory is unsafe"
        )


def _verify_existing_fixed_artifact(
    path: Path, expected: bytes
) -> HostArtifactReceipt:
    observed = _read_existing_fixed_artifact(path, max_size=len(expected) + 1)
    if observed != expected:
        raise MapApplication300ContractError(
            "fixed artifact already exists with different bytes"
        )
    return HostArtifactReceipt(
        path=path, sha256=sha256_bytes(expected), size=len(expected)
    )


def _read_existing_fixed_artifact(path: Path, *, max_size: int = 64 * 1024) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MapApplication300ContractError("fixed artifact is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o444
        or metadata.st_nlink != 1
    ):
        raise MapApplication300ContractError("fixed artifact metadata is unsafe")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != 0
                or stat.S_IMODE(opened.st_mode) != 0o444
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino)
                != (metadata.st_dev, metadata.st_ino)
            ):
                raise MapApplication300ContractError(
                    "fixed artifact changed while opening"
                )
            observed = os.read(descriptor, max_size + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise MapApplication300ContractError(
            "fixed artifact cannot be read safely"
        ) from exc
    if len(observed) > max_size:
        raise MapApplication300ContractError("fixed artifact is too large")
    return observed


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
