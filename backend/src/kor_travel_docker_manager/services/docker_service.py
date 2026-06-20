import logging
import os
from typing import Any

import docker
import yaml
from docker.errors import DockerException, NotFound

from kor_travel_docker_manager.services.compose_service import compose_service, get_compose_path
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


def get_compose_config() -> dict[str, Any]:
    path = _get_compose_path()
    if not os.path.exists(path):
        logger.error(f"docker-compose.yml not found at {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Error reading docker-compose.yml: {e}")
        return {}


def save_compose_config(config: dict[str, Any]):
    path = _get_compose_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        logger.error(f"Error writing docker-compose.yml: {e}")
        raise


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
            else:
                return {"success": False, "error": f"Invalid action: {action}"}

            return {"success": True, "message": f"Successfully performed '{action}' on {cname}."}
        except NotFound:
            if action == "start":
                logger.info(
                    f"Container {cname} not found. Attempting to create and start it from docker-compose.yml settings."
                )
                try:
                    compose_cfg = get_compose_config()
                    services = compose_cfg.get("services", {})
                    svc_name = MANAGED_CONTAINERS[container_id]["compose_service"]
                    svc_config = services.get(svc_name, {})

                    ports = svc_config.get("ports", [])
                    env = svc_config.get("environment", {})
                    volumes = svc_config.get("volumes", [])
                    networks = svc_config.get("networks", [])

                    res = self.update_container_config(container_id, ports, env, volumes, networks)
                    if res.get("success"):
                        return {
                            "success": True,
                            "message": f"Container {cname} was not found, so it was created and started from compose configuration.",
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"Container {cname} not found, and failed to create: {res.get('error')}",
                        }
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
        new_volumes: list[str],
        new_networks: list[str],
    ) -> dict[str, Any]:
        """Update docker-compose.yml configuration and recreate the service through Compose."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

        spec = MANAGED_CONTAINERS[container_id]
        cname = spec["name"]
        svc_name = spec["compose_service"]

        try:
            # 1. Load current docker-compose.yml
            compose_cfg = get_compose_config()
            if not compose_cfg:
                return {"success": False, "error": "Failed to read docker-compose.yml."}

            if "services" not in compose_cfg:
                compose_cfg["services"] = {}
            if svc_name not in compose_cfg["services"]:
                compose_cfg["services"][svc_name] = {}

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

            # 3. Save docker-compose.yml back to disk
            save_compose_config(compose_cfg)
            logger.info(f"Updated docker-compose.yml for service {svc_name}.")

            recreate_result = compose_service.run(
                ["up", "-d", "--force-recreate", svc_name],
                capture_output=True,
            )
            if not recreate_result.get("success"):
                return {
                    "success": False,
                    "error": (
                        "docker compose recreate failed: "
                        f"{recreate_result.get('stderr') or recreate_result.get('stdout')}"
                    ),
                    "command": recreate_result.get("command"),
                }

            # RustFS 재생성 후에는 compose에 정의된 init service를 그대로 실행해 bucket을 보정한다.
            if container_id == "rustfs":
                init_result = compose_service.run(
                    ["run", "--rm", "rustfs-init"],
                    capture_output=True,
                )
                if not init_result.get("success"):
                    return {
                        "success": False,
                        "error": (
                            "rustfs bucket initialization failed: "
                            f"{init_result.get('stderr') or init_result.get('stdout')}"
                        ),
                        "command": init_result.get("command"),
                    }

            return {
                "success": True,
                "message": f"Successfully updated config and recreated {cname}.",
            }
        except Exception as e:
            logger.error(f"Failed to update config for {cname}: {e}")
            return {"success": False, "error": str(e)}

    def reset_container_config(self, container_id: str) -> dict[str, Any]:
        """Reset container configuration in docker-compose.yml to default and recreate it."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

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

        import copy

        default_svc_config = copy.deepcopy(default_services[svc_name])

        # Reload current compose config
        current_cfg = get_compose_config()
        if not current_cfg:
            current_cfg = {"services": {}}

        # Revert config
        current_cfg["services"][svc_name] = default_svc_config
        save_compose_config(current_cfg)
        logger.info(f"Reverted docker-compose.yml config for service {svc_name} to default.")

        # Recreate container with default settings
        ports = default_svc_config.get("ports", [])
        env = default_svc_config.get("environment", {})
        volumes = default_svc_config.get("volumes", [])
        networks = default_svc_config.get("networks", [])

        return self.update_container_config(container_id, ports, env, volumes, networks)


docker_service = DockerService()
