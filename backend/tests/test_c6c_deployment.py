from __future__ import annotations

import io
import json
import os
import stat
import urllib.error
from contextlib import nullcontext
from contextvars import Context
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.c6c_deployment import (
    C6cDeploymentConfig,
    C6cSmokeConfig,
    CompatibleImagePair,
    CompatiblePairManifest,
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
    HttpProbeResponse,
    PinviCancelProbeState,
    assert_c6c_mutation_allowed,
    c6c_deployment_lock,
    c6c_state_paths,
    initial_pair_manifest,
    load_c6c_deployment_config,
    load_c6c_deployment_config_from_environment,
    load_pair_manifest,
    new_image_pair,
    run_map_ui_auth_preflight,
    run_map_ops_smoke,
    run_pinvi_canonical_smoke,
    run_ui_auth_smoke,
    validate_compose_candidate_protected_values,
    validate_compose_env_file_isolation,
    validate_current_map_ui_auth_runtime,
    validate_resolved_compose_candidate_protected_values,
    validate_resolved_compose_image_pair,
    validate_resolved_compose_secret_isolation,
    validate_runtime_secret_isolation,
    write_pair_manifest,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeEnvFileIdentity,
    ComposeEnvironmentSnapshot,
    ComposeExternalFileSnapshot,
    ComposeExternalInputSnapshot,
    ComposeExternalReference,
    ComposeService,
    ComposeTransactionSnapshot,
    ValidatedComposeCandidate,
    _capture_compose_environment_snapshot,
    _capture_compose_external_input_snapshot,
    _resolved_compose_document_hash,
)
from kor_travel_docker_manager.services.docker_service import DockerService

_READ_TOKEN = "r" * 32
_CANCEL_TOKEN = "c" * 32
_MAP_IMAGE_ID = f"sha256:{'a' * 64}"
_PINVI_IMAGE_ID = f"sha256:{'b' * 64}"
_ACTIVE_MAP_IMAGE_ID = f"sha256:{'c' * 64}"
_ACTIVE_PINVI_IMAGE_ID = f"sha256:{'d' * 64}"
_CONTRACT_GENERATION = "c6c-ops-v1"
_MAP_UI_USERNAME = "map-ui-admin-placeholder"
_MAP_UI_PASSWORD_HASH = "pbkdf2_sha256$100000$test-salt$test-digest"
_MAP_UI_SESSION_SECRET = "map-ui-session-secret-placeholder-value"
_DOLLAR_MAP_UI_USERNAME = "map$ui$admin"
_DOLLAR_MAP_UI_SESSION_SECRET = "map$ui$session$secret$placeholder$value"
_MAP_UI_PASSWORD = "map-ui-password-strong"
_UNICODE_WHITESPACE = tuple(
    chr(codepoint)
    for codepoint in (
        *range(0x0009, 0x000E),
        *range(0x001C, 0x0021),
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)
_PINVI_ADMIN_PASSWORD = "pinvi-admin-password-strong"
_CANCEL_PROBE_JOB_ID = "77777777-7777-4777-8777-777777777777"
_OPERATION_MEMBER_ID = "88888888-8888-4888-8888-888888888888"
_C6C_ENV_NAMES = (
    "KTDM_DEPLOYMENT_ENVIRONMENT",
    "PINVI_ENVIRONMENT",
    "KTDM_DOCKER_NETWORK_MODE",
    "KOR_TRAVEL_MAP_API_CONTAINER_PORT",
    "KOR_TRAVEL_MAP_UI_PORT",
    "PINVI_API_PORT",
    "PINVI_WEB_PORT",
    "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL",
    "KOR_TRAVEL_MAP_API_CONTAINER",
    "KOR_TRAVEL_MAP_UI_CONTAINER",
    "PINVI_API_CONTAINER",
    "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN",
    "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN",
    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED",
    "KTDM_C6C_CONTRACT_GENERATION",
    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
    "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
    "KTDM_C6C_MAP_UI_ADMIN_PASSWORD",
    "KTDM_C6C_PINVI_ADMIN_EMAIL",
    "KTDM_C6C_PINVI_ADMIN_PASSWORD",
    "KTDM_C6C_CANCEL_PROBE_JOB_ID",
    "COMPOSE_PROJECT_NAME",
    "KTDM_C6C_STATE_ROOT",
    "KTDM_C6C_COMPATIBLE_PAIR_MANIFEST",
    "KTDM_C6C_DEPLOYMENT_LOCK",
)


def _production_config() -> C6cDeploymentConfig:
    return C6cDeploymentConfig(
        deployment_environment="production",
        pinvi_environment="production",
        base_url="http://127.0.0.1:12701",
        map_container_port=12701,
        read_token=_READ_TOKEN,
        cancel_token=_CANCEL_TOKEN,
        map_container="kor-travel-map-api-latest",
        map_ui_container="kor-travel-map-ui-latest",
        map_ui_password_hash=_MAP_UI_PASSWORD_HASH,
        map_ui_session_secret=_MAP_UI_SESSION_SECRET,
        pinvi_container="pinvi-api-latest",
        contract_generation=_CONTRACT_GENERATION,
        smoke=C6cSmokeConfig(
            pinvi_api_base_url="http://127.0.0.1:12801",
            map_ui_base_url="http://127.0.0.1:12705",
            pinvi_web_base_url="http://127.0.0.1:12805",
            map_ui_username=_MAP_UI_USERNAME,
            map_ui_password=_MAP_UI_PASSWORD,
            pinvi_admin_email="admin@example.test",
            pinvi_admin_password=_PINVI_ADMIN_PASSWORD,
            cancel_probe_job_id=_CANCEL_PROBE_JOB_ID,
        ),
    )


def _manifest() -> CompatiblePairManifest:
    return initial_pair_manifest(
        new_image_pair(_MAP_IMAGE_ID, _PINVI_IMAGE_ID, _CONTRACT_GENERATION)
    )


def _production_environment() -> dict[str, str | None]:
    return {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "PINVI_ENVIRONMENT": "production",
        "COMPOSE_PROJECT_NAME": "kor-travel-test",
        "KTDM_DOCKER_NETWORK_MODE": "host",
        "KOR_TRAVEL_MAP_API_CONTAINER_PORT": "12701",
        "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL": "http://127.0.0.1:12701",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _READ_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
        "KTDM_C6C_CONTRACT_GENERATION": _CONTRACT_GENERATION,
        "KTDM_C6C_MAP_UI_ADMIN_PASSWORD": _MAP_UI_PASSWORD,
        "KTDM_C6C_PINVI_ADMIN_EMAIL": "admin@example.test",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD": _PINVI_ADMIN_PASSWORD,
        "KTDM_C6C_CANCEL_PROBE_JOB_ID": _CANCEL_PROBE_JOB_ID,
    }


def _write_env(path: Path, **overrides: str | None) -> None:
    values = _production_environment()
    values.update(overrides)
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items() if value is not None),
        encoding="utf-8",
    )


def _clear_c6c_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _C6C_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _set_production_guard_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "production")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "true")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_READ_TOKEN", _READ_TOKEN)
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN", _CANCEL_TOKEN)


def _allow_manager_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_manager_mutation_allowed",
        lambda **_kwargs: "production",
    )


def _frozen_external_transaction(tmp_path: Path) -> ComposeTransactionSnapshot:
    compose_path = tmp_path / "docker-compose.yml"
    env_path = tmp_path / ".env"
    external_path = tmp_path / "worker.env"
    source = {
        "services": {
            "worker": {
                "env_file": [
                    {"path": "worker.env", "required": True, "format": "raw"}
                ]
            }
        }
    }
    source_bytes = yaml.safe_dump(
        source,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    resolved = {"services": {"worker": {"environment": {"SAFE": "1"}}}}
    compose_path.write_bytes(source_bytes)
    env_path.write_text("SAFE=initial\n", encoding="utf-8")
    external_path.write_text("WORKER_SAFE=initial\n", encoding="utf-8")
    return ComposeTransactionSnapshot(
        environment=ComposeEnvironmentSnapshot(
            effective={
                "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
                "PINVI_ENVIRONMENT": "development",
                "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false",
            },
            env_path=str(env_path),
            compose_path=str(compose_path),
            override_path=str(tmp_path / "missing.override.yml"),
            env_file_identity=ComposeEnvFileIdentity(exists=True),
            env_file_bytes=b"SAFE=initial\n",
        ),
        external_inputs=ComposeExternalInputSnapshot(
            references=(
                ComposeExternalReference(
                    service="worker",
                    index=0,
                    raw_path="worker.env",
                    resolved_path=str(external_path),
                    required=True,
                    format="raw",
                ),
            ),
            files=(
                ComposeExternalFileSnapshot(
                    path=str(external_path),
                    identity=ComposeEnvFileIdentity(exists=True),
                    contents=b"WORKER_SAFE=initial\n",
                ),
            ),
        ),
        compose_source_bytes=source_bytes,
        compose_source_mode=0o640,
        system_bind_snapshots=(),
        raw_volume_graph_hash=c6c_deployment.compose_volume_graph_hash(source),
        resolved_volume_graph_hash=(
            c6c_deployment.compose_volume_graph_hash(resolved)
        ),
        resolved=resolved,
        resolved_document_hash=_resolved_compose_document_hash(resolved),
    )


def _production_guard_transaction(tmp_path: Path) -> ComposeTransactionSnapshot:
    transaction = _frozen_external_transaction(tmp_path)
    environment = replace(
        transaction.environment,
        effective={
            "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _READ_TOKEN,
            "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
        },
    )
    return replace(transaction, environment=environment)


def _cancel_error_details(
    *,
    status: str,
    retryable: bool,
    error_code: str = "DAGSTER_TERMINATE_FAILED",
) -> dict[str, object]:
    structured_error = {
        "code": error_code,
        "message": "typed failure",
        "details": {"retryable": True},
    }
    failed = status in {"retryable", "failed"}
    dagster_run_id = "owned-dagster-run" if failed else None
    member = {
        "job_id": _CANCEL_PROBE_JOB_ID,
        "dagster_run_id": dagster_run_id,
        "operation_kind": "provider_feature_load_run",
        "requires_run_termination": failed,
        "initial_status": "running",
        "result": "cancel_failed" if failed else "pending",
        "terminal_status": None,
        "error": structured_error if failed else None,
        "updated_at": "2026-07-18T00:00:03Z",
    }
    dagster_runs: list[dict[str, object]] = []
    if failed:
        dagster_runs.append(
            {
                "dagster_run_id": dagster_run_id,
                "initial_status": "STARTED",
                "termination_reserved_at": "2026-07-18T00:00:01Z",
                "result": "cancel_failed",
                "terminal_status": None,
                "error": structured_error,
                "engine_started_at": None,
                "engine_finished_at": None,
                "updated_at": "2026-07-18T00:00:03Z",
            }
        )
    return {
        "cancellation_id": "22222222-2222-4222-8222-222222222222",
        "previous_cancellation_id": None,
        "root": {"kind": "import_job", "id": _CANCEL_PROBE_JOB_ID},
        "status": status,
        "requested_at": "2026-07-18T00:00:00Z",
        "requested_by": "service:pinvi",
        "reason": "c6c owned typed-failure fixture",
        "error": structured_error if failed else None,
        "updated_at": "2026-07-18T00:00:03Z",
        "finished_at": "2026-07-18T00:00:03Z" if failed else None,
        "retryable": retryable,
        "unresolved_member_count": 1,
        "members": [member],
        "dagster_runs": dagster_runs,
        "committed_data_rolled_back": False,
        "warnings": ["committed data is retained"],
    }


def _cancel_root_without_attempt() -> dict[str, object]:
    return {
        "root": {"kind": "import_job", "id": _CANCEL_PROBE_JOB_ID},
        "cancellation": None,
    }


def _map_problem(status: int, code: str) -> dict[str, object]:
    return {
        "type": f"https://kor-travel-map/errors/{code.lower().replace('_', '-')}",
        "title": "typed problem",
        "status": status,
        "detail": "typed problem",
        "code": code,
        "request_id": "c6c-request",
        "errors": [],
    }


def _pinvi_etl_envelope() -> dict[str, object]:
    return {
        "data": {
            "generated_at": "2026-07-18T00:00:00Z",
            "pinvi": {
                "status": "ok",
                "repository_count": 0,
                "job_count": 0,
                "asset_count": 0,
                "schedule_count": 0,
                "sensor_count": 0,
                "repositories": [],
                "recent_runs": [],
                "assets": [],
                "jobs": [],
                "schedules": [],
                "sensors": [],
            },
            "kor_travel_map": {
                "status": "ok",
                "dagster_status": "ok",
                "run_counts": {},
                "operations_by_status": {},
                "dagster_errors": [],
                "recent_runs": [],
                "recent_import_jobs": [],
                "errors": [],
            },
        }
    }


def _pinvi_provider_envelope() -> dict[str, object]:
    return {
        "data": {
            "items": [
                {
                    "provider": "kma",
                    "dataset_key": "weather",
                    "sync_scope": "dataset_wide",
                    "status": "active",
                    "consecutive_failures": 0,
                    "last_success_at": None,
                    "last_failure_at": None,
                    "eligible_after": None,
                    "schedule_next_scheduled_at": None,
                    "links": [],
                    "refresh_policy": None,
                }
            ],
            "total": 1,
            "schedule_source_status": "ok",
            "schedule_source_errors": [],
        }
    }


def _pinvi_import_job() -> dict[str, object]:
    return {
        "job_id": _CANCEL_PROBE_JOB_ID,
        "kind": "import_job",
        "status": "running",
        "progress": 50,
        "projected_job_id": _CANCEL_PROBE_JOB_ID,
        "projected_job_kind": "provider_feature_load_run",
        "projected_job_status": "running",
        "projected_job_progress": 50,
        "projected_job_load_batch_id": None,
        "projected_job_parent_job_id": None,
        "cancellation": None,
        "payload": {},
        "status_url": f"/v1/ops/pipeline/executions/import_job/{_CANCEL_PROBE_JOB_ID}",
        "current_stage": "loading",
        "error_message": None,
        "created_at": "2026-07-18T00:00:00Z",
        "started_at": "2026-07-18T00:00:01Z",
        "finished_at": None,
        "links": [],
    }


def _pinvi_repository() -> dict[str, object]:
    return {
        "name": "pinvi",
        "location_name": None,
        "jobs": [{"name": "poi_job", "is_job": True}],
        "schedules": [
            {
                "name": "poi_schedule",
                "job_name": "poi_job",
                "cron_schedule": "0 * * * *",
                "execution_timezone": "Asia/Seoul",
                "status": "RUNNING",
            }
        ],
        "sensors": [{"name": "poi_sensor", "status": None}],
        "asset_count": 1,
        "asset_groups": ["poi"],
    }


def _pinvi_etl_definition_items() -> dict[str, list[dict[str, object]]]:
    return {
        "assets": [{"key": "poi", "group_name": None, "description": None}],
        "schedules": [
            {
                "name": "poi_schedule",
                "job_name": "poi_job",
                "cron_schedule": "0 * * * *",
                "execution_timezone": None,
                "status": "configured",
            }
        ],
        "sensors": [
            {"name": "poi_sensor", "job_name": None, "status": "configured"}
        ],
    }


def _map_dataset_execution() -> dict[str, object]:
    return {
        "kind": "import_job",
        "id": _CANCEL_PROBE_JOB_ID,
        "detail_url": (
            f"/v1/ops/pipeline/executions/import_job/{_CANCEL_PROBE_JOB_ID}"
        ),
        "status": "done",
        "pair_status": "done",
        "operation_member_id": _OPERATION_MEMBER_ID,
        "sync_scope": "dataset_wide",
        "providers": ["kma"],
        "dataset_keys": ["weather"],
        "provider_datasets": [
            {
                "provider": "kma",
                "dataset_key": "weather",
                "sync_scope": "dataset_wide",
                "operation_member_id": _OPERATION_MEMBER_ID,
                "status": "done",
            }
        ],
        "created_at": "2026-07-18T00:00:00Z",
        "started_at": "2026-07-18T00:00:01Z",
        "finished_at": "2026-07-18T00:01:00Z",
        "dagster_run_id": None,
        "dagster_run_status": None,
        "trigger_kind": "manual",
        "operation_registry_version": "1",
        "error_message": None,
        "projected_job": {
            "id": _CANCEL_PROBE_JOB_ID,
            "job_kind": "provider_feature_load_run",
            "status": "done",
            "progress": 100,
            "current_stage": "completed",
            "error_message": None,
            "created_at": "2026-07-18T00:00:00Z",
            "started_at": "2026-07-18T00:00:01Z",
            "finished_at": "2026-07-18T00:01:00Z",
            "dagster_run_id": None,
            "dagster_run_status": None,
            "trigger_kind": "manual",
            "operation_registry_version": "1",
            "depth": 0,
            "detail_url": (
                f"/v1/ops/pipeline/executions/import_job/{_CANCEL_PROBE_JOB_ID}"
            ),
        },
        "cancellation": None,
    }


def _map_dataset_row() -> dict[str, object]:
    return {
        "provider": "kma",
        "dataset_key": "weather",
        "detail_url": (
            "/v1/ops/datasets/detail?provider=kma&dataset_key=weather&"
            "sync_scope=dataset_wide"
        ),
        "sync_scope": "dataset_wide",
        "status": "never_run",
        "last_success_at": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "eligible_after": None,
        "freshness": {
            "state": "never_run",
            "basis": "unknown",
            "sla_seconds": None,
            "due_at": None,
            "is_overdue": False,
            "overdue_by_seconds": 0,
        },
        "schedule": {
            "source": "dagster_graphql",
            "basis": "not_scheduled",
            "status": None,
            "schedule_names": [],
            "active_schedule_names": [],
            "next_scheduled_at": None,
        },
        "latest_execution": None,
        "active_execution": None,
        "catalog_state": "orphan",
        "orphan_reason": "not present in catalog",
        "mutable": False,
        "catalog": None,
        "refresh_policy": None,
        "dataset_issues": {"open_count": 0, "severity_counts": {}},
        "provider_issues": {"open_count": 0, "severity_counts": {}},
    }


def _map_datasets_envelope() -> dict[str, object]:
    return {
        "data": {
            "items": [_map_dataset_row()],
            "schedule_source_status": "ok",
            "schedule_source_errors": [],
            "execution_coverage": "db_recorded_canonical_operations",
        },
        "meta": {"duration_ms": 1, "request_id": "c6c-smoke"},
    }


def _compose_resolved_literal(value: str) -> str:
    return value.replace("$", "$$")


def _resolved_compose() -> dict[str, object]:
    return {
        "services": {
            "kor-travel-map-api": {
                "container_name": "kor-travel-map-api-latest",
                "network_mode": "host",
                "image": "kor-travel-map-api:latest-main",
                "environment": {
                    "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _READ_TOKEN,
                    "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
                    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
                    "KOR_TRAVEL_MAP_API_PORT": "12701",
                }
            },
            "pinvi-api": {
                "container_name": "pinvi-api-latest",
                "network_mode": "host",
                "image": "pinvi-api:latest-main",
                "environment": {
                    "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN": _READ_TOKEN,
                    "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
                    "PINVI_ENVIRONMENT": "production",
                    "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL": "http://127.0.0.1:12701",
                }
            },
            "kor-travel-map-ui": {
                "container_name": "kor-travel-map-ui-latest",
                "network_mode": "host",
                "environment": {
                    "NODE_ENV": "production",
                    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
                    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": (
                        _compose_resolved_literal(_MAP_UI_PASSWORD_HASH)
                    ),
                    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
                },
            },
            "pinvi-web": {"command": ["npm", "start"]},
        }
    }


def _resolved_compose_with_map_ui_auth() -> dict[str, object]:
    return deepcopy(_resolved_compose())


def _resolved_candidate_environment() -> dict[str, str]:
    return {
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _READ_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
    }


def _raw_candidate_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
    }
    environment.update(overrides)
    return environment


def _runtime_secret_configs(
    config: C6cDeploymentConfig,
) -> dict[str, dict[str, object]]:
    return {
        config.map_container: {
            "Env": {
                "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _READ_TOKEN,
                "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
                "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            }
        },
        config.pinvi_container: {
            "Env": {
                "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN": _READ_TOKEN,
                "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN": _CANCEL_TOKEN,
            }
        },
        config.map_ui_container: {
            "Env": {
                "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
                "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
                "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
            }
        },
        "kor-travel-map-dagster-latest": {},
        "pinvi-web-latest": {},
    }


def _map_ui_source_candidate() -> tuple[Path, dict[str, object]]:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    services = document["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    candidate = _source_compose()
    candidate_services = candidate["services"]
    assert isinstance(candidate_services, dict)
    candidate_services["kor-travel-map-ui"] = deepcopy(map_ui)
    return compose_path, candidate


def _source_compose() -> dict[str, object]:
    return {
        "services": {
            "kor-travel-map-api": {
                "environment": {
                    "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": (
                        "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"
                    ),
                    "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": (
                        "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
                    ),
                    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": (
                        "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?"
                        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED must be explicitly set}"
                    ),
                }
            },
            "pinvi-api": {
                "environment": {
                    "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN": (
                        "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"
                    ),
                    "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN": (
                        "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
                    ),
                }
            },
            "kor-travel-map-ui": {
                "environment": {
                    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": (
                        "${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:?"
                        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME must be explicitly set}"
                    ),
                    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": (
                        "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
                        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH must be explicitly set}"
                    ),
                    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": (
                        "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?"
                        "KOR_TRAVEL_MAP_UI_SESSION_SECRET must be explicitly set}"
                    ),
                }
            },
            "rustfs": {"environment": {"RUSTFS_LOG_LEVEL": "info"}},
        }
    }


def _materializable_source_compose() -> dict[str, object]:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    identities = {
        "kor-travel-map-api": "kor-travel-map-api-latest",
        "kor-travel-map-ui": "kor-travel-map-ui-latest",
        "pinvi-api": "pinvi-api-latest",
    }
    for service_name, container_name in identities.items():
        service = services[service_name]
        assert isinstance(service, dict)
        service.update(
            {
                "image": f"fixture.invalid/{service_name}:test",
                "container_name": container_name,
                "network_mode": "host",
            }
        )
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["image"] = "fixture.invalid/rustfs:test"
    return candidate


def _resolve_dollar_auth_candidate_with_docker_compose(
    tmp_path: Path,
) -> tuple[dict[str, str], dict[str, object]]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "COMPOSE_PROJECT_NAME": "c6c-dollar-auth-fixture",
        "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
        "PINVI_ENVIRONMENT": "development",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "",
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false",
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _DOLLAR_MAP_UI_USERNAME,
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _DOLLAR_MAP_UI_SESSION_SECRET,
    }
    snapshot = ComposeEnvironmentSnapshot(
        effective=environment,
        env_path=str(tmp_path / ".env"),
        compose_path=str(tmp_path / "docker-compose.yml"),
        override_path=str(tmp_path / "missing.override.yml"),
        env_file_identity=ComposeEnvFileIdentity(exists=False),
        env_file_bytes=b"",
    )
    resolved = ComposeService()._resolve_compose_candidate_unlocked(
        _materializable_source_compose(),
        environment=environment,
        expected_system_bind_snapshots=(),
        environment_snapshot=snapshot,
        environment_override=None,
        external_input_snapshot=ComposeExternalInputSnapshot(references=(), files=()),
    )
    assert isinstance(resolved, dict)
    return environment, resolved


def test_production_config_requires_explicit_matching_modes_and_strong_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(env_path)

    loaded = load_c6c_deployment_config(str(env_path))

    assert loaded.production is True
    assert loaded.base_url == "http://127.0.0.1:12701"


def test_production_config_rejects_matching_nondefault_container_bind_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(
        env_path,
        KOR_TRAVEL_MAP_API_CONTAINER_PORT="12711",
        PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL="http://127.0.0.1:12711",
    )

    with pytest.raises(DeploymentContractError, match="exactly 12701"):
        load_c6c_deployment_config(str(env_path))


def test_local_config_keeps_nondefault_map_port_policy_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(
        env_path,
        KTDM_DEPLOYMENT_ENVIRONMENT="local",
        PINVI_ENVIRONMENT="development",
        KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED="false",
        KOR_TRAVEL_MAP_API_CONTAINER_PORT="12711",
        PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL="http://127.0.0.1:12711",
    )

    loaded = load_c6c_deployment_config(str(env_path))

    assert loaded.production is False
    assert loaded.map_container_port == 12711


def test_production_state_paths_are_checkout_independent_and_project_scoped(
    tmp_path: Path,
) -> None:
    with patch.object(c6c_deployment.Path, "home", return_value=tmp_path):
        manifest, lock = c6c_state_paths(
            {
                "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
                "COMPOSE_PROJECT_NAME": "pinvi-prod",
            }
        )

    state_dir = (
        tmp_path / ".local" / "state" / "kor-travel-docker-manager" / "pinvi-prod"
    )
    assert Path(manifest) == state_dir / "compatible-pair-v2.json"
    assert Path(lock) == (
        tmp_path
        / ".local"
        / "state"
        / "kor-travel-docker-manager"
        / "global-mutation.lock"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"KTDM_C6C_STATE_ROOT": "/tmp/alternate-c6c-state"},
        {"KTDM_C6C_COMPATIBLE_PAIR_MANIFEST": "relative/pair.json"},
        {"KTDM_C6C_DEPLOYMENT_LOCK": "relative/deployment.lock"},
    ],
)
def test_production_state_paths_reject_every_path_override(
    overrides: dict[str, str],
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "COMPOSE_PROJECT_NAME": "pinvi-prod",
        **overrides,
    }

    with pytest.raises(DeploymentContractError, match="fixed"):
        c6c_state_paths(values)


def test_production_state_paths_cannot_split_one_project_lock(tmp_path: Path) -> None:
    with pytest.raises(DeploymentContractError, match="fixed"):
        c6c_state_paths(
            {
                "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
                "COMPOSE_PROJECT_NAME": "pinvi-prod",
                "KTDM_C6C_DEPLOYMENT_LOCK": str(tmp_path / "second.lock"),
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"KTDM_DEPLOYMENT_ENVIRONMENT": None},
        {"PINVI_ENVIRONMENT": None},
        {"KTDM_DEPLOYMENT_ENVIRONMENT": "local"},
        {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": ""},
        {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "short"},
        {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": f'"{_READ_TOKEN} "'},
        {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": f"{_READ_TOKEN[:16]} {_READ_TOKEN[17:]}"},
        {"KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _READ_TOKEN},
        {"KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false"},
        {"KTDM_DOCKER_NETWORK_MODE": None},
        {"KTDM_DOCKER_NETWORK_MODE": "bridge"},
        {"PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL": None},
        {"KTDM_C6C_CONTRACT_GENERATION": None},
        {"KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": None},
        {"KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": '" admin"'},
        {"KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": None},
        {
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": (
                "pbkdf2_sha256$99999$test-salt$test-digest"
            )
        },
        {"KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": "not-a-password-hash"},
        {"KOR_TRAVEL_MAP_UI_SESSION_SECRET": None},
        {"KOR_TRAVEL_MAP_UI_SESSION_SECRET": "too-short"},
        {"KOR_TRAVEL_MAP_UI_SESSION_SECRET": f'" {_MAP_UI_SESSION_SECRET}"'},
        {"KTDM_C6C_MAP_UI_ADMIN_PASSWORD": None},
        {"KTDM_C6C_PINVI_ADMIN_EMAIL": None},
        {"KTDM_C6C_CANCEL_PROBE_JOB_ID": "not-a-uuid"},
        {"KOR_TRAVEL_MAP_API_CONTAINER": "attacker-map"},
        {"KOR_TRAVEL_MAP_UI_CONTAINER": "attacker-map-ui"},
        {"PINVI_API_CONTAINER": "attacker-pinvi"},
        {"KOR_TRAVEL_MAP_API_CONTAINER_PORT": "12702"},
        {"PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL": "http://127.0.0.1:12702"},
    ],
)
def test_production_config_rejects_before_compose_and_never_echoes_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str | None],
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(env_path, **overrides)

    with pytest.raises(DeploymentContractError) as captured:
        load_c6c_deployment_config(str(env_path))

    assert _READ_TOKEN not in str(captured.value)
    assert _CANCEL_TOKEN not in str(captured.value)
    assert _MAP_UI_PASSWORD_HASH not in str(captured.value)
    assert _MAP_UI_SESSION_SECRET not in str(captured.value)
    assert _MAP_UI_PASSWORD not in str(captured.value)


@pytest.mark.parametrize(
    "environment_name",
    [
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
    ],
)
@pytest.mark.parametrize("whitespace", _UNICODE_WHITESPACE)
def test_map_ui_auth_rejects_every_unicode_whitespace_from_environment(
    environment_name: str,
    whitespace: str,
) -> None:
    assert whitespace.isspace()
    values = {
        name: value
        for name, value in _production_environment().items()
        if value is not None
    }
    rejected_value = (
        f"{whitespace}{_MAP_UI_USERNAME}"
        if environment_name == "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"
        else f"{'a' * 16}{whitespace}{'b' * 16}"
    )
    values[environment_name] = rejected_value

    with pytest.raises(
        DeploymentContractError,
        match="Map UI runtime authentication environment is invalid",
    ) as captured:
        load_c6c_deployment_config_from_environment(values)

    assert rejected_value not in str(captured.value)


def test_explicit_local_mode_keeps_tokenless_development_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(
        env_path,
        KTDM_DEPLOYMENT_ENVIRONMENT="local",
        PINVI_ENVIRONMENT="development",
        KOR_TRAVEL_MAP_API_OPS_READ_TOKEN="",
        KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN="",
        KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED="false",
    )

    loaded = load_c6c_deployment_config(str(env_path))

    assert loaded.production is False
    assert loaded.read_token == ""
    assert loaded.cancel_token == ""


@pytest.mark.parametrize(
    ("read_token", "cancel_token"),
    [
        (_READ_TOKEN, ""),
        ("", _CANCEL_TOKEN),
        ("short", "also-short"),
        (" " * 32, "\t" * 32),
        (_READ_TOKEN, _READ_TOKEN),
    ],
)
def test_local_mode_rejects_partial_or_weak_token_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_token: str,
    cancel_token: str,
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(
        env_path,
        KTDM_DEPLOYMENT_ENVIRONMENT="local",
        PINVI_ENVIRONMENT="development",
        KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=f'"{read_token}"',
        KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=f'"{cancel_token}"',
        KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED="false",
    )

    with pytest.raises(DeploymentContractError):
        load_c6c_deployment_config(str(env_path))


@pytest.mark.parametrize(
    ("service", "leak", "message"),
    [
        (
            "kor-travel-map-ui",
            {"command": ["worker", _READ_TOKEN]},
            "protected value leaks",
        ),
        (
            "pinvi-web",
            {"build": {"args": {"OPS_SECRET": _CANCEL_TOKEN}}},
            "protected value leaks",
        ),
        (
            "pinvi-web",
            {"environment": {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _READ_TOKEN}},
            "protected environment name leaks",
        ),
        (
            "kor-travel-map-ui",
            {"labels": {"PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN": "present"}},
            "protected environment name leaks",
        ),
        (
            "pinvi-web",
            {"environment": {"KTDM_C6C_PINVI_ADMIN_PASSWORD": "injected"}},
            "protected environment name leaks",
        ),
        (
            "pinvi-web",
            {
                "environment": {
                    "KTDM_C6C_CONTRACT_GENERATION": _CONTRACT_GENERATION
                }
            },
            "protected environment name leaks",
        ),
        (
            "kor-travel-map-ui",
            {"command": ["worker", _MAP_UI_PASSWORD]},
            "protected value leaks",
        ),
        (
            "kor-travel-map-ui",
            {"labels": {"contract": _CONTRACT_GENERATION}},
            "protected value leaks",
        ),
    ],
)
def test_resolved_compose_rejects_secret_leak_from_any_channel(
    service: str,
    leak: dict[str, object],
    message: str,
) -> None:
    resolved = _resolved_compose_with_map_ui_auth()
    services = resolved["services"]
    assert isinstance(services, dict)
    service_config = services[service]
    assert isinstance(service_config, dict)
    for key, value in leak.items():
        if key == "environment" and isinstance(value, dict):
            environment = service_config.setdefault("environment", {})
            assert isinstance(environment, dict)
            environment.update(value)
        else:
            service_config[key] = value

    with pytest.raises(
        DeploymentContractError,
        match=message,
    ):
        validate_resolved_compose_secret_isolation(resolved, _production_config())


def test_resolved_candidate_requires_map_ui_service() -> None:
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services.pop("kor-travel-map-ui")

    with pytest.raises(
        ComposeCandidateContractError,
        match="missing required protected services: kor-travel-map-ui",
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
        )


def test_resolved_compose_accepts_exact_protected_service_wiring() -> None:
    validate_resolved_compose_secret_isolation(
        _resolved_compose_with_map_ui_auth(),
        _production_config(),
    )


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", None),
        ("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", "attacker"),
        ("KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH", None),
        (
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
            "pbkdf2_sha256$100000$other-salt$other-digest",
        ),
        ("KOR_TRAVEL_MAP_UI_SESSION_SECRET", None),
        (
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
            "other-map-ui-session-secret-placeholder",
        ),
    ],
)
def test_resolved_compose_rejects_missing_or_changed_map_ui_auth(
    env_name: str,
    value: str | None,
) -> None:
    resolved = _resolved_compose_with_map_ui_auth()
    services = resolved["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    environment = map_ui["environment"]
    assert isinstance(environment, dict)
    if value is None:
        environment.pop(env_name)
    else:
        environment[env_name] = value

    with pytest.raises(
        DeploymentContractError,
        match=rf"does not wire {env_name} to kor-travel-map-ui",
    ):
        validate_resolved_compose_secret_isolation(resolved, _production_config())


@pytest.mark.parametrize(
    "top_level_leak",
    [
        {"secrets": {"safe_alias": {"name": _READ_TOKEN}}},
        {"configs": {"KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": {"external": True}}},
        {"x-contract": {"generation": _CONTRACT_GENERATION}},
    ],
)
def test_resolved_compose_rejects_protected_top_level_graph(
    top_level_leak: dict[str, object],
) -> None:
    resolved = _resolved_compose_with_map_ui_auth()
    resolved.update(top_level_leak)

    with pytest.raises(DeploymentContractError):
        validate_resolved_compose_secret_isolation(resolved, _production_config())


def test_generic_resolved_candidate_rejects_service_config_alias_value() -> None:
    resolved = _resolved_compose()
    resolved["configs"] = {"safe_alias": {"name": _CANCEL_TOKEN}}
    services = resolved["services"]
    assert isinstance(services, dict)
    rustfs = services.setdefault("rustfs", {})
    assert isinstance(rustfs, dict)
    rustfs["configs"] = [{"source": "safe_alias", "target": "/run/safe"}]

    with pytest.raises(ComposeCandidateContractError):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
        )


def test_candidate_document_rejects_indirect_value_after_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {"environment": {"INDIRECT": _READ_TOKEN}}
    resolve = Mock(return_value=resolved)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(tmp_path / "docker-compose.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(service, "_resolve_compose_candidate_unlocked", resolve)

    with pytest.raises(ComposeCandidateContractError):
        service._validate_compose_candidate_document_unlocked(
            _source_compose(),
            environment_override=_resolved_candidate_environment(),
        )

    resolve.assert_called_once()


@pytest.mark.parametrize(
    "boundary",
    ["include", "extends", "compose-file", "override-file"],
)
def test_compose_candidate_rejects_multi_file_composition_boundary(
    boundary: str,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    environment = _raw_candidate_environment()
    if boundary == "include":
        candidate["include"] = ["./compose.extra.yml"]
    elif boundary == "extends":
        services = candidate["services"]
        assert isinstance(services, dict)
        rustfs = services["rustfs"]
        assert isinstance(rustfs, dict)
        rustfs["extends"] = {"file": "./compose.extra.yml", "service": "rustfs"}
    elif boundary == "compose-file":
        environment["COMPOSE_FILE"] = "docker-compose.yml:compose.extra.yml"
    else:
        environment["KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE"] = (
            "compose.extra.yml"
        )

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=environment,
        )


def test_candidate_document_rejects_existing_override_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    override_path = tmp_path / "docker-compose.override.yml"
    override_path.write_text("services: {}\n", encoding="utf-8")
    resolve = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(tmp_path / "docker-compose.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(override_path),
    )
    monkeypatch.setattr(service, "_resolve_compose_candidate_unlocked", resolve)

    with pytest.raises(ComposeCandidateContractError, match="single-file"):
        service._validate_compose_candidate_document_unlocked(
            _source_compose(),
            environment_override=_raw_candidate_environment(),
        )

    resolve.assert_not_called()


@pytest.mark.parametrize(
    ("candidate_raw_hash", "candidate_resolved_hash", "message"),
    [
        ("raw-drift", "resolved-stable", "raw volume graph"),
        ("raw-stable", "resolved-drift", "resolved volume graph"),
    ],
)
def test_candidate_transaction_rejects_raw_or_resolved_volume_graph_drift(
    candidate_raw_hash: str,
    candidate_resolved_hash: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    persisted = ValidatedComposeCandidate(
        resolved={},
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash="resolved-stable",
    )
    candidate = ValidatedComposeCandidate(
        resolved={},
        system_bind_snapshots=(),
        raw_volume_graph_hash=candidate_raw_hash,
        resolved_volume_graph_hash=candidate_resolved_hash,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_c6c_deployment_lock_path",
        lambda: str(tmp_path / "compose.lock"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        Mock(return_value=persisted),
    )
    validate_candidate = Mock(return_value=candidate)
    monkeypatch.setattr(
        service,
        "_validate_compose_candidate_document_unlocked",
        validate_candidate,
    )

    with pytest.raises(ComposeCandidateContractError, match=message):
        service.capture_compose_candidate_transaction({"services": {}})

    validate_candidate.assert_called_once()


def test_compose_candidate_accepts_only_exact_api_source_wiring() -> None:
    validate_compose_candidate_protected_values(
        _source_compose(),
        compose_path="/tmp/docker-compose.yml",
        root_env_path="/tmp/.env",
        environment=_raw_candidate_environment(
            KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=_READ_TOKEN,
            KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=_CANCEL_TOKEN,
            KTDM_C6C_CONTRACT_GENERATION=_CONTRACT_GENERATION,
        ),
    )


def test_resolved_candidate_accepts_docker_compose_escaped_auth_literals(
    tmp_path: Path,
) -> None:
    environment, resolved = _resolve_dollar_auth_candidate_with_docker_compose(
        tmp_path
    )
    services = resolved["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    resolved_environment = map_ui["environment"]
    assert isinstance(resolved_environment, dict)

    for environment_name in (
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
    ):
        raw_value = environment[environment_name]
        assert resolved_environment[environment_name] == _compose_resolved_literal(
            raw_value
        )

    validate_resolved_compose_candidate_protected_values(
        resolved,
        environment=environment,
    )


@pytest.mark.parametrize(
    "invalid_resolved_hash",
    [
        _MAP_UI_PASSWORD_HASH,
        _compose_resolved_literal(_MAP_UI_PASSWORD_HASH).replace("$$", "$$$"),
    ],
    ids=["missing-dollar-escape", "extra-dollar"],
)
def test_resolved_candidate_rejects_inexact_compose_dollar_escaping(
    invalid_resolved_hash: str,
    tmp_path: Path,
) -> None:
    environment, resolved = _resolve_dollar_auth_candidate_with_docker_compose(
        tmp_path
    )
    services = resolved["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    resolved_environment = map_ui["environment"]
    assert isinstance(resolved_environment, dict)
    resolved_environment["KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"] = (
        invalid_resolved_hash
    )

    with pytest.raises(
        ComposeCandidateContractError,
        match="KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH wiring is invalid",
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
        )


@pytest.mark.parametrize(
    "environment_name",
    [
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
    ],
)
@pytest.mark.parametrize("field", ["environment", "command", "labels"])
def test_resolved_candidate_rejects_escaped_auth_literal_clones(
    environment_name: str,
    field: str,
    tmp_path: Path,
) -> None:
    environment, resolved = _resolve_dollar_auth_candidate_with_docker_compose(
        tmp_path
    )
    services = resolved["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    map_ui_environment = map_ui["environment"]
    assert isinstance(map_ui_environment, dict)
    escaped_value = map_ui_environment[environment_name]
    assert isinstance(escaped_value, str)
    assert "$$" in escaped_value
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    if field == "environment":
        rustfs_environment = rustfs["environment"]
        assert isinstance(rustfs_environment, dict)
        rustfs_environment["COPIED_AUTH"] = escaped_value
    elif field == "command":
        rustfs[field] = ["worker", escaped_value]
    else:
        rustfs[field] = {"copied-auth": escaped_value}

    with pytest.raises(
        ComposeCandidateContractError,
        match="resolved compose candidate leaks a protected C6c reference",
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
        )


def test_repository_compose_uses_fail_closed_map_ui_auth_wiring() -> None:
    compose_path, candidate = _map_ui_source_candidate()
    services = candidate["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    environment = map_ui["environment"]
    assert isinstance(environment, dict)
    assert environment["KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"] == (
        "${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:?"
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME must be explicitly set}"
    )
    assert environment["KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"] == (
        "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH must be explicitly set}"
    )
    assert environment["KOR_TRAVEL_MAP_UI_SESSION_SECRET"] == (
        "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?"
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET must be explicitly set}"
    )
    serialized = yaml.safe_dump(candidate, sort_keys=False)
    assert "KTDM_C6C_MAP_UI_ADMIN_PASSWORD" not in serialized
    assert _MAP_UI_PASSWORD not in serialized

    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(compose_path),
        root_env_path=str(compose_path.with_name(".env")),
        environment=_raw_candidate_environment(),
    )


def test_raw_candidate_requires_map_ui_service() -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    services.pop("kor-travel-map-ui")

    with pytest.raises(
        ComposeCandidateContractError,
        match="missing required protected services: kor-travel-map-ui",
    ):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", None),
        ("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", " admin"),
        ("KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH", None),
        ("KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH", "not-a-password-hash"),
        ("KOR_TRAVEL_MAP_UI_SESSION_SECRET", None),
        ("KOR_TRAVEL_MAP_UI_SESSION_SECRET", "too-short"),
    ],
)
def test_compose_candidate_rejects_invalid_map_ui_auth_source_environment(
    env_name: str,
    value: str | None,
) -> None:
    compose_path, candidate = _map_ui_source_candidate()
    environment: dict[str, str] = {
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
    }
    if value is None:
        environment.pop(env_name)
    else:
        environment[env_name] = value

    with pytest.raises(ComposeCandidateContractError, match="authentication"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(compose_path),
            root_env_path=str(compose_path.with_name(".env")),
            environment=environment,
        )


@pytest.mark.parametrize(
    ("env_name", "raw_value"),
    [
        ("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", None),
        (
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
            "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH}",
        ),
        (
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
            "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:-unsafe-default}",
        ),
    ],
)
def test_compose_candidate_rejects_noncanonical_map_ui_auth_wiring(
    env_name: str,
    raw_value: str | None,
) -> None:
    compose_path, candidate = _map_ui_source_candidate()
    services = candidate["services"]
    assert isinstance(services, dict)
    map_ui = services["kor-travel-map-ui"]
    assert isinstance(map_ui, dict)
    environment = map_ui["environment"]
    assert isinstance(environment, dict)
    if raw_value is None:
        environment.pop(env_name)
    else:
        environment[env_name] = raw_value

    with pytest.raises(ComposeCandidateContractError, match="wiring"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(compose_path),
            root_env_path=str(compose_path.with_name(".env")),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_cross_wired_api_source() -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    map_service = services["kor-travel-map-api"]
    assert isinstance(map_service, dict)
    environment = map_service["environment"]
    assert isinstance(environment, dict)
    environment["KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"] = (
        "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
    )

    with pytest.raises(ComposeCandidateContractError, match="wiring"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    "raw_value",
    [
        "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN}",
        "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-attacker-default}",
        "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:?attacker-message}",
    ],
)
def test_compose_candidate_rejects_noncanonical_api_suffix(raw_value: str) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    map_service = services["kor-travel-map-api"]
    assert isinstance(map_service, dict)
    environment = map_service["environment"]
    assert isinstance(environment, dict)
    environment["KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"] = raw_value

    with pytest.raises(ComposeCandidateContractError, match="wiring"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_noncanonical_required_message() -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    map_service = services["kor-travel-map-api"]
    assert isinstance(map_service, dict)
    environment = map_service["environment"]
    assert isinstance(environment, dict)
    environment["KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED"] = (
        "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?required}"
    )

    with pytest.raises(ComposeCandidateContractError, match="wiring"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    "top_level_leak",
    [
        {"secrets": {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": {"external": True}}},
        {"configs": {"safe_alias": {"content": _CANCEL_TOKEN}}},
        {"x-contract": {"alias": "KTDM_C6C_CONTRACT_GENERATION"}},
    ],
)
def test_compose_candidate_rejects_protected_reference_in_top_level_graph(
    top_level_leak: dict[str, object],
) -> None:
    candidate = _source_compose()
    candidate.update(top_level_leak)

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(
                KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=_CANCEL_TOKEN,
                KTDM_C6C_CONTRACT_GENERATION=_CONTRACT_GENERATION,
            ),
        )


@pytest.mark.parametrize("collection_name", ["secrets", "configs"])
def test_compose_candidate_rejects_protected_external_source_file(
    collection_name: str,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "safe-alias.txt"
    source_path.write_text(f"alias={_READ_TOKEN}\n", encoding="utf-8")
    candidate = _source_compose()
    candidate[collection_name] = {"safe_alias": {"file": source_path.name}}
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs[collection_name] = ["safe_alias"]

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=_READ_TOKEN
            ),
        )


@pytest.mark.parametrize(
    "path_expression",
    [
        "$ROOT_ALIAS",
        "${ROOT_ALIAS}",
        "${MISSING:-.env}",
        "${MISSING-.env}",
        "${ROOT_ALIAS:?required}",
        "${ROOT_ALIAS?required}",
        "${ROOT_ALIAS:+.env}",
        "${ROOT_ALIAS+.env}",
    ],
)
def test_compose_candidate_env_file_path_rejects_root_env_aliases(
    path_expression: str,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["env_file"] = path_expression

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                ROOT_ALIAS=str(tmp_path / ".env")
            ),
        )


@pytest.mark.parametrize(
    "path_expression",
    [
        "${ROOT_ALIAS/foo/bar}",
        "${MISSING:?required}",
        "${MISSING?required}",
        "${MISSING:-${ROOT_ALIAS}}",
        "$",
    ],
)
def test_compose_candidate_env_file_path_rejects_unresolved_or_unsupported(
    path_expression: str,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["env_file"] = path_expression

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                ROOT_ALIAS=str(tmp_path / ".env")
            ),
        )


def test_compose_candidate_env_file_path_supports_escaped_dollar(
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["env_file"] = "$$ROOT_ALIAS.env"

    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(tmp_path / "docker-compose.yml"),
        root_env_path=str(tmp_path / ".env"),
        environment=_raw_candidate_environment(ROOT_ALIAS=str(tmp_path / ".env")),
    )


@pytest.mark.parametrize(
    "volume_factory",
    [
        lambda root: f"{root.name}:/run/manager.env",
        lambda root: f"./nested/../{root.name}:/run/manager.env:ro",
        lambda root: f"{root}:/run/manager.env:ro",
        lambda root: {
            "type": "bind",
            "source": f"./{root.name}",
            "target": "/run/manager.env",
            "read_only": True,
        },
    ],
)
def test_compose_candidate_rejects_root_env_bind_short_and_long_syntax(
    volume_factory,
    tmp_path: Path,
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [volume_factory(root_env)]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(root_env),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_symlink_to_root_env_bind(tmp_path: Path) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    alias = tmp_path / "manager-secret.alias"
    alias.symlink_to(root_env)
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"./{alias.name}:/run/secret:ro"]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(root_env),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_interpolated_root_env_bind(tmp_path: Path) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = ["${MANAGER_ENV:?required}:/run/manager.env:ro"]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(root_env),
            environment=_raw_candidate_environment(MANAGER_ENV=str(root_env)),
        )


def test_compose_candidate_rejects_manager_state_file_bind(tmp_path: Path) -> None:
    manifest = tmp_path / "compatible-pair-v2.json"
    manifest.write_text("{}\n", encoding="utf-8")
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"{manifest}:/run/manager-state.json:ro"]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                KTDM_C6C_COMPATIBLE_PAIR_MANIFEST=str(manifest.resolve())
            ),
        )


@pytest.mark.parametrize(
    "source",
    [r"C:\manager\.env", r"C:/manager/.env", r"\\server\share\.env"],
)
def test_compose_candidate_rejects_windows_looking_bind_source(
    source: str,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [
        {"type": "bind", "source": source, "target": "/run/secret"}
    ]

    with pytest.raises(ComposeCandidateContractError, match="unsupported path"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_bind_file_containing_protected_value(
    tmp_path: Path,
) -> None:
    protected_file = tmp_path / "opaque-manager-secret"
    protected_file.write_text(f"alias={_CANCEL_TOKEN}\n", encoding="utf-8")
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"./{protected_file.name}:/run/secret:ro"]

    with pytest.raises(ComposeCandidateContractError, match="canonical baseline"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=_CANCEL_TOKEN
            ),
        )


def test_compose_candidate_named_volume_is_not_treated_as_host_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "manager-data").write_text(
        f"alias={_READ_TOKEN}\n", encoding="utf-8"
    )
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = ["manager-data:/data:ro"]
    candidate["volumes"] = {"manager-data": {}}

    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(tmp_path / "docker-compose.yml"),
        root_env_path=str(tmp_path / ".env"),
        environment=_raw_candidate_environment(
            KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=_READ_TOKEN
        ),
    )


def test_resolved_compose_candidate_rejects_canonical_root_env_bind(
    tmp_path: Path,
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {
        "volumes": [
            {
                "type": "bind",
                "source": str(root_env.resolve()),
                "target": "/run/manager.env",
                "read_only": True,
            }
        ]
    }

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(root_env),
        )


def test_resolved_compose_candidate_rejects_host_root_directory_bind(
    tmp_path: Path,
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {
        "volumes": [
            {"type": "bind", "source": "/", "target": "/host", "read_only": True}
        ]
    }

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(root_env),
        )


@pytest.mark.parametrize(
    ("collection_name", "resource", "reference"),
    [
        ("secrets", {"external": True}, "external_alias"),
        (
            "configs",
            {"name": "platform-managed-config"},
            {"source": "external_alias", "target": "/run/config"},
        ),
    ],
)
def test_compose_candidate_rejects_uninspectable_external_resource_reference(
    collection_name: str,
    resource: dict[str, object],
    reference: object,
) -> None:
    candidate = _source_compose()
    candidate[collection_name] = {"external_alias": resource}
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs[collection_name] = [reference]

    with pytest.raises(ComposeCandidateContractError, match="uninspectable external"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(),
        )


def test_resolved_compose_rejects_uninspectable_external_secret_reference() -> None:
    resolved = _resolved_compose()
    resolved["secrets"] = {"external_alias": {"external": True}}
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {"secrets": [{"source": "external_alias"}]}

    with pytest.raises(ComposeCandidateContractError, match="uninspectable external"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
        )


@pytest.mark.parametrize(
    "volume",
    [".:/host:ro", {"type": "bind", "source": ".", "target": "/host"}],
)
def test_compose_candidate_rejects_compose_root_directory_bind(
    volume: object,
    tmp_path: Path,
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [volume]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(root_env),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_manager_state_directory_bind(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "manager-state"
    state_dir.mkdir()
    manifest = state_dir / "compatible-pair-v2.json"
    lock = state_dir / "deployment.lock"
    manifest.write_text("{}\n", encoding="utf-8")
    lock.write_text("", encoding="utf-8")
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"{state_dir}:/host-state:ro"]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                KTDM_C6C_COMPATIBLE_PAIR_MANIFEST=str(manifest.resolve()),
                KTDM_C6C_DEPLOYMENT_LOCK=str(lock.resolve()),
            ),
        )


def test_compose_candidate_rejects_host_root_directory_bind(tmp_path: Path) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = ["/:/host:ro"]

    with pytest.raises(ComposeCandidateContractError, match="manager file"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_unapproved_directory_with_protected_child(
    tmp_path: Path,
) -> None:
    source = tmp_path / "opaque-directory"
    source.mkdir()
    (source / "nested-secret").write_text(
        f"alias={_READ_TOKEN}\n", encoding="utf-8"
    )
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [
        {"type": "bind", "source": str(source), "target": "/host", "read_only": True}
    ]

    with pytest.raises(ComposeCandidateContractError, match="canonical baseline"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=_READ_TOKEN
            ),
        )


@pytest.mark.parametrize(
    "volume",
    [
        "./future-secret:/run/future-secret:ro",
        "${FUTURE_SOURCE:-./future-secret}:/run/future-secret:ro",
        {
            "type": "bind",
            "source": "./nested/../future-secret",
            "target": "/run/future-secret",
            "read_only": True,
        },
    ],
)
def test_compose_candidate_rejects_missing_bind_source(
    volume: object,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [volume]

    with pytest.raises(ComposeCandidateContractError, match="does not exist"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )

    assert not (tmp_path / "future-secret").exists()


def test_resolved_compose_candidate_rejects_missing_absolute_bind_source(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "future-secret"
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {
        "volumes": [
            {"type": "bind", "source": str(missing), "target": "/run/secret"}
        ]
    }

    with pytest.raises(ComposeCandidateContractError, match="does not exist"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
        )

    assert not missing.exists()


def test_compose_candidate_rejects_oversized_bind_file(tmp_path: Path) -> None:
    source = tmp_path / "oversized-secret"
    source.write_bytes(b"x" * (1_048_576 + 1))
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"{source}:/run/oversized:ro"]

    with pytest.raises(ComposeCandidateContractError, match="canonical baseline"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_rejects_non_regular_bind_source(tmp_path: Path) -> None:
    source = tmp_path / "opaque-pipe"
    os.mkfifo(source)
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"{source}:/run/opaque:ro"]

    with pytest.raises(ComposeCandidateContractError, match="canonical baseline"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    "volume",
    [
        "/sys:/sys",
        "/sys:/sys:rw",
        "/sys:/sys:ro,z",
        {"type": "bind", "source": "/sys", "target": "/sys"},
        {
            "type": "bind",
            "source": "/sys",
            "target": "/sys",
            "read_only": False,
        },
        {
            "type": "bind",
            "source": "/sys",
            "target": "/sys",
            "read_only": "true",
        },
    ],
)
def test_cadvisor_system_bind_requires_exact_read_only_mode(
    volume: object,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    services["cadvisor"] = {"volumes": [volume]}

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    "volume",
    [
        "/sys:/sys:ro",
        {
            "type": "bind",
            "source": "/sys",
            "target": "/sys",
            "read_only": True,
        },
    ],
)
def test_cadvisor_rejects_mount_set_missing_docker_socket(
    volume: object,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    services["cadvisor"] = {"volumes": [volume]}

    with pytest.raises(ComposeCandidateContractError, match="exactly read-only"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize("read_only", [None, False, "true"])
def test_resolved_cadvisor_sys_rejects_non_boolean_or_writable_mode(
    read_only: object,
    tmp_path: Path,
) -> None:
    resolved = _resolved_compose()
    mount: dict[str, object] = {
        "type": "bind",
        "source": "/sys",
        "target": "/sys",
    }
    if read_only is not None:
        mount["read_only"] = read_only
    services = resolved["services"]
    assert isinstance(services, dict)
    services["cadvisor"] = {"volumes": [mount]}

    with pytest.raises(ComposeCandidateContractError):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
        )


def test_resolved_cadvisor_rejects_mount_set_missing_docker_socket(
    tmp_path: Path,
) -> None:
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["cadvisor"] = {
        "volumes": [
            {
                "type": "bind",
                "source": "/sys",
                "target": "/sys",
                "read_only": True,
                "bind": {"create_host_path": True},
            }
        ]
    }

    with pytest.raises(ComposeCandidateContractError, match="exactly read-only"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
        )


def test_cadvisor_mount_identity_set_accepts_only_exact_pair() -> None:
    mounts = (
        c6c_deployment.CandidateVolumeMount(
            kind="bind", source="/sys", target="/sys", read_only=True
        ),
        c6c_deployment.CandidateVolumeMount(
            kind="bind",
            source="/var/run/docker.sock",
            target="/var/run/docker.sock",
            read_only=True,
        ),
    )

    c6c_deployment._assert_candidate_cadvisor_mount_set(
        mounts,
        compose_directory=Path("/"),
        resolved_document=False,
    )


def test_cadvisor_raw_mount_identity_rejects_interpolated_alias() -> None:
    mounts = (
        c6c_deployment.CandidateVolumeMount(
            kind="bind",
            source="/sys",
            target="/sys",
            read_only=True,
            declared_source="${CADVISOR_SYS:-/sys}",
            declared_target="/sys",
        ),
        c6c_deployment.CandidateVolumeMount(
            kind="bind",
            source="/var/run/docker.sock",
            target="/var/run/docker.sock",
            read_only=True,
            declared_source="/var/run/docker.sock",
            declared_target="/var/run/docker.sock",
        ),
    )

    with pytest.raises(ComposeCandidateContractError, match="exactly read-only"):
        c6c_deployment._assert_candidate_cadvisor_mount_set(
            mounts,
            compose_directory=Path("/"),
            resolved_document=False,
        )


@pytest.mark.parametrize(
    "extra",
    [
        c6c_deployment.CandidateVolumeMount(
            kind="bind", source="/tmp/extra", target="/extra", read_only=True
        ),
        c6c_deployment.CandidateVolumeMount(
            kind="volume", source="metrics", target="/metrics", read_only=True
        ),
        c6c_deployment.CandidateVolumeMount(
            kind="bind", source="/sys", target="/sys", read_only=True
        ),
    ],
)
def test_cadvisor_mount_identity_set_rejects_extra_or_duplicate(
    extra: c6c_deployment.CandidateVolumeMount,
) -> None:
    mounts = (
        c6c_deployment.CandidateVolumeMount(
            kind="bind", source="/sys", target="/sys", read_only=True
        ),
        c6c_deployment.CandidateVolumeMount(
            kind="bind",
            source="/var/run/docker.sock",
            target="/var/run/docker.sock",
            read_only=True,
        ),
        extra,
    )

    with pytest.raises(ComposeCandidateContractError, match="exactly read-only"):
        c6c_deployment._assert_candidate_cadvisor_mount_set(
            mounts,
            compose_directory=Path("/"),
            resolved_document=False,
        )


@pytest.mark.parametrize(
    "volume",
    [
        "/var/run/docker.sock:/var/run/docker.sock",
        "/var/run/docker.sock:/var/run/docker.sock:rw",
        {
            "type": "bind",
            "source": "/var/run/docker.sock",
            "target": "/var/run/docker.sock",
        },
        {
            "type": "bind",
            "source": "/var/run/docker.sock",
            "target": "/var/run/docker.sock",
            "read_only": False,
        },
    ],
)
def test_cadvisor_docker_socket_rejects_writable_mount(
    volume: object,
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    services["cadvisor"] = {"volumes": [volume]}

    with pytest.raises(ComposeCandidateContractError, match="exactly read-only"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


def test_cadvisor_system_bind_rejects_symlink_alias(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sys-alias"
    source.symlink_to("/sys", target_is_directory=True)
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    services["cadvisor"] = {"volumes": [f"{source}:/sys:ro"]}

    with pytest.raises(ComposeCandidateContractError, match="cAdvisor mounts"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


def test_system_bind_snapshot_rejects_atomic_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = c6c_deployment.CandidateSystemBindSnapshot(
        service="cadvisor",
        source="/sys",
        target="/sys",
        read_only=True,
        path_chain=(
            c6c_deployment.CandidatePathIdentity(
                path="/sys",
                device=1,
                inode=10,
                mode=stat.S_IFDIR | 0o555,
                uid=0,
                gid=0,
            ),
        ),
    )
    replaced = c6c_deployment.CandidateSystemBindSnapshot(
        service="cadvisor",
        source="/sys",
        target="/sys",
        read_only=True,
        path_chain=(
            c6c_deployment.CandidatePathIdentity(
                path="/sys",
                device=2,
                inode=20,
                mode=stat.S_IFDIR | 0o555,
                uid=0,
                gid=0,
            ),
        ),
    )
    monkeypatch.setattr(
        c6c_deployment,
        "_capture_candidate_system_bind_snapshot",
        Mock(return_value=replaced),
    )

    with pytest.raises(ComposeCandidateContractError, match="identity changed"):
        c6c_deployment.revalidate_candidate_system_bind_snapshots((original,))


@pytest.mark.parametrize(
    ("socket_mode", "socket_gid", "message"),
    [
        (stat.S_IFSOCK | 0o660, 999, None),
        (stat.S_IFSOCK | 0o662, 999, "world-writable"),
        (stat.S_IFSOCK | 0o660, 998, "docker group"),
    ],
)
def test_cadvisor_socket_rejects_world_or_arbitrary_group_write(
    socket_mode: int,
    socket_gid: int,
    message: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_socket = Path("/run/docker.sock")
    path_stats = {
        "/run/docker.sock": SimpleNamespace(
            st_dev=1,
            st_ino=10,
            st_mode=socket_mode,
            st_uid=0,
            st_gid=socket_gid,
        ),
        "/run": SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=stat.S_IFDIR | 0o755,
            st_uid=0,
            st_gid=0,
        ),
        "/": SimpleNamespace(
            st_dev=1,
            st_ino=1,
            st_mode=stat.S_IFDIR | 0o755,
            st_uid=0,
            st_gid=0,
        ),
    }
    monkeypatch.setattr(Path, "is_symlink", lambda _self: False)
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda _self, strict=False: resolved_socket,
    )
    monkeypatch.setattr(Path, "stat", lambda self: path_stats[str(self)])
    monkeypatch.setattr(
        c6c_deployment.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=999),
    )

    def capture() -> c6c_deployment.CandidateSystemBindSnapshot:
        return c6c_deployment._capture_candidate_system_bind_snapshot(
            service="cadvisor",
            source="/var/run/docker.sock",
            target="/var/run/docker.sock",
            read_only=True,
        )

    if message is None:
        assert capture().path_chain[0].mode == socket_mode
    else:
        with pytest.raises(ComposeCandidateContractError, match=message):
            capture()


@pytest.mark.parametrize(
    "definition",
    [
        {"driver": "local", "driver_opts": {"type": "none"}},
        {"driver": "local", "driver_opts": {"o": "bind"}},
        {"driver": "local", "driver_opts": {"o": "rbind"}},
        {"driver": "local", "driver_opts": {"device": "/tmp/data"}},
        {"driver": "nfs"},
        {"external": True},
        {"external": False},
        {"external": {"name": "operator-volume"}},
        {"name": "operator-volume"},
    ],
)
def test_compose_candidate_rejects_unsafe_named_volume_definition(
    definition: dict[str, object],
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    candidate["volumes"] = {"unsafe-data": definition}
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = ["unsafe-data:/data"]

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


def test_compose_candidate_accepts_internal_default_named_volume(
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    candidate["volumes"] = {"safe-data": {}}
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = ["safe-data:/data"]

    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(tmp_path / "docker-compose.yml"),
        root_env_path=str(tmp_path / ".env"),
        environment=_raw_candidate_environment(),
    )


def test_compose_candidate_rejects_undeclared_named_volume_reference(
    tmp_path: Path,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = ["undeclared-data:/data"]

    with pytest.raises(ComposeCandidateContractError, match="undeclared"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    "definition",
    [
        None,
        {
            "name": "project_unsafe-data",
            "driver": "local",
            "driver_opts": {"o": "bind", "device": "/tmp/data"},
        },
        {"name": "project_unsafe-data", "driver": "nfs"},
        {"name": "operator-volume", "external": True},
    ],
)
def test_resolved_compose_candidate_rejects_unsafe_named_volume_definition(
    definition: object,
    tmp_path: Path,
) -> None:
    resolved = _resolved_compose()
    resolved["name"] = "project"
    resolved["volumes"] = {"unsafe-data": definition}
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {
        "volumes": [
            {"type": "volume", "source": "unsafe-data", "target": "/data"}
        ]
    }

    with pytest.raises(ComposeCandidateContractError):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
        )


def test_resolved_compose_candidate_accepts_internal_default_named_volume(
    tmp_path: Path,
) -> None:
    resolved = _resolved_compose()
    resolved["name"] = "project"
    resolved["volumes"] = {"safe-data": {"name": "project_safe-data"}}
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {
        "volumes": [
            {"type": "volume", "source": "safe-data", "target": "/data"}
        ]
    }

    validate_resolved_compose_candidate_protected_values(
        resolved,
        environment=_resolved_candidate_environment(),
        compose_path=str(tmp_path / "docker-compose.yml"),
        root_env_path=str(tmp_path / ".env"),
    )


@pytest.mark.parametrize(
    ("project_name", "resolved_name"),
    [("project", "operator-volume"), (None, "project_safe-data")],
)
def test_resolved_named_volume_rejects_alias_or_unknown_project(
    project_name: str | None,
    resolved_name: str,
    tmp_path: Path,
) -> None:
    resolved = _resolved_compose()
    if project_name is not None:
        resolved["name"] = project_name
    resolved["volumes"] = {"safe-data": {"name": resolved_name}}
    services = resolved["services"]
    assert isinstance(services, dict)
    services["rustfs"] = {
        "volumes": [
            {"type": "volume", "source": "safe-data", "target": "/data"}
        ]
    }

    with pytest.raises(ComposeCandidateContractError):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
        )


def test_compose_candidate_accepts_exact_rustfs_data_directory_bind(
    tmp_path: Path,
) -> None:
    source = tmp_path / "rustfs-data"
    source.mkdir()
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs["volumes"] = [f"{source}:/data"]

    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(tmp_path / "docker-compose.yml"),
        root_env_path=str(tmp_path / ".env"),
        environment=_raw_candidate_environment(RUSTFS_DATA_DIR=str(source)),
    )


def test_cadvisor_does_not_bind_host_root_or_docker_data() -> None:
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
    )
    cadvisor = compose["services"]["cadvisor"]

    assert "--docker_only=true" in cadvisor["command"]
    assert cadvisor["volumes"] == [
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "/sys:/sys:ro",
    ]


@pytest.mark.parametrize(
    "leak",
    [
        {"environment": {"ALIAS": "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"}},
        {"labels": {"secret": _CANCEL_TOKEN}},
        {"command": ["worker", "KTDM_C6C_CONTRACT_GENERATION"]},
    ],
)
def test_compose_candidate_rejects_protected_reference_in_non_api_service(
    leak: dict[str, object],
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    services["rustfs"] = leak

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(
                KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=_READ_TOKEN,
                KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=_CANCEL_TOKEN,
                KTDM_C6C_CONTRACT_GENERATION=_CONTRACT_GENERATION,
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", {"COPIED_USERNAME": _MAP_UI_USERNAME}),
        ("command", ["worker", _MAP_UI_USERNAME]),
        ("labels", {"copied-username": _MAP_UI_USERNAME}),
    ],
)
def test_raw_candidate_rejects_map_ui_username_outside_exact_path(
    field: str,
    value: object,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    rustfs = services["rustfs"]
    assert isinstance(rustfs, dict)
    rustfs[field] = value

    with pytest.raises(
        ComposeCandidateContractError,
        match="compose candidate leaks a protected C6c reference",
    ):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path="/tmp/docker-compose.yml",
            root_env_path="/tmp/.env",
            environment=_raw_candidate_environment(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", {"COPIED_USERNAME": _MAP_UI_USERNAME}),
        ("command", ["worker", _MAP_UI_USERNAME]),
        ("labels", {"copied-username": _MAP_UI_USERNAME}),
    ],
)
def test_resolved_candidate_rejects_map_ui_username_outside_exact_path(
    field: str,
    value: object,
) -> None:
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    pinvi_web = services["pinvi-web"]
    assert isinstance(pinvi_web, dict)
    pinvi_web[field] = value

    with pytest.raises(
        ComposeCandidateContractError,
        match="resolved compose candidate leaks a protected C6c reference",
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=_resolved_candidate_environment(),
        )


@pytest.mark.parametrize(
    "raw_value",
    [_READ_TOKEN, "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"],
)
def test_compose_candidate_rejects_protected_value_in_non_root_env_file(
    tmp_path: Path,
    raw_value: str,
) -> None:
    candidate = _source_compose()
    services = candidate["services"]
    assert isinstance(services, dict)
    env_file = tmp_path / "rustfs.env"
    env_file.write_text(f"ALIAS={raw_value}\n", encoding="utf-8")
    services["rustfs"] = {"env_file": env_file.name}

    with pytest.raises(ComposeCandidateContractError):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(tmp_path / "docker-compose.yml"),
            root_env_path=str(tmp_path / ".env"),
            environment=_raw_candidate_environment(
                KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=_READ_TOKEN
            ),
        )


@pytest.mark.parametrize(
    ("service_name", "field", "value", "message"),
    [
        ("kor-travel-map-api", "network_mode", "bridge", "requires host network"),
        ("pinvi-api", "network_mode", "bridge", "requires host network"),
        ("kor-travel-map-ui", "network_mode", "bridge", "requires host network"),
        (
            "kor-travel-map-api",
            "container_name",
            "attacker-map",
            "Map API container identity",
        ),
        (
            "pinvi-api",
            "container_name",
            "attacker-pinvi",
            "PinVi API container identity",
        ),
        (
            "kor-travel-map-ui",
            "container_name",
            "attacker-map-ui",
            "Map UI container identity",
        ),
        ("kor-travel-map-api", "env_file", ["malicious.env"], "forbids env_file"),
        ("pinvi-api", "env_file", ["malicious.env"], "forbids env_file"),
        ("kor-travel-map-ui", "env_file", ["malicious.env"], "forbids env_file"),
    ],
)
def test_resolved_compose_rejects_malicious_api_override(
    service_name: str,
    field: str,
    value: object,
    message: str,
) -> None:
    resolved = _resolved_compose_with_map_ui_auth()
    services = resolved["services"]
    assert isinstance(services, dict)
    service = services[service_name]
    assert isinstance(service, dict)
    service[field] = value

    with pytest.raises(DeploymentContractError, match=message):
        validate_resolved_compose_secret_isolation(resolved, _production_config())


@pytest.mark.parametrize(
    ("service_name", "env_name", "value", "message"),
    [
        (
            "kor-travel-map-api",
            "KOR_TRAVEL_MAP_API_PORT",
            "12799",
            "Map API bind port",
        ),
        ("pinvi-api", "PINVI_ENVIRONMENT", "local", "PinVi mode"),
        (
            "pinvi-api",
            "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL",
            "http://127.0.0.1:12799",
            "PinVi Map base URL",
        ),
        (
            "kor-travel-map-ui",
            "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
            "attacker",
            "does not wire KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
        ),
        (
            "kor-travel-map-ui",
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
            "pbkdf2_sha256$100000$other-salt$other-digest",
            "does not wire KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
        ),
        (
            "kor-travel-map-ui",
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
            "other-map-ui-session-secret-placeholder",
            "does not wire KOR_TRAVEL_MAP_UI_SESSION_SECRET",
        ),
    ],
)
def test_resolved_compose_rejects_malicious_api_environment_override(
    service_name: str,
    env_name: str,
    value: str,
    message: str,
) -> None:
    resolved = _resolved_compose_with_map_ui_auth()
    services = resolved["services"]
    assert isinstance(services, dict)
    service = services[service_name]
    assert isinstance(service, dict)
    environment = service["environment"]
    assert isinstance(environment, dict)
    environment[env_name] = value

    with pytest.raises(DeploymentContractError, match=message):
        validate_resolved_compose_secret_isolation(resolved, _production_config())


def test_rollback_resolved_compose_requires_both_manifest_image_ids() -> None:
    resolved = _resolved_compose()
    services = resolved["services"]
    assert isinstance(services, dict)
    services["kor-travel-map-api"]["image"] = _MAP_IMAGE_ID
    services["pinvi-api"]["image"] = _PINVI_IMAGE_ID
    pair = _manifest().active

    validate_resolved_compose_image_pair(resolved, _production_config(), pair)
    services["pinvi-api"]["image"] = _ACTIVE_PINVI_IMAGE_ID
    with pytest.raises(DeploymentContractError, match="immutable image"):
        validate_resolved_compose_image_pair(resolved, _production_config(), pair)

    wrong_generation = new_image_pair(_MAP_IMAGE_ID, _PINVI_IMAGE_ID, "legacy-c6b")
    services["pinvi-api"]["image"] = _PINVI_IMAGE_ID
    with pytest.raises(DeploymentContractError, match="generation"):
        validate_resolved_compose_image_pair(
            resolved,
            _production_config(),
            wrong_generation,
        )


def test_override_env_file_cannot_load_manager_root_env(
    tmp_path: Path,
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=hidden\n", encoding="utf-8")
    override = tmp_path / "docker-compose.override.yml"
    override.write_text(
        "services:\n  pinvi-web:\n    env_file:\n      - .env\n",
        encoding="utf-8",
    )

    with pytest.raises(DeploymentContractError, match="root .env"):
        validate_compose_env_file_isolation(
            [str(override)],
            root_env_path=str(root_env),
            environment={},
        )


def test_override_env_file_cannot_carry_ops_secret_under_another_name(
    tmp_path: Path,
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("PINVI_ENVIRONMENT=production\n", encoding="utf-8")
    leaked_env = tmp_path / "leaked.env"
    leaked_env.write_text(
        f"PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN={_CANCEL_TOKEN}\n",
        encoding="utf-8",
    )
    override = tmp_path / "docker-compose.override.yml"
    override.write_text(
        "services:\n  kor-travel-map-dagster:\n    env_file: leaked.env\n",
        encoding="utf-8",
    )

    with pytest.raises(DeploymentContractError, match="env_file"):
        validate_compose_env_file_isolation(
            [str(override)],
            root_env_path=str(root_env),
            environment={},
        )


def test_api_override_cannot_use_even_a_secret_free_env_file(tmp_path: Path) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("KTDM_DEPLOYMENT_ENVIRONMENT=production\n", encoding="utf-8")
    safe_env = tmp_path / "safe.env"
    safe_env.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
    override = tmp_path / "docker-compose.override.yml"
    override.write_text(
        "services:\n  pinvi-api:\n    env_file: safe.env\n",
        encoding="utf-8",
    )

    with pytest.raises(DeploymentContractError, match="explicit environment"):
        validate_compose_env_file_isolation(
            [str(override)],
            root_env_path=str(root_env),
            environment={},
        )


def test_non_api_env_file_cannot_carry_contract_generation(tmp_path: Path) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text("KTDM_DEPLOYMENT_ENVIRONMENT=production\n", encoding="utf-8")
    leaked_env = tmp_path / "leaked-generation.env"
    leaked_env.write_text(
        f"KTDM_C6C_CONTRACT_GENERATION={_CONTRACT_GENERATION}\n",
        encoding="utf-8",
    )
    override = tmp_path / "docker-compose.override.yml"
    override.write_text(
        "services:\n  pinvi-web:\n    env_file: leaked-generation.env\n",
        encoding="utf-8",
    )

    with pytest.raises(DeploymentContractError, match="env_file"):
        validate_compose_env_file_isolation(
            [str(override)],
            root_env_path=str(root_env),
            environment={},
        )


def test_runtime_secret_gate_accepts_exact_protected_containers() -> None:
    config = _production_config()
    validate_runtime_secret_isolation(_runtime_secret_configs(config), config)


def test_current_map_ui_runtime_accepts_exact_frozen_authentication() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)[config.map_ui_container]

    validate_current_map_ui_auth_runtime(runtime, config)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "authentication differs from the frozen environment"),
        ("changed", "authentication differs from the frozen environment"),
        ("duplicate", "duplicate authentication variables"),
        ("plaintext", "plaintext smoke credential"),
    ],
)
def test_current_map_ui_runtime_rejects_authentication_drift(
    mutation: str,
    message: str,
) -> None:
    config = _production_config()
    runtime = deepcopy(_runtime_secret_configs(config)[config.map_ui_container])
    environment = runtime["Env"]
    assert isinstance(environment, dict)
    if mutation == "missing":
        environment.pop("KOR_TRAVEL_MAP_UI_SESSION_SECRET")
    elif mutation == "changed":
        environment["KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"] = (
            "pbkdf2_sha256$100000$other-salt$other-digest"
        )
    elif mutation == "duplicate":
        runtime["Env"] = [
            f"{name}={value}"
            for name, value in environment.items()
        ] + [f"KOR_TRAVEL_MAP_UI_ADMIN_USERNAME={_MAP_UI_USERNAME}"]
    else:
        environment["UNRELATED"] = _MAP_UI_PASSWORD

    with pytest.raises(DeploymentContractError, match=message):
        validate_current_map_ui_auth_runtime(runtime, config)


@pytest.mark.parametrize("field", ["Env", "Cmd", "Labels"])
def test_current_map_ui_runtime_rejects_copied_username_in_other_scalar(
    field: str,
) -> None:
    config = _production_config()
    runtime = deepcopy(_runtime_secret_configs(config)[config.map_ui_container])
    if field == "Env":
        environment = runtime["Env"]
        assert isinstance(environment, dict)
        environment["COPIED_USERNAME"] = _MAP_UI_USERNAME
    elif field == "Cmd":
        runtime[field] = ["worker", _MAP_UI_USERNAME]
    else:
        runtime[field] = {"copied-username": _MAP_UI_USERNAME}

    with pytest.raises(
        DeploymentContractError,
        match="current Map UI authentication leaks outside",
    ):
        validate_current_map_ui_auth_runtime(runtime, config)


@pytest.mark.parametrize("field", ["Env", "Cmd", "Labels"])
def test_final_runtime_rejects_copied_map_ui_username_in_other_scalar(
    field: str,
) -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    map_ui = runtime[config.map_ui_container]
    if field == "Env":
        environment = map_ui["Env"]
        assert isinstance(environment, dict)
        environment["COPIED_USERNAME"] = _MAP_UI_USERNAME
    elif field == "Cmd":
        map_ui[field] = ["worker", _MAP_UI_USERNAME]
    else:
        map_ui[field] = {"copied-username": _MAP_UI_USERNAME}

    with pytest.raises(DeploymentContractError, match="leaks"):
        validate_runtime_secret_isolation(runtime, config)


@pytest.mark.parametrize(
    ("container_name", "env_name", "value", "message"),
    [
        ("missing-map-ui", None, None, "required C6c container is missing"),
        (
            "map-ui",
            "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
            None,
            "runtime protected value wiring is missing",
        ),
        (
            "map-ui",
            "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
            "attacker",
            "runtime protected value wiring is invalid",
        ),
        (
            "map-ui",
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
            None,
            "runtime protected value wiring is missing",
        ),
        (
            "map-ui",
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
            "pbkdf2_sha256$100000$other-salt$other-digest",
            "runtime protected value wiring is invalid",
        ),
        (
            "map-ui",
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
            None,
            "runtime protected value wiring is missing",
        ),
        (
            "map-ui",
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
            "other-map-ui-session-secret-placeholder",
            "runtime protected value wiring is invalid",
        ),
    ],
)
def test_runtime_secret_gate_rejects_missing_or_changed_map_ui_auth(
    container_name: str,
    env_name: str | None,
    value: str | None,
    message: str,
) -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    if container_name == "missing-map-ui":
        runtime.pop(config.map_ui_container)
    else:
        map_ui = runtime[config.map_ui_container]["Env"]
        assert isinstance(map_ui, dict)
        assert env_name is not None
        if value is None:
            map_ui.pop(env_name)
        else:
            map_ui[env_name] = value

    with pytest.raises(DeploymentContractError, match=message):
        validate_runtime_secret_isolation(runtime, config)


def test_runtime_secret_gate_rejects_non_api_container() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime["pinvi-web-latest"] = {
        "Cmd": ["worker", f"prefix-{_READ_TOKEN}-suffix"],
    }
    with pytest.raises(DeploymentContractError, match="leaks outside"):
        validate_runtime_secret_isolation(runtime, config)


def test_runtime_secret_gate_rejects_manager_only_smoke_credential() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime["pinvi-web-latest"] = {
        "Labels": {"KTDM_C6C_PINVI_ADMIN_PASSWORD": "injected"},
    }
    with pytest.raises(DeploymentContractError, match="leaks outside"):
        validate_runtime_secret_isolation(runtime, config)


def test_runtime_secret_gate_rejects_map_ui_plaintext_password() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime[config.map_ui_container]["Cmd"] = ["worker", _MAP_UI_PASSWORD]

    with pytest.raises(DeploymentContractError, match="leaks outside"):
        validate_runtime_secret_isolation(runtime, config)


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH", _MAP_UI_PASSWORD_HASH),
        ("KOR_TRAVEL_MAP_UI_SESSION_SECRET", _MAP_UI_SESSION_SECRET),
    ],
)
def test_runtime_secret_gate_rejects_map_ui_secret_in_other_container(
    env_name: str,
    value: str,
) -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime["kor-travel-map-dagster-latest"] = {"Env": {env_name: value}}

    with pytest.raises(DeploymentContractError, match="unauthorized container"):
        validate_runtime_secret_isolation(runtime, config)


def test_runtime_secret_gate_rejects_contract_generation_environment() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime["pinvi-web-latest"] = {
        "Env": {"KTDM_C6C_CONTRACT_GENERATION": _CONTRACT_GENERATION},
    }
    with pytest.raises(DeploymentContractError, match="manager-only"):
        validate_runtime_secret_isolation(runtime, config)


@pytest.mark.parametrize(
    "leak",
    [
        {"Cmd": ["worker", _READ_TOKEN]},
        {"Entrypoint": ["sh", "-c", f"echo {_CANCEL_TOKEN}"]},
        {"Labels": {"probe": "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"}},
    ],
)
def test_runtime_secret_gate_rejects_non_env_api_config_leaks(
    leak: dict[str, object],
) -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime[config.map_container].update(leak)
    with pytest.raises(DeploymentContractError, match="exact environment"):
        validate_runtime_secret_isolation(runtime, config)


def test_runtime_secret_gate_rejects_raw_env_secret_masked_by_duplicate() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime[config.map_container]["Env"] = [
        "OTHER=" + _READ_TOKEN,
        "OTHER=safe-later-value",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=" + _READ_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=" + _CANCEL_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true",
    ]
    with pytest.raises(DeploymentContractError, match="unauthorized variable"):
        validate_runtime_secret_isolation(runtime, config)


def test_runtime_secret_gate_rejects_duplicate_authorized_env() -> None:
    config = _production_config()
    runtime = _runtime_secret_configs(config)
    runtime[config.map_container]["Env"] = [
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=" + _READ_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=" + _READ_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=" + _CANCEL_TOKEN,
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true",
    ]
    with pytest.raises(DeploymentContractError, match="duplicate"):
        validate_runtime_secret_isolation(runtime, config)


def test_mandatory_service_readiness_requires_running_and_healthy_without_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    run = Mock(
        return_value={
            **_success(["ps"]),
            "stdout": json.dumps(
                [
                    {
                        "Service": "kor-travel-map-api",
                        "Name": "kor-travel-map-api-latest",
                        "State": "running",
                        "Health": "healthy",
                    },
                    {
                        "Service": "pinvi-api",
                        "Name": "pinvi-api-latest",
                        "State": "running",
                        "Health": "",
                    },
                ]
            ),
        }
    )
    monkeypatch.setattr(service, "run", run)
    transaction = Mock(spec=ComposeTransactionSnapshot)

    records = service._require_services_ready(
        ["kor-travel-map-api", "pinvi-api"],
        transaction=transaction,
    )

    assert len(records) == 2
    args = run.call_args.args[0]
    assert args[:3] == ["ps", "--format", "json"]
    assert "--all" not in args


@pytest.mark.parametrize(
    "records",
    [
        [],
        [
            {
                "Service": "kor-travel-map-api",
                "Name": "kor-travel-map-api-latest",
                "State": "exited",
                "Health": "",
            }
        ],
        [
            {
                "Service": "kor-travel-map-api",
                "Name": "kor-travel-map-api-latest",
                "State": "running",
                "Health": "unhealthy",
            }
        ],
    ],
)
def test_mandatory_service_readiness_rejects_missing_exited_or_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, str]],
) -> None:
    service = ComposeService()
    monkeypatch.setattr(
        service,
        "run",
        lambda *_args, **_kwargs: {
            **_success(["ps"]),
            "stdout": json.dumps(records),
        },
    )
    transaction = Mock(spec=ComposeTransactionSnapshot)

    with pytest.raises(DeploymentContractError):
        service._require_services_ready(
            ["kor-travel-map-api"],
            transaction=transaction,
        )


def test_map_smoke_uses_exact_status_gates_without_returning_bodies() -> None:
    responses = [
        (200, _map_datasets_envelope()),
        (401, _map_problem(401, "OPS_TOKEN_REQUIRED")),
        (403, _map_problem(403, "OPS_SCOPE_FORBIDDEN")),
        (403, _map_problem(403, "OPS_SCOPE_FORBIDDEN")),
        (404, _map_problem(404, "PIPELINE_EXECUTION_NOT_FOUND")),
        (403, _map_problem(403, "OPS_SCOPE_FORBIDDEN")),
    ]
    with patch.object(c6c_deployment, "_request_json", side_effect=responses) as request_json:
        result = run_map_ops_smoke(_production_config())

    assert [item["status"] for item in result] == [200, 401, 403, 403, 404, 403]
    assert request_json.call_count == 6
    calls = request_json.call_args_list
    assert calls[0].kwargs["headers"]["X-Kor-Travel-Map-Ops-Token"] == _READ_TOKEN
    assert "X-Kor-Travel-Map-Ops-Token" not in calls[1].kwargs["headers"]
    assert calls[2].kwargs["headers"]["X-Kor-Travel-Map-Ops-Token"] == _CANCEL_TOKEN
    assert calls[2].kwargs["method"] == "GET"
    assert calls[3].kwargs["headers"]["X-Kor-Travel-Map-Ops-Token"] == _READ_TOKEN
    assert calls[3].kwargs["headers"]["X-Kor-Travel-Map-Ops-Scope"] == "ops:cancel"
    assert calls[4].kwargs["headers"]["X-Kor-Travel-Map-Ops-Token"] == _CANCEL_TOKEN
    assert calls[5].kwargs["headers"]["X-Kor-Travel-Map-Ops-Token"] == _CANCEL_TOKEN


def test_map_smoke_rejects_status_code_or_problem_code_drift() -> None:
    for response in (
        (403, _map_problem(401, "OPS_TOKEN_REQUIRED")),
        (401, _map_problem(401, "OPS_SCOPE_FORBIDDEN")),
    ):
        responses = [(200, _map_datasets_envelope()), response]
        with (
            patch.object(c6c_deployment, "_request_json", side_effect=responses),
            pytest.raises(DeploymentContractError, match="typed 401"),
        ):
            run_map_ops_smoke(_production_config())


@pytest.mark.parametrize(
    "payload",
    [
        {"data": None, "meta": {"duration_ms": 1, "request_id": "c6c"}},
        {
            "data": {
                "items": {},
                "schedule_source_status": "ok",
                "schedule_source_errors": [],
                "execution_coverage": "db_recorded_canonical_operations",
            },
            "meta": {"duration_ms": 1, "request_id": "c6c"},
        },
        {"data": _map_datasets_envelope()["data"], "meta": None},
        {
            "data": _map_datasets_envelope()["data"],
            "meta": {"duration_ms": True, "request_id": "c6c"},
        },
    ],
)
def test_map_signed_smoke_rejects_null_or_untyped_envelope(
    payload: dict[str, object],
) -> None:
    responses = [(200, payload)]
    with (
        patch.object(c6c_deployment, "_request_json", side_effect=responses),
        pytest.raises(DeploymentContractError, match="canonical read"),
    ):
        run_map_ops_smoke(_production_config())


def test_map_signed_smoke_rejects_null_or_invalid_dataset_rows() -> None:
    valid = _map_datasets_envelope()
    valid_data = valid["data"]
    assert isinstance(valid_data, dict)
    valid_row = _map_dataset_row()
    valid_row["latest_execution"] = _map_dataset_execution()
    valid_data["items"] = [valid_row]
    assert c6c_deployment._validate_map_datasets_envelope(valid) is True

    invalid_items: list[object] = [None]
    invalid = _map_dataset_row()
    invalid["freshness"] = None
    invalid_items.append(invalid)
    invalid = _map_dataset_row()
    invalid["status"] = "healthy"
    invalid_items.append(invalid)
    invalid = _map_dataset_row()
    invalid["schedule"] = {"source": "fallback"}
    invalid_items.append(invalid)
    invalid = _map_dataset_row()
    invalid["latest_execution"] = {}
    invalid_items.append(invalid)
    invalid = _map_dataset_row()
    invalid_execution = _map_dataset_execution()
    invalid_execution["provider_datasets"] = [None]
    invalid["latest_execution"] = invalid_execution
    invalid_items.append(invalid)

    for item in invalid_items:
        payload = _map_datasets_envelope()
        data = payload["data"]
        assert isinstance(data, dict)
        data["items"] = [item]
        assert c6c_deployment._validate_map_datasets_envelope(payload) is False


def test_http_error_body_is_never_read_or_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = io.BytesIO(f"upstream body contains {_READ_TOKEN}".encode())
    error = urllib.error.HTTPError("http://127.0.0.1", 500, "failure", {}, body)
    monkeypatch.setattr(c6c_deployment.urllib.request, "urlopen", Mock(side_effect=error))

    status, payload = c6c_deployment._request_json(
        "http://127.0.0.1:12701/v1/ops/datasets",
        method="GET",
        headers={},
    )

    assert status == 500
    assert payload is None
    assert body.tell() == 0


def test_pinvi_canonical_smoke_requires_envelopes_typed_cancel_and_logout() -> None:
    responses = [
        HttpProbeResponse(200, {"data": {"roles": ["admin"]}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            409,
            {
                "error": {
                    "code": "PIPELINE_CANCELLATION_IN_PROGRESS",
                    "message": "redacted",
                    "details": _cancel_error_details(
                        status="in_progress", retryable=False
                    ),
                }
            },
            retry_after=17,
        ),
        HttpProbeResponse(204, None, set_cookie=True),
        HttpProbeResponse(401, None),
    ]
    with patch.object(c6c_deployment, "_session_request", side_effect=responses) as request:
        result = run_pinvi_canonical_smoke(_production_config())

    assert [item["status"] for item in result] == [200, 200, 200, 409, 204, 401]
    assert request.call_count == 6
    assert request.call_args_list[0].args[1].endswith("/auth/login")
    assert request.call_args_list[4].args[1].endswith("/auth/logout")
    serialized = json.dumps(result)
    assert _PINVI_ADMIN_PASSWORD not in serialized
    assert _READ_TOKEN not in serialized
    assert _CANCEL_TOKEN not in serialized


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-18",
        "2026-07-18T00:00:00",
        "2026-07-18 00:00:00+00:00",
        "2026-07-18T00:00:00+00",
    ],
)
def test_pinvi_iso8601_contract_requires_datetime_offset(value: str) -> None:
    assert c6c_deployment._is_iso8601(value) is False


def test_pinvi_canonical_smoke_rejects_missing_retry_after() -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}, "meta": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            409,
            {
                "error": {
                    "code": "PIPELINE_CANCELLATION_IN_PROGRESS",
                    "details": _cancel_error_details(
                        status="in_progress", retryable=False
                    ),
                }
            },
        ),
    ]
    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="Retry-After"),
    ):
        run_pinvi_canonical_smoke(_production_config())


@pytest.mark.parametrize(
    ("status_code", "error_code", "attempt_status", "retryable"),
    [
        (409, "PIPELINE_CANCELLATION_UNSAFE", "failed", False),
        (502, "DAGSTER_TERMINATE_FAILED", "retryable", True),
        (503, "DAGSTER_TERMINATION_TIMEOUT", "retryable", True),
    ],
)
def test_pinvi_cancel_rejects_present_but_invalid_retry_after(
    status_code: int,
    error_code: str,
    attempt_status: str,
    retryable: bool,
) -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            status_code,
            {
                "error": {
                    "code": error_code,
                    "details": _cancel_error_details(
                        status=attempt_status,
                        retryable=retryable,
                        error_code=error_code,
                    ),
                }
            },
            retry_after=None,
            retry_after_present=True,
        ),
    ]

    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="Retry-After"),
    ):
        run_pinvi_canonical_smoke(_production_config())


@pytest.mark.parametrize("raw", ["1", "5", "300", "001"])
def test_retry_after_parser_accepts_only_bounded_ascii_decimal(raw: str) -> None:
    assert c6c_deployment._retry_after_header(raw) == int(raw)


@pytest.mark.parametrize(
    "raw",
    ["garbage", "+5", " 5", "5 ", "\t5", "５", "٥", "0", "301", "-1"],
)
def test_retry_after_parser_rejects_noncanonical_or_out_of_range(raw: str) -> None:
    assert c6c_deployment._retry_after_header(raw) is None


@pytest.mark.parametrize(
    ("status_code", "error_code", "attempt_status", "retryable", "retry_after"),
    [
        (409, "PIPELINE_CANCELLATION_IN_PROGRESS", "in_progress", False, 7),
        (409, "PIPELINE_CANCELLATION_UNSAFE", "failed", False, None),
        (502, "DAGSTER_TERMINATE_FAILED", "retryable", True, 7),
        (503, "DAGSTER_UNAVAILABLE", "retryable", True, 7),
        (503, "DAGSTER_TERMINATION_TIMEOUT", "retryable", True, 7),
    ],
)
def test_pinvi_cancel_fixture_accepts_only_owned_typed_contract(
    status_code: int,
    error_code: str,
    attempt_status: str,
    retryable: bool,
    retry_after: int | None,
) -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            status_code,
            {
                "error": {
                    "code": error_code,
                    "details": _cancel_error_details(
                        status=attempt_status,
                        retryable=retryable,
                        error_code=error_code,
                    ),
                }
            },
            retry_after=retry_after,
        ),
        HttpProbeResponse(204, None, set_cookie=True),
        HttpProbeResponse(401, None),
    ]

    with patch.object(c6c_deployment, "_session_request", side_effect=responses):
        result = run_pinvi_canonical_smoke(_production_config())

    assert result[3] == {
        "name": "pinvi_cancel_error",
        "status": status_code,
        "code": error_code,
    }


def test_pinvi_cancel_fixture_accepts_canonical_409_root_without_attempt() -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            409,
            {
                "error": {
                    "code": "PIPELINE_CANCELLATION_IN_PROGRESS",
                    "details": _cancel_root_without_attempt(),
                }
            },
            retry_after=7,
        ),
        HttpProbeResponse(204, None, set_cookie=True),
        HttpProbeResponse(401, None),
    ]

    with patch.object(c6c_deployment, "_session_request", side_effect=responses):
        result = run_pinvi_canonical_smoke(_production_config())

    assert result[3]["code"] == "PIPELINE_CANCELLATION_IN_PROGRESS"


def test_pinvi_full_409_accepts_actual_resolved_and_cas_drift_matrix() -> None:
    all_resolved = _cancel_error_details(status="in_progress", retryable=False)
    root_member = all_resolved["members"]
    assert isinstance(root_member, list)
    assert isinstance(root_member[0], dict)
    root_member[0].update(
        {
            "result": "cancelled",
            "terminal_status": "cancelled",
            "error": None,
        }
    )
    all_resolved["unresolved_member_count"] = 0

    child_unresolved = deepcopy(all_resolved)
    child_members = child_unresolved["members"]
    assert isinstance(child_members, list)
    child_members.append(
        {
            "job_id": "99999999-9999-4999-8999-999999999999",
            "dagster_run_id": None,
            "operation_kind": "provider_feature_load_run",
            "requires_run_termination": False,
            "initial_status": "queued",
            "result": "pending",
            "terminal_status": None,
            "error": None,
            "updated_at": "2026-07-18T00:00:03Z",
        }
    )
    child_unresolved["unresolved_member_count"] = 1

    definitive_cas_drift = _cancel_error_details(
        status="in_progress",
        retryable=False,
    )
    drift_members = definitive_cas_drift["members"]
    assert isinstance(drift_members, list)
    assert isinstance(drift_members[0], dict)
    definitive_error = {
        "code": "PIPELINE_CANCELLATION_UNSAFE",
        "message": "frozen member tracking diverged",
        "details": {},
    }
    drift_members[0].update(
        {
            "dagster_run_id": "owned-dagster-run",
            "requires_run_termination": True,
            "result": "cancel_failed",
            "terminal_status": None,
            "error": definitive_error,
        }
    )
    definitive_cas_drift["dagster_runs"] = [
        {
            "dagster_run_id": "owned-dagster-run",
            "initial_status": "STARTED",
            "termination_reserved_at": "2026-07-18T00:00:01Z",
            "result": "cancelled",
            "terminal_status": "CANCELED",
            "error": None,
            "engine_started_at": "2026-07-18T00:00:01Z",
            "engine_finished_at": "2026-07-18T00:00:02Z",
            "updated_at": "2026-07-18T00:00:03Z",
        }
    ]

    for details in (all_resolved, child_unresolved, definitive_cas_drift):
        c6c_deployment._validate_owned_cancel_error_details(
            details,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )

    failed_cas_drift = deepcopy(definitive_cas_drift)
    failed_cas_drift.update(
        {
            "status": "failed",
            "error": definitive_error,
            "finished_at": "2026-07-18T00:00:03Z",
        }
    )
    c6c_deployment._validate_owned_cancel_error_details(
        failed_cas_drift,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_UNSAFE",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )


def test_pinvi_in_progress_runless_failure_requires_definitive_code() -> None:
    definitive = _cancel_error_details(status="in_progress", retryable=False)
    members = definitive["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0].update(
        {
            "result": "cancel_failed",
            "error": {
                "code": "PIPELINE_CANCELLATION_UNSAFE",
                "message": "frozen member tracking diverged",
                "details": {},
            },
        }
    )

    c6c_deployment._validate_owned_cancel_error_details(
        definitive,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )

    retryable = deepcopy(definitive)
    retryable_members = retryable["members"]
    assert isinstance(retryable_members, list)
    assert isinstance(retryable_members[0], dict)
    assert isinstance(retryable_members[0]["error"], dict)
    retryable_members[0]["error"]["code"] = "DAGSTER_UNAVAILABLE"
    with pytest.raises(DeploymentContractError, match="runless failure"):
        c6c_deployment._validate_owned_cancel_error_details(
            retryable,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )


def test_pinvi_in_progress_run_backed_failure_policy_groups_must_match() -> None:
    details = _cancel_error_details(status="in_progress", retryable=False)
    members = details["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    retryable_error = {
        "code": "DAGSTER_UNAVAILABLE",
        "message": "Dagster unavailable",
        "details": {},
    }
    members[0].update(
        {
            "dagster_run_id": "owned-dagster-run",
            "requires_run_termination": True,
            "result": "cancel_failed",
            "error": deepcopy(retryable_error),
        }
    )
    details["dagster_runs"] = [
        {
            "dagster_run_id": "owned-dagster-run",
            "initial_status": "STARTED",
            "termination_reserved_at": "2026-07-18T00:00:01Z",
            "result": "cancel_failed",
            "terminal_status": None,
            "error": retryable_error,
            "engine_started_at": None,
            "engine_finished_at": None,
            "updated_at": "2026-07-18T00:00:03Z",
        }
    ]

    c6c_deployment._validate_owned_cancel_error_details(
        details,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )

    mismatched = deepcopy(details)
    runs = mismatched["dagster_runs"]
    assert isinstance(runs, list)
    assert isinstance(runs[0], dict)
    assert isinstance(runs[0]["error"], dict)
    runs[0]["error"]["code"] = "PIPELINE_CANCELLATION_UNSAFE"
    with pytest.raises(DeploymentContractError, match="policies must match"):
        c6c_deployment._validate_owned_cancel_error_details(
            mismatched,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )


@pytest.mark.parametrize(
    ("member_result", "member_status", "run_result", "run_status"),
    [
        ("cancelled", "cancelled", "cancelled", "CANCELED"),
        ("already_terminal", "done", "already_terminal", "SUCCESS"),
        ("already_terminal", "failed", "already_terminal", "FAILURE"),
    ],
)
def test_pinvi_resolved_run_mapping_matches_canonical_terminal_state(
    member_result: str,
    member_status: str,
    run_result: str,
    run_status: str,
) -> None:
    details = _cancel_error_details(status="in_progress", retryable=False)
    members = details["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0].update(
        {
            "dagster_run_id": "owned-dagster-run",
            "operation_kind": "provider_import",
            "requires_run_termination": True,
            "result": member_result,
            "terminal_status": member_status,
            "error": None,
        }
    )
    details["unresolved_member_count"] = 0
    details["dagster_runs"] = [
        {
            "dagster_run_id": "owned-dagster-run",
            "initial_status": "STARTED",
            "termination_reserved_at": "2026-07-18T00:00:01Z",
            "result": run_result,
            "terminal_status": run_status,
            "error": None,
            "engine_started_at": "2026-07-18T00:00:01Z",
            "engine_finished_at": "2026-07-18T00:00:02Z",
            "updated_at": "2026-07-18T00:00:03Z",
        }
    ]

    c6c_deployment._validate_owned_cancel_error_details(
        details,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )


@pytest.mark.parametrize(
    ("member_result", "member_status", "run_result", "run_status"),
    [
        ("already_terminal", "done", "already_terminal", "FAILURE"),
        ("already_terminal", "failed", "already_terminal", "SUCCESS"),
        ("cancelled", "cancelled", "already_terminal", "SUCCESS"),
    ],
)
def test_pinvi_resolved_run_mapping_rejects_terminal_drift(
    member_result: str,
    member_status: str,
    run_result: str,
    run_status: str,
) -> None:
    details = _cancel_error_details(status="in_progress", retryable=False)
    members = details["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0].update(
        {
            "dagster_run_id": "owned-dagster-run",
            "operation_kind": "provider_import",
            "requires_run_termination": True,
            "result": member_result,
            "terminal_status": member_status,
            "error": None,
        }
    )
    details["unresolved_member_count"] = 0
    details["dagster_runs"] = [
        {
            "dagster_run_id": "owned-dagster-run",
            "initial_status": "STARTED",
            "termination_reserved_at": "2026-07-18T00:00:01Z",
            "result": run_result,
            "terminal_status": run_status,
            "error": None,
            "engine_started_at": "2026-07-18T00:00:01Z",
            "engine_finished_at": "2026-07-18T00:00:02Z",
            "updated_at": "2026-07-18T00:00:03Z",
        }
    ]

    with pytest.raises(DeploymentContractError, match="terminal result"):
        c6c_deployment._validate_owned_cancel_error_details(
            details,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )


def test_pinvi_feature_load_failed_after_success_requires_same_run_child() -> None:
    details = _cancel_error_details(status="in_progress", retryable=False)
    members = details["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0].update(
        {
            "dagster_run_id": "owned-dagster-run",
            "operation_kind": "provider_feature_load_run",
            "requires_run_termination": True,
            "result": "already_terminal",
            "terminal_status": "failed",
            "error": None,
        }
    )
    details["unresolved_member_count"] = 0
    details["dagster_runs"] = [
        {
            "dagster_run_id": "owned-dagster-run",
            "initial_status": "SUCCESS",
            "termination_reserved_at": "2026-07-18T00:00:01Z",
            "result": "already_terminal",
            "terminal_status": "SUCCESS",
            "error": None,
            "engine_started_at": "2026-07-18T00:00:01Z",
            "engine_finished_at": "2026-07-18T00:00:02Z",
            "updated_at": "2026-07-18T00:00:03Z",
        }
    ]

    with pytest.raises(DeploymentContractError, match="terminal result"):
        c6c_deployment._validate_owned_cancel_error_details(
            details,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )

    child = deepcopy(members[0])
    child.update(
        {
            "job_id": "99999999-9999-4999-8999-999999999999",
            "operation_kind": "provider_feature_load",
        }
    )
    members.append(child)
    c6c_deployment._validate_owned_cancel_error_details(
        details,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )


def test_pinvi_definitive_attempt_rejects_retryable_member_error() -> None:
    invalid = _cancel_error_details(
        status="failed",
        retryable=False,
        error_code="PIPELINE_CANCELLATION_UNSAFE",
    )
    members = invalid["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    assert isinstance(members[0]["error"], dict)
    members[0]["error"]["code"] = "DAGSTER_UNAVAILABLE"

    with pytest.raises(DeploymentContractError, match="failed cancellation"):
        c6c_deployment._validate_owned_cancel_error_details(
            invalid,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_UNSAFE",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )


def test_pinvi_failed_attempt_accepts_mixed_retryable_and_definitive_evidence() -> None:
    details = _cancel_error_details(
        status="failed",
        retryable=False,
        error_code="PIPELINE_CANCELLATION_UNSAFE",
    )
    members = details["members"]
    runs = details["dagster_runs"]
    assert isinstance(members, list)
    assert isinstance(runs, list)
    retryable_error = {
        "code": "DAGSTER_UNAVAILABLE",
        "message": "Dagster unavailable",
        "details": {},
    }
    retryable_member = deepcopy(members[0])
    retryable_member.update(
        {
            "job_id": "99999999-9999-4999-8999-999999999999",
            "dagster_run_id": "retryable-dagster-run",
            "error": retryable_error,
        }
    )
    retryable_run = deepcopy(runs[0])
    retryable_run.update(
        {
            "dagster_run_id": "retryable-dagster-run",
            "error": retryable_error,
        }
    )
    members.append(retryable_member)
    runs.append(retryable_run)
    details["unresolved_member_count"] = 2

    c6c_deployment._validate_owned_cancel_error_details(
        details,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_UNSAFE",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )


def test_pinvi_retry_subset_accepts_only_valid_run_backed_lineage() -> None:
    details = _cancel_error_details(
        status="failed",
        retryable=False,
        error_code="PIPELINE_CANCELLATION_UNSAFE",
    )
    details["previous_cancellation_id"] = "33333333-3333-4333-8333-333333333333"
    members = details["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0]["job_id"] = "99999999-9999-4999-8999-999999999999"

    c6c_deployment._validate_owned_cancel_error_details(
        details,
        expected_status=409,
        expected_code="PIPELINE_CANCELLATION_UNSAFE",
        expected_root_id=_CANCEL_PROBE_JOB_ID,
    )

    invalid = _cancel_error_details(status="in_progress", retryable=False)
    invalid["previous_cancellation_id"] = "33333333-3333-4333-8333-333333333333"
    with pytest.raises(DeploymentContractError, match="member/run/warning"):
        c6c_deployment._validate_owned_cancel_error_details(
            invalid,
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_IN_PROGRESS",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )


def test_pinvi_cancel_attempt_rejects_db_lifecycle_drift() -> None:
    invalid_details: list[tuple[dict[str, object], int, str]] = []
    invalid = _cancel_error_details(status="in_progress", retryable=False)
    invalid["finished_at"] = "2026-07-18T00:00:03Z"
    invalid_details.append((invalid, 409, "PIPELINE_CANCELLATION_IN_PROGRESS"))
    invalid = _cancel_error_details(status="in_progress", retryable=False)
    invalid["error"] = {
        "code": "PIPELINE_CANCELLATION_UNSAFE",
        "message": "unexpected in-progress error",
        "details": {},
    }
    invalid_details.append((invalid, 409, "PIPELINE_CANCELLATION_IN_PROGRESS"))
    invalid = _cancel_error_details(status="retryable", retryable=True)
    invalid["finished_at"] = None
    invalid_details.append((invalid, 502, "DAGSTER_TERMINATE_FAILED"))
    invalid = _cancel_error_details(status="failed", retryable=False)
    invalid["previous_cancellation_id"] = invalid["cancellation_id"]
    invalid_details.append((invalid, 409, "PIPELINE_CANCELLATION_UNSAFE"))

    for details, status_code, error_code in invalid_details:
        with pytest.raises(DeploymentContractError, match="DB lifecycle"):
            c6c_deployment._validate_owned_cancel_error_details(
                details,
                expected_status=status_code,
                expected_code=error_code,
                expected_root_id=_CANCEL_PROBE_JOB_ID,
            )


def test_pinvi_cancel_member_and_run_match_frozen_db_lifecycle() -> None:
    retryable = _cancel_error_details(status="retryable", retryable=True)
    members = retryable["members"]
    runs = retryable["dagster_runs"]
    assert isinstance(members, list)
    assert isinstance(runs, list)
    invalid_member = deepcopy(members[0])
    invalid_member["requires_run_termination"] = False
    assert c6c_deployment._validate_cancellation_member(invalid_member) is False

    invalid_member = deepcopy(members[0])
    invalid_member.update({"dagster_run_id": None, "requires_run_termination": True})
    assert c6c_deployment._validate_cancellation_member(invalid_member) is False

    invalid_run = deepcopy(runs[0])
    invalid_run["engine_started_at"] = "2026-07-18T00:00:01Z"
    assert c6c_deployment._validate_cancellation_run(invalid_run) is False

    invalid_run = deepcopy(runs[0])
    invalid_run.update(
        {
            "result": "cancelled",
            "terminal_status": "CANCELED",
            "error": None,
            "engine_started_at": "2026-07-18T00:00:03Z",
            "engine_finished_at": "2026-07-18T00:00:02Z",
        }
    )
    assert c6c_deployment._validate_cancellation_run(invalid_run) is False

    valid_run = deepcopy(invalid_run)
    valid_run.update({"engine_started_at": None, "engine_finished_at": None})
    assert c6c_deployment._validate_cancellation_run(valid_run) is True


def test_pinvi_retryable_attempt_requires_exact_run_backed_failures() -> None:
    invalid_details: list[dict[str, object]] = []
    invalid_details.append(
        _cancel_error_details(
            status="retryable",
            retryable=True,
            error_code="DAGSTER_TERMINATION_TIMEOUT",
        )
    )
    invalid = _cancel_error_details(status="retryable", retryable=True)
    members = invalid["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0]["requires_run_termination"] = False
    invalid_details.append(invalid)

    invalid = _cancel_error_details(status="retryable", retryable=True)
    members = invalid["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    assert isinstance(members[0]["error"], dict)
    members[0]["error"]["code"] = "PIPELINE_CANCELLATION_UNSAFE"
    invalid_details.append(invalid)

    invalid = _cancel_error_details(status="retryable", retryable=True)
    runs = invalid["dagster_runs"]
    assert isinstance(runs, list)
    assert isinstance(runs[0], dict)
    runs[0].update(
        {
            "result": "already_terminal",
            "terminal_status": "SUCCESS",
            "error": None,
        }
    )
    invalid_details.append(invalid)

    invalid = _cancel_error_details(status="retryable", retryable=True)
    runs = invalid["dagster_runs"]
    assert isinstance(runs, list)
    assert isinstance(runs[0], dict)
    assert isinstance(runs[0]["error"], dict)
    runs[0]["error"]["code"] = "PIPELINE_CANCELLATION_UNSAFE"
    invalid_details.append(invalid)

    for details in invalid_details:
        with pytest.raises(DeploymentContractError, match="C6c"):
            c6c_deployment._validate_owned_cancel_error_details(
                details,
                expected_status=502,
                expected_code="DAGSTER_TERMINATE_FAILED",
                expected_root_id=_CANCEL_PROBE_JOB_ID,
            )


def test_pinvi_root_only_shape_is_not_definitive_failure_evidence() -> None:
    with pytest.raises(DeploymentContractError, match="attempt lifecycle"):
        c6c_deployment._validate_owned_cancel_error_details(
            _cancel_root_without_attempt(),
            expected_status=409,
            expected_code="PIPELINE_CANCELLATION_UNSAFE",
            expected_root_id=_CANCEL_PROBE_JOB_ID,
        )


def test_pinvi_cancel_fixture_rejects_root_only_shape_for_non_409_code() -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            502,
            {
                "error": {
                    "code": "DAGSTER_TERMINATE_FAILED",
                    "details": _cancel_root_without_attempt(),
                }
            },
            retry_after=7,
        ),
    ]

    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="attempt lifecycle"),
    ):
        run_pinvi_canonical_smoke(_production_config())


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (429, "RATE_LIMITED"),
        (409, "INVALID_STATE"),
        (502, "DAGSTER_UNAVAILABLE"),
        (503, "DAGSTER_TERMINATE_FAILED"),
    ],
)
def test_pinvi_cancel_fixture_rejects_rate_limit_or_generic_mismatch(
    status_code: int,
    error_code: str,
) -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            status_code,
            {
                "error": {
                    "code": error_code,
                    "details": _cancel_error_details(status="retryable", retryable=True),
                }
            },
            retry_after=7,
        ),
    ]

    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="typed error"),
    ):
        run_pinvi_canonical_smoke(_production_config())


@pytest.mark.parametrize(
    "details_override",
    [
        {"retryable": True},
        {"status": "failed"},
        {"root": {"kind": "import_job", "id": "11111111-1111-4111-8111-111111111111"}},
        {"unresolved_member_count": -1},
        {"warnings": [1]},
        {"unresolved_member_count": 0},
        {"unresolved_member_count": 0, "members": []},
        {"members": [{"job_id": "not-a-uuid", "result": "cancel_failed"}]},
        {"members": [{"job_id": _CANCEL_PROBE_JOB_ID, "result": "unknown"}]},
        {
            "unresolved_member_count": 2,
            "members": [
                {"job_id": _CANCEL_PROBE_JOB_ID, "result": "cancel_failed"},
                {"job_id": _CANCEL_PROBE_JOB_ID, "result": "pending"},
            ],
        },
        {
            "members": [
                {
                    "job_id": "11111111-1111-4111-8111-111111111111",
                    "result": "cancel_failed",
                }
            ]
        },
        {
            "unresolved_member_count": 1,
            "members": [
                {"job_id": _CANCEL_PROBE_JOB_ID, "result": "cancelled"},
                {
                    "job_id": "11111111-1111-4111-8111-111111111111",
                    "result": "cancel_failed",
                },
            ],
        },
    ],
)
def test_pinvi_cancel_fixture_rejects_noncanonical_details(
    details_override: dict[str, object],
) -> None:
    details = _cancel_error_details(status="in_progress", retryable=False)
    details.update(details_override)
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            409,
            {
                "error": {
                    "code": "PIPELINE_CANCELLATION_IN_PROGRESS",
                    "details": details,
                }
            },
            retry_after=7,
        ),
    ]

    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="C6c PinVi"),
    ):
        run_pinvi_canonical_smoke(_production_config())


def test_pinvi_cancel_fixture_rejects_deep_attempt_member_and_run_drift() -> None:
    invalid_details: list[dict[str, object]] = []
    invalid = _cancel_error_details(status="retryable", retryable=True)
    invalid["requested_at"] = "2026-07-18T00:00:00"
    invalid_details.append(invalid)
    invalid = _cancel_error_details(status="retryable", retryable=True)
    invalid["committed_data_rolled_back"] = True
    invalid_details.append(invalid)
    invalid = _cancel_error_details(status="retryable", retryable=True)
    invalid["dagster_runs"] = [None]
    invalid_details.append(invalid)
    invalid = _cancel_error_details(status="retryable", retryable=True)
    members = invalid["members"]
    assert isinstance(members, list)
    assert isinstance(members[0], dict)
    members[0]["updated_at"] = "2026-07-18T00:00:03"
    invalid_details.append(invalid)
    invalid = _cancel_error_details(status="retryable", retryable=True)
    runs = invalid["dagster_runs"]
    assert isinstance(runs, list)
    assert isinstance(runs[0], dict)
    runs[0]["error"] = {"code": "missing-message"}
    invalid_details.append(invalid)

    for details in invalid_details:
        responses = [
            HttpProbeResponse(200, {"data": {}}, set_cookie=True),
            HttpProbeResponse(200, _pinvi_etl_envelope()),
            HttpProbeResponse(200, _pinvi_provider_envelope()),
            HttpProbeResponse(
                502,
                {
                    "error": {
                        "code": "DAGSTER_TERMINATE_FAILED",
                        "details": details,
                    }
                },
                retry_after=7,
            ),
        ]
        with (
            patch.object(c6c_deployment, "_session_request", side_effect=responses),
            pytest.raises(DeploymentContractError, match="C6c"),
        ):
            run_pinvi_canonical_smoke(_production_config())


def test_pinvi_destructive_cancel_probe_runs_once_and_reuses_evidence() -> None:
    first = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(
            409,
            {
                "error": {
                    "code": "PIPELINE_CANCELLATION_IN_PROGRESS",
                    "details": _cancel_error_details(
                        status="in_progress",
                        retryable=False,
                    ),
                }
            },
            retry_after=7,
        ),
        HttpProbeResponse(204, None, set_cookie=True),
        HttpProbeResponse(401, None),
    ]
    read_only_reverification = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
        HttpProbeResponse(204, None, set_cookie=True),
        HttpProbeResponse(401, None),
    ]
    state = PinviCancelProbeState()
    with patch.object(
        c6c_deployment,
        "_session_request",
        side_effect=[*first, *read_only_reverification],
    ) as request:
        first_result = run_pinvi_canonical_smoke(
            _production_config(),
            cancel_probe_state=state,
        )
        second_result = run_pinvi_canonical_smoke(
            _production_config(),
            cancel_probe_state=state,
        )

    cancel_calls = [
        call
        for call in request.call_args_list
        if call.args[1].endswith(f"/{_CANCEL_PROBE_JOB_ID}/cancel")
    ]
    assert len(cancel_calls) == 1
    assert first_result[3] == second_result[3]


def test_pinvi_uncertain_cancel_probe_is_never_reissued() -> None:
    state = PinviCancelProbeState(attempted=True)
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, _pinvi_provider_envelope()),
    ]
    with (
        patch.object(
            c6c_deployment,
            "_session_request",
            side_effect=responses,
        ) as request,
        pytest.raises(DeploymentContractError, match="cannot be repeated"),
    ):
        run_pinvi_canonical_smoke(
            _production_config(),
            cancel_probe_state=state,
        )

    assert not any(
        call.args[1].endswith(f"/{_CANCEL_PROBE_JOB_ID}/cancel")
        for call in request.call_args_list
    )


def test_map_ui_auth_preflight_requires_login_protected_logout_and_reblock() -> None:
    responses = [
        HttpProbeResponse(200, {"ok": True}, set_cookie=True),
        HttpProbeResponse(200, None),
        HttpProbeResponse(200, {"ok": True}, set_cookie=True),
        HttpProbeResponse(307, None, location="/login?next=%2Fops%2Fproviders"),
    ]
    with patch.object(
        c6c_deployment, "_session_request", side_effect=responses
    ) as request:
        result = run_map_ui_auth_preflight(_production_config())

    assert [item["status"] for item in result] == [200, 200, 200, 307]
    assert request.call_args_list[1].args[1].endswith("/ops/providers")
    assert request.call_args_list[3].args[1].endswith("/ops/providers")


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        (
            [HttpProbeResponse(401, None)],
            "C6c Map UI login smoke failed",
        ),
        (
            [
                HttpProbeResponse(200, {"ok": True}, set_cookie=True),
                HttpProbeResponse(403, None),
            ],
            "C6c Map UI protected-page smoke failed",
        ),
        (
            [
                HttpProbeResponse(200, {"ok": True}, set_cookie=True),
                HttpProbeResponse(200, None),
                HttpProbeResponse(500, None),
            ],
            "C6c Map UI logout smoke failed",
        ),
        (
            [
                HttpProbeResponse(200, {"ok": True}, set_cookie=True),
                HttpProbeResponse(200, None),
                HttpProbeResponse(200, {"ok": True}, set_cookie=True),
                HttpProbeResponse(200, None),
            ],
            "C6c Map UI post-logout protection smoke failed",
        ),
    ],
)
def test_map_ui_auth_preflight_rejects_each_pre_mutation_auth_failure(
    responses: list[HttpProbeResponse],
    message: str,
) -> None:
    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match=message),
    ):
        run_map_ui_auth_preflight(_production_config())


def test_ui_auth_smoke_requires_login_protected_logout_and_pinvi_shell() -> None:
    responses = [
        HttpProbeResponse(200, {"ok": True}, set_cookie=True),
        HttpProbeResponse(200, None),
        HttpProbeResponse(200, {"ok": True}, set_cookie=True),
        HttpProbeResponse(307, None, location="/login?next=%2Fops%2Fproviders"),
        HttpProbeResponse(
            200,
            None,
            body_text=(
                '<html><form data-testid="admin-login-form"></form>'
                '<script src="/_next/static/chunks/app.js"></script></html>'
            ),
            content_type="text/html; charset=utf-8",
        ),
    ]
    with patch.object(
        c6c_deployment, "_session_request", side_effect=responses
    ) as request:
        result = run_ui_auth_smoke(_production_config())

    assert [item["status"] for item in result] == [200, 200, 200, 307, 200]
    assert request.call_args_list[1].args[1].endswith("/ops/providers")
    assert request.call_args_list[3].args[1].endswith("/ops/providers")


def test_pinvi_canonical_smoke_rejects_data_null() -> None:
    responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, {"data": None}),
    ]
    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="etl_summary"),
    ):
        run_pinvi_canonical_smoke(_production_config())

    provider_responses = [
        HttpProbeResponse(200, {"data": {}}, set_cookie=True),
        HttpProbeResponse(200, _pinvi_etl_envelope()),
        HttpProbeResponse(200, {"data": None}),
    ]
    with (
        patch.object(
            c6c_deployment, "_session_request", side_effect=provider_responses
        ),
        pytest.raises(DeploymentContractError, match="provider_sync"),
    ):
        run_pinvi_canonical_smoke(_production_config())


def test_pinvi_etl_nested_dto_is_fail_closed() -> None:
    valid = _pinvi_etl_envelope()
    valid_data = valid["data"]
    assert isinstance(valid_data, dict)
    kor_travel_map = valid_data["kor_travel_map"]
    assert isinstance(kor_travel_map, dict)
    kor_travel_map["run_counts"] = {"STARTED": 1}
    kor_travel_map["operations_by_status"] = {"running": 1}
    kor_travel_map["recent_import_jobs"] = [_pinvi_import_job()]
    assert c6c_deployment._validate_pinvi_etl_summary(valid) is True

    invalid_payloads: list[dict[str, object]] = []
    invalid = deepcopy(valid)
    invalid["data"]["kor_travel_map"]["run_counts"] = {"STARTED": True}  # type: ignore[index]
    invalid_payloads.append(invalid)
    invalid = deepcopy(valid)
    invalid["data"]["kor_travel_map"]["operations_by_status"] = {"unknown": 1}  # type: ignore[index]
    invalid_payloads.append(invalid)
    for field, value in (
        ("status", "unknown"),
        ("projected_job_progress", 101),
        ("created_at", "2026-07-18T00:00:00"),
    ):
        invalid = deepcopy(valid)
        invalid["data"]["kor_travel_map"]["recent_import_jobs"][0][field] = value  # type: ignore[index]
        invalid_payloads.append(invalid)

    assert all(
        c6c_deployment._validate_pinvi_etl_summary(payload) is False
        for payload in invalid_payloads
    )


def test_pinvi_etl_definition_arrays_validate_actual_typed_items() -> None:
    valid = _pinvi_etl_envelope()
    data = valid["data"]
    assert isinstance(data, dict)
    pinvi = data["pinvi"]
    assert isinstance(pinvi, dict)
    pinvi["checked_at"] = "2026-07-18T00:00:00Z"
    pinvi["repositories"] = [_pinvi_repository()]
    pinvi.update(_pinvi_etl_definition_items())
    assert c6c_deployment._validate_pinvi_etl_summary(valid) is True

    for field in ("repositories", "assets", "schedules", "sensors"):
        invalid = deepcopy(valid)
        invalid["data"]["pinvi"][field] = [None]  # type: ignore[index]
        assert c6c_deployment._validate_pinvi_etl_summary(invalid) is False

    invalid = deepcopy(valid)
    invalid["data"]["pinvi"]["repositories"][0]["location_name"] = 7  # type: ignore[index]
    assert c6c_deployment._validate_pinvi_etl_summary(invalid) is False
    invalid = deepcopy(valid)
    invalid["data"]["pinvi"]["assets"][0]["group_name"] = 7  # type: ignore[index]
    assert c6c_deployment._validate_pinvi_etl_summary(invalid) is False
    invalid = deepcopy(valid)
    invalid["data"]["pinvi"]["schedules"][0]["status"] = None  # type: ignore[index]
    assert c6c_deployment._validate_pinvi_etl_summary(invalid) is False
    invalid = deepcopy(valid)
    invalid["data"]["pinvi"]["sensors"][0]["status"] = None  # type: ignore[index]
    assert c6c_deployment._validate_pinvi_etl_summary(invalid) is False
    invalid = deepcopy(valid)
    invalid["data"]["pinvi"]["checked_at"] = "2026-07-18T00:00:00"  # type: ignore[index]
    assert c6c_deployment._validate_pinvi_etl_summary(invalid) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sync_scope", "daily"),
        ("sync_scope", "external_system: "),
        ("status", "healthy"),
    ],
)
def test_pinvi_provider_sync_requires_canonical_scope_and_status(
    field: str,
    value: str,
) -> None:
    payload = _pinvi_provider_envelope()
    data = payload["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert isinstance(items[0], dict)
    items[0][field] = value

    assert c6c_deployment._validate_pinvi_provider_sync(payload) is False


def test_ui_auth_smoke_rejects_fallback_html_200() -> None:
    responses = [
        HttpProbeResponse(200, {"ok": True}, set_cookie=True),
        HttpProbeResponse(200, None),
        HttpProbeResponse(200, {"ok": True}, set_cookie=True),
        HttpProbeResponse(307, None, location="/login"),
        HttpProbeResponse(
            200,
            None,
            body_text='<html><script src="/_next/static/fallback.js"></script></html>',
            content_type="text/html",
        ),
    ]
    with (
        patch.object(c6c_deployment, "_session_request", side_effect=responses),
        pytest.raises(DeploymentContractError, match="login shell"),
    ):
        run_ui_auth_smoke(_production_config())


def _success(args: list[str]) -> dict[str, object]:
    return {
        "success": True,
        "returncode": 0,
        "command": ["docker", "compose", *args],
        "stdout": "",
        "stderr": "",
    }


def test_production_generic_mutation_guard_rejects_every_api_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_production_guard_environment(monkeypatch)
    transaction = _production_guard_transaction(tmp_path)
    environment = transaction.environment
    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.docker_service._capture_compose_environment_snapshot",
        Mock(return_value=environment),
    )
    subprocess_run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["up", "-d", "kor-travel-map-api"])
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["stop", "pinvi-api"], mutation_capability=object())
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["build", "kor-travel-map-api"])
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["exec", "pinvi-api", "sh"])
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["cp", "local.txt", "pinvi-api:/tmp/remote.txt"])
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["up", "-d", "pinvi-web"])
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(["up", "-d", "--scale", "pinvi-api=0", "rustfs"])
    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().ensure_target("map")
    subprocess_run.assert_not_called()

    service = DockerService()
    for mutation in (
        lambda: service.control_container("kor-travel-map-api", "restart"),
        lambda: service.update_container_config("pinvi-api", [], {}, [], []),
        lambda: service.reset_container_config("kor-travel-map-api"),
    ):
        with pytest.raises(DeploymentContractError, match="compatible-pair"):
            mutation()


def test_non_api_generic_mutation_is_not_blocked_by_c6c_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "production")
    assert_c6c_mutation_allowed(["rustfs"], env_path="/missing/.env")


@pytest.mark.parametrize(
    "args",
    [
        ["up", "-d", "rustfs"],
        ["up", "-d", "--force-recreate", "rustfs"],
        ["create", "rustfs"],
    ],
)
def test_generic_compose_mutation_rejects_candidate_before_subprocess(
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    candidate_error = ComposeCandidateContractError("candidate rejected")
    validate = Mock(side_effect=candidate_error)
    subprocess_run = Mock()
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "compose.lock"))
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        validate,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    with pytest.raises(ComposeCandidateContractError) as caught:
        service.run(args)

    assert caught.value is candidate_error
    validate.assert_called_once()
    environment_snapshot = validate.call_args.kwargs["environment_snapshot"]
    assert isinstance(environment_snapshot, ComposeEnvironmentSnapshot)
    subprocess_run.assert_not_called()


def test_generic_ensure_rejects_candidate_before_any_target_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    candidate_error = ComposeCandidateContractError("candidate rejected")
    ensure = Mock()
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "ensure.lock"))
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        Mock(side_effect=candidate_error),
    )
    monkeypatch.setattr(service, "_ensure_target_unlocked", ensure)

    with pytest.raises(ComposeCandidateContractError) as caught:
        service.ensure_target("storage")

    assert caught.value is candidate_error
    ensure.assert_not_called()


def test_ensure_target_recovers_persisted_runtime_after_second_preflight_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = ComposeService()
    compose_path = tmp_path / "docker-compose.yml"
    original = (
        b"services:\n"
        b"  kor-travel-geo-postgres:\n"
        b"    volumes:\n"
        b"    - /srv/pgdata:/var/lib/postgresql/data\n"
        b"  rustfs: {}\n"
    )
    compose_path.write_bytes(original)
    compose_path.chmod(0o640)
    raw_volume_graph_hash = c6c_deployment.compose_volume_graph_hash(
        yaml.safe_load(original.decode("utf-8"))
    )
    resolved = {
        "services": {
            "kor-travel-geo-postgres": {},
            "rustfs": {},
        }
    }
    resolved_volume_graph_hash = c6c_deployment.compose_volume_graph_hash(resolved)
    validation = ValidatedComposeCandidate(
        resolved=resolved,
        system_bind_snapshots=(),
        raw_volume_graph_hash=raw_volume_graph_hash,
        resolved_volume_graph_hash=resolved_volume_graph_hash,
    )
    original_error = ComposeCandidateContractError(
        "compose resolved volume graph changed during the request"
    )
    run_calls = 0

    def run(args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal run_calls
        run_calls += 1
        if run_calls == 1:
            compose_path.write_text(
                "services:\n  kor-travel-geo-postgres: {}\n  rustfs: {}\n",
                encoding="utf-8",
            )
            return _success(list(args))
        if run_calls == 2:
            raise original_error
        assert compose_path.read_bytes() == original
        assert compose_path.stat().st_mode & 0o777 == 0o640
        assert list(args) == [
            "up",
            "-d",
            "--force-recreate",
            "kor-travel-geo-postgres",
            "rustfs",
        ]
        recovery_transaction = kwargs["transaction"]
        assert recovery_transaction.system_bind_snapshots == ()
        assert recovery_transaction.raw_volume_graph_hash == raw_volume_graph_hash
        assert (
            recovery_transaction.resolved_volume_graph_hash
            == resolved_volume_graph_hash
        )
        assert recovery_transaction.environment is environment_snapshots[0]
        return _success(list(args))

    validate_calls = 0
    environment_snapshots: list[ComposeEnvironmentSnapshot] = []

    def validate(
        *,
        environment_snapshot: ComposeEnvironmentSnapshot,
        **_kwargs: object,
    ) -> ValidatedComposeCandidate:
        nonlocal validate_calls
        validate_calls += 1
        environment_snapshots.append(environment_snapshot)
        return validation

    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_manager_mutation_allowed",
        Mock(return_value="local"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_c6c_mutation_allowed",
        Mock(),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_c6c_deployment_lock_path",
        lambda: str(tmp_path / "ensure.lock"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        validate,
    )
    monkeypatch.setattr(service, "run", run)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.ensure_target("storage")

    assert caught.value.original_error is original_error
    assert caught.value.recovery_attempted is True
    assert caught.value.recovery_succeeded is True, caught.value.restoration.get(
        "error"
    )
    assert caught.value.recovery_error is None
    assert caught.value.restoration is not None
    assert caught.value.restoration["success"] is True
    assert caught.value.restoration["config_restored"] is True
    assert caught.value.restoration["contract_revalidated"] is True
    assert caught.value.restoration["runtime_recovery_attempted"] is True
    assert caught.value.restoration["baseline"] == {
        "raw_volume_graph_hash": raw_volume_graph_hash,
        "resolved_volume_graph_hash": resolved_volume_graph_hash,
        "system_bind_snapshots": 0,
    }
    assert validate_calls == 1
    assert run_calls == 3


def test_ensure_recovery_validation_failure_skips_docker_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    original = b"services:\n  rustfs:\n    volumes: []\n"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    service = ComposeService()
    run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(service, "run", run)
    environment_snapshot = _capture_compose_environment_snapshot(
        environment_override={},
    )
    resolved = {"services": {"rustfs": {"volumes": []}}}
    resolved_volume_graph_hash = c6c_deployment.compose_volume_graph_hash(resolved)
    external_inputs = ComposeExternalInputSnapshot(references=(), files=())
    transaction = ComposeTransactionSnapshot(
        environment=environment_snapshot,
        external_inputs=external_inputs,
        compose_source_bytes=original,
        compose_source_mode=0o640,
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash=resolved_volume_graph_hash,
        resolved=resolved,
        resolved_document_hash=_resolved_compose_document_hash(resolved),
    )

    recovery = service._recover_persisted_target_runtime(
        ["rustfs"],
        capture_output=True,
        original_compose_bytes=original,
        original_compose_mode=0o640,
        expected_system_bind_snapshots=(),
        expected_raw_volume_graph_hash="raw-stable",
        expected_resolved_volume_graph_hash=resolved_volume_graph_hash,
        expected_environment_snapshot=environment_snapshot,
        expected_external_input_snapshot=external_inputs,
        transaction=transaction,
    )

    assert recovery["success"] is False
    assert recovery["config_restored"] is True
    assert recovery["contract_revalidated"] is False
    assert recovery["runtime_recovery_attempted"] is False
    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == 0o640
    run.assert_not_called()


def test_production_low_level_compose_mutation_requires_managed_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_production_guard_environment(monkeypatch)
    transaction = _production_guard_transaction(tmp_path)
    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )

    with pytest.raises(DeploymentContractError, match="managed workflow"):
        ComposeService().run(["up", "-d", "rustfs"])

    run.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"PINVI_ENVIRONMENT": ""},
        {"PINVI_ENVIRONMENT": "local"},
        {"KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false"},
        {"KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": ""},
        {"KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "short"},
    ],
)
def test_generic_api_mutation_guard_validates_common_production_contract(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
) -> None:
    _set_production_guard_environment(monkeypatch)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(DeploymentContractError):
        assert_c6c_mutation_allowed(
            ["kor-travel-map-api"],
            env_path="/missing/.env",
            capability=c6c_deployment._COMPATIBLE_PAIR_MUTATION_CAPABILITY,
        )


def test_generic_api_mutation_guard_allows_only_exact_local_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_READ_TOKEN", "")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN", "")

    assert_c6c_mutation_allowed(["pinvi-api"], env_path="/missing/.env")

    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "true")
    with pytest.raises(DeploymentContractError):
        assert_c6c_mutation_allowed(["pinvi-api"], env_path="/missing/.env")


@pytest.mark.parametrize(
    "args",
    [
        ["scale", "pinvi-api=0"],
        ["watch", "pinvi-web"],
        ["--profile", "prod", "watch", "kor-travel-map-ui"],
        ["up", "--wait-timeout", "120", "pinvi-api"],
        ["exec", "--index", "1", "pinvi-api", "sh"],
        ["run", "--name", "temporary", "kor-travel-map-api", "sh"],
        ["wait", "--down-project"],
        ["wait", "--down-project=true"],
        ["wait", "--down-project=true", "rustfs"],
        ["unknown-future-command"],
    ],
)
def test_production_compose_classifier_is_default_deny_for_api_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    _set_production_guard_environment(monkeypatch)
    transaction = _production_guard_transaction(tmp_path)
    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )

    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(args)

    run.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["kill", "-s", "SIGKILL"],
        ["kill", "--signal", "SIGTERM"],
        ["kill", "--future-option", "value", "rustfs"],
        ["kill", "--signal"],
        ["unknown-future-command", "rustfs"],
        ["up", "--future-option=value", "rustfs"],
    ],
)
def test_compose_mutation_parser_defaults_ambiguous_scope_to_both_apis(
    args: list[str],
) -> None:
    assert ComposeService._compose_mutation_identifiers(args) == [
        "kor-travel-map-api",
        "pinvi-api",
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["kill", "-s", "SIGKILL", "rustfs"],
        ["kill", "--signal", "SIGTERM", "rustfs"],
        ["kill", "--signal=SIGQUIT", "rustfs"],
    ],
)
def test_compose_kill_signal_option_consumes_value_before_service(
    args: list[str],
) -> None:
    assert ComposeService._compose_mutation_identifiers(args) == ["rustfs"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["build", "--pull", "kor-travel-map-api"], ["kor-travel-map-api"]),
        (["rm", "-s", "pinvi-api"], ["pinvi-api"]),
        (["rm", "--force", "--stop", "kor-travel-map-api"], ["kor-travel-map-api"]),
        (["run", "--rm", "kor-travel-map-api"], ["kor-travel-map-api"]),
    ],
)
def test_compose_command_specific_flags_do_not_consume_service_scope(
    args: list[str],
    expected: list[str],
) -> None:
    assert ComposeService._compose_mutation_identifiers(args) == expected


@pytest.mark.parametrize(
    "args",
    [
        ["run", "--rm", "rustfs-init"],
        ["rm", "--force", "--stop", "temporary-service"],
        ["build", "--pull", "rustfs"],
    ],
)
def test_production_managed_compose_flags_reach_locked_execution(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    _set_production_guard_environment(monkeypatch)
    lock = Mock(return_value=nullcontext())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        lock,
    )
    completed = Mock(returncode=0, stdout="", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )

    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        Mock(
            return_value=ValidatedComposeCandidate(
                resolved={}, system_bind_snapshots=()
            )
        ),
    )
    result = service.run(
        args,
        mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
    )

    assert result["success"] is True
    lock.assert_called_once()
    run.assert_called_once()


def test_direct_compose_mutation_rejects_source_change_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_production_guard_environment(monkeypatch)
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        run,
    )
    service = ComposeService()

    def replace_source(**_kwargs: object) -> ValidatedComposeCandidate:
        compose_path.write_text("services:\n  attacker: {}\n", encoding="utf-8")
        return ValidatedComposeCandidate(resolved={}, system_bind_snapshots=())

    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        replace_source,
    )

    with pytest.raises(ComposeCandidateContractError, match="source changed"):
        service.run(
            ["run", "--rm", "rustfs-init"],
            mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
        )

    run.assert_not_called()


def test_mutation_rejects_override_created_after_validation_without_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    override_path = tmp_path / "docker-compose.override.yml"
    service = ComposeService()
    subprocess_run = Mock()
    commands: list[list[str]] = []
    original_build_command = service.build_command

    def validate(**_kwargs: object) -> ValidatedComposeCandidate:
        override_path.write_text("services:\n  attacker: {}\n", encoding="utf-8")
        return ValidatedComposeCandidate(
            resolved={},
            system_bind_snapshots=(),
            raw_volume_graph_hash="raw-stable",
            resolved_volume_graph_hash="resolved-stable",
        )

    def build_command(
        args: list[str],
        *,
        canonical_single_file: bool = False,
        compose_path: str | None = None,
    ) -> list[str]:
        command = original_build_command(
            args,
            canonical_single_file=canonical_single_file,
            compose_path=compose_path,
        )
        commands.append(command)
        return command

    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(override_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_c6c_deployment_lock_path",
        lambda: str(tmp_path / "compose.lock"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        validate,
    )
    monkeypatch.setattr(service, "build_command", build_command)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    with pytest.raises(ComposeCandidateContractError, match="override file appeared"):
        service.run(
            ["up", "-d", "rustfs"],
            mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
        )

    assert commands == [
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(tmp_path),
            "-f",
            "-",
            "up",
            "-d",
            "rustfs",
        ]
    ]
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("env_file_exists", [True, False])
def test_mutation_rejects_env_file_change_after_validation_without_subprocess(
    env_file_exists: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    if env_file_exists:
        env_path.write_text("PASS16_SAFE_MARKER=initial\n", encoding="utf-8")
    service = ComposeService()
    subprocess_run = Mock()

    def validate(**_kwargs: object) -> ValidatedComposeCandidate:
        env_path.write_text("PASS16_SAFE_MARKER=changed\n", encoding="utf-8")
        return ValidatedComposeCandidate(
            resolved={},
            system_bind_snapshots=(),
            raw_volume_graph_hash="raw-stable",
            resolved_volume_graph_hash="resolved-stable",
        )

    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(env_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_c6c_deployment_lock_path",
        lambda: str(tmp_path / "compose.lock"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        validate,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    with pytest.raises(ComposeCandidateContractError, match="env-file"):
        service.run(
            ["up", "-d", "rustfs"],
            mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
        )

    subprocess_run.assert_not_called()


def test_mutation_uses_dev_null_and_frozen_effective_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("PASS16_SAFE_MARKER=frozen\n", encoding="utf-8")
    service = ComposeService()
    completed = Mock(returncode=0, stdout="", stderr="")
    subprocess_run = Mock(return_value=completed)
    monkeypatch.delenv("PASS16_SAFE_MARKER", raising=False)

    def validate(
        **_kwargs: object,
    ) -> ValidatedComposeCandidate:
        monkeypatch.setenv("PASS16_SAFE_MARKER", "live-after-snapshot")
        return ValidatedComposeCandidate(
            resolved={},
            system_bind_snapshots=(),
            raw_volume_graph_hash="raw-stable",
            resolved_volume_graph_hash="resolved-stable",
        )

    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(env_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_c6c_deployment_lock_path",
        lambda: str(tmp_path / "compose.lock"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        validate,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    result = service.run(
        ["up", "-d", "rustfs"],
        mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
    )

    assert result["success"] is True
    command = subprocess_run.call_args.args[0]
    assert command[:8] == [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(tmp_path),
        "-f",
        "-",
    ]
    process_environment = subprocess_run.call_args.kwargs["env"]
    assert process_environment["PASS16_SAFE_MARKER"] == "frozen"
    assert os.environ["PASS16_SAFE_MARKER"] == "live-after-snapshot"


def test_mutation_consumes_resolved_stdin_with_original_project_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    source = b"services:\n  rustfs: {}\n"
    compose_path.write_bytes(source)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    environment_snapshot = _capture_compose_environment_snapshot(
        environment_override=None
    )
    external_snapshot = ComposeExternalInputSnapshot(references=(), files=())
    resolved = {
        "services": {
            "rustfs": {
                "build": {"context": str(tmp_path / "rustfs-build")},
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(tmp_path / "rustfs-data"),
                        "target": "/data",
                    }
                ],
            }
        }
    }
    transaction = ComposeTransactionSnapshot(
        environment=environment_snapshot,
        external_inputs=external_snapshot,
        compose_source_bytes=source,
        compose_source_mode=compose_path.stat().st_mode & 0o777,
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash="resolved-stable",
    )
    validation = ValidatedComposeCandidate(
        resolved=resolved,
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash="resolved-stable",
        environment_snapshot=environment_snapshot,
        external_input_snapshot=external_snapshot,
        transaction_snapshot=transaction,
    )
    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        Mock(return_value=validation),
    )
    completed = Mock(returncode=0, stdout="", stderr="")
    subprocess_run = Mock(return_value=completed)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    service.run(
        ["up", "-d", "rustfs"],
        mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
        transaction=transaction,
    )

    command = subprocess_run.call_args.args[0]
    assert command[:9] == [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(tmp_path),
        "-f",
        "-",
        "up",
    ]
    materialized = json.loads(subprocess_run.call_args.kwargs["input"])
    rustfs = materialized["services"]["rustfs"]
    assert rustfs["build"]["context"] == str(tmp_path / "rustfs-build")
    assert rustfs["volumes"][0]["source"] == str(tmp_path / "rustfs-data")


@pytest.mark.parametrize("external_exists", [True, False])
def test_external_env_file_drift_after_validation_skips_mutation_subprocess(
    external_exists: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    external_path = tmp_path / "worker.env"
    candidate = {
        "services": {
            "worker": {
                "env_file": [
                    {"path": "worker.env", "required": False, "format": "raw"}
                ]
            }
        }
    }
    compose_path.write_text(yaml.safe_dump(candidate), encoding="utf-8")
    if external_exists:
        external_path.write_text("PASS17_SAFE_MARKER=initial\n", encoding="utf-8")
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_compose_path",
        lambda: str(compose_path),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_env_path",
        lambda: str(tmp_path / ".env"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    environment_snapshot = _capture_compose_environment_snapshot(
        environment_override=None
    )
    external_snapshot = _capture_compose_external_input_snapshot(
        candidate,
        environment_snapshot=environment_snapshot,
    )
    source = compose_path.read_bytes()
    transaction = ComposeTransactionSnapshot(
        environment=environment_snapshot,
        external_inputs=external_snapshot,
        compose_source_bytes=source,
        compose_source_mode=compose_path.stat().st_mode & 0o777,
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash="resolved-stable",
    )
    validation = ValidatedComposeCandidate(
        resolved={"services": {"worker": {}}},
        system_bind_snapshots=(),
        raw_volume_graph_hash="raw-stable",
        resolved_volume_graph_hash="resolved-stable",
        environment_snapshot=environment_snapshot,
        external_input_snapshot=external_snapshot,
        transaction_snapshot=transaction,
    )
    service = ComposeService()

    def validate(**_kwargs: object) -> ValidatedComposeCandidate:
        external_path.write_text("PASS17_SAFE_MARKER=changed\n", encoding="utf-8")
        return validation

    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        validate,
    )
    subprocess_run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )

    with pytest.raises(ComposeCandidateContractError, match="external env_file"):
        service.run(
            ["up", "-d", "worker"],
            mutation_capability=c6c_deployment._MANAGED_COMPOSE_MUTATION_CAPABILITY,
            transaction=transaction,
        )

    subprocess_run.assert_not_called()


def test_pair_drift_recovery_halts_from_same_frozen_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = _frozen_external_transaction(tmp_path)
    Path(transaction.environment.env_path).write_text(
        "SAFE=drifted\n", encoding="utf-8"
    )
    Path(transaction.external_inputs.files[0].path).write_text(
        "WORKER_SAFE=drifted\n", encoding="utf-8"
    )
    Path(transaction.environment.compose_path).write_text(
        "services: {}\n", encoding="utf-8"
    )
    service = ComposeService()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_validate_resolved_compose_contract",
        Mock(return_value=transaction.resolved),
    )
    original_error = ComposeCandidateContractError(
        "external env_file changed before the next forward stage"
    )
    monkeypatch.setattr(
        service,
        "_activate_pair_sequentially",
        Mock(side_effect=original_error),
    )
    subprocess_run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )
    result: dict[str, object] = {
        "success": False,
        "returncode": 1,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
        "deployment_state": "failed",
    }

    recovery = service._recover_previous_pair(
        result,
        _production_config(),
        _manifest().active,
        ["kor-travel-map-api", "pinvi-api"],
        transaction=transaction,
    )

    assert recovery["success"] is False
    assert recovery["error"] == str(original_error)
    assert recovery["state"] == "halted_requires_operator"
    command = subprocess_run.call_args.args[0]
    assert command[2:8] == [
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(tmp_path),
        "-f",
        "-",
    ]
    assert json.loads(subprocess_run.call_args.kwargs["input"]) == transaction.resolved


def test_active_recovery_transaction_freezes_manifest_image_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "services": {
            "kor-travel-map-api": {"image": "${KOR_TRAVEL_MAP_API_IMAGE}"},
            "pinvi-api": {"image": "${PINVI_API_IMAGE}"},
            "kor-travel-map-ui": {
                "environment": {
                    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": (
                        "${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:?"
                        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME must be explicitly set}"
                    ),
                    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": (
                        "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
                        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH must be explicitly set}"
                    ),
                    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": (
                        "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?"
                        "KOR_TRAVEL_MAP_UI_SESSION_SECRET must be explicitly set}"
                    ),
                }
            },
        }
    }
    root_resolved = {
        "services": {
            "kor-travel-map-api": {"image": _ACTIVE_MAP_IMAGE_ID},
            "pinvi-api": {"image": _ACTIVE_PINVI_IMAGE_ID},
            "kor-travel-map-ui": {
                "environment": {
                    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
                    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
                    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
                }
            },
        }
    }
    source_bytes = yaml.safe_dump(source, sort_keys=False).encode("utf-8")
    environment = ComposeEnvironmentSnapshot(
        effective={
            "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
            "KTDM_C6C_MAP_UI_ADMIN_PASSWORD": _MAP_UI_PASSWORD,
        },
        env_path=str(tmp_path / ".env"),
        compose_path=str(tmp_path / "docker-compose.yml"),
        override_path=str(tmp_path / "missing.override.yml"),
        env_file_identity=ComposeEnvFileIdentity(exists=True),
        env_file_bytes=b"frozen",
    )
    root_transaction = ComposeTransactionSnapshot(
        environment=environment,
        external_inputs=ComposeExternalInputSnapshot(references=(), files=()),
        compose_source_bytes=source_bytes,
        compose_source_mode=0o640,
        system_bind_snapshots=(),
        raw_volume_graph_hash=c6c_deployment.compose_volume_graph_hash(source),
        resolved_volume_graph_hash=(
            c6c_deployment.compose_volume_graph_hash(root_resolved)
        ),
        resolved=root_resolved,
        resolved_document_hash=_resolved_compose_document_hash(root_resolved),
    )
    active = _manifest().active
    resolved_active = {
        "services": {
            "kor-travel-map-api": {"image": active.map_image_id},
            "pinvi-api": {"image": active.pinvi_image_id},
            "kor-travel-map-ui": {
                "environment": {
                    "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
                    "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
                    "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
                }
            },
        }
    }
    subprocess_run = Mock(
        return_value=Mock(
            returncode=0,
            stdout=json.dumps(resolved_active),
            stderr="",
        )
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        subprocess_run,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.validate_resolved_compose_image_pair",
        Mock(),
    )
    monkeypatch.setenv("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", "attacker")
    monkeypatch.setenv(
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
        "pbkdf2_sha256$100000$other-salt$other-digest",
    )
    monkeypatch.setenv(
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
        "other-map-ui-session-secret-placeholder",
    )

    recovery_transaction = (
        ComposeService()._materialize_active_recovery_transaction_unlocked(
            root_transaction,
            _production_config(),
            active,
        )
    )

    assert recovery_transaction is not root_transaction
    assert recovery_transaction.environment is root_transaction.environment
    assert recovery_transaction.external_inputs is root_transaction.external_inputs
    assert recovery_transaction.compose_source_bytes is root_transaction.compose_source_bytes
    assert recovery_transaction.system_bind_snapshots is root_transaction.system_bind_snapshots
    assert recovery_transaction.resolved["services"]["kor-travel-map-api"]["image"] == (
        active.map_image_id
    )
    assert recovery_transaction.resolved["services"]["pinvi-api"]["image"] == (
        active.pinvi_image_id
    )
    assert subprocess_run.call_args.kwargs["env"]["KOR_TRAVEL_MAP_API_IMAGE"] == (
        active.map_image_id
    )
    assert subprocess_run.call_args.kwargs["env"]["PINVI_API_IMAGE"] == (
        active.pinvi_image_id
    )
    assert subprocess_run.call_args.kwargs["env"][
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"
    ] == _MAP_UI_USERNAME
    assert subprocess_run.call_args.kwargs["env"][
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"
    ] == _MAP_UI_PASSWORD_HASH
    assert subprocess_run.call_args.kwargs["env"][
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET"
    ] == _MAP_UI_SESSION_SECRET
    resolved_map_ui = recovery_transaction.resolved["services"][
        "kor-travel-map-ui"
    ]
    assert resolved_map_ui["environment"] == {
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": _MAP_UI_USERNAME,
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": _MAP_UI_PASSWORD_HASH,
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": _MAP_UI_SESSION_SECRET,
    }
    serialized_source = subprocess_run.call_args.kwargs["input"]
    serialized_resolved = json.dumps(recovery_transaction.resolved)
    assert _MAP_UI_PASSWORD not in serialized_source
    assert _MAP_UI_PASSWORD not in serialized_resolved


def test_deploy_map_contract_error_after_quiesce_uses_active_recovery_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = Mock(spec=ComposeTransactionSnapshot)
    active_recovery_transaction = Mock(spec=ComposeTransactionSnapshot)
    manifest = _manifest()
    monkeypatch.setattr(
        service,
        "_production_preflight",
        Mock(return_value=manifest),
    )
    materialize = Mock(return_value=active_recovery_transaction)
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        materialize,
    )
    monkeypatch.setattr(service, "_require_services_ready", Mock())
    monkeypatch.setattr(
        service,
        "_preflight_current_map_ui_auth",
        Mock(return_value=[{"name": "map_ui_login", "status": 200}]),
    )
    monkeypatch.setattr(
        service,
        "run",
        Mock(return_value=_success(["stop", "pinvi-api"])),
    )
    original_error = ComposeCandidateContractError(
        "compose external env_file bytes changed during the transaction"
    )
    monkeypatch.setattr(service, "_run_up_stage", Mock(side_effect=original_error))
    recover = Mock(return_value={"success": True, "state": "restored"})
    monkeypatch.setattr(service, "_recover_previous_pair", recover)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service._ensure_production_pinvi_target(
            "pinvi",
            config=_production_config(),
            build=False,
            recreate=True,
            capture_output=True,
            transaction=transaction,
        )

    assert caught.value.original_error is original_error
    assert caught.value.recovery_succeeded is True
    assert materialize.call_args.args == (
        transaction,
        _production_config(),
        manifest.active,
    )
    assert recover.call_args.kwargs["transaction"] is active_recovery_transaction


def test_capture_base_contract_exception_uses_same_root_and_preserves_halt_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(tmp_path / "compatible-pair-v2.json"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.get_c6c_deployment_lock_path",
        lambda: str(tmp_path / "global-mutation.lock"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_manager_mutation_allowed",
        Mock(return_value="production"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        Mock(return_value=_production_config()),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.assert_pair_manifest_bootstrap_allowed",
        Mock(),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock())),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", Mock())
    monkeypatch.setattr(service, "_snapshot_service_states", Mock(return_value={}))
    original_error = ComposeCandidateContractError(
        "compose external env_file identity changed during the transaction"
    )
    monkeypatch.setattr(service, "_run_up_stage", Mock(side_effect=original_error))
    cleanup = Mock(
        return_value={
            "success": False,
            "state": "halt_failed_requires_operator",
            "error": "halt failed",
        }
    )
    monkeypatch.setattr(service, "_cleanup_bootstrap", cleanup)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.capture_compatible_pinvi_pair(
            verified_compatible=True,
            build=False,
        )

    assert caught.value.original_error is original_error
    assert caught.value.recovery_succeeded is False
    assert caught.value.restoration == {
        "success": False,
        "state": "halt_failed_requires_operator",
        "error": "halt failed",
    }
    assert cleanup.call_args.kwargs["transaction"] is transaction


@pytest.mark.parametrize(
    "args",
    [
        ["config", "-o", "resolved.yml"],
        ["config", "-oresolved.yml"],
        ["config", "--output", "resolved.yml"],
        ["config", "--output=resolved.yml"],
        ["config", "-o"],
        ["config", "--output"],
        ["config", "--format"],
        ["config", "--future-read-option"],
    ],
)
def test_production_compose_config_output_or_ambiguity_is_default_deny(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    _set_production_guard_environment(monkeypatch)
    transaction = _production_guard_transaction(tmp_path)
    monkeypatch.setattr(
        ComposeService,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    run = Mock()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )

    with pytest.raises(DeploymentContractError, match="compatible-pair"):
        ComposeService().run(args)

    run.assert_not_called()


def test_production_compose_config_output_uses_capability_and_host_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_production_guard_environment(monkeypatch)
    lock = Mock(return_value=nullcontext())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        lock,
    )
    completed = Mock(returncode=0, stdout="", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )

    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        Mock(
            return_value=ValidatedComposeCandidate(
                resolved={}, system_bind_snapshots=()
            )
        ),
    )
    result = service.run(
        ["config", "--output=resolved.yml"],
        mutation_capability=c6c_deployment._COMPATIBLE_PAIR_MUTATION_CAPABILITY,
    )

    assert result["success"] is True
    lock.assert_called_once()
    run.assert_called_once()


@pytest.mark.parametrize(
    "args",
    [
        ["config", "--format", "json"],
        ["config", "--format=json"],
        ["config", "--services"],
        ["ps", "--format", "json", "pinvi-api"],
        ["logs", "--tail", "10", "kor-travel-map-api"],
        ["wait", "pinvi-api"],
    ],
)
def test_production_compose_classifier_allows_known_read_only_commands(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "production")
    completed = Mock(returncode=0, stdout="", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )

    result = ComposeService().run(args)

    assert result["success"] is True
    run.assert_called_once()


def test_wait_down_project_with_explicit_service_still_uses_host_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_READ_TOKEN", "")
    monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN", "")
    lock = Mock(return_value=nullcontext())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        lock,
    )
    completed = Mock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run",
        Mock(return_value=completed),
    )

    service = ComposeService()
    monkeypatch.setattr(
        service,
        "_validate_current_compose_candidate_unlocked",
        Mock(
            return_value=ValidatedComposeCandidate(
                resolved={}, system_bind_snapshots=()
            )
        ),
    )
    result = service.run(["wait", "--down-project=true", "rustfs"])

    assert result["success"] is True
    lock.assert_called_once()


def test_c6c_deployment_lock_rejects_concurrent_operation(tmp_path: Path) -> None:
    lock_path = tmp_path / "c6c.lock"
    with c6c_deployment_lock(str(lock_path)):
        with c6c_deployment_lock(str(lock_path)):
            pass
        with pytest.raises(DeploymentContractError, match="already active"):
            Context().run(
                lambda: c6c_deployment_lock(str(lock_path)).__enter__()
            )


def test_dedicated_pair_deploy_is_the_only_production_ensure_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "deploy.lock"))
    service = ComposeService()
    transaction = _frozen_external_transaction(tmp_path)
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    deploy = Mock(return_value={"success": True, "returncode": 0})
    monkeypatch.setattr(service, "_ensure_production_pinvi_target", deploy)

    result = service.deploy_compatible_pinvi_pair(build=True, recreate=True)

    assert result["success"] is True
    deploy.assert_called_once()


def test_current_map_ui_preflight_orders_inspect_validation_and_auth_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    config = _production_config()
    runtime = _runtime_secret_configs(config)[config.map_ui_container]
    events: list[str] = []
    monkeypatch.setattr(
        service,
        "_inspect_container_runtime_config",
        lambda container: events.append(f"inspect:{container}") or runtime,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.validate_current_map_ui_auth_runtime",
        lambda actual, expected: events.append("validate")
        if actual is runtime and expected is config
        else None,
    )
    smoke = [{"name": "map_ui_login", "status": 200}]
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ui_auth_preflight",
        lambda expected: events.append("smoke") or smoke
        if expected is config
        else [],
    )

    result = service._preflight_current_map_ui_auth(config)

    assert result is smoke
    assert events == [f"inspect:{config.map_ui_container}", "validate", "smoke"]


@pytest.mark.parametrize("operation", ["deploy", "rollback"])
@pytest.mark.parametrize(
    "message",
    [
        "C6c Map UI login smoke failed",
        "C6c Map UI protected-page smoke failed",
        "C6c Map UI logout smoke failed",
        "C6c Map UI post-logout protection smoke failed",
    ],
)
def test_map_ui_preflight_failure_keeps_docker_mutation_at_zero(
    operation: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    manifest_path = tmp_path / "pair.json"
    manifest = _manifest()
    write_pair_manifest(str(manifest_path), manifest)
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    service = ComposeService()
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        service,
        "_production_preflight",
        lambda _config, **_kwargs: manifest,
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=transaction),
    )
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(
        service,
        "_validate_resolved_compose_contract",
        lambda *_args, **_kwargs: _resolved_compose(),
    )
    monkeypatch.setattr(
        service,
        "_inspect_current_pair",
        lambda _config: manifest.active,
    )
    events: list[str] = []
    monkeypatch.setattr(
        service,
        "_require_services_ready",
        lambda _services, **_kwargs: events.append("readiness") or [],
    )
    monkeypatch.setattr(
        service,
        "_preflight_current_map_ui_auth",
        Mock(side_effect=DeploymentContractError(message)),
    )
    run = Mock()
    monkeypatch.setattr(service, "run", run)

    with pytest.raises(DeploymentContractError, match=message):
        if operation == "deploy":
            service.deploy_compatible_pinvi_pair()
        else:
            service.rollback_compatible_pinvi_pair()

    assert events == ["readiness"]
    run.assert_not_called()


def test_production_pinvi_ensure_is_staged_without_duplicate_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    config = _production_config()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(tmp_path / "compatible-pair-v2.json"),
    )
    events: list[tuple[str, object]] = []
    cancel_probe_states: list[PinviCancelProbeState] = []
    active = new_image_pair(
        _ACTIVE_MAP_IMAGE_ID, _ACTIVE_PINVI_IMAGE_ID, _CONTRACT_GENERATION
    )
    monkeypatch.setattr(
        service, "_production_preflight", lambda _config, **_kwargs: _manifest()
    )
    readiness: list[list[str]] = []
    monkeypatch.setattr(
        service,
        "_require_services_ready",
        lambda services, **_kwargs: readiness.append(list(services)) or [],
    )
    preflight_ui_smoke = [{"name": "map_ui_login", "status": 200}]
    monkeypatch.setattr(
        service,
        "_preflight_current_map_ui_auth",
        lambda _config: events.append(("preflight", "map-ui"))
        or preflight_ui_smoke,
    )

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        events.append(("run", args_list))
        return _success(args_list)

    def fake_smoke(_config):  # type: ignore[no-untyped-def]
        events.append(("smoke", "map"))
        return [{"name": "signed_read", "status": 200}]

    monkeypatch.setattr(service, "run", fake_run)
    monkeypatch.setattr(service, "_run_frozen_recovery", fake_run)
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=transaction),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        fake_smoke,
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_pinvi_canonical_smoke",
        lambda _config, **kwargs: cancel_probe_states.append(
            kwargs["cancel_probe_state"]
        )
        or events.append(("smoke", "pinvi"))
        or [{"name": "pinvi_provider_sync", "status": 200}],
    )
    monkeypatch.setattr(
        service,
        "_verify_active_contract",
        lambda _config, _pair, _services, **kwargs: cancel_probe_states.append(
            kwargs["cancel_probe_state"]
        )
        or events.append(("verify", "full"))
        or {
            "map_smoke": [],
            "pinvi_smoke": [],
            "ui_smoke": [{"name": "map_ui_login", "status": 200}],
            "runtime_secret_isolation": True,
        },
    )
    monkeypatch.setattr(service, "_inspect_current_pair", lambda _config: active)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.write_pair_manifest",
        lambda *_args: events.append(("manifest", "active")),
    )

    result = service._ensure_production_pinvi_target(
        "srv",
        config=config,
        build=True,
        recreate=True,
        capture_output=True,
        transaction=transaction,
    )

    assert result["success"] is True
    assert result["preflight_ui_smoke"] is preflight_ui_smoke
    assert events[0] == ("preflight", "map-ui")
    assert [stage["name"] for stage in result["stages"]] == [
        "quiesce_pinvi_api",
        "map_api",
        "pinvi_api",
    ]
    explicit_services = [
        service_name
        for stage in result["stages"]
        for service_name in stage["services"]
    ]
    assert len(explicit_services) == len(set(explicit_services))
    assert set(explicit_services) == {"kor-travel-map-api", "pinvi-api"}
    assert readiness
    assert len(cancel_probe_states) == 2
    assert cancel_probe_states[0] is cancel_probe_states[1]

    event_names = [event[0] for event in events]
    smoke_index = event_names.index("smoke")
    map_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "run"
        and event[1][0] == "up"
        and "kor-travel-map-api" in event[1]
    )
    pinvi_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "run" and event[1][0] == "up" and "pinvi-api" in event[1]
    )
    assert map_index < smoke_index < pinvi_index
    assert not any(
        event[0] == "run"
        and any(
            service in event[1]
            for service in ("pinvi-web", "pinvi-dagster", "kor-travel-map-ui")
        )
        for event in events
    )
    assert events.index(("verify", "full")) < events.index(("manifest", "active"))


def test_failed_map_smoke_never_invokes_pinvi_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(tmp_path / "compatible-pair-v2.json"),
    )
    active_recovery_transaction = replace(transaction)
    events: list[list[str]] = []
    monkeypatch.setattr(
        service, "_production_preflight", lambda _config, **_kwargs: _manifest()
    )
    monkeypatch.setattr(
        service, "_require_services_ready", lambda _services, **_kwargs: []
    )
    monkeypatch.setattr(service, "_preflight_current_map_ui_auth", lambda _config: [])

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        events.append(args_list)
        return _success(args_list)

    monkeypatch.setattr(service, "run", fake_run)
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=active_recovery_transaction),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        Mock(side_effect=DeploymentContractError("signed read failed")),
    )
    recovery = Mock(return_value={"success": True, "state": "restored"})
    monkeypatch.setattr(service, "_recover_previous_pair", recovery)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service._ensure_production_pinvi_target(
            "srv",
            config=_production_config(),
            build=False,
            recreate=True,
            capture_output=True,
            transaction=transaction,
        )

    result = recovery.call_args.args[0]
    assert result["success"] is False
    assert isinstance(caught.value.original_error, DeploymentContractError)
    assert not any(
        command[0] == "up" and "pinvi-api" in command for command in events
    )
    assert _READ_TOKEN not in result["stderr"]
    assert _CANCEL_TOKEN not in result["stderr"]
    recovery.assert_called_once()
    assert recovery.call_args.kwargs["transaction"] is active_recovery_transaction


def test_invalid_production_env_stops_before_any_container_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_c6c_process_environment(monkeypatch)
    env_path = tmp_path / ".env"
    _write_env(env_path, KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=None)
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(env_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "deploy.lock"))
    run = Mock()
    monkeypatch.setattr("kor_travel_docker_manager.services.compose_service.subprocess.run", run)

    with pytest.raises(DeploymentContractError):
        ComposeService().deploy_compatible_pinvi_pair(recreate=True)

    run.assert_not_called()


def test_pair_manifest_is_atomic_and_rejects_mutable_tags(tmp_path: Path) -> None:
    manifest_path = tmp_path / ".local" / "pair.json"
    with patch.object(c6c_deployment.os, "fsync", wraps=os.fsync) as fsync:
        write_pair_manifest(str(manifest_path), _manifest())

    loaded = load_pair_manifest(str(manifest_path))

    assert loaded.rollback.map_image_id == _MAP_IMAGE_ID
    assert loaded.rollback.pinvi_image_id == _PINVI_IMAGE_ID
    assert loaded.version == 2
    assert loaded.active.contract_generation == _CONTRACT_GENERATION
    assert fsync.call_count == 2
    assert not list(manifest_path.parent.glob("*.tmp"))
    with pytest.raises(DeploymentContractError, match="immutable"):
        new_image_pair(
            "kor-travel-map-api:latest-main", _PINVI_IMAGE_ID, _CONTRACT_GENERATION
        )

    legacy_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy_payload["version"] = 1
    manifest_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    with pytest.raises(DeploymentContractError, match="version"):
        load_pair_manifest(str(manifest_path))


@pytest.mark.parametrize("version", [True, "2", 2.0])
def test_pair_manifest_version_requires_exact_integer(
    tmp_path: Path,
    version: object,
) -> None:
    manifest_path = tmp_path / "pair.json"
    payload = json.loads(json.dumps(asdict(_manifest())))
    payload["version"] = version
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="invalid"):
        load_pair_manifest(str(manifest_path))


def test_pair_manifest_rejects_recorded_at_without_offset(tmp_path: Path) -> None:
    manifest_path = tmp_path / "pair.json"
    payload = json.loads(json.dumps(asdict(_manifest())))
    payload["active"]["recorded_at"] = "2026-07-18T00:00:00"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DeploymentContractError, match="recorded_at"):
        load_pair_manifest(str(manifest_path))


def test_pair_manifest_parent_fsync_failure_restores_previous_bytes(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "pair.json"
    original = _manifest()
    write_pair_manifest(str(manifest_path), original)
    manifest_path.chmod(0o640)
    original_bytes = manifest_path.read_bytes()
    replacement = CompatiblePairManifest(
        version=2,
        rollback=original.active,
        active=new_image_pair(
            _ACTIVE_MAP_IMAGE_ID,
            _ACTIVE_PINVI_IMAGE_ID,
            _CONTRACT_GENERATION,
        ),
    )
    real_fsync = os.fsync
    call_count = 0

    def fail_first_parent_fsync(fd: int) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("injected parent fsync failure")
        real_fsync(fd)

    with (
        patch.object(c6c_deployment.os, "fsync", side_effect=fail_first_parent_fsync),
        pytest.raises(DeploymentContractError, match="write failed"),
    ):
        write_pair_manifest(str(manifest_path), replacement)

    assert manifest_path.read_bytes() == original_bytes
    assert manifest_path.stat().st_mode & 0o777 == 0o640
    assert load_pair_manifest(str(manifest_path)) == original


def test_pair_capture_requires_explicit_compatibility_attestation() -> None:
    with pytest.raises(DeploymentContractError, match="verified-compatible"):
        ComposeService().capture_compatible_pinvi_pair(verified_compatible=False)


def test_pair_capture_bootstraps_candidate_pair_and_records_v2_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    manifest_path = tmp_path / ".local" / "pair.json"
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "capture.lock"))
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    pair = new_image_pair(_MAP_IMAGE_ID, _PINVI_IMAGE_ID, _CONTRACT_GENERATION)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        service, "_snapshot_service_states", lambda _services, **_kwargs: {}
    )
    monkeypatch.setattr(service, "_inspect_current_pair", lambda _config: pair)
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        lambda _config: [],
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_pinvi_canonical_smoke",
        lambda _config, **_kwargs: [],
    )
    monkeypatch.setattr(
        service,
        "_verify_active_contract",
        lambda *_a, **_k: {"runtime_secret_isolation": True},
    )
    commands: list[list[str]] = []

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        commands.append(args_list)
        return _success(args_list)

    monkeypatch.setattr(service, "run", fake_run)

    result = service.capture_compatible_pinvi_pair(
        verified_compatible=True,
        build=True,
    )

    assert result["success"] is True
    assert [stage["name"] for stage in result["stages"]] == [
        "bootstrap_base_db",
        "bootstrap_base_storage",
        "bootstrap_base_gra",
        "bootstrap_base_cadv",
        "bootstrap_base_prom",
        "bootstrap_base_geo",
        "bootstrap_base_conc",
        "bootstrap_stop_pair",
        "bootstrap_map_api",
        "bootstrap_map_dependents",
        "bootstrap_pinvi_api",
        "bootstrap_pinvi_dependents",
    ]
    assert len(result["init_results"]) == 3
    stop_index = commands.index(["stop", "pinvi-api", "kor-travel-map-api"])
    map_index = next(
        index
        for index, command in enumerate(commands)
        if command[0] == "up" and command[-1] == "kor-travel-map-api"
    )
    assert stop_index < map_index
    assert "--no-deps" in commands[map_index]
    assert "--build" in commands[map_index]
    assert commands[map_index + 1][-3:] == [
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
    ]
    assert commands[map_index + 2][-1] == "pinvi-api"
    assert commands[map_index + 3][-2:] == ["pinvi-web", "pinvi-dagster"]
    assert not any(
        command[0] == "up"
        and "kor-travel-map-api" in command
        and "pinvi-api" in command
        for command in commands
    )
    loaded = load_pair_manifest(str(manifest_path))
    assert loaded.rollback == pair
    assert loaded.active == pair


def test_pair_capture_actual_init_exception_cleans_created_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    manifest_path = tmp_path / "pair.json"
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "capture.lock"))
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        service, "_snapshot_service_states", lambda _services, **_kwargs: {}
    )
    commands: list[list[str]] = []

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        commands.append(args_list)
        if args_list[:3] == ["exec", "-T", "kor-travel-geo-postgres"]:
            raise RuntimeError("injected init exception")
        return _success(args_list)

    monkeypatch.setattr(service, "run", fake_run)
    monkeypatch.setattr(service, "_run_frozen_recovery", fake_run)
    captured_result: dict[str, object] = {}
    cleanup = service._cleanup_bootstrap

    def capture_cleanup(result, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured_result.update(result)
        return cleanup(result, *args, **kwargs)

    monkeypatch.setattr(service, "_cleanup_bootstrap", capture_cleanup)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.capture_compatible_pinvi_pair(verified_compatible=True)

    assert isinstance(caught.value.original_error, DeploymentContractError)
    assert str(caught.value.original_error) == "bootstrap init command failed"
    assert captured_result["success"] is False
    assert captured_result["init_results"] == []
    remove_command = next(command for command in commands if command[0] == "rm")
    assert "kor-travel-geo-postgres" in remove_command
    assert not manifest_path.exists()


def test_pair_capture_failure_halts_both_apis_without_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    manifest_path = tmp_path / "pair.json"
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "capture.lock"))
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        service, "_snapshot_service_states", lambda _services, **_kwargs: {}
    )
    monkeypatch.setattr(service, "_run_init_steps", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        Mock(side_effect=DeploymentContractError("candidate rejected")),
    )
    commands: list[list[str]] = []

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        commands.append(args_list)
        return _success(args_list)

    monkeypatch.setattr(service, "run", fake_run)
    monkeypatch.setattr(service, "_run_frozen_recovery", fake_run)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.capture_compatible_pinvi_pair(verified_compatible=True)

    assert isinstance(caught.value.original_error, DeploymentContractError)
    assert caught.value.restoration["state"] == "halted_requires_operator"
    assert ["stop", "pinvi-api", "kor-travel-map-api"] in commands
    remove_command = next(command for command in commands if command[0] == "rm")
    assert "kor-travel-map-api" in remove_command
    assert "pinvi-api" not in remove_command
    assert not manifest_path.exists()


def test_bootstrap_cleanup_removes_only_created_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    commands: list[list[str]] = []

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        commands.append(list(args))
        return _success(list(args))

    monkeypatch.setattr(service, "_run_frozen_recovery", fake_run)
    transaction = Mock(spec=ComposeTransactionSnapshot)
    result: dict[str, object] = {
        "success": False,
        "returncode": 1,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
        "deployment_state": "failed",
    }
    service._cleanup_bootstrap(
        result,
        _production_config(),
        {
            "kor-travel-geo-postgres": "running",
            "rustfs": "exited",
        },
        {
            "kor-travel-geo-postgres",
            "rustfs",
            "kor-travel-map-api",
            "pinvi-api",
            "new-service",
        },
        transaction=transaction,
    )

    remove = next(command for command in commands if command[0] == "rm")
    restore_stop = next(
        command
        for command in commands
        if command[0] == "stop" and command != ["stop", "pinvi-api", "kor-travel-map-api"]
    )
    assert set(remove[3:]) == {"kor-travel-map-api", "pinvi-api", "new-service"}
    assert restore_stop == ["stop", "rustfs"]
    assert "kor-travel-geo-postgres" not in remove


def test_production_bootstrap_cleanup_uses_real_guarded_compose_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_production_guard_environment(monkeypatch)
    lock = Mock(return_value=nullcontext())
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        lock,
    )
    completed = Mock(returncode=0, stdout="", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.subprocess.run", run
    )
    result: dict[str, object] = {
        "success": False,
        "returncode": 1,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
        "deployment_state": "failed",
    }

    transaction = _frozen_external_transaction(tmp_path)
    ComposeService()._cleanup_bootstrap(
        result,
        _production_config(),
        {},
        {"kor-travel-map-api", "new-service"},
        transaction=transaction,
    )

    assert result["deployment_state"] == "halted_requires_operator"
    compose_args = [call.args[0] for call in run.call_args_list]
    assert any(
        command[-3:] == ["stop", "pinvi-api", "kor-travel-map-api"]
        for command in compose_args
    )
    assert any(
        command[-5:]
        == [
            "rm",
            "--force",
            "--stop",
            "kor-travel-map-api",
            "new-service",
        ]
        for command in compose_args
    )
    assert lock.call_count == 2


def test_bootstrap_cleanup_exception_returns_controlled_operator_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        if list(args)[0] == "rm":
            raise DeploymentContractError("cleanup dispatch is uncertain")
        return _success(list(args))

    monkeypatch.setattr(service, "_run_frozen_recovery", fake_run)
    transaction = Mock(spec=ComposeTransactionSnapshot)
    result: dict[str, object] = {
        "success": False,
        "returncode": 1,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
        "deployment_state": "failed",
    }

    service._cleanup_bootstrap(
        result,
        _production_config(),
        {},
        {"new-service"},
        transaction=transaction,
    )

    assert result["deployment_state"] == "bootstrap_cleanup_failed_requires_operator"
    assert "cleanup raised unexpectedly" in str(result["stderr"])


def test_pair_capture_replaces_legacy_v1_only_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    manifest_path = tmp_path / "pair.json"
    write_pair_manifest(str(manifest_path), _manifest())
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy["version"] = 1
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "capture.lock"))
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=replace(transaction)),
    )
    pair = _manifest().active
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        service, "_snapshot_service_states", lambda _services, **_kwargs: {}
    )
    monkeypatch.setattr(service, "_run_init_steps", lambda *_a, **_k: True)
    monkeypatch.setattr(service, "_inspect_current_pair", lambda _config: pair)
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(service, "_verify_active_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        lambda _config: [],
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_pinvi_canonical_smoke",
        lambda _config, **_kwargs: [],
    )
    monkeypatch.setattr(
        service,
        "run",
        lambda args, **_kwargs: _success(list(args)),
    )

    result = service.capture_compatible_pinvi_pair(verified_compatible=True)

    assert result["success"] is True
    assert load_pair_manifest(str(manifest_path)).version == 2


def test_pair_capture_never_overwrites_existing_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    manifest_path = tmp_path / "pair.json"
    original = _manifest()
    write_pair_manifest(str(manifest_path), original)
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "capture.lock"))
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    run = Mock()
    monkeypatch.setattr(service, "run", run)

    with pytest.raises(DeploymentContractError, match="v2 already exists"):
        service.capture_compatible_pinvi_pair(verified_compatible=True)

    run.assert_not_called()
    assert load_pair_manifest(str(manifest_path)) == original


def test_pair_rollback_restores_map_then_smoke_then_pinvi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    manifest_path = tmp_path / ".local" / "pair.json"
    manifest = _manifest()
    write_pair_manifest(str(manifest_path), manifest)
    rollback_pair = manifest.rollback
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "rollback.lock"))
    service = ComposeService()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=replace(transaction)),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(
        service, "_require_services_ready", lambda _services, **_kwargs: []
    )
    preflight_ui_smoke = [{"name": "map_ui_login", "status": 200}]
    monkeypatch.setattr(
        service,
        "_preflight_current_map_ui_auth",
        lambda _config: preflight_ui_smoke,
    )
    monkeypatch.setattr(service, "_inspect_current_pair", lambda _config: manifest.active)
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        service,
        "_verify_active_contract",
        lambda *_a, **_k: {"runtime_secret_isolation": True},
    )
    events: list[str] = []
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        lambda _config: events.append("map_smoke") or [],
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        if args_list[0] == "up":
            events.append(f"up:{args_list[-1]}")
        calls.append((args_list, kwargs))
        return _success(args_list)

    monkeypatch.setattr(service, "run", fake_run)

    result = service.rollback_compatible_pinvi_pair()

    assert result["success"] is True
    assert result["preflight_ui_smoke"] is preflight_ui_smoke
    assert calls[0][0] == ["stop", "pinvi-api", "kor-travel-map-api"]
    map_args, map_kwargs = calls[1]
    pinvi_args, pinvi_kwargs = calls[2]
    assert map_args[-1] == "kor-travel-map-api"
    assert pinvi_args[-1] == "pinvi-api"
    assert events == ["up:kor-travel-map-api", "map_smoke", "up:pinvi-api"]
    assert map_kwargs["environment"] == {
        "KOR_TRAVEL_MAP_API_IMAGE": _MAP_IMAGE_ID,
        "PINVI_API_IMAGE": _PINVI_IMAGE_ID,
    }
    assert pinvi_kwargs["environment"] == map_kwargs["environment"]
    assert not any(
        call[0][0] == "up"
        and "kor-travel-map-api" in call[0]
        and "pinvi-api" in call[0]
        for call in calls
    )
    assert load_pair_manifest(str(manifest_path)).active == rollback_pair


def test_production_preflight_rejects_running_pair_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_pair_manifest",
        lambda _path: _manifest(),
    )
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(
        service,
        "_inspect_current_pair",
        lambda _config: new_image_pair(
            _ACTIVE_MAP_IMAGE_ID, _ACTIVE_PINVI_IMAGE_ID, _CONTRACT_GENERATION
        ),
    )
    transaction = Mock(spec=ComposeTransactionSnapshot)

    with pytest.raises(DeploymentContractError, match="drifted"):
        service._production_preflight(
            _production_config(),
            transaction=transaction,
        )


def test_pinvi_smoke_failure_restores_start_pair_before_remaining_apps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    config = _production_config()
    manifest = _manifest()
    transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(tmp_path / "compatible-pair-v2.json"),
    )
    active_recovery_transaction = replace(transaction)
    commands: list[list[str]] = []
    recovery = Mock(return_value={"success": True, "state": "restored"})
    write_manifest = Mock()
    monkeypatch.setattr(
        service, "_production_preflight", lambda _config, **_kwargs: manifest
    )
    monkeypatch.setattr(
        service, "_require_services_ready", lambda _services, **_kwargs: []
    )
    monkeypatch.setattr(service, "_preflight_current_map_ui_auth", lambda _config: [])

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        args_list = list(args)
        commands.append(args_list)
        return _success(args_list)

    monkeypatch.setattr(service, "run", fake_run)
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=active_recovery_transaction),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        lambda _config: [],
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_pinvi_canonical_smoke",
        Mock(side_effect=DeploymentContractError("typed cancel contract failed")),
    )
    monkeypatch.setattr(service, "_recover_previous_pair", recovery)
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.write_pair_manifest",
        write_manifest,
    )

    with pytest.raises(ComposePostMutationContractError):
        service._ensure_production_pinvi_target(
            "pinvi",
            config=config,
            build=False,
            recreate=True,
            capture_output=True,
            transaction=transaction,
        )

    result = recovery.call_args.args[0]
    assert result["success"] is False
    recovery.assert_called_once()
    assert recovery.call_args.args == (
        result,
        config,
        manifest.active,
        result["services"],
    )
    assert isinstance(
        recovery.call_args.kwargs["cancel_probe_state"],
        PinviCancelProbeState,
    )
    assert recovery.call_args.kwargs["transaction"] is active_recovery_transaction
    untouched = {
        "pinvi-web",
        "pinvi-dagster",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "kor-travel-geo-postgres",
    }
    assert not any(untouched.intersection(command) for command in commands)
    write_manifest.assert_not_called()


def test_recovery_restores_start_pair_and_runs_full_contract_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    config = _production_config()
    active_at_start = new_image_pair(
        _ACTIVE_MAP_IMAGE_ID,
        _ACTIVE_PINVI_IMAGE_ID,
        _CONTRACT_GENERATION,
    )
    result: dict[str, object] = {
        "success": False,
        "returncode": 1,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
        "deployment_state": "failed",
    }
    transaction = Mock(spec=ComposeTransactionSnapshot)
    validated: list[tuple[object, object, bool]] = []
    monkeypatch.setattr(
        service,
        "_validate_resolved_compose_contract",
        lambda _config, *, expected_pair, transaction, frozen_recovery: validated.append(
            (expected_pair, transaction, frozen_recovery)
        ),
    )

    commands: list[list[str]] = []

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        commands.append(list(args))
        return _success(list(args))

    monkeypatch.setattr(service, "_run_frozen_recovery", fake_run)
    verify = Mock(return_value={"runtime_secret_isolation": True})
    monkeypatch.setattr(service, "_verify_active_contract", verify)
    map_smoke = Mock(return_value=[])
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        map_smoke,
    )

    service._recover_previous_pair(
        result,
        config,
        active_at_start,
        ["map", "pinvi"],
        transaction=transaction,
    )

    assert result["deployment_state"] == "previous_active_pair_restored"
    assert validated == [(active_at_start, transaction, True)]
    verify.assert_called_once()
    assert verify.call_args.args == (config, active_at_start, ["map", "pinvi"])
    assert verify.call_args.kwargs["cancel_probe_state"] is None
    assert verify.call_args.kwargs["transaction"] is transaction
    assert verify.call_args.kwargs["frozen_recovery"] is True
    assert commands[0] == ["stop", "pinvi-api", "kor-travel-map-api"]
    assert commands[1][-1] == "kor-travel-map-api"
    assert commands[2][-1] == "pinvi-api"
    map_smoke.assert_called_once_with(config)


def test_recovery_validation_failure_halts_both_apis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ComposeService()
    config = _production_config()
    result: dict[str, object] = {
        "success": False,
        "returncode": 1,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
        "deployment_state": "failed",
    }
    monkeypatch.setattr(
        service,
        "_validate_resolved_compose_contract",
        Mock(side_effect=DeploymentContractError("active contract unavailable")),
    )
    transaction = Mock(spec=ComposeTransactionSnapshot)
    run = Mock(return_value=_success(["stop", "pinvi-api", "kor-travel-map-api"]))
    monkeypatch.setattr(service, "_run_frozen_recovery", run)

    service._recover_previous_pair(
        result,
        config,
        _manifest().active,
        ["pinvi"],
        transaction=transaction,
    )

    assert result["deployment_state"] == "halted_requires_operator"
    assert run.call_args.args[0] == ["stop", "pinvi-api", "kor-travel-map-api"]


def test_rollback_validates_active_and_rollback_images_before_first_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    rollback = _manifest().rollback
    active = new_image_pair(
        _ACTIVE_MAP_IMAGE_ID,
        _ACTIVE_PINVI_IMAGE_ID,
        _CONTRACT_GENERATION,
    )
    manifest = CompatiblePairManifest(version=2, rollback=rollback, active=active)
    manifest_path = tmp_path / "pair.json"
    write_pair_manifest(str(manifest_path), manifest)
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "rollback.lock"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    service = ComposeService()
    root_transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    active_recovery_transaction = replace(root_transaction)
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(root_transaction, Mock())),
    )
    materialize = Mock(return_value=active_recovery_transaction)
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        materialize,
    )
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(
        service, "_require_services_ready", lambda _services, **_kwargs: []
    )
    monkeypatch.setattr(service, "_inspect_current_pair", lambda _config: active)
    validated: list[CompatibleImagePair] = []

    def validate(  # type: ignore[no-untyped-def]
        _config,
        *,
        environment_override,
        expected_pair,
        transaction,
    ):
        assert environment_override == service._pair_image_environment(expected_pair)
        assert transaction is root_transaction
        validated.append(expected_pair)
        if expected_pair == rollback:
            raise DeploymentContractError("rollback override mismatch")
        return {}

    monkeypatch.setattr(service, "_validate_resolved_compose_contract", validate)
    run = Mock()
    monkeypatch.setattr(service, "run", run)

    with pytest.raises(DeploymentContractError, match="override mismatch"):
        service.rollback_compatible_pinvi_pair()

    assert validated == [active, rollback]
    assert materialize.call_args.args == (
        root_transaction,
        _production_config(),
        active,
    )
    run.assert_not_called()


def test_rollback_verification_failure_keeps_manifest_and_recovers_start_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_manager_mutation(monkeypatch)
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    rollback = _manifest().rollback
    active = new_image_pair(
        _ACTIVE_MAP_IMAGE_ID,
        _ACTIVE_PINVI_IMAGE_ID,
        _CONTRACT_GENERATION,
    )
    manifest = CompatiblePairManifest(version=2, rollback=rollback, active=active)
    manifest_path = tmp_path / "pair.json"
    write_pair_manifest(str(manifest_path), manifest)
    monkeypatch.setenv("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", str(manifest_path))
    monkeypatch.setenv("KTDM_C6C_DEPLOYMENT_LOCK", str(tmp_path / "rollback.lock"))
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.load_c6c_deployment_config_from_environment",
        lambda _environment: _production_config(),
    )
    service = ComposeService()
    root_transaction = replace(
        _frozen_external_transaction(tmp_path),
        manifest_path=str(manifest_path),
    )
    active_recovery_transaction = replace(root_transaction)
    monkeypatch.setattr(
        service,
        "_capture_transaction_unlocked",
        Mock(return_value=(root_transaction, Mock(spec=ValidatedComposeCandidate))),
    )
    monkeypatch.setattr(
        service,
        "_materialize_active_recovery_transaction_unlocked",
        Mock(return_value=active_recovery_transaction),
    )
    monkeypatch.setattr(service, "_require_local_image", lambda _image: None)
    monkeypatch.setattr(service, "_inspect_current_pair", lambda _config: active)
    monkeypatch.setattr(service, "_validate_resolved_compose_contract", lambda *_a, **_k: {})
    monkeypatch.setattr(
        service, "_require_services_ready", lambda _services, **_kwargs: []
    )
    monkeypatch.setattr(service, "_preflight_current_map_ui_auth", lambda _config: [])
    monkeypatch.setattr(
        service,
        "run",
        lambda args, **_kwargs: _success(list(args)),
    )
    monkeypatch.setattr(
        service,
        "_verify_active_contract",
        Mock(side_effect=DeploymentContractError("full rollback smoke failed")),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.run_map_ops_smoke",
        lambda _config: [],
    )
    recovery = Mock(return_value={"success": True, "state": "restored"})
    monkeypatch.setattr(service, "_recover_previous_pair", recovery)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.rollback_compatible_pinvi_pair()

    result = recovery.call_args.args[0]
    assert result["success"] is False
    assert isinstance(caught.value.original_error, DeploymentContractError)
    assert load_pair_manifest(str(manifest_path)) == manifest
    recovery.assert_called_once()
    recovery_args = recovery.call_args.args
    assert recovery_args[:3] == (result, _production_config(), active)
    assert set(result["services"]) <= set(recovery_args[3])
    assert isinstance(
        recovery.call_args.kwargs["cancel_probe_state"],
        PinviCancelProbeState,
    )
    assert recovery.call_args.kwargs["transaction"] is active_recovery_transaction


def test_stage_output_redacts_every_c6c_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ComposeService()
    config = _production_config()
    credentials = (
        _READ_TOKEN,
        _CANCEL_TOKEN,
        _MAP_UI_USERNAME,
        _MAP_UI_PASSWORD_HASH,
        _MAP_UI_SESSION_SECRET,
        _MAP_UI_PASSWORD,
        config.smoke.pinvi_admin_email,
        _PINVI_ADMIN_PASSWORD,
        _CANCEL_PROBE_JOB_ID,
        _CONTRACT_GENERATION,
    )
    all_secrets = " ".join(credentials)
    monkeypatch.setattr(
        service,
        "run",
        lambda *_args, **_kwargs: {
            "success": False,
            "returncode": 1,
            "command": [],
            "stdout": all_secrets,
            "stderr": all_secrets,
        },
    )
    result: dict[str, object] = {
        "success": True,
        "returncode": 0,
        "stages": [],
        "command": [],
        "stdout": "",
        "stderr": "",
    }

    assert not service._run_up_stage(
        result,
        "redaction_probe",
        ["pinvi-api"],
        build=False,
        recreate=False,
        no_deps=True,
        capture_output=True,
        redact_config=config,
        transaction=Mock(spec=ComposeTransactionSnapshot),
    )
    serialized = json.dumps(result)
    for secret in credentials:
        assert secret not in serialized


def test_c6c_config_repr_error_and_redactor_never_expose_credentials() -> None:
    service = ComposeService()
    config = _production_config()
    credentials = (
        config.read_token,
        config.cancel_token,
        config.smoke.map_ui_username,
        config.map_ui_password_hash,
        config.map_ui_session_secret,
        config.smoke.map_ui_password,
        config.smoke.pinvi_admin_email,
        config.smoke.pinvi_admin_password,
        config.smoke.cancel_probe_job_id,
        config.contract_generation,
    )
    raw = " | ".join(credentials)
    redacted = service._redact_c6c_output(raw, config)
    error = DeploymentContractError(redacted)
    surfaces = (
        repr(config),
        repr(config.smoke),
        redacted,
        str(error),
        json.dumps({"result": {"stdout": redacted, "stderr": str(error)}}),
    )

    for surface in surfaces:
        for credential in credentials:
            assert credential not in surface


def test_c6c_redactor_removes_overlapping_credentials_without_suffix_residue() -> None:
    base = _production_config()
    smoke = replace(
        base.smoke,
        map_ui_username="overlap-ui",
        map_ui_password="overlap-ui-password-identifiable-tail",
    )
    config = replace(
        base,
        read_token="overlap-token",
        cancel_token="overlap-token-cancel-identifiable-tail",
        smoke=smoke,
    )
    raw = " | ".join(
        (
            config.cancel_token,
            config.read_token,
            config.smoke.map_ui_password,
            config.smoke.map_ui_username,
        )
    )

    redacted = ComposeService()._redact_c6c_output(raw, config)

    assert redacted == " | ".join(["<redacted>"] * 4)
    assert "cancel-identifiable-tail" not in redacted
    assert "password-identifiable-tail" not in redacted
    for credential in (
        config.cancel_token,
        config.read_token,
        config.smoke.map_ui_password,
        config.smoke.map_ui_username,
    ):
        assert credential not in redacted
