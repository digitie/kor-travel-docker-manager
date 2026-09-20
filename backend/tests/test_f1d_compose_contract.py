"""F1D의 일회성 schema bootstrap Compose 경계를 회귀 고정한다."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
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
from kor_travel_docker_manager.services.docker_service import (
    ContainerConfigValidationError,
    validate_container_config_update,
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


def _shared_postgres_contract_pgdata() -> str:
    """ADR-46 — kor-travel-shared-postgres resolved bind이 요구하는, 실제로 존재하는 경로.

    `PINVI_PGDATA`(362행)는 이미 존재하는 checkout 디렉터리를 우연히 재사용하는
    기존 관행이다. 여기서는 그 관행에 기대지 않고 직접 만든다 — 이 값이 없는
    환경(CI 등)에서도 안전하다.
    """

    path = Path(tempfile.gettempdir()) / "ktdm-shared-postgres-contract-pgdata"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _compose_contract_environment() -> dict[str, str]:
    return {
        **os.environ,
        "KOR_TRAVEL_SHARED_PGDATA": _shared_postgres_contract_pgdata(),
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
        # ADR-46 — PinVi 앱/Dagster DSN이 실제로 접속하는 공용 instance의 cluster
        # 관리자 비밀번호. `kor-travel-shared-db-init-pinvi`/`pinvi-shared-db-runtime-role`
        # 둘 다 이 secret을 참조한다.
        "KOR_TRAVEL_SHARED_POSTGRES_PASSWORD": "shared-contract-postgres-password",
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
                # ADR-46 — 공용 instance 두 서비스도 같은 이유로 실제 정의가 필요하다:
                # `kor-travel-shared-postgres`는 `_declared_postgres_compose_services()`에
                # 등록돼 있어(config/docker-targets.yml), alpine stub으로 두면
                # POSTGRES_INITDB_ARGS가 없다며 전역 술어가 거부한다.
                if dependency in {
                    "pinvi-postgres",
                    "pinvi-db-init",
                    "kor-travel-shared-postgres",
                    "kor-travel-shared-db-init-pinvi",
                }:
                    services[dependency] = deepcopy(source_services[dependency])
                else:
                    # 실제 resolver가 dependency graph를 검증하게 이름만 최소 stub으로 둔다.
                    services[dependency] = {"image": "alpine:3.20"}

    fragment: dict[str, object] = {"services": services}
    if (
        "kor-travel-map-postgres" in services
        or "pinvi-postgres" in services
        or "kor-travel-shared-postgres" in services
    ):
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
        if "kor-travel-shared-postgres" in services:
            fragment["secrets"]["kor-travel-shared-postgres-password"] = deepcopy(
                source_secrets["kor-travel-shared-postgres-password"]
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
        "127.0.0.1:11000/wrong_database"
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
        "127.0.0.1:11000/pinvi"
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
            f"postgresql+asyncpg://pinvi_contract_app:{leaked_password}@127.0.0.1:11000/pinvi"
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
        "postgresql+asyncpg://pinvi_contract_app:pinvi-contract-app-password@127.0.0.1:11000/pinvi"
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

    # ADR-46 — 앱/Dagster DSN이 실제로 접속하는 공용 instance 포트도 같은 자리에서
    # 독립적으로 고정된다.
    wrong_pinvi_shared_port = dict(environment)
    wrong_pinvi_shared_port["KOR_TRAVEL_SHARED_DB_PORT"] = "12900"
    with pytest.raises(DeploymentContractError, match="PinVi database URL identity"):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=wrong_pinvi_shared_port,
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
        "127.0.0.1:11000/pinvi"
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
        "127.0.0.1:11000/pinvi"
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
        "127.0.0.1:11000/pinvi"
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
        # 이 값의 주인은 이제 전역 술어다 — 서비스를 열거하는 방식이 저장소의
        # PostgreSQL 넷 중 둘(geo·concierge)을 빠뜨렸던 것이 적대 리뷰 2026-09-18 F1.
        with pytest.raises(DeploymentContractError, match="non-canonical POSTGRES_INITDB_ARGS"):
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
        with pytest.raises(DeploymentContractError, match="non-canonical POSTGRES_INITDB_ARGS"):
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


def test_pinvi_shared_db_runtime_role_bind_leak_exemption_matches_dedicated_instance(
    tmp_path: Path,
) -> None:
    """ADR-46 shared-instance one-shot도 dedicated-instance와 같은 identifier-only 면제를 받는다.

    `pinvi-shared-db-runtime-role`은 `pinvi-db-runtime-role`과 완전히 같은
    `bootstrap-pinvi-runtime-role.sh`를 그대로 마운트한다(docker-compose.yml 주석
    실측, 두 서비스 모두 같은 `PINVI_REPO_DIR` 기준 경로). 이 테스트를 추가하기 전
    코드는 `_PINVI_DB_RUNTIME_ROLE_SERVICE`(dedicated 이름)만 면제했고,
    `_PINVI_SHARED_DB_RUNTIME_ROLE_SERVICE`는 일반 스캔으로 떨어져 스크립트가 선언하는
    role/password env 이름(`PINVI_APP_DB_PASSWORD` 등, protected_names의 일부)만으로도
    거짓 양성 "bind source leaks C6c data"를 냈다 — n150 실배포 `t52a` 재구축이 바로
    이 자리에서 막혔다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    source_services = _source_compose()["services"]
    for name in (
        "pinvi-shared-db-runtime-role",
        "kor-travel-shared-postgres",
        "kor-travel-shared-db-init-pinvi",
    ):
        if name not in candidate["services"]:
            candidate["services"][name] = deepcopy(source_services[name])
    role_bootstrap_script = (
        Path(environment["PINVI_REPO_DIR"]) / "infra" / "postgres" / "bootstrap-pinvi-runtime-role.sh"
    )

    raw_snapshots = validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )
    assert raw_snapshots is not None

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


def test_every_protected_env_reference_in_compose_sits_at_a_registered_path() -> None:
    """compose가 보호 env를 참조하면 그 경로가 **같은 커밋에서** 계약에 등록돼야 한다.

    `validate_compose_candidate_protected_values`는 문서 전체의 스칼라를 훑어
    보호 이름/값이 등장하는데 `allowed_paths`에 없으면
    `compose candidate leaks a protected C6c reference`로 fail-close한다. 그런데
    그 실패는 CI가 아니라 **n150의 핀 재구축 시점에만** 드러나고, 러너가 사유를
    `prebuild_snapshot` 한 단어로 봉인해 원인이 보이지 않는다.

    2026-09-19에 정확히 그렇게 깨졌다 — #356이 `pinvi-dagster-daemon`을, #358이
    `pinvi-dagster-code-server`를 더하면서 compose에는 PinVi DSN을 넣었지만
    `_PINVI_DATABASE_URL_ALLOWED_PATHS`에는 등록하지 않았다. 그 사이 재구축이
    한 번도 돌지 않아 **핀 재구축 전부가 조용히 막힌 채** 있었고, ADR-099 2단계
    배포가 처음으로 그것을 밟았다. `_validate_candidate_volume_graph`의 bind
    allowlist가 #318에서 겪은 것과 같은 부류다.

    여기서 같은 조건을 정적으로 건다 — 런타임 검사와 **같은 집합**을 써서,
    한쪽만 넓히면 다른 쪽이 빨개지도록.
    """

    # **치환하지 않는다.** 이 검사가 보는 것은 값이 아니라 `${PINVI_APP_DB_PASSWORD…}`
    # 같은 **이름의 등장 위치**이고, 런타임 검사도 raw 스칼라를 그대로 훑는다.
    document = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))

    protected_names = c6c_deployment_module._CANDIDATE_PROTECTED_VALUE_ENV_NAMES
    allowed_paths = (
        {
            ("services", service_name, "environment", target_name)
            for service_name, target_name in (
                c6c_deployment_module._CANDIDATE_CANONICAL_API_ENV_VALUES
            )
        }
        | c6c_deployment_module._DATABASE_ALLOWED_NON_ENV_PATHS
        | c6c_deployment_module._PINVI_DATABASE_URL_ALLOWED_PATHS
    )

    unregistered: list[tuple[str, tuple[str, ...]]] = []
    seen = 0
    for path, scalar in c6c_deployment_module._walk_scalars(document):
        value = "" if scalar is None else str(scalar)
        names = sorted(name for name in protected_names if name in value)
        if not names:
            continue
        seen += 1
        if path in allowed_paths:
            continue
        if path[-1:] == ("<key>",) and path[:-1] in allowed_paths:
            continue
        # 값이 아니라 **이름**만 보여준다. 값은 자격증명일 수 있다.
        unregistered.append((names[0], path))

    assert seen >= 5, (
        f"보호 env를 참조하는 스칼라를 {seen}개만 찾았다 — `_walk_scalars`나 보호 "
        "집합이 바뀌었으면 이 검사는 항진명제가 된다."
    )
    assert not unregistered, (
        "compose가 보호 C6c 참조를 등록되지 않은 경로에서 쓴다 — 이 상태로는 "
        "**모든 핀 재구축**이 prebuild_snapshot에서 fail-close한다: "
        + ", ".join(
            f"{name} @ {'.'.join(str(part) for part in path)}"
            for name, path in unregistered
        )
    )


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
#: 정체다 — `frozenset` 14 + `_validate_pinvi_db_init_presence`의 별도 강제.
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
    required 집합에는 없고, 그 보증의 출처는 `_validate_pinvi_db_init_presence`다 —
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
    # `_validate_pinvi_db_init_presence`가 먼저 걸러서 정체성 오류로 보고한다.
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
    # **required 목록은 두 곳이다.** frozenset만 패치하면 하드코딩 15개 소비자 루프가
    # 여전히 부재를 거부해서, 시뮬레이션이 목표 지점에 닿기도 전에 막힌다(적대 리뷰
    # 2026-09-17 L-2). `docs/tasks.md`가 "S4는 둘 다 풀어야 한다"고 적어 둔 그 루프다.
    monkeypatch.setattr(
        c6c_deployment_module,
        "_CANDIDATE_KNOWN_SERVICE_NAMES",
        frozenset(
            name
            for name in c6c_deployment_module._CANDIDATE_KNOWN_SERVICE_NAMES
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


def test_the_loopback_binding_has_two_load_bearing_homes(tmp_path: Path) -> None:
    """`listen_addresses` 강제의 **자리와 개수**를 결박한다.

    S3-a 때는 한 곳뿐이었다 — PinVi의 신원 검사가 command 배열을 exact-match 하는
    자리다. 그것이 저장소의 네 PostgreSQL 중 **하나만** 지킨다는 것이 오래된 열린
    항목이었고, 적대 리뷰 2026-09-18이 실측으로 확인했다(map은 `listen_addresses=*`로
    바꿔도 통과했다).

    그래서 두 번째 자리를 **의도적으로** 만들었다: postgres 서버를 효과로 식별하는
    전역 바닥이다. 둘은 일이 다르고 **둘 다 결박돼 있다** —

      전역 바닥을 지우면  geo·concierge·map의 loopback 강제가 사라진다
      PinVi 쪽을 지우면   exact-match가 느슨해진다

    "중복은 결박을 없앤다"는 이 저장소의 교훈이 여기에는 적용되지 않는다 — 그 교훈의
    조건은 "어느 쪽을 지워도 아무 검사가 빨개지지 않는다"인데, 그것이 성립하지 않는다.

    **AST 리터럴 개수로 세지 않는다.** 전역 바닥이 값을 부분으로 나눠 표현하게 되면서
    리터럴 수가 자리 수와 갈렸다 — 그때 개수 세기는 결박이 아니라 잡음이 된다
    (`detector-floors-count-what-was-seen`). **효과로 센다.**
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)

    # (1) PinVi 쪽 — exact-match가 자기 문구로 먼저 말한다.
    pinvi_shaped = deepcopy(candidate)
    pinvi_services = pinvi_shaped["services"]
    assert isinstance(pinvi_services, dict)
    pinvi_postgres = pinvi_services["pinvi-postgres"]
    assert isinstance(pinvi_postgres, dict)
    pinvi_command = list(pinvi_postgres["command"])
    pinvi_command[pinvi_command.index("listen_addresses=127.0.0.1")] = (
        "listen_addresses=*"
    )
    pinvi_postgres["command"] = pinvi_command
    with pytest.raises(
        ComposeCandidateContractError, match="PinVi PostgreSQL identity is invalid"
    ):
        validate_compose_candidate_protected_values(
            pinvi_shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    # (2) 전역 바닥 — PinVi가 보지 않는 서비스를 자기 문구로 거부한다.
    map_shaped = deepcopy(candidate)
    map_services = map_shaped["services"]
    assert isinstance(map_services, dict)
    map_postgres = map_services["kor-travel-map-postgres"]
    assert isinstance(map_postgres, dict)
    map_command = list(map_postgres["command"])
    map_command[map_command.index("listen_addresses=127.0.0.1")] = "listen_addresses=*"
    map_postgres["command"] = map_command
    with pytest.raises(ComposeCandidateContractError, match="loopback binding"):
        validate_compose_candidate_protected_values(
            map_shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
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


# ── GM-17 B · S3-b: PinVi password validator 삼분할 ──────────────────────
#
# Map(S2)과 같은 모양이되 인가 집합이 셋이다 — 소유자(`pinvi-postgres`)는 파생,
# `pinvi-db-init`·`pinvi-db-runtime-role`은 리터럴이다. 리터럴 둘은 소유자와 무관하므로
# 소유자가 없어도 유효하고, 그 사실이 스캔을 소유자로부터 독립시킨다.
#
# S2에서 적대 리뷰가 실제로 뚫은 것만 골라 결박한다: 진입점 결박, 파생의 모양 검증,
# 짧은 문법 축.

_PINVI_PASSWORD_SECRET = "pinvi-postgres-password"


def _pinvi_document_with_foreign_consumer(
    *, include_owner: bool, shorthand: bool = False
) -> dict[str, object]:
    """PinVi password secret을 **인가되지 않은 서비스**가 가져가는 문서."""

    foreign_reference: object = (
        _PINVI_PASSWORD_SECRET
        if shorthand
        else {"source": _PINVI_PASSWORD_SECRET, "target": _PINVI_PASSWORD_SECRET}
    )
    services: dict[str, object] = {
        "some-other-service": {
            "image": "example:latest",
            "secrets": [foreign_reference],
        }
    }
    if include_owner:
        services["pinvi-postgres"] = {
            "image": "postgis:latest",
            "environment": {
                "POSTGRES_PASSWORD_FILE": f"/run/secrets/{_PINVI_PASSWORD_SECRET}"
            },
            "secrets": [
                {"source": _PINVI_PASSWORD_SECRET, "target": _PINVI_PASSWORD_SECRET}
            ],
        }
    return {
        "secrets": {
            _PINVI_PASSWORD_SECRET: {"environment": "PINVI_POSTGRES_PASSWORD"}
        },
        "services": services,
    }


@pytest.mark.parametrize("shorthand", [False, True], ids=["long", "shorthand"])
@pytest.mark.parametrize("include_owner", [True, False], ids=["owner", "no-owner"])
def test_pinvi_sole_consumer_scan_rejects_a_foreign_consumer(
    include_owner: bool, shorthand: bool
) -> None:
    """인가되지 않은 소비자는 **두 문법 모두** 거부된다 — 소유자 유무와 무관하게."""

    document = _pinvi_document_with_foreign_consumer(
        include_owner=include_owner, shorthand=shorthand
    )
    with pytest.raises(
        ComposeCandidateContractError, match="unauthorized consumer"
    ):
        c6c_deployment_module._assert_pinvi_postgres_password_sole_consumer(document)


def test_pinvi_entry_point_runs_the_consumer_scan(tmp_path: Path) -> None:
    """소비자 스캔이 **진입점에서** 실제로 불린다.

    S2에서 배운 것: 쪼갠 함수를 직접 태우는 검사만으로는 호출부의 게이팅을 잡지 못한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["some-other-service"] = {
        "image": "example:latest",
        "secrets": [_PINVI_PASSWORD_SECRET],
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


def test_pinvi_entry_point_checks_the_secret_declaration(tmp_path: Path) -> None:
    """선언 검사가 **진입점에서** 실제로 불린다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    secrets = shaped["secrets"]
    assert isinstance(secrets, dict)
    secrets[_PINVI_PASSWORD_SECRET] = {"environment": "KOR_TRAVEL_MAP_POSTGRES_PASSWORD"}

    with pytest.raises(
        ComposeCandidateContractError,
        match="PinVi PostgreSQL password secret is invalid",
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_pinvi_authorized_reference_requires_a_valid_shape() -> None:
    """인가 파생은 **모양까지** 본다 — PinVi는 Map보다 느슨해서 더 중요하다.

    PinVi 소유자 참조는 짧은 문법과 **두 가지 target**을 허용한다. 그래서 파생을
    무검증으로 두면 Map보다 위험하다(S2 적대 리뷰 M1을 여기서는 처음부터 적용했다).
    """

    derive = c6c_deployment_module._authorized_pinvi_postgres_password_reference

    def owner_with(reference: object) -> dict[str, object]:
        return {"services": {"pinvi-postgres": {"secrets": [reference]}}}

    # 허용되는 세 모양
    assert derive(owner_with(_PINVI_PASSWORD_SECRET)) == _PINVI_PASSWORD_SECRET
    for target in (_PINVI_PASSWORD_SECRET, f"/run/secrets/{_PINVI_PASSWORD_SECRET}"):
        reference = {"source": _PINVI_PASSWORD_SECRET, "target": target}
        assert derive(owner_with(reference)) == reference

    # 거부되는 모양들
    for label, bad in {
        "target 어긋남": {"source": _PINVI_PASSWORD_SECRET, "target": "elsewhere"},
        "source 어긋남": {"source": "other", "target": _PINVI_PASSWORD_SECRET},
        "target 없음": {"source": _PINVI_PASSWORD_SECRET},
        "참조가 리스트": [{"source": _PINVI_PASSWORD_SECRET}],
        "다른 secret 이름": "some-other-secret",
    }.items():
        assert derive(owner_with(bad)) is None, f"{label}: 인가하면 안 된다"

    assert derive({"services": {}}) is None
    assert derive({"services": {"pinvi-postgres": {"secrets": []}}}) is None


def test_pinvi_literal_allowances_survive_without_the_owner() -> None:
    """리터럴 인가 둘(`pinvi-db-init`·`pinvi-db-runtime-role`)은 소유자와 무관하다.

    이 사실이 PinVi 스캔을 소유자로부터 독립시킨다 — 소유자가 사라져도 one-shot들의
    정당한 소비는 계속 인가되고, 그 밖은 계속 거부된다.
    """

    document = _pinvi_document_with_foreign_consumer(include_owner=False)
    services = document["services"]
    assert isinstance(services, dict)
    del services["some-other-service"]
    services["pinvi-db-init"] = {
        "image": "postgis:latest",
        "secrets": [_PINVI_PASSWORD_SECRET],
    }
    services["pinvi-db-runtime-role"] = {
        "image": "postgis:latest",
        "secrets": [
            {
                "source": _PINVI_PASSWORD_SECRET,
                "target": f"/run/secrets/{_PINVI_PASSWORD_SECRET}",
            }
        ],
    }

    # 소유자가 없어도 정당한 소비는 통과한다.
    c6c_deployment_module._assert_pinvi_postgres_password_sole_consumer(document)

    # 같은 one-shot이라도 모양이 다르면 거부된다.
    runtime_role = services["pinvi-db-runtime-role"]
    assert isinstance(runtime_role, dict)
    runtime_role["secrets"] = [_PINVI_PASSWORD_SECRET]
    with pytest.raises(
        ComposeCandidateContractError, match="unauthorized consumer"
    ):
        c6c_deployment_module._assert_pinvi_postgres_password_sole_consumer(document)


def test_pinvi_global_invariants_are_not_inside_the_family_validator() -> None:
    """전역 불변식 둘은 PinVi family validator **밖**에 있어야 한다.

    S2에서 이 자리를 두 번 틀렸다 — 처음에는 validator 안에 두었고, 다음에는 이중화해서
    어느 쪽을 지워도 아무 검사가 빨개지지 않았다. 자리가 하나여야 결박이 성립한다.
    """

    import inspect

    wiring_source = inspect.getsource(
        c6c_deployment_module._validate_pinvi_postgres_password_owner_wiring
    )
    for forbidden in (
        "_assert_pinvi_postgres_password_sole_consumer",
        "_validate_pinvi_postgres_password_declaration",
    ):
        assert forbidden not in wiring_source, (
            f"전역 불변식 {forbidden}이 family validator 안으로 들어왔다"
        )


# ── S3 적대 리뷰 반영: PinVi에도 Map(S2)의 결박을 건다 ───────────────────
#
# 리뷰가 실측했다 — "Map(S2)과 같은 배치다"는 **코드 배치에 대해서만** 참이었고
# **검증에 대해서는 거짓**이었다. S2가 Map에 넣은 네 검사가 PinVi에 복제되지 않아,
# PinVi 전역 둘을 게이팅하거나 owner gate 뒤로 인라인하는 변이 넷이 전부 초록이었다.
#
# 그리고 `..._not_inside_the_family_validator`는 **이름에 결박**돼 있어 인라인 한 번에
# 뚫린다(리뷰 H-3). 이름 grep은 싸니까 두되, 아래가 **효과**를 센다.


def test_pinvi_entry_points_keep_the_global_checks_when_the_owner_is_out_of_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**S4를 오늘 시뮬레이션한다** — PinVi 소유자가 scope 밖이어도 전역 둘이 산다.

    Map에는 S2가 같은 검사를 넣었고(적대 리뷰가 두 번 뚫은 뒤에), PinVi에는 빠져
    있었다. 이 검사 하나가 리뷰가 보고한 생존 변이 일곱(전역 둘 게이팅·인라인·간접
    호출, 소유자 배선의 핵심 검사 셋)을 한꺼번에 덮는다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    for name in ("_CANDIDATE_REQUIRED_PROTECTED_SERVICES", "_CANDIDATE_KNOWN_SERVICE_NAMES"):
        monkeypatch.setattr(
            c6c_deployment_module,
            name,
            frozenset(
                value
                for value in getattr(c6c_deployment_module, name)
                if not value.startswith("pinvi-")
            ),
        )

    base = _shape_without(
        candidate,
        ("pinvi-postgres", "pinvi-db-init", "pinvi-db-runtime-role", "pinvi-api"),
    )

    # (1) 무단 소비자는 소유자가 없어도 거부된다 — 두 문법 모두.
    for shorthand in (False, True):
        shaped = deepcopy(base)
        services = shaped["services"]
        assert isinstance(services, dict)
        assert "pinvi-postgres" not in services
        services["zz-thief"] = {
            "image": "example:latest",
            "secrets": [
                "pinvi-postgres-password"
                if shorthand
                else {
                    "source": "pinvi-postgres-password",
                    "target": "pinvi-postgres-password",
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
        assert "unauthorized consumer" in str(rejection.value), f"shorthand={shorthand}"

    # (2) 선언이 틀리면 소유자가 없어도 거부된다.
    shaped = deepcopy(base)
    secrets = shaped["secrets"]
    assert isinstance(secrets, dict)
    secrets["pinvi-postgres-password"] = {"environment": "WRONG_ENV"}
    with pytest.raises(
        ComposeCandidateContractError,
        match="PinVi PostgreSQL password secret is invalid",
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_pinvi_owner_must_receive_the_password_only_through_the_secret_file(
    tmp_path: Path,
) -> None:
    """소유자 배선의 **핵심 셋**을 센다 (적대 리뷰 M-2).

    함수 docstring이 "`pinvi-postgres`가 secret file로만 password를 받는가"라고
    선언하는데, 그 문장을 실현하는 세 줄을 지워도 1,721건이 전부 초록이었다.
    Map에는 S2가 같은 검사를 넣었고 PinVi에는 빠져 있었다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)

    def reject(mutate: object, expected: str) -> None:
        shaped = deepcopy(candidate)
        services = shaped["services"]
        assert isinstance(services, dict)
        owner = services["pinvi-postgres"]
        assert isinstance(owner, dict)
        mutate(owner)  # type: ignore[operator]
        with pytest.raises(ComposeCandidateContractError) as rejection:
            validate_compose_candidate_protected_values(
                shaped,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )
        assert expected in str(rejection.value), f"{expected} 기대, 실제 {rejection.value}"

    message = "PinVi PostgreSQL password secret is invalid"
    # PASSWORD_FILE을 Map secret으로 돌려놓기 / 삭제
    reject(
        lambda owner: owner["environment"].__setitem__(
            "POSTGRES_PASSWORD_FILE", "/run/secrets/kor-travel-map-postgres-password"
        ),
        message,
    )
    reject(lambda owner: owner["environment"].pop("POSTGRES_PASSWORD_FILE"), message)
    # 참조를 두 번 마운트 / 모양이 어긋난 참조
    reject(
        lambda owner: owner.__setitem__(
            "secrets", [*owner["secrets"], {"source": "pinvi-postgres-password"}]
        ),
        message,
    )
    reject(
        lambda owner: owner.__setitem__(
            "secrets", [{"source": "pinvi-postgres-password", "target": "/elsewhere"}]
        ),
        message,
    )


def test_entrypoint_override_cannot_defeat_the_loopback_binding(tmp_path: Path) -> None:
    """**command를 한 글자도 안 바꾸고** loopback을 무력화하는 경로를 막는다 (리뷰 M-1).

    Compose에서 `entrypoint`를 주면 `command` 배열은 그 entrypoint의 **인자**가 된다.
    즉 고정된 command 검사를 전부 통과하면서 실제로는 다른 명령이 돈다. 그 유일한
    방어가 `entrypoint not in (None, [])` 한 줄인데 **지워도 1,721건이 전부 초록**이었다.

    S3-a가 그 줄을 "loopback을 지키는 함수"로 옮겨 서사의 무게를 실었으므로, 검사도
    함께 옮긴다 — `test_loopback_binding_survives_...`가 command만 변조하는 한
    그 검사가 초록이라는 사실은 "결박이 살아 있다"를 뜻하지 않는다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)

    for service, expected in (
        ("pinvi-postgres", "PinVi PostgreSQL identity is invalid"),
        ("pinvi-db-init", "PinVi database init command is invalid"),
    ):
        shaped = deepcopy(candidate)
        services = shaped["services"]
        assert isinstance(services, dict)
        target = services[service]
        assert isinstance(target, dict)
        before = deepcopy(target.get("command"))
        target["entrypoint"] = ["sh", "-ec", "exec postgres -c listen_addresses=*"]
        assert target.get("command") == before, "command는 건드리지 않는다 — 그것이 요점이다"

        with pytest.raises(ComposeCandidateContractError) as rejection:
            validate_compose_candidate_protected_values(
                shaped,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )
        assert expected in str(rejection.value), (
            f"{service}: entrypoint 우회가 거부되지 않았다 — {rejection.value}"
        )


# ── PostgreSQL 인증 우회 두 경로 ─────────────────────────────────────────
#
# 2026-09-17 감사가 Map↔PinVi 60필드를 전수로 재서 찾았다. Map postgres에는 PinVi의
# `_validate_pinvi_postgres_identity`에 해당하는 **서비스 신원 validator가 없어서**
# image·command(`listen_addresses` 포함)·entrypoint·`POSTGRES_INITDB_ARGS`가 통째로
# 무검사였다. 그리고 `POSTGRES_HOST_AUTH_METHOD`는 **양쪽 모두** 무검사였다 — 계약
# 기계가 값-동등 비교라 "키가 추가됐다"를 표현하지 못하기 때문이다.
#
# 두 값 다 fresh PGDATA의 인증을 끄는 데 쓰인다. 전자는 initdb가 쓰는 `pg_hba.conf`의
# host 행을, 후자는 entrypoint가 같은 행을 통째로 덮어쓴다.


@pytest.mark.parametrize(
    "service",
    ["kor-travel-map-postgres", "pinvi-postgres"],
)
def test_initdb_trust_auth_is_rejected_for_both_postgres_services(
    service: str, tmp_path: Path
) -> None:
    """`POSTGRES_INITDB_ARGS=--auth-host=trust`는 **양쪽 다** 거부된다.

    종전에는 Map 쪽만 통과했다(raw·resolved·UI 저장 경로 전부). PinVi에는 신원
    validator가 그 값을 강제하는데 Map에는 대응물이 없었고, 계약표에도 없었다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services[service]
    assert isinstance(postgres, dict)
    postgres_environment = postgres["environment"]
    assert isinstance(postgres_environment, dict)
    assert postgres_environment["POSTGRES_INITDB_ARGS"] == "--auth-host=scram-sha-256", (
        "전제가 깨졌다 — 정본 값이 바뀌었다"
    )
    postgres_environment["POSTGRES_INITDB_ARGS"] = "--auth-host=trust"

    with pytest.raises(ComposeCandidateContractError) as rejection:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    assert "POSTGRES_INITDB_ARGS" in str(rejection.value) or "identity is invalid" in str(
        rejection.value
    ), f"{service}: trust 초기화가 거부되지 않았다 — {rejection.value}"


@pytest.mark.parametrize(
    "service",
    ["kor-travel-map-postgres", "pinvi-postgres", "kor-travel-map-api"],
)
def test_host_auth_method_override_is_rejected_anywhere(
    service: str, tmp_path: Path
) -> None:
    """`POSTGRES_HOST_AUTH_METHOD`는 **어느 서비스에서도** 거부된다.

    이것이 계약표로 막히지 않는 이유가 핵심이다 — 계약표는 **값 동등**을 비교하므로
    나열된 키의 값만 본다. **키가 추가된 것**은 표현할 수 없다. 그래서 이름 자체를
    금지하고, 그 금지를 **전역**으로 둔다(어느 서비스가 들고 있든 결과가 같다).

    세 번째 파라미터가 postgres가 아닌 것은 의도적이다 — 이 금지가 특정 서비스의
    존재에 매이지 않는다는 것을 센다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    target = services[service]
    assert isinstance(target, dict)
    target.setdefault("environment", {})
    target_environment = target["environment"]
    assert isinstance(target_environment, dict)
    target_environment["POSTGRES_HOST_AUTH_METHOD"] = "trust"

    with pytest.raises(
        ComposeCandidateContractError,
        match="overrides PostgreSQL host authentication",
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_two_postgres_services_share_one_initdb_contract() -> None:
    """두 PostgreSQL이 **같은** 초기화 인증 인자를 쓴다 — 한쪽만 바뀌는 것을 막는다.

    이 비대칭이 바로 구멍의 원인이었다: PinVi에는 강제가 있고 Map에는 없었다.
    공유 상수를 쓰는지 값으로 확인한다.
    """

    canonical = c6c_deployment_module._POSTGRES_CANONICAL_INITDB_ARGS
    assert canonical == "--auth-host=scram-sha-256"
    # `_PINVI_POSTGRES_INITDB_ARGS`는 제거했다 — 값 고정의 자리가 전역 술어 하나로
    # 옮겨간 뒤로 아무도 읽지 않는 죽은 별칭이었다(적대 리뷰 2026-09-18 라운드4 F13).
    assert not hasattr(c6c_deployment_module, "_PINVI_POSTGRES_INITDB_ARGS")
    assert (
        c6c_deployment_module._MAP_DATABASE_CANONICAL_ENV_VALUES[
            ("kor-travel-map-postgres", "POSTGRES_INITDB_ARGS")
        ]
        == canonical
    )
    # UI 저장 경로의 잠금이 같은 dict에서 파생되는지도 센다 — 계약표에 넣은 효과가
    # 검증 경로에만 머무르지 않는다는 것이 이 수정의 절반이다.
    locked = c6c_deployment_module._CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE
    assert "POSTGRES_INITDB_ARGS" in locked["kor-travel-map-postgres"]


# ── Concierge UI 게이트가 API 계약을 함께 끄던 것 ────────────────────────
#
# 2026-09-17 감사(14 에이전트)가 "존재 게이트 뒤에 숨은 전역 불변식"의 **네 번째
# 인스턴스**를 찾았다. S2·S3-a·S3-c와 달리 이것은 S4를 기다리지 않았다 — main에서
# 이미 열려 있었다.
#
# `_validate_concierge_ui_canonical_contract`가 `UI not in services`면 함수째
# return하고, 그 안에 **API의 계약 전부와 Manager root env 불변식**이 있었다.
# concierge 두 서비스는 required set 밖이라 UI 한 줄만 지우면 API는 남은 채 전부
# 꺼졌다. 방향도 비대칭이었다 — API 삭제는 거부, **UI 삭제는 통과**.
#
# 처방이 한 번 틀렸던 것도 함께 적는다. "API가 있으면 검사"로 고치자 정당한 Map
# 단독 후보가 9건 빨개졌다 — 의존성으로만 끌려온 서비스는 `image` 하나뿐인 **stub**
# 이고(`concierge-api`·`geo-api`·`rustfs`가 같은 모양) Map API가 concierge를 HTTP로
# 부르므로 stub이 정상이다. 그래서 **stub이냐 구성됨이냐**로 가른다.


def _configured_concierge_api(*, auth_enabled: str = "false") -> dict[str, object]:
    """감사가 통과시킨 그 형상 — 구성됐지만 안전하지 않은 concierge-api."""

    return {
        "image": "kor-travel-concierge-api:latest",
        "network_mode": "bridge",
        "command": ["uvicorn", "app:app", "--host", "0.0.0.0"],
        "environment": {
            "KTC_ADMIN_PROXY_SECRET": "attacker-chosen-secret",
            "APP_ENV": "development",
            "API_AUTH_ENABLED": auth_enabled,
            "API_KEYS": "",
        },
    }


def test_configured_concierge_api_is_validated_without_the_ui_service(
    tmp_path: Path,
) -> None:
    """**구멍이 닫혔다.** UI가 없어도 구성된 API는 계약을 받는다.

    감사가 실측한 착취 형상을 그대로 재현한다 — `API_AUTH_ENABLED=false` +
    `network_mode: bridge` + 임의 proxy secret인 concierge-api를 두고 UI는 넣지
    않는다. 종전에는 두 진입점 모두 ACCEPT였다.

    API는 `--host 0.0.0.0`이므로 인증이 꺼지는 대상이 전 인터페이스라는 점이
    이 형상의 심각도를 정한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    assert "kor-travel-concierge-ui" not in services, "전제: fixture에 UI가 없다"
    services["kor-travel-concierge-api"] = _configured_concierge_api()

    with pytest.raises(ComposeCandidateContractError) as rejection:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    assert "Concierge" in str(rejection.value), (
        f"구성된 API가 UI 없이 검사를 빠져나갔다 — {rejection.value}"
    )


def test_a_dependency_stub_concierge_api_still_passes(tmp_path: Path) -> None:
    """**정당한 형상은 그대로 통과한다.** 의존성 stub에 계약을 요구하지 않는다.

    이 검사가 없으면 "구멍을 막았다"가 "정당한 배포를 막았다"로 미끄러진다.
    실제로 첫 처방이 그랬다 — `test_frozen_bootstrap_...`을 포함해 9건이 빨개졌다.

    raw stub은 `['image']`이고 resolved stub은 `docker compose config`가 붙인
    `command: null`·`entrypoint: null`까지 갖는다. 그래서 "구성됨" 판정은 **키 존재가
    아니라 값이 있는지**로 해야 한다 — 키로 하면 resolved에서 모든 stub이 오인된다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    services = candidate["services"]
    assert isinstance(services, dict)
    api = services["kor-travel-concierge-api"]
    assert isinstance(api, dict)
    assert sorted(api) == ["image"], f"전제: raw stub은 image 하나뿐이다 — {sorted(api)}"

    # 기동 후보 그대로 통과해야 한다(아무 예외도 없이).
    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )

    # resolved stub은 값이 None인 키를 더 갖는다 — 그래도 stub이다.
    resolved = _bootstrap_resolved(environment)
    resolved_api = resolved["services"]["kor-travel-concierge-api"]
    assert isinstance(resolved_api, dict)
    assert resolved_api.get("command") is None
    assert resolved_api.get("environment") is None
    validate_resolved_compose_candidate_protected_values(
        resolved,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )


def test_removing_the_ui_is_no_longer_a_way_to_disable_the_api_contract(
    tmp_path: Path,
) -> None:
    """비대칭이 사라졌다 — 어느 쪽을 지워도 구성된 API는 검사를 받는다.

    감사가 지적한 것이 정확히 이 비대칭이었다: API를 지우면 거부되고 UI를 지우면
    통과했다. 같은 편집이 Map superuser password에 대해서는 전역 불변식에 잡히는데
    concierge에서는 빠져나갔다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)

    def verdict(services_mutation: object) -> str:
        shaped = deepcopy(candidate)
        services = shaped["services"]
        assert isinstance(services, dict)
        services_mutation(services)  # type: ignore[operator]
        try:
            validate_compose_candidate_protected_values(
                shaped,
                compose_path=str(_COMPOSE_PATH),
                root_env_path=str(root_env),
                environment=environment,
            )
        except ComposeCandidateContractError as exc:
            return f"REJECT: {exc}"
        return "ACCEPT"

    def configured_api_without_ui(services: dict[str, object]) -> None:
        services.pop("kor-travel-concierge-ui", None)
        services["kor-travel-concierge-api"] = _configured_concierge_api()

    def configured_api_with_auth_on(services: dict[str, object]) -> None:
        services.pop("kor-travel-concierge-ui", None)
        services["kor-travel-concierge-api"] = _configured_concierge_api(
            auth_enabled="true"
        )

    # 둘 다 거부돼야 한다 — auth를 켜도 나머지 계약(network_mode·command·proxy 권위)이
    # 어긋나 있으므로, "auth만 켜면 통과"가 되어서는 안 된다.
    for label, mutation in (
        ("auth off", configured_api_without_ui),
        ("auth on", configured_api_with_auth_on),
    ):
        result = verdict(mutation)
        assert result.startswith("REJECT"), f"{label}: {result}"


@pytest.mark.parametrize(
    ("axis", "value"),
    [
        ("network_mode", "bridge"),
        ("command", ["uvicorn", "app:app", "--host", "0.0.0.0"]),
        ("environment", {"API_AUTH_ENABLED": "false"}),
    ],
)
def test_each_configured_axis_alone_puts_the_api_under_contract(
    axis: str, value: object, tmp_path: Path
) -> None:
    """"구성됨" 신호는 **세 축 각각**으로 성립한다.

    변이 실측: 신호를 `environment` 하나로 좁혀도 검사가 전부 초록이었다 — 내 착취
    형상이 environment를 갖고 있어서다. 축마다 **단독으로** 세워 그 구멍을 막는다.

    `network_mode` 단독이 특히 중요하다. environment가 없어 proxy 권위는 못 얻지만,
    `host`로 바꾸면 호스트 네트워크에 붙는다 — 그것을 "stub이라 검사 안 함"으로
    넘기면 안 된다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    assert "kor-travel-concierge-ui" not in services
    # stub에 **한 축만** 값을 준다.
    services["kor-travel-concierge-api"] = {
        "image": "kor-travel-concierge-api:latest",
        axis: value,
    }

    with pytest.raises(ComposeCandidateContractError) as rejection:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    assert "Concierge" in str(rejection.value), (
        f"{axis} 축만 구성된 API가 검사를 빠져나갔다 — {rejection.value}"
    )


# ── GM-17 B · S3-c: PinVi DSN의 전역 env 불변식을 떼어낸다 ───────────────
#
# `_validate_pinvi_database_url_identities`의 앞 절반은 **서비스와 무관한 env
# 불변식**이었다 — `PINVI_DB_PORT == 12800` 고정, role 이름 5자 상호 상이성과 정규식,
# root/app/migrator password 3자 상호 비동일. 뒤 절반만 `services`를 보고, 그쪽은 이미
# 존재-조건부였다(`.get()` + `continue`).
#
# 둘이 한 함수에 있어서 S4가 이 호출을 family scope로 게이팅하면 앞 절반까지 함께
# 꺼진다 — S3-a가 loopback 결박에 대해 고친 것과 같은 모양이다. 비대칭이 결정적이었다:
# Map 쌍둥이 `_validate_map_database_dsn_identities`는 이미 `environment`만 받는다.
#
# **제자리에서 쪼갰다.** 감사가 396형상으로 실측했다 — 제자리 분할은 메시지 변경 0칸,
# Map DSN 자리로 올리면 46칸이 바뀌고 그중 일부는 S1의 성과를 되돌린다.


def _s4_without_pinvi_services(
    monkeypatch: pytest.MonkeyPatch, document: dict[str, object]
) -> dict[str, object]:
    """S4가 PinVi family를 scope에서 뺀 상태 — 문서에서도 서비스를 지운다.

    S3-a에서 배운 것: validator만 no-op으로 만들고 문서에 서비스를 남기면 게이트
    조건이 계속 참이라 시뮬레이션이 가짜가 된다.
    """

    for name in ("_CANDIDATE_REQUIRED_PROTECTED_SERVICES", "_CANDIDATE_KNOWN_SERVICE_NAMES"):
        monkeypatch.setattr(
            c6c_deployment_module,
            name,
            frozenset(
                value
                for value in getattr(c6c_deployment_module, name)
                if not value.startswith("pinvi-")
            ),
        )
    # per-service 절반을 끈다 — 그것이 S4가 게이팅할 수 있는 쪽이다.
    monkeypatch.setattr(
        c6c_deployment_module,
        "_validate_pinvi_database_url_service_identities",
        lambda services, identity, *, resolved: None,
    )
    return _shape_without(
        document,
        (
            "pinvi-postgres",
            "pinvi-api",
            "pinvi-db-init",
            "pinvi-db-runtime-role",
            "pinvi-admin-bootstrap",
            # ADR-46 — pinvi-api/pinvi-admin-bootstrap의 depends_on을 통해서만 이
            # 최소 fragment에 들어온다. 그 둘을 지우면 이 두 서비스도 pinvi
            # family와 함께 지워야 "PinVi가 scope 밖" 시뮬레이션이 유지된다.
            "kor-travel-shared-postgres",
            "kor-travel-shared-db-init-pinvi",
        ),
    )


@pytest.mark.parametrize(
    ("label", "mutation", "expected_error"),
    [
        (
            "전용 instance 포트 핀",
            {"PINVI_DB_PORT": "12900"},
            "PinVi database URL identity is invalid",
        ),
        (
            "Map 대역 탈취(전용 instance)",
            {"PINVI_DB_PORT": "12700"},
            "PinVi database URL identity is invalid",
        ),
        (
            "공용 instance 포트 핀",
            {"KOR_TRAVEL_SHARED_DB_PORT": "12800"},
            "PinVi database URL identity is invalid",
        ),
        (
            "role 이름 충돌(owner 쌍)",
            {
                "PINVI_MIGRATION_OWNER": "pinvi_app_owner",
                "PINVI_APP_SCHEMA_OWNER": "pinvi_app_owner",
            },
            "PinVi database URL identity is invalid",
        ),
        (
            "role 이름 정규식",
            {"PINVI_APP_SCHEMA_OWNER": "Bad-Owner"},
            "PinVi database URL identity is invalid",
        ),
        (
            "password 3자 비동일(root=app)",
            {"PINVI_APP_DB_PASSWORD": "__ROOT__"},
            "PinVi database URL identity is invalid",
        ),
    ],
)
def test_pinvi_database_env_invariants_survive_without_the_services(
    label: str,
    mutation: dict[str, str],
    expected_error: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**S3-c의 핵심.** PinVi 서비스가 scope 밖이어도 env 불변식은 남는다.

    ADR-46 이후 포트 핀은 둘이다 — `PINVI_DB_PORT == 12800`(전용 instance 자신의
    listen 포트)과 `KOR_TRAVEL_SHARED_DB_PORT == 11000`(앱/Dagster DSN이 실제로
    접속하는 공용 instance 포트). 둘 다 `_validate_pinvi_database_url_environment`
    **한 함수**가 계속 쥔다 — `_validate_pinvi_postgres_identity`/
    `_validate_pinvi_db_init_presence`는 `PINVI_DB_PORT`를 환경에서 그대로 파생해
    resolved 후보와 자기-일관성만 볼 뿐, `.env` 자체의 드리프트는 못 잡는다. 감사가
    구체적 피해를 지목했다 — 전용 instance 포트가 `12700`이면 두 PostgreSQL이 모두
    `network_mode: host`로 127.0.0.1:12700을 잡는 후보가 통과하고(**Map 전용 대역
    탈취**), 저장소에 host 포트 충돌 검사는 없다.

    role 이름의 owner 쌍(`PINVI_APP_SCHEMA_OWNER`/`PINVI_MIGRATION_OWNER`)도 중요하다 —
    그 둘은 **어느 DSN에도 나타나지 않으므로** per-service DSN 비교가 구조적으로 볼 수
    없다. 전역 절반이 유일한 자리다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = _s4_without_pinvi_services(monkeypatch, deepcopy(candidate))

    mutated_environment = dict(environment)
    for name, value in mutation.items():
        mutated_environment[name] = (
            mutated_environment["PINVI_POSTGRES_PASSWORD"] if value == "__ROOT__" else value
        )

    with pytest.raises(ComposeCandidateContractError, match=expected_error):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=mutated_environment,
        )


def test_the_env_half_does_not_look_at_services_at_all() -> None:
    """전역 절반은 `services`를 **인자로도 받지 않는다** — 구조로 결박한다.

    시그니처에 `services`가 없으면 family 조건을 달 재료 자체가 없다. Map 쌍둥이
    `_validate_map_database_dsn_identities`가 이미 그 모양이고, PinVi만 달랐다.

    **이 검사가 못 보는 것을 적어 둔다**(적대 리뷰 2026-09-18 F12): 호출부를 family
    scope로 감싸는 S4 변이는 함수 본문을 건드리지 않으므로 여기서는 초록이다. 실제
    방어는 효과에 결박한 옆의
    `test_pinvi_database_env_invariants_survive_without_the_services`가 한다. 이
    검사는 "게이팅할 재료가 없다"만 센다 — 그 이상을 주장하지 마라.
    """

    import inspect

    parameters = inspect.signature(
        c6c_deployment_module._validate_pinvi_database_url_environment
    ).parameters
    assert list(parameters) == ["environment"], (
        f"전역 절반이 services를 받으면 게이팅할 재료가 생긴다: {list(parameters)}"
    )

    source = inspect.getsource(
        c6c_deployment_module._validate_pinvi_database_url_environment
    )
    # docstring은 설명을 위해 `services`를 언급한다 — 본문만 본다.
    body = source.split('"""')[-1]
    assert "services" not in body, "전역 절반 본문이 services를 참조한다"


def test_the_port_pin_is_still_enforced_with_every_service_present(
    tmp_path: Path,
) -> None:
    """게이팅 없이도 포트 핀이 돈다 — 분리가 기존 강제를 잃지 않았다는 증거.

    ADR-46 이후에도 `PINVI_DB_PORT == 12800`은 `_validate_pinvi_database_url_environment`
    (전역 절반)가 계속 쥔다 — 공용 instance 포트(`KOR_TRAVEL_SHARED_DB_PORT`)가
    새로 생겼을 뿐, 전용 instance 포트 핀은 그대로다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    mutated_environment = {**environment, "PINVI_DB_PORT": "12900"}
    with pytest.raises(
        ComposeCandidateContractError, match="PinVi database URL identity is invalid"
    ):
        validate_compose_candidate_protected_values(
            candidate,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=mutated_environment,
        )


def test_the_service_half_stays_gateable_and_still_runs_today(tmp_path: Path) -> None:
    """per-service 절반은 게이팅 대상이지만 **오늘은 실제로 돈다**.

    "조건부로 만들 수 있다"가 "이미 껐다"로 미끄러지지 않게 한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    api = services["pinvi-api"]
    assert isinstance(api, dict)
    api_environment = api["environment"]
    assert isinstance(api_environment, dict)
    api_environment["PINVI_DATABASE_URL"] = "postgresql://wrong:wrong@127.0.0.1:12800/pinvi"

    with pytest.raises(
        ComposeCandidateContractError, match="PinVi database URL identity is invalid"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


# ── 전역 env 술어가 **정말** 전역인가 ─────────────────────────────────────
#
# 적대 리뷰 2026-09-18이 세 가지를 실측했다.
#
# **F1** 저장소 compose에 `POSTGRES_INITDB_ARGS` 리터럴이 네 곳(geo·concierge·pinvi·
# map)인데 2026-09-17 수정은 계약표로 막았고 **계약표는 서비스를 열거한다.**
# `kor-travel-geo-postgres`·`kor-travel-concierge-postgres`는 계약표에도 validator
# 에도 UI 잠금에도 없었고, `--auth-host=trust`가 raw·resolved·UI 저장 **세 진입점
# 전부**를 통과했다. fresh PGDATA에서 initdb가 `trust`를 pg_hba **첫 행**으로 쓰므로
# 12500/12600에 비밀번호 없는 superuser가 생긴다.
#
# **F4** `POSTGRES_HOST_AUTH_METHOD` 금지가 raw 층에서 전역이 아니었다 —
# `environment`가 리스트면 통째로 건너뛰는데 `["NAME=value"]`는 합법 문법이다.
# 오늘 최종 거부되던 이유는 `docker compose config`가 맵으로 정규화해 주기
# 때문뿐이라 검사 이름 `..._rejected_anywhere`는 과장이었다.
#
# **F2** 그리고 그 금지를 `if MAP_PG in services:`로 감싸는 **순진한 S4 변이가 전체
# 스위트를 그대로 통과**했다 — 종전 검사가 키를 놓는 서비스만 바꾸고 문서에서
# map-postgres를 **지우지 않았기** 때문이다. 아래 S4 시뮬레이션이 그 자리를 메운다.


def _service_with_environment(environment: object) -> dict[str, object]:
    # **정본 command를 함께 준다.** 식별이 "postgres 서버를 돌리는가"로 바뀌면서
    # `command` 없는 postgres 이미지도 서버로 판정된다 — fragment를 완전하게 하는 것이
    # S1에서 정한 처방이고, 그러면 아래 검사들이 겨냥한 결함 하나만 남는다.
    return {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=127.0.0.1"],
        "environment": environment,
    }


def _bootstrap_resolved(environment: dict[str, str]) -> dict[str, Any]:
    """`_bootstrap_candidate`와 **같은 서비스 집합**의 resolved 문서.

    좌표는 전부 `environment`에서 되읽는다 — 목록을 복제하면 그 사본이 곧 원본과
    갈라진다(이 파일이 `_bootstrap_candidate`를 뽑아낸 이유와 같다).
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


@pytest.mark.parametrize(
    "service_name",
    ["kor-travel-geo-postgres", "kor-travel-concierge-postgres", "rustfs"],
)
def test_non_canonical_initdb_args_are_rejected_on_any_service(
    tmp_path: Path, service_name: str
) -> None:
    """**계약표 밖의 서비스도** 정본 값에 묶인다.

    셋 중 앞의 둘은 정본 compose의 실재하는 PostgreSQL이고 계약표에 없었다.
    세 번째는 postgres조차 아닌 서비스다 — 술어가 **이름을 보지 않는다**는 것을
    센다. 이름에 결박하면 다섯째 postgres에서 같은 실수를 반복한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services[service_name] = _service_with_environment(
        {"POSTGRES_INITDB_ARGS": "--auth-host=trust"}
    )

    with pytest.raises(
        ComposeCandidateContractError, match="non-canonical POSTGRES_INITDB_ARGS"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_non_canonical_initdb_args_are_rejected_on_the_resolved_entry_point(
    tmp_path: Path,
) -> None:
    """**resolved 진입점도 센다.**

    적대 리뷰 실측: 종전 전역 금지의 resolved 호출은 **커버리지 0**이었다(호출을
    지워도 전부 초록). resolved가 실제 배포에 적용되는 쪽이다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    services = resolved["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _service_with_environment(
        {"POSTGRES_INITDB_ARGS": "--auth-host=trust"}
    )

    with pytest.raises(
        ComposeCandidateContractError, match="non-canonical POSTGRES_INITDB_ARGS"
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


def test_the_forbidden_auth_key_is_rejected_on_the_resolved_entry_point(
    tmp_path: Path,
) -> None:
    """같은 공백이 `POSTGRES_HOST_AUTH_METHOD` 쪽에도 있었다 — resolved 호출 무커버리지."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    services = resolved["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _service_with_environment(
        {"POSTGRES_HOST_AUTH_METHOD": "trust"}
    )

    with pytest.raises(
        ComposeCandidateContractError, match="overrides PostgreSQL host authentication"
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("POSTGRES_HOST_AUTH_METHOD=trust", "overrides PostgreSQL host authentication"),
        ("POSTGRES_HOST_AUTH_METHOD", "overrides PostgreSQL host authentication"),
        ("POSTGRES_INITDB_ARGS=--auth-host=trust", "non-canonical POSTGRES_INITDB_ARGS"),
        ("POSTGRES_INITDB_ARGS", "non-canonical POSTGRES_INITDB_ARGS"),
    ],
)
def test_list_form_environment_does_not_escape_the_global_predicates(
    tmp_path: Path, entry: str, message: str
) -> None:
    """**리스트 문법도 본다.**

    Compose는 `environment: ["NAME=value"]`를 완전히 합법으로 받는다. 종전 검사는
    `isinstance(environment, Mapping)`이 아니면 `continue`해서 그 문법을 통째로
    건너뛰었다 — 오늘 최종적으로 막힌 이유는 `docker compose config`가 맵으로
    정규화해 주기 때문뿐이라, raw 층에서는 방어가 없었다.

    값이 없는 항목(`"NAME"`)도 센다 — 셸에서 값을 물려받는 형태라 **문서만 보고는
    무엇이 들어올지 알 수 없고**, 그래서 정본이 아니다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _service_with_environment([entry])

    with pytest.raises(ComposeCandidateContractError, match=message):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_forbidden_auth_key_is_banned_by_name_not_by_value(tmp_path: Path) -> None:
    """**값이 아니라 존재를 막는다**는 docstring을 검사로 남긴다.

    적대 리뷰 실측: 술어를 `value.lower() == "trust"`로 바꿔도 전부 초록이었다
    (검사가 `trust`만 써 왔다). 여기서는 **무해해 보이는 값**을 준다 — 그래도
    거부돼야 한다. 계약 기계가 값-동등 비교라 "키가 추가됐다"를 표현하지 못하는
    것이 이 금지의 존재 이유이고, 값으로 판정하면 그 이유가 사라진다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _service_with_environment(
        {"POSTGRES_HOST_AUTH_METHOD": "scram-sha-256"}
    )

    with pytest.raises(
        ComposeCandidateContractError, match="overrides PostgreSQL host authentication"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    ("flaw", "message"),
    [
        (
            {"POSTGRES_HOST_AUTH_METHOD": "trust"},
            "overrides PostgreSQL host authentication",
        ),
        (
            {"POSTGRES_INITDB_ARGS": "--auth-host=trust"},
            "non-canonical POSTGRES_INITDB_ARGS",
        ),
    ],
)
def test_the_global_predicates_survive_a_document_without_any_known_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flaw: dict[str, str],
    message: str,
) -> None:
    """**순진한 S4 시뮬레이션.** required 집합을 줄이고 문서에서 두 postgres를 지운다.

    이것이 이 파일의 다른 S4 검사들과 같은 모양이다(`_s4_without_pinvi_services`).
    적대 리뷰 실측: 종전 검사는 키를 **어느 서비스에 놓느냐**만 바꾸고 문서에서
    map-postgres를 지우지 않아서, 전역 금지를 `if MAP_PG in services:`로 감싸는
    변이가 **1770건을 그대로 통과**했다.

    S4가 family scope를 도입하면 정확히 이 형상이 실제로 생긴다 — 그때 두 술어가
    함께 꺼지면 어느 PostgreSQL도 인증 계약을 받지 못한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    for known in list(services):
        if known.endswith("postgres") or known.endswith("postgresql"):
            del services[known]
    assert not any("postgres" in name for name in services), sorted(services)
    services["some-future-database"] = _service_with_environment(flaw)

    survivors = frozenset(
        name
        for name in c6c_deployment_module._CANDIDATE_REQUIRED_PROTECTED_SERVICES
        if name in services
    )
    monkeypatch.setattr(
        c6c_deployment_module, "_CANDIDATE_REQUIRED_PROTECTED_SERVICES", survivors
    )

    with pytest.raises(ComposeCandidateContractError, match=message):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_initdb_rule_has_exactly_one_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """값 고정의 주인은 전역 술어 **하나**다.

    2026-09-17에는 PinVi의 `_validate_pinvi_postgres_identity`가 같은 규칙을 자기
    exact-match dict에 들고 있었다. 두 자리에 같은 규칙이 있으면 한쪽을 지워도 아무
    검사가 빨개지지 않는다 — 이 저장소가 S2에서 실제로 겪은 일이다.

    **이름이 아니라 효과로 센다.** 전역 술어를 no-op으로 만든 뒤 PinVi postgres의
    initdb를 망가뜨린다. 두 번째 자리가 있으면 그래도 거부되고, 이 검사가 빨개진다.

    **이 검사는 심층 방어를 금지한다.** 언젠가 의도적으로 두 번째 자리를 두기로
    한다면 여기를 "둘 다 센다"로 고쳐야 한다 — 조용히 늘어나는 것만 막는 것이 목적이다
    (적대 리뷰 2026-09-18이 이 성질을 명시해 달라고 지적했다).
    소스에서 문자열을 세는 방식은 설명 주석 한 줄에 빨개지므로 결박이 아니라 잡음이다
    (`test_the_loopback_binding_has_exactly_one_home`이 같은 이유로 AST를 쓴다).

    Map의 계약표 항목은 **다른 일을 한다**(UI 저장 경로의 잠금 파생) — 그쪽은
    `test_the_two_postgres_services_share_one_initdb_contract`가 센다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["pinvi-postgres"]
    assert isinstance(postgres, dict)
    postgres_environment = postgres["environment"]
    assert isinstance(postgres_environment, dict)
    postgres_environment["POSTGRES_INITDB_ARGS"] = "--auth-host=trust"

    with pytest.raises(
        ComposeCandidateContractError, match="non-canonical POSTGRES_INITDB_ARGS"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    monkeypatch.setattr(
        c6c_deployment_module,
        "_assert_canonical_postgres_initdb_args",
        lambda document: None,
    )
    validate_compose_candidate_protected_values(
        shaped,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )


def test_the_ui_save_path_locks_initdb_args_for_every_service() -> None:
    """세 번째 진입점 — **UI 저장**도 서비스 이름을 보지 않는다.

    종전 규칙은 `service_name == "pinvi-postgres"`였고, 그래서 geo·concierge의
    PostgreSQL은 이 화면에서 인증을 자유롭게 끌 수 있었다. 적대 리뷰 2026-09-18이
    raw·resolved와 함께 이 경로도 통과하는 것을 실측했다.
    """

    for service_name in (
        "kor-travel-geo-postgres",
        "kor-travel-concierge-postgres",
        "kor-travel-map-postgres",
        "pinvi-postgres",
    ):
        with pytest.raises(
            ContainerConfigValidationError, match="initdb authentication policy"
        ):
            validate_container_config_update(
                ports=[],
                env={"POSTGRES_INITDB_ARGS": "--auth-host=trust"},
                networks=[],
                baseline_env={"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
                service_name=service_name,
            )


def test_the_ui_save_path_refuses_to_add_the_forbidden_auth_key() -> None:
    """**키 추가**는 잠금이 표현하지 못한다 — 저장 시점에 따로 막는다.

    계약 잠금은 값 동등 비교라 "없던 키가 생겼다"를 볼 수 없다. 최종적으로는 후보
    검증이 쓰기 전에 거부하므로 fail-close지만, 그때는 실패가 조작에서 멀어져 원인이
    화면 조작이었다는 사실이 드러나지 않는다(적대 리뷰 F6).
    """

    with pytest.raises(ContainerConfigValidationError, match="cannot be added"):
        validate_container_config_update(
            ports=[],
            env={"POSTGRES_HOST_AUTH_METHOD": "trust"},
            networks=[],
            baseline_env={},
            service_name="kor-travel-geo-postgres",
        )



# ── Concierge 게이트: 신호 집합이 좁으면 우회로가 된다 ───────────────────


def _canonical_concierge_api() -> dict[str, object]:
    """정본 그대로의 raw concierge-api. **축을 하나씩만** 깨려면 나머지가 정본이어야 한다."""

    return {
        "image": "kor-travel-concierge-api:latest",
        "network_mode": c6c_deployment_module._CONCIERGE_CANONICAL_RAW_NETWORK_MODE,
        "command": list(c6c_deployment_module._CONCIERGE_API_CANONICAL_RAW_COMMAND),
        "environment": dict(
            c6c_deployment_module._CONCIERGE_API_CANONICAL_RAW_ENV_VALUES
        ),
    }


@pytest.mark.parametrize(
    ("signal", "value"),
    [
        ("entrypoint", ["python", "-m", "ktc.cli", "api", "--host", "0.0.0.0"]),
        ("ports", ["12601:12601"]),
        ("env_file", [{"path": "/srv/concierge/.env", "required": False}]),
        ("build", {"context": "/srv/concierge"}),
    ],
)
def test_a_deployment_signal_outside_the_first_three_still_triggers_the_contract(
    tmp_path: Path, signal: str, value: object
) -> None:
    """**신호 집합이 좁으면 그것이 곧 우회로다.**

    첫 판의 신호는 `environment`·`command`·`network_mode` 셋뿐이었다. 적대 리뷰
    2026-09-18이 그 셋을 한 글자도 건드리지 않고 살아 있는 API를 세웠다 —
    `entrypoint`로 `--host 0.0.0.0`을 주고, `ports`로 **전 인터페이스**에 게시하고,
    `env_file`로 concierge 저장소 `.env`(정본 compose가 이미 쓰는 통로이고
    `KTC_ADMIN_PROXY_SECRET`가 거기 있다)를 읽는 형상이다.

    여기서는 신호 **하나만** 준다. 나머지는 stub 그대로다 — 그래도 계약이 걸려야
    한다. 각 항목이 "이 키에 값이 있으면 이 서비스는 실제로 배포된다"를 만족한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    assert "kor-travel-concierge-ui" not in services, "전제: fixture에 UI가 없다"
    services["kor-travel-concierge-api"] = {
        "image": "kor-travel-concierge-api:latest",
        signal: value,
    }

    with pytest.raises(ComposeCandidateContractError) as rejection:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    assert "Concierge" in str(rejection.value), (
        f"'{signal}' 신호로 세운 API가 계약을 빠져나갔다 — {rejection.value}"
    )


def test_a_null_ui_service_is_invalid_not_absent(tmp_path: Path) -> None:
    """`ui: null`은 **부재가 아니다.**

    게이트를 고치면서 `not in services`를 `services.get(...)`으로 바꾼 탓에 "키가
    있고 값이 null"이 부재와 구분되지 않았다(적대 리뷰 F10). 이 저장소가 S1에서
    명시적으로 박은 규칙의 위반이다 — null을 부재로 오인하면 계약을 한 줄로 우회할
    수 있다. 오늘은 진입점의 non-Mapping 스캔이 더 앞에서 막아 주지만, 이 함수가
    **단독으로도** 안전해야 한다.
    """

    with pytest.raises(ComposeCandidateContractError):
        c6c_deployment_module._validate_concierge_ui_canonical_contract(
            {"kor-travel-concierge-ui": None, "kor-travel-concierge-api": {"image": "x"}},
            {},
            resolved=False,
        )


def test_the_api_auth_axis_is_counted_on_its_own(tmp_path: Path) -> None:
    """**축을 하나만 깨서** 그 축이 실제로 일하는지 센다.

    종전 착취 형상은 `network_mode: bridge` + 비정본 `command`를 함께 갖고 있어
    **더 앞의 검사에서** 거부됐고, 단언이 `"Concierge" in str(...)`라 어느 축이
    잡았는지 세지 않았다. 그래서 API env 계약을 UI 존재로 다시 게이팅하는 변이가
    전체 스위트를 통과했다(적대 리뷰 F9). 여기서는 나머지를 정본으로 두고
    `API_AUTH_ENABLED`만 리터럴 `false`로 바꾼다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    api = _canonical_concierge_api()
    api_environment = api["environment"]
    assert isinstance(api_environment, dict)
    api_environment["API_AUTH_ENABLED"] = "false"
    services["kor-travel-concierge-api"] = api

    with pytest.raises(
        ComposeCandidateContractError, match="API_AUTH_ENABLED canonical wiring"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_proxy_authority_axis_is_counted_on_its_own(tmp_path: Path) -> None:
    """같은 이유로 proxy secret 축도 단독으로 센다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    api = _canonical_concierge_api()
    api_environment = api["environment"]
    assert isinstance(api_environment, dict)
    api_environment["KTC_ADMIN_PROXY_SECRET"] = "attacker-chosen-secret"
    services["kor-travel-concierge-api"] = api

    with pytest.raises(
        ComposeCandidateContractError, match="canonical Manager proxy authority"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_manager_root_environment_axis_is_counted_on_its_own(
    tmp_path: Path,
) -> None:
    """Manager root env 불변식도 UI 없이 단독으로 걸린다.

    이 축이 PBKDF2 형식·반복수·32자 secret·API_KEYS 소속을 본다. UI 게이트 뒤에
    있던 탓에 UI 한 줄 삭제로 전부 꺼지던 것이 2026-09-18 수정의 절반이었고,
    적대 리뷰는 그 절반이 **검사로 결박되지 않았다**는 것을 실측했다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-concierge-api"] = _canonical_concierge_api()
    broken_environment = {
        **environment,
        c6c_deployment_module._CONCIERGE_ROOT_PROXY_SECRET_ENV: "too-short",
    }

    with pytest.raises(
        ComposeCandidateContractError, match="Manager root environment is invalid"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=broken_environment,
        )


def test_the_canonical_loopback_api_port_axis_is_counted_on_its_own(
    tmp_path: Path,
) -> None:
    """12601 핀도 UI 없이 단독으로 걸린다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-concierge-api"] = _canonical_concierge_api()
    moved_environment = {**environment, "KOR_TRAVEL_CONCIERGE_API_PORT": "12699"}

    with pytest.raises(
        ComposeCandidateContractError, match="canonical loopback API port"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=moved_environment,
        )


def test_the_expected_database_is_threaded_not_hardcoded(tmp_path: Path) -> None:
    """`_PinviDatabaseIdentity.expected_database`가 env에서 온다.

    S3-c의 전제는 "dataclass로 넘기는 값이 종전 지역변수와 정확히 같다"였는데, 그
    전제를 지키는 검사가 없었다 — 적대 리뷰 2026-09-18 F11 실측: 그 필드를 `'pinvi'`
    상수로 굳혀도 1770건이 전부 초록이다.

    **resolved 문서라야 보인다.** raw에서는 DSN이 `${PINVI_POSTGRES_DB:-pinvi}`라
    양쪽이 함께 움직여 불일치가 생기지 않는다. resolved는 `/pinvi`가 리터럴로 박혀
    있으므로, env만 바꾸면 값이 실제로 흘러가는지가 드러난다.
    """

    _candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    renamed_environment = {**environment, "PINVI_POSTGRES_DB": "pinvi_renamed"}

    with pytest.raises(
        ComposeCandidateContractError, match="PinVi database URL identity is invalid"
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=renamed_environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


# ── 보안 라운드 2: 값을 막았더니 **삭제**가 열렸다 ───────────────────────
#
# 적대 리뷰가 실제 postgres 컨테이너로 실측했다:
#
#     POSTGRES_INITDB_ARGS 없음   host all all 127.0.0.1/32  trust
#     POSTGRES_INITDB_ARGS=trust  host all all 127.0.0.1/32  trust
#
# **부재는 `trust`와 한 글자도 다르지 않다.** 이 저장소의 `docker-compose.yml` 주석이
# 이미 그렇게 적어 뒀는데도 첫 수정은 "값이 정본인가"만 물었다.
#
# 더 나쁜 것은 그 사이 **한 칸을 약화시켰다**는 것이다. PinVi 신원 검사의 exact-match
# dict는 `.get()` 비교라 **부재도 거부**했는데, "자리를 하나로" 한다며 그 항목을 뺄 때
# 그 성질이 함께 사라졌다. 960형상 대조에서 약화된 14칸이 전부 이 형상이다. 중복
# 제거 자체는 옳았지만 **남긴 쪽이 원래보다 약했다.**


def _cluster_service(**extra: object) -> dict[str, object]:
    """클러스터를 **초기화하는** 서비스. 판정의 정확한 재료만 담는다."""

    base: dict[str, object] = {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=127.0.0.1"],
        "environment": {
            "POSTGRES_PASSWORD": "x",
            "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
        },
    }
    base.update(extra)
    return base


@pytest.mark.parametrize(
    "service_name",
    ["kor-travel-geo-postgres", "kor-travel-concierge-postgres", "some-future-database"],
)
def test_omitting_initdb_args_is_rejected(tmp_path: Path, service_name: str) -> None:
    """**부재가 곧 `trust`다.** 값을 막고 삭제를 열어 두면 더 짧은 payload가 생길 뿐이다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services[service_name] = {
        "image": "postgres:16",
        "environment": {"POSTGRES_PASSWORD": "x"},
    }

    with pytest.raises(ComposeCandidateContractError, match="absence selects trust"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_omitting_initdb_args_is_rejected_on_the_resolved_entry_point(
    tmp_path: Path,
) -> None:
    """resolved가 실제 배포에 적용되는 쪽이다."""

    _candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    services = resolved["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = {
        "image": "postgres:16",
        "environment": {"POSTGRES_PASSWORD": "x"},
    }

    with pytest.raises(ComposeCandidateContractError, match="absence selects trust"):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


def test_deleting_initdb_args_is_rejected_at_the_ui_save_path() -> None:
    """세 번째 진입점에서도 삭제를 막는다."""

    with pytest.raises(ContainerConfigValidationError, match="cannot be removed"):
        validate_container_config_update(
            ports=[],
            env={},
            networks=[],
            baseline_env={"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
            service_name="kor-travel-geo-postgres",
        )


def test_the_canonical_value_survives_incidental_whitespace() -> None:
    """정본 값이 앞뒤 공백 때문에 거부되면 안 된다.

    바로 위 루프가 `str(value)`로 정규화하는데 새 규칙만 원시 비교를 했다 — 이 파일이
    "터미널·`.env` 복붙 공백"을 일부러 trim하는 것과 어긋났다(적대 리뷰 C-F8).
    """

    validate_container_config_update(
        ports=[],
        env={"POSTGRES_INITDB_ARGS": "  --auth-host=scram-sha-256 "},
        networks=[],
        baseline_env={"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
        service_name="kor-travel-geo-postgres",
    )


def test_the_forbidden_key_may_stay_if_it_was_already_there() -> None:
    """추가만 막는다 — **의미론을 센다**.

    `forbidden not in baseline_env` 조건을 떼는 변이가 살아남았다(적대 리뷰 C-M11).
    그 조건이 없으면 "이미 있던 값을 그대로 다시 저장"이 거부돼, 정당한 편집이 막힌다.
    """

    validate_container_config_update(
        ports=[],
        env={"POSTGRES_HOST_AUTH_METHOD": "trust"},
        networks=[],
        baseline_env={"POSTGRES_HOST_AUTH_METHOD": "trust"},
        service_name="kor-travel-geo-postgres",
    )


def test_relocating_the_data_directory_is_rejected(tmp_path: Path) -> None:
    """`PGDATA` 재지정은 "fresh PGDATA에서만"이라는 전제를 **공격자가 만들 수 있게** 한다.

    적대 리뷰 실측: map·pinvi 양쪽에서 통과했다. 새 경로를 주면 initdb가 다시 돌므로
    이미 초기화된 클러스터에서도 부재/`trust`의 조건이 성립한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _cluster_service()
    environment_map = services["kor-travel-geo-postgres"]["environment"]
    assert isinstance(environment_map, dict)
    environment_map["PGDATA"] = "/var/lib/postgresql/data/fresh"

    with pytest.raises(ComposeCandidateContractError, match="relocates PostgreSQL data"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_env_file_on_a_cluster_service_is_rejected(tmp_path: Path) -> None:
    """`env_file`은 이 문서를 읽어서는 알 수 없는 값을 주입한다 — 볼 재료가 사라진다.

    종전에는 이 금지가 **열거된 서비스에만** 걸려서 geo/concierge가 통째로 빠져나갔다.
    resolved는 막지만 그것은 `docker compose config`가 인라인해 주기 때문일 뿐이라,
    같은 결함이 다른 문으로 돌아온 것이었다(적대 리뷰 C-F3).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _cluster_service(env_file=["./pg.env"])

    with pytest.raises(
        ComposeCandidateContractError, match="forbids env_file on a PostgreSQL service"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_a_one_shot_on_the_postgres_image_is_not_a_cluster(tmp_path: Path) -> None:
    """**식별을 넓히면 정당한 서비스가 죽고, 좁히면 서버가 새어나간다.**

    `pinvi-db-init`은 같은 postgis 이미지를 쓰지만 psql을 돌리는 one-shot이라 서버가
    아니다. 첫 판은 이미지로 식별해서 계약 fragment 13건을 빨갛게 만들었고, 둘째 판은
    `command[0]` 리터럴 비교여서 절대경로·문자열 command가 판정을 피했다.

    지금 재료는 **command 토큰의 basename**(+ 초기화 env 보조축)이고, 이미지 문자열은
    아예 쓰지 않는다. 그리고 이 흔적 축은 `config/docker-targets.yml`의 `role`에서 오는
    **declared 축과 논리합**이다 — `test_an_undeclared_postgres_server_is_refused`가
    그 둘의 관계를 센다.
    """

    witnesses = c6c_deployment_module._service_witnesses_a_postgres_server
    # 자기 entrypoint로 one-shot을 돌리는 정본 형상 둘 — 서버 흔적이 없다.
    assert not witnesses(
        {
            "image": "postgis/postgis@sha256:deadbeef",
            "entrypoint": ["/bin/sh", "/usr/local/bin/postgres-role-bootstrap"],
        },
        {"POSTGRES_USER": "x", "POSTGRES_DB": "y"},
    )
    assert not witnesses(
        {"image": "postgis/postgis@sha256:deadbeef", "command": ["sh", "-ec", "psql"]},
        {"PGUSER": "x"},
    )
    # **basename으로 본다.** 첫 판은 `command[0] == "postgres"` 리터럴 비교여서
    # 절대경로·문자열 command·`sh -c 'exec postgres …'`가 판정을 피했다(적대 리뷰
    # 2026-09-18 F1 실측).
    assert witnesses({"command": ["postgres", "-c", "x=1"]}, {})
    assert witnesses({"command": ["/usr/local/bin/postgres", "-i"]}, {})
    assert witnesses({"command": "sh -c 'exec postgres -i'"}, {})
    # 보조 축 — command가 침묵해도 초기화 env가 있으면 흔적이다.
    assert witnesses({"image": "scratch"}, {"POSTGRES_PASSWORD_FILE": "/run/secrets/x"})
    # **이미지 문자열은 재료가 아니다.** digest 핀·리네임·플레이스홀더에서 깨지고,
    # 리뷰어가 map-postgres의 resolved 층에서 마커가 꺼지는 것까지 실측했다(F6).
    assert not witnesses({"image": "postgres:16"}, {})


def test_the_rejection_names_the_service_it_rejected(tmp_path: Path) -> None:
    """운영자가 어느 서비스가 거부됐는지 알아야 한다.

    `_CANDIDATE_KNOWN_SERVICE_NAMES` 밖의 이름은 sha8로 가려지는데, 적대 리뷰 실측상
    **이 수정의 대상 서비스 둘 다**가 가려졌다. 새 검사가 문구의 앞부분만 보고 있어서
    그 결함을 영영 못 봤다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-concierge-postgres"] = _service_with_environment(
        {"POSTGRES_INITDB_ARGS": "--auth-host=trust"}
    )

    with pytest.raises(ComposeCandidateContractError) as rejection:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    assert "kor-travel-concierge-postgres" in str(rejection.value)


def test_a_null_mapping_value_is_not_a_canonical_value(tmp_path: Path) -> None:
    """매핑형 `{"NAME": None}`도 리스트형 bare 이름과 같은 것이다.

    `docker compose config`가 `environment: [NAME]`을 그 형태로 정규화한다 — 즉
    resolved 층에서 `None`은 실제로 발생한다. 리스트형만 보던 검사가 그것을 놓쳤고,
    매핑형 `None`을 버리는 변이가 살아남았다(적대 리뷰 C-M20).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _service_with_environment(
        {"POSTGRES_INITDB_ARGS": None}
    )

    with pytest.raises(
        ComposeCandidateContractError, match="non-canonical POSTGRES_INITDB_ARGS"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize("signal", ["ports", "env_file", "build"])
def test_an_empty_signal_is_not_a_deployment(signal: str) -> None:
    """빈 리스트·빈 dict는 "배포한다"는 신호가 아니다.

    `command`/`entrypoint`는 compose가 stub에도 `null`을 붙이므로 `is not None`이
    옳지만, 나머지 셋은 그 이유가 없다. 오늘 깨지지 않는 이유는 compose가 그 키를
    stub에 붙이지 않기 때문뿐이었다(적대 리뷰 C-F5).
    """

    assert signal in c6c_deployment_module._CONCIERGE_PRESENT_DEPLOYMENT_SIGNALS
    c6c_deployment_module._validate_concierge_ui_canonical_contract(
        {"kor-travel-concierge-api": {"image": "x", signal: [] if signal != "build" else {}}},
        {},
        resolved=False,
    )


# ── env 축보다 **강한** 축이 무검사였다 (적대 리뷰 C-F4) ─────────────────
#
# 리뷰어가 심각도를 다시 쟀다. `-c hba_file=<경로>`는 pg_hba를 통째로 갈아치우므로
# `--auth-host=trust`와 결과가 같고, **fresh PGDATA를 요구하지 않는다** — 이미
# 초기화된 클러스터에도 즉시 적용된다. 즉 initdb 축을 지키는 것만으로는 부족했다.
#
# 그리고 `listen_addresses` 강제는 저장소에 **PinVi 하나뿐**이었다. 네 PostgreSQL이
# 전부 loopback을 쓰는데 검증은 하나만 봤다(오래된 열린 항목).


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param(
            {"entrypoint": ["sh", "-c", "postgres"]},
            "non-canonical keys",
            id="entrypoint",
        ),
        pytest.param({"privileged": True}, "host privilege", id="privileged"),
        pytest.param({"user": "root"}, "host privilege", id="user"),
        pytest.param({"cap_add": ["SYS_ADMIN"]}, "host privilege", id="cap_add"),
        pytest.param({"pid": "host"}, "host privilege", id="pid"),
        pytest.param({"devices": ["/dev/sda:/dev/sda"]}, "host privilege", id="devices"),
        pytest.param({"ipc": "host"}, "host privilege", id="ipc"),
        pytest.param(
            {"security_opt": ["apparmor:unconfined"]}, "host privilege", id="security_opt"
        ),
        pytest.param({"userns_mode": "host"}, "host privilege", id="userns_mode"),
        pytest.param({"volumes_from": ["other"]}, "host privilege", id="volumes_from"),
    ],
)
def test_a_cluster_service_cannot_take_privileged_shapes(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    """두 기계가 이것을 막는다 — 그리고 **둘의 범위가 다르다.**

    `entrypoint`는 PostgreSQL **허용 목록**이 막는다(그 키가 목록에 없다). 주면
    `command`가 인자로 강등되어 command 규칙이 무의미해지므로, 허용 목록에서 빼는
    것으로 금지가 자동 성립한다 — 기계가 하나 줄었다.

    나머지는 **문서 전역** 특권 금지가 막는다. 정본 34 서비스 실측에서 `devices`·
    `ipc`·`security_opt`·`userns_mode`·`volumes_from`·`cap_add`·`pid` 사용은 0건이고,
    `privileged`·`devices`는 cadvisor, `user`는 prometheus·grafana만 쓴다. 첫 판은
    금지 목록이 PostgreSQL 안쪽에만 있어서 **정본 그대로의 geo 클러스터에
    `devices: /dev/sda`가 양쪽 통과했다**(적대 리뷰 2026-09-18 F3, 호스트 root).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _cluster_service(**mutation)

    with pytest.raises(ComposeCandidateContractError, match=message):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize("setting", ["hba_file", "ident_file", "password_encryption"])
@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param("-c {name}={value}", id="short"),
        pytest.param("--{dashed}={value}", id="long-option"),
        pytest.param("-c {upper}={value}", id="upper-case-guc"),
        pytest.param("-c{name}={value}", id="glued-short"),
        pytest.param("-c{upper}={value}", id="glued-upper"),
    ],
)
def test_a_runtime_setting_cannot_replace_the_authentication_policy(
    tmp_path: Path, setting: str, spelling: str
) -> None:
    """`-c hba_file=...`는 `--auth-host=trust`와 **결과가 같고 더 강하다.**

    fresh PGDATA를 요구하지 않으므로 이미 도는 클러스터에도 즉시 적용된다.

    **철자가 아니라 효과에 결박한다.** 첫 판은 리터럴 소문자 `-c hba_file=`만 봤고,
    postgres가 **동일하게 해석하는** `--hba-file=`·`-c HBA_FILE=`이 전부 통과했다 —
    GUC 이름은 대소문자를 구분하지 않고 long option에서 하이픈은 밑줄과 같다(적대
    리뷰 2026-09-18 F가 실제 서버로 honor까지 확인했다).
    """

    fragment = spelling.format(
        name=setting,
        dashed=setting.replace("_", "-"),
        upper=setting.upper(),
        value="/tmp/evil.conf",
    ).split(" ")
    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = _cluster_service(
        command=["postgres", "-c", "listen_addresses=127.0.0.1", *fragment]
    )

    with pytest.raises(
        ComposeCandidateContractError,
        match=f"PostgreSQL command sets a non-canonical {setting}",
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    ("command", "message"),
    [
        pytest.param(None, "canonical postgres command", id="absent"),
        pytest.param(
            ["postgres", "-c", "listen_addresses=*"],
            "loopback binding",
            id="all-interfaces",
        ),
        pytest.param(
            ["sh", "-c", "postgres"], "canonical postgres command", id="not-postgres"
        ),
    ],
)
def test_a_cluster_service_keeps_the_loopback_binding(
    tmp_path: Path, command: object, message: str
) -> None:
    """**네 PostgreSQL 전부**에 걸린다 — 종전에는 PinVi 하나뿐이었다.

    `command`가 없으면 기본값으로 뜨고 `listen_addresses`는 `*`다. 이 스택은 host
    네트워킹이므로 그것은 전 인터페이스 노출이다 — "지우면 통과"를 남기지 않는다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    shaped_service = _cluster_service()
    if command is None:
        shaped_service.pop("command")
    else:
        shaped_service["command"] = command
    services["kor-travel-geo-postgres"] = shaped_service

    with pytest.raises(ComposeCandidateContractError, match=message):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_map_postgres_loopback_binding_is_now_enforced(tmp_path: Path) -> None:
    """이름을 지목한 회귀 검사.

    `docs/tasks.md`의 오래된 열린 항목이다 — "Map에는 `command` 검사가 아예 없다".
    PinVi는 `listen_addresses=*`로 바꾸면 거부하는데 Map은 통과했다. 그 비대칭이
    닫혔는지 **Map 이름으로** 확인한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    command = list(postgres["command"])
    command[command.index("listen_addresses=127.0.0.1")] = "listen_addresses=*"
    postgres["command"] = command

    with pytest.raises(ComposeCandidateContractError, match="loopback binding"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_sourcing_initdb_args_from_a_file_is_rejected(tmp_path: Path) -> None:
    """entrypoint의 `file_env`가 **두 번째 이름**을 읽는다.

    실제 이미지의 entrypoint 251행이 `file_env 'POSTGRES_INITDB_ARGS'`이므로
    `POSTGRES_INITDB_ARGS_FILE`이 같은 값을 준다 — 적대 리뷰 2026-09-18 D-F7이 그
    형태로 `trust` pg_hba가 만들어지는 것까지 실측했다. 이름으로 막는 술어는 이름의
    **변형**까지 봐야 한다. (`POSTGRES_HOST_AUTH_METHOD`는 252행에서 `file_env`를
    거치지 않으므로 그쪽 변형은 대상이 아니다.)
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=127.0.0.1"],
        "environment": {
            "POSTGRES_PASSWORD": "x",
            "POSTGRES_INITDB_ARGS_FILE": "/run/secrets/initdb-args",
        },
    }

    with pytest.raises(
        ComposeCandidateContractError, match="from a file the contract cannot read"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    "environment",
    [
        pytest.param([{"POSTGRES_HOST_AUTH_METHOD": "trust"}], id="dict-in-list"),
        pytest.param([5432], id="number-in-list"),
        pytest.param("POSTGRES_HOST_AUTH_METHOD=trust", id="scalar"),
    ],
)
def test_an_unreadable_environment_shape_is_rejected(
    tmp_path: Path, environment: object
) -> None:
    """읽을 수 없는 형태를 **"env가 없다"로 보지 않는다.**

    종전에는 조용히 건너뛰어서, 위의 전역 술어들이 볼 재료를 잃었다. 오늘 최종적으로
    막히는 이유는 `docker compose config`가 그 형태를 거부하기 때문뿐이고, 그것이
    바로 F4가 "방어가 아니다"라고 판정한 의존이다(적대 리뷰 D-F10).
    """

    candidate, contract_environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = {
        "image": "postgres:16",
        "environment": environment,
    }

    with pytest.raises(ComposeCandidateContractError, match="unreadable environment"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=contract_environment,
        )


def test_relocating_the_cluster_is_rejected_at_the_ui_save_path() -> None:
    """`PGDATA` 재지정이 UI 저장 경로를 통과했다(적대 리뷰 D-F11).

    후보 검증이 최종적으로 막지만, 이 화면에서 통과시키면 실패가 조작에서 멀어진다.
    """

    with pytest.raises(ContainerConfigValidationError, match="relocates the PostgreSQL"):
        validate_container_config_update(
            ports=[],
            env={"PGDATA": "/var/lib/postgresql/data/fresh"},
            networks=[],
            baseline_env={},
            service_name="kor-travel-geo-postgres",
        )


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(["-c", "listen_addresses=0.0.0.0"], id="appended-short"),
        pytest.param(["--listen-addresses=0.0.0.0"], id="appended-long"),
        pytest.param(["-c", "Listen_Addresses=0.0.0.0"], id="appended-upper"),
    ],
)
def test_an_appended_listen_addresses_cannot_widen_the_binding(
    tmp_path: Path, extra: list[str]
) -> None:
    """**postgres는 같은 설정이 여러 번 오면 마지막을 쓴다.**

    첫 판은 "정본 문자열이 목록에 있는가"만 봐서, canonical 뒤에 `0.0.0.0` 한 줄을
    더하면 통과하면서 전 인터페이스에 붙었다 — `network_mode: host`인 map-postgres
    12700이 LAN에 노출된다(적대 리뷰 2026-09-18 F 실측). 이제 **모든**
    `listen_addresses`가 loopback이어야 한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    postgres["command"] = [*postgres["command"], *extra]

    with pytest.raises(ComposeCandidateContractError, match="loopback binding"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_deleting_the_environment_does_not_hide_a_postgres_server(
    tmp_path: Path,
) -> None:
    """**식별 회피를 막는다.**

    첫 판은 `POSTGRES_PASSWORD{,_FILE}` 선언 여부로 클러스터를 식별했다. entrypoint는
    그 값을 **빈 PGDATA에서만** 요구하므로, 이미 초기화된 PGDATA에서는 `environment`를
    통째로 지워도 서버가 뜬다 — geo·concierge는 password가 `secrets:`로 오므로 특히
    그렇다. 리뷰어가 `environment` 삭제 + `privileged: true` + `listen_addresses=0.0.0.0`
    후보를 raw·resolved 양쪽에서 통과시키고 실제 컨테이너로 재현했다(F-주장1).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-geo-postgres"] = {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=0.0.0.0"],
    }

    # 식별이 되면 **부재=trust** 검사가 먼저 말한다 — `environment`를 지운 것 자체가
    # 더 짧은 결함이기 때문이다. 요점은 문구가 아니라 **거부된다는 것**이다.
    with pytest.raises(
        ComposeCandidateContractError, match="absence selects trust"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

    # INITDB_ARGS를 되돌려 놓아도 loopback 축이 남는다.
    services["kor-travel-geo-postgres"] = {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=0.0.0.0"],
        "environment": {"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
    }
    with pytest.raises(ComposeCandidateContractError, match="loopback binding"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("privileged", True, id="privileged"),
        pytest.param("user", "root", id="user"),
        pytest.param("cap_add", ["SYS_ADMIN"], id="cap_add"),
        pytest.param("pid", "host", id="pid"),
    ],
)
def test_privilege_keys_are_refused_even_on_a_postgres_one_shot(
    tmp_path: Path, key: str, value: object
) -> None:
    """서버 판정에서 **빠지는** 서비스까지 덮는다 — 그리고 PostgreSQL에 한정하지 않는다.

    첫 판은 이 그물을 "PostgreSQL 이미지를 쓰는 서비스"에 걸었는데 이미지 문자열
    판정이 신뢰할 수 없었다(F6). 그래서 **문서 전역**으로 올렸다 — 리뷰어 둘이 각각
    실측한 것이 그 자리다: `concierge-api`에 `privileged: true` + `pid: host`를 준
    후보가 계약 전무로 통과하고 `ktdctl deploy conc`가 그것을 띄운다(호스트 root).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["kor-travel-map-db-role-bootstrap"] = {
        **services["kor-travel-map-db-role-bootstrap"],
        key: value,
    }

    with pytest.raises(ComposeCandidateContractError, match="host privilege"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_runtime_predicate_runs_on_the_resolved_entry_point(tmp_path: Path) -> None:
    """**resolved 진입점도 센다.**

    새 런타임 술어의 검사 넷이 전부 raw만 태워서, resolved 호출을 지워도 스위트가
    초록이었다(적대 리뷰 2026-09-18 F-M24). 이 저장소가 반복해서 지적받은 병이
    새 술어에서 재발한 것이고, resolved가 실제 배포에 적용되는 쪽이다.
    """

    _candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    resolved = _bootstrap_resolved(environment)
    services = resolved["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    postgres["command"] = [*postgres["command"], "-c", "hba_file=/tmp/evil.conf"]

    with pytest.raises(
        ComposeCandidateContractError, match="non-canonical hba_file"
    ):
        validate_resolved_compose_candidate_protected_values(
            resolved,
            environment=environment,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
        )


@pytest.mark.parametrize(
    ("fragment", "id_"),
    [
        pytest.param(["-i"], "bare-i", id="bare-i"),
        pytest.param(["-h", "0.0.0.0"], "h-spaced", id="h-spaced"),
        pytest.param(["-h0.0.0.0"], "h-glued", id="h-glued"),
        pytest.param(["-clisten_addresses=0.0.0.0"], "c-glued", id="c-glued"),
        pytest.param(["--listen-addresses=0.0.0.0"], "long", id="long"),
    ],
)
def test_every_spelling_that_widens_the_binding_is_refused(
    tmp_path: Path, fragment: list[str], id_: str
) -> None:
    """**파서가 postgres와 같아야 한다.**

    첫 판은 `-c name=value`와 `--name=value` 둘만 읽었다. 실제 서버는 이 다섯을 모두
    honor하고, 리뷰어가 `-i` 한 토큰으로 map·geo·concierge 세 대를 LAN에 여는 것을
    실측했다(적대 리뷰 2026-09-18 F2). 세 대 다 `network_mode: host`라
    `ports: 127.0.0.1:…`는 무시된다 — 정본 compose 주석이 스스로 적어 둔 사실이다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    postgres["command"] = [*postgres["command"], *fragment]

    with pytest.raises(ComposeCandidateContractError, match="loopback binding"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param(["-D", "/tmp/fresh"], id="D-spaced"),
        pytest.param(["-D/tmp/fresh"], id="D-glued"),
        pytest.param(["-c", "data_directory=/tmp/fresh"], id="data_directory"),
        pytest.param(["-c", "config_file=/tmp/evil.conf"], id="config_file"),
        pytest.param(["-k", "/tmp/sock"], id="socket-dir"),
        pytest.param(["--unknown-future-option=1"], id="unknown-long"),
        pytest.param(["-X"], id="unknown-short"),
        pytest.param(["extra-positional"], id="positional"),
    ],
)
def test_an_unknown_command_token_is_refused(
    tmp_path: Path, fragment: list[str]
) -> None:
    """**모르는 것이 하나라도 있으면 거부**가 이 방향의 전부다.

    금지 목록은 세 라운드 연속으로 뒤처졌다 — 매번 내가 놓친 철자가 우회로였다.
    허용 목록은 `hba_file`·`config_file`·`data_directory`·`-D`를 **따로 열거하지
    않아도** 전부 막고, 다음 postgres 버전이 추가하는 옵션에 대해서도 fail-close다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    postgres["command"] = [*postgres["command"], *fragment]

    with pytest.raises(
        ComposeCandidateContractError, match="non-canonical|canonical postgres command"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_an_undeclared_postgres_server_is_refused(tmp_path: Path) -> None:
    """`in_scope = declared OR witnessed` — 그리고 **불일치 자체가 거부**다.

    `declared`는 `config/docker-targets.yml`의 `role`(`*postgresql`)에서 온다. 그
    문서는 GM-17 A가 신뢰시켜 뒀다(trusted 설치본에서 env redirect 거부 + root 소유).
    `role`은 UI 문자열이라 아무것도 강제하지 않지만, witnessed가 declared에 없는
    형상을 거부하므로 **여섯째 postgres를 `role: db`로 선언해도 새어나가지 않는다.**
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    # 다른 축은 전부 정본으로 둔다 — 이 검사의 대상은 declared/witnessed 불일치
    # 하나다. env를 비우면 더 앞의 "부재=trust" 검사가 먼저 말한다.
    services["shadow-database"] = {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=127.0.0.1"],
        "environment": {"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
    }

    with pytest.raises(
        ComposeCandidateContractError, match="undeclared PostgreSQL server"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_declared_set_comes_from_the_trusted_document() -> None:
    """declared 축이 실제로 그 문서에서 나오는지 센다 — 리터럴 목록이 아니라.

    이 검사가 없으면 `_declared_postgres_compose_services()`를 하드코딩 집합으로
    굳혀도 아무도 빨개지지 않는다.
    """

    declared = c6c_deployment_module._declared_postgres_compose_services()
    assert declared == frozenset(
        {
            "kor-travel-geo-postgres",
            "kor-travel-concierge-postgres",
            "kor-travel-map-postgres",
            "pinvi-postgres",
            "kor-travel-shared-postgres",
        }
    ), declared


#: 정본 특권 예외 서비스의 이미지. 예외는 이름만으로 성립하지 않는다.
_PRIVILEGE_EXCEPTION_IMAGES = {
    "cadvisor": "${CADVISOR_IMAGE:-gcr.io/cadvisor/cadvisor:v0.52.1}",
    "prometheus": "${PROMETHEUS_IMAGE:-prom/prometheus:v2.53.1}",
    "grafana": "${GRAFANA_IMAGE:-grafana/grafana:11.1.4}",
}


@pytest.mark.parametrize(
    ("service_name", "key", "value"),
    [
        pytest.param("cadvisor", "privileged", True, id="cadvisor-privileged"),
        pytest.param("cadvisor", "devices", ["/dev/kmsg:/dev/kmsg"], id="cadvisor-devices"),
        pytest.param("prometheus", "user", "0", id="prometheus-root"),
        pytest.param("grafana", "user", "0", id="grafana-root"),
    ],
)
def test_the_canonical_privilege_exceptions_still_pass(
    tmp_path: Path, service_name: str, key: str, value: object
) -> None:
    """예외가 **너무 좁으면** 정본 배포가 깨진다 — 이 검사가 그 방향을 잡는다.

    정본 34 서비스 중 `privileged`·`devices`는 cadvisor, `user`는 prometheus·grafana가
    쓴다(실측 — 그 `user` 값은 둘 다 **root**다). 전역 금지가 그것까지 막으면 다음
    배포가 실패한다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services[service_name] = {
        "image": _PRIVILEGE_EXCEPTION_IMAGES[service_name],
        key: value,
    }

    # 이 서비스들은 계약의 다른 축을 태우지 않는다 — 특권 축에서 거부되지 않는 것만 본다.
    try:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    except ComposeCandidateContractError as rejection:
        assert "host privilege" not in str(rejection), rejection


@pytest.mark.parametrize(
    ("service_name", "key", "value"),
    [
        pytest.param("cadvisor", "privileged", True, id="cadvisor-privileged"),
        pytest.param("prometheus", "user", "0", id="prometheus-root"),
    ],
)
def test_a_privilege_exception_does_not_survive_an_image_swap(
    tmp_path: Path, service_name: str, key: str, value: object
) -> None:
    """**예외는 이름이 아니라 신원에 걸린다.**

    첫 판은 이름만 봤다. 정본 cadvisor의 mount와 `privileged: true`를 그대로 두고
    `image`만 바꿔도 통과했다 — **한 줄로 호스트 root**다(적대 리뷰 2026-09-18
    라운드4 F3 실측). 버전 bump는 막지 않되 임의 이미지는 막는다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services[service_name] = {"image": "attacker.invalid/x:latest", key: value}

    with pytest.raises(ComposeCandidateContractError, match="host privilege"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_privilege_exception_set_is_pinned() -> None:
    """예외 집합 자체를 리터럴로 못박는다.

    첫 판은 이 집합이 무결박이라, 거기에 `("kor-travel-concierge-api", "privileged")`를
    더하는 변이가 전체 스위트를 통과했다(적대 리뷰 라운드4 F3). 늘리려면 이 검사를
    함께 고쳐야 하고, 그 마찰이 규칙의 값어치다.
    """

    assert c6c_deployment_module._ALLOWED_PRIVILEGE_KEY_PAIRS == frozenset(
        {
            ("cadvisor", "privileged"),
            ("cadvisor", "devices"),
            ("prometheus", "user"),
            ("grafana", "user"),
        }
    )


# ── 라운드 5: 내가 만든 표면 넷 ──────────────────────────────────────────


@pytest.mark.parametrize("empty", [{}, []])
def test_an_empty_environment_still_turns_the_contract_on(
    tmp_path: Path, empty: object
) -> None:
    """**약화 24칸의 정체.**

    라운드 3에서 `environment`를 truthy 판정으로 옮겼다. 그 결과
    `{image, environment: {}}`인 concierge-api가 UI 없이 계약을 **통째로** 빠져나갔다 —
    2,836형상 대조에서 약화된 칸이 전부 이 결함군이었다(적대 리뷰 2026-09-18 라운드4
    F1). compose는 stub에 `environment`를 붙이지 **않으므로** 빈 dict·빈 list는
    "이 서비스를 실제로 선언했다"는 신호다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    assert "kor-travel-concierge-ui" not in services, "전제: fixture에 UI가 없다"
    services["kor-travel-concierge-api"] = {
        "image": "kor-travel-concierge-api:latest-main",
        "environment": empty,
    }

    with pytest.raises(ComposeCandidateContractError) as rejection:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    assert "Concierge" in str(rejection.value), rejection.value


@pytest.mark.parametrize(
    "signal", ["ports", "env_file", "build"]
)
def test_an_empty_present_signal_is_not_a_deployment(signal: str) -> None:
    """이 셋은 compose가 stub에 붙이지 않으므로 **truthy**가 맞다.

    `environment`는 이 집합에 **없다** — 위 검사가 그 이유를 센다.
    """

    assert signal in c6c_deployment_module._CONCIERGE_PRESENT_DEPLOYMENT_SIGNALS
    assert "environment" not in c6c_deployment_module._CONCIERGE_PRESENT_DEPLOYMENT_SIGNALS
    c6c_deployment_module._validate_concierge_ui_canonical_contract(
        {"kor-travel-concierge-api": {"image": "x", signal: [] if signal != "build" else {}}},
        {},
        resolved=False,
    )


@pytest.mark.parametrize(
    ("flaw_key", "flaw_value", "message"),
    [
        pytest.param("privileged", True, "host privilege", id="privilege-axis"),
        pytest.param(
            # 이 형상에서는 런타임 술어가 **undeclared 분기**로 말한다 — 문서에 아는
            # postgres가 하나도 없으므로 declared 집합에 없는 서버다. 그것도 같은
            # 술어이고, 요점은 그 술어가 map-postgres 존재에 게이팅되지 않았다는 것이다.
            "command",
            ["postgres", "-i"],
            "undeclared PostgreSQL server",
            id="runtime-axis",
        ),
    ],
)
def test_the_new_predicates_survive_a_shrunken_required_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flaw_key: str,
    flaw_value: object,
    message: str,
) -> None:
    """**"어떤 서비스의 존재에도 게이팅하지 마라"를 효과로 센다.**

    두 전역 술어를 `if MAP_PG not in services: return`으로 감싸는 변이가 **전체 스위트를
    초록으로 통과**했다(적대 리뷰 2026-09-18 라운드4 F2). no-op 변이는 죽는데 게이팅
    변이는 살았다 — 검사가 "술어가 존재하고 거부한다"만 세고 "게이팅되지 않았다"는
    세지 않았기 때문이다.

    이 파일의 `_s4_without_*` 검사들과 같은 모양으로, required 집합을 줄이고 문서에서도
    그 서비스를 **실제로 지운** 뒤 다른 서비스의 결함이 여전히 거부되는지 본다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    # 이 파일이 확립한 시뮬레이션 헬퍼를 그대로 쓴다 — PinVi family를 scope와 문서에서
    # 함께 뺀다. Map postgres도 문서에서 지워 "아는 postgres가 하나도 없는" 형상을
    # 만든다.
    shaped = _s4_without_pinvi_services(monkeypatch, candidate)
    shaped = _shape_without(shaped, ("kor-travel-map-postgres",))
    # required 집합에서도 뺀다 — S4가 실제로 만드는 형상이 그것이다.
    for name in (
        "_CANDIDATE_REQUIRED_PROTECTED_SERVICES",
        "_CANDIDATE_KNOWN_SERVICE_NAMES",
    ):
        monkeypatch.setattr(
            c6c_deployment_module,
            name,
            frozenset(
                value
                for value in getattr(c6c_deployment_module, name)
                if "postgres" not in value
            ),
        )
    # PinVi family validator들도 함께 끈다 — S4가 scope에서 빼는 것이 바로 그것들이고,
    # 켜 두면 서비스 부재를 먼저 말해서 이 검사가 목표 지점에 닿지 못한다.
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
        "_validate_pinvi_postgres_identity",
        lambda services, environment, *, resolved: None,
    )
    monkeypatch.setattr(
        c6c_deployment_module,
        "_validate_pinvi_db_runtime_role",
        lambda services, environment, *, resolved: None,
    )
    services = shaped["services"]
    assert isinstance(services, dict)
    assert not any("postgres" in name for name in services), sorted(services)
    services["some-future-service"] = {
        "image": "postgres:16",
        "command": ["postgres", "-c", "listen_addresses=127.0.0.1"],
        "environment": {"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
        flaw_key: flaw_value,
    }

    with pytest.raises(ComposeCandidateContractError, match=message):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("device_cgroup_rules", ["c *:* rmw"], id="device_cgroup_rules"),
        pytest.param("cgroup", "host", id="cgroup"),
        pytest.param("uts", "host", id="uts"),
        pytest.param("runtime", "sysbox-runc", id="runtime"),
    ],
)
def test_the_remaining_host_privilege_keys_are_refused(
    tmp_path: Path, key: str, value: object
) -> None:
    """열넷 밖에 다섯이 더 있었다.

    `device_cgroup_rules`가 `devices`의 cgroup 절반이고, `docker compose config`가 이
    값들을 그대로 낸다는 것도 실측됐다(적대 리뷰 2026-09-18 라운드4 F4).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["some-new-service"] = {"image": "alpine:3.20", key: value}

    with pytest.raises(ComposeCandidateContractError, match="host privilege"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_a_nested_device_reservation_is_refused(tmp_path: Path) -> None:
    """`deploy.resources.reservations.devices`는 **중첩**이라 최상위 스캔에 안 걸렸다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["some-new-service"] = {
        "image": "alpine:3.20",
        "deploy": {
            "resources": {
                "reservations": {"devices": [{"capabilities": ["gpu"], "count": "all"}]}
            }
        },
    }

    with pytest.raises(
        ComposeCandidateContractError, match="deploy.resources.reservations.devices"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("cap_drop", ["ALL"], id="cap_drop-all"),
        pytest.param("security_opt", ["no-new-privileges:true"], id="no-new-privileges"),
        pytest.param("user", "65534:65534", id="non-root-user"),
    ],
)
def test_hardening_is_not_mistaken_for_privilege(
    tmp_path: Path, key: str, value: object
) -> None:
    """**능력을 버리는 것은 특권 부여가 아니다.**

    첫 판은 키 존재만 봐서 `cap_drop: [ALL]`·`no-new-privileges:true`·비-root `user`를
    전부 거부했다 — 하드닝을 금지하는 규칙이었다(적대 리뷰 2026-09-18 라운드4 F12).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["some-new-service"] = {"image": "alpine:3.20", key: value}

    try:
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )
    except ComposeCandidateContractError as rejection:
        assert "host privilege" not in str(rejection), rejection


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(["apparmor:unconfined"], id="apparmor"),
        pytest.param(["seccomp:unconfined"], id="seccomp"),
        pytest.param(["systempaths=unconfined"], id="systempaths"),
    ],
)
def test_unconfined_security_opt_is_still_refused(
    tmp_path: Path, value: list[str]
) -> None:
    """값을 보게 했다고 그 축이 열리면 안 된다 — `unconfined`는 그대로 막는다."""

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    services["some-new-service"] = {"image": "alpine:3.20", "security_opt": value}

    with pytest.raises(ComposeCandidateContractError, match="host privilege"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            "psql -U postgres -c \"ALTER ROLE postgres PASSWORD 'pwned'\"",
            id="psql-alter-role",
        ),
        pytest.param(
            "pg_isready -h 127.0.0.1 && psql -c \"COPY (SELECT 1) TO PROGRAM 'id'\"",
            id="chained-psql",
        ),
        pytest.param("sh -c 'id > /tmp/o'", id="shell"),
    ],
)
def test_a_healthcheck_cannot_run_an_arbitrary_program(
    tmp_path: Path, payload: str
) -> None:
    """허용 목록이 **키 이름만** 묶었던 자리.

    컨테이너 안 소켓은 `local all all trust`라 healthcheck의 `psql`이 **superuser
    실행**이다(적대 리뷰 2026-09-18 라운드4 F5 실측). 정본 넷의 payload에 나타나는
    프로그램 자리는 `pg_isready`·`test`·`cat` 셋뿐이므로 그 축도 허용 목록으로
    뒤집었다 — 금지 목록은 이 파일에서 세 라운드 연속으로 뒤처졌다.
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    postgres["healthcheck"] = {"test": ["CMD-SHELL", payload], "interval": "10s"}

    with pytest.raises(
        ComposeCandidateContractError, match="healthcheck runs a non-canonical program"
    ):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )


def test_the_canonical_healthchecks_still_pass(tmp_path: Path) -> None:
    """정본 넷의 healthcheck는 그대로 통과한다 — 좁히기가 넓어지면 여기가 잡는다.

    map은 `test "$(cat /proc/1/comm)" = postgres && pg_isready …`이므로 프로그램 자리가
    셋이다(`test`·`cat`·`pg_isready`).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    services = candidate["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    assert "healthcheck" in postgres, "전제: 정본 fragment가 healthcheck를 담는다"
    validate_compose_candidate_protected_values(
        candidate,
        compose_path=str(_COMPOSE_PATH),
        root_env_path=str(root_env),
        environment=environment,
    )


def test_removing_the_loopback_binding_entirely_is_refused(tmp_path: Path) -> None:
    """규칙의 **절반**("아예 없다")을 아무 검사도 세지 않았다.

    `if not bindings: raise` 절을 무력화하는 변이가 전체 스위트를 통과했다(적대 리뷰
    2026-09-18 라운드4 F10).
    """

    candidate, environment, root_env = _bootstrap_candidate(tmp_path)
    shaped = deepcopy(candidate)
    services = shaped["services"]
    assert isinstance(services, dict)
    postgres = services["kor-travel-map-postgres"]
    assert isinstance(postgres, dict)
    command = list(postgres["command"])
    index = command.index("listen_addresses=127.0.0.1")
    del command[index - 1 : index + 1]
    postgres["command"] = command

    with pytest.raises(ComposeCandidateContractError, match="loopback binding"):
        validate_compose_candidate_protected_values(
            shaped,
            compose_path=str(_COMPOSE_PATH),
            root_env_path=str(root_env),
            environment=environment,
        )

