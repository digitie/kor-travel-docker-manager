"""F1D의 일회성 schema bootstrap Compose 경계를 회귀 고정한다."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from kor_travel_docker_manager.services import c6c_deployment as c6c_deployment_module
from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.c6c_deployment import (
    _CANDIDATE_ALLOWED_OPERATOR_BINDS,
    C6cBuildProvenance,
    DeploymentContractError,
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
    PINNED_RUNTIME_RELEASE,
)
from kor_travel_docker_manager.services.pinned_runtime_sources import (
    MaterializedRuntimeSource,
    PinnedRuntimeSourceMaterialization,
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
    assert _CANDIDATE_ALLOWED_OPERATOR_BINDS[
        ("pinvi-postgres", "/var/lib/postgresql/data", False)
    ] == "${PINVI_PGDATA:-/home/digitie/pinvi-data/pgdata}"


def test_pinvi_role_bootstrap_source_bind_is_in_canonical_candidate_allowlist() -> None:
    assert _CANDIDATE_ALLOWED_OPERATOR_BINDS[
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
    assert _CANDIDATE_ALLOWED_OPERATOR_BINDS[
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
            "bind": {},
        }
    ]

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


def test_frozen_bootstrap_compose_contract_passes_raw_and_resolved_c6c_validation(
    tmp_path: Path,
) -> None:
    """F1D reset 전에 bootstrap의 실제 profile/production 환경을 정적으로 고정한다."""

    source = _source_compose()
    assert "x-pinvi-map-ops-validation" not in source
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
    role_bootstrap_script = pinvi_source / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh"
    role_bootstrap_script.parent.mkdir(parents=True)
    role_bootstrap_script.write_text(
        "#!/bin/sh\nruntime=PINVI_APP_DB_PASSWORD\nmigrator=PINVI_MIGRATOR_DB_PASSWORD\n",
        encoding="utf-8",
    )
    environment["PINVI_REPO_DIR"] = str(pinvi_source)
    for environment_name, directory_name in (
        ("KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR", "application-permit"),
        ("KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR", "metadata-permit"),
        ("KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR", "root-fence"),
        ("KOR_TRAVEL_MAP_APPLICATION_FRESH_FINALIZE_FENCE_DIR", "finalize-fence"),
    ):
        directory = tmp_path / directory_name
        directory.mkdir()
        environment[environment_name] = str(directory)

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
    map_revision = PINNED_RUNTIME_RELEASE.source_for("map").revision
    pinvi_revision = PINNED_RUNTIME_RELEASE.source_for("pinvi").revision
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
        release=PINNED_RUNTIME_RELEASE,
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
