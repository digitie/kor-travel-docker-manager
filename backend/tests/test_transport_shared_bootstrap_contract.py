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
    assert environment["KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_DB"] == (
        "kor_travel_transport_dagster"
    )
    # 2026-09-25: 별도 dagster role은 통합돼 더는 없다(PinVi/geo/weather와 같은
    # 단일-role 패턴) — 두 DB는 여전히 분리, owner만 하나.
    assert "KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_APP_USER" not in environment

    command = service["command"]
    assert isinstance(command, list)
    script = command[-1]
    assert isinstance(script, str)
    assert 'REVOKE CONNECT ON DATABASE \\"$$PGDATABASE\\" FROM PUBLIC' in script
    assert 'REVOKE CONNECT ON DATABASE \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_DB\\" FROM PUBLIC' in script
    assert 'REVOKE CONNECT ON DATABASE \\"$$KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_DB\\" FROM PUBLIC' in script
    assert 'GRANT CONNECT ON DATABASE \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_DB\\" TO \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_USER\\"' in script
    assert 'GRANT CONNECT ON DATABASE \\"$$KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_DB\\" TO \\"$$KOR_TRAVEL_TRANSPORT_SHARED_APP_USER\\"' in script
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
    assert 'mc mb --ignore-existing "local/$bucket"' in bucket_script

    env_example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "KOR_TRAVEL_TRANSPORT_SHARED_APP_USER=" not in env_example
    assert "KOR_TRAVEL_TRANSPORT_SHARED_APP_DB=" not in env_example
    assert "KOR_TRAVEL_TRANSPORT_SHARED_APP_PASSWORD=" in env_example
    assert "KOR_TRAVEL_TRANSPORT_DAGSTER_SHARED_APP_PASSWORD=" not in env_example
    assert "KOR_TRAVEL_TRANSPORT_RUSTFS_BUCKET=kor-travel-transport-raw" in env_example
