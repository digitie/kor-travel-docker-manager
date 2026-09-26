"""배포 상태 파일 하나 — 마이그레이션 전진 재구축이 실행 사이에 남기는 유일한 상태(ADR-51).

pinset별 journal·phase·receipt 대신 전역 파일 하나에 세 상태만 둔다.

- 파일 없음: 이 Manager가 아직 한 번도 배포를 끝내지 않았다. 기준선 없는 전체 경로 한 번을
  돈다 — v6 manifest·v8 journal은 읽지도 넘겨받지도 않는다.
- ``in_progress``: 배포가 시작돼 무언가를 바꿨다. 다음 실행은 처음부터 다시 돈다 — 모든
  단계가 멱등이라 재개가 필요 없다.
- ``committed``: 마지막 배포가 끝났다. 같은 pair를 다시 돌리면 빌드 없이 수렴만 한다.

``databases``는 직전 committed 배포가 관측한 DB identity다. 배포 시작 전에 라이브 DB가 이것과
다르면(누가 지우고 다시 만들었다) 아무것도 바꾸기 전에 거부한다. 명시적 ``--restart``만 이
기준을 지운 DB로 다시 잡고, 명시적 ``--adopt-live-databases``는 지금 떠 있는 DB를 새
기준으로 받아들인다(백업 복원처럼 비파괴로 DB가 바뀐 경우). 비밀은 담지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import DatabaseRole

DEPLOY_STATUS_FILENAME: Final = "deploy-status.json"
DeployState = Literal["in_progress", "committed"]

_VERSION: Final = 1
_DATABASE_ROLES: Final[tuple[DatabaseRole, ...]] = ("map_application", "map_dagster", "pinvi")
_SCHEMA_ROLES: Final = ("map_application", "map_dagster", "pinvi")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SERVICE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SCHEMA_HEAD = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_SYSTEM_IDENTIFIER = re.compile(r"^[0-9]{1,20}$")
_MAX_BYTES: Final = 64 * 1024
_MAX_TEXT: Final = 200
_FIELDS: Final = frozenset(
    {
        "version",
        "state",
        "run_id",
        "started_at",
        "committed_at",
        "step",
        "manager_revision",
        "map_revision",
        "pinvi_revision",
        "pinset_sha256",
        "images",
        "schema_heads",
        "databases",
        "restart",
        "adopted",
        "carried_over_from",
    }
)


def _is_text(value: object, *, optional: bool = False) -> bool:
    if value is None:
        return optional
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_TEXT
        and "\n" not in value
        and "\r" not in value
    )


@dataclass(frozen=True)
class DeployedDatabase:
    """배포가 관측한 DB 하나의 identity. 다시 만들면 oid가 바뀐다."""

    name: str
    oid: int
    system_identifier: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or _IDENTIFIER.fullmatch(self.name) is None
            or type(self.oid) is not int
            or self.oid <= 0
            or not isinstance(self.system_identifier, str)
            or _SYSTEM_IDENTIFIER.fullmatch(self.system_identifier) is None
        ):
            raise DeploymentContractError("deploy status database identity is invalid")

    def to_payload(self) -> dict[str, object]:
        return {"name": self.name, "oid": self.oid, "system_identifier": self.system_identifier}


@dataclass(frozen=True)
class DeployRestart:
    """명시적 결정(``--restart``·``--adopt-live-databases``)의 기록. 사유는 사람이 쓴 한 줄이다."""

    reason: str
    at: str

    def __post_init__(self) -> None:
        if (
            not _is_text(self.reason)
            or not self.reason.strip()
            or not _is_text(self.at)
        ):
            raise DeploymentContractError("deploy status restart record is invalid")

    def to_payload(self) -> dict[str, object]:
        return {"reason": self.reason, "at": self.at}


@dataclass(frozen=True)
class DeployStatus:
    """``deploy-status.json``의 내용."""

    state: DeployState
    run_id: str
    started_at: str
    manager_revision: str
    map_revision: str
    pinvi_revision: str
    pinset_sha256: str
    databases: Mapping[DatabaseRole, DeployedDatabase] | None
    step: str | None = None
    committed_at: str | None = None
    images: Mapping[str, str] = field(default_factory=dict)
    schema_heads: Mapping[str, str] = field(default_factory=dict)
    restart: DeployRestart | None = None
    adopted: DeployRestart | None = None
    # v6/v8 넘겨받기는 없어졌다(ADR-51 B3). 지금은 아무도 채우지 않지만 키는 파일 계약이다 —
    # 읽기가 키 집합을 정확히 요구하므로 지우려면 ``_VERSION``을 올려야 한다.
    carried_over_from: str | None = None

    def __post_init__(self) -> None:
        if self.state not in ("in_progress", "committed"):
            raise DeploymentContractError("deploy status state is invalid")
        if not isinstance(self.run_id, str) or not _is_canonical_uuid(self.run_id):
            raise DeploymentContractError("deploy status run id is invalid")
        if (
            not _is_text(self.started_at)
            or not _is_text(self.committed_at, optional=True)
            or not _is_text(self.step, optional=True)
            or not _is_text(self.carried_over_from, optional=True)
        ):
            raise DeploymentContractError("deploy status text field is invalid")
        for revision in (self.manager_revision, self.map_revision, self.pinvi_revision):
            if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
                raise DeploymentContractError("deploy status revision is invalid")
        if not isinstance(self.pinset_sha256, str) or _SHA256.fullmatch(self.pinset_sha256) is None:
            raise DeploymentContractError("deploy status pinset digest is invalid")
        if self.databases is not None and (
            not isinstance(self.databases, Mapping)
            or set(self.databases) != set(_DATABASE_ROLES)
            or not all(isinstance(value, DeployedDatabase) for value in self.databases.values())
        ):
            raise DeploymentContractError("deploy status databases are invalid")
        if not isinstance(self.images, Mapping) or not all(
            isinstance(service, str)
            and isinstance(image_id, str)
            and _SERVICE.fullmatch(service) is not None
            and _IMAGE_ID.fullmatch(image_id) is not None
            for service, image_id in self.images.items()
        ):
            raise DeploymentContractError("deploy status image is invalid")
        if not isinstance(self.schema_heads, Mapping) or (
            self.schema_heads
            and (
                set(self.schema_heads) != set(_SCHEMA_ROLES)
                or not all(
                    isinstance(head, str) and _SCHEMA_HEAD.fullmatch(head) is not None
                    for head in self.schema_heads.values()
                )
            )
        ):
            raise DeploymentContractError("deploy status schema heads are invalid")
        for record in (self.restart, self.adopted):
            if record is not None and not isinstance(record, DeployRestart):
                raise DeploymentContractError("deploy status restart record is invalid")
        if self.restart is not None and self.adopted is not None:
            raise DeploymentContractError("a deploy either restarts or adopts, not both")
        if self.state == "committed":
            if (
                self.committed_at is None
                or not self.images
                or not self.schema_heads
                or self.databases is None
            ):
                raise DeploymentContractError("committed deploy status is incomplete")
        elif self.committed_at is not None:
            raise DeploymentContractError("in-progress deploy status has a commit time")
        # 매핑은 호출자 사본에서 떼어 둔다 — 쓰고 나서 바뀌면 파일과 메모리가 갈린다.
        object.__setattr__(self, "images", MappingProxyType(dict(self.images)))
        object.__setattr__(self, "schema_heads", MappingProxyType(dict(self.schema_heads)))
        if self.databases is not None:
            object.__setattr__(self, "databases", MappingProxyType(dict(self.databases)))

    def to_payload(self) -> dict[str, object]:
        return {
            "version": _VERSION,
            "state": self.state,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "committed_at": self.committed_at,
            "step": self.step,
            "manager_revision": self.manager_revision,
            "map_revision": self.map_revision,
            "pinvi_revision": self.pinvi_revision,
            "pinset_sha256": self.pinset_sha256,
            "images": dict(sorted(self.images.items())),
            "schema_heads": dict(sorted(self.schema_heads.items())),
            "databases": (
                None
                if self.databases is None
                else {role: self.databases[role].to_payload() for role in _DATABASE_ROLES}
            ),
            "restart": None if self.restart is None else self.restart.to_payload(),
            "adopted": None if self.adopted is None else self.adopted.to_payload(),
            "carried_over_from": self.carried_over_from,
        }


#: ``--restart``의 리셋이 실제로 끝났다는 표시(``in_progress``의 ``step``). 이어서 끝내는 일반
#: 실행이 리셋 기록을 가져갈지는 이것 하나로 정한다.
RESET_DONE_STEP: Final = "reset"


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def begin_deploy(
    previous: DeployStatus | None,
    *,
    run_id: str,
    started_at: str,
    manager_revision: str,
    map_revision: str,
    pinvi_revision: str,
    pinset_sha256: str,
    restart: DeployRestart | None = None,
    adopted: DeployRestart | None = None,
    adopted_databases: Mapping[DatabaseRole, DeployedDatabase] | None = None,
) -> DeployStatus:
    """첫 변경 직전에 쓸 ``in_progress``. identity 기준선은 직전 기록에서 물려받는다.

    ``--restart``도 기준선을 **물려받는다** — 실제로 지운 **뒤에** 호출자가 비운다. 리셋 전에
    죽으면 DB는 그대로이므로 다음 일반 실행이 여전히 그 기준으로 확인해야 한다.
    ``--adopt-live-databases``는 지금 떠 있는 DB(``adopted_databases``)를 새 기준으로 삼는다.
    직전 실행이 ``in_progress``로 죽었어도 기준선을 그대로 물려받는다.

    명시적 결정(``--restart``·``--adopt-live-databases``)으로 시작한 배포가 끝나지 못하면,
    그것을 이어서 끝내는 일반 실행이 그 기록을 가져간다. 리셋 기록은 리셋이 실제로 일어난
    뒤(``step == RESET_DONE_STEP``)만 — 그 전에 죽었으면 리셋은 없었다. 기준선이 비었는지로
    가르면 기준선 없이 시작한 ``--restart``(새 호스트)가 리셋 전에 죽어도 리셋으로 남는다.
    """

    step: str | None = None
    if adopted is not None:
        databases = adopted_databases
    else:
        databases = None if previous is None else previous.databases
    if (
        restart is None
        and adopted is None
        and previous is not None
        and previous.state == "in_progress"
    ):
        adopted = previous.adopted
        if previous.step == RESET_DONE_STEP:
            restart = previous.restart
            step = RESET_DONE_STEP
    return DeployStatus(
        state="in_progress",
        run_id=run_id,
        started_at=started_at,
        manager_revision=manager_revision,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        pinset_sha256=pinset_sha256,
        databases=databases,
        step=step,
        restart=restart,
        adopted=adopted,
    )


def commit_deploy(
    status: DeployStatus,
    *,
    committed_at: str,
    images: Mapping[str, str],
    schema_heads: Mapping[str, str],
    databases: Mapping[DatabaseRole, DeployedDatabase],
) -> DeployStatus:
    """관측한 이미지·head·DB identity로 ``committed``를 만든다(한 번의 쓰기로 남긴다)."""

    if status.state != "in_progress":
        raise DeploymentContractError("only an in-progress deploy can be committed")
    return DeployStatus(
        state="committed",
        run_id=status.run_id,
        started_at=status.started_at,
        committed_at=committed_at,
        manager_revision=status.manager_revision,
        map_revision=status.map_revision,
        pinvi_revision=status.pinvi_revision,
        pinset_sha256=status.pinset_sha256,
        images=images,
        schema_heads=schema_heads,
        databases=databases,
        restart=status.restart,
        adopted=status.adopted,
    )


def deploy_status_path(state_root: Path) -> Path:
    return state_root / DEPLOY_STATUS_FILENAME


def read_deploy_status(path: Path) -> DeployStatus | None:
    """없으면 ``None``. 있는데 읽을 수 없거나 모양이 틀리면 거부한다(추측하지 않는다)."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeploymentContractError("deploy status cannot be read") from exc
    if len(raw) > _MAX_BYTES:
        raise DeploymentContractError("deploy status is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("deploy status is invalid") from exc
    return _status_from_payload(payload)


def write_deploy_status(path: Path, status: DeployStatus) -> None:
    """같은 디렉터리의 임시 파일에 쓰고 ``os.replace``한다(0600)."""

    raw = (json.dumps(status.to_payload(), ensure_ascii=False, sort_keys=True) + "\n").encode()
    if len(raw) > _MAX_BYTES:
        # 읽기가 거부할 크기를 쓰면 다음 배포가 전부 막힌다.
        raise DeploymentContractError("deploy status is too large")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise DeploymentContractError("deploy status cannot be written") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    # rename의 durability. replace는 이미 끝났으므로 실패해도 쓰기 실패로 보고하지 않는다.
    try:
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _exact_mapping(value: object, keys: frozenset[str] | set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise DeploymentContractError("deploy status is invalid")
    return cast(Mapping[str, Any], value)


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise DeploymentContractError("deploy status is invalid")
    return {str(key): str(item) for key, item in value.items()}


def _status_from_payload(payload: object) -> DeployStatus:
    fields = _exact_mapping(payload, _FIELDS)
    version = fields["version"]
    if type(version) is not int or version != _VERSION:
        raise DeploymentContractError("deploy status version is unsupported")
    databases: dict[DatabaseRole, DeployedDatabase] | None = None
    if fields["databases"] is not None:
        entries = _exact_mapping(fields["databases"], set(_DATABASE_ROLES))
        databases = {}
        for role in _DATABASE_ROLES:
            entry = _exact_mapping(entries[role], {"name", "oid", "system_identifier"})
            databases[role] = DeployedDatabase(
                name=entry["name"],
                oid=entry["oid"],
                system_identifier=entry["system_identifier"],
            )
    records: dict[str, DeployRestart | None] = {}
    for name in ("restart", "adopted"):
        records[name] = None
        if fields[name] is not None:
            record = _exact_mapping(fields[name], {"reason", "at"})
            records[name] = DeployRestart(reason=record["reason"], at=record["at"])
    return DeployStatus(
        state=cast(DeployState, fields["state"]),
        run_id=fields["run_id"],
        started_at=fields["started_at"],
        committed_at=fields["committed_at"],
        step=fields["step"],
        manager_revision=fields["manager_revision"],
        map_revision=fields["map_revision"],
        pinvi_revision=fields["pinvi_revision"],
        pinset_sha256=fields["pinset_sha256"],
        images=_string_mapping(fields["images"]),
        schema_heads=_string_mapping(fields["schema_heads"]),
        databases=databases,
        restart=records["restart"],
        adopted=records["adopted"],
        carried_over_from=fields["carried_over_from"],
    )
