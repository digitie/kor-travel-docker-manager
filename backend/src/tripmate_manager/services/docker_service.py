import logging
from typing import Any, Dict, List

import docker
from docker.errors import DockerException, NotFound

logger = logging.getLogger(__name__)

MANAGED_CONTAINERS = {
    "tripmate-postgresql": {
        "name": "tripmate-postgres",
        "role": "postgresql",
        "display_name": "TripMate PostgreSQL / PostGIS",
        "connection": "postgresql://tripmate:***@localhost:55432/tripmate",
        "expected_ports": ["55432:5432"],
    },
    "kraddr-geo-postgresql": {
        "name": "kraddr-geo-postgres",
        "role": "postgresql",
        "display_name": "python-kraddr-geo PostgreSQL / PostGIS",
        "connection": "postgresql+psycopg://addr:***@localhost:15434/kraddr_geo",
        "expected_ports": ["15434:5432"],
    },
    "rustfs": {
        "name": "tripmate-rustfs",
        "role": "rustfs",
        "display_name": "RustFS 공용 오브젝트 스토리지",
        "connection": "http://127.0.0.1:9003",
        "expected_ports": ["9003:9003", "9004:9004"],
    },
}


class DockerService:
    def __init__(self):
        self._client = None
        self._initialized = False

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

    def get_containers_status(self) -> List[Dict[str, Any]]:
        """Fetch the statuses of all managed containers."""
        status_list = []
        try:
            client = self._get_client()
        except RuntimeError:
            # If Docker daemon is inaccessible, return all managed containers as 'unknown' or 'offline'
            for key, spec in MANAGED_CONTAINERS.items():
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
                    }
                )
            return status_list

        for key, spec in MANAGED_CONTAINERS.items():
            cname = spec["name"]
            try:
                container = client.containers.get(cname)
                # Parse exposed ports
                ports = []
                port_bindings = container.attrs.get("HostConfig", {}).get("PortBindings", {})
                for container_port, host_ports in port_bindings.items():
                    if host_ports:
                        ports.append(f"{host_ports[0].get('HostPort')}:{container_port.split('/')[0]}")

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
                    }
                )
        return status_list

    def control_container(self, container_id: str, action: str) -> Dict[str, Any]:
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
            return {"success": False, "error": f"Container {cname} not found. Please run docker-compose up."}
        except Exception as e:
            logger.error(f"Failed to {action} container {cname}: {e}")
            return {"success": False, "error": str(e)}

    def get_container_logs(self, container_id: str, tail: int = 100) -> Dict[str, Any]:
        """Retrieve the recent stdout/stderr logs of a container."""
        if container_id not in MANAGED_CONTAINERS:
            return {"success": False, "error": f"Container {container_id} is not managed."}

        cname = MANAGED_CONTAINERS[container_id]["name"]
        try:
            client = self._get_client()
            container = client.containers.get(cname)
            logs = container.logs(tail=tail, stdout=True, stderr=True).decode("utf-8", errors="ignore")
            return {"success": True, "logs": logs}
        except NotFound:
            return {"success": False, "error": f"Container {cname} not found."}
        except Exception as e:
            logger.error(f"Failed to fetch logs for {cname}: {e}")
            return {"success": False, "error": str(e)}

docker_service = DockerService()
