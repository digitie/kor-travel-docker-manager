import asyncio
import datetime
import logging
import math
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from docker.errors import NotFound

from kor_travel_docker_manager.services.docker_service import MANAGED_CONTAINERS, docker_service
from kor_travel_docker_manager.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_STATE_VALUES = (
    "created",
    "running",
    "paused",
    "restarting",
    "removing",
    "exited",
    "dead",
    "not_created",
    "offline",
    "error",
    "unknown",
)
_HEALTH_VALUES = ("healthy", "unhealthy", "starting", "no_check", "unknown")


def _empty_metric() -> dict[str, Any]:
    """화면과 Prometheus가 공유하는 최신 컨테이너 메트릭의 빈 값."""
    return {
        "timestamp": "",
        "cpu_pct": 0.0,
        "mem_pct": 0.0,
        "mem_usage": 0,
        "mem_limit": 0,
        "io_read": 0,
        "io_write": 0,
        "io_read_total": 0,
        "io_write_total": 0,
        "network_rx_bytes": 0,
        "network_tx_bytes": 0,
        "network_rx_packets": 0,
        "network_tx_packets": 0,
        "network_rx_errors": 0,
        "network_tx_errors": 0,
        "pids_current": None,
        "pids_limit": None,
        "stats_available": False,
    }


def _as_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _normalize_state(value: Any) -> str:
    state = str(value or "unknown").strip().lower().replace(" ", "_")
    return state if state in _STATE_VALUES else "unknown"


def _health_status(health: Any) -> str:
    if isinstance(health, str):
        status = health.strip().lower()
        return status if status in _HEALTH_VALUES else "unknown"
    if not isinstance(health, Mapping):
        return "unknown" if health is not None else "no_check"
    status = str(health.get("Status") or "").strip().lower()
    if not status:
        return "no_check"
    return status if status in _HEALTH_VALUES else "unknown"


def _timestamp_seconds(value: Any) -> float:
    """Docker RFC3339 시각을 Unix timestamp로 변환한다."""
    if not value:
        return 0.0
    text = str(value).strip()
    if not text or text.startswith("0001-01-01"):
        return 0.0
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.timestamp()


def _escape_label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _prom_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    number = _as_number(value)
    if number == 0:
        return "0"
    return format(number, ".15g")


def _sample_line(name: str, labels: Mapping[str, Any], value: Any) -> str:
    label_text = ",".join(
        f'{key}="{_escape_label(labels[key])}"' for key in sorted(labels)
    )
    suffix = f"{{{label_text}}}" if label_text else ""
    return f"{name}{suffix} {_prom_value(value)}"


def _metric_block(
    name: str,
    help_text: str,
    metric_type: str,
    samples: list[tuple[Mapping[str, Any], Any]],
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {metric_type}"]
    lines.extend(_sample_line(name, labels, value) for labels, value in samples)
    return lines


def _safe_attrs(container: Any) -> dict[str, Any]:
    try:
        attrs = getattr(container, "attrs", {})
    except Exception:
        return {}
    return dict(attrs) if isinstance(attrs, Mapping) else {}


def _container_status(container: Any, attrs: Mapping[str, Any]) -> str:
    try:
        status = getattr(container, "status", None)
    except Exception:
        status = None
    state = attrs.get("State")
    if not status and isinstance(state, Mapping):
        status = state.get("Status")
    return _normalize_state(status)


def _image_metadata(container: Any, attrs: Mapping[str, Any]) -> tuple[str, str]:
    config = _as_mapping(attrs.get("Config"))
    image_id = str(attrs.get("Image") or "")
    configured_image = str(config.get("Image") or "")
    tags: list[str] = []
    try:
        image = getattr(container, "image", None)
        raw_tags = getattr(image, "tags", []) if image is not None else []
        tags = [str(tag) for tag in (raw_tags or []) if tag]
        if not image_id and image is not None:
            image_id = str(getattr(image, "short_id", "") or "")
    except Exception:
        pass
    return configured_image or (tags[0] if tags else image_id) or "unknown", image_id


def _network_stats(stats: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    totals = {
        "rx_bytes": 0,
        "tx_bytes": 0,
        "rx_packets": 0,
        "tx_packets": 0,
        "rx_errors": 0,
        "tx_errors": 0,
    }
    interfaces: dict[str, dict[str, int]] = {}
    networks = stats.get("networks")
    if not isinstance(networks, Mapping):
        return totals, interfaces
    for raw_name, raw_values in networks.items():
        if not isinstance(raw_values, Mapping):
            continue
        name = str(raw_name)[:128]
        values = {key: max(0, _as_int(raw_values.get(key))) for key in totals}
        interfaces[name] = values
        for key, value in values.items():
            totals[key] += value
    return totals, interfaces


class MetricsCollector:
    """Docker stats 수집기와 캐시 기반 Prometheus exporter."""

    def __init__(self):
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._prev_io: dict[str, tuple[int, int]] = {}
        self._latest_metrics: dict[str, dict[str, Any]] = {}
        self._container_observations: dict[str, dict[str, Any]] = {
            key: self._default_observation(key) for key in MANAGED_CONTAINERS
        }
        self._lock = threading.RLock()
        self._collection_runs_total = 0
        self._collection_errors_total = 0
        self._last_collection_timestamp = 0.0
        self._last_collection_duration = 0.0
        self._docker_daemon_up = 0

    @staticmethod
    def _default_observation(container_id: str) -> dict[str, Any]:
        spec = MANAGED_CONTAINERS.get(container_id, {})
        return {
            "container_id": container_id,
            "container_name": str(spec.get("name") or container_id),
            "display_name": str(spec.get("display_name") or container_id),
            "compose_service": str(spec.get("compose_service") or "unknown"),
            "role": str(spec.get("role") or "unknown"),
            "state": "unknown",
            "health": "unknown",
            "running": False,
            "paused": False,
            "restarting": False,
            "oom_killed": False,
            "dead": False,
            "restart_count": 0,
            "exit_code": None,
            "exit_code_available": False,
            "created_timestamp": 0.0,
            "started_timestamp": 0.0,
            "finished_timestamp": 0.0,
            "image": "unknown",
            "image_id": "",
            "docker_id": "",
            "collection_success": False,
            "collection_errors_total": 0,
            "stats_available": False,
            "last_observation_timestamp": 0.0,
            "mounts": 0,
            "networks": 0,
            "port_bindings": 0,
            "expected_ports": len(spec.get("expected_ports") or []),
            "metrics": _empty_metric(),
            "network_interfaces": {},
        }

    def _observation_from_container(
        self,
        container_id: str,
        container: Any,
        *,
        metric: dict[str, Any],
        collection_success: bool,
        stats_available: bool,
        network_interfaces: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, Any]:
        attrs = _safe_attrs(container)
        state = _as_mapping(attrs.get("State"))
        host_config = _as_mapping(attrs.get("HostConfig"))
        network_settings = _as_mapping(attrs.get("NetworkSettings"))
        image, image_id = _image_metadata(container, attrs)
        state_name = _container_status(container, attrs)
        old = self._container_observations.get(container_id, self._default_observation(container_id))
        raw_exit_code = state.get("ExitCode")
        return {
            **self._default_observation(container_id),
            "state": state_name,
            "health": _health_status(state.get("Health")),
            "running": _as_bool(state.get("Running")) or state_name == "running",
            "paused": _as_bool(state.get("Paused")),
            "restarting": _as_bool(state.get("Restarting")),
            "oom_killed": _as_bool(state.get("OOMKilled")),
            "dead": _as_bool(state.get("Dead")),
            "restart_count": max(0, _as_int(attrs.get("RestartCount"))),
            "exit_code": _as_int(raw_exit_code) if raw_exit_code is not None else None,
            "exit_code_available": raw_exit_code is not None,
            "created_timestamp": _timestamp_seconds(attrs.get("Created")),
            "started_timestamp": _timestamp_seconds(state.get("StartedAt")),
            "finished_timestamp": _timestamp_seconds(state.get("FinishedAt")),
            "image": image,
            "image_id": image_id,
            "docker_id": str(attrs.get("Id") or ""),
            "collection_success": collection_success,
            "collection_errors_total": old.get("collection_errors_total", 0),
            "stats_available": stats_available,
            "last_observation_timestamp": time.time(),
            "mounts": len(attrs.get("Mounts") or []),
            "networks": len(network_settings.get("Networks") or {}),
            "port_bindings": len(host_config.get("PortBindings") or {}),
            "metrics": metric,
            "network_interfaces": network_interfaces or {},
        }

    def _set_observation(self, container_id: str, observation: dict[str, Any]) -> None:
        with self._lock:
            self._container_observations[container_id] = observation
            self._latest_metrics[container_id] = dict(observation["metrics"])

    def _mark_unavailable(self, container_id: str, state: str, *, success: bool) -> None:
        observation = self._default_observation(container_id)
        old = self._container_observations.get(container_id, {})
        observation.update(
            {
                "state": state,
                "collection_success": success,
                "collection_errors_total": old.get("collection_errors_total", 0),
                "last_observation_timestamp": time.time(),
            }
        )
        self._prev_io.pop(container_id, None)
        self._set_observation(container_id, observation)

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._collect_loop())
            logger.info("Metrics collector background task started.")

    def stop(self):
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
            logger.info("Metrics collector background task stopped.")

    def get_latest_metric(self, container_id: str, docker_id: str | None = None) -> dict[str, Any]:
        """화면/WebSocket용 최신 캐시를 반환한다."""
        with self._lock:
            if docker_id:
                observation = self._container_observations.get(container_id)
                if observation and observation.get("docker_id") != docker_id:
                    return _empty_metric()
            return deepcopy(self._latest_metrics.get(container_id, _empty_metric()))

    def get_container_observation(self, container_id: str) -> dict[str, Any] | None:
        with self._lock:
            observation = self._container_observations.get(container_id)
            return deepcopy(observation) if observation is not None else None

    async def _collect_loop(self):
        cleanup_counter = 0
        try:
            metrics_service.cleanup_old_metrics()
        except Exception as exc:
            logger.error(f"Initial old metrics cleanup failed: {exc}")

        while self._running:
            try:
                cleanup_counter += 1
                if cleanup_counter >= 360:
                    metrics_service.cleanup_old_metrics()
                    cleanup_counter = 0
                await self.collect_metrics()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in metrics collection loop: {exc}")
            await asyncio.sleep(10)

    async def collect_metrics(self):
        started = time.monotonic()
        with self._lock:
            self._collection_runs_total += 1
        try:
            try:
                client = docker_service._get_client()
            except Exception:
                with self._lock:
                    self._collection_errors_total += 1
                    self._docker_daemon_up = 0
                for key in MANAGED_CONTAINERS:
                    self._mark_unavailable(key, "offline", success=False)
                return

            with self._lock:
                self._docker_daemon_up = 1

            for key, spec in MANAGED_CONTAINERS.items():
                cname = spec["name"]
                container = None
                try:
                    container = client.containers.get(cname)
                    attrs = _safe_attrs(container)
                    status = _container_status(container, attrs)
                    if status != "running":
                        self._set_observation(
                            key,
                            self._observation_from_container(
                                key,
                                container,
                                metric=_empty_metric(),
                                collection_success=True,
                                stats_available=False,
                            ),
                        )
                        self._prev_io.pop(key, None)
                        continue

                    stats = await asyncio.to_thread(container.stats, stream=False)
                    if not isinstance(stats, Mapping):
                        raise TypeError("Docker stats response is not a mapping")

                    cpu_stats = stats.get("cpu_stats") if isinstance(stats.get("cpu_stats"), Mapping) else {}
                    precpu_stats = (
                        stats.get("precpu_stats")
                        if isinstance(stats.get("precpu_stats"), Mapping)
                        else {}
                    )
                    cpu_usage = (
                        cpu_stats.get("cpu_usage")
                        if isinstance(cpu_stats.get("cpu_usage"), Mapping)
                        else {}
                    )
                    precpu_usage = (
                        precpu_stats.get("cpu_usage")
                        if isinstance(precpu_stats.get("cpu_usage"), Mapping)
                        else {}
                    )
                    cpu_delta = _as_number(cpu_usage.get("total_usage")) - _as_number(
                        precpu_usage.get("total_usage")
                    )
                    system_delta = _as_number(cpu_stats.get("system_cpu_usage")) - _as_number(
                        precpu_stats.get("system_cpu_usage")
                    )
                    online_cpus = _as_number(cpu_stats.get("online_cpus"))
                    if online_cpus <= 0:
                        percpu = cpu_usage.get("percpu_usage")
                        online_cpus = max(1, len(percpu) if isinstance(percpu, list) else 1)
                    cpu_pct = (
                        (cpu_delta / system_delta) * online_cpus * 100.0
                        if system_delta > 0 and cpu_delta > 0
                        else 0.0
                    )

                    memory_stats = (
                        stats.get("memory_stats")
                        if isinstance(stats.get("memory_stats"), Mapping)
                        else {}
                    )
                    mem_usage = max(0, _as_int(memory_stats.get("usage")))
                    mem_limit = max(0, _as_int(memory_stats.get("limit")))
                    mem_pct = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0

                    blkio_stats = (
                        stats.get("blkio_stats")
                        if isinstance(stats.get("blkio_stats"), Mapping)
                        else {}
                    )
                    curr_read = 0
                    curr_write = 0
                    io_entries = blkio_stats.get("io_service_bytes_recursive")
                    if isinstance(io_entries, list):
                        for io_entry in io_entries:
                            if not isinstance(io_entry, Mapping):
                                continue
                            operation = str(io_entry.get("op") or "").lower()
                            value = max(0, _as_int(io_entry.get("value")))
                            if "read" in operation:
                                curr_read += value
                            elif "write" in operation:
                                curr_write += value

                    prev_read, prev_write = self._prev_io.get(key, (None, None))
                    delta_read = 0 if prev_read is None or curr_read < prev_read else curr_read - prev_read
                    delta_write = (
                        0 if prev_write is None or curr_write < prev_write else curr_write - prev_write
                    )
                    self._prev_io[key] = (curr_read, curr_write)

                    network_totals, network_interfaces = _network_stats(stats)
                    pids_stats = stats.get("pids_stats") if isinstance(stats.get("pids_stats"), Mapping) else {}
                    pids_current = (
                        max(0, _as_int(pids_stats.get("current")))
                        if pids_stats.get("current") is not None
                        else None
                    )
                    pids_limit = (
                        max(0, _as_int(pids_stats.get("limit")))
                        if pids_stats.get("limit") is not None
                        else None
                    )
                    timestamp = datetime.datetime.now(datetime.UTC).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    metric = {
                        "timestamp": timestamp,
                        "cpu_pct": round(max(0.0, cpu_pct), 1),
                        "mem_pct": round(max(0.0, mem_pct), 1),
                        "mem_usage": mem_usage,
                        "mem_limit": mem_limit,
                        "io_read": delta_read,
                        "io_write": delta_write,
                        "io_read_total": curr_read,
                        "io_write_total": curr_write,
                        "network_rx_bytes": network_totals["rx_bytes"],
                        "network_tx_bytes": network_totals["tx_bytes"],
                        "network_rx_packets": network_totals["rx_packets"],
                        "network_tx_packets": network_totals["tx_packets"],
                        "network_rx_errors": network_totals["rx_errors"],
                        "network_tx_errors": network_totals["tx_errors"],
                        "pids_current": pids_current,
                        "pids_limit": pids_limit,
                        "stats_available": True,
                    }

                    try:
                        metrics_service.save_metric(
                            container_id=key,
                            cpu_pct=metric["cpu_pct"],
                            mem_usage=mem_usage,
                            mem_limit=mem_limit,
                            mem_pct=metric["mem_pct"],
                            io_read=delta_read,
                            io_write=delta_write,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist metrics for %s: %s", cname, exc)

                    self._set_observation(
                        key,
                        self._observation_from_container(
                            key,
                            container,
                            metric=metric,
                            collection_success=True,
                            stats_available=True,
                            network_interfaces=network_interfaces,
                        ),
                    )
                except NotFound:
                    self._mark_unavailable(key, "not_created", success=True)
                except Exception as exc:
                    with self._lock:
                        self._collection_errors_total += 1
                        previous = self._container_observations.get(
                            key, self._default_observation(key)
                        )
                        error_count = previous.get("collection_errors_total", 0) + 1
                    if container is not None:
                        observation = self._observation_from_container(
                            key,
                            container,
                            metric=_empty_metric(),
                            collection_success=False,
                            stats_available=False,
                        )
                    else:
                        observation = self._default_observation(key)
                        observation["state"] = "error"
                        observation["last_observation_timestamp"] = time.time()
                    observation["collection_errors_total"] = error_count
                    self._set_observation(key, observation)
                    self._prev_io.pop(key, None)
                    logger.debug("Metrics collection failed for container %s: %s", cname, exc)
        finally:
            with self._lock:
                self._last_collection_timestamp = time.time()
                self._last_collection_duration = max(0.0, time.monotonic() - started)

    def render_prometheus_metrics(self) -> str:
        """캐시된 최신 관측을 Prometheus text exposition format으로 렌더링한다."""
        with self._lock:
            observations = deepcopy(self._container_observations)
            collection_runs = self._collection_runs_total
            collection_errors = self._collection_errors_total
            last_timestamp = self._last_collection_timestamp
            last_duration = self._last_collection_duration
            docker_daemon_up = self._docker_daemon_up

        lines: list[str] = []
        lines.extend(
            _metric_block("ktdm_up", "Docker Manager metrics endpoint is available.", "gauge", [({}, 1)])
        )
        lines.extend(
            _metric_block(
                "ktdm_docker_daemon_up",
                "Whether the Docker daemon was reachable during the last collection.",
                "gauge",
                [({}, docker_daemon_up)],
            )
        )
        lines.extend(
            _metric_block(
                "ktdm_metrics_collection_runs_total",
                "Number of completed metrics collection attempts.",
                "counter",
                [({}, collection_runs)],
            )
        )
        lines.extend(
            _metric_block(
                "ktdm_metrics_collection_errors_total",
                "Number of Docker or container metrics collection errors.",
                "counter",
                [({}, collection_errors)],
            )
        )
        lines.extend(
            _metric_block(
                "ktdm_metrics_collection_last_timestamp_seconds",
                "Unix timestamp of the last metrics collection attempt.",
                "gauge",
                [({}, last_timestamp)],
            )
        )
        lines.extend(
            _metric_block(
                "ktdm_metrics_collection_duration_seconds",
                "Duration of the last metrics collection attempt in seconds.",
                "gauge",
                [({}, last_duration)],
            )
        )

        ordered_observations = [
            observations[key] for key in MANAGED_CONTAINERS if key in observations
        ]
        base_labels = [self._metric_labels(observation) for observation in ordered_observations]

        def per_container(name: str, help_text: str, value_getter) -> None:
            lines.extend(
                _metric_block(
                    name,
                    help_text,
                    "gauge",
                    [
                        (labels, value_getter(observation))
                        for labels, observation in zip(base_labels, ordered_observations, strict=False)
                    ],
                )
            )

        lines.extend(
            _metric_block(
                "ktdm_container_info",
                "Managed Docker container identity and image information.",
                "gauge",
                [
                    (
                        {
                            **labels,
                            "image": observation.get("image", "unknown"),
                            "image_id": observation.get("image_id", ""),
                        },
                        1,
                    )
                    for labels, observation in zip(base_labels, ordered_observations, strict=False)
                ],
            )
        )

        state_samples: list[tuple[Mapping[str, Any], Any]] = []
        for labels, observation in zip(base_labels, ordered_observations, strict=False):
            current_state = _normalize_state(observation.get("state"))
            state_samples.extend(
                ({**labels, "state": state}, 1 if state == current_state else 0)
                for state in _STATE_VALUES
            )
        lines.extend(
            _metric_block(
                "ktdm_container_state",
                "One-hot current Docker container state.",
                "gauge",
                state_samples,
            )
        )

        health_samples: list[tuple[Mapping[str, Any], Any]] = []
        for labels, observation in zip(base_labels, ordered_observations, strict=False):
            current_health = _health_status(observation.get("health"))
            health_samples.extend(
                ({**labels, "status": status}, 1 if status == current_health else 0)
                for status in _HEALTH_VALUES
            )
        lines.extend(
            _metric_block(
                "ktdm_container_health_status",
                "One-hot Docker healthcheck status.",
                "gauge",
                health_samples,
            )
        )

        for name, help_text, key in (
            ("ktdm_container_running", "Whether the container is running.", "running"),
            ("ktdm_container_paused", "Whether the container is paused.", "paused"),
            ("ktdm_container_restarting", "Whether the container is restarting.", "restarting"),
            ("ktdm_container_oom_killed", "Whether the container was killed by OOM.", "oom_killed"),
            ("ktdm_container_dead", "Whether Docker reports the container as dead.", "dead"),
            (
                "ktdm_container_collection_success",
                "Whether the last observation for the container succeeded.",
                "collection_success",
            ),
            (
                "ktdm_container_stats_available",
                "Whether Docker stats were available for the last observation.",
                "stats_available",
            ),
        ):
            per_container(name, help_text, lambda observation, key=key: observation.get(key, 0))

        for name, help_text, key in (
            ("ktdm_container_restart_count", "Docker restart count for the container.", "restart_count"),
            ("ktdm_container_mounts", "Number of mounts attached to the container.", "mounts"),
            ("ktdm_container_networks", "Number of Docker networks attached to the container.", "networks"),
            (
                "ktdm_container_port_bindings",
                "Number of published Docker port bindings.",
                "port_bindings",
            ),
            (
                "ktdm_container_expected_ports",
                "Number of ports expected by the managed service registry.",
                "expected_ports",
            ),
            (
                "ktdm_container_collection_errors_total",
                "Container-specific metrics collection errors.",
                "collection_errors_total",
            ),
        ):
            metric_type = "counter" if name.endswith("_total") else "gauge"
            lines.extend(
                _metric_block(
                    name,
                    help_text,
                    metric_type,
                    [
                        (labels, observation.get(key, 0))
                        for labels, observation in zip(base_labels, ordered_observations, strict=False)
                    ],
                )
            )

        per_container(
            "ktdm_container_exit_code_available",
            "Whether a Docker exit code is available for the container.",
            lambda observation: observation.get("exit_code_available", False),
        )
        lines.extend(
            _metric_block(
                "ktdm_container_exit_code",
                "Last Docker exit code for the container when available.",
                "gauge",
                [
                    (labels, observation["exit_code"])
                    for labels, observation in zip(base_labels, ordered_observations, strict=False)
                    if observation.get("exit_code_available")
                ],
            )
        )

        for name, help_text, key in (
            (
                "ktdm_container_created_timestamp_seconds",
                "Unix creation timestamp of the Docker container.",
                "created_timestamp",
            ),
            (
                "ktdm_container_started_timestamp_seconds",
                "Unix start timestamp of the Docker container.",
                "started_timestamp",
            ),
            (
                "ktdm_container_finished_timestamp_seconds",
                "Unix finish timestamp of the Docker container.",
                "finished_timestamp",
            ),
            (
                "ktdm_container_last_observation_timestamp_seconds",
                "Unix timestamp of the last container observation.",
                "last_observation_timestamp",
            ),
        ):
            per_container(name, help_text, lambda observation, key=key: observation.get(key, 0))

        for name, help_text, key in (
            ("ktdm_container_cpu_percent", "Latest Docker container CPU usage percentage.", "cpu_pct"),
            (
                "ktdm_container_memory_usage_bytes",
                "Latest Docker container memory usage in bytes.",
                "mem_usage",
            ),
            (
                "ktdm_container_memory_limit_bytes",
                "Latest Docker container memory limit in bytes.",
                "mem_limit",
            ),
            (
                "ktdm_container_memory_percent",
                "Latest Docker container memory usage percentage.",
                "mem_pct",
            ),
            (
                "ktdm_container_block_io_read_bytes",
                "Latest cumulative Docker block I/O reads in bytes.",
                "io_read_total",
            ),
            (
                "ktdm_container_block_io_write_bytes",
                "Latest cumulative Docker block I/O writes in bytes.",
                "io_write_total",
            ),
            (
                "ktdm_container_network_receive_bytes",
                "Latest cumulative Docker network receive bytes.",
                "network_rx_bytes",
            ),
            (
                "ktdm_container_network_transmit_bytes",
                "Latest cumulative Docker network transmit bytes.",
                "network_tx_bytes",
            ),
            (
                "ktdm_container_network_receive_packets",
                "Latest cumulative Docker network receive packets.",
                "network_rx_packets",
            ),
            (
                "ktdm_container_network_transmit_packets",
                "Latest cumulative Docker network transmit packets.",
                "network_tx_packets",
            ),
            (
                "ktdm_container_network_receive_errors",
                "Latest Docker network receive errors.",
                "network_rx_errors",
            ),
            (
                "ktdm_container_network_transmit_errors",
                "Latest Docker network transmit errors.",
                "network_tx_errors",
            ),
        ):
            per_container(
                name,
                help_text,
                lambda observation, key=key: observation.get("metrics", {}).get(key, 0),
            )

        per_container(
            "ktdm_container_pids_available",
            "Whether Docker exposed PID metrics for the container.",
            lambda observation: any(
                observation.get("metrics", {}).get(key) is not None
                for key in ("pids_current", "pids_limit")
            ),
        )
        for name, help_text, key in (
            (
                "ktdm_container_pids_current",
                "Current number of processes in the Docker container.",
                "pids_current",
            ),
            (
                "ktdm_container_pids_limit",
                "Configured process limit for the Docker container.",
                "pids_limit",
            ),
        ):
            lines.extend(
                _metric_block(
                    name,
                    help_text,
                    "gauge",
                    [
                        (labels, observation.get("metrics", {}).get(key))
                        for labels, observation in zip(base_labels, ordered_observations, strict=False)
                        if observation.get("metrics", {}).get(key) is not None
                    ],
                )
            )

        interface_samples: dict[str, list[tuple[Mapping[str, Any], Any]]] = {}
        for labels, observation in zip(base_labels, ordered_observations, strict=False):
            interfaces = observation.get("network_interfaces", {})
            if not isinstance(interfaces, Mapping):
                continue
            for interface, values in sorted(interfaces.items(), key=lambda item: str(item[0])):
                if not isinstance(values, Mapping):
                    continue
                interface_labels = {**labels, "interface": str(interface)[:128]}
                for metric_name, field in (
                    ("ktdm_container_network_interface_receive_bytes", "rx_bytes"),
                    ("ktdm_container_network_interface_transmit_bytes", "tx_bytes"),
                    ("ktdm_container_network_interface_receive_packets", "rx_packets"),
                    ("ktdm_container_network_interface_transmit_packets", "tx_packets"),
                    ("ktdm_container_network_interface_receive_errors", "rx_errors"),
                    ("ktdm_container_network_interface_transmit_errors", "tx_errors"),
                ):
                    interface_samples.setdefault(metric_name, []).append(
                        (interface_labels, values.get(field, 0))
                    )
        interface_help = {
            "ktdm_container_network_interface_receive_bytes": "Latest Docker network interface receive bytes.",
            "ktdm_container_network_interface_transmit_bytes": "Latest Docker network interface transmit bytes.",
            "ktdm_container_network_interface_receive_packets": "Latest Docker network interface receive packets.",
            "ktdm_container_network_interface_transmit_packets": "Latest Docker network interface transmit packets.",
            "ktdm_container_network_interface_receive_errors": "Latest Docker network interface receive errors.",
            "ktdm_container_network_interface_transmit_errors": "Latest Docker network interface transmit errors.",
        }
        for name in (
            "ktdm_container_network_interface_receive_bytes",
            "ktdm_container_network_interface_transmit_bytes",
            "ktdm_container_network_interface_receive_packets",
            "ktdm_container_network_interface_transmit_packets",
            "ktdm_container_network_interface_receive_errors",
            "ktdm_container_network_interface_transmit_errors",
        ):
            lines.extend(
                _metric_block(name, interface_help[name], "gauge", interface_samples.get(name, []))
            )

        return "\n".join(lines) + "\n"

    @staticmethod
    def _metric_labels(observation: Mapping[str, Any]) -> dict[str, str]:
        return {
            "container_id": str(observation.get("container_id") or "unknown"),
            "container_name": str(observation.get("container_name") or "unknown"),
            "compose_service": str(observation.get("compose_service") or "unknown"),
            "role": str(observation.get("role") or "unknown"),
        }


metrics_collector = MetricsCollector()


__all__ = ["MetricsCollector", "metrics_collector", "_PROMETHEUS_CONTENT_TYPE"]
