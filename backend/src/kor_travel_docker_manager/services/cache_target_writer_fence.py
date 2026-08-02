from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_ENVIRONMENT_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_POSTGRES_URI_SCHEMES = frozenset(
    {
        "postgres",
        "postgresql",
        "postgresql+asyncpg",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }
)
_CONNECTION_ENV_SUFFIXES = (
    "DATABASE_URL",
    "DATABASE_DSN",
    "PG_DSN",
    "PG_URL",
    "POSTGRES_DSN",
    "POSTGRES_URL",
    "SQLALCHEMY_DATABASE_URI",
)
_SPLIT_CONNECTION_PARTS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER")
_IDENTITY_QUERY_KEYS = frozenset(
    {"host", "hostaddr", "port", "database", "dbname", "user"}
)
_TARGET_HASH_NAMESPACE = b"ktdm-cache-target-postgres-target-v1\0"
_INVENTORY_HASH_NAMESPACE = b"ktdm-cache-target-global-writer-fence-v1\0"


@dataclass(frozen=True)
class GlobalWriterFenceEvidence:
    contract_version: str
    protected_target_count: int
    expected_stopped_writer_count: int
    running_container_count: int
    unrelated_running_container_count: int
    inventory_sha256: str


@dataclass(frozen=True)
class _ContainerObservation:
    container_id: str
    name: str
    running: bool
    target_sha256s: tuple[str, ...]

    def logical_value(self) -> dict[str, object]:
        return {
            "container_id": self.container_id,
            "name": self.name,
            "running": self.running,
            "target_sha256s": list(self.target_sha256s),
        }


def postgres_target_sha256(connection: str) -> str:
    """PostgreSQL 연결 문자열에서 credential을 제외한 target identity를 만든다."""

    host, port, database = _parse_postgres_connection(connection)
    payload = json.dumps(
        {"database": database, "host": host, "port": port},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(_TARGET_HASH_NAMESPACE + payload).hexdigest()


def postgres_target_sha256s_from_environment(
    environment: Mapping[str, str],
) -> frozenset[str]:
    """resolved Env를 target hash로 바꾸고 모호한 연결 표기는 거부한다.

    PostgreSQL 전용/일반 database connection suffix는 보호 계약 slot이므로 다른 URI
    scheme도 조용히 무시하지 않는다. 반면 `REDIS_URL`처럼 별도 protocol을 명시한 key는
    PostgreSQL inventory 범위 밖이며, 값 자체가 PostgreSQL URI일 때만 수집한다.
    """

    normalized = _validate_environment(environment)
    connections: list[str] = []
    split_groups: dict[str, dict[str, str]] = {}

    for key, value in normalized.items():
        upper_key = key.upper()
        split = _split_connection_component(upper_key)
        if split is not None:
            prefix, part = split
            split_groups.setdefault(prefix, {})[part] = value
            continue
        if _is_connection_environment_name(upper_key) or _looks_like_postgres_uri(value):
            connections.append(value)

    for group in split_groups.values():
        present = frozenset(group)
        if present != frozenset(_SPLIT_CONNECTION_PARTS):
            raise DeploymentContractError(
                "container has an incomplete split PostgreSQL connection environment"
            )
        connections.append(
            "host={host} port={port} dbname={database} user={user}".format(
                host=shlex.quote(group["PGHOST"]),
                port=shlex.quote(group["PGPORT"]),
                database=shlex.quote(group["PGDATABASE"]),
                user=shlex.quote(group["PGUSER"]),
            )
        )

    return frozenset(postgres_target_sha256(value) for value in connections)


def cache_target_writer_environments_from_resolved_compose(
    resolved: Mapping[str, object],
    service_names: Sequence[str],
) -> dict[str, Mapping[str, str]]:
    """frozen resolved Compose의 service 이름을 exact runtime 이름/Env로 바꾼다."""

    if len(service_names) != 5 or len(service_names) != len(set(service_names)):
        raise DeploymentContractError(
            "cache-target global fence requires exactly five writer services"
        )
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("cutover resolved services are invalid")
    result: dict[str, Mapping[str, str]] = {}
    for service_name in service_names:
        service = services.get(service_name)
        if not isinstance(service_name, str) or not isinstance(service, Mapping):
            raise DeploymentContractError("cache-target writer service is invalid")
        container_name = service.get("container_name")
        environment = service.get("environment")
        if (
            not isinstance(container_name, str)
            or _CONTAINER_NAME.fullmatch(container_name) is None
            or not isinstance(environment, Mapping)
            or container_name in result
        ):
            raise DeploymentContractError(
                "cache-target writer runtime identity is invalid"
            )
        result[container_name] = _validate_environment(environment)
    return result


def attest_cache_target_global_writer_fence(
    *,
    expected_stopped_writers: Mapping[str, Mapping[str, str]],
    docker_bin: str = "docker",
    cwd: str | Path | None = None,
) -> GlobalWriterFenceEvidence:
    """모든 running container에서 세 보호 DB를 쓰는 foreign writer가 없음을 증명한다.

    반환값과 예외에는 원문 Env/DSN/credential을 포함하지 않는다. Docker inventory는
    list/inspect를 두 번 읽어 container create/remove/rename race도 fail-close한다.
    """

    expected = _validate_expected_writers(expected_stopped_writers)
    expected_targets = {
        name: postgres_target_sha256s_from_environment(environment)
        for name, environment in expected.items()
    }
    if any(not targets for targets in expected_targets.values()):
        raise DeploymentContractError(
            "cache-target writer has no protected PostgreSQL target"
        )
    protected_targets = frozenset().union(*expected_targets.values())
    if len(protected_targets) != 3:
        raise DeploymentContractError(
            "cache-target writer inventory does not resolve three protected databases"
        )

    first = _capture_inventory(
        expected_names=tuple(sorted(expected)),
        docker_bin=docker_bin,
        cwd=cwd,
    )
    second = _capture_inventory(
        expected_names=tuple(sorted(expected)),
        docker_bin=docker_bin,
        cwd=cwd,
    )
    if first != second:
        raise DeploymentContractError(
            "Docker container inventory changed during global writer fencing"
        )
    running, stopped = first

    stopped_by_name = {observation.name: observation for observation in stopped}
    if frozenset(stopped_by_name) != frozenset(expected):
        raise DeploymentContractError(
            "cache-target exact writer container identity is incomplete"
        )
    for name, targets in expected_targets.items():
        observation = stopped_by_name[name]
        if observation.running:
            raise DeploymentContractError(
                "cache-target exact writer container is still running"
            )
        if frozenset(observation.target_sha256s) != targets:
            raise DeploymentContractError(
                "cache-target exact writer runtime target differs from frozen Compose"
            )

    foreign = [
        observation
        for observation in running
        if protected_targets.intersection(observation.target_sha256s)
    ]
    if foreign:
        raise DeploymentContractError(
            "a running foreign container targets a protected cache-target database"
        )

    logical_inventory = {
        "contract_version": "ktdm-cache-target-global-writer-fence/v1",
        "protected_target_sha256s": sorted(protected_targets),
        "running": [item.logical_value() for item in running],
        "stopped_exact_writers": [item.logical_value() for item in stopped],
    }
    inventory_payload = json.dumps(
        logical_inventory,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return GlobalWriterFenceEvidence(
        contract_version="ktdm-cache-target-global-writer-fence/v1",
        protected_target_count=len(protected_targets),
        expected_stopped_writer_count=len(stopped),
        running_container_count=len(running),
        unrelated_running_container_count=len(running),
        inventory_sha256=hashlib.sha256(
            _INVENTORY_HASH_NAMESPACE + inventory_payload
        ).hexdigest(),
    )


def _capture_inventory(
    *,
    expected_names: tuple[str, ...],
    docker_bin: str,
    cwd: str | Path | None,
) -> tuple[tuple[_ContainerObservation, ...], tuple[_ContainerObservation, ...]]:
    running_ids = _list_running_container_ids(docker_bin=docker_bin, cwd=cwd)
    running_payload = _inspect_containers(
        running_ids,
        docker_bin=docker_bin,
        cwd=cwd,
        label="running container",
    )
    running = _parse_container_observations(
        running_payload,
        expected_identities=frozenset(running_ids),
        expected_running=True,
    )
    stopped_payload = _inspect_containers(
        expected_names,
        docker_bin=docker_bin,
        cwd=cwd,
        label="exact writer container",
    )
    stopped = _parse_container_observations(
        stopped_payload,
        expected_names=frozenset(expected_names),
        expected_running=False,
    )
    return running, stopped


def _list_running_container_ids(
    *, docker_bin: str, cwd: str | Path | None
) -> tuple[str, ...]:
    completed = _run_docker(
        [docker_bin, "container", "ls", "--quiet", "--no-trunc"],
        cwd=cwd,
    )
    ids = tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
    if len(ids) != len(set(ids)) or any(_CONTAINER_ID.fullmatch(item) is None for item in ids):
        raise DeploymentContractError("Docker returned invalid running container identities")
    return tuple(sorted(ids))


def _inspect_containers(
    identities: Sequence[str],
    *,
    docker_bin: str,
    cwd: str | Path | None,
    label: str,
) -> list[object]:
    if not identities:
        return []
    completed = _run_docker(
        [docker_bin, "container", "inspect", *identities],
        cwd=cwd,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise DeploymentContractError(
            f"Docker returned invalid {label} metadata"
        ) from None
    if not isinstance(payload, list):
        raise DeploymentContractError(f"Docker returned invalid {label} metadata")
    return payload


def _run_docker(argv: list[str], *, cwd: str | Path | None) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise DeploymentContractError(
            "Docker inventory inspection could not run"
        ) from None
    if completed.returncode != 0:
        raise DeploymentContractError("Docker inventory inspection failed")
    return completed


def _parse_container_observations(
    payload: list[object],
    *,
    expected_identities: frozenset[str] | None = None,
    expected_names: frozenset[str] | None = None,
    expected_running: bool,
) -> tuple[_ContainerObservation, ...]:
    observations: list[_ContainerObservation] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise DeploymentContractError("Docker returned invalid container metadata")
        container_id = item.get("Id")
        raw_name = item.get("Name")
        config = item.get("Config")
        state = item.get("State")
        if (
            not isinstance(container_id, str)
            or _CONTAINER_ID.fullmatch(container_id) is None
            or not isinstance(raw_name, str)
            or not isinstance(config, Mapping)
            or not isinstance(state, Mapping)
            or not isinstance(state.get("Running"), bool)
        ):
            raise DeploymentContractError("Docker returned invalid container metadata")
        name = raw_name.removeprefix("/")
        if _CONTAINER_NAME.fullmatch(name) is None:
            raise DeploymentContractError("Docker returned invalid container metadata")
        if state["Running"] is not expected_running:
            raise DeploymentContractError("Docker container state differs during writer fencing")
        environment = _environment_from_inspect(config.get("Env"))
        observations.append(
            _ContainerObservation(
                container_id=container_id,
                name=name,
                running=expected_running,
                target_sha256s=tuple(
                    sorted(postgres_target_sha256s_from_environment(environment))
                ),
            )
        )

    ids = [item.container_id for item in observations]
    names = [item.name for item in observations]
    if len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise DeploymentContractError("Docker container inventory contains duplicate identities")
    if expected_identities is not None and frozenset(ids) != expected_identities:
        raise DeploymentContractError("running Docker container inventory is incomplete")
    if expected_names is not None and frozenset(names) != expected_names:
        raise DeploymentContractError("cache-target exact writer container identity drifted")
    return tuple(sorted(observations, key=lambda item: item.container_id))


def _environment_from_inspect(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        raise DeploymentContractError("container environment metadata is invalid")
    environment: dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or "=" not in item:
            raise DeploymentContractError("container environment metadata is invalid")
        key, item_value = item.split("=", 1)
        if key in environment:
            raise DeploymentContractError("container has duplicate environment variables")
        environment[key] = item_value
    return environment


def _validate_expected_writers(
    value: Mapping[str, Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    if len(value) != 5:
        raise DeploymentContractError(
            "cache-target global fence requires exactly five stopped writers"
        )
    expected: dict[str, Mapping[str, str]] = {}
    for name, environment in value.items():
        if not isinstance(name, str) or _CONTAINER_NAME.fullmatch(name) is None:
            raise DeploymentContractError("cache-target writer container name is invalid")
        if not isinstance(environment, Mapping):
            raise DeploymentContractError("cache-target writer environment is invalid")
        expected[name] = environment
    return expected


def _validate_environment(environment: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in environment.items():
        if (
            not isinstance(key, str)
            or _ENVIRONMENT_NAME.fullmatch(key) is None
            or not isinstance(value, str)
            or key in normalized
        ):
            raise DeploymentContractError("container environment metadata is invalid")
        normalized[key] = value
    return normalized


def _is_connection_environment_name(key: str) -> bool:
    return any(key == suffix or key.endswith(f"_{suffix}") for suffix in _CONNECTION_ENV_SUFFIXES)


def _looks_like_postgres_uri(value: str) -> bool:
    scheme = value.partition("://")[0].lower()
    return scheme == "postgres" or scheme.startswith("postgresql")


def _split_connection_component(key: str) -> tuple[str, str] | None:
    for part in _SPLIT_CONNECTION_PARTS:
        if key == part:
            return "", part
        suffix = f"_{part}"
        if key.endswith(suffix):
            return key[: -len(suffix)], part
    return None


def _parse_postgres_connection(value: str) -> tuple[str, int, str]:
    candidate = value.strip()
    if not candidate:
        raise DeploymentContractError("PostgreSQL connection metadata is invalid")
    if "://" in candidate:
        return _parse_postgres_uri(candidate)
    return _parse_postgres_keyword_dsn(candidate)


def _parse_postgres_uri(value: str) -> tuple[str, int, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 5432
    except ValueError:
        raise DeploymentContractError(
            "PostgreSQL connection metadata is invalid"
        ) from None
    if parsed.scheme.lower() not in _POSTGRES_URI_SCHEMES or parsed.hostname is None:
        raise DeploymentContractError("PostgreSQL connection metadata is invalid")
    if parsed.fragment:
        raise DeploymentContractError("PostgreSQL connection metadata is invalid")
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise DeploymentContractError(
            "PostgreSQL connection metadata is invalid"
        ) from None
    if any(key.lower() in _IDENTITY_QUERY_KEYS for key, _ in query):
        raise DeploymentContractError("PostgreSQL connection metadata is ambiguous")
    database = unquote(parsed.path.removeprefix("/"))
    return _canonical_target(unquote(parsed.hostname), port, database)


def _parse_postgres_keyword_dsn(value: str) -> tuple[str, int, str]:
    try:
        parts = shlex.split(value, posix=True)
    except ValueError:
        raise DeploymentContractError(
            "PostgreSQL connection metadata is invalid"
        ) from None
    parameters: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            raise DeploymentContractError("PostgreSQL connection metadata is invalid")
        key, item_value = part.split("=", 1)
        key = key.lower()
        if not key or key in parameters:
            raise DeploymentContractError("PostgreSQL connection metadata is invalid")
        parameters[key] = item_value
    host = parameters.get("host")
    database = parameters.get("dbname", parameters.get("database"))
    port_text = parameters.get("port", "5432")
    if host is None or database is None or not port_text.isascii() or not port_text.isdigit():
        raise DeploymentContractError("PostgreSQL connection metadata is invalid")
    return _canonical_target(host, int(port_text), database)


def _canonical_target(host: str, port: int, database: str) -> tuple[str, int, str]:
    normalized_host = host.strip().rstrip(".").casefold()
    normalized_database = database.strip()
    if (
        not normalized_host
        or any(character.isspace() for character in normalized_host)
        or "/" in normalized_host
        or not 1 <= port <= 65535
        or not normalized_database
        or "/" in normalized_database
        or "\x00" in normalized_database
    ):
        raise DeploymentContractError("PostgreSQL connection metadata is invalid")
    try:
        address = ipaddress.ip_address(normalized_host)
        normalized_host = "loopback" if address.is_loopback else address.compressed
    except ValueError:
        if normalized_host == "localhost":
            normalized_host = "loopback"
        if len(normalized_host) > 253 or any(
            not label
            or len(label) > 63
            or re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label)
            is None
            for label in normalized_host.split(".")
        ):
            raise DeploymentContractError("PostgreSQL connection metadata is invalid") from None
    return normalized_host, port, normalized_database
