import logging
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import docker
import yaml
from docker.errors import DockerException, NotFound

from kor_travel_docker_manager.services.c6c_deployment import (
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    assert_c6c_mutation_allowed,
    assert_manager_mutation_allowed,
    c6c_deployment_lock,
    compose_volume_graph_hash,
    revalidate_candidate_system_bind_snapshots,
)
from kor_travel_docker_manager.services.compose_service import (
    ComposeEnvironmentSnapshot,
    ComposeTransactionSnapshot,
    ValidatedComposeCandidate,
    _capture_compose_environment_snapshot,
    compose_service,
    get_c6c_deployment_lock_path,
    get_compose_path,
)
from kor_travel_docker_manager.services.registry import MANAGED_CONTAINERS

logger = logging.getLogger(__name__)


def _get_compose_path() -> str:
    return get_compose_path()


def _public_url(spec: dict[str, Any]) -> str | None:
    """컨테이너의 운영(prod) 공개 URL을 환경변수에서 해석한다.

    docker-targets.yml의 `prod_url_env`가 가리키는 환경변수(KTDM_PROD_URL_*)에서
    실제 도메인을 읽는다. 미설정이면 None을 반환해 대시보드가 로컬 connection만 표시한다.
    실제 도메인은 저장소에 커밋하지 않고 gitignore된 .env에만 둔다.
    """
    env_key = spec.get("prod_url_env")
    if not env_key:
        return None
    value = os.environ.get(str(env_key), "").strip()
    return value or None


def get_compose_config(path: str | None = None) -> dict[str, Any]:
    path = path or _get_compose_path()
    if not os.path.exists(path):
        logger.error(f"docker-compose.yml not found at {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Error reading docker-compose.yml: {e}")
        return {}


def _atomic_write(path: str, payload: bytes, *, mode: int | None = None) -> None:
    destination = Path(path)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _save_compose_config_unlocked(
    config: dict[str, Any],
    *,
    compose_path: str | None = None,
) -> None:
    payload = yaml.safe_dump(
        config,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    path = compose_path or _get_compose_path()
    mode = Path(path).stat().st_mode & 0o777
    _atomic_write(path, payload, mode=mode)


def _validate_compose_candidate(
    config: dict[str, Any],
    *,
    environment_snapshot: ComposeEnvironmentSnapshot | None = None,
) -> ValidatedComposeCandidate:
    return compose_service.capture_compose_candidate_transaction(
        config,
        environment_snapshot=environment_snapshot,
    )


def save_compose_config(config: dict[str, Any]) -> None:
    """검증·host lock을 거친 manager compose 파일 변경 진입점."""

    with c6c_deployment_lock(get_c6c_deployment_lock_path()):
        environment_snapshot = _capture_compose_environment_snapshot(
            environment_override=None
        )
        assert_manager_mutation_allowed(
            environment=environment_snapshot.effective
        )
        assert_c6c_mutation_allowed(
            ["kor-travel-map-api", "pinvi-api"],
            environment=environment_snapshot.effective,
        )
        compose_path = Path(environment_snapshot.compose_path)
        original_bytes = compose_path.read_bytes()
        current = get_compose_config(str(compose_path))
        if not current or (
            compose_volume_graph_hash(config) != compose_volume_graph_hash(current)
        ):
            raise ComposeCandidateContractError(
                "compose candidate volume configuration is immutable through the Manager API"
            )
        validation = _validate_compose_candidate(
            config,
            environment_snapshot=environment_snapshot,
        )
        revalidate_candidate_system_bind_snapshots(
            validation.system_bind_snapshots
        )
        if compose_path.read_bytes() != original_bytes:
            raise ComposeCandidateContractError(
                "compose candidate source changed during the config request"
            )
        candidate_transaction = validation.transaction_snapshot
        if candidate_transaction is None:
            raise ComposeCandidateContractError(
                "compose candidate transaction was not captured"
            )
        _atomic_write(
            str(compose_path),
            candidate_transaction.compose_source_bytes,
            mode=candidate_transaction.compose_source_mode,
        )


SENSITIVE_KEY_PARTS = ("PASSWORD", "SECRET", "TOKEN", "ACCESS_KEY", "PRIVATE_KEY")


def _is_sensitive_key(key: str) -> bool:
    upper_key = key.upper()
    return any(part in upper_key for part in SENSITIVE_KEY_PARTS)


def _redact_env_pair(raw_pair: str) -> str:
    if "=" not in raw_pair:
        return raw_pair
    key, value = raw_pair.split("=", 1)
    if _is_sensitive_key(key):
        return f"{key}=<redacted>"
    return f"{key}={value}"


def _sanitize_labels(labels: dict[str, str] | None) -> dict[str, str]:
    if not labels:
        return {}
    return {key: "<redacted>" if _is_sensitive_key(key) else value for key, value in labels.items()}


def _format_mount(mount: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": mount.get("Type"),
        "name": mount.get("Name"),
        "source": mount.get("Source"),
        "destination": mount.get("Destination"),
        "mode": mount.get("Mode"),
        "rw": mount.get("RW"),
    }


class DockerService:
    def __init__(self):
        self._client = None
        self._initialized = False
        self._default_compose_config = None
        self._backup_default_config()

    def _backup_default_config(self):
        try:
            cfg = get_compose_config()
            import copy

            self._default_compose_config = copy.deepcopy(cfg)
            logger.info("Successfully backed up default docker-compose.yml config.")
        except Exception as e:
            logger.error(f"Failed to backup default compose config: {e}")

    def _get_client(self) -> docker.DockerClient:
        """Lazily initialize Docker client to prevent startup failures when Docker is down."""
        if not self._initialized:
            try:
                self._client = docker.from_env()
                self._initialized = True
            except DockerException as e:
                logger.error(f"Failed to connect to Docker daemon: {e}")
                raise RuntimeError("Docker daemon is not accessible.") from e
        return self._client

    def get_containers_status(self) -> list[dict[str, Any]]:
        """Fetch the statuses of all managed containers."""
        status_list = []
        compose_cfg = get_compose_config()
        services = compose_cfg.get("services", {})

        # 순환 참조 방지를 위해 로컬 임포트 수행
        from kor_travel_docker_manager.services.metrics_collector import metrics_collector

        try:
            client = self._get_client()
        except RuntimeError:
            for key, spec in MANAGED_CONTAINERS.items():
                svc_name = spec["compose_service"]
                svc_config = services.get(svc_name, {})
                status_list.append(
                    {
                        "id": key,
                        "name": spec["name"],
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "status": "offline",
                        "state": "Docker daemon unavailable",
                        "ports": [],
                        "metrics": {
                            "cpu_pct": 0.0,
                            "mem_pct": 0.0,
                            "mem_usage": 0,
                            "mem_limit": 0,
                            "io_read": 0,
                            "io_write": 0,
                        },
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                        },
                    }
                )
            return status_list

        for key, spec in MANAGED_CONTAINERS.items():
            cname = spec["name"]
            svc_name = spec["compose_service"]
            svc_config = services.get(svc_name, {})
            metric = metrics_collector.get_latest_metric(key)

            try:
                container = client.containers.get(cname)
                # Parse exposed ports
                ports = []
                port_bindings = container.attrs.get("HostConfig", {}).get("PortBindings", {})
                for container_port, host_ports in port_bindings.items():
                    if host_ports:
                        ports.append(
                            f"{host_ports[0].get('HostPort')}:{container_port.split('/')[0]}"
                        )

                image_tags = container.image.tags
                status_list.append(
                    {
                        "id": key,
                        "name": cname,
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "image": image_tags[0] if image_tags else container.image.short_id,
                        "status": container.status,  # e.g., 'running', 'exited', 'paused'
                        "state": container.attrs.get("State", {}).get("Status", "unknown"),
                        "ports": ports,
                        "metrics": metric,
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                        },
                    }
                )
            except NotFound:
                status_list.append(
                    {
                        "id": key,
                        "name": cname,
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "status": "not_created",
                        "state": "Container not found",
                        "ports": [],
                        "metrics": {
                            "cpu_pct": 0.0,
                            "mem_pct": 0.0,
                            "mem_usage": 0,
                            "mem_limit": 0,
                            "io_read": 0,
                            "io_write": 0,
                        },
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                        },
                    }
                )
            except Exception as e:
                logger.error(f"Error querying container {cname}: {e}")
                status_list.append(
                    {
                        "id": key,
                        "name": cname,
                        "display_name": spec["display_name"],
                        "role": spec["role"],
                        "connection": spec["connection"],
                        "public_url": _public_url(spec),
                        "expected_ports": spec["expected_ports"],
                        "status": "error",
                        "state": str(e),
                        "ports": [],
                        "metrics": {
                            "cpu_pct": 0.0,
                            "mem_pct": 0.0,
                            "mem_usage": 0,
                            "mem_limit": 0,
                            "io_read": 0,
                            "io_write": 0,
                        },
                        "config": {
                            "ports": svc_config.get("ports", []),
                            "env": svc_config.get("environment", {}),
                            "volumes": svc_config.get("volumes", []),
                            "networks": svc_config.get("networks", []),
                        },
                    }
                )
        return status_list

    def control_container(self, container_id: str, action: str) -> dict[str, Any]:
        """Perform start/stop/restart action on a container."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}
        if action not in {"start", "stop", "restart"}:
            return {"success": False, "error": f"Invalid action: {action}"}
        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            assert_manager_mutation_allowed(
                environment=environment_snapshot.effective
            )
            assert_c6c_mutation_allowed(
                [container_id],
                environment=environment_snapshot.effective,
            )
            return self._control_container_unlocked(
                container_id,
                action,
                environment_snapshot=environment_snapshot,
            )

    def _control_container_unlocked(
        self,
        container_id: str,
        action: str,
        *,
        environment_snapshot: ComposeEnvironmentSnapshot,
    ) -> dict[str, Any]:
        """검증과 host lock을 이미 확보한 container SDK 변경 구현."""

        cname = MANAGED_CONTAINERS[container_id]["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)

            if action == "start":
                container.start()
            elif action == "stop":
                container.stop()
            elif action == "restart":
                container.restart()
            return {"success": True, "message": f"Successfully performed '{action}' on {cname}."}
        except NotFound:
            if action == "start":
                logger.info(
                    f"Container {cname} not found. Attempting to create and start it from docker-compose.yml settings."
                )
                try:
                    compose_cfg = get_compose_config(
                        environment_snapshot.compose_path
                    )
                    services = compose_cfg.get("services", {})
                    svc_name = MANAGED_CONTAINERS[container_id]["compose_service"]
                    svc_config = services.get(svc_name, {})

                    ports = svc_config.get("ports", [])
                    env = svc_config.get("environment", {})
                    volumes = svc_config.get("volumes", [])
                    networks = svc_config.get("networks", [])

                    res = self._update_container_config_unlocked(
                        container_id,
                        ports,
                        env,
                        volumes,
                        networks,
                        environment_snapshot=environment_snapshot,
                    )
                    if res.get("success"):
                        return {
                            "success": True,
                            "message": f"Container {cname} was not found, so it was created and started from compose configuration.",
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"Container {cname} not found, and failed to create: {res.get('error')}",
                            "command": res.get("command"),
                            "returncode": res.get("returncode"),
                            "stdout": res.get("stdout"),
                            "stderr": res.get("stderr"),
                            "restoration": res.get("restoration"),
                        }
                except (
                    ComposePostMutationContractError,
                    ComposeCandidateContractError,
                ):
                    raise
                except Exception as create_err:
                    return {
                        "success": False,
                        "error": f"Container {cname} not found, and failed during creation process: {str(create_err)}",
                    }
            else:
                return {
                    "success": False,
                    "error": f"Container {cname} not found. Please start it first to create it.",
                }
        except (
            ComposePostMutationContractError,
            ComposeCandidateContractError,
        ):
            raise
        except Exception as e:
            logger.error(f"Failed to {action} container {cname}: {e}")
            return {"success": False, "error": str(e)}

    def get_container_logs(self, container_id: str, tail: int = 100) -> dict[str, Any]:
        """Retrieve the recent stdout/stderr logs of a container."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

        cname = MANAGED_CONTAINERS[container_id]["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)
            logs = container.logs(tail=tail, stdout=True, stderr=True).decode(
                "utf-8", errors="ignore"
            )
            return {"success": True, "logs": logs}
        except NotFound:
            return {"success": False, "error": f"Container {cname} not found."}
        except Exception as e:
            logger.error(f"Failed to fetch logs for {cname}: {e}")
            return {"success": False, "error": str(e)}

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        """Return a safe, UI-oriented subset of Docker inspect data."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

        spec = MANAGED_CONTAINERS[container_id]
        cname = spec["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)
            attrs = container.attrs
            config = attrs.get("Config", {})
            host_config = attrs.get("HostConfig", {})
            network_settings = attrs.get("NetworkSettings", {})
            state = attrs.get("State", {})

            env = [_redact_env_pair(pair) for pair in config.get("Env", [])]
            networks = {
                name: {
                    "network_id": details.get("NetworkID"),
                    "ip_address": details.get("IPAddress"),
                    "gateway": details.get("Gateway"),
                    "mac_address": details.get("MacAddress"),
                    "aliases": details.get("Aliases") or [],
                }
                for name, details in (network_settings.get("Networks") or {}).items()
            }

            return {
                "success": True,
                "container": {
                    "id": container_id,
                    "docker_id": attrs.get("Id"),
                    "name": cname,
                    "display_name": spec["display_name"],
                    "role": spec["role"],
                    "image": config.get("Image"),
                    "created": attrs.get("Created"),
                    "status": container.status,
                    "state": {
                        "status": state.get("Status"),
                        "running": state.get("Running"),
                        "paused": state.get("Paused"),
                        "restarting": state.get("Restarting"),
                        "oom_killed": state.get("OOMKilled"),
                        "dead": state.get("Dead"),
                        "exit_code": state.get("ExitCode"),
                        "error": state.get("Error"),
                        "started_at": state.get("StartedAt"),
                        "finished_at": state.get("FinishedAt"),
                        "health": state.get("Health", {}),
                    },
                    "config": {
                        "hostname": config.get("Hostname"),
                        "env": env,
                        "cmd": config.get("Cmd"),
                        "entrypoint": config.get("Entrypoint"),
                        "labels": _sanitize_labels(config.get("Labels")),
                        "working_dir": config.get("WorkingDir"),
                    },
                    "host_config": {
                        "restart_policy": host_config.get("RestartPolicy"),
                        "network_mode": host_config.get("NetworkMode"),
                        "port_bindings": host_config.get("PortBindings"),
                        "binds": host_config.get("Binds") or [],
                    },
                    "mounts": [_format_mount(mount) for mount in attrs.get("Mounts", [])],
                    "network": {
                        "ports": network_settings.get("Ports") or {},
                        "networks": networks,
                    },
                },
            }
        except NotFound:
            return {"success": False, "error": f"Container {cname} not found."}
        except Exception as e:
            logger.error(f"Failed to inspect container {cname}: {e}")
            return {"success": False, "error": str(e)}

    def update_container_config(
        self,
        container_id: str,
        new_ports: list[str],
        new_env: dict[str, str],
        new_volumes: list[Any],
        new_networks: list[str],
    ) -> dict[str, Any]:
        """Update docker-compose.yml configuration and recreate the service through Compose."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}
        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            assert_manager_mutation_allowed(
                environment=environment_snapshot.effective
            )
            assert_c6c_mutation_allowed(
                [container_id],
                environment=environment_snapshot.effective,
            )
            return self._update_container_config_unlocked(
                container_id,
                new_ports,
                new_env,
                new_volumes,
                new_networks,
                environment_snapshot=environment_snapshot,
            )

    def _update_container_config_unlocked(
        self,
        container_id: str,
        new_ports: list[str],
        new_env: dict[str, str],
        new_volumes: list[Any],
        new_networks: list[str],
        *,
        replacement_service_config: dict[str, Any] | None = None,
        environment_snapshot: ComposeEnvironmentSnapshot,
    ) -> dict[str, Any]:
        """검증과 host lock을 이미 확보한 config transaction 구현."""

        spec = MANAGED_CONTAINERS[container_id]
        cname = spec["name"]
        svc_name = spec["compose_service"]

        original_bytes: bytes | None = None
        original_mode: int | None = None
        write_attempted = False
        mutation_succeeded = False
        baseline_transaction: ComposeTransactionSnapshot | None = None
        validation = None
        try:
            compose_path = Path(environment_snapshot.compose_path)
            baseline_transaction, baseline_validation = (
                compose_service._capture_transaction_unlocked(
                    environment_snapshot=environment_snapshot,
                )
            )
            original_bytes = baseline_transaction.compose_source_bytes
            original_mode = baseline_transaction.compose_source_mode
            # 1. Load current docker-compose.yml
            loaded = yaml.safe_load(original_bytes.decode("utf-8")) or {}
            if not isinstance(loaded, dict) or not loaded:
                return {"success": False, "error": "Failed to read docker-compose.yml."}
            compose_cfg = deepcopy(loaded)
            baseline_volume_hash = compose_volume_graph_hash(compose_cfg)

            if "services" not in compose_cfg:
                compose_cfg["services"] = {}
            if svc_name not in compose_cfg["services"]:
                compose_cfg["services"][svc_name] = {}

            if replacement_service_config is not None:
                compose_cfg["services"][svc_name] = deepcopy(
                    replacement_service_config
                )
            else:
                svc_config = compose_cfg["services"][svc_name]

                # 2. Update service settings inside dict
                svc_config["ports"] = new_ports
                svc_config["environment"] = new_env
                svc_config["volumes"] = new_volumes
                if new_networks:
                    svc_config["networks"] = new_networks
                    svc_config.pop("network_mode", None)
                else:
                    svc_config.pop("networks", None)

            # 3. Candidate 전체를 검증한 뒤 docker-compose.yml을 저장한다.
            if compose_volume_graph_hash(compose_cfg) != baseline_volume_hash:
                raise ComposeCandidateContractError(
                    "compose candidate volume configuration is immutable through the Manager API"
                )
            validation = compose_service._capture_candidate_transaction_unlocked(
                compose_cfg,
                baseline_transaction=baseline_transaction,
                baseline_validation=baseline_validation,
            )
            candidate_transaction = validation.transaction_snapshot
            if candidate_transaction is None:
                raise ComposeCandidateContractError(
                    "compose candidate transaction was not captured"
                )
            if compose_path.read_bytes() != original_bytes:
                raise ComposeCandidateContractError(
                    "compose candidate source changed during the config request"
                )
            revalidate_candidate_system_bind_snapshots(
                validation.system_bind_snapshots
            )
            write_attempted = True
            _atomic_write(
                str(compose_path),
                candidate_transaction.compose_source_bytes,
                mode=candidate_transaction.compose_source_mode,
            )
            logger.info(f"Updated docker-compose.yml for service {svc_name}.")

            recreate_result = compose_service.run(
                ["up", "-d", "--force-recreate", svc_name],
                capture_output=True,
                mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                expected_system_bind_snapshots=validation.system_bind_snapshots,
                expected_raw_volume_graph_hash=validation.raw_volume_graph_hash,
                expected_resolved_volume_graph_hash=(
                    validation.resolved_volume_graph_hash
                ),
                expected_environment_snapshot=validation.environment_snapshot,
                expected_external_input_snapshot=(
                    validation.external_input_snapshot
                ),
                transaction=candidate_transaction,
            )
            if not recreate_result.get("success"):
                restoration = self._restore_compose_transaction(
                    original_bytes,
                    original_mode,
                    svc_name,
                    baseline_transaction,
                )
                return {
                    "success": False,
                    "error": (
                        "docker compose recreate failed: "
                        f"{recreate_result.get('stderr') or recreate_result.get('stdout')}"
                    ),
                    "command": recreate_result.get("command"),
                    "returncode": recreate_result.get("returncode"),
                    "stdout": recreate_result.get("stdout"),
                    "stderr": recreate_result.get("stderr"),
                    "restoration": restoration,
                }
            mutation_succeeded = True

            # RustFS 재생성 후에는 compose에 정의된 init service를 그대로 실행해 bucket을 보정한다.
            if container_id == "rustfs":
                init_result = compose_service.run(
                    ["run", "--rm", "rustfs-init"],
                    capture_output=True,
                    mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                    expected_system_bind_snapshots=validation.system_bind_snapshots,
                    expected_raw_volume_graph_hash=(
                        validation.raw_volume_graph_hash
                    ),
                    expected_resolved_volume_graph_hash=(
                        validation.resolved_volume_graph_hash
                    ),
                    expected_environment_snapshot=(
                        validation.environment_snapshot
                    ),
                    expected_external_input_snapshot=(
                        validation.external_input_snapshot
                    ),
                    transaction=candidate_transaction,
                )
                if not init_result.get("success"):
                    restoration = self._restore_compose_transaction(
                        original_bytes,
                        original_mode,
                        svc_name,
                        baseline_transaction,
                    )
                    return {
                        "success": False,
                        "error": (
                            "rustfs bucket initialization failed: "
                            f"{init_result.get('stderr') or init_result.get('stdout')}"
                        ),
                        "command": init_result.get("command"),
                        "returncode": init_result.get("returncode"),
                        "stdout": init_result.get("stdout"),
                        "stderr": init_result.get("stderr"),
                        "restoration": restoration,
                    }

            return {
                "success": True,
                "message": f"Successfully updated config and recreated {cname}.",
            }
        except ComposeCandidateContractError as exc:
            restore_required = write_attempted
            if original_bytes is not None and original_mode is not None:
                try:
                    compose_path = Path(environment_snapshot.compose_path)
                    restore_required = restore_required or (
                        compose_path.read_bytes() != original_bytes
                        or compose_path.stat().st_mode & 0o777 != original_mode
                    )
                except OSError:
                    restore_required = True
            if restore_required and original_bytes is not None and original_mode is not None:
                if mutation_succeeded:
                    try:
                        restoration = self._restore_compose_transaction(
                            original_bytes,
                            original_mode,
                            svc_name,
                            baseline_transaction,
                        )
                        recovery_succeeded = bool(
                            restoration.get("config_restored")
                            and restoration.get("runtime_restored")
                        )
                        recovery_error = restoration.get("error")
                    except Exception as recovery_exc:
                        restoration = {
                            "config_restored": False,
                            "runtime_restored": False,
                            "error": str(recovery_exc),
                        }
                        recovery_succeeded = False
                        recovery_error = str(recovery_exc)
                    raise ComposePostMutationContractError(
                        exc,
                        recovery_attempted=True,
                        recovery_succeeded=recovery_succeeded,
                        recovery_error=(
                            None
                            if recovery_succeeded
                            else str(recovery_error or "recovery failed")
                        ),
                        restoration=restoration,
                    ) from exc
                try:
                    _atomic_write(
                        environment_snapshot.compose_path,
                        original_bytes,
                        mode=original_mode,
                    )
                except Exception as recovery_exc:
                    restoration = {
                        "config_restored": False,
                        "runtime_restored": False,
                        "runtime_recovery_attempted": False,
                        "durable_config_mutation": True,
                        "error": str(recovery_exc),
                    }
                    raise ComposePostMutationContractError(
                        exc,
                        recovery_attempted=True,
                        recovery_succeeded=False,
                        recovery_error=str(recovery_exc),
                        restoration=restoration,
                    ) from exc
            raise
        except Exception as e:
            logger.error(f"Failed to update config for {cname}: {e}")
            restoration = None
            if write_attempted and original_bytes is not None and original_mode is not None:
                restoration = self._restore_compose_transaction(
                    original_bytes,
                    original_mode,
                    svc_name,
                    baseline_transaction,
                )
            return {"success": False, "error": str(e), "restoration": restoration}

    @staticmethod
    def _restore_compose_transaction(
        original_bytes: bytes,
        original_mode: int,
        svc_name: str,
        transaction: ComposeTransactionSnapshot | None = None,
    ) -> dict[str, Any]:
        try:
            compose_path = (
                transaction.environment.compose_path
                if transaction is not None
                else _get_compose_path()
            )
            _atomic_write(compose_path, original_bytes, mode=original_mode)
        except Exception as exc:
            logger.error("Failed to restore compose config for %s: %s", svc_name, exc)
            return {
                "config_restored": False,
                "runtime_restored": False,
                "error": str(exc),
            }
        try:
            if transaction is None:
                raise ComposeCandidateContractError(
                    "compose restoration has no baseline transaction"
                )
            recreate_result = compose_service._run_frozen_recovery(
                ["up", "-d", "--force-recreate", svc_name],
                capture_output=True,
                mutation_capability=_MANAGED_COMPOSE_MUTATION_CAPABILITY,
                transaction=transaction,
            )
            return {
                "config_restored": True,
                "runtime_restored": bool(recreate_result.get("success")),
                "command": recreate_result.get("command"),
                "returncode": recreate_result.get("returncode"),
                "stdout": recreate_result.get("stdout"),
                "stderr": recreate_result.get("stderr"),
                "error": (
                    None
                    if recreate_result.get("success")
                    else str(
                        recreate_result.get("stderr")
                        or recreate_result.get("stdout")
                        or "docker compose runtime restoration failed"
                    )
                ),
            }
        except Exception as exc:
            logger.error("Failed to restore compose runtime for %s: %s", svc_name, exc)
            return {
                "config_restored": True,
                "runtime_restored": False,
                "error": str(exc),
            }

    def reset_container_config(self, container_id: str) -> dict[str, Any]:
        """Reset container configuration in docker-compose.yml to default and recreate it."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}
        with c6c_deployment_lock(get_c6c_deployment_lock_path()):
            environment_snapshot = _capture_compose_environment_snapshot(
                environment_override=None
            )
            assert_manager_mutation_allowed(
                environment=environment_snapshot.effective
            )
            assert_c6c_mutation_allowed(
                [container_id],
                environment=environment_snapshot.effective,
            )
            return self._reset_container_config_unlocked(
                container_id,
                environment_snapshot=environment_snapshot,
            )

    def _reset_container_config_unlocked(
        self,
        container_id: str,
        *,
        environment_snapshot: ComposeEnvironmentSnapshot,
    ) -> dict[str, Any]:
        """기본값 계산부터 재생성까지 한 config transaction으로 수행한다."""

        if not self._default_compose_config:
            return {"success": False, "error": "No default config backup available."}

        spec = MANAGED_CONTAINERS[container_id]
        svc_name = spec["compose_service"]

        default_services = self._default_compose_config.get("services", {})
        if svc_name not in default_services:
            return {
                "success": False,
                "error": f"Service {svc_name} not found in default config backup.",
            }

        default_svc_config = deepcopy(default_services[svc_name])

        # Recreate container with default settings
        ports = default_svc_config.get("ports", [])
        env = default_svc_config.get("environment", {})
        volumes = default_svc_config.get("volumes", [])
        networks = default_svc_config.get("networks", [])

        return self._update_container_config_unlocked(
            container_id,
            ports,
            env,
            volumes,
            networks,
            replacement_service_config=default_svc_config,
            environment_snapshot=environment_snapshot,
        )


docker_service = DockerService()
