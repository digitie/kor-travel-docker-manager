"""배포 상태 파일 하나 — 마이그레이션 전진 재구축이 실행 사이에 남기는 유일한 상태(ADR-51).

pinset별 journal·phase·receipt 대신 전역 파일 하나에 세 상태만 둔다.

- 파일 없음: 이 Manager가 아직 한 번도 배포를 끝내지 않았다(또는 v6/v8에서 넘어오는 중).
- ``in_progress``: 배포가 시작돼 무언가를 바꿨다. 다음 실행은 처음부터 다시 돈다 — 모든
  단계가 멱등이라 재개가 필요 없다.
- ``committed``: 마지막 배포가 끝났다. 같은 pair를 다시 돌리면 빌드 없이 수렴만 한다.

``databases``는 직전 committed 배포가 관측한 DB identity다. 배포 시작 전에 라이브 DB가 이것과
다르면(누가 지우고 다시 만들었다) 아무것도 바꾸기 전에 거부한다. 명시적 ``--restart``만 이
기준을 비운다. 비밀은 담지 않는다.
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
from typing import Final, Literal, cast

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.database_runtime import DatabaseRole
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    RuntimeService,
    legacy_journal_file,
    legacy_manifest_file,
    read_manifest,
    read_rebuild_journal,
)

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
_MAX_REASON: Final = 200


@dataclass(frozen=True)
class DeployedDatabase:
    """배포가 관측한 DB 하나의 identity. 다시 만들면 oid가 바뀐다."""

    name: str
    oid: int
    system_identifier: str

    def __post_init__(self) -> None:
        if (
            _IDENTIFIER.fullmatch(self.name) is None
            or type(self.oid) is not int
            or self.oid <= 0
            or _SYSTEM_IDENTIFIER.fullmatch(self.system_identifier) is None
        ):
            raise DeploymentContractError("deploy status database identity is invalid")

    def to_payload(self) -> dict[str, object]:
        return {"name": self.name, "oid": self.oid, "system_identifier": self.system_identifier}


@dataclass(frozen=True)
class DeployRestart:
    """명시적 ``--restart``의 기록. 사유는 사람이 쓴 한 줄이다."""

    reason: str
    at: str

    def __post_init__(self) -> None:
        if (
            not self.reason.strip()
            or len(self.reason) > _MAX_REASON
            or "\n" in self.reason
            or "\r" in self.reason
            or not self.at
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
    carried_over_from: str | None = None

    def __post_init__(self) -> None:
        if self.state not in ("in_progress", "committed"):
            raise DeploymentContractError("deploy status state is invalid")
        try:
            uuid.UUID(self.run_id)
        except (TypeError, ValueError) as exc:
            raise DeploymentContractError("deploy status run id is invalid") from exc
        for revision in (self.manager_revision, self.map_revision, self.pinvi_revision):
            if _REVISION.fullmatch(revision) is None:
                raise DeploymentContractError("deploy status revision is invalid")
        if _SHA256.fullmatch(self.pinset_sha256) is None:
            raise DeploymentContractError("deploy status pinset digest is invalid")
        if self.databases is not None and set(self.databases) != set(_DATABASE_ROLES):
            raise DeploymentContractError("deploy status databases are invalid")
        for service, image_id in self.images.items():
            if _SERVICE.fullmatch(service) is None or _IMAGE_ID.fullmatch(image_id) is None:
                raise DeploymentContractError("deploy status image is invalid")
        if self.schema_heads and (
            set(self.schema_heads) != set(_SCHEMA_ROLES)
            or any(_SCHEMA_HEAD.fullmatch(head) is None for head in self.schema_heads.values())
        ):
            raise DeploymentContractError("deploy status schema heads are invalid")
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
            "carried_over_from": self.carried_over_from,
        }


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
) -> DeployStatus:
    """첫 변경 직전에 쓸 ``in_progress``. identity 기준선은 직전 기록에서 물려받는다.

    ``--restart``는 DB를 다시 만들 것이므로 기준선을 비운다. 직전 실행이 ``in_progress``로
    죽었어도 그 기준선을 그대로 물려받는다 — 그 실행은 DB를 지우지 않았다(``--restart``가
    아니었다면).
    """

    return DeployStatus(
        state="in_progress",
        run_id=run_id,
        started_at=started_at,
        manager_revision=manager_revision,
        map_revision=map_revision,
        pinvi_revision=pinvi_revision,
        pinset_sha256=pinset_sha256,
        databases=None if restart is not None or previous is None else previous.databases,
        restart=restart,
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


def carry_over_committed_generation(
    state_root: Path,
    *,
    companions: Mapping[str, RuntimeService],
    manager_revision: str,
) -> DeployStatus | None:
    """v6 manifest + 그 pinset의 committed v8 journal을 ``committed`` 상태로 옮긴다.

    ``deploy-status.json``이 아직 없는 첫 마이그레이션 전진 배포에서 한 번 쓰인다. 무엇이든
    맞지 않으면 ``None``이다 — 그 결과는 리셋이 아니라 전체 경로 한 번이다. journal은 원장이
    아니라 **manifest의 pinset**으로 찾는다(그 사이 회전이 있었어도 지금 떠 있는 세대를 본다).
    ``manifest_committing``도 받는다: manifest는 전체 readiness·이미지 검증 뒤에 쓰인다.
    """

    manifest_path = legacy_manifest_file(state_root)
    if not manifest_path.exists():
        return None
    try:
        generation = read_manifest(manifest_path).active_generation
        journal = read_rebuild_journal(
            legacy_journal_file(state_root, pinset_sha256=generation.pinset_sha256)
        )
    except DeploymentContractError:
        return None
    if journal.phase not in ("committed", "manifest_committing") or journal.candidate != generation:
        return None
    evidence = journal.map_application_300_execution_evidence
    application = evidence.application_database_identity
    dagster = evidence.dagster_metadata_database_identity
    pinvi = journal.pinvi_database_identity
    if application is None or dagster is None or pinvi is None:
        return None
    slot_images = generation.image_ids
    images = {str(service): image for service, image in slot_images.items()}
    images.update((name, slot_images[owner]) for name, owner in companions.items())
    return DeployStatus(
        state="committed",
        run_id=journal.transaction_id,
        started_at=journal.created_at,
        committed_at=generation.recorded_at,
        manager_revision=manager_revision,
        map_revision=generation.map_source_revision,
        pinvi_revision=generation.pinvi_source_revision,
        pinset_sha256=generation.pinset_sha256,
        images=images,
        schema_heads={str(role): head for role, head in generation.schema_heads.items()},
        databases={
            "map_application": DeployedDatabase(
                application.database_name,
                application.database_oid,
                application.postgres_system_identifier,
            ),
            "map_dagster": DeployedDatabase(dagster.name, dagster.oid, dagster.system_identifier),
            "pinvi": DeployedDatabase(pinvi.name, pinvi.oid, pinvi.system_identifier),
        },
        carried_over_from=f"v6+v8:{generation.pinset_sha256}",
    )


def _status_from_payload(payload: object) -> DeployStatus:
    expected = {
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
        "carried_over_from",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeploymentContractError("deploy status fields are invalid")
    if payload["version"] != _VERSION:
        raise DeploymentContractError("deploy status version is unsupported")
    try:
        databases_payload = payload["databases"]
        databases: dict[DatabaseRole, DeployedDatabase] | None = None
        if databases_payload is not None:
            databases = {
                role: DeployedDatabase(
                    name=databases_payload[role]["name"],
                    oid=databases_payload[role]["oid"],
                    system_identifier=databases_payload[role]["system_identifier"],
                )
                for role in _DATABASE_ROLES
            }
            if set(databases_payload) != set(_DATABASE_ROLES):
                raise DeploymentContractError("deploy status databases are invalid")
        restart_payload = payload["restart"]
        restart = (
            None
            if restart_payload is None
            else DeployRestart(reason=restart_payload["reason"], at=restart_payload["at"])
        )
        return DeployStatus(
            state=cast(DeployState, payload["state"]),
            run_id=payload["run_id"],
            started_at=payload["started_at"],
            committed_at=payload["committed_at"],
            step=payload["step"],
            manager_revision=payload["manager_revision"],
            map_revision=payload["map_revision"],
            pinvi_revision=payload["pinvi_revision"],
            pinset_sha256=payload["pinset_sha256"],
            images=dict(payload["images"]),
            schema_heads=dict(payload["schema_heads"]),
            databases=databases,
            restart=restart,
            carried_over_from=payload["carried_over_from"],
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise DeploymentContractError("deploy status is invalid") from exc
