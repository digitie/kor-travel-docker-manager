"""kor-travel-transport 공용 DB/RustFS provision 계약 회귀 방지."""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict[str, object]:
    parsed = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_transport_bootstrap_separates_application_and_dagster_databases() -> None:
    compose = _compose()
    services = compose["services"]
    assert isinstance(services, dict)
    service = services["kor-travel-shared-db-init-transport"]
    assert isinstance(service, dict)

    assert service["image"] == "postgis/postgis:16-3.5"
    assert service["restart"] == "no"
    assert service["network_mode"] == "${KTDM_DOCKER_NETWORK_MODE:-host}"
    assert service["depends_on"] == {
        "kor-travel-shared-postgres": {"condition": "service_healthy"}
    }

    environment = service["environment"]
    assert isinstance(environment, dict)
    assert environment["PGHOST"] == "127.0.0.1"
    assert environment["KOR_TRAVEL_TRANSPORT_SHARED_APP_DB"] == "kor_travel_transport"
    assert environment["KOR_TRAVEL_TRANSPORT_SHARED_APP_USER"] == "kor_travel_transport_app"
    # 옛 Dagster metadata DB(`kor_travel_transport_dagster`)는 platform-topology.md §7 4단계로 막히고
    # DROP된다 — db-init은 그것을 만들거나 소유자를 확인하거나 CONNECT를 주지 않는다.
    assert "KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_DB" not in environment
    assert "KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_APP_USER" not in environment

    command = service["command"]
    assert isinstance(command, list)
    script = command[-1]
    assert isinstance(script, str)
    assert 'REVOKE CONNECT ON DATABASE \\"$$PGDATABASE\\" FROM PUBLIC' in script
    assert 'REVOKE CONNECT ON DATABASE \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_DB\\" FROM PUBLIC' in script
    assert 'GRANT CONNECT ON DATABASE \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_DB\\" TO \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_USER\\"' in script
    assert "DAGSTER" not in script
    assert "SELECT pg_get_userbyid(datdba) FROM pg_database" in script
    assert "-v role_password=\"$$password\"" in script
    assert "PASSWORD :'role_password'" in script
    assert "PASSWORD '$$password'" not in script

    secrets = compose["secrets"]
    assert isinstance(secrets, dict)
    assert secrets["kor-travel-transport-shared-app-password"] == {
        "environment": "KOR_TRAVEL_TRANSPORT_SHARED_APP_PASSWORD"
    }
    assert "kor-travel-transport-dagster-shared-app-password" not in secrets


def test_transport_raw_bucket_is_provisioned_and_documented() -> None:
    compose = _compose()
    services = compose["services"]
    assert isinstance(services, dict)
    rustfs_init = services["rustfs-init"]
    assert isinstance(rustfs_init, dict)
    environment = rustfs_init["environment"]
    assert isinstance(environment, dict)
    assert environment["KOR_TRAVEL_TRANSPORT_RUSTFS_BUCKET"] == (
        "${KOR_TRAVEL_TRANSPORT_RUSTFS_BUCKET:-kor-travel-transport-raw}"
    )

    bucket_script = (_ROOT / "scripts" / "ensure-rustfs-buckets.sh").read_text(
        encoding="utf-8"
    )
    assert '"${KOR_TRAVEL_TRANSPORT_RUSTFS_BUCKET:-kor-travel-transport-raw}"' in bucket_script
    assert 'ensure_bucket "$bucket"' in bucket_script

    env_example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "KOR_TRAVEL_TRANSPORT_SHARED_APP_USER=" not in env_example
    assert "KOR_TRAVEL_TRANSPORT_SHARED_APP_DB=" not in env_example
    assert "KOR_TRAVEL_TRANSPORT_SHARED_APP_PASSWORD=" in env_example
    assert "KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_APP_PASSWORD=" not in env_example
    assert "KOR_TRAVEL_TRANSPORT_RUSTFS_BUCKET=kor-travel-transport-raw" in env_example
