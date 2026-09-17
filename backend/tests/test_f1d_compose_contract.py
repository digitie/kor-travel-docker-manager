"""F1D의 일회성 schema bootstrap Compose 경계를 회귀 고정한다."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services import c6c_deployment as c6c_deployment_module
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services.c6c_deployment import (
    _CANDIDATE_ALLOWED_SYSTEM_BINDS,
    C6cBuildProvenance,
    ComposeCandidateContractError,
    DeploymentContractError,
    _candidate_volume_mounts,
    _validate_feature_create_credentials,
    _validate_map_production_secret_values,
    derive_curation_service_principal_environment,
    validate_compose_candidate_protected_values,
    validate_concierge_ui_canonical_compose_boundary,
    validate_map_postgres_runtime_secret_isolation,
    validate_pinvi_postgres_runtime_secret_isolation,
    validate_resolved_c6c_build_provenance,
    validate_resolved_compose_candidate_protected_values,
    validate_runtime_secret_isolation,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeEnvFileIdentity,
    ComposeEnvironmentSnapshot,
    ComposeExternalInputSnapshot,
    ComposeService,
    ComposeTransactionSnapshot,
)
from kor_travel_docker_manager.services.map_application_300 import (
    Application300Contract,
)
from kor_travel_docker_manager.services.map_application_300_candidate import (
    MapApplication300Candidate,
)
from kor_travel_docker_manager.services.pinned_runtime_rebuild import (
    CandidateRuntimeBuild,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
)
from kor_travel_docker_manager.services.registry import (
    load_compose_bind_allowlist,
)

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_PATH = _ROOT / "docker-compose.yml"
_MAP_RUNTIME_SERVICES = (
    "kor-travel-map-api",
    "kor-travel-map-ui",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
)
_MAP_DATABASE_ONESHOT_SERVICES = (
    "kor-travel-map-dagster-db-init",
    "kor-travel-map-db-role-bootstrap",
    "kor-travel-map-application-fresh-300",
    "kor-travel-map-application-fresh-finalize",
    "kor-travel-map-dagster-storage-migrate",
)
_PINVI_RUNTIME_SERVICES = ("pinvi-api", "pinvi-web", "pinvi-dagster")
_PINVI_BOOTSTRAP_MAP_ENVIRONMENT = frozenset(
    {
        "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL",
        "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN",
    }
)
_PINVI_POSTGRES_IMAGE = (
    "postgis/postgis@sha256:8b33190b6486ab9905dea999171817c1ac461733a7078dd4c836091c6e6b5d40"
)
_FEATURE_CREATE_TOKEN = "manual-feature-create-contract-token-0000"
_MAP_API_IMAGE_ID = f"sha256:{'1' * 64}"
_MAP_DAGSTER_IMAGE_ID = f"sha256:{'2' * 64}"
_MAP_POSTGRES_IMAGE_ID = f"sha256:{'3' * 64}"


def _runtime_secret_config() -> SimpleNamespace:
    return SimpleNamespace(
        map_container="map-api",
        pinvi_container="pinvi-api",
        map_ui_container="map-ui",
        read_token="r" * 32,
        cancel_token="c" * 32,
        fixture_token="f" * 32,
        map_admin_proxy_secret="a" * 32,
        map_service_token="s" * 32,
        map_cursor_signing_secret="u" * 32,
        map_geo_api_key="g" * 32,
        feature_create_token="feature-token",
        feature_create_enabled="true",
        map_ui_password_hash="pbkdf2_sha256$100000$salt$digest",
        map_ui_session_secret="q" * 32,
        contract_generation="c6c-ops-v1",
        smoke=SimpleNamespace(
            map_ui_username="admin",
            map_ui_password="map-password",
            pinvi_admin_email="admin@example.test",
            pinvi_admin_password="pinvi-password",
        ),
    )


def _runtime_environment(values: dict[str, str]) -> list[str]:
    return [f"{name}={value}" for name, value in values.items()]


def test_map_runtime_requires_the_image_entrypoint_and_empty_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        c6c_deployment_module,
        "_validate_map_production_secrets",
        lambda _config: None,
    )
    config = _runtime_secret_config()
    map_environment = {
        c6c_deployment_module._MAP_READ_ENV: config.read_token,
        c6c_deployment_module._MAP_CANCEL_ENV: config.cancel_token,
        c6c_deployment_module._MAP_FIXTURE_ENV: config.fixture_token,
        c6c_deployment_module._MAP_REQUIRED_ENV: "true",
        c6c_deployment_module._MAP_ADMIN_PROXY_ENV: config.map_admin_proxy_secret,
        c6c_deployment_module._MAP_SERVICE_TOKEN_ENV: config.map_service_token,
        c6c_deployment_module._MAP_CURSOR_SIGNING_SECRET_ENV: (
            config.map_cursor_signing_secret
        ),
        c6c_deployment_module._MAP_GEO_API_KEY_SOURCE_ENV: config.map_geo_api_key,
        c6c_deployment_module._MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV: hashlib.sha256(
            config.feature_create_token.encode("utf-8")
        ).hexdigest(),
        c6c_deployment_module._MAP_FEATURE_CREATE_ENABLED_ENV: (
            config.feature_create_enabled
        ),
        **c6c_deployment_module._MAP_PRODUCTION_API_LITERAL_VALUES,
    }
    runtime_configs = {
        config.map_container: {
            "Env": _runtime_environment(map_environment),
            "Entrypoint": ["/app/docker/api-entrypoint.sh"],
            "Cmd": None,
        },
        config.pinvi_container: {
            "Env": _runtime_environment(
                {
                    c6c_deployment_module._PINVI_READ_ENV: config.read_token,
                    c6c_deployment_module._PINVI_CANCEL_ENV: config.cancel_token,
                }
            )
        },
        config.map_ui_container: {
            "Env": _runtime_environment(
                {
                    c6c_deployment_module._MAP_UI_USERNAME_ENV: (
                        config.smoke.map_ui_username
                    ),
                    c6c_deployment_module._MAP_UI_PASSWORD_HASH_ENV: (
                        config.map_ui_password_hash
                    ),
                    c6c_deployment_module._MAP_UI_SESSION_SECRET_ENV: (
                        config.map_ui_session_secret
                    ),
                    c6c_deployment_module._MAP_ADMIN_PROXY_ENV: (
                        config.map_admin_proxy_secret
                    ),
                    c6c_deployment_module._MAP_UI_GEO_API_KEY_ENV: (
                        config.map_geo_api_key
                    ),
                    c6c_deployment_module._MAP_FEATURE_CREATE_TOKEN_ENV: (
                        config.feature_create_token
                    ),
                }
            )
        },
        "kor-travel-map-dagster-latest": {
            "Env": _runtime_environment(
                {
                    c6c_deployment_module._MAP_GEO_API_KEY_SOURCE_ENV: (
                        config.map_geo_api_key
                    )
                }
            )
        },
        "kor-travel-map-dagster-daemon-latest": {
            "Env": _runtime_environment(
                {
                    c6c_deployment_module._MAP_GEO_API_KEY_SOURCE_ENV: (
                        config.map_geo_api_key
                    )
                }
            )
        },
    }

    validate_runtime_secret_isolation(runtime_configs, config)

    broken = deepcopy(runtime_configs)
    broken[config.map_container]["Entrypoint"] = None
    with pytest.raises(DeploymentContractError, match="immutable image entrypoint"):
        validate_runtime_secret_isolation(broken, config)

    broken = deepcopy(runtime_configs)
    broken[config.map_container]["Cmd"] = ["./docker/api-entrypoint.sh"]
    with pytest.raises(DeploymentContractError, match="immutable image entrypoint"):
        validate_runtime_secret_isolation(broken, config)

    broken = deepcopy(runtime_configs)
    del broken[config.map_container]["Cmd"]
    with pytest.raises(DeploymentContractError, match="immutable image entrypoint"):
        validate_runtime_secret_isolation(broken, config)


def test_pinvi_postgres_data_bind_is_in_canonical_candidate_allowlist() -> None:
    assert load_compose_bind_allowlist()[
        ("pinvi-postgres", "/var/lib/postgresql/data", False)
    ] == "${PINVI_PGDATA:-/home/digitie/pinvi-data/pgdata}"


def test_pinvi_role_bootstrap_source_bind_is_in_canonical_candidate_allowlist() -> None:
    assert load_compose_bind_allowlist()[
        ("pinvi-db-runtime-role", "/opt/pinvi/bootstrap-pinvi-runtime-role.sh", True)
    ] == ("${PINVI_REPO_DIR:-../pinvi}/infra/postgres/bootstrap-pinvi-runtime-role.sh")


def test_pinvi_role_bootstrap_entrypoint_interprets_a_non_executable_source(
    tmp_path: Path,
) -> None:
    source = _source_compose()
    services = source["services"]
    assert isinstance(services, dict)
    role_service = services["pinvi-db-runtime-role"]
    assert isinstance(role_service, dict)
    assert role_service["entrypoint"] == [
        "sh",
        "-ec",
        'export POSTGRES_PASSWORD="$$(cat /run/secrets/pinvi-postgres-password)"\n'
        "exec sh /opt/pinvi/bootstrap-pinvi-runtime-role.sh\n",
    ]

    script = tmp_path / "bootstrap-pinvi-runtime-role.sh"
    script.write_text('test "$POSTGRES_PASSWORD" = "root-password"\n', encoding="utf-8")
    script.chmod(0o444)
    assert script.stat().st_mode & 0o111 == 0
    completed = subprocess.run(
        ["sh", str(script)],
        env={"POSTGRES_PASSWORD": "root-password"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_concierge_postgres_data_bind_is_in_canonical_candidate_allowlist() -> None:
    assert load_compose_bind_allowlist()[
        ("kor-travel-concierge-postgres", "/var/lib/postgresql/data", False)
    ] == (
        "${KOR_TRAVEL_CONCIERGE_PGDATA:-/home/digitie/kor-travel-concierge-data/pgdata}"
    )


def _compose_contract_environment() -> dict[str, str]:
    return {
        **os.environ,
        "COMPOSE_PROJECT_NAME": "ktdm-f1d-compose-contract",
        "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET": "a" * 32,
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET": "s" * 32,
        "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN": "c" * 32,
        "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN": "f" * 32,
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN": "r" * 32,
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN": "t" * 32,
        "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": _FEATURE_CREATE_TOKEN,
        "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256": hashlib.sha256(
            _FEATURE_CREATE_TOKEN.encode("utf-8")
        ).hexdigest(),
        "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN": "n" * 32,
        "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN": "m" * 32,
        "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY": "v" * 32,
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": "300",
        "KOR_TRAVEL_MAP_API_IMAGE": _MAP_API_IMAGE_ID,
        "KOR_TRAVEL_MAP_DAGSTER_IMAGE": _MAP_DAGSTER_IMAGE_ID,
        "KOR_TRAVEL_MAP_POSTGRES_IMAGE_ID": _MAP_POSTGRES_IMAGE_ID,
        "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR": ("/tmp/ktdm-map-application-final-permit"),
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR": ("/tmp/ktdm-map-dagster-storage-permit"),
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR": ("/tmp/ktdm-map-fresh-migrate-fence"),
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR": (
            "/tmp/ktdm-map-fresh-finalize-fence"
        ),
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PAIRED_RECEIPT_SHA256": "4" * 64,
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256": "5" * 64,
        "KOR_TRAVEL_MAP_POSTGRES_DB": "map_contract",
        "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB": "map_contract_dagster",
        "KOR_TRAVEL_MAP_POSTGRES_USER": "map_contract_admin",
        "KOR_TRAVEL_MAP_POSTGRES_PASSWORD": "map-contract-postgres-password",
        "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN": (
            "postgresql://map_contract_admin:map-contract-postgres-password@"
            "127.0.0.1:12700/map_contract"
        ),
        "KOR_TRAVEL_MAP_MIGRATOR_PASSWORD": "map-contract-migrator-password",
        "KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD": "map-contract-api-password",
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD": "map-contract-dagster-password",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER": "map_contract_dagster_metadata",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD": "map-contract-dagster-metadata-password",
        "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_migrator:map-contract-migrator-password@"
            "127.0.0.1:12700/map_contract"
        ),
        "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_api_runtime:map-contract-api-password@"
            "127.0.0.1:12700/map_contract"
        ),
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_dagster_runtime:map-contract-dagster-password@"
            "127.0.0.1:12700/map_contract"
        ),
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
            "postgresql://map_contract_dagster_metadata:map-contract-dagster-metadata-password@"
            "127.0.0.1:12700/map_contract_dagster"
        ),
        "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH": ("pbkdf2_sha256$100000$test-salt$test-digest"),
        "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME": "admin",
        "KOR_TRAVEL_MAP_UI_SESSION_SECRET": "u" * 32,
        "KOR_TRAVEL_CONCIERGE_API_KEYS": "concierge-old-key,concierge-bff-key",
        "KOR_TRAVEL_CONCIERGE_APP_ENV": "production",
        "KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED": "true",
        "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY": "concierge-bff-key",
        "KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY": "concierge-browser-key",
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME": "admin",
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH": (
            "pbkdf2_sha256$100000$test-salt$test-digest"
        ),
        "KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET": "c" * 32,
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET": "p" * 32,
        "KOR_TRAVEL_CONCIERGE_UI_TRUST_FORWARDED_IPS": "false",
        "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_ORIGINS": "https://concierge.example.test",
        "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_API_BASE_URL": "",
        "PINVI_PGDATA": "/mnt/f/dev/kor-travel-map-codex",
        "PINVI_POSTGRES_DB": "pinvi",
        "PINVI_POSTGRES_USER": "pinvi_contract_root",
        "PINVI_POSTGRES_PASSWORD": "pinvi-contract-postgres-password",
        "PINVI_APP_DB_USER": "pinvi_contract_app",
        "PINVI_APP_DB_PASSWORD": "pinvi-contract-app-password",
        "PINVI_APP_SCHEMA_OWNER": "pinvi_contract_app_owner",
        "PINVI_MIGRATION_OWNER": "pinvi_contract_migration_owner",
        "PINVI_MIGRATOR_DB_USER": "pinvi_contract_migrator",
        "PINVI_MIGRATOR_DB_PASSWORD": "pinvi-contract-migrator-password",
        "PINVI_ENVIRONMENT": "production",
    }


def _source_compose() -> dict[str, Any]:
    document = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _map_application_300_candidate(
    sources: PinnedRuntimeSourceMaterialization,
) -> MapApplication300Candidate:
    contract = Application300Contract(
        application_head="300",
        reference_manifest_sha256="1" * 64,
        postgres_image_id=_MAP_POSTGRES_IMAGE_ID,
        source_catalog_sha256="2" * 64,
        destination_catalog_sha256="3" * 64,
        seed_sha256="4" * 64,
        privileged_residue_sha256="5" * 64,
        source_alembic_version_sha256="6" * 64,
        destination_alembic_version_sha256="7" * 64,
        runtime_invariants_sql_sha256="8" * 64,
    )
    map_source = sources.source_for("map")
    return MapApplication300Candidate(
        receipt_sha256="9" * 64,
        api_receipt_sha256="a" * 64,
        candidate_commit=map_source.revision,
        candidate_git_tree=map_source.tree,
        api_image_id=_MAP_API_IMAGE_ID,
        dagster_image_id=_MAP_DAGSTER_IMAGE_ID,
        postgres_image_id=_MAP_POSTGRES_IMAGE_ID,
        dagster_config_sha256="b" * 64,
        dagster_yaml_sha256="c" * 64,
        application_contract=contract,
        application_contract_sha256="d" * 64,
        launch_contract_sha256="e" * 64,
        webserver_argv_prefix=("/usr/local/bin/dagster-webserver",),
        webserver_port_minimum=1,
        webserver_port_maximum=65535,
        daemon_argv=("/usr/local/bin/dagster-daemon", "run"),
        storage_migration_argv=("/usr/local/bin/ktm-dagster-storage", "migrate"),
    )


def _compose_fragment(*service_names: str) -> dict[str, object]:
    """선택 service와 그 dependency 이름을 실제 Compose resolver로 해석한다."""

    source_services = _source_compose()["services"]
    assert isinstance(source_services, dict)
    services: dict[str, object] = {
        name: deepcopy(source_services[name]) for name in service_names
    }
    pending = list(service_names)
    while pending:
        name = pending.pop()
        service = services[name]
        assert isinstance(service, dict)
        depends_on = service.get("depends_on") or {}
        assert isinstance(depends_on, dict)
        for dependency in depends_on:
            if dependency not in services:
                # DB service는 F1D target identity의 일부이므로 실제 Compose 정의를
                # 유지한다. 나머지 dependency의 실행 내용은 이 계약의 대상이 아니다.
                if dependency in {"pinvi-postgres", "pinvi-db-init"}:
                    services[dependency] = deepcopy(source_services[dependency])
                else:
                    # 실제 resolver가 dependency graph를 검증하게 이름만 최소 stub으로 둔다.
                    services[dependency] = {"image": "alpine:3.20"}

    fragment: dict[str, object] = {"services": services}
    if "kor-travel-map-postgres" in services or "pinvi-postgres" in services:
        source_secrets = _source_compose().get("secrets")
        assert isinstance(source_secrets, dict)
        fragment["secrets"] = {}
        if "kor-travel-map-postgres" in services:
            fragment["secrets"]["kor-travel-map-postgres-password"] = deepcopy(
                source_secrets["kor-travel-map-postgres-password"]
            )
        if "pinvi-postgres" in services:
            fragment["secrets"]["pinvi-postgres-password"] = deepcopy(
                source_secrets["pinvi-postgres-password"]
            )
    return fragment


def _resolved_compose(
    *service_names: str,
    environment_update: dict[str, str] | None = None,
    strip_env_file_for: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose가 없어 resolved Compose 계약을 실행할 수 없음")

    environment = _compose_contract_environment()
    if environment_update is not None:
        environment.update(environment_update)
    environment = derive_curation_service_principal_environment(environment)
    compose_fragment = _compose_fragment(*service_names)
    fragment_services = compose_fragment["services"]
    assert isinstance(fragment_services, dict)
    for service_name in strip_env_file_for:
        service = fragment_services.get(service_name)
        assert isinstance(service, dict)
        service.pop("env_file", None)
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--profile",
            "bootstrap",
            "--file",
            "-",
            "config",
            "--format",
            "json",
        ],
        cwd=_ROOT,
        env=environment,
        input=yaml.safe_dump(compose_fragment, sort_keys=False),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert isinstance(document, dict)
    return document


def test_concierge_ui_canonical_contract_matches_raw_and_resolved_compose() -> None:
    environment = _compose_contract_environment()
    source = _source_compose()
    source_services = source["services"]
    assert isinstance(source_services, dict)

    c6c_deployment_module._validate_concierge_ui_canonical_contract(
        source_services,
        environment,
        resolved=False,
    )

    resolved = _resolved_compose(
        "kor-travel-concierge-api",
        "kor-travel-concierge-ui",
        # 이 검증의 대상은 canonical explicit 환경이다. API의 provider source file은
        # 별도 계약이므로 test host의 실제 source `.env`를 읽지 않는다.
        strip_env_file_for=frozenset({"kor-travel-concierge-api"}),
    )
    resolved_services = resolved["services"]
    assert isinstance(resolved_services, dict)
    c6c_deployment_module._validate_concierge_ui_canonical_contract(
        resolved_services,
        environment,
        resolved=True,
    )
    validate_concierge_ui_canonical_compose_boundary(
        source,
        resolved,
        environment=environment,
    )

    raw_env_file_drift = deepcopy(source_services)
    raw_ui = raw_env_file_drift["kor-travel-concierge-ui"]
    assert isinstance(raw_ui, dict)
    raw_ui["env_file"] = ["attacker.env"]
    with pytest.raises(DeploymentContractError, match="must not load an env_file"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            raw_env_file_drift,
            environment,
            resolved=False,
        )

    raw_command_drift = deepcopy(source_services)
    raw_command_ui = raw_command_drift["kor-travel-concierge-ui"]
    assert isinstance(raw_command_ui, dict)
    raw_command_ui["command"] = ["npm", "run", "dev"]
    with pytest.raises(DeploymentContractError, match="canonical production command"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            raw_command_drift,
            environment,
            resolved=False,
        )

    root_authority_drift = dict(environment)
    root_authority_drift["KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY"] = "not-in-api-key-set"
    with pytest.raises(DeploymentContractError, match="Manager root environment is invalid"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            source_services,
            root_authority_drift,
            resolved=False,
        )

    api_auth_drift = dict(environment)
    api_auth_drift["KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED"] = "false"
    with pytest.raises(DeploymentContractError, match="Manager root environment is invalid"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            source_services,
            api_auth_drift,
            resolved=False,
        )

    ui_port_drift = dict(environment)
    ui_port_drift["KOR_TRAVEL_CONCIERGE_UI_PORT"] = "12606"
    with pytest.raises(DeploymentContractError, match="canonical production port"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            source_services,
            ui_port_drift,
            resolved=False,
        )

    malformed_hash_drift = dict(environment)
    malformed_hash_drift["KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH"] = "not-a-password-hash"
    with pytest.raises(DeploymentContractError, match="Manager root environment is invalid"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            source_services,
            malformed_hash_drift,
            resolved=False,
        )

    resolved_proxy_drift = deepcopy(resolved_services)
    resolved_api = resolved_proxy_drift["kor-travel-concierge-api"]
    assert isinstance(resolved_api, dict)
    resolved_api_environment = resolved_api["environment"]
    assert isinstance(resolved_api_environment, dict)
    resolved_api_environment["KTC_ADMIN_PROXY_SECRET"] = "different-proxy-authority"
    with pytest.raises(DeploymentContractError, match="share the canonical Manager proxy"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            resolved_proxy_drift,
            environment,
            resolved=True,
        )

    resolved_network_drift = deepcopy(resolved_services)
    resolved_ui = resolved_network_drift["kor-travel-concierge-ui"]
    assert isinstance(resolved_ui, dict)
    resolved_ui["network_mode"] = "bridge"
    with pytest.raises(DeploymentContractError, match="canonical host network"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            resolved_network_drift,
            environment,
            resolved=True,
        )

    resolved_port_drift = deepcopy(resolved_services)
    resolved_api = resolved_port_drift["kor-travel-concierge-api"]
    assert isinstance(resolved_api, dict)
    resolved_api_command = resolved_api["command"]
    assert isinstance(resolved_api_command, list)
    resolved_api_command[-1] = "12602"
    with pytest.raises(DeploymentContractError, match="canonical loopback BFF command"):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            resolved_port_drift,
            environment,
            resolved=True,
        )


def test_map_dagster_db_init_passes_conninfo_as_psql_dbname() -> None:
    service = _source_compose()["services"]["kor-travel-map-dagster-db-init"]
    assert isinstance(service, dict)
    command = service["command"]
    assert isinstance(command, list)
    assert len(command) == 1
    script = command[0]
    assert isinstance(script, str)
    assert (
        'psql --dbname "$$KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN" '
        "--set ON_ERROR_STOP=1"
    ) in script
    assert (
        'psql "$$KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN" --dbname postgres'
    ) not in script


def test_resolved_map_dagster_services_require_candidate_storage_migration() -> None:
    resolved = _resolved_compose(
        "kor-travel-map-api",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        "kor-travel-map-dagster-storage-migrate",
    )
    services = resolved["services"]
    assert isinstance(services, dict)

    migration = services["kor-travel-map-dagster-storage-migrate"]
    assert migration["image"] == f"sha256:{'2' * 64}"
    assert "build" not in migration
    assert migration["command"] == ["/usr/local/bin/ktm-dagster-storage", "migrate"]
    assert migration["restart"] == "no"
    assert migration["network_mode"] == "host"
    assert migration["environment"] == {
        "DAGSTER_DISABLE_TELEMETRY": "yes",
        "DAGSTER_HOME": "/opt/dagster/dagster_home",
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL": (
            "postgresql://map_contract_dagster_metadata:map-contract-dagster-metadata-password@"
            "127.0.0.1:12700/map_contract_dagster"
        ),
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256": "5" * 64,
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PAIRED_RECEIPT_SHA256": "4" * 64,
        "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_IMAGE_ID": f"sha256:{'2' * 64}",
    }
    assert migration["depends_on"]["kor-travel-map-postgres"]["condition"] == (
        "service_healthy"
    )
    assert migration["extra_hosts"] == ["host.docker.internal=host-gateway"]
    assert migration["volumes"] == [
        {
            "type": "bind",
            "source": "/tmp/ktdm-map-dagster-storage-permit",
            "target": "/run/kor-travel-map-dagster-storage-permit",
            "read_only": True,
            # Compose v2는 기본 bind option을 생략하고 v5는 이를 명시한다.
            "bind": migration["volumes"][0]["bind"],
        }
    ]
    assert migration["volumes"][0]["bind"] in ({}, {"create_host_path": True})

    for service_name in (
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
    ):
        dependency = services[service_name]["depends_on"]
        assert dependency["kor-travel-map-dagster-storage-migrate"]["condition"] == (
            "service_completed_successfully"
        )
        assert services[service_name]["image"] == migration["image"]


def test_map_source_dagster_profile_fallback_is_allowed_at_exact_paths() -> None:
    api_environment = dict(compose_service_module._MAP_SOURCE_V3_API_ENVIRONMENT)
    api_environment[
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET"
    ] = compose_service_module._MAP_SOURCE_V4_CURSOR_ENV_VALUE
    services: dict[str, object] = {
        "api": {"environment": api_environment},
        "frontend": {
            "environment": dict(compose_service_module._MAP_SOURCE_V3_UI_ENVIRONMENT)
        },
    }
    for service_name in (
        "dagster-db-init",
        "dagster-db-init-fresh-300",
        "dagster",
        "dagster-daemon",
        "dagster-storage-migrate",
    ):
        services[service_name] = {
            "environment": {
                "KOR_TRAVEL_MAP_DAGSTER_PROFILE": (
                    "local-dev"
                    if service_name == "dagster-db-init-fresh-300"
                    else compose_service_module._MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE
                )
            }
        }

    compose_service_module._validate_map_source_protected_scalar_tree(
        {"services": services},
        contract_version=4,
    )


def test_map_source_dagster_profile_fallback_cannot_move_to_another_path() -> None:
    api_environment = dict(compose_service_module._MAP_SOURCE_V3_API_ENVIRONMENT)
    api_environment[
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET"
    ] = compose_service_module._MAP_SOURCE_V4_CURSOR_ENV_VALUE
    services: dict[str, object] = {
        "api": {
            "environment": {
                **api_environment,
                "KOR_TRAVEL_MAP_DAGSTER_PROFILE": (
                    compose_service_module._MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE
                ),
            }
        },
        "frontend": {
            "environment": dict(compose_service_module._MAP_SOURCE_V3_UI_ENVIRONMENT)
        },
    }
    for service_name in (
        "dagster-db-init",
        "dagster-db-init-fresh-300",
        "dagster",
        "dagster-daemon",
        "dagster-storage-migrate",
    ):
        services[service_name] = {"environment": {}}

    with pytest.raises(DeploymentContractError, match="outside its exact path"):
        compose_service_module._validate_map_source_protected_scalar_tree(
            {"services": services},
            contract_version=4,
        )


def test_map_source_dagster_profile_bare_placeholder_cannot_be_added() -> None:
    api_environment = dict(compose_service_module._MAP_SOURCE_V3_API_ENVIRONMENT)
    api_environment[
        "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET"
    ] = compose_service_module._MAP_SOURCE_V4_CURSOR_ENV_VALUE
    services: dict[str, object] = {
        "api": {"environment": api_environment},
        "frontend": {
            "environment": {
                **compose_service_module._MAP_SOURCE_V3_UI_ENVIRONMENT,
                "UNEXPECTED_PROFILE": (
                    "${KOR_TRAVEL_MAP_DAGSTER_PROFILE:-attacker}"
                ),
            }
        },
    }
    for service_name in (
        "dagster-db-init",
        "dagster-db-init-fresh-300",
        "dagster",
        "dagster-daemon",
        "dagster-storage-migrate",
    ):
        services[service_name] = {
            "environment": {
                "KOR_TRAVEL_MAP_DAGSTER_PROFILE": (
                    "local-dev"
                    if service_name == "dagster-db-init-fresh-300"
                    else compose_service_module._MAP_SOURCE_DAGSTER_PROFILE_FALLBACK_VALUE
                )
            }
        }

    with pytest.raises(DeploymentContractError, match="outside its exact path"):
        compose_service_module._validate_map_source_protected_scalar_tree(
            {"services": services},
            contract_version=4,
        )


def test_resolved_pinvi_api_has_no_implicit_schema_mutation_or_bootstrap_secret() -> None:
    resolved = _resolved_compose("pinvi-api", "pinvi-admin-bootstrap")
    services = resolved["services"]
    assert isinstance(services, dict)

    api = services["pinvi-api"]
    assert api["command"] == [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "12801",
        "--workers",
        "1",
    ]
    assert "alembic" not in json.dumps(api["command"]).lower()

    bootstrap = services["pinvi-admin-bootstrap"]
    assert bootstrap["profiles"] == ["bootstrap"]
    assert bootstrap["image"] == api["image"]
    assert "build" not in bootstrap
    assert bootstrap["command"] == ["pinvi-admin-bootstrap"]
    assert bootstrap["restart"] == "no"
    assert bootstrap["network_mode"] == "host"
    assert "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE" not in bootstrap["environment"]
    for name in (
        "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL",
        "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN",
    ):
        assert bootstrap["environment"][name] == api["environment"][name]
    assert {
        "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN",
    }.isdisjoint(bootstrap["environment"])
    assert api["environment"]["PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN"] == "n" * 32
    assert (
        api["environment"]["PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN"]
        == "m" * 32
    )


def test_tvn40_curation_service_principals_are_api_only_and_digest_derived() -> None:
    """Map에는 digest만, PinVi ordinary API에는 원시 pair만 전달한다."""

    source = _source_compose()
    services = source["services"]
    assert isinstance(services, dict)
    environment = derive_curation_service_principal_environment(
        _compose_contract_environment()
    )
    map_api = services["kor-travel-map-api"]["environment"]
    pinvi_api = services["pinvi-api"]["environment"]
    assert isinstance(map_api, dict)
    assert isinstance(pinvi_api, dict)
    assert map_api["KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256"] == (
        "${KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256:-}"
    )
    assert map_api[
        "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256"
    ] == "${KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256:-}"
    assert pinvi_api["PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN"] == (
        "${PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN:-}"
    )
    assert pinvi_api["PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN"] == (
        "${PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN:-}"
    )
    assert environment["KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256"] == (
        hashlib.sha256(("n" * 32).encode("utf-8")).hexdigest()
    )
    assert environment[
        "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256"
    ] == hashlib.sha256(("m" * 32).encode("utf-8")).hexdigest()

    names = {
        "KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256",
        "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256",
        "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN",
        "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN",
    }
    for service_name, service in services.items():
        assert isinstance(service, dict)
        service_environment = service.get("environment")
        if not isinstance(service_environment, dict):
            continue
        found = names.intersection(service_environment)
        expected = (
            {
                "KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256",
                "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256",
            }
            if service_name == "kor-travel-map-api"
            else {
                "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN",
                "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN",
            }
            if service_name == "pinvi-api"
            else set()
        )
        assert found == expected


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        (
            {"PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN": ""},
            "configured together",
        ),
        (
            {"PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN": "short"},
            "at least 32 characters",
        ),
        (
            {
                "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN": "n" * 32,
            },
            "must differ",
        ),
        (
            {
                "KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256": "0" * 64,
            },
            "must be derived",
        ),
    ),
)
def test_tvn40_curation_service_principal_derivation_fails_closed(
    updates: dict[str, str],
    message: str,
) -> None:
    environment = _compose_contract_environment()
    environment.update(updates)

    with pytest.raises(DeploymentContractError, match=message):
        derive_curation_service_principal_environment(environment)


def test_manual_feature_create_credentials_are_derived_from_one_raw_source() -> None:
    environment = _compose_contract_environment()

    _validate_feature_create_credentials(environment, require_nonempty=True)

    environment["KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256"] = "0" * 64
    with pytest.raises(DeploymentContractError, match="must be derived"):
        _validate_feature_create_credentials(environment, require_nonempty=True)


def test_manual_feature_create_credential_collision_is_rejected_before_reset() -> None:
    environment = _compose_contract_environment()
    environment["KTDM_DEPLOYMENT_ENVIRONMENT"] = "rehearsal"
    environment["KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN"] = environment[
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN"
    ]
    environment["KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256"] = hashlib.sha256(
        environment["KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN"].encode("utf-8")
    ).hexdigest()

    with pytest.raises(
        DeploymentContractError,
        match=(
            "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN must differ from "
            "KOR_TRAVEL_MAP_API_SERVICE_TOKEN"
        ),
    ):
        _validate_map_production_secret_values(environment)


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        (
            {"KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": ""},
            "configured together",
        ),
        (
            {"KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256": "bad"},
            "lowercase SHA-256 hex",
        ),
        (
            {"KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN": "short"},
            "at least 32 characters",
        ),
    ),
)
def test_manual_feature_create_credentials_fail_closed(
    updates: dict[str, str],
    message: str,
) -> None:
    environment = _compose_contract_environment()
    environment.update(updates)

    with pytest.raises(DeploymentContractError, match=message):
        _validate_feature_create_credentials(environment, require_nonempty=True)


def _bootstrap_candidate(tmp_path: Path) -> tuple[dict[str, object], dict[str, str], Path]:
    """F1D bootstrap candidate + 그것을 통과시키는 환경 + root .env.

    `test_frozen_bootstrap_compose_contract_passes_raw_and_resolved_c6c_validation`의
    설정 블록을 그대로 옮긴 것이다(본문 무변경). 배포 진입점에 결박된 GM-17 A 검사들이
    같은 환경을 필요로 하는데, 40줄을 복제하면 그 사본이 곧 원본과 갈라진다.
    """

    source = _source_compose()
    assert "x-pinvi-map-ops-validation" not in source
    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    candidate = _compose_fragment(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
    )
    environment = _compose_contract_environment()
    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    map_pgdata = tmp_path / "map-pgdata"
    map_pgdata.mkdir()
    environment["KOR_TRAVEL_MAP_PGDATA"] = str(map_pgdata)
    map_source = tmp_path / "map-source"
    bootstrap_script = map_source / "docker" / "postgres-role-bootstrap.sh"
    bootstrap_script.parent.mkdir(parents=True)
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    credential_preflight = map_source / "scripts" / "database-credential-preflight.sh"
    credential_preflight.parent.mkdir(parents=True)
    credential_preflight.write_text(
        "#!/bin/sh\n"
        "# Map-owned helper names the runtime credentials it validates.\n"
        "required_name=KOR_TRAVEL_MAP_MIGRATOR_PASSWORD\n"
        "metadata_name=KOR_TRAVEL_MAP_DAGSTER_PG_URL\n",
        encoding="utf-8",
    )
    environment["KOR_TRAVEL_MAP_REPO_DIR"] = str(map_source)
    pinvi_source = tmp_path / "pinvi-source"
    pinvi_pgdata = tmp_path / "pinvi-pgdata"
    pinvi_pgdata.mkdir()
    role_bootstrap_script = pinvi_source / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh"
    role_bootstrap_script.parent.mkdir(parents=True)
    role_bootstrap_script.write_text(
        "#!/bin/sh\nruntime=PINVI_APP_DB_PASSWORD\nmigrator=PINVI_MIGRATOR_DB_PASSWORD\n",
        encoding="utf-8",
    )
    environment["PINVI_REPO_DIR"] = str(pinvi_source)
    environment["PINVI_PGDATA"] = str(pinvi_pgdata)
    for environment_name, directory_name in (
        ("KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR", "application-permit"),
        ("KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR", "metadata-permit"),
        ("KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR", "root-fence"),
        ("KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR", "finalize-fence"),
    ):
        directory = tmp_path / directory_name
        directory.mkdir()
        environment[environment_name] = str(directory)

    return candidate, environment, root_env


def test_frozen_bootstrap_compose_contract_passes_raw_and_resolved_c6c_validation(
    tmp_path: Path,
) -> None:
    """F1D reset 전에 bootstrap의 실제 profile/production 환경을 정적으로 고정한다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    source = _source_compose()
    assert "x-pinvi-map-ops-validation" not in source
    map_source = Path(environment["KOR_TRAVEL_MAP_REPO_DIR"])
    pinvi_source = Path(environment["PINVI_REPO_DIR"])
    credential_preflight = map_source / "scripts" / "database-credential-preflight.sh"
    role_bootstrap_script = (
        pinvi_source / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh"
    )
    map_pgdata = Path(environment["KOR_TRAVEL_MAP_PGDATA"])
    pinvi_pgdata = Path(environment["PINVI_PGDATA"])
    raw_snapshots = validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )
    role_bootstrap_script.write_text(
        f"#!/bin/sh\nleaked_value={environment['PINVI_APP_DB_PASSWORD']}\n",
        encoding="utf-8",
    )
    with pytest.raises(DeploymentContractError, match="bind source leaks C6c data"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    role_bootstrap_script.write_text(
        "#!/bin/sh\nruntime=PINVI_APP_DB_PASSWORD\nmigrator=PINVI_MIGRATOR_DB_PASSWORD\n",
        encoding="utf-8",
    )
    credential_preflight.write_text(
        f"#!/bin/sh\nleaked_value={environment['KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET']}\n",
        encoding="utf-8",
    )
    with pytest.raises(DeploymentContractError, match="bind source leaks C6c data"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    credential_preflight.write_text(
        "#!/bin/sh\n"
        "# Map-owned helper names the runtime credentials it validates.\n"
        "required_name=KOR_TRAVEL_MAP_MIGRATOR_PASSWORD\n"
        "metadata_name=KOR_TRAVEL_MAP_DAGSTER_PG_URL\n",
        encoding="utf-8",
    )
    raw_image_drift = deepcopy(candidate)
    raw_image_services = raw_image_drift["services"]
    assert isinstance(raw_image_services, dict)
    raw_image_fresh = raw_image_services["kor-travel-map-application-fresh-300"]
    assert isinstance(raw_image_fresh, dict)
    raw_image_fresh["image"] = "attacker.invalid/map-application:stale"
    with pytest.raises(DeploymentContractError, match="image provenance"):
        validate_compose_candidate_protected_values(
            raw_image_drift,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    fresh_drift = deepcopy(candidate)
    fresh_services = fresh_drift["services"]
    assert isinstance(fresh_services, dict)
    fresh_service = fresh_services["kor-travel-map-application-fresh-300"]
    assert isinstance(fresh_service, dict)
    fresh_environment = fresh_service["environment"]
    assert isinstance(fresh_environment, dict)
    fresh_environment["KOR_TRAVEL_MAP_API_SERVICE_TOKEN"] = (
        "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?KOR_TRAVEL_MAP_API_SERVICE_TOKEN must be explicitly set}"
    )
    with pytest.raises(DeploymentContractError, match="application 300 service environment"):
        validate_compose_candidate_protected_values(
            fresh_drift,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    resolved = _resolved_compose(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
        environment_update={
            "KOR_TRAVEL_MAP_PGDATA": str(map_pgdata),
            "KOR_TRAVEL_MAP_REPO_DIR": str(map_source),
            "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR": environment[
                "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR"
            ],
            "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR": environment[
                "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR"
            ],
            "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR": environment[
                "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR"
            ],
            "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR": environment[
                "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR"
            ],
                "PINVI_REPO_DIR": str(pinvi_source),
                "PINVI_PGDATA": str(pinvi_pgdata),
            },
        )
    assert (
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )
        == raw_snapshots
    )

    resolved_image_drift = deepcopy(resolved)
    resolved_image_services = resolved_image_drift["services"]
    assert isinstance(resolved_image_services, dict)
    resolved_image_fresh = resolved_image_services["kor-travel-map-application-fresh-finalize"]
    assert isinstance(resolved_image_fresh, dict)
    resolved_image_fresh["image"] = "attacker.invalid/map-application:stale"
    with pytest.raises(DeploymentContractError, match="image provenance"):
        validate_resolved_compose_candidate_protected_values(
            resolved_image_drift,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    resolved_environment_drift = deepcopy(resolved)
    resolved_environment_services = resolved_environment_drift["services"]
    assert isinstance(resolved_environment_services, dict)
    resolved_environment_fresh = resolved_environment_services[
        "kor-travel-map-application-fresh-finalize"
    ]
    assert isinstance(resolved_environment_fresh, dict)
    resolved_fresh_environment = resolved_environment_fresh["environment"]
    assert isinstance(resolved_fresh_environment, dict)
    resolved_fresh_environment["UNRELATED_FRESH_SETTING"] = "x"
    with pytest.raises(DeploymentContractError, match="environment is invalid"):
        validate_resolved_compose_candidate_protected_values(
            resolved_environment_drift,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    empty_tuning_environment = dict(environment)
    empty_tuning_environment["PINVI_POSTGRES_SHARED_BUFFERS"] = ""
    assert (
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=empty_tuning_environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )
        == raw_snapshots
    )

    drifted = deepcopy(resolved)
    drifted_services = drifted["services"]
    assert isinstance(drifted_services, dict)
    drifted_pinvi_api = drifted_services["pinvi-api"]
    assert isinstance(drifted_pinvi_api, dict)
    drifted_pinvi_environment = drifted_pinvi_api["environment"]
    assert isinstance(drifted_pinvi_environment, dict)
    drifted_pinvi_environment["PINVI_DATABASE_URL"] = (
        "postgresql+asyncpg://pinvi_contract_app:pinvi-contract-app-password@"
        "127.0.0.1:12800/wrong_database"
    )
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_resolved_compose_candidate_protected_values(
            drifted,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    encoded_password = deepcopy(resolved)
    encoded_services = encoded_password["services"]
    assert isinstance(encoded_services, dict)
    encoded_pinvi_api = encoded_services["pinvi-api"]
    assert isinstance(encoded_pinvi_api, dict)
    encoded_pinvi_environment = encoded_pinvi_api["environment"]
    assert isinstance(encoded_pinvi_environment, dict)
    encoded_pinvi_environment["PINVI_DATABASE_URL"] = (
        "postgresql+asyncpg://pinvi_contract_app:pinvi-contract-app%2Dpassword@"
        "127.0.0.1:12800/pinvi"
    )
    assert (
        validate_resolved_compose_candidate_protected_values(
            encoded_password,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )
        == raw_snapshots
    )

    for leaked_password in ("wrong-password", environment["KOR_TRAVEL_MAP_POSTGRES_PASSWORD"]):
        leaked = deepcopy(resolved)
        leaked_services = leaked["services"]
        assert isinstance(leaked_services, dict)
        leaked_pinvi_api = leaked_services["pinvi-api"]
        assert isinstance(leaked_pinvi_api, dict)
        leaked_pinvi_environment = leaked_pinvi_api["environment"]
        assert isinstance(leaked_pinvi_environment, dict)
        leaked_pinvi_environment["PINVI_DATABASE_URL"] = (
            f"postgresql+asyncpg://pinvi_contract_app:{leaked_password}@127.0.0.1:12800/pinvi"
        )
        with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
            validate_resolved_compose_candidate_protected_values(
                leaked,
                environment=environment,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
            )

    admin_uses_runtime_role = deepcopy(resolved)
    admin_uses_runtime_services = admin_uses_runtime_role["services"]
    assert isinstance(admin_uses_runtime_services, dict)
    admin_bootstrap = admin_uses_runtime_services["pinvi-admin-bootstrap"]
    assert isinstance(admin_bootstrap, dict)
    admin_environment = admin_bootstrap["environment"]
    assert isinstance(admin_environment, dict)
    admin_environment["PINVI_DATABASE_URL"] = (
        "postgresql+asyncpg://pinvi_contract_app:pinvi-contract-app-password@127.0.0.1:12800/pinvi"
    )
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_resolved_compose_candidate_protected_values(
            admin_uses_runtime_role,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    wrong_pinvi_port = dict(environment)
    wrong_pinvi_port["PINVI_DB_PORT"] = "12900"
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=wrong_pinvi_port,
        )

    repeated_pinvi_role = dict(environment)
    repeated_pinvi_role["PINVI_MIGRATOR_DB_USER"] = repeated_pinvi_role["PINVI_APP_DB_USER"]
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=repeated_pinvi_role,
        )

    repeated_pinvi_password = dict(environment)
    repeated_pinvi_password["PINVI_MIGRATOR_DB_PASSWORD"] = repeated_pinvi_password[
        "PINVI_APP_DB_PASSWORD"
    ]
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=repeated_pinvi_password,
        )
    resolved_repeated_pinvi_password = deepcopy(resolved)
    resolved_repeated_password_services = resolved_repeated_pinvi_password["services"]
    assert isinstance(resolved_repeated_password_services, dict)
    resolved_repeated_admin = resolved_repeated_password_services["pinvi-admin-bootstrap"]
    assert isinstance(resolved_repeated_admin, dict)
    resolved_repeated_admin_environment = resolved_repeated_admin["environment"]
    assert isinstance(resolved_repeated_admin_environment, dict)
    resolved_repeated_admin_environment["PINVI_DATABASE_URL"] = (
        "postgresql+asyncpg://pinvi_contract_migrator:pinvi-contract-app-password@"
        "127.0.0.1:12800/pinvi"
    )
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_resolved_compose_candidate_protected_values(
            resolved_repeated_pinvi_password,
            environment=repeated_pinvi_password,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    runtime_uses_root_password = dict(environment)
    runtime_uses_root_password["PINVI_APP_DB_PASSWORD"] = runtime_uses_root_password[
        "PINVI_POSTGRES_PASSWORD"
    ]
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=runtime_uses_root_password,
        )
    resolved_runtime_uses_root_password = deepcopy(resolved)
    resolved_runtime_uses_root_services = resolved_runtime_uses_root_password["services"]
    assert isinstance(resolved_runtime_uses_root_services, dict)
    resolved_runtime_api = resolved_runtime_uses_root_services["pinvi-api"]
    assert isinstance(resolved_runtime_api, dict)
    resolved_runtime_api_environment = resolved_runtime_api["environment"]
    assert isinstance(resolved_runtime_api_environment, dict)
    resolved_runtime_api_environment["PINVI_DATABASE_URL"] = (
        "postgresql+asyncpg://pinvi_contract_app:pinvi-contract-postgres-password@"
        "127.0.0.1:12800/pinvi"
    )
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_resolved_compose_candidate_protected_values(
            resolved_runtime_uses_root_password,
            environment=runtime_uses_root_password,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    migrator_uses_root_password = dict(environment)
    migrator_uses_root_password["PINVI_MIGRATOR_DB_PASSWORD"] = migrator_uses_root_password[
        "PINVI_POSTGRES_PASSWORD"
    ]
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=migrator_uses_root_password,
        )
    resolved_migrator_uses_root_password = deepcopy(resolved)
    resolved_migrator_uses_root_services = resolved_migrator_uses_root_password["services"]
    assert isinstance(resolved_migrator_uses_root_services, dict)
    resolved_migrator_admin = resolved_migrator_uses_root_services["pinvi-admin-bootstrap"]
    assert isinstance(resolved_migrator_admin, dict)
    resolved_migrator_admin_environment = resolved_migrator_admin["environment"]
    assert isinstance(resolved_migrator_admin_environment, dict)
    resolved_migrator_admin_environment["PINVI_DATABASE_URL"] = (
        "postgresql+asyncpg://pinvi_contract_migrator:pinvi-contract-postgres-password@"
        "127.0.0.1:12800/pinvi"
    )
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_resolved_compose_candidate_protected_values(
            resolved_migrator_uses_root_password,
            environment=migrator_uses_root_password,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    root_secret_leak = deepcopy(candidate)
    root_secret_services = root_secret_leak["services"]
    assert isinstance(root_secret_services, dict)
    root_secret_api = root_secret_services["pinvi-api"]
    assert isinstance(root_secret_api, dict)
    root_secret_api["secrets"] = [
        {
            "source": "pinvi-postgres-password",
            "target": "unexpected-root-password-copy",
        }
    ]
    with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL password secret"):
        validate_compose_candidate_protected_values(
            root_secret_leak,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    pinvi_literal = deepcopy(candidate)
    pinvi_literal_services = pinvi_literal["services"]
    assert isinstance(pinvi_literal_services, dict)
    pinvi_postgres = pinvi_literal_services["pinvi-postgres"]
    assert isinstance(pinvi_postgres, dict)
    pinvi_postgres_environment = pinvi_postgres["environment"]
    assert isinstance(pinvi_postgres_environment, dict)
    pinvi_postgres_environment["POSTGRES_PASSWORD"] = "attacker-literal"
    with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL password"):
        validate_compose_candidate_protected_values(
            pinvi_literal,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    pinvi_db_init_drift = deepcopy(candidate)
    pinvi_db_init_services = pinvi_db_init_drift["services"]
    assert isinstance(pinvi_db_init_services, dict)
    pinvi_db_init = pinvi_db_init_services["pinvi-db-init"]
    assert isinstance(pinvi_db_init, dict)
    pinvi_db_init_environment = pinvi_db_init["environment"]
    assert isinstance(pinvi_db_init_environment, dict)
    pinvi_db_init_environment["PGPORT"] = "12900"
    with pytest.raises(DeploymentContractError, match="database init identity"):
        validate_compose_candidate_protected_values(
            pinvi_db_init_drift,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    for environment_name, drifted_value in (
        ("POSTGRES_USER", "${PINVI_POSTGRES_USER:-wrong_admin}"),
        ("POSTGRES_DB", "${PINVI_POSTGRES_BOOTSTRAP_DB:-wrong_bootstrap}"),
    ):
        pinvi_postgres_drift = deepcopy(candidate)
        pinvi_postgres_services = pinvi_postgres_drift["services"]
        assert isinstance(pinvi_postgres_services, dict)
        pinvi_postgres = pinvi_postgres_services["pinvi-postgres"]
        assert isinstance(pinvi_postgres, dict)
        pinvi_postgres_environment = pinvi_postgres["environment"]
        assert isinstance(pinvi_postgres_environment, dict)
        pinvi_postgres_environment[environment_name] = drifted_value
        with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
            validate_compose_candidate_protected_values(
                pinvi_postgres_drift,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )

    pinvi_postgres_command_drift = deepcopy(candidate)
    pinvi_postgres_command_services = pinvi_postgres_command_drift["services"]
    assert isinstance(pinvi_postgres_command_services, dict)
    pinvi_postgres_command = pinvi_postgres_command_services["pinvi-postgres"]
    assert isinstance(pinvi_postgres_command, dict)
    pinvi_postgres_command_values = pinvi_postgres_command["command"]
    assert isinstance(pinvi_postgres_command_values, list)
    pinvi_postgres_command_values[4] = "${PINVI_DB_PORT:-12900}"
    with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
        validate_compose_candidate_protected_values(
            pinvi_postgres_command_drift,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    for appended_tokens in (
        ["-p", "${PINVI_DB_PORT:-12900}"],
        ["-c", "listen_addresses=0.0.0.0"],
        ["-c", "shared_buffers=attacker"],
    ):
        pinvi_postgres_override = deepcopy(candidate)
        pinvi_postgres_override_services = pinvi_postgres_override["services"]
        assert isinstance(pinvi_postgres_override_services, dict)
        pinvi_postgres_override_service = pinvi_postgres_override_services["pinvi-postgres"]
        assert isinstance(pinvi_postgres_override_service, dict)
        pinvi_postgres_override_command = pinvi_postgres_override_service["command"]
        assert isinstance(pinvi_postgres_override_command, list)
        pinvi_postgres_override_command.extend(appended_tokens)
        with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
            validate_compose_candidate_protected_values(
                pinvi_postgres_override,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )

    for service_name, error_message in (
        ("pinvi-postgres", "PinVi PostgreSQL image provenance"),
        ("pinvi-db-init", "PinVi database init image provenance"),
    ):
        pinvi_image_drift = deepcopy(candidate)
        pinvi_image_services = pinvi_image_drift["services"]
        assert isinstance(pinvi_image_services, dict)
        pinvi_image_services[service_name]["image"] = "attacker.invalid/postgis:latest"
        with pytest.raises(DeploymentContractError, match=error_message):
            validate_compose_candidate_protected_values(
                pinvi_image_drift,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )

    for initdb_args in (
        "--auth-host=trust",
        "--auth-host=scram-sha-256 --auth-local=trust",
        "",
    ):
        pinvi_initdb_drift = deepcopy(candidate)
        pinvi_initdb_services = pinvi_initdb_drift["services"]
        assert isinstance(pinvi_initdb_services, dict)
        pinvi_initdb_postgres = pinvi_initdb_services["pinvi-postgres"]
        assert isinstance(pinvi_initdb_postgres, dict)
        pinvi_initdb_environment = pinvi_initdb_postgres["environment"]
        assert isinstance(pinvi_initdb_environment, dict)
        pinvi_initdb_environment["POSTGRES_INITDB_ARGS"] = initdb_args
        with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
            validate_compose_candidate_protected_values(
                pinvi_initdb_drift,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )

    for service_name, error_message in (
        ("pinvi-postgres", "PinVi PostgreSQL image provenance"),
        ("pinvi-db-init", "PinVi database init image provenance"),
    ):
        pinvi_resolved_image_drift = deepcopy(resolved)
        pinvi_resolved_image_services = pinvi_resolved_image_drift["services"]
        assert isinstance(pinvi_resolved_image_services, dict)
        pinvi_resolved_image_service = pinvi_resolved_image_services[service_name]
        assert isinstance(pinvi_resolved_image_service, dict)
        pinvi_resolved_image_service["image"] = "attacker.invalid/postgis:latest"
        with pytest.raises(DeploymentContractError, match=error_message):
            validate_resolved_compose_candidate_protected_values(
                pinvi_resolved_image_drift,
                environment=environment,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
            )

    pinvi_resolved_command_drift = deepcopy(resolved)
    pinvi_resolved_command_services = pinvi_resolved_command_drift["services"]
    assert isinstance(pinvi_resolved_command_services, dict)
    pinvi_resolved_command_service = pinvi_resolved_command_services["pinvi-postgres"]
    assert isinstance(pinvi_resolved_command_service, dict)
    pinvi_resolved_command = pinvi_resolved_command_service["command"]
    assert isinstance(pinvi_resolved_command, list)
    pinvi_resolved_command.extend(["-p", "12900"])
    with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
        validate_resolved_compose_candidate_protected_values(
            pinvi_resolved_command_drift,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    pinvi_resolved_initdb_drift = deepcopy(resolved)
    pinvi_resolved_initdb_services = pinvi_resolved_initdb_drift["services"]
    assert isinstance(pinvi_resolved_initdb_services, dict)
    pinvi_resolved_initdb_postgres = pinvi_resolved_initdb_services["pinvi-postgres"]
    assert isinstance(pinvi_resolved_initdb_postgres, dict)
    pinvi_resolved_initdb_environment = pinvi_resolved_initdb_postgres["environment"]
    assert isinstance(pinvi_resolved_initdb_environment, dict)
    for initdb_args in ("--auth-host=trust", ""):
        pinvi_resolved_initdb_environment["POSTGRES_INITDB_ARGS"] = initdb_args
        with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
            validate_resolved_compose_candidate_protected_values(
                pinvi_resolved_initdb_drift,
                environment=environment,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
            )

    pinvi_db_init_command_drift = deepcopy(candidate)
    pinvi_db_init_command_services = pinvi_db_init_command_drift["services"]
    assert isinstance(pinvi_db_init_command_services, dict)
    pinvi_db_init_command_service = pinvi_db_init_command_services["pinvi-db-init"]
    assert isinstance(pinvi_db_init_command_service, dict)
    pinvi_db_init_command_service["command"] = ["sh", "-ec", "createdb pinvi"]
    with pytest.raises(DeploymentContractError, match="database init command"):
        validate_compose_candidate_protected_values(
            pinvi_db_init_command_drift,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    pinvi_resolved_identity_drift = deepcopy(resolved)
    pinvi_resolved_services = pinvi_resolved_identity_drift["services"]
    assert isinstance(pinvi_resolved_services, dict)
    pinvi_resolved_postgres = pinvi_resolved_services["pinvi-postgres"]
    assert isinstance(pinvi_resolved_postgres, dict)
    pinvi_resolved_environment = pinvi_resolved_postgres["environment"]
    assert isinstance(pinvi_resolved_environment, dict)
    pinvi_resolved_environment["POSTGRES_USER"] = "wrong_admin"
    with pytest.raises(DeploymentContractError, match="PinVi PostgreSQL identity"):
        validate_resolved_compose_candidate_protected_values(
            pinvi_resolved_identity_drift,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )

    services = resolved["services"]
    assert isinstance(services, dict)
    map_postgres_environment = services["kor-travel-map-postgres"]["environment"]
    assert isinstance(map_postgres_environment, dict)
    assert map_postgres_environment["POSTGRES_PASSWORD_FILE"] == (
        "/run/secrets/kor-travel-map-postgres-password"
    )
    assert "POSTGRES_PASSWORD" not in map_postgres_environment
    assert services["kor-travel-map-postgres"]["secrets"] == [
        {
            "source": "kor-travel-map-postgres-password",
            "target": "kor-travel-map-postgres-password",
        }
    ]
    bootstrap_environment = services["pinvi-admin-bootstrap"]["environment"]
    assert isinstance(bootstrap_environment, dict)
    assert _PINVI_BOOTSTRAP_MAP_ENVIRONMENT.issubset(bootstrap_environment)
    assert not {
        "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE",
        "PINVI_KOR_TRAVEL_MAP_OPS_FIXTURE_TOKEN",
        "KOR_TRAVEL_MAP_API_SERVICE_TOKEN",
    }.intersection(bootstrap_environment)

    map_ui_environment = services["kor-travel-map-ui"]["environment"]
    assert map_ui_environment["KOR_TRAVEL_GEO_API_KEY"] == "v" * 32
    assert "NEXT_PUBLIC_KOR_TRAVEL_GEO_API_KEY" not in map_ui_environment

    map_api_environment = services["kor-travel-map-api"]["environment"]
    assert map_api_environment["KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY"] == "v" * 32
    map_dagster_environment = services["kor-travel-map-dagster"]["environment"]
    map_bootstrap_environment = services["kor-travel-map-db-role-bootstrap"]["environment"]
    map_fresh_environment = services["kor-travel-map-application-fresh-300"]["environment"]
    map_finalize_environment = services["kor-travel-map-application-fresh-finalize"]["environment"]
    assert isinstance(map_api_environment, dict)
    assert isinstance(map_dagster_environment, dict)
    assert isinstance(map_bootstrap_environment, dict)
    assert isinstance(map_fresh_environment, dict)
    assert isinstance(map_finalize_environment, dict)
    assert "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN" not in map_api_environment
    assert "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN" in map_api_environment
    assert "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN" not in map_api_environment
    assert {
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN",
    }.issubset(map_dagster_environment)
    assert not {
        "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN",
        "KOR_TRAVEL_MAP_MIGRATOR_PASSWORD",
        "KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD",
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD",
        "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD",
    }.intersection(map_api_environment | map_dagster_environment)
    assert map_bootstrap_environment["KOR_TRAVEL_MAP_DB_ROLE_BOOTSTRAP_ENABLED"] == "true"
    assert {
        "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN",
        "KOR_TRAVEL_MAP_MIGRATOR_PASSWORD",
        "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN",
        "KOR_TRAVEL_MAP_API_RUNTIME_PASSWORD",
        "KOR_TRAVEL_MAP_API_RUNTIME_PG_DSN",
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PASSWORD",
        "KOR_TRAVEL_MAP_DAGSTER_RUNTIME_PG_DSN",
    }.issubset(map_bootstrap_environment)
    assert map_fresh_environment == {
        "KOR_TRAVEL_MAP_APPLICATION_SCHEMA_PROFILE": "production",
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_IMAGE_ID": f"sha256:{'1' * 64}",
        "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_migrator:map-contract-migrator-password@"
            "127.0.0.1:12700/map_contract"
        ),
        "KOR_TRAVEL_MAP_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_migrator:map-contract-migrator-password@"
            "127.0.0.1:12700/map_contract"
        ),
    }
    assert map_finalize_environment == {
        "KOR_TRAVEL_MAP_APPLICATION_SCHEMA_PROFILE": "production",
        "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_IMAGE_ID": f"sha256:{'1' * 64}",
        "KOR_TRAVEL_MAP_MIGRATOR_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_migrator:map-contract-migrator-password@"
            "127.0.0.1:12700/map_contract"
        ),
        "KOR_TRAVEL_MAP_PG_DSN": (
            "postgresql+asyncpg://ktm_feature_migrator:map-contract-migrator-password@"
            "127.0.0.1:12700/map_contract"
        ),
    }


@pytest.mark.parametrize(
    "leaked_name",
    [
        "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY",
        "KOR_TRAVEL_GEO_API_KEY",
    ],
)
def test_map_geo_key_cannot_leak_outside_exact_runtime_wiring(
    tmp_path: Path,
    leaked_name: str,
) -> None:
    candidate = _compose_fragment(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
    )
    pinvi_api = candidate["services"]["pinvi-api"]
    assert isinstance(pinvi_api, dict)
    environment = pinvi_api.setdefault("environment", {})
    assert isinstance(environment, dict)
    environment[leaked_name] = "${KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY}"

    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    map_pgdata = tmp_path / "map-pgdata"
    map_pgdata.mkdir()
    map_source = tmp_path / "map-source"
    bootstrap_script = map_source / "docker" / "postgres-role-bootstrap.sh"
    bootstrap_script.parent.mkdir(parents=True)
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    contract_environment = _compose_contract_environment()
    contract_environment["KOR_TRAVEL_MAP_PGDATA"] = str(map_pgdata)
    contract_environment["KOR_TRAVEL_MAP_REPO_DIR"] = str(map_source)

    with pytest.raises(DeploymentContractError, match="protected C6c reference"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=contract_environment,
        )


@pytest.mark.parametrize("resolved_candidate", (False, True))
def test_c6c_rejects_map_postgres_password_secret_extra_consumer(
    resolved_candidate: bool,
    tmp_path: Path,
) -> None:
    """initial-superuser secret file은 PostgreSQL entrypoint만 읽을 수 있다."""

    service_names = (
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        # GM-17 B S1: required-set 검사가 이제 소비자 스캔보다 **먼저** 돈다. 이
        # fragment는 종전에 required 서비스 둘을 빼고도 "무단 소비자" 오류에 도달했는데,
        # 지금은 부재가 먼저 보고된다(그것이 S1의 목적이다). 이 검사의 의도는 무단
        # 소비자 거부이므로 fragment를 완전하게 만들어 그 의도를 보존한다.
        "pinvi-postgres",
        "pinvi-db-init",
        "pinvi-db-runtime-role",
    )
    candidate = (
        _resolved_compose(*service_names)
        if resolved_candidate
        else _compose_fragment(*service_names)
    )
    services = candidate["services"]
    assert isinstance(services, dict)
    map_api = services["kor-travel-map-api"]
    assert isinstance(map_api, dict)
    map_api["secrets"] = [
        {
            "source": "kor-travel-map-postgres-password",
            "target": "unexpected-password-copy",
        }
    ]

    environment = _compose_contract_environment()
    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    map_pgdata = tmp_path / "map-pgdata"
    map_pgdata.mkdir()
    environment["KOR_TRAVEL_MAP_PGDATA"] = str(map_pgdata)
    map_source = tmp_path / "map-source"
    bootstrap_script = map_source / "docker" / "postgres-role-bootstrap.sh"
    bootstrap_script.parent.mkdir(parents=True)
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    environment["KOR_TRAVEL_MAP_REPO_DIR"] = str(map_source)

    validator = (
        validate_resolved_compose_candidate_protected_values
        if resolved_candidate
        else validate_compose_candidate_protected_values
    )
    with pytest.raises(
        DeploymentContractError,
        match="Map PostgreSQL password secret has an unauthorized consumer",
    ):
        validator(
            candidate,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


def test_map_postgres_runtime_password_secret_isolation_requires_file_only() -> None:
    """F1D는 실제 PostgreSQL inspect Env에서도 password 노출을 fail-close한다."""

    validate_map_postgres_runtime_secret_isolation(
        {
            "Env": [
                "POSTGRES_DB=kor_travel_map",
                "POSTGRES_PASSWORD_FILE=/run/secrets/kor-travel-map-postgres-password",
            ]
        }
    )

    with pytest.raises(
        DeploymentContractError,
        match="exposes the initial superuser password",
    ):
        validate_map_postgres_runtime_secret_isolation(
            {
                "Env": [
                    "POSTGRES_PASSWORD=legacy-password",
                    "POSTGRES_PASSWORD_FILE=/run/secrets/kor-travel-map-postgres-password",
                ]
            }
        )

    with pytest.raises(
        DeploymentContractError,
        match="password file wiring is invalid",
    ):
        validate_map_postgres_runtime_secret_isolation({"Env": []})


def test_pinvi_postgres_runtime_password_secret_isolation_requires_file_only() -> None:
    validate_pinvi_postgres_runtime_secret_isolation(
        {"Env": ["POSTGRES_PASSWORD_FILE=/run/secrets/pinvi-postgres-password"]}
    )

    with pytest.raises(
        DeploymentContractError,
        match="exposes the initial superuser password",
    ):
        validate_pinvi_postgres_runtime_secret_isolation(
            {
                "Env": [
                    "POSTGRES_PASSWORD=literal-password",
                    "POSTGRES_PASSWORD_FILE=/run/secrets/pinvi-postgres-password",
                ]
            }
        )

    with pytest.raises(
        DeploymentContractError,
        match="password file wiring is invalid",
    ):
        validate_pinvi_postgres_runtime_secret_isolation({"Env": []})


def test_c6c_rejects_map_bootstrap_dsn_outside_dedicated_instance_before_mutation(
    tmp_path: Path,
) -> None:
    """bootstrap one-shot이 shared 5432를 건드리기 전에 endpoint drift를 차단한다."""

    candidate = _compose_fragment(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
    )
    environment = _compose_contract_environment()
    environment["KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN"] = (
        "postgresql://map_contract_admin:map-contract-postgres-password@"
        "127.0.0.1:5432/map_contract"
    )
    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    map_pgdata = tmp_path / "map-pgdata"
    map_pgdata.mkdir()
    environment["KOR_TRAVEL_MAP_PGDATA"] = str(map_pgdata)
    map_source = tmp_path / "map-source"
    bootstrap_script = map_source / "docker" / "postgres-role-bootstrap.sh"
    bootstrap_script.parent.mkdir(parents=True)
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    environment["KOR_TRAVEL_MAP_REPO_DIR"] = str(map_source)

    with pytest.raises(DeploymentContractError, match="Map database DSN identity is invalid"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_c6c_rejects_map_database_port_override(
    tmp_path: Path,
) -> None:
    """Map 전용 DB 포트는 loopback `12700` 계약값으로 고정한다.

    ADR-35가 정한 것은 "전용 instance의 loopback 고정"이고, 번호는 ADR-047 대역
    규칙(각 프로젝트 100번대의 x00)에 따라 2026-08-17에 `12703` -> `12700`으로 옮겼다.
    이 테스트의 픽스처가 옛 번호에 머물면 **테스트는 초록인데 prod 배포가 막힌다** —
    실제로 그 상태였다.
    """

    candidate = _compose_fragment(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
    )
    environment = _compose_contract_environment()
    environment["KOR_TRAVEL_MAP_POSTGRES_PORT"] = "15432"
    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    map_pgdata = tmp_path / "map-pgdata"
    map_pgdata.mkdir()
    environment["KOR_TRAVEL_MAP_PGDATA"] = str(map_pgdata)
    map_source = tmp_path / "map-source"
    bootstrap_script = map_source / "docker" / "postgres-role-bootstrap.sh"
    bootstrap_script.parent.mkdir(parents=True)
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    environment["KOR_TRAVEL_MAP_REPO_DIR"] = str(map_source)

    with pytest.raises(DeploymentContractError, match="Map database DSN identity is invalid"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_c6c_rejects_resolved_map_database_bridge_network(
    tmp_path: Path,
) -> None:
    """loopback dedicated DSN은 host-network runtime에서만 유효하다."""

    resolved = _resolved_compose(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
    )
    services = resolved["services"]
    assert isinstance(services, dict)
    services["kor-travel-map-api"]["network_mode"] = "bridge"
    environment = _compose_contract_environment()
    root_env = tmp_path / ".env"
    root_env.write_text("\n", encoding="utf-8")
    map_pgdata = tmp_path / "map-pgdata"
    map_pgdata.mkdir()
    environment["KOR_TRAVEL_MAP_PGDATA"] = str(map_pgdata)
    map_source = tmp_path / "map-source"
    bootstrap_script = map_source / "docker" / "postgres-role-bootstrap.sh"
    bootstrap_script.parent.mkdir(parents=True)
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    environment["KOR_TRAVEL_MAP_REPO_DIR"] = str(map_source)

    with pytest.raises(DeploymentContractError, match="must use host network"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


def test_resolved_pinvi_runtime_builds_receive_exact_candidate_provenance() -> None:
    revision = "a" * 40
    resolved = _resolved_compose(
        *_PINVI_RUNTIME_SERVICES,
        environment_update={
            "PINVI_SOURCE_REVISION": revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
    )
    services = resolved["services"]
    assert isinstance(services, dict)

    for service_name, dockerfile in {
        "pinvi-api": "apps/api/Dockerfile",
        "pinvi-web": "apps/web/Dockerfile",
        "pinvi-dagster": "apps/etl/Dockerfile",
    }.items():
        build = services[service_name]["build"]
        assert build["dockerfile"] == dockerfile
        assert build["args"]["PINVI_SOURCE_REVISION"] == revision
        assert build["args"]["PINVI_BUILD_ENVIRONMENT"] == "production"


def test_c6c_preflight_rejects_any_pinvi_runtime_provenance_gap() -> None:
    map_revision = "b" * 40
    pinvi_revision = "a" * 40
    resolved = _resolved_compose(
        *_MAP_RUNTIME_SERVICES,
        *_PINVI_RUNTIME_SERVICES,
        environment_update={
            "KOR_TRAVEL_MAP_GIT_COMMIT": map_revision,
            "PINVI_SOURCE_REVISION": pinvi_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
    )

    validate_resolved_c6c_build_provenance(
        resolved,
        C6cBuildProvenance(
            map_source_revision=map_revision,
            pinvi_source_revision=pinvi_revision,
        ),
    )

    build = resolved["services"]["pinvi-dagster"]["build"]
    del build["args"]["PINVI_SOURCE_REVISION"]
    with pytest.raises(
        DeploymentContractError,
        match="pinvi-dagster.*provenance build args",
    ):
        validate_resolved_c6c_build_provenance(
            resolved,
            C6cBuildProvenance(
                map_source_revision=map_revision,
                pinvi_source_revision=pinvi_revision,
            ),
        )


def test_candidate_preflight_rejects_a_build_context_outside_staged_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    map_root = tmp_path / "map"
    pinvi_root = tmp_path / "pinvi"
    for root, dockerfiles in {
        map_root: (
            "docker/api.Dockerfile",
            "docker/frontend.Dockerfile",
            "docker/dagster.Dockerfile",
        ),
        pinvi_root: (
            "apps/api/Dockerfile",
            "apps/web/Dockerfile",
            "apps/etl/Dockerfile",
        ),
    }.items():
        for relative in dockerfiles:
            dockerfile = root / relative
            dockerfile.parent.mkdir(parents=True, exist_ok=True)
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    release = current_pinned_runtime_release()
    map_revision = release.source_for("map").revision
    pinvi_revision = release.source_for("pinvi").revision
    resolved = _resolved_compose(
        *_MAP_RUNTIME_SERVICES,
        *_PINVI_RUNTIME_SERVICES,
        environment_update={
            "KOR_TRAVEL_MAP_REPO_DIR": str(map_root),
            "KOR_TRAVEL_MAP_GIT_COMMIT": map_revision,
            "PINVI_REPO_DIR": str(pinvi_root),
            "PINVI_SOURCE_REVISION": pinvi_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
    )
    sources = PinnedRuntimeSourceMaterialization(
        release=release,
        sources=(
            MaterializedRuntimeSource(
                role="map",
                root=map_root,
                revision=map_revision,
                tree="a" * 40,
            ),
            MaterializedRuntimeSource(
                role="pinvi",
                root=pinvi_root,
                revision=pinvi_revision,
                tree="b" * 40,
            ),
        ),
    )
    build = CandidateRuntimeBuild(
        sources=sources,
        map_application_300_candidate=_map_application_300_candidate(sources),
    )
    environment_snapshot = ComposeEnvironmentSnapshot(
        effective={},
        env_path="",
        compose_path=str(_COMPOSE_PATH),
        override_path="",
        env_file_identity=ComposeEnvFileIdentity(exists=False),
        env_file_bytes=b"",
    )
    transaction = ComposeTransactionSnapshot(
        environment=environment_snapshot,
        external_inputs=ComposeExternalInputSnapshot(references=(), files=()),
        compose_source_bytes=_COMPOSE_PATH.read_bytes(),
        compose_source_mode=0o644,
        system_bind_snapshots=(),
        raw_volume_graph_hash="",
        resolved_volume_graph_hash="",
        resolved=resolved,
    )
    source_contract = Mock(return_value=4)
    monkeypatch.setattr(
        compose_service_module,
        "_map_source_environment_contract_version",
        source_contract,
    )

    ComposeService._validate_pinned_runtime_candidate_build_contract(
        transaction,
        build=build,
        environment_override={"KOR_TRAVEL_MAP_REPO_DIR": str(map_root)},
    )
    source_contract.assert_called_once_with(
        {"KOR_TRAVEL_MAP_REPO_DIR": str(map_root)},
        compose_path=str(_COMPOSE_PATH),
        source_revision=map_revision,
    )

    untrusted_root = tmp_path / "untrusted"
    untrusted_dockerfile = untrusted_root / "apps/web/Dockerfile"
    untrusted_dockerfile.parent.mkdir(parents=True)
    untrusted_dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    resolved["services"]["pinvi-web"]["build"]["context"] = str(untrusted_root)
    with pytest.raises(DeploymentContractError, match="pinvi-web.*not the Git snapshot"):
        ComposeService._validate_pinned_runtime_candidate_build_contract(
            transaction,
            build=build,
        )


def test_ordinary_runtime_services_never_receive_bootstrap_credential_contract() -> None:
    services = _source_compose()["services"]
    assert isinstance(services, dict)

    for service_name in (*_MAP_RUNTIME_SERVICES, *_PINVI_RUNTIME_SERVICES):
        assert "PINVI_BOOTSTRAP_ADMIN" not in json.dumps(services[service_name])


def test_every_real_compose_bind_is_declared_in_a_candidate_bind_allowlist() -> None:
    """compose에 서비스를 등록하면 그 bind도 **같은 커밋에서** baseline에 등록돼야 한다.

    `_validate_candidate_volume_graph`는 문서 전체의 bind를 순회하며
    `(service, target, read_only)` 키가 두 allowlist 어디에도 없으면 fail-close한다.
    그런데 그 실패는 CI가 아니라 **n150의 compose mutation 시점에만** 드러난다 —
    PR #318이 정확히 그렇게 깨졌다(weather bind 6건, 등록 0건). 그 결과 pinned rebuild가
    `prebuild_snapshot`에서 죽었고, 사유는 러너가 가려서 c6c lock을 직접 잡고 재현해야
    나왔다(2026-09-05).

    여기서 같은 조건을 정적으로 건다. 단언을 스키마에 결박하지 않으면 다음 등록이
    또 조용히 깨뜨린다(AGENTS.md DO NOT 15).
    """

    text = _COMPOSE_PATH.read_text(encoding="utf-8")
    services = yaml.safe_load(text)["services"]
    # 키는 (service, target, read_only)뿐이라 source **값**은 검사 대상이 아니다.
    # 기본값 없는 `${VAR}`에서 파서가 죽지 않게만 채운다.
    environment = {
        name: "/placeholder" for name in set(re.findall(r"\$\{([A-Za-z0-9_]+)", text))
    }

    undeclared: list[tuple[str, str | None, str, bool]] = []
    for service_name, service in services.items():
        for mount in _candidate_volume_mounts(
            service.get("volumes"), environment=environment
        ):
            if mount.kind != "bind":
                continue
            key = (service_name, mount.target, mount.read_only)
            if key in _CANDIDATE_ALLOWED_SYSTEM_BINDS:
                continue
            if key in load_compose_bind_allowlist():
                continue
            undeclared.append(
                (service_name, mount.declared_source, mount.target, mount.read_only)
            )

    assert not undeclared, (
        "docker-compose.yml의 bind가 candidate baseline에 없다 — 이 상태로 배포하면 "
        f"Manager의 모든 compose mutation이 fail-close한다: {undeclared!r}"
    )


# ── GM-17 A 적대 리뷰 H-1: 배포 경로가 **설정을 실제로 소비하는가** ──────────
#
# 리뷰어가 로더를 코드 안 얼린 dict로 갈아끼운 고장난 구현에서 전체 스위트
# `1684 passed`를 재현했다 — 커밋이 근거로 든 바로 그 숫자다. 즉 "설정이 정본"이라는
# 이 이관의 유일한 결과물을 지키는 검사가 **0건**이었다. 손으로 한 변이("항목 하나
# 지우면 3건 빨개진다")는 다음 사람이 깨뜨릴 때 아무것도 세지 않는다.
#
# 아래는 그 변이를 **자동화**한 것이다. `registry.load_compose_bind_allowlist`를
# 갈아끼우고 **실제 배포 검증 진입점**이 그 변화를 보는지 단언한다. 상수 부활,
# import 끊김, 로더 우회 어느 쪽이든 빨개진다.

_MAP_PGDATA_BIND_KEY = ("kor-travel-map-postgres", "/var/lib/postgresql/data", False)


def _patched_allowlist(
    monkeypatch: pytest.MonkeyPatch, entries: dict[tuple[str, str, bool], str]
) -> None:
    """배포 경로가 보는 allowlist를 갈아끼운다.

    `registry_module`의 속성을 갈아끼우는 것이 핵심이다 — `c6c_deployment`가 이름을
    직접 당겨왔다면 이 패치가 아무 효과도 없고, 그 사실 자체가 결함이다.
    """

    monkeypatch.setattr(
        registry_module, "load_compose_bind_allowlist", lambda: MappingProxyType(entries)
    )


def test_deployment_validation_actually_consumes_the_config_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """설정에서 항목이 사라지면 **배포 검증**이 거부해야 한다.

    이 검사가 묻는 것은 "설정 파일에 무엇이 적혔나"가 아니라 **"배포기가 그것을
    읽나"**다. 앞의 것만 재는 검사는 로더가 통째로 우회돼도 초록이다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)

    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )

    reduced = dict(load_compose_bind_allowlist())
    assert reduced.pop(_MAP_PGDATA_BIND_KEY, None), "fixture가 겨냥한 항목이 allowlist에 없다"
    _patched_allowlist(monkeypatch, reduced)

    with pytest.raises(
        ComposeCandidateContractError, match="not in the canonical baseline"
    ):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_deployment_validation_rejects_a_forbidden_host_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """allowlist가 설정이 된 뒤 필요해진 값 정책 (적대 리뷰 H-1).

    `source: "/etc"` 한 줄이면 production 컨테이너가 host `/etc`를 쓰기 가능으로
    얻는다 — 종전 manager 가드는 manager 파일의 **조상**만 거부하므로 `/etc`는 그냥
    지나가고, 디렉터리 bind는 protected 값 스캔도 받지 않는다(`S_ISDIR`이면 내용
    검사가 없다). 즉 아무 신호 없이 통과했다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    services = candidate["services"]
    assert isinstance(services, dict)
    service = services["kor-travel-map-postgres"]
    assert isinstance(service, dict)
    service["volumes"] = ["/etc:/var/lib/postgresql/data"]

    patched = dict(load_compose_bind_allowlist())
    patched[_MAP_PGDATA_BIND_KEY] = "/etc"
    _patched_allowlist(monkeypatch, patched)

    with pytest.raises(ComposeCandidateContractError, match="forbidden host location"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_deployment_validation_rejects_binding_the_allowlist_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """인가하는 파일을 인가되는 것으로 쓸 수 없다 (자기-인가 루프).

    allowlist 한 줄이 allowlist 파일을 RW로 마운트하면, 그 컨테이너가 다음 backend
    재기동에 임의 bind를 인가할 수 있다. 인가하는 것과 인가되는 것이 같아지면 그것은
    경계가 아니다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    targets_config = registry_module.get_targets_config_path()
    services = candidate["services"]
    assert isinstance(services, dict)
    service = services["kor-travel-map-postgres"]
    assert isinstance(service, dict)
    service["volumes"] = [f"{targets_config}:/var/lib/postgresql/data"]

    patched = dict(load_compose_bind_allowlist())
    patched[_MAP_PGDATA_BIND_KEY] = targets_config
    _patched_allowlist(monkeypatch, patched)

    with pytest.raises(ComposeCandidateContractError, match="bind allowlist itself"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


# ── GM-17 B · S0: 서비스 부재 형상의 골든 테이블 ─────────────────────────
#
# **동작을 바꾸지 않는다.** 오늘 이 저장소가 각 부재 형상을 어떤 예외·어떤 메시지로
# 거부하는지를 표로 박기만 한다.
#
# 왜 이것이 먼저인가. GM-17 B S4는 required-set(실질 15개 서비스) 강제를 존재-조건부로
# 바꾸는 일인데, **그 변경의 폭발 반경이 지금 CI에 전혀 보이지 않는다**:
#
# 1. `missing required protected services` 문자열을 잡는 테스트가 저장소에 0건이었다.
# 2. 여섯 개의 cross-service validator가 required-set 검사보다 먼저 돌았다(S1이 교정).
# 3. 기존 계약 테스트는 전부 "서비스가 다 있는 상태의 값 드리프트"만 덮는다.
#
# 즉 완화 후 어떤 부재 형상이 계속 거부되고 어떤 것이 조용히 통과하는지는 **한 번도
# 테스트된 적 없는 경로**가 결정하며, 어느 쪽이든 CI는 초록이다. 이 표가 그 침묵을
# 리뷰 가능한 diff로 바꾼다.
#
# ── 2026-09-17 적대 리뷰 정정 — 이 표의 첫 판은 자기 목적을 달성하지 못했다 ──
#
# 첫 판은 `PASS / not-PASS`만 박았고 실질 한 칸(`all_present`)만 값을 고정했다.
# 리뷰가 그것을 뚫었다: **required set을 `frozenset()`으로 통째로 비워도 초록이었다.**
# 3개로 줄여도, 이름 셋을 빼도 초록이었다.
#
# 이유는 완화가 "거부 → 통과"로 움직이지 않기 때문이다. required 집합을 좁혀도
# 15개 이름 루프의 `.get()` 가드(S1이 넣은 그것)가 전부 받아내서, 실제로는
# **"거부 이유 A → 거부 이유 B"**가 된다(리뷰의 S4 시뮬레이션: 이름 하나를 빼고 그
# 서비스를 지우면 14/14 전부 여전히 거부, 그중 8건이 바로 그 가드). 이유를 보지 않는
# 표는 구조적으로 그 이동을 못 본다.
#
# 그래서 이 판은 둘을 바꾼다.
#
# * **이유를 박는다** — 예외 타입 + 메시지를 칸마다 고정한다.
# * **서비스별 단독 제거를 전수로 돈다** — 묶음 형상은 입도가 굵어 서비스별 변화를
#   가린다. 같은 리뷰가 실측했다: main에서 무관한 문구를 낸 것은 "여섯 형상 중 다섯"이
#   아니라 **14개 중 3개**였고(`kor-travel-map-postgres`·`pinvi-postgres`·
#   `pinvi-db-runtime-role`), 묶음 4행이 각각 그 셋 중 하나를 품어서 그렇게 보였다.
# * **resolved 열을 더한다** — 리뷰가 실측으로 보였다: resolved 진입점에 들어간 S1
#   수정 세 가지(순서·모양 루프·`.get()`)를 **전부 되돌려도 스위트가 초록**이었다.
#   프로덕션 diff의 절반이 무증거였다. Docker는 CI(`ubuntu-24.04`)에도 있고 이 파일의
#   이웃 테스트들이 이미 `_resolved_compose`를 스킵 없이 쓴다.
#
# 표가 고정하는 것은 "오늘의 동작"이지 "옳은 동작"이 아니다. **이후 단계의 PR은 이
# 표의 diff를 본문에 싣고, 바뀐 칸을 전부 의도한 변경으로 열거해야 한다** — 특히
# "거부 이유 A → 거부 이유 B"로 움직인 칸을. 그것이 S4가 실제로 만들 변화다.

#: required 집합의 **리터럴 사본**이다. 프로덕션 상수에서 파생하지 않는다 —
#: 파생하면 S4가 집합을 비웠을 때 이 표도 함께 비어 **공허하게 초록**이 된다.
#: (그것이 리뷰가 실제로 뚫은 구멍이다. `docs/tasks-rule.md`가 말하는 "검사기 하한은
#: 본 것에 건다"의 정확한 반례이므로 여기서는 세지 않고 **적는다**.)
_REQUIRED_SERVICES_GOLDEN: tuple[str, ...] = (
    "kor-travel-map-api",
    "kor-travel-map-application-fresh-300",
    "kor-travel-map-application-fresh-finalize",
    "kor-travel-map-dagster",
    "kor-travel-map-dagster-daemon",
    "kor-travel-map-dagster-db-init",
    "kor-travel-map-dagster-storage-migrate",
    "kor-travel-map-db-role-bootstrap",
    "kor-travel-map-postgres",
    "kor-travel-map-ui",
    "pinvi-admin-bootstrap",
    "pinvi-api",
    "pinvi-db-runtime-role",
    "pinvi-postgres",
)

#: required 집합 **밖**이지만 15개 소비자 루프에는 있는 이름. 이 비대칭이 실질 15의
#: 정체다 — `frozenset` 14 + `_validate_pinvi_db_init_identity`의 별도 강제.
#: 코드 주석이 한동안 "15개 전부 required-set이 보증한다"고 잘못 적고 있었다.
_NON_REQUIRED_LOOP_SERVICE = "pinvi-db-init"

_ABSENCE_MATRIX_SERVICES = {
    "map_core": ("kor-travel-map-api", "kor-travel-map-postgres", "kor-travel-map-ui"),
    "map_oneshots": _MAP_DATABASE_ONESHOT_SERVICES,
    "pinvi_core": ("pinvi-api", "pinvi-postgres"),
    "pinvi_oneshots": ("pinvi-db-init", "pinvi-db-runtime-role", "pinvi-admin-bootstrap"),
}


def _shape_without(candidate: dict[str, object], names: tuple[str, ...]) -> dict[str, object]:
    """서비스 키를 **제거한** 문서. `null` 값과 구분하려고 키 자체를 지운다."""

    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    for name in names:
        services.pop(name, None)
    return shaped


def _shape_nulled(candidate: dict[str, object], name: str) -> dict[str, object]:
    """서비스 키는 있고 값이 `null`인 문서.

    유효한 YAML이고 **부재가 아니다.** 완화 조건을 falsy 기반으로 쓰면(`if not
    services.get(x)`) 이 형상이 부재로 오인되어 계약을 한 줄로 우회할 수 있다.
    """

    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services[name] = None  # type: ignore[assignment]
    return shaped


def _verdict(
    entry: object, candidate: dict[str, object], environment: dict[str, str], root_env: Path
) -> str:
    """진입점을 태우고 `예외클래스: 메시지`를 돌려준다."""

    try:
        entry(  # type: ignore[operator]
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    except Exception as exc:  # noqa: BLE001 - 표를 만드는 것이 목적이다
        # **절단하지 않는다.** 종전 `[:200]`은 긴 메시지의 꼬리를 잘랐고, 그 때문에
        # "빠진 이름 대신 전체 집합을 나열한다"는 변이가 **우연히** 죽었다(적대 리뷰
        # 2026-09-17 실측 — 14개 이름이 상한을 넘겨 확인 대상이 잘려나갔을 뿐이다).
        # 우연으로 잡힌 것은 잡힌 것이 아니다.
        return f"{type(exc).__name__}: {exc}"
    return "PASS"


def _bootstrap_resolved(environment: dict[str, str]) -> dict[str, Any]:
    """`_bootstrap_candidate`와 **같은 fragment**의 resolved 문서.

    `_resolved_compose`는 `docker compose config`를 부르므로 형상마다 부르지 않는다 —
    **한 번** 해석한 뒤 메모리에서 서비스를 지우거나 null로 만든다. 서비스 수만큼
    Docker를 부르면 이 표가 스위트에서 가장 느린 검사가 된다.
    """

    return _resolved_compose(
        "kor-travel-map-postgres",
        "kor-travel-map-api",
        "kor-travel-map-ui",
        "kor-travel-map-dagster",
        "kor-travel-map-dagster-daemon",
        *_MAP_DATABASE_ONESHOT_SERVICES,
        "pinvi-api",
        "pinvi-admin-bootstrap",
        "pinvi-db-runtime-role",
        environment_update={
            name: environment[name]
            for name in (
                "KOR_TRAVEL_MAP_PGDATA",
                "KOR_TRAVEL_MAP_REPO_DIR",
                "KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR",
                "KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR",
                "KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR",
                "KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR",
                "PINVI_REPO_DIR",
                "PINVI_PGDATA",
            )
        },
    )


def test_required_protected_service_set_is_pinned() -> None:
    """required 집합을 **리터럴로** 고정한다 — 이 검사가 S4의 첫 관문이다.

    S4는 이 집합을 좁히는 일이고, 그 diff가 리뷰에 보이게 만드는 것이 S0의 전부다.
    집합을 프로덕션 상수에서 파생해 비교하면 항진명제가 되므로 리터럴로 적는다.

    `pinvi-db-init`이 여기 **없다**는 것도 함께 박는다. 15개 소비자 루프에는 있으나
    required 집합에는 없고, 그 보증의 출처는 `_validate_pinvi_db_init_identity`다 —
    S3가 바로 그 함수를 이분할한다.
    """

    assert len(_REQUIRED_SERVICES_GOLDEN) == 14
    assert set(_REQUIRED_SERVICES_GOLDEN) == set(
        c6c_deployment_module._CANDIDATE_REQUIRED_PROTECTED_SERVICES
    ), (
        "required 집합이 바뀌었다 — S4라면 이 리터럴을 갱신하고 "
        "PR 본문에 어느 서비스를 왜 뺐는지 열거하라"
    )
    assert _NON_REQUIRED_LOOP_SERVICE not in _REQUIRED_SERVICES_GOLDEN
    assert (
        _NON_REQUIRED_LOOP_SERVICE
        not in c6c_deployment_module._CANDIDATE_REQUIRED_PROTECTED_SERVICES
    )


#: 15개 소비자 루프 이름의 **리터럴 사본**. `_REQUIRED_SERVICES_GOLDEN`(14)과 달리
#: 이 목록은 S4가 줄이지 않는다 — 줄이면 그 서비스의 행이 표에서 통째로 사라져
#: 다시 눈이 먼다. required 집합이 좁아져도 **행은 남고 이유만 바뀐다**, 그것이
#: 보여야 할 diff다.
_PROTECTED_LOOP_SERVICES_GOLDEN: tuple[str, ...] = (
    *_REQUIRED_SERVICES_GOLDEN,
    _NON_REQUIRED_LOOP_SERVICE,
)

#: 서비스 **하나만** 지웠을 때의 거부 이유. 키는 `<서비스>/<진입점>`.
#: 값이 바뀌면 그것이 곧 S4의 폭발 반경이다 — PR 본문에 옮겨 적어라.
#:
#: required 14개는 기계적이라 리터럴 목록에서 **파생**한다(프로덕션 상수가 아니라
#: 이 파일의 리터럴에서다). S4가 그 리터럴을 줄이면 빠진 이름의 기대값이 사라지고,
#: 그래도 `_PROTECTED_LOOP_SERVICES_GOLDEN`은 그 행을 계속 관측하므로 표가
#: **빨개진다** — 작성자가 새 이유를 명시적으로 적어야 통과한다.
_SINGLE_ABSENCE_GOLDEN: dict[str, str] = {
    **{
        f"{name}/raw": (
            "ComposeCandidateContractError: compose candidate is missing "
            f"required protected services: {name}"
        )
        for name in _REQUIRED_SERVICES_GOLDEN
    },
    **{
        f"{name}/resolved": (
            "ComposeCandidateContractError: resolved compose candidate is missing "
            f"required protected services: {name}"
        )
        for name in _REQUIRED_SERVICES_GOLDEN
    },
    # `pinvi-db-init`은 required 집합 밖이라 **부재를 부재라고 말하지 않는다.**
    # `_validate_pinvi_db_init_identity`가 먼저 걸러서 정체성 오류로 보고한다.
    # S1 커밋과 `docs/tasks.md`가 "absent_* → missing required protected services"라고
    # 단정했는데 15개 중 이 하나에서 거짓이었다(적대 리뷰 2026-09-17). 표에 그
    # 예외를 **적어서** 남긴다 — 숨기면 S3가 그 함수를 이분할할 때 아무도 모른다.
    "pinvi-db-init/raw": (
        "ComposeCandidateContractError: PinVi database init identity is invalid"
    ),
    "pinvi-db-init/resolved": (
        "ComposeCandidateContractError: PinVi database init identity is invalid"
    ),
}


def test_single_service_absence_reason_is_pinned(tmp_path: Path) -> None:
    """15개 서비스를 **하나씩** 지웠을 때의 거부 이유를 전수로 고정한다.

    첫 판이 묶음 4행이었고, 그래서 리뷰가 required 집합을 비워도 초록이었다. 이유를
    서비스별로 박으면 완화는 반드시 어떤 칸의 문구를 바꾼다 — 그것이 보이는 diff다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    names = _PROTECTED_LOOP_SERVICES_GOLDEN

    observed: dict[str, str] = {}
    for name in names:
        observed[f"{name}/raw"] = _verdict(
            validate_compose_candidate_protected_values,
            _shape_without(candidate, (name,)),
            environment,
            root_env,
        )
        observed[f"{name}/resolved"] = _verdict(
            validate_resolved_compose_candidate_protected_values,
            _shape_without(resolved, (name,)),
            environment,
            root_env,
        )

    assert observed == _SINGLE_ABSENCE_GOLDEN, _golden_diff(observed, _SINGLE_ABSENCE_GOLDEN)


def _golden_diff(observed: dict[str, str], golden: dict[str, str]) -> str:
    """표가 틀렸을 때 **어느 칸이 어떻게** 움직였는지 보여 준다."""

    lines = ["골든 테이블과 다르다 — 바뀐 칸을 PR 본문에 열거하라:"]
    for key in sorted(set(observed) | set(golden)):
        was, now = golden.get(key, "<없던 칸>"), observed.get(key, "<사라진 칸>")
        if was != now:
            lines.append(
                f"  {key}\n      before: {was}\n      after : {now}"
            )
    return "\n".join(lines)


#: 묶음 부재 + `null` 형상. 서비스별 표가 못 보는 **상호작용**(둘 이상이 함께 빠질 때
#: 어느 이름이 먼저 보고되는가)과 `null` 경로를 덮는다.
#:
#: `absent_pinvi_oneshots`가 서비스 **셋**을 지우는데 이름은 **둘**만 댄다는 점에
#: 주목하라 — `pinvi-db-init`이 required 집합 밖이라서다. 첫 판의 표는 이 비대칭을
#: 드러내지 못했다.
_SHAPE_GOLDEN: dict[str, str] = {
    "all_present/raw": "PASS",
    "all_present/resolved": "PASS",
    "absent_map_core/raw": (
        "ComposeCandidateContractError: compose candidate is missing required "
        "protected services: kor-travel-map-api, kor-travel-map-postgres, "
        "kor-travel-map-ui"
    ),
    "absent_map_core/resolved": (
        "ComposeCandidateContractError: resolved compose candidate is missing "
        "required protected services: kor-travel-map-api, kor-travel-map-postgres, "
        "kor-travel-map-ui"
    ),
    "absent_map_oneshots/raw": (
        "ComposeCandidateContractError: compose candidate is missing required "
        "protected services: kor-travel-map-application-fresh-300, "
        "kor-travel-map-application-fresh-finalize, kor-travel-map-dagster-db-init, "
        "kor-travel-map-dagster-storage-migrate, kor-travel-map-db-role-bootstrap"
    ),
    "absent_map_oneshots/resolved": (
        "ComposeCandidateContractError: resolved compose candidate is missing "
        "required protected services: kor-travel-map-application-fresh-300, "
        "kor-travel-map-application-fresh-finalize, kor-travel-map-dagster-db-init, "
        "kor-travel-map-dagster-storage-migrate, kor-travel-map-db-role-bootstrap"
    ),
    "absent_pinvi_core/raw": (
        "ComposeCandidateContractError: compose candidate is missing required "
        "protected services: pinvi-api, pinvi-postgres"
    ),
    "absent_pinvi_core/resolved": (
        "ComposeCandidateContractError: resolved compose candidate is missing "
        "required protected services: pinvi-api, pinvi-postgres"
    ),
    "absent_pinvi_oneshots/raw": (
        "ComposeCandidateContractError: compose candidate is missing required "
        "protected services: pinvi-admin-bootstrap, pinvi-db-runtime-role"
    ),
    "absent_pinvi_oneshots/resolved": (
        "ComposeCandidateContractError: resolved compose candidate is missing "
        "required protected services: pinvi-admin-bootstrap, pinvi-db-runtime-role"
    ),
    "nulled_kor-travel-map-api/raw": (
        "ComposeCandidateContractError: compose candidate service is missing or "
        "invalid: kor-travel-map-api"
    ),
    "nulled_kor-travel-map-api/resolved": (
        "ComposeCandidateContractError: resolved compose candidate service is "
        "missing or invalid: kor-travel-map-api"
    ),
    "nulled_pinvi-api/raw": (
        "ComposeCandidateContractError: compose candidate service is missing or "
        "invalid: pinvi-api"
    ),
    "nulled_pinvi-api/resolved": (
        "ComposeCandidateContractError: resolved compose candidate service is "
        "missing or invalid: pinvi-api"
    ),
}


def test_absence_matrix_is_pinned(tmp_path: Path) -> None:
    """묶음 부재·`null` 형상의 판정을 표로 고정한다.

    이 검사는 **아무것도 주장하지 않는다.** "이 형상이 거부돼야 한다"가 아니라
    "오늘은 이렇게 거부된다"를 적는다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    entries = {
        "raw": (validate_compose_candidate_protected_values, candidate),
        "resolved": (validate_resolved_compose_candidate_protected_values, resolved),
    }

    observed: dict[str, str] = {}
    for entry_name, (entry, base) in entries.items():
        observed[f"all_present/{entry_name}"] = _verdict(entry, base, environment, root_env)
        for family, names in _ABSENCE_MATRIX_SERVICES.items():
            observed[f"absent_{family}/{entry_name}"] = _verdict(
                entry, _shape_without(base, names), environment, root_env
            )
        for name in ("kor-travel-map-api", "pinvi-api"):
            observed[f"nulled_{name}/{entry_name}"] = _verdict(
                entry, _shape_nulled(base, name), environment, root_env
            )

    assert observed == _SHAPE_GOLDEN, _golden_diff(observed, _SHAPE_GOLDEN)


def test_absence_is_reported_as_absence(tmp_path: Path) -> None:
    """부재는 **부재라고** 보고된다 — 그리고 어느 이름이 빠졌는지 말한다 (S1).

    S0이 박은 표가 드러낸 것: required-set 검사가 여섯 validator보다 **뒤**에 있어서,
    서비스가 빠져도 사용자는 "Map PostgreSQL password secret is invalid" 같은 무관한
    문구를 봤다. 운영자는 그것을 쫓다가 실제 원인에 도달하지 못한다.

    S1이 순서를 교정했다. 이 검사가 그 교정을 결박한다 — 누군가 순서를 되돌리면
    빨개진다. **두 진입점 모두** 건다: 리뷰가 실측으로 보였듯 resolved 쪽만 되돌리면
    종전에는 아무도 못 봤다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)

    for entry, base, label in (
        (validate_compose_candidate_protected_values, candidate, "raw"),
        (validate_resolved_compose_candidate_protected_values, resolved, "resolved"),
    ):
        verdict = _verdict(
            entry, _shape_without(base, ("kor-travel-map-postgres",)), environment, root_env
        )
        assert "missing required protected services" in verdict, f"{label}: {verdict}"
        assert "kor-travel-map-postgres" in verdict, (
            f"{label}: 무엇이 빠졌는지 말하지 않는다: {verdict}"
        )


def test_null_service_is_invalid_not_absent(tmp_path: Path) -> None:
    """`service: null`은 **부재가 아니라 invalid**다 — 그리고 그 서비스를 지목한다.

    이 구분이 S4의 안전 조건이다. 완화의 skip 판정은 **키 부재로만** 해야 하는데,
    falsy 기반(`if not services.get(x)`)으로 쓰면 `null` 한 줄이 부재로 오인되어
    계약을 우회한다.

    S0 표의 실측: 종전에는 **어느 서비스를 null로 만들든** "Map PostgreSQL password
    secret is invalid"가 나왔다(소비자 스캔이 non-Mapping을 먼저 만난다). PinVi를
    null로 해도 Map 오류였다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)

    for entry, base, label in (
        (validate_compose_candidate_protected_values, candidate, "raw"),
        (validate_resolved_compose_candidate_protected_values, resolved, "resolved"),
    ):
        for name in ("kor-travel-map-api", "pinvi-api"):
            verdict = _verdict(entry, _shape_nulled(base, name), environment, root_env)
            assert "missing or invalid" in verdict, f"{label}/{name}: {verdict}"
            assert name in verdict, (
                f"{label}: {name}을 null로 했는데 그 이름을 말하지 않는다: {verdict}"
            )


def test_unknown_service_key_is_not_echoed_into_the_contract_error(
    tmp_path: Path,
) -> None:
    """계약이 모르는 서비스 키는 **문구에 그대로 실리지 않는다**.

    이 오류는 CLI stderr와 HTTP 500 body로 나가는데(`api/routes.py`의
    `_config_failure_detail`), 서비스 키는 후보 문서 작성자가 정하는 임의 문자열이다.
    적대 리뷰 2026-09-17이 보호값을 키 자리에 넣어 실측했다 — S1의 첫 판은 그 문자열을
    그대로 실었고 main은 싣지 않았다. 이 루프는 보호값 전역 스캔보다 **앞**이라
    그 스캔이 막아 주지도 못한다.

    권한 상승은 아니다(운영자가 직접 적은 키다). 심층 방어이고, 이 저장소가 이미
    명시적으로 지키는 계약이다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    secret_shaped_key = environment["KOR_TRAVEL_MAP_POSTGRES_PASSWORD"]
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services[secret_shaped_key] = None

    verdict = _verdict(
        validate_compose_candidate_protected_values, shaped, environment, root_env
    )
    assert verdict != "PASS", verdict
    assert secret_shaped_key not in verdict, (
        f"후보가 정한 키가 계약 오류 문구로 새어 나왔다: {verdict}"
    )
    assert "sha256:" in verdict, f"모르는 키를 가리키는 표식이 없다: {verdict}"

    # 아는 이름은 그대로 지목한다 — 진단을 잃지 않는다.
    known = _verdict(
        validate_compose_candidate_protected_values,
        _shape_nulled(candidate, "pinvi-api"),
        environment,
        root_env,
    )
    assert "pinvi-api" in known, known


# ── GM-17 B · S2: Map password validator 이분할 ──────────────────────────
#
# 감사가 찾은 함정은 이렇다 — `_validate_map_postgres_password_secret`이 두 가지를
# 한 함수에 담고 있어서, S4가 서비스 부재 시 **함수째** 건너뛰면 둘 다 꺼진다.
#
#   (A) 소유자 배선 — `kor-travel-map-postgres`가 secret file로만 password를 받는가.
#       소유자의 존재를 전제하므로 부재 시 건너뛰어도 된다.
#   (B) 유일 소비자 스캔 — 문서의 **아무** 서비스도 그 secret을 alias로 가져가지
#       못한다. 소유자와 무관한 전역 불변식이라 **절대 꺼지면 안 된다.**
#
# (B)가 꺼지면 남는 그물이 없다(감사 실측): 전역 보호 이름 스캔은 alias를 substring으로
# 잡지 못하고, external-resource 검사는 그 alias를 무조건 면제하며, runtime 검사는
# 소비자를 보지 않는다.
#
# 아래 검사들은 **분리가 실재하는지**를 묻는다. 오늘 공개 진입점으로는 소유자 부재에
# 도달할 수 없으므로(required-set이 먼저 막는다) 쪼갠 함수를 직접 태운다 — 그것이
# 요점이다. S4가 그 문을 여는 날 이 검사들이 이미 자리를 지키고 있어야 한다.

_MAP_PASSWORD_SECRET = "kor-travel-map-postgres-password"


def _document_with_foreign_consumer(
    *, include_owner: bool, shorthand: bool = False
) -> dict[str, object]:
    """Map password secret을 **남의 서비스**가 가져가는 문서.

    `include_owner=False`는 S4 이후의 형상이다 — Map family가 scope 밖이라 소유자
    서비스가 아예 없는데, 누군가는 여전히 그 secret을 마운트하려 한다.

    `shorthand=True`는 **짧은 문법**(`secrets: ["<이름>"]`)이다. Compose에서 가장 싼
    마운트 표기인데 첫 판의 검사는 긴 문법만 만들었다(적대 리뷰 2026-09-17 M3).
    코드는 두 문법을 다 처리하지만 **아무도 그것을 지키지 않았다** — 짧은 문법 처리를
    `continue`로 바꾸는 변이가 전체 스위트 1700건을 그대로 통과했다.
    """

    foreign_reference: object = (
        _MAP_PASSWORD_SECRET
        if shorthand
        else {"source": _MAP_PASSWORD_SECRET, "target": _MAP_PASSWORD_SECRET}
    )
    services: dict[str, object] = {
        "some-other-service": {
            "image": "example:latest",
            "secrets": [foreign_reference],
        }
    }
    if include_owner:
        services["kor-travel-map-postgres"] = {
            "image": "postgis:latest",
            "environment": {
                "POSTGRES_PASSWORD_FILE": f"/run/secrets/{_MAP_PASSWORD_SECRET}"
            },
            "secrets": [
                {"source": _MAP_PASSWORD_SECRET, "target": _MAP_PASSWORD_SECRET}
            ],
        }
    return {
        "secrets": {
            _MAP_PASSWORD_SECRET: {"environment": "KOR_TRAVEL_MAP_POSTGRES_PASSWORD"}
        },
        "services": services,
    }


@pytest.mark.parametrize("shorthand", [False, True], ids=["long", "shorthand"])
@pytest.mark.parametrize("include_owner", [True, False], ids=["owner", "no-owner"])
def test_sole_consumer_scan_rejects_a_foreign_consumer(
    include_owner: bool, shorthand: bool
) -> None:
    """남의 소비는 **두 문법 모두** 거부된다 — 소유자 유무와 무관하게.

    짧은 문법 축은 적대 리뷰 2026-09-17 M3이 추가시켰다: 코드는 처리하는데 검사가
    없어서, 짧은 문법 처리를 `continue`로 바꾸는 변이가 스위트 전체를 통과했다.
    """

    document = _document_with_foreign_consumer(
        include_owner=include_owner, shorthand=shorthand
    )
    with pytest.raises(
        ComposeCandidateContractError, match="unauthorized consumer"
    ):
        c6c_deployment_module._assert_map_postgres_password_sole_consumer(document)


def test_authorized_reference_is_empty_without_an_owner() -> None:
    """인가 집합은 소유자에서만 나온다 — 없으면 공집합(`None`)이다.

    이 파생이 (A)의 지역 변수를 빌리지 않는다는 것이 S2의 전부다. 빌려 쓰면 (A)를
    끄는 순간 (B)가 함께 무너진다.
    """

    derive = c6c_deployment_module._authorized_map_postgres_password_reference
    assert derive(_document_with_foreign_consumer(include_owner=False)) is None
    assert derive(_document_with_foreign_consumer(include_owner=True)) == {
        "source": _MAP_PASSWORD_SECRET,
        "target": _MAP_PASSWORD_SECRET,
    }
    # 소유자가 참조를 둘 들고 있으면 "유일"이 성립하지 않으므로 인가하지 않는다.
    two_references = _document_with_foreign_consumer(include_owner=True)
    owner = two_references["services"]["kor-travel-map-postgres"]  # type: ignore[index]
    assert isinstance(owner, dict)
    owner["secrets"] = [*owner["secrets"], {"source": "other", "target": "other"}]
    assert derive(two_references) is None


def test_owner_wiring_is_skipped_only_when_the_owner_is_absent() -> None:
    """(A)는 소유자가 없을 때만 조용하다 — 있으면 종전처럼 배선을 따진다.

    이 검사가 없으면 "조건부로 만든다"가 "그냥 끈다"로 조용히 미끄러질 수 있다.
    """

    wiring = c6c_deployment_module._validate_map_postgres_password_owner_wiring

    # 소유자 부재 → 조용히 통과(판정할 대상이 없다).
    wiring(_document_with_foreign_consumer(include_owner=False))

    # 소유자 존재 + 배선 파손(`POSTGRES_PASSWORD`가 환경으로 샌다) → 거부.
    leaking = _document_with_foreign_consumer(include_owner=True)
    owner = leaking["services"]["kor-travel-map-postgres"]  # type: ignore[index]
    assert isinstance(owner, dict)
    environment = owner["environment"]
    assert isinstance(environment, dict)
    environment["POSTGRES_PASSWORD"] = "leaked"
    with pytest.raises(
        ComposeCandidateContractError, match="leaks to container environment"
    ):
        wiring(leaking)


def test_wiring_is_reported_before_consumers_at_the_entry_point(
    tmp_path: Path,
) -> None:
    """배선 오류와 무단 소비자가 동시에 있으면 **배선이 먼저** 보고된다.

    이것이 S2의 "동작 변경 0"을 지키는 제약이다. 전역 소비자 스캔을 family 블록
    **앞**에 두면 이 문서의 문구가 "...unauthorized consumer"로 바뀐다 — 판정은
    같지만 진단이 달라지므로 종전과 다르다. 그래서 전역 블록을 family 블록 **뒤**에
    두었고, 이 검사가 그 배치를 결박한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    owner = services["kor-travel-map-postgres"]
    assert isinstance(owner, dict)
    owner_environment = owner["environment"]
    assert isinstance(owner_environment, dict)
    owner_environment["POSTGRES_PASSWORD"] = "leaked"
    services["some-other-service"] = {
        "image": "example:latest",
        "secrets": [{"source": _MAP_PASSWORD_SECRET, "target": _MAP_PASSWORD_SECRET}],
    }

    with pytest.raises(
        ComposeCandidateContractError, match="leaks to container environment"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_global_invariants_are_not_inside_the_family_validator() -> None:
    """전역 불변식 둘은 family validator **밖**에 있어야 한다 — 자리 자체를 결박한다.

    적대 리뷰 둘이 각각 같은 구멍을 찾았다: 첫 판은 선언 검사와 소비자 스캔을 Map
    validator **안**에 두었고, 그래서 진입점의 호출부를 소유자 존재로 감싸는 순진한
    S4가 전체 스위트를 통과시키면서 무단 소비자를 실제로 통과시켰다.

    그 뒤 이중화(합성 wrapper 안에도, 진입점에도)를 시도했는데 그것도 틀렸다 —
    둘 중 하나를 지우는 변이가 **아무 검사도** 빨갛게 만들지 못했다. 이중화는
    방어처럼 보이지만 검사 불가능한 방어다.

    그래서 자리를 **하나**로 만들었다: family 블록은 (A)만 부르고, 전역 불변식 둘은
    진입점의 전역 블록에만 산다. 이 검사는 그 구조를 직접 확인한다 — (A)가 전역
    불변식을 부르면 자리가 둘로 늘어난 것이므로 빨개진다.
    """

    import inspect

    wiring_source = inspect.getsource(
        c6c_deployment_module._validate_map_postgres_password_owner_wiring
    )
    for forbidden in (
        "_assert_map_postgres_password_sole_consumer",
        "_validate_map_postgres_password_declaration",
    ):
        assert forbidden not in wiring_source, (
            f"전역 불변식 {forbidden}이 family validator 안으로 들어왔다 — 자리가 둘이 되면"
            " 하나를 지우는 변이를 아무 검사도 잡지 못한다"
        )


def test_entry_points_keep_the_consumer_scan_when_the_required_set_shrinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**S4를 오늘 시뮬레이션한다.** required 집합이 줄어도 소비자 스캔은 살아 있는가.

    적대 리뷰 둘이 각각 이 PR의 실질적 구멍을 찾았다. S2 검사들은 전부 private 합성
    함수를 태우는데, **S4가 게이트를 넣을 자리는 공개 진입점의 호출부**다. 거기에
    순진한 S4를 넣자 S2 검사 6건이 전부 초록이었고 무단 소비자가 실제로 통과했다.
    빨개진 넷은 S1의 required-set 골든 핀뿐인데, 그 핀의 docstring은 S4 저자에게
    **리터럴을 갱신하라고 지시한다** — 지시를 정당하게 따르면 그물이 사라진다.

    그래서 이 검사는 **S4가 바꿀 바로 그것을 오늘 바꿔 본다**: required 집합에서
    소유자를 빼고(= S4의 절반), 완전한 후보에서 소유자만 지운 뒤 진입점에 태운다.
    진입점이 여전히 무단 소비자로 거부해야 한다.

    최소 문서로는 안 된다 — 진입점은 required-set과 모양 검사를 지난 뒤 전역 블록과
    family validator를 도는데, 최소 문서는 그 전에 다른 이유로 죽는다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    monkeypatch.setattr(
        c6c_deployment_module,
        "_CANDIDATE_REQUIRED_PROTECTED_SERVICES",
        frozenset(
            name
            for name in c6c_deployment_module._CANDIDATE_REQUIRED_PROTECTED_SERVICES
            if name != "kor-travel-map-postgres"
        ),
    )

    for shorthand in (False, True):
        shaped = _shape_without(candidate, ("kor-travel-map-postgres",))
        services = shaped["services"]
        assert isinstance(services, dict)
        services["some-other-service"] = {
            "image": "example:latest",
            "secrets": [
                _MAP_PASSWORD_SECRET
                if shorthand
                else {
                    "source": _MAP_PASSWORD_SECRET,
                    "target": _MAP_PASSWORD_SECRET,
                }
            ],
        }
        with pytest.raises(
            ComposeCandidateContractError, match="unauthorized consumer"
        ) as rejection:
            validate_compose_candidate_protected_values(
                shaped,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )
        assert "unauthorized consumer" in str(rejection.value), (
            f"shorthand={shorthand}: {rejection.value}"
        )


def test_owner_must_mount_the_secret_at_the_exact_target(tmp_path: Path) -> None:
    """소유자는 secret을 **exact target**에 마운트해야 한다 (적대 리뷰 M1/F-3).

    **선재 공백**이었다: (A)의 `source`/`target` 검사를 지워도 backend 1,700건이 전부
    통과하는데 게이트의 판정은 실제로 바뀐다 — 소유자가 superuser secret을 임의 alias
    target에 마운트하거나 짧은 문법으로 target을 생략해도 통과하게 된다. main도 같아
    회귀는 아니지만, S2가 그 위험을 올렸다: 이제 인가 집합 파생이 그 참조를 (B)에
    넘긴다. 그래서 파생에도 모양 검증을 넣고, 그 불변식을 여기서 처음으로 센다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    services = candidate["services"]
    assert isinstance(services, dict)
    owner = services["kor-travel-map-postgres"]
    assert isinstance(owner, dict)
    references = owner["secrets"]
    assert isinstance(references, list) and len(references) == 1
    authorized = references[0]
    assert isinstance(authorized, dict)

    for label, replacement in {
        "alias target": [{**authorized, "target": "some-other-target"}],
        "short syntax (target 생략)": [authorized["source"]],
        "traversal-looking target": [{**authorized, "target": "../escaped"}],
    }.items():
        shaped = deepcopy(candidate)
        shaped_services = shaped["services"]
        assert isinstance(shaped_services, dict)
        shaped_owner = shaped_services["kor-travel-map-postgres"]
        assert isinstance(shaped_owner, dict)
        shaped_owner["secrets"] = replacement
        with pytest.raises(ComposeCandidateContractError) as rejection:
            validate_compose_candidate_protected_values(
                shaped,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )
        assert "Map PostgreSQL password secret is invalid" in str(rejection.value), (
            f"{label}: 소유자의 어긋난 마운트가 거부되지 않았다 — {rejection.value}"
        )


def test_the_secret_declaration_is_checked_without_the_owner(tmp_path: Path) -> None:
    """최상위 `secrets` 선언 검사는 **소유자와 무관**하다 (적대 리뷰 M2).

    첫 판은 이 블록을 (A) 안에 두었고 그래서 (A)의 docstring이 거짓이었다 — 최상위
    선언은 소유자 서비스에 관한 물음이 아니라 문서 전역의 성질이다. 게다가
    `_DATABASE_ALLOWED_NON_ENV_PATHS`가 그 경로를 전역 스캔에서 **무조건 면제**하는
    근거가 "이 검사가 그 경로를 소유한다"였으므로, S4가 (A)를 끄면 면제만 남는다.
    """

    document = _document_with_foreign_consumer(include_owner=False)
    secrets = document["secrets"]
    assert isinstance(secrets, dict)
    secrets[_MAP_PASSWORD_SECRET] = {"environment": "PINVI_POSTGRES_PASSWORD"}

    with pytest.raises(
        ComposeCandidateContractError, match="Map PostgreSQL password secret is invalid"
    ):
        c6c_deployment_module._validate_map_postgres_password_declaration(document)


def test_entry_point_checks_the_secret_declaration(tmp_path: Path) -> None:
    """선언 검사가 **진입점에서** 실제로 불린다.

    직접 호출 검사만 두면 진입점의 호출을 지우는 변이를 잡지 못한다(실측: 그 상태에서
    전체 스위트가 초록이었다). 진입점을 태워 호출 자체를 결박한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    secrets = shaped["secrets"]
    assert isinstance(secrets, dict)
    secrets[_MAP_PASSWORD_SECRET] = {"environment": "PINVI_POSTGRES_PASSWORD"}

    with pytest.raises(
        ComposeCandidateContractError, match="Map PostgreSQL password secret is invalid"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_entry_point_runs_the_consumer_scan_for_a_valid_owner(tmp_path: Path) -> None:
    """소비자 스캔이 **진입점에서** 실제로 불린다 — 소유자가 멀쩡할 때도.

    `..._when_the_required_set_shrinks`는 required 집합을 patch하므로, 진입점의 전역
    호출을 지우는 변이를 그것만으로는 못 잡는다((A) 안의 경로로 대체될 수 있었다).
    여기서는 patch 없이, 소유자가 정상인 후보에 남의 소비자만 얹어 진입점을 태운다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["some-other-service"] = {
        "image": "example:latest",
        "secrets": [_MAP_PASSWORD_SECRET],
    }

    with pytest.raises(
        ComposeCandidateContractError, match="unauthorized consumer"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_authorized_reference_requires_the_exact_shape() -> None:
    """인가 파생은 **모양까지** 본다 (적대 리뷰 M1).

    첫 판은 소유자의 `secrets[0]`을 검증 없이 돌려줬고, 그래서 이름이 거짓이었다 —
    "인가받은"이 아니라 "소유자가 선언한"이었다. 진입점 경로로는 (A)가 같은 것을
    확인하므로 이 결함이 보이지 않는다(실측: 모양 검증을 지워도 전체 스위트 초록).
    그래서 파생을 **직접** 태운다.
    """

    derive = c6c_deployment_module._authorized_map_postgres_password_reference

    def owner_with(reference: object) -> dict[str, object]:
        return {
            "services": {"kor-travel-map-postgres": {"secrets": [reference]}}
        }

    good = {"source": _MAP_PASSWORD_SECRET, "target": _MAP_PASSWORD_SECRET}
    assert derive(owner_with(good)) == good

    for label, bad in {
        "짧은 문법": _MAP_PASSWORD_SECRET,
        "target 어긋남": {**good, "target": "elsewhere"},
        "source 어긋남": {**good, "source": "other-secret"},
        "target 없음": {"source": _MAP_PASSWORD_SECRET},
        "참조가 리스트": [good],
    }.items():
        assert derive(owner_with(bad)) is None, f"{label}: 인가하면 안 된다"


# ── GM-17 B · S3-a: PinVi postgres 신원을 db-init 게이트에서 떼어낸다 ────
#
# 종전 `_validate_pinvi_db_init_identity`는 맨 앞에서 `pinvi-db-init` 부재를 즉시
# 거부한 뒤, 같은 함수 안에서 `pinvi-postgres`의 image·environment·**command**를
# 검사했다. 그 command 배열이 `listen_addresses=127.0.0.1`을 강제하는
# **저장소에서 유일한 자리**다(`backend/src` 전역 1건).
#
# 그래서 S4가 그 함수를 db-init 존재로 게이팅하면 PostgreSQL의 loopback 결박이
# 통째로 사라진다. 네트워크 노출 통제라 S2의 secret 소비자 스캔보다 결과가 나쁘다.


def _s4_without_pinvi_oneshots(
    monkeypatch: pytest.MonkeyPatch, document: dict[str, object]
) -> dict[str, object]:
    """S4가 PinVi one-shot을 scope에서 뺀 상태를 흉내낸다.

    **문서에서도 db-init을 뺀다.** 전용 validator만 no-op으로 만들고 문서에 서비스를
    남겨 두면 시뮬레이션이 가짜가 된다 — 변이로 확인했다: `pinvi-postgres` 신원 검사를
    db-init 존재로 게이팅해도 검사가 **전부 초록**이었다(게이트 조건이 여전히 참이라).
    S4가 실제로 만드는 형상은 서비스가 사라진 상태다.
    """

    monkeypatch.setattr(
        c6c_deployment_module,
        "_validate_pinvi_db_init_presence",
        lambda services, environment: ({}, {}, ("", "", "", "")),
    )
    monkeypatch.setattr(
        c6c_deployment_module,
        "_validate_pinvi_db_init_command",
        lambda service, service_environment, expected, *, resolved: None,
    )
    monkeypatch.setattr(
        c6c_deployment_module,
        "_CANDIDATE_REQUIRED_PROTECTED_SERVICES",
        frozenset(
            name
            for name in c6c_deployment_module._CANDIDATE_REQUIRED_PROTECTED_SERVICES
            if not name.startswith("pinvi-")
        ),
    )
    return _shape_without(document, ("pinvi-db-init",))


def test_loopback_binding_survives_when_db_init_is_out_of_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**S3-a의 핵심.** db-init이 scope 밖이어도 loopback 결박은 남는다.

    `listen_addresses=127.0.0.1`은 저장소에서 이 command 배열 한 곳에만 있다. 종전
    구조에서는 그것이 db-init 게이트 뒤에 있었으므로, S4가 PinVi one-shot을 빼는
    순간 PostgreSQL이 모든 인터페이스에 바인딩해도 아무도 막지 못했다.

    이 검사는 진입점을 태운다 — db-init 전용 검사를 no-op으로 만든 뒤
    `pinvi-postgres`의 바인딩을 열어 보고, 여전히 거부되는지 본다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = _s4_without_pinvi_oneshots(monkeypatch, deepcopy(candidate))
    services = shaped["services"]
    assert isinstance(services, dict)
    assert "pinvi-db-init" not in services, "시뮬레이션이 db-init을 실제로 빼야 한다"
    postgres = services["pinvi-postgres"]
    assert isinstance(postgres, dict)
    command = postgres["command"]
    assert isinstance(command, list)
    assert "listen_addresses=127.0.0.1" in command, "전제가 깨졌다 — 결박 문자열이 없다"
    postgres["command"] = [
        "listen_addresses=*" if item == "listen_addresses=127.0.0.1" else item
        for item in command
    ]

    with pytest.raises(
        ComposeCandidateContractError, match="PinVi PostgreSQL identity is invalid"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_postgres_image_provenance_survives_when_db_init_is_out_of_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 이유로 `pinvi-postgres`의 image provenance도 남는다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = _s4_without_pinvi_oneshots(monkeypatch, deepcopy(candidate))
    services = shaped["services"]
    assert isinstance(services, dict)
    assert "pinvi-db-init" not in services, "시뮬레이션이 db-init을 실제로 빼야 한다"
    postgres = services["pinvi-postgres"]
    assert isinstance(postgres, dict)
    postgres["image"] = "postgres:16"

    with pytest.raises(
        ComposeCandidateContractError,
        match="PinVi PostgreSQL image provenance is invalid",
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_loopback_binding_has_exactly_one_home() -> None:
    """`listen_addresses` 강제가 **한 곳뿐**이라는 전제를 결박한다.

    S3-a의 모든 논거가 이 사실에 기댄다. 누군가 두 번째 자리를 만들면 이 검사가
    빨개지고, 그때 위 검사들의 서사를 다시 써야 한다. 반대로 유일한 자리가 사라져도
    빨개진다.
    """

    source = (_ROOT / "backend/src/kor_travel_docker_manager").rglob("*.py")
    homes = [
        path.relative_to(_ROOT).as_posix()
        for path in source
        if "listen_addresses=127.0.0.1" in path.read_text(encoding="utf-8")
    ]
    assert homes == ["backend/src/kor_travel_docker_manager/services/c6c_deployment.py"], (
        f"loopback 결박의 자리가 바뀌었다: {homes}"
    )


def test_db_init_image_check_is_conditional_but_still_runs_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """db-init image 검사는 존재-조건부지만 **오늘은 항상 돈다**.

    그 한 줄은 db-init의 image를 보는데 자리가 두 postgres 검사 **사이**라, 옮기면
    두 결함이 동시에 있는 문서의 문구가 바뀐다. 그래서 자리를 두고 조건만 걸었다.
    이 검사가 "조건을 걸었다"가 "그냥 껐다"로 미끄러지지 않게 한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    db_init = services["pinvi-db-init"]
    assert isinstance(db_init, dict)
    db_init["image"] = "postgres:16"

    with pytest.raises(
        ComposeCandidateContractError,
        match="PinVi database init image provenance is invalid",
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    # db-init이 아예 없으면 물을 대상이 없다 — 조용히 지나간다(S4 이후의 형상).
    without = _s4_without_pinvi_oneshots(monkeypatch, shaped)
    c6c_deployment_module._validate_pinvi_postgres_identity(
        without["services"], environment, resolved=False
    )
