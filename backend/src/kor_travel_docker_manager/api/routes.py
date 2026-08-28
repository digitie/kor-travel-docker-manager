from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from kor_travel_docker_manager.services.auth_service import (
    AdminSessionContext,
    record_login_audit_event,
    require_admin_session,
)
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
)
from kor_travel_docker_manager.services.compose_service import compose_service
from kor_travel_docker_manager.services.deployment_readiness import (
    read_deployment_readiness,
)
from kor_travel_docker_manager.services.disk_usage import read_disk_usage
from kor_travel_docker_manager.services.docker_service import (
    ContainerConfigValidationError,
    docker_service,
)
from kor_travel_docker_manager.services.metrics_service import metrics_service
from kor_travel_docker_manager.services.registry import list_targets
from kor_travel_docker_manager.services.runtime_pin_registry import (
    read_published_runtime_pins,
)
from kor_travel_docker_manager.services.runtime_pin_request import (
    RuntimePinRequest,
    RuntimePinRequestError,
    clear_runtime_pin_request,
    prospective_pinset_sha256,
    read_runtime_pin_request,
    runtime_pin_request_path,
    utc_timestamp,
    write_runtime_pin_request,
)
from kor_travel_docker_manager.services.source_status import collect_source_status
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    StandaloneBackupError,
    list_standalone_backups,
)

router = APIRouter(dependencies=[Depends(require_admin_session)])


class ActionRequest(BaseModel):
    action: str = Field(..., description="Action to perform: 'start', 'stop', or 'restart'")


class EnsureTargetRequest(BaseModel):
    build: bool = Field(False, description="Run docker compose up with --build")
    recreate: bool = Field(False, description="Run docker compose up with --force-recreate")


class ContainerConfigUpdate(BaseModel):
    ports: list[Any] = Field(..., description="Compose ports list, e.g. ['5432:5432']")
    env: dict[str, Any] = Field(..., description="Compose environment variables dict")
    volumes: list[Any] = Field(
        ...,
        description=(
            "Immutable Compose volumes list; callers must echo the current exact value"
        ),
    )
    networks: list[str] = Field(..., description="Compose networks list, e.g. ['default']")


class RuntimePinRotationRequestBody(BaseModel):
    """UI가 남기는 회전 **요청** 본문. 이 값이 pin이 되지는 않는다."""

    role: Literal["map", "pinvi"]
    revision: str = Field(..., min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _single_line(cls, value: str) -> str:
        # runtime_pin_request._require_text와 같은 규칙이어야 한다 — 여기서 통과시킨
        # 값을 CLI가 읽지 못하면 요청은 영원히 적용되지 않는다.
        text = value.strip()
        if not text:
            raise ValueError("reason must not be empty")
        if any(character in text for character in ("\n", "\r", "\x00")):
            raise ValueError("reason must be a single line")
        return text


def _config_failure_detail(result: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "message": result.get("error"),
        "restoration": result.get("restoration"),
    }
    for field in ("command", "returncode", "stdout", "stderr"):
        if field in result:
            detail[field] = result.get(field)
    return detail


def _candidate_contract_detail(
    error: ComposeCandidateContractError,
) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": str(error),
        "stage": "candidate_validation",
        "mutation_applied": False,
    }


def _post_mutation_contract_detail(
    error: ComposePostMutationContractError,
) -> dict[str, Any]:
    original_code = getattr(error.original_error, "code", None)
    return {
        "code": error.code,
        "message": str(error),
        "stage": "post_mutation_recovery",
        "mutation_applied": True,
        "original_error": {
            "code": original_code,
            "message": str(error.original_error),
        },
        "recovery_attempted": error.recovery_attempted,
        "recovery_succeeded": error.recovery_succeeded,
        "recovery_error": error.recovery_error,
        "restoration": error.restoration,
    }


@router.get("/targets")
def get_targets():
    """Retrieve application-oriented infrastructure targets for UI and CLI parity."""
    return list_targets()


@router.get("/backups")
def get_backups(role: str | None = Query(default=None)):
    """Read-only standalone DB backup listing (issue #177). `create`/`gc` stay
    CLI-only (`ktdctl db-backup ...`) and are not exposed here. Restore isn't
    implemented anywhere yet (CLI or API) — this route only lists what exists."""
    roles = BACKUP_ROLES if role is None else (role,)
    if role is not None and role not in BACKUP_ROLES:
        raise HTTPException(status_code=400, detail=f"unknown backup role: {role}")
    backups: list[dict[str, Any]] = []
    for backup_role in roles:
        try:
            backups.extend(
                manifest.to_json() for manifest in list_standalone_backups(backup_role)
            )
        except StandaloneBackupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    backups.sort(key=lambda item: item["created_at_unix"])
    return {"backups": backups}


@router.get("/runtime-pins")
def get_runtime_pins():
    """Read-only view of the Map/PinVi runtime pin registry.

    Rotation stays root-only via `ktdctl pin rotate` — the API process cannot write
    the registry, and that boundary is the cheapest safety we have. When the
    published copy is missing or malformed the payload says `unknown` instead of
    guessing a value."""
    payload = read_published_runtime_pins()
    # degraded/stale은 값이 있으므로 그대로 보여주되 상태를 그대로 전달한다.
    # unknown만 값 자체가 없다.
    if payload.get("status") == "unknown":
        return {
            "status": payload.get("status", "unknown"),
            "source": payload.get("source"),
            "detail": payload.get("detail"),
            "pins": None,
            # 요청 파일의 가독성은 registry의 가독성과 무관하다. 여기서 None으로
            # 잘라 버리면 대기 중인 요청이 화면에서 사라지고, id를 볼 수 없으니
            # 취소도 못 하게 된다 — 정작 그 상태에서 가장 필요한 정보다.
            "pending_request": _pending_request_payload(current_pinset=None),
        }
    blocked = payload.get("blocked_pinsets", [])
    pinset_sha256 = payload.get("pinset_sha256")
    # The rebuild start gate only honours entries without a phase; phase-scoped entries
    # block one journal state, not the pinset. Collapsing the two here would tell the
    # operator to rotate when a rebuild would in fact be allowed.
    current_is_blocked = any(
        entry.get("pinset_sha256") == pinset_sha256 and entry.get("phase") is None
        for entry in blocked
    )
    current_has_phase_scoped_block = any(
        entry.get("pinset_sha256") == pinset_sha256 and entry.get("phase") is not None
        for entry in blocked
    )
    return {
        "status": payload.get("status", "ok"),
        "source": payload.get("source"),
        "published_at": payload.get("published_at"),
        "pins": {
            "release_version": payload.get("release_version"),
            "pinset_sha256": pinset_sha256,
            "sources": payload.get("sources", []),
            "rotated_at": payload.get("rotated_at"),
            "rotated_by": payload.get("rotated_by"),
            "reason": payload.get("reason"),
        },
        "lifecycle": {
            "current_pinset_is_blocked": current_is_blocked,
            "current_pinset_has_phase_scoped_block": current_has_phase_scoped_block,
            "blocked_pinsets": blocked,
            "history": payload.get("history", []),
        },
        "pending_request": _pending_request_payload(current_pinset=pinset_sha256),
        "summary": _runtime_pin_summary(
            status=payload.get("status", "ok"),
            current_is_blocked=current_is_blocked,
            rotated_at=payload.get("rotated_at"),
        ),
    }


def _pending_request_payload(*, current_pinset: str | None) -> dict[str, Any] | None:
    """UI가 남긴 대기 요청. 읽지 못하면 값을 지어내지 않고 그 사실을 말한다."""

    try:
        request = read_runtime_pin_request()
    except RuntimePinRequestError as exc:
        return {"status": "unreadable", "detail": str(exc)}
    if request is None:
        return None
    # base가 어긋난 요청을 'pending'이라 부르지 않는다 — apply-pending이 반드시 거부할
    # 요청을 UI가 적용 가능한 것처럼 보여주면 안 된다.
    stale = current_pinset is not None and request.base_pinset_sha256 != current_pinset
    return {
        "status": "stale" if stale else "pending",
        "request_id": request.request_id,
        "role": request.role,
        "revision": request.revision,
        "reason": request.reason,
        "requested_by": request.requested_by,
        "requested_at": request.requested_at,
        "base_pinset_sha256": request.base_pinset_sha256,
        "prospective_pinset_sha256": request.prospective_pinset_sha256,
    }


def _runtime_pin_summary(
    *,
    status: str,
    current_is_blocked: bool,
    rotated_at: str | None,
) -> dict[str, str]:
    """Plain-language status for operators who do not read digests."""
    if status != "ok":
        return {
            "state": "unverified",
            "text": (
                "표시된 값이 이 호스트의 최신 고정 값이 아닐 수 있습니다. "
                "SSH에서 확인이 필요합니다."
            ),
            "next_action": "sudo -n backend/.venv/bin/ktdctl pin verify",
        }
    if current_is_blocked:
        return {
            "state": "action_required",
            "text": (
                "현재 고정된 pinset은 재시도가 금지된 candidate입니다. "
                "새 revision으로 회전해야 재구축할 수 있습니다."
            ),
            "next_action": "ktdctl pin rotate --role <map|pinvi> --revision <40-hex> --confirm",
        }
    return {
        "state": "ok",
        "text": f"고정된 pinset이 정상 등록돼 있습니다. 마지막 회전: {rotated_at or '알 수 없음'}",
        "next_action": "",
    }


@router.get("/source-status")
def get_source_status(refresh: bool = Query(default=False)):
    """Read-only deployment provenance card (design P3/P4).

    Observation only — git and docker are invoked read-only and nothing is written.
    A collection failure degrades that one row to `확인할 수 없습니다`, so this
    handler has no try/except: the service is total by contract.

    `refresh=true` bypasses the TTL cache. That still spawns no mutation, so there
    is no audit row; the durable-audit pattern belongs to mutations."""
    return collect_source_status(force_refresh=refresh)


@router.get("/system/disk-usage")
def get_disk_usage(refresh: bool = Query(default=False)):
    """Read-only Docker disk usage (design P8b).

    "디스크 참"이 비전문 관리자가 이 시스템을 죽이는 가장 그럴듯한 경로인데, 지금까지
    어느 화면도 그것을 보여 주지 않았다. 관측만 한다 — `prune`은 파괴적이라 CLI 전용이고
    이 카드는 실행할 명령만 알려 준다."""
    return read_disk_usage(force_refresh=refresh)


@router.get("/deployment-readiness")
def get_deployment_readiness(refresh: bool = Query(default=False)):
    """Read-only preflight readiness rows (KUM-M7 / design P10-4).

    Answers "would a rebuild fail right now?" without touching anything. There is no
    mutation, so there is no audit row. The service never raises — an unreadable host
    degrades to `unknown` rows rather than a 500 that hides the whole panel.

    The payload carries absolute host paths and sibling revisions, so it must stay on
    this router, whose `require_admin_session` dependency gates every route."""
    # `refresh=true`는 30초 TTL을 건너뛴다. 관측만 하므로 mutation도 감사 행도 없다.
    return read_deployment_readiness(force_refresh=refresh)


APPLY_PENDING_COMMAND = "sudo -n backend/.venv/bin/ktdctl pin apply-pending --confirm"


def _reject_runtime_pin_request(
    request: Request,
    session: AdminSessionContext,
    *,
    code: str,
    message: str,
    extra: dict[str, Any] | None = None,
    status_code: int = 409,
) -> HTTPException:
    """거부도 감사에 남긴다 — 남지 않은 거부는 조사할 수 없다."""

    record_login_audit_event(
        request,
        event_type="runtime_pin",
        outcome="rejected",
        attempted_username=session.username,
        reason="runtime_pin_rotation_request_rejected",
        session_id_hash=session.session_id_hash,
        detail={"code": code, **(extra or {})},
    )
    detail: dict[str, Any] = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)


@router.post("/runtime-pins/requests", status_code=201)
def post_runtime_pin_request(
    payload: RuntimePinRotationRequestBody,
    request: Request,
    session: Annotated[AdminSessionContext, Depends(require_admin_session)],
) -> dict[str, Any]:
    """Record a pin rotation **request**; applying it stays root-only.

    The registry is root-owned `0600`, so this process cannot write it even if this
    handler were wrong. What lands here is a proposal: `ktdctl pin apply-pending
    --confirm` re-derives the canonical URLs, recomputes the digest and re-checks the
    block list from the root registry before anything changes."""
    published = read_published_runtime_pins()
    if published.get("status") != "ok":
        # stale/degraded 값을 base로 삼으면 apply-pending이 반드시 거부한다.
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PINS_UNVERIFIED",
            message=(
                "현재 고정 값을 확인할 수 없어 회전 요청을 받을 수 없습니다. "
                "SSH에서 'ktdctl pin verify'로 공개 사본을 갱신하세요."
            ),
            extra={"published_status": published.get("status")},
        )

    sources = published.get("sources", [])
    revisions = {
        entry.get("role"): entry.get("revision")
        for entry in sources
        if isinstance(entry, dict)
    }
    if set(revisions) != {"map", "pinvi"} or not all(
        isinstance(value, str) and len(value) == 40 for value in revisions.values()
    ):
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PINS_MALFORMED",
            message="공개된 고정 값의 형식이 올바르지 않습니다. SSH에서 확인이 필요합니다.",
        )

    next_map = payload.revision if payload.role == "map" else revisions["map"]
    next_pinvi = payload.revision if payload.role == "pinvi" else revisions["pinvi"]
    try:
        prospective = prospective_pinset_sha256(
            release_version=published["release_version"],
            map_revision=next_map,
            pinvi_revision=next_pinvi,
        )
    except DeploymentContractError as exc:
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PINS_MALFORMED",
            message=str(exc),
        ) from exc

    if payload.revision == revisions[payload.role] or prospective == published.get(
        "pinset_sha256"
    ):
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_UNCHANGED",
            message="이미 그 revision이 고정돼 있습니다 — 바뀌는 것이 없습니다.",
        )

    # phase 유무와 무관하게 차단한다: 그 pinset을 다시 고정하는 것 자체가 금지다.
    if any(
        entry.get("pinset_sha256") == prospective
        for entry in published.get("blocked_pinsets", [])
        if isinstance(entry, dict)
    ):
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_BLOCKED_TARGET",
            message=(
                "이 조합은 재시도가 영구 금지된 세트입니다. 다른 revision을 지정하세요."
            ),
            extra={"prospective_pinset_sha256": prospective},
        )

    try:
        existing = read_runtime_pin_request()
    except RuntimePinRequestError as exc:
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_REQUEST_UNREADABLE",
            message=str(exc),
        ) from exc
    if existing is not None:
        # 조용히 덮어쓰지 않는다 — 대기 중인 요청을 없앨지는 사람이 정한다.
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_REQUEST_EXISTS",
            message="이미 대기 중인 회전 요청이 있습니다. 먼저 적용하거나 취소하세요.",
            extra={
                "request_id": existing.request_id,
                "role": existing.role,
                "revision": existing.revision,
            },
        )

    pending = RuntimePinRequest(
        request_id=str(uuid4()),
        role=payload.role,
        revision=payload.revision,
        reason=payload.reason,
        requested_by=session.username,
        requested_at=utc_timestamp(),
        base_pinset_sha256=published["pinset_sha256"],
        prospective_pinset_sha256=prospective,
    )
    try:
        written = write_runtime_pin_request(pending)
    except (RuntimePinRequestError, OSError) as exc:
        # 경로만 말한다. 파일 내용이나 uid는 오류 메시지에 넣지 않는다.
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_REQUEST_NOT_WRITABLE",
            message=(
                "요청을 저장할 수 없습니다. 요청 디렉터리를 백엔드 사용자 소유로 한 번 "
                "만들어 주세요: sudo install -d -o <backend-user> -g <backend-user> "
                f"-m 0700 {runtime_pin_request_path().parent}"
            ),
            extra={"directory": str(runtime_pin_request_path().parent)},
            status_code=503,
        ) from exc

    # 감사 기록은 파일 쓰기 뒤에 남긴다 — 존재하지 않는 요청을 기록하면 안 된다.
    record_login_audit_event(
        request,
        event_type="runtime_pin",
        outcome="succeeded",
        attempted_username=session.username,
        reason="runtime_pin_rotation_requested",
        session_id_hash=session.session_id_hash,
        detail={
            "request_id": pending.request_id,
            "role": pending.role,
            "revision": pending.revision,
            "base_pinset_sha256": pending.base_pinset_sha256,
            "prospective_pinset_sha256": pending.prospective_pinset_sha256,
            "operator_reason": pending.reason,
            "stored_in": written.parent.name,
        },
    )
    return {
        "status": "pending",
        "request": pending.to_payload(),
        "next_action": APPLY_PENDING_COMMAND,
    }


@router.delete("/runtime-pins/requests/{request_id}")
def delete_runtime_pin_request(
    request_id: str,
    request: Request,
    session: Annotated[AdminSessionContext, Depends(require_admin_session)],
) -> dict[str, Any]:
    """Discard the pending rotation request identified by `request_id`.

    The id must match what is on disk, so a stale browser tab cannot delete a newer
    request someone else just filed."""
    # 거부도 남긴다. 남지 않은 거부는 조사할 수 없고, id를 바꿔 가며 두드리는 시도가
    # 흔적 없이 지나가면 안 된다.
    try:
        cleared = clear_runtime_pin_request(expect_request_id=request_id)
    except RuntimePinRequestError as exc:
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_REQUEST_UNREADABLE",
            message=str(exc),
        ) from exc
    if not cleared:
        raise _reject_runtime_pin_request(
            request,
            session,
            code="RUNTIME_PIN_REQUEST_NOT_FOUND",
            message="그 id의 대기 중인 요청이 없습니다. 화면을 새로 고치세요.",
            status_code=404,
        )
    record_login_audit_event(
        request,
        event_type="runtime_pin",
        outcome="succeeded",
        attempted_username=session.username,
        reason="runtime_pin_rotation_request_cancelled",
        session_id_hash=session.session_id_hash,
        detail={"request_id": request_id},
    )
    return {"status": "cancelled", "request_id": request_id}


@router.post("/targets/{target}/ensure")
def ensure_target(target: str, payload: EnsureTargetRequest):
    """Ensure a dependency target is running through docker compose."""
    try:
        result = compose_service.ensure_target(
            target,
            build=payload.build,
            recreate=payload.recreate,
        )
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail={
                "message": "docker compose ensure failed",
                "stderr": result.get("stderr"),
                "command": result.get("command"),
            },
        )

    return result


@router.get("/containers")
def list_containers():
    """Retrieve state and ports for managed PostgreSQL and RustFS services."""
    try:
        return docker_service.get_containers_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/containers/{container_id}/action")
def control_container(container_id: str, payload: ActionRequest):
    """Start, stop, or restart a specific infrastructure service."""
    action = payload.action.lower()
    if action not in ["start", "stop", "restart"]:
        raise HTTPException(status_code=400, detail="Action must be start, stop, or restart")

    try:
        result = docker_service.control_container(container_id, action)
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("success"):
        detail: Any = result.get("error")
        if "restoration" in result:
            detail = _config_failure_detail(result)
        raise HTTPException(status_code=500, detail=detail)

    return {"status": "success", "message": result.get("message")}


@router.get("/containers/{container_id}/logs")
def get_container_logs(container_id: str, tail: int = 100):
    """Retrieve recent console output logs from a container."""
    result = docker_service.get_container_logs(container_id, tail=tail)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return {"logs": result.get("logs")}


@router.get("/containers/{container_id}/inspect")
def inspect_container(container_id: str):
    """Retrieve a sanitized Docker inspect summary for a managed container."""
    result = docker_service.inspect_container(container_id)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result.get("container")


@router.post("/containers/{container_id}/config")
def update_container_config(container_id: str, payload: ContainerConfigUpdate):
    """Update container configurations (docker-compose) and recreate the container using Docker SDK."""
    try:
        result = docker_service.update_container_config(
            container_id, payload.ports, payload.env, payload.volumes, payload.networks
        )
    except ContainerConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=_config_failure_detail(result))

    return {"status": "success", "message": result.get("message")}


@router.post("/containers/{container_id}/reset")
def reset_container_config(container_id: str):
    """Reset container configurations to default and recreate the container using Docker SDK."""
    try:
        result = docker_service.reset_container_config(container_id)
    except ComposePostMutationContractError as exc:
        raise HTTPException(
            status_code=500, detail=_post_mutation_contract_detail(exc)
        ) from exc
    except ComposeCandidateContractError as exc:
        raise HTTPException(
            status_code=409, detail=_candidate_contract_detail(exc)
        ) from exc
    except DeploymentContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=_config_failure_detail(result))

    return {"status": "success", "message": result.get("message")}


@router.get("/containers/{container_id}/metrics")
def get_container_metrics_history(container_id: str, hours: int = 1):
    """Retrieve historical metrics (CPU, Memory, IO) for a container over the last N hours."""
    try:
        return metrics_service.get_recent_metrics(container_id, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
