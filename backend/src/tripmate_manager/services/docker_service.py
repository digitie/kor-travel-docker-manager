import logging
import os
from typing import Any

import docker
import yaml
from docker.errors import DockerException, NotFound
from dotenv import load_dotenv

from tripmate_manager.services.compose_service import get_compose_path, get_env_path
from tripmate_manager.services.registry import MANAGED_CONTAINERS

logger = logging.getLogger(__name__)

import re


def _substitute_env_vars(val: Any) -> Any:
    if not isinstance(val, str):
        return val
    # Perform standard os.path.expandvars
    expanded = os.path.expandvars(val)
    # Match ${VAR:-default}
    pattern = r"\$\{([^:]+):-([^}]+)\}"

    def repl(match):
        env_key = match.group(1)
        default_val = match.group(2)
        return os.environ.get(env_key, default_val)

    return re.sub(pattern, repl, expanded)


def _get_compose_path() -> str:
    return get_compose_path()


def _get_env_path() -> str:
    return get_env_path()


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


def parse_compose_ports(ports_list: list[Any]) -> dict[str, int]:
    bindings = {}
    for port in ports_list:
        if isinstance(port, str):
            parts = port.split(":")
            if len(parts) == 2:
                host_port, container_port = parts
                bindings[f"{container_port}/tcp"] = int(host_port)
            elif len(parts) == 1:
                bindings[f"{port}/tcp"] = int(port)
        elif isinstance(port, dict):
            target = port.get("target")
            published = port.get("published")
            if target and published:
                bindings[f"{target}/tcp"] = int(published)
    return bindings


def parse_compose_volumes(volumes_list: list[Any]) -> dict[str, dict[str, str]]:
    binds = {}
    compose_path = _get_compose_path()
    project_root = os.path.dirname(compose_path) if compose_path else ""

    for vol in volumes_list:
        if isinstance(vol, str):
            parts = vol.split(":")
            if len(parts) >= 2:
                host_path = parts[0]
                container_path = parts[1]
                mode = parts[2] if len(parts) > 2 else "rw"

                # Resolve relative path to absolute path relative to project root
                if project_root and (
                    host_path.startswith("./")
                    or host_path.startswith("../")
                    or not os.path.isabs(host_path)
                ):
                    # If it's a named volume (no slashes and doesn't start with dot), leave it as is
                    if not (host_path.startswith(".") or "/" in host_path or "\\" in host_path):
                        pass
                    else:
                        host_path = os.path.abspath(os.path.join(project_root, host_path))

                binds[host_path] = {"bind": container_path, "mode": mode}
        elif isinstance(vol, dict):
            source = vol.get("source")
            target = vol.get("target")
            read_only = vol.get("read_only", False)
            if source and target:
                host_path = source
                if project_root and (
                    host_path.startswith("./")
                    or host_path.startswith("../")
                    or not os.path.isabs(host_path)
                ):
                    if not (host_path.startswith(".") or "/" in host_path or "\\" in host_path):
                        pass
                    else:
                        host_path = os.path.abspath(os.path.join(project_root, host_path))
                binds[host_path] = {"bind": target, "mode": "ro" if read_only else "rw"}
    return binds


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
        from tripmate_manager.services.metrics_collector import metrics_collector

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
        """Update docker-compose.yml configuration and recreate container with updated settings using Docker SDK."""
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
            svc_config["networks"] = new_networks

            # 3. Save docker-compose.yml back to disk
            save_compose_config(compose_cfg)
            logger.info(f"Updated docker-compose.yml for service {svc_name}.")

            # Load latest env for variable substitution
            env_path = _get_env_path()
            if os.path.exists(env_path):
                load_dotenv(env_path, override=True)

            # 4. Stop and Remove existing container
            client = self._get_client()
            image_name = svc_config.get(
                "image",
                "postgis/postgis:16-3.5-alpine"
                if spec["role"] == "postgresql"
                else "rustfs/rustfs:latest",
            )

            # Default options in case container attributes retrieval fails
            binds_list = new_volumes
            network_mode = "bridge"
            if len(new_networks) > 0:
                # Resolve compose network prefix (e.g. tripmate-manager_default)
                network_mode = f"tripmate-manager_{new_networks[0]}"
            restart_policy = {"Name": "unless-stopped"}
            command = svc_config.get("command", None)
            shm_size = svc_config.get("shm_size", None)

            try:
                container = client.containers.get(cname)
                image_name = (
                    container.image.tags[0] if container.image.tags else container.image.short_id
                )
                restart_policy = container.attrs.get("HostConfig", {}).get(
                    "RestartPolicy", {"Name": "unless-stopped"}
                )

                logger.info(f"Stopping conflicting container {cname}...")
                container.stop(timeout=5)
                logger.info(f"Removing conflicting container {cname}...")
                container.remove()
            except NotFound:
                logger.info(f"Container {cname} not found, proceeding to create new one.")

            # 5. Parse environment variables (substitute ${VAR})
            parsed_env = {}
            for k, v in new_env.items():
                parsed_env[k] = _substitute_env_vars(v)

            # 6. Parse ports mapping (substitute env vars first)
            resolved_ports = [_substitute_env_vars(p) for p in new_ports]
            port_bindings = parse_compose_ports(resolved_ports)

            # 7. Parse volumes mapping (substitute env vars first)
            resolved_volumes = [_substitute_env_vars(vol) for vol in binds_list]
            volumes_dict = parse_compose_volumes(resolved_volumes)

            logger.info(
                f"Recreating container {cname} (image: {image_name}, ports: {port_bindings})..."
            )

            # 8. Create new container
            client.containers.run(
                image=image_name,
                name=cname,
                detach=True,
                environment=parsed_env,
                ports=port_bindings,
                volumes=volumes_dict,
                network_mode=network_mode,
                restart_policy=restart_policy,
                command=command,
                shm_size=shm_size,
            )

            # Special Case: RustFS 버킷 초기화가 필요한 경우 rustfs-init을 한번 실행
            if container_id == "rustfs":
                try:
                    rustfs_internal_port = parsed_env.get("RUSTFS_API_CONTAINER_PORT", "9000")
                    init_command = (
                        f"mc alias set local http://rustfs:{rustfs_internal_port} "
                        f"{parsed_env.get('RUSTFS_ACCESS_KEY', 'rustfsadmin')} {parsed_env.get('RUSTFS_SECRET_KEY', 'rustfsadmin')}; "
                        f"mc mb -p local/tripmate-media || true; "
                        f"mc mb -p local/kraddr-geo || true; "
                        f"mc mb -p local/krtour-map || true; "
                        f"mc mb -p local/krtour-uploads || true;"
                    )
                    logger.info("Initializing RustFS buckets...")
                    client.containers.run(
                        image="minio/mc:latest",
                        command=f'/bin/sh -c "{init_command}"',
                        network_mode=network_mode,
                        remove=True,
                    )
                except Exception as e:
                    logger.error(f"Failed to auto-initialize rustfs buckets: {e}")

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
