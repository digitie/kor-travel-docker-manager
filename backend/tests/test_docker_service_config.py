import hashlib
from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services import docker_service as docker_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
    compose_volume_graph_hash,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeEnvFileIdentity,
    ComposeEnvironmentSnapshot,
    ComposeExternalInputSnapshot,
    ComposeTransactionSnapshot,
    ValidatedComposeCandidate,
    _resolved_compose_document_hash,
)
from kor_travel_docker_manager.services.docker_service import (
    ContainerConfigValidationError,
    DockerService,
    validate_container_config_update,
    validate_env_entry,
    validate_network_name,
    validate_port_mapping,
)

_ROOT = Path(__file__).resolve().parents[2]
_CONCIERGE_BASE_URL_ENV = "${KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_BASE_URL:-http://127.0.0.1:12601}"
_CONCIERGE_API_KEY_ENV = "${KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY:-}"
_MAP_FETCH_SERVICES = (
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_MAP_INGESTION_SERVICES = (
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_MAP_API_SERVICE = "kor-travel-map-api"
_MAP_UI_SERVICE = "kor-travel-map-ui"
_MAP_POSTGRES_SERVICE = "kor-travel-map-postgres"
_MAP_DAGSTER_SERVICE = "kor-travel-map-dagster"
_MAP_DAGSTER_DAEMON_SERVICE = "kor-travel-map-dagster-daemon"
_MAP_DAGSTER_STORAGE_MIGRATE_SERVICE = "kor-travel-map-dagster-storage-migrate"
_MAP_DAGSTER_DB_INIT_SERVICE = "kor-travel-map-dagster-db-init"
_MAP_DB_ROLE_BOOTSTRAP_SERVICE = "kor-travel-map-db-role-bootstrap"
_MAP_MIGRATION_BOUNDARY_SERVICE = "kor-travel-map-migration-boundary"
_PINVI_POSTGRES_SERVICE = "pinvi-postgres"
_PINVI_DB_INIT_SERVICE = "pinvi-db-init"
_PINVI_API_SERVICE = "pinvi-api"
_PINVI_ADMIN_BOOTSTRAP_SERVICE = "pinvi-admin-bootstrap"
_OPS_READ_SOURCE = "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"
_OPS_CANCEL_SOURCE = "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
_OPS_FIXTURE_SOURCE = "${KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN:-}"
_CURATION_SNAPSHOT_DIGEST_SOURCE = (
    "${KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256:-}"
)
_CURATION_CUTOVER_MAPPING_DIGEST_SOURCE = (
    "${KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256:-}"
)
_FEATURE_CREATE_TOKEN_SOURCE = (
    "${KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN:?"
    "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN must be explicitly set}"
)
_FEATURE_CREATE_TOKEN_DIGEST_SOURCE = (
    "${KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256:?"
    "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256 must be explicitly set}"
)
_PINVI_CURATION_SNAPSHOT_SOURCE = "${PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN:-}"
_PINVI_CUTOVER_MAPPING_SOURCE = (
    "${PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN:-}"
)
_PINVI_MAP_BASE_URL_SOURCE = (
    "${PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL:-http://127.0.0.1:"
    "${KOR_TRAVEL_MAP_API_CONTAINER_PORT:-12701}}"
)
_OPINET_API_KEY_ENV = "${KOR_TRAVEL_MAP_OPINET_API_KEY:-}"
_KREX_EX_API_KEY_ENV = "${KOR_TRAVEL_MAP_KREX_EX_API_KEY:-}"
_KREX_GO_API_KEY_ENV = "${KOR_TRAVEL_MAP_KREX_GO_API_KEY:-}"
_FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES = {
    "KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_KMA_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_KMA_APIHUB_KEY",
    "KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_DATAGOKR_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_VISITKOREA_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_KNPS_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_AIRKOREA_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_KRFOREST_SERVICE_KEY",
    "KOR_TRAVEL_MAP_API_ETL_LIVE_PREVIEW_ENABLED",
}
_MAP_UI_USERNAME = "map-ui-admin-placeholder"
_MAP_UI_PASSWORD_HASH = "pbkdf2_sha256$100000$test-salt$test-digest"
_MAP_UI_SESSION_SECRET = "map-ui-session-secret-placeholder-value"
_MAP_ADMIN_PROXY_SECRET = "map-admin-proxy-secret-placeholder-value"
_MAP_SERVICE_TOKEN = "map-service-token-placeholder-value"
_MAP_CURSOR_SIGNING_SECRET = "map-cursor-signing-secret-placeholder-value"
_MAP_GEO_API_KEY_SOURCE = "${KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY}"
_MAP_UI_GEO_API_KEY_SOURCE = (
    "${KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY:?"
    "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY must be explicitly set}"
)
_PINVI_POSTGRES_IMAGE = (
    "postgis/postgis@sha256:8b33190b6486ab9905dea999171817c1ac461733a7078dd4c836091c6e6b5d40"
)
_PINVI_POSTGRES_INITDB_ARGS = "--auth-host=scram-sha-256"


def _compose_success(command: list[str] | None = None) -> dict[str, object]:
    return {
        "success": True,
        "returncode": 0,
        "command": command or ["docker", "compose"],
        "stdout": "",
        "stderr": "",
    }


def _config_transaction(
    compose_path: Path,
    config: dict[str, object],
) -> tuple[ComposeTransactionSnapshot, ValidatedComposeCandidate]:
    source_bytes = yaml.safe_dump(
        config,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    resolved = deepcopy(config)
    environment = ComposeEnvironmentSnapshot(
        effective={
            "KTDM_DEPLOYMENT_ENVIRONMENT": "local",
            "PINVI_ENVIRONMENT": "development",
            "PINVI_POSTGRES_PASSWORD": "pinvi-contract-password",
            "PINVI_DB_PORT": "12800",
            "PINVI_POSTGRES_USER": "pinvi",
            "PINVI_POSTGRES_DB": "pinvi",
            "PINVI_POSTGRES_BOOTSTRAP_DB": "pinvi_bootstrap",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "false",
        },
        env_path=str(compose_path.parent / ".env"),
        compose_path=str(compose_path),
        override_path=str(compose_path.parent / "missing.override.yml"),
        env_file_identity=ComposeEnvFileIdentity(exists=False),
        env_file_bytes=b"",
    )
    external = ComposeExternalInputSnapshot(references=(), files=())
    transaction = ComposeTransactionSnapshot(
        environment=environment,
        external_inputs=external,
        compose_source_bytes=source_bytes,
        compose_source_mode=0o640,
        system_bind_snapshots=(),
        raw_volume_graph_hash=compose_volume_graph_hash(config),
        resolved_volume_graph_hash=compose_volume_graph_hash(resolved),
        resolved=resolved,
        resolved_document_hash=_resolved_compose_document_hash(resolved),
    )
    return transaction, ValidatedComposeCandidate(
        resolved=resolved,
        system_bind_snapshots=(),
        raw_volume_graph_hash=transaction.raw_volume_graph_hash,
        resolved_volume_graph_hash=transaction.resolved_volume_graph_hash,
        environment_snapshot=environment,
        external_input_snapshot=external,
        transaction_snapshot=transaction,
    )


def _candidate_capture_for(compose_path: Path):  # type: ignore[no-untyped-def]
    def capture(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    return capture


def _compose_with_canonical_c6c_services(
    services: dict[str, object],
) -> dict[str, object]:
    bootstrap_dsn = (
        "${KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN:?"
        "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN must be explicitly set}"
    )
    dagster_runtime_dsn = (
        "${KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN:?"
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN must be explicitly set}"
    )
    dagster_pg_url = (
        "${KOR_TRAVEL_MAP_DAGSTER_PG_URL:?"
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL must be explicitly set}"
    )
    metadata_password = (
        "${KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD:?"
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD must be explicitly set}"
    )
    protected_services: dict[str, object] = {
        _MAP_POSTGRES_SERVICE: {
            "image": "fixture.invalid/postgis:test",
            "container_name": "kor-travel-map-postgres",
            "network_mode": "host",
            "environment": {
                "POSTGRES_DB": (
                    "${KOR_TRAVEL_MAP_POSTGRES_DB:?"
                    "KOR_TRAVEL_MAP_POSTGRES_DB must be explicitly set}"
                ),
                "POSTGRES_USER": (
                    "${KOR_TRAVEL_MAP_POSTGRES_USER:?"
                    "KOR_TRAVEL_MAP_POSTGRES_USER must be explicitly set}"
                ),
                "POSTGRES_PASSWORD_FILE": "/run/secrets/kor-travel-map-postgres-password",
            },
            "secrets": [
                {
                    "source": "kor-travel-map-postgres-password",
                    "target": "kor-travel-map-postgres-password",
                }
            ],
        },
        _MAP_DAGSTER_DB_INIT_SERVICE: {
            "image": "fixture.invalid/postgres:test",
            "network_mode": "host",
            "environment": {
                "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN": bootstrap_dsn,
                "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": (
                    "${KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB:?"
                    "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": (
                    "${KOR_TRAVEL_MAP_DAGSTER_METADATA_USER:?"
                    "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD": metadata_password,
            },
            "command": ["psql \"$KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN\""],
        },
        _MAP_DB_ROLE_BOOTSTRAP_SERVICE: {
            "image": "fixture.invalid/postgres:test",
            "network_mode": "host",
            "environment": {
                "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN": bootstrap_dsn,
                "KOR_TRAVEL_MAP_DB_ROLE_BOOTSTRAP_CONFIRM_DATABASE": (
                    "${KOR_TRAVEL_MAP_POSTGRES_DB:?"
                    "KOR_TRAVEL_MAP_POSTGRES_DB must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_POSTGRES_DB": (
                    "${KOR_TRAVEL_MAP_POSTGRES_DB:?"
                    "KOR_TRAVEL_MAP_POSTGRES_DB must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_POSTGRES_USER": (
                    "${KOR_TRAVEL_MAP_POSTGRES_USER:?"
                    "KOR_TRAVEL_MAP_POSTGRES_USER must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_MIGRATOR_PASSWORD": (
                    "${KOR_TRAVEL_MAP_MIGRATOR_PASSWORD:?"
                    "KOR_TRAVEL_MAP_MIGRATOR_PASSWORD must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD": (
                    "${KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD:?"
                    "KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD": (
                    "${KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD:?"
                    "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD must be explicitly set}"
                ),
            },
        },
        _MAP_MIGRATION_BOUNDARY_SERVICE: {
            "image": "fixture.invalid/kor-travel-map-api:test",
            "network_mode": "host",
            "environment": {
                "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN": (
                    "${KOR_TRAVEL_MAP_MIGRATOR_PG_DSN:?"
                    "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN must be explicitly set}"
                ),
            },
        },
        _PINVI_POSTGRES_SERVICE: {
            "image": _PINVI_POSTGRES_IMAGE,
            "container_name": "pinvi-postgres",
            "network_mode": "host",
            "environment": {
                "PINVI_CONTRACT_FIXTURE": "fixture",
                "POSTGRES_USER": "${PINVI_POSTGRES_USER:-pinvi}",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/pinvi-postgres-password",
                "POSTGRES_DB": "${PINVI_POSTGRES_BOOTSTRAP_DB:-pinvi_bootstrap}",
                "POSTGRES_INITDB_ARGS": _PINVI_POSTGRES_INITDB_ARGS,
            },
            "command": [
                "postgres",
                "-c",
                "listen_addresses=127.0.0.1",
                "-p",
                "${PINVI_DB_PORT:-12800}",
                "-c",
                "shared_preload_libraries=pg_stat_statements",
                "-c",
                "shared_buffers=${PINVI_POSTGRES_SHARED_BUFFERS:-128MB}",
                "-c",
                "work_mem=${PINVI_POSTGRES_WORK_MEM:-16MB}",
                "-c",
                "maintenance_work_mem=${PINVI_POSTGRES_MAINTENANCE_WORK_MEM:-128MB}",
                "-c",
                "effective_cache_size=${PINVI_POSTGRES_EFFECTIVE_CACHE_SIZE:-512MB}",
                "-c",
                "random_page_cost=${PINVI_POSTGRES_RANDOM_PAGE_COST:-1.1}",
                "-c",
                "max_wal_size=${PINVI_POSTGRES_MAX_WAL_SIZE:-1GB}",
                "-c",
                "pg_stat_statements.track=all",
                "-c",
                "pg_stat_statements.max=10000",
            ],
            "secrets": [
                {
                    "source": "pinvi-postgres-password",
                    "target": "pinvi-postgres-password",
                }
            ],
        },
        _PINVI_DB_INIT_SERVICE: {
            "image": _PINVI_POSTGRES_IMAGE,
            "network_mode": "host",
            "environment": {
                "PGHOST": "127.0.0.1",
                "PGPORT": "${PINVI_DB_PORT:-12800}",
                "PGUSER": "${PINVI_POSTGRES_USER:-pinvi}",
                "PGDATABASE": "${PINVI_POSTGRES_BOOTSTRAP_DB:-pinvi_bootstrap}",
                "PINVI_POSTGRES_DB": "${PINVI_POSTGRES_DB:-pinvi}",
            },
            "secrets": ["pinvi-postgres-password"],
            "command": [
                "sh",
                "-ec",
                "PGPASSWORD=\"$$(cat /run/secrets/pinvi-postgres-password)\"\n"
                "export PGPASSWORD\n"
                "if psql -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='$$PINVI_POSTGRES_DB'\" | grep -q 1; then\n"
                "  echo \"database $$PINVI_POSTGRES_DB already exists\"\n"
                "else\n"
                "  createdb \"$$PINVI_POSTGRES_DB\"\n"
                "fi\n",
            ],
        },
        _MAP_API_SERVICE: {
            "image": "fixture.invalid/kor-travel-map-api:test",
            "container_name": "kor-travel-map-api-latest",
            "network_mode": "host",
            "environment": {
                "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": _OPS_READ_SOURCE,
                "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": _OPS_CANCEL_SOURCE,
                "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": _OPS_FIXTURE_SOURCE,
                "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": (
                    "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?"
                    "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
                    "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
                    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": (
                    "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?"
                    "KOR_TRAVEL_MAP_API_SERVICE_TOKEN must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET": (
                    "${KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET:?"
                    "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY": _MAP_GEO_API_KEY_SOURCE,
                "KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256": (
                    _CURATION_SNAPSHOT_DIGEST_SOURCE
                ),
                "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256": (
                    _CURATION_CUTOVER_MAPPING_DIGEST_SOURCE
                ),
                "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256": (
                    _FEATURE_CREATE_TOKEN_DIGEST_SOURCE
                ),
                "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED": (
                    "${KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED:-false}"
                ),
                "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN": (
                    "${KOR_TRAVEL_MAP_MIGRATOR_PG_DSN:?"
                    "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN": (
                    "${KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN:?"
                    "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_PG_DSN": (
                    "${KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN:?"
                    "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_API_PROFILE": "production",
                "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED": "true",
                "KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED": "true",
                "KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED": "true",
                "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED": "false",
                "KOR_TRAVEL_MAP_API_PROMETHEUS_METRICS_ENABLED": "false",
                "KOR_TRAVEL_MAP_API_ADMIN_TRUSTED_PROXY_CIDRS": (
                    '["127.0.0.1/32","::1/128"]'
                ),
            }
        },
        **{
            service_name: {
                "image": "fixture.invalid/kor-travel-map-dagster:test",
                "network_mode": "host",
                "environment": {
                    "KOR_TRAVEL_MAP_DAGSTER_PG_URL": dagster_pg_url,
                    "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN": dagster_runtime_dsn,
                    "KOR_TRAVEL_MAP_PG_DSN": dagster_runtime_dsn,
                    **(
                        {
                            "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY": (
                                _MAP_GEO_API_KEY_SOURCE
                            )
                        }
                        if service_name
                        in (_MAP_DAGSTER_SERVICE, _MAP_DAGSTER_DAEMON_SERVICE)
                        else {}
                    ),
                },
            }
            for service_name in (
                _MAP_DAGSTER_SERVICE,
                _MAP_DAGSTER_DAEMON_SERVICE,
                _MAP_DAGSTER_STORAGE_MIGRATE_SERVICE,
            )
        },
        _MAP_UI_SERVICE: {
            "image": "fixture.invalid/kor-travel-map-ui:test",
            "container_name": "kor-travel-map-ui-latest",
            "network_mode": "host",
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
                "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": (
                    "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
                    "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET must be explicitly set}"
                ),
                "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": (
                    _FEATURE_CREATE_TOKEN_SOURCE
                ),
                "KOR_TRAVEL_GEO_API_KEY": _MAP_UI_GEO_API_KEY_SOURCE,
            }
        },
        _PINVI_API_SERVICE: {
            "image": "fixture.invalid/pinvi-api:test",
            "container_name": "pinvi-api-latest",
            "network_mode": "host",
            "environment": {
                "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN": _OPS_READ_SOURCE,
                "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN": _OPS_CANCEL_SOURCE,
                "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN": (
                    _PINVI_CURATION_SNAPSHOT_SOURCE
                ),
                "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN": (
                    _PINVI_CUTOVER_MAPPING_SOURCE
                ),
                "PINVI_DATABASE_URL": (
                    "${PINVI_DOCKER_DATABASE_URL:-postgresql+asyncpg://pinvi:"
                    "pinvi_dev_password@127.0.0.1:12800/pinvi}"
                ),
                "PINVI_KOR_TRAVEL_MAP_API_BASE_URL": (
                    "${PINVI_KOR_TRAVEL_MAP_API_BASE_URL:-http://127.0.0.1:"
                    "${KOR_TRAVEL_MAP_API_CONTAINER_PORT:-12701}}"
                ),
            }
        },
        _PINVI_ADMIN_BOOTSTRAP_SERVICE: {
            "image": "fixture.invalid/pinvi-api:test",
            "network_mode": "host",
            "environment": {
                "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN": _OPS_READ_SOURCE,
                "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN": _OPS_CANCEL_SOURCE,
                "PINVI_DATABASE_URL": (
                    "${PINVI_DOCKER_DATABASE_URL:-postgresql+asyncpg://pinvi:"
                    "pinvi_dev_password@127.0.0.1:12800/pinvi}"
                ),
            },
        },
    }
    assert not protected_services.keys() & services.keys()
    protected_services.update(deepcopy(services))
    return {
        "services": protected_services,
        "secrets": {
            "kor-travel-map-postgres-password": {
                "environment": "KOR_TRAVEL_MAP_POSTGRES_PASSWORD"
            },
            "pinvi-postgres-password": {"environment": "PINVI_POSTGRES_PASSWORD"},
        },
    }


def test_nontrivial_config_change_runs_candidate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    original_config: dict[str, object] = {
        "services": {
                "kor-travel-geo-postgres": {
                    "image": "postgres:16",
                    "environment": {"POSTGRES_DB": "before"},
                    "volumes": [],
                }
        }
    }
    baseline, baseline_validation = _config_transaction(
        compose_path, original_config
    )
    compose_path.write_bytes(baseline.compose_source_bytes)
    compose_path.chmod(baseline.compose_source_mode)
    candidate_transactions: list[ComposeTransactionSnapshot] = []

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        transaction, validation = _config_transaction(compose_path, candidate)
        candidate_transactions.append(transaction)
        return validation

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    forward = Mock(return_value=_compose_success())
    monkeypatch.setattr(docker_service_module.compose_service, "run", forward)

    result = DockerService()._update_container_config_unlocked(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "after"},
        [],
        [],
        environment_snapshot=baseline.environment,
    )

    assert result["success"] is True
    candidate = candidate_transactions[0]
    assert candidate is not baseline
    assert compose_path.read_bytes() == candidate.compose_source_bytes
    assert forward.call_args.kwargs["transaction"] is candidate


def test_locked_config_transaction_revalidates_secret_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    current_config: dict[str, object] = {
        "services": {
            "kor-travel-geo-postgres": {
                "image": "postgres:16",
                "environment": {
                    "DATABASE_PASSWORD": "${DATABASE_PASSWORD}",
                },
                "volumes": [],
            }
        }
    }
    baseline, baseline_validation = _config_transaction(
        compose_path,
        current_config,
    )
    compose_path.write_bytes(baseline.compose_source_bytes)
    compose_path.chmod(baseline.compose_source_mode)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )
    capture_candidate = Mock(
        side_effect=AssertionError("invalid locked candidate must not be captured")
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )

    result = DockerService()._update_container_config_unlocked(
        "kor-travel-geo-postgresql",
        [],
        {"DATABASE_PASSWORD": "new-literal-secret"},
        [],
        [],
        environment_snapshot=baseline.environment,
    )

    assert result["success"] is False
    assert "리터럴로 바꾸면" in result["error"]
    assert compose_path.read_bytes() == baseline.compose_source_bytes
    capture_candidate.assert_not_called()


def test_candidate_failure_restores_exact_baseline_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    original_config: dict[str, object] = {
        "services": {
                "kor-travel-geo-postgres": {
                    "image": "postgres:16",
                    "environment": {"POSTGRES_DB": "before"},
                    "volumes": [],
                }
        }
    }
    baseline, baseline_validation = _config_transaction(
        compose_path, original_config
    )
    compose_path.write_bytes(baseline.compose_source_bytes)
    compose_path.chmod(baseline.compose_source_mode)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    forward = Mock(
        return_value={
            **_compose_success(),
            "success": False,
            "returncode": 1,
            "stderr": "candidate failed",
        }
    )
    recovery = Mock(return_value=_compose_success())
    monkeypatch.setattr(docker_service_module.compose_service, "run", forward)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_run_frozen_recovery",
        recovery,
    )

    result = DockerService()._update_container_config_unlocked(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "after"},
        [],
        [],
        environment_snapshot=baseline.environment,
    )

    assert result["success"] is False
    assert compose_path.read_bytes() == baseline.compose_source_bytes
    assert compose_path.stat().st_mode & 0o777 == baseline.compose_source_mode
    assert recovery.call_args.kwargs["transaction"] is baseline


def test_map_services_share_single_concierge_read_key_source() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services_with_key = {
        service_name
        for service_name, service in compose["services"].items()
        if "KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY"
        in service.get("environment", {})
    }
    assert services_with_key == set(_MAP_FETCH_SERVICES)

    for service_name in _MAP_FETCH_SERVICES:
        environment = compose["services"][service_name]["environment"]
        assert environment["KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_BASE_URL"] == (
            _CONCIERGE_BASE_URL_ENV
        )
        assert (
            environment["KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY"]
            == _CONCIERGE_API_KEY_ENV
        )

    key_lines = [
        line
        for line in (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY=")
    ]
    assert key_lines == ["KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY="]


def test_map_ingestion_services_interpolate_provider_credentials_from_current_env_names() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    provider_keys = {
        "KOR_TRAVEL_MAP_OPINET_API_KEY": _OPINET_API_KEY_ENV,
        "KOR_TRAVEL_MAP_KREX_EX_API_KEY": _KREX_EX_API_KEY_ENV,
        "KOR_TRAVEL_MAP_KREX_GO_API_KEY": _KREX_GO_API_KEY_ENV,
    }
    for key in provider_keys:
        services_with_key = {
            service_name
            for service_name, service in compose["services"].items()
            if key in service.get("environment", {})
        }
        assert services_with_key == set(_MAP_INGESTION_SERVICES)

    for service_name in _MAP_INGESTION_SERVICES:
        environment = compose["services"][service_name]["environment"]
        for key, source_expression in provider_keys.items():
            assert environment[key] == source_expression


def test_map_api_excludes_removed_provider_runtime_credentials() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    api_environment = compose["services"][_MAP_API_SERVICE]["environment"]
    assert _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.isdisjoint(api_environment)


def test_map_features_routes_are_explicitly_enabled_only_for_map_api() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    env_name = "KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED"

    assert services[_MAP_API_SERVICE]["environment"][env_name] == "true"
    assert {
        service_name
        for service_name, service in services.items()
        if env_name in service.get("environment", {})
    } == {_MAP_API_SERVICE}


def test_map_provider_credentials_have_empty_env_example_placeholders() -> None:
    env_example_lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for key in (
        "KOR_TRAVEL_MAP_OPINET_API_KEY",
        "KOR_TRAVEL_MAP_KREX_EX_API_KEY",
        "KOR_TRAVEL_MAP_KREX_GO_API_KEY",
    ):
        assert [line for line in env_example_lines if line.startswith(f"{key}=")] == [
            f"{key}="
        ]
    for key in _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES:
        assert not any(line.startswith(f"{key}=") for line in env_example_lines)


def test_map_pinvi_ops_principal_is_api_only_and_uses_single_secret_source() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    map_environment = services[_MAP_API_SERVICE]["environment"]
    pinvi_environment = services[_PINVI_API_SERVICE]["environment"]

    assert map_environment["KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"] == _OPS_READ_SOURCE
    assert map_environment["KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN"] == _OPS_CANCEL_SOURCE
    assert map_environment["KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN"] == _OPS_FIXTURE_SOURCE
    assert map_environment["KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED"] == (
        "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?"
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED must be explicitly set}"
    )
    assert pinvi_environment["PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN"] == _OPS_READ_SOURCE
    assert pinvi_environment["PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN"] == _OPS_CANCEL_SOURCE
    assert (
        map_environment["KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256"]
        == _CURATION_SNAPSHOT_DIGEST_SOURCE
    )
    assert (
        map_environment[
            "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256"
        ]
        == _CURATION_CUTOVER_MAPPING_DIGEST_SOURCE
    )
    assert (
        pinvi_environment["PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN"]
        == _PINVI_CURATION_SNAPSHOT_SOURCE
    )
    assert (
        pinvi_environment["PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN"]
        == _PINVI_CUTOVER_MAPPING_SOURCE
    )
    assert (
        pinvi_environment["PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL"]
        == _PINVI_MAP_BASE_URL_SOURCE
    )
    assert pinvi_environment["PINVI_ENVIRONMENT"] == (
        "${PINVI_ENVIRONMENT:?PINVI_ENVIRONMENT must be explicitly set}"
    )


def test_manual_feature_create_credential_is_split_between_map_api_and_ui() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    map_api = services[_MAP_API_SERVICE]["environment"]
    map_ui = services[_MAP_UI_SERVICE]["environment"]

    assert map_api["KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256"] == (
        _FEATURE_CREATE_TOKEN_DIGEST_SOURCE
    )
    assert map_ui["KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN"] == (
        _FEATURE_CREATE_TOKEN_SOURCE
    )
    assert "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN" not in map_api
    assert "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256" not in map_ui
    assert {
        service_name
        for service_name, service in services.items()
        if "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256"
        in service.get("environment", {})
    } == {_MAP_API_SERVICE}
    assert {
        service_name
        for service_name, service in services.items()
        if "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN"
        in service.get("environment", {})
    } == {_MAP_UI_SERVICE}

    assert {
        service_name
        for service_name, service in services.items()
        if "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN" in service.get("environment", {})
        or "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN" in service.get("environment", {})
        or "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN" in service.get("environment", {})
        or "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED" in service.get("environment", {})
    } == {_MAP_API_SERVICE}
    assert {
        service_name
        for service_name, service in services.items()
        if "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN" in service.get("environment", {})
        or "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN" in service.get("environment", {})
    } == {_PINVI_API_SERVICE, _PINVI_ADMIN_BOOTSTRAP_SERVICE}
    assert {
        service_name
        for service_name, service in services.items()
        if "KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256"
        in service.get("environment", {})
        or "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256"
        in service.get("environment", {})
    } == {_MAP_API_SERVICE}
    assert {
        service_name
        for service_name, service in services.items()
        if "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN"
        in service.get("environment", {})
        or "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN"
        in service.get("environment", {})
    } == {_PINVI_API_SERVICE}


def test_c6c_env_example_separates_runtime_and_manager_only_credentials() -> None:
    env_example_lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "KTDM_DEPLOYMENT_ENVIRONMENT=local" in env_example_lines
    assert "COMPOSE_PROJECT_NAME=kor-travel-local" in env_example_lines
    assert "PINVI_ENVIRONMENT=development" in env_example_lines
    assert "KTDM_C6C_CONTRACT_GENERATION=c6c-ops-v1" in env_example_lines
    for key in (
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN",
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN",
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN",
    ):
        assert [line for line in env_example_lines if line.startswith(f"{key}=")] == [
            f"{key}="
        ]
    assert "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=false" in env_example_lines
    assert not any(
        line.startswith("PINVI_KOR_TRAVEL_MAP_OPS_") for line in env_example_lines
    )
    assert "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME=admin" in env_example_lines
    for key in (
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
    ):
        assert [line for line in env_example_lines if line.startswith(f"{key}=")] == [
            f"{key}="
        ]
    manager_only_names = {
        "KTDM_C6C_MAP_UI_ADMIN_PASSWORD",
        "KTDM_C6C_PINVI_ADMIN_EMAIL",
        "KTDM_C6C_PINVI_ADMIN_PASSWORD",
    }
    for name in manager_only_names:
        assert any(line.startswith(f"{name}=") for line in env_example_lines)

    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    map_ui_environment = compose["services"][_MAP_UI_SERVICE]["environment"]
    for key in (
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME",
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET",
    ):
        assert map_ui_environment[key] == f"${{{key}:?{key} must be explicitly set}}"
        assert {
            service_name
            for service_name, service in compose["services"].items()
            if key in service.get("environment", {})
        } == {_MAP_UI_SERVICE}
    assert all(
        name not in service.get("environment", {})
        for service in compose["services"].values()
        for name in manager_only_names
    )


def test_update_container_config_recreates_with_compose_and_preserves_host_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "network_mode": "${KTDM_DOCKER_NETWORK_MODE:-host}",
                "environment": {"RUSTFS_ACCESS_KEY": "${RUSTFS_ACCESS_KEY:-rustfsadmin}"},
                "volumes": ["${RUSTFS_DATA_DIR:-/tmp/rustfs}:/data"],
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    baseline, baseline_validation = _config_transaction(compose_path, compose_config)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    compose_run.return_value = _compose_success()

    result = service.update_container_config(
        "rustfs",
        ["${RUSTFS_API_PORT:-12101}:${RUSTFS_API_CONTAINER_PORT:-12101}"],
        {"RUSTFS_ACCESS_KEY": "${RUSTFS_ACCESS_KEY:-rustfsadmin}"},
        ["${RUSTFS_DATA_DIR:-/tmp/rustfs}:/data"],
        [],
    )

    assert result["success"] is True
    saved_service = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"][
        "rustfs"
    ]
    assert saved_service["network_mode"] == "${KTDM_DOCKER_NETWORK_MODE:-host}"
    assert "networks" not in saved_service
    assert compose_run.call_args_list[0].args == (
        ["up", "-d", "--force-recreate", "rustfs"],
    )
    assert compose_run.call_args_list[0].kwargs["capture_output"] is True
    assert compose_run.call_args_list[0].kwargs["mutation_capability"] is not None
    assert compose_run.call_args_list[1].args == (["run", "--rm", "rustfs-init"],)
    assert compose_run.call_args_list[1].kwargs["mutation_capability"] is not None


def test_update_container_config_switches_to_compose_networks_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "kor-travel-geo-postgres": {
                "image": "postgis/postgis:16-3.5",
                "network_mode": "${KTDM_DOCKER_NETWORK_MODE:-host}",
                "volumes": ["pgdata:/var/lib/postgresql/data"],
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    baseline, baseline_validation = _config_transaction(compose_path, compose_config)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )

    def capture_candidate(candidate, **_kwargs):  # type: ignore[no-untyped-def]
        return _config_transaction(compose_path, candidate)[1]

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        capture_candidate,
    )
    compose_run.return_value = _compose_success()

    result = service.update_container_config(
        "kor-travel-geo-postgresql",
        ["5432:5432"],
        {"POSTGRES_DB": "kor_travel_geo"},
        ["pgdata:/var/lib/postgresql/data"],
        ["default"],
    )

    assert result["success"] is True
    saved_service = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"][
        "kor-travel-geo-postgres"
    ]
    assert saved_service["networks"] == ["default"]
    assert "network_mode" not in saved_service
    assert compose_run.call_args.args == (
        ["up", "-d", "--force-recreate", "kor-travel-geo-postgres"],
    )


def _real_compose_config() -> dict[str, object]:
    compose_path = _ROOT / "docker-compose.yml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}


def _real_ports_and_env() -> tuple[list[str], list[tuple[str, str]]]:
    """실제 docker-compose.yml 전 서비스의 ports/environment 값을 모은다.

    파일이 바뀌면 이 값도 같이 바뀌는 living regression guard다 — 새 검증 규칙이
    실제 운영 설정을 오탐으로 막지 않는지 CI에서 항상 확인한다.
    """
    config = _real_compose_config()
    ports: list[str] = []
    envs: list[tuple[str, str]] = []
    for svc in (config.get("services") or {}).values():
        for port in svc.get("ports") or []:
            ports.append(str(port))
        env = svc.get("environment")
        if isinstance(env, dict):
            for key, value in env.items():
                envs.append((str(key), "" if value is None else str(value)))
    return ports, envs


def test_validate_port_mapping_accepts_every_real_compose_port() -> None:
    ports, _ = _real_ports_and_env()
    assert len(ports) >= 15, "docker-compose.yml에서 ports를 못 읽었다 — fixture 확인"
    for port in ports:
        validate_port_mapping(port)  # 실패하면 예외로 바로 드러난다


def test_validate_env_entry_accepts_every_real_compose_env_value_unchanged() -> None:
    """설정 모달을 열고 아무것도 바꾸지 않은 채 저장하면 항상 통과해야 한다."""
    _, envs = _real_ports_and_env()
    assert len(envs) >= 200, "docker-compose.yml에서 environment를 못 읽었다 — fixture 확인"
    for key, value in envs:
        validate_env_entry(key, value, baseline_value=value)  # 실패하면 예외로 바로 드러난다


@pytest.mark.parametrize(
    "raw",
    [
        "5432:5432",
        "${RUSTFS_API_PORT:-12101}:${RUSTFS_API_CONTAINER_PORT:-12101}",
        "127.0.0.1:8080:80",
        "8080/tcp",
        "8080",
        "8000-8010:8000-8010",
        "${VAR}:${VAR}",
    ],
)
def test_validate_port_mapping_accepts_typical_forms(raw: str) -> None:
    validate_port_mapping(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc:80",
        "70000:80",
        "80:700000",
        "0:80",
        "80-70:80",
        "80::80",
        "80:80:80:80",
        "80/ftp",
    ],
)
def test_validate_port_mapping_rejects_bad_forms(raw: str) -> None:
    with pytest.raises(ContainerConfigValidationError):
        validate_port_mapping(raw)


@pytest.mark.parametrize("raw", ["default", "kor-travel-geo-net", "a", "a.b-c_d"])
def test_validate_network_name_accepts_typical_forms(raw: str) -> None:
    validate_network_name(raw)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", " default", "default ", "bad name", "-leading-dash", "${VAR}"],
)
def test_validate_network_name_rejects_bad_forms(raw: str) -> None:
    with pytest.raises(ContainerConfigValidationError):
        validate_network_name(raw)


@pytest.mark.parametrize(
    "key",
    [
        "POSTGRES_PASSWORD",
        "RUSTFS_ACCESS_KEY",
        "KOR_TRAVEL_MAP_OPINET_API_KEY",
        "SOME_TOKEN",
        "SERVICE_CREDENTIAL",
    ],
)
def test_validate_env_entry_rejects_regression_from_interpolated_baseline_to_literal(
    key: str,
) -> None:
    """이미 `${...}`로 분리돼 있던 값을 리터럴로 되돌리면 거부한다."""
    with pytest.raises(ContainerConfigValidationError, match="원래"):
        validate_env_entry(
            key,
            "an-actual-literal-secret-value",
            baseline_value=f"${{{key}:-dev-default}}",
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("POSTGRES_PASSWORD", "${KOR_TRAVEL_GEO_POSTGRES_PASSWORD:-addr}"),
        ("RUSTFS_ACCESS_KEY", "${RUSTFS_ACCESS_KEY}"),
    ],
)
def test_validate_env_entry_accepts_unchanged_interpolated_secret(
    key: str, value: str
) -> None:
    validate_env_entry(key, value, baseline_value=value)


def test_validate_env_entry_allows_literal_flag_whose_baseline_was_never_interpolated() -> None:
    """key 이름에 'API_KEY'가 들어 있어도 원래부터 리터럴 불리언이면 건드리지 않는다.

    실제로 `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`가 정적 key-이름
    휴리스틱만으로는 오탐(전수 검증 중 실제로 발견)이었다.
    """
    validate_env_entry(
        "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED",
        "false",
        baseline_value="true",
    )


def test_validate_env_entry_requires_interpolation_for_new_sensitive_key_without_baseline() -> None:
    """baseline을 모르는(신규 key) 경우에는 sensitive 이름 휴리스틱으로 방어한다."""
    with pytest.raises(ContainerConfigValidationError, match="비밀 성격"):
        validate_env_entry("NEW_SERVICE_API_KEY", "literal-secret", baseline_value=None)


def test_validate_env_entry_rejects_literal_url_credential_even_for_innocuous_key_name() -> None:
    """key 이름이 sensitive 목록에 안 걸려도 값에 literal credential이 있으면 거부한다."""
    with pytest.raises(ContainerConfigValidationError, match="접속 문자열"):
        validate_env_entry(
            "KOR_TRAVEL_MAP_PG_DSN",
            "postgresql+asyncpg://map:realpassword123@127.0.0.1:5432/kor_travel_map",
        )


def test_validate_env_entry_allows_existing_dsn_default_wrapping_convention() -> None:
    """docker-compose.yml 전체가 쓰는 관행: DSN 전체가 하나의 `${VAR:-...}` 안에 있다.

    baseline과 완전히 같은(수정 없는) 값으로만 통과한다 — `validate_container_config_update`가
    실제로 호출하는 방식과 같다. baseline 없이(None) 이 값을 그대로 받으면 지금은
    거부된다(아래 `test_validate_env_entry_rejects_dsn_literal_credential_without_baseline`).
    """
    value = (
        "${KOR_TRAVEL_MAP_DOCKER_PG_DSN:-postgresql+asyncpg://"
        "krtour_map:krtour_map_dev_password@127.0.0.1:5432/krtour_map}"
    )
    validate_env_entry("KOR_TRAVEL_MAP_PG_DSN", value, baseline_value=value)


def test_validate_env_entry_rejects_dsn_literal_credential_without_baseline() -> None:
    """baseline을 모르면(값이 unchanged인지 증명할 수 없으면) literal credential은 항상 거부한다."""
    value = (
        "${KOR_TRAVEL_MAP_DOCKER_PG_DSN:-postgresql+asyncpg://"
        "krtour_map:krtour_map_dev_password@127.0.0.1:5432/krtour_map}"
    )
    with pytest.raises(ContainerConfigValidationError, match="접속 문자열"):
        validate_env_entry("KOR_TRAVEL_MAP_PG_DSN", value, baseline_value=None)


def test_validate_env_entry_rejects_any_change_to_a_value_with_embedded_literal_credential() -> None:
    """DSN 관행값이라도 baseline과 달라지면(감싸는 이름이 같아도) literal credential은
    거부한다 — 이 UI로 DSN 기본값에 새 비밀을 커밋시키지 않기 위한 의도적으로 엄격한
    정책이다. `.env`를 직접 고치는 것이 올바른 경로다."""
    baseline = (
        "${KOR_TRAVEL_MAP_DOCKER_PG_DSN:-postgresql+asyncpg://"
        "krtour_map:krtour_map_dev_password@127.0.0.1:5432/krtour_map}"
    )
    changed = baseline.replace("krtour_map_dev_password", "changed_password")
    with pytest.raises(ContainerConfigValidationError, match="접속 문자열"):
        validate_env_entry("KOR_TRAVEL_MAP_PG_DSN", changed, baseline_value=baseline)


def test_validate_env_entry_rejects_fabricated_variable_name_hiding_new_credential() -> None:
    """적대적 리뷰에서 재현된 우회: 아무 key에나, 지어낸 이름을 `${...}`로 감싸기만
    하면 credential 스캔을 피해 갈 수 있었다(key 이름이 sensitive하지 않고 baseline도
    보간이 아니어도). `${...}` 블록을 통째로 지우고 스캔하던 이전 구현에서 실제로
    통과했던 입력이다.
    """
    with pytest.raises(ContainerConfigValidationError, match="접속 문자열"):
        validate_env_entry(
            "POSTGRES_DB",
            "${TOTALLY_MADE_UP_VAR_NAME:-http://admin:SuperSecretPassw0rd@internal-host/x}",
            baseline_value="kor_travel_geo",
        )


def test_validate_env_entry_rejects_fabricated_variable_name_replacing_protected_reference() -> None:
    """key 이름이 `_is_sensitive_key`에 걸리지 않아도(예: DB URL류 이름), 값이 DSN이고
    baseline이 이미 `${...}`로 보호되던 경우 다른 이름으로 지어낸 `${...}` 참조로
    바꾸면(여전히 "보간 형태"라 규칙 1의 재보간 요구 자체는 통과하지만) credential
    스캔(규칙 3)에서 잡혀야 한다. 규칙 1(재보간 요구)만으로는 이 우회를 막지 못한다 —
    baseline이 `${REAL_VAR:-old}`이고 새 값이 `${FAKE_VAR:-new-secret}`이면 둘 다
    "보간 형태"이기 때문이다. (key 이름 자체가 sensitive한 경우의 동등한 시나리오는
    `test_validate_env_entry_rejects_fabricated_variable_name_for_non_url_secret`가
    규칙 1의 일반화된 default-비교로 더 일찍 잡는다.)
    """
    with pytest.raises(ContainerConfigValidationError, match="접속 문자열"):
        validate_env_entry(
            "GEO_DB_CONNECTION",
            "${SOME_UNRELATED_MADE_UP_NAME:-postgresql://"
            "admin:N3wR3alProdPassw0rd!@prod-db.internal/db}",
            baseline_value="${GEO_DB_CONNECTION:-devpw}",
        )


def test_validate_env_entry_rejects_fabricated_variable_name_for_non_url_secret() -> None:
    """규칙 3(URL credential 스캔)은 `scheme://user:pass@` 형태에만 반응한다 — 단일
    리터럴 비밀(URL이 아닌 값, 예: `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}`)은
    스캔 대상이 아니라서, 규칙 1의 "여전히 보간 형태면 통과"만으로는 지어낸 변수명 +
    새 리터럴 default 우회가 막히지 않았다(적대적 리뷰 이후 재검토에서 재현됨). 규칙 1의
    일반화(“sensitive key면 default 리터럴 자체가 baseline과 같아야 한다”)로 막는다.
    """
    with pytest.raises(ContainerConfigValidationError, match="비밀 성격 값"):
        validate_env_entry(
            "GF_SECURITY_ADMIN_PASSWORD",
            "${TOTALLY_MADE_UP_NAME:-h4x0r-literal-secret}",
            baseline_value="${GRAFANA_ADMIN_PASSWORD:-admin}",
        )


def test_validate_env_entry_rejects_new_literal_default_under_same_var_name() -> None:
    """변수 이름을 그대로 두어도, sensitive key의 default 리터럴을 바꾸면 거부한다 —
    이 UI로 새 비밀 리터럴이 git 추적 파일에 커밋되는 것을 막는 것이 목적이지,
    변수 이름이 바뀌었는지는 부수적이다."""
    with pytest.raises(ContainerConfigValidationError, match="비밀 성격 값"):
        validate_env_entry(
            "GF_SECURITY_ADMIN_PASSWORD",
            "${GRAFANA_ADMIN_PASSWORD:-newsecret123}",
            baseline_value="${GRAFANA_ADMIN_PASSWORD:-admin}",
        )


def test_validate_env_entry_allows_renaming_reference_when_default_literal_unchanged() -> None:
    """sensitive key라도, 참조하는 변수 이름만 바뀌고 default 리터럴 자체는 baseline과
    동일하면 허용한다 — git에 새로 노출되는 리터럴이 없기 때문이다."""
    validate_env_entry(
        "GF_SECURITY_ADMIN_PASSWORD",
        "${GRAFANA_ADMIN_PASSWORD_V2:-admin}",
        baseline_value="${GRAFANA_ADMIN_PASSWORD:-admin}",
    )


def test_validate_env_entry_allows_dropping_default_entirely_for_sensitive_key() -> None:
    """sensitive key의 default를 아예 없애는 것(`${OTHER_NAME}`, default 없음)은 git에
    새 리터럴을 남기지 않으므로 허용한다."""
    validate_env_entry(
        "GF_SECURITY_ADMIN_PASSWORD",
        "${GRAFANA_ADMIN_PASSWORD}",
        baseline_value="${GRAFANA_ADMIN_PASSWORD:-admin}",
    )


def test_validate_env_entry_allows_default_change_for_non_sensitive_key() -> None:
    """key 이름이 sensitive하지 않으면(예: 포트 번호), 지어낸 변수명 + 새 default도
    자유롭다 — 비밀이 아니므로 이 정책의 대상이 아니다."""
    validate_env_entry(
        "GF_SERVER_HTTP_PORT",
        "${SOME_OTHER_PORT_VAR:-12206}",
        baseline_value="${GRAFANA_PORT:-12205}",
    )


@pytest.mark.parametrize(
    "value",
    [
        "  ${POSTGRES_PASSWORD}  ",
        "${POSTGRES_PASSWORD}\t",
        "\n${POSTGRES_PASSWORD:-addr}",
    ],
)
def test_validate_env_entry_ignores_incidental_whitespace_around_interpolation(
    value: str,
) -> None:
    """터미널/.env에서 복붙하면 흔히 붙는 앞뒤 공백 때문에 '리터럴로 바꿨다'는
    오해의 소지가 있는 메시지가 뜨면 안 된다."""
    validate_env_entry(
        "POSTGRES_PASSWORD", value, baseline_value="${POSTGRES_PASSWORD:-addr}"
    )


def test_validate_env_entry_allows_partial_interpolation_of_just_the_password() -> None:
    validate_env_entry(
        "KOR_TRAVEL_MAP_PG_DSN",
        "postgresql+asyncpg://map:${MAP_DB_PASSWORD}@127.0.0.1:5432/map",
    )


def test_validate_env_entry_rejects_bad_key_name() -> None:
    with pytest.raises(ContainerConfigValidationError):
        validate_env_entry("1_BAD_NAME", "value")
    with pytest.raises(ContainerConfigValidationError):
        validate_env_entry("BAD-NAME", "value")


def test_validate_container_config_update_checks_ports_env_and_networks() -> None:
    with pytest.raises(ContainerConfigValidationError):
        validate_container_config_update(ports=["not-a-port"], env={}, networks=[])
    with pytest.raises(ContainerConfigValidationError):
        validate_container_config_update(ports=[], env={}, networks=["bad name"])
    with pytest.raises(ContainerConfigValidationError):
        validate_container_config_update(
            ports=[], env={"POSTGRES_PASSWORD": "literal"}, networks=[]
        )
    # 유효한 입력은 조용히 통과한다.
    validate_container_config_update(
        ports=["5432:5432"],
        env={"POSTGRES_DB": "kor_travel_geo"},
        networks=["default"],
    )


@pytest.mark.parametrize(
    "value",
    ["--auth-host=trust", "--auth-host=scram-sha-256 --auth-local=trust"],
)
def test_validate_container_config_update_rejects_pinvi_initdb_auth_drift(
    value: str,
) -> None:
    with pytest.raises(
        ContainerConfigValidationError,
        match="initdb authentication policy",
    ):
        validate_container_config_update(
            ports=[],
            env={"POSTGRES_INITDB_ARGS": value},
            networks=[],
            baseline_env={"POSTGRES_INITDB_ARGS": _PINVI_POSTGRES_INITDB_ARGS},
            service_name="pinvi-postgres",
        )


def test_update_container_config_validates_before_lock_or_environment_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """형식이 잘못된 입력은 lock도, 환경 스냅샷도 건드리기 전에 거부되어야 한다."""
    service = DockerService()
    monkeypatch.setattr(
        docker_service_module,
        "c6c_deployment_lock_from_environment",
        Mock(side_effect=AssertionError("lock을 잡기 전에 검증에서 걸러야 한다")),
    )
    monkeypatch.setattr(
        docker_service_module,
        "_capture_compose_environment_snapshot",
        Mock(side_effect=AssertionError("환경 스냅샷보다 검증이 먼저여야 한다")),
    )

    with pytest.raises(ContainerConfigValidationError):
        service.update_container_config("rustfs", ["not-a-port"], {}, [], [])


def test_non_api_sdk_mutation_validates_environment_before_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DockerService()
    client = Mock()
    monkeypatch.setattr(service, "_get_client", client)
    monkeypatch.setattr(
        docker_service_module,
        "assert_manager_mutation_allowed",
        Mock(side_effect=DeploymentContractError("invalid manager environment")),
    )

    with pytest.raises(DeploymentContractError, match="invalid manager environment"):
        service.control_container("rustfs", "restart")

    client.assert_not_called()


def _prepare_candidate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compose_config: dict[str, object],
) -> tuple[DockerService, Path, Mock]:
    services = compose_config.get("services")
    if isinstance(services, dict) and {
        _MAP_API_SERVICE,
        _MAP_UI_SERVICE,
        _PINVI_API_SERVICE,
    }.issubset(services):
        monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
        monkeypatch.setenv("PINVI_ENVIRONMENT", "development")
        monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_READ_TOKEN", "")
        monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN", "")
        monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN", "")
        monkeypatch.setenv("PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN", "")
        monkeypatch.setenv("PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN", "")
        monkeypatch.setenv("KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED", "false")
        monkeypatch.setenv("KOR_TRAVEL_MAP_UI_ADMIN_USERNAME", _MAP_UI_USERNAME)
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH", _MAP_UI_PASSWORD_HASH
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET", _MAP_UI_SESSION_SECRET
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET", _MAP_ADMIN_PROXY_SECRET
        )
        monkeypatch.setenv("KOR_TRAVEL_MAP_API_SERVICE_TOKEN", _MAP_SERVICE_TOKEN)
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN",
            "manual-feature-create-test-token-0000",
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256",
            hashlib.sha256(
                b"manual-feature-create-test-token-0000"
            ).hexdigest(),
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET",
            _MAP_CURSOR_SIGNING_SECRET,
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY",
            "test-map-geo-api-key",
        )
        monkeypatch.setenv("KOR_TRAVEL_MAP_POSTGRES_DB", "kor_travel_map")
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB", "kor_travel_map_dagster"
        )
        monkeypatch.setenv("KOR_TRAVEL_MAP_POSTGRES_USER", "test_map_admin")
        monkeypatch.setenv("KOR_TRAVEL_MAP_POSTGRES_PASSWORD", "test-map-postgres-password")
        monkeypatch.setenv("PINVI_POSTGRES_PASSWORD", "pinvi-contract-password")
        monkeypatch.setenv(
            "PINVI_DOCKER_DATABASE_URL",
            "postgresql+asyncpg://pinvi:pinvi-contract-password@127.0.0.1:12800/pinvi",
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN",
            "postgresql://test_map_admin:test-map-postgres-password@127.0.0.1:12700/kor_travel_map",
        )
        monkeypatch.setenv("KOR_TRAVEL_MAP_MIGRATOR_PASSWORD", "test-map-migrator")
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD", "test-map-api-runtime"
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD", "test-map-dagster-runtime"
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER", "test_map_dagster_metadata"
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD", "test-map-dagster-metadata"
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN",
            "postgresql+asyncpg://ktm_feature_migrator:test-map-migrator@127.0.0.1:12700/kor_travel_map",
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN",
            "postgresql+asyncpg://ktm_feature_api_runtime:test-map-api-runtime@127.0.0.1:12700/kor_travel_map",
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN",
            "postgresql+asyncpg://ktm_feature_dagster_runtime:test-map-dagster-runtime@127.0.0.1:12700/kor_travel_map",
        )
        monkeypatch.setenv(
            "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
            "postgresql://test_map_dagster_metadata:test-map-dagster-metadata@127.0.0.1:12700/kor_travel_map_dagster",
        )
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        yaml.safe_dump(compose_config, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        docker_service_module, "_get_compose_path", lambda: str(compose_path)
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
        "kor_travel_docker_manager.services.compose_service.get_override_path",
        lambda: str(tmp_path / "missing.override.yml"),
    )
    monkeypatch.setattr(
        "kor_travel_docker_manager.services.compose_service.c6c_deployment_lock",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        docker_service_module,
        "get_compose_config",
        lambda _path=None: compose_config,
    )
    monkeypatch.setattr(
        docker_service_module, "assert_manager_mutation_allowed", Mock()
    )
    compose_run = Mock()
    monkeypatch.setattr(docker_service_module.compose_service, "run", compose_run)
    return DockerService(), compose_path, compose_run


def test_non_api_config_update_rejects_candidate_before_write_or_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError):
        service.update_container_config(
            "rustfs",
            [],
            {"ALIAS": "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"},
            [],
            [],
        )

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_rustfs_config_rejects_root_env_bind_before_write_or_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {"rustfs": {"image": "rustfs/rustfs:latest"}}
    )
    root_env = tmp_path / ".env"
    root_env.write_text("SAFE=value\n", encoding="utf-8")
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()

    with pytest.raises(
        ComposeCandidateContractError,
        match="volume configuration is immutable",
    ):
        service.update_container_config(
            "rustfs",
            [],
            {},
            ["./.env:/run/manager.env:ro"],
            [],
        )

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_rustfs_config_rejects_missing_bind_without_creating_or_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {"rustfs": {"image": "rustfs/rustfs:latest"}}
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    missing = tmp_path / "future-secret"

    with pytest.raises(
        ComposeCandidateContractError,
        match="volume configuration is immutable",
    ):
        service.update_container_config(
            "rustfs",
            [],
            {},
            ["./future-secret:/run/future-secret:ro"],
            [],
        )

    assert not missing.exists()
    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


@pytest.mark.parametrize(
    "volumes",
    [
        ["/var/run/docker.sock:/var/run/docker.sock:ro", "/sys:/sys:rw"],
        [
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": "/sys",
                "target": "/sys",
                "read_only": False,
            },
        ],
    ],
)
def test_cadvisor_config_rejects_writable_system_bind_without_mutation(
    volumes: list[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = [
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "/sys:/sys:ro",
    ]
    compose_config = _compose_with_canonical_c6c_services(
        {
            "cadvisor": {
                "image": "gcr.io/cadvisor/cadvisor:v0.52.1",
                "volumes": baseline,
            }
        }
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()

    with pytest.raises(
        ComposeCandidateContractError,
        match="volume configuration is immutable",
    ):
        service.update_container_config("cadvisor", [], {}, volumes, [])

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_system_bind_snapshot_change_before_write_keeps_compose_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    monkeypatch.setattr(
        docker_service_module,
        "revalidate_candidate_system_bind_snapshots",
        Mock(
            side_effect=ComposeCandidateContractError(
                "compose candidate system bind identity changed during the request"
            )
        ),
    )

    with pytest.raises(ComposeCandidateContractError, match="identity changed"):
        service.update_container_config("rustfs", [], {"SAFE": "updated"}, [], [])

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_preflight_rejection_restore_failure_is_typed_post_mutation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    original_mode = compose_path.stat().st_mode & 0o777
    original_error = ComposeCandidateContractError(
        "compose candidate source changed during the config request"
    )

    def reject_candidate(
        _candidate: object, **_kwargs: object
    ) -> ValidatedComposeCandidate:
        compose_path.write_text(
            "services:\n  attacker:\n    volumes:\n    - /tmp:/host\n",
            encoding="utf-8",
        )
        raise original_error

    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        reject_candidate,
    )
    restore = Mock(side_effect=OSError("atomic compose restore failed"))
    monkeypatch.setattr(docker_service_module, "_atomic_write", restore)

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.update_container_config("rustfs", [], {"SAFE": "updated"}, [], [])

    assert caught.value.original_error is original_error
    assert caught.value.recovery_attempted is True
    assert caught.value.recovery_succeeded is False
    assert caught.value.recovery_error == "atomic compose restore failed"
    assert caught.value.restoration == {
        "config_restored": False,
        "runtime_restored": False,
        "runtime_recovery_attempted": False,
        "durable_config_mutation": True,
        "error": "atomic compose restore failed",
    }
    assert compose_path.read_bytes() != original
    restore.assert_called_once_with(str(compose_path), original, mode=original_mode)
    compose_run.assert_not_called()


def test_system_bind_snapshot_change_before_subprocess_restores_compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    original = compose_path.read_bytes()
    original_mode = compose_path.stat().st_mode & 0o777
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    compose_run.side_effect = ComposeCandidateContractError(
        "compose candidate system bind identity changed during the request"
    )

    with pytest.raises(ComposeCandidateContractError, match="identity changed"):
        service.update_container_config("rustfs", [], {"SAFE": "updated"}, [], [])

    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == original_mode


@pytest.mark.parametrize(
    ("recovery_result", "recovery_succeeded", "recovery_error"),
    [
        (_compose_success(), True, None),
        (
            {
                **_compose_success(),
                "success": False,
                "returncode": 9,
                "stderr": "persisted runtime recovery failed",
            },
            False,
            "persisted runtime recovery failed",
        ),
    ],
)
def test_rustfs_second_preflight_drift_restores_bytes_mode_and_runtime(
    recovery_result: dict[str, object],
    recovery_succeeded: bool,
    recovery_error: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "environment": {"ORIGINAL": "yes"},
                "volumes": [],
            }
        }
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    compose_path.chmod(0o640)
    original = compose_path.read_bytes()
    original_error = ComposeCandidateContractError(
        "compose resolved volume graph changed during the request"
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    compose_run.side_effect = [
        _compose_success(),
        original_error,
        recovery_result,
    ]

    with pytest.raises(ComposePostMutationContractError) as caught:
        service.update_container_config(
            "rustfs",
            [],
            {"UPDATED": "yes"},
            [],
            [],
        )

    assert caught.value.original_error is original_error
    assert caught.value.recovery_attempted is True
    assert caught.value.recovery_succeeded is recovery_succeeded
    assert caught.value.recovery_error == recovery_error
    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == 0o640
    assert caught.value.restoration is not None
    assert caught.value.restoration["config_restored"] is True
    assert caught.value.restoration["runtime_restored"] is recovery_succeeded
    assert compose_run.call_count == 3


def test_non_api_config_update_rejects_resolved_candidate_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {"rustfs": {"image": "rustfs/rustfs:latest", "volumes": []}}
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    candidate_error = ComposeCandidateContractError(
        "resolved compose candidate leaks a protected C6c reference"
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        Mock(side_effect=candidate_error),
    )
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError) as caught:
        service.update_container_config("rustfs", [], {}, [], [])

    assert caught.value is candidate_error
    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_non_api_config_reset_rejects_candidate_before_write_or_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {"rustfs": {"image": "rustfs/rustfs:latest"}}
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    service._default_compose_config = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "command": ["worker", "KTDM_C6C_CONTRACT_GENERATION"],
            }
        }
    }
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError):
        service.reset_container_config("rustfs")

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


@pytest.mark.parametrize(
    "message",
    [
        "compose candidate raw volume graph differs from persisted compose",
        "compose candidate resolved volume graph differs from persisted compose",
    ],
)
def test_reset_rejects_persisted_volume_graph_drift_without_mutation(
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config = _compose_with_canonical_c6c_services(
        {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "volumes": [],
            }
        }
    )
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    service._default_compose_config = deepcopy(compose_config)
    original = compose_path.read_bytes()
    candidate_error = ComposeCandidateContractError(message)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        Mock(side_effect=candidate_error),
    )

    with pytest.raises(ComposeCandidateContractError) as caught:
        service.reset_container_config("rustfs")

    assert caught.value is candidate_error
    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_missing_non_api_container_create_rejects_candidate_before_recreate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_config: dict[str, object] = {
        "services": {
            "rustfs": {
                "image": "rustfs/rustfs:latest",
                "labels": {"leak": "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN"},
            }
        }
    }
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    client = Mock()
    client.containers.get.side_effect = docker_service_module.NotFound("missing")
    monkeypatch.setattr(service, "_get_client", Mock(return_value=client))
    original = compose_path.read_bytes()

    with pytest.raises(ComposeCandidateContractError):
        service.control_container("rustfs", "start")

    assert compose_path.read_bytes() == original
    compose_run.assert_not_called()


def test_config_recreate_failure_restores_exact_file_and_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        b"services:\n"
        b"  rustfs:\n"
        b"    image: rustfs/rustfs:latest\n"
        b"    environment:\n"
        b"      ORIGINAL: exact-format-preserved\n"
        b"    volumes:\n"
        b"    - rustfs:/data\n"
    )
    compose_config = yaml.safe_load(original.decode("utf-8"))
    service, compose_path, compose_run = _prepare_candidate_transaction(
        tmp_path, monkeypatch, compose_config
    )
    compose_path.write_bytes(original)
    compose_path.chmod(0o640)
    baseline, baseline_validation = _config_transaction(compose_path, compose_config)
    baseline = replace(
        baseline,
        compose_source_bytes=original,
        compose_source_mode=0o640,
    )
    baseline_validation = replace(
        baseline_validation,
        transaction_snapshot=baseline,
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_transaction_unlocked",
        Mock(return_value=(baseline, baseline_validation)),
    )
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_capture_candidate_transaction_unlocked",
        _candidate_capture_for(compose_path),
    )
    compose_run.return_value = {
        **_compose_success(),
        "success": False,
        "returncode": 1,
        "stderr": "candidate recreate failed",
    }
    frozen_recovery = Mock(return_value=_compose_success())
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_run_frozen_recovery",
        frozen_recovery,
    )

    result = service.update_container_config(
        "rustfs",
        ["12101:12101"],
        {"CHANGED": "yes"},
        ["rustfs:/data"],
        [],
    )

    assert result["success"] is False
    assert compose_path.read_bytes() == original
    assert compose_path.stat().st_mode & 0o777 == 0o640
    assert result["restoration"] == {
        "config_restored": True,
        "runtime_restored": True,
        "command": ["docker", "compose"],
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "error": None,
    }
    assert compose_run.call_count == 1
    frozen_recovery.assert_called_once()


def test_config_runtime_restore_failure_preserves_compose_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services:\n  rustfs:\n    image: rustfs:old\n", encoding="utf-8")
    monkeypatch.setattr(docker_service_module, "_get_compose_path", lambda: str(compose_path))
    restore_run = {
        "success": False,
        "returncode": 9,
        "command": ["docker", "compose", "up", "rustfs"],
        "stdout": "restore stdout",
        "stderr": "restore stderr",
    }
    frozen_run = Mock(return_value=restore_run)
    monkeypatch.setattr(
        docker_service_module.compose_service,
        "_run_frozen_recovery",
        frozen_run,
    )
    transaction = _config_transaction(
        compose_path,
        yaml.safe_load(compose_path.read_text(encoding="utf-8")),
    )[0]

    restoration = DockerService()._restore_compose_transaction(
        compose_path.read_bytes(),
        0o640,
        "rustfs",
        transaction,
    )

    assert restoration == {
        "config_restored": True,
        "runtime_restored": False,
        "command": ["docker", "compose", "up", "rustfs"],
        "returncode": 9,
        "stdout": "restore stdout",
        "stderr": "restore stderr",
        "error": "restore stderr",
    }


def test_missing_container_start_preserves_nested_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DockerService()
    client = Mock()
    client.containers.get.side_effect = docker_service_module.NotFound("missing")
    monkeypatch.setattr(service, "_get_client", lambda: client)
    restoration = {
        "config_restored": True,
        "runtime_restored": False,
        "returncode": 9,
        "stdout": "restore stdout",
        "stderr": "restore stderr",
        "error": "restore stderr",
    }
    monkeypatch.setattr(
        service,
        "_update_container_config_unlocked",
        Mock(
            return_value={
                "success": False,
                "error": "candidate recreate failed",
                "command": ["docker", "compose"],
                "returncode": 1,
                "stdout": "candidate stdout",
                "stderr": "candidate stderr",
                "restoration": restoration,
            }
        ),
    )

    environment_snapshot = Mock()
    environment_snapshot.compose_path = "/tmp/docker-compose.yml"
    result = service._control_container_unlocked(
        "rustfs",
        "start",
        environment_snapshot=environment_snapshot,
    )

    assert result["success"] is False
    assert result["restoration"] == restoration
    assert result["returncode"] == 1
    assert result["stderr"] == "candidate stderr"


def test_reset_config_uses_one_locked_update_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DockerService()
    service._default_compose_config = {
        "services": {
            "rustfs": {
                "ports": ["12101:12101"],
                "environment": {"RESET": "yes"},
                "volumes": ["rustfs:/data"],
            }
        }
    }
    monkeypatch.setattr(
        docker_service_module, "assert_manager_mutation_allowed", lambda **_kwargs: "local"
    )
    lock_snapshot = object()
    lock = Mock(return_value=nullcontext(lock_snapshot))
    monkeypatch.setattr(
        docker_service_module,
        "c6c_deployment_lock_from_environment",
        lock,
    )
    lock_binding = Mock()
    monkeypatch.setattr(
        docker_service_module,
        "assert_environment_snapshot_matches_c6c_lock",
        lock_binding,
    )
    update = Mock(return_value={"success": True})
    monkeypatch.setattr(service, "_update_container_config_unlocked", update)

    result = service.reset_container_config("rustfs")

    assert result["success"] is True
    lock.assert_called_once()
    lock_binding.assert_called_once()
    assert lock_binding.call_args.args[1] is lock_snapshot
    update.assert_called_once()
    assert update.call_args.args == (
        "rustfs",
        ["12101:12101"],
        {"RESET": "yes"},
        ["rustfs:/data"],
        [],
    )
    assert update.call_args.kwargs["replacement_service_config"] == (
        service._default_compose_config["services"]["rustfs"]
    )
    assert isinstance(
        update.call_args.kwargs["environment_snapshot"],
        ComposeEnvironmentSnapshot,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda service: docker_service_module.save_compose_config({}),
        lambda service: service.control_container("rustfs", "restart"),
        lambda service: service.update_container_config("rustfs", [], {}, [], []),
        lambda service: service.reset_container_config("rustfs"),
    ],
)
def test_all_docker_mutation_entries_bind_transaction_to_selected_c6c_lock(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[DockerService], object],
) -> None:
    service = DockerService()
    snapshot = ComposeEnvironmentSnapshot(
        effective={},
        env_path="/tmp/.env",
        compose_path="/tmp/docker-compose.yml",
        override_path="/tmp/docker-compose.override.yml",
        env_file_identity=ComposeEnvFileIdentity(exists=False),
        env_file_bytes=b"",
    )
    lock_snapshot = object()
    monkeypatch.setattr(
        docker_service_module,
        "c6c_deployment_lock_from_environment",
        Mock(return_value=nullcontext(lock_snapshot)),
    )
    monkeypatch.setattr(
        docker_service_module,
        "_capture_compose_environment_snapshot",
        Mock(return_value=snapshot),
    )
    mismatch = Mock(side_effect=DeploymentContractError("lock snapshot mismatch"))
    monkeypatch.setattr(
        docker_service_module,
        "assert_environment_snapshot_matches_c6c_lock",
        mismatch,
    )
    monkeypatch.setattr(
        docker_service_module,
        "get_compose_config",
        Mock(return_value={"services": {"rustfs": {"environment": {}}}}),
    )

    with pytest.raises(DeploymentContractError, match="lock snapshot mismatch"):
        mutation(service)

    mismatch.assert_called_once_with(snapshot, lock_snapshot)
