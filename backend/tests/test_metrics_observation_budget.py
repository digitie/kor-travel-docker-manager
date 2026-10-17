"""수집기 실제 AST를 격리해 Docker·DB·앱 초기화 없이 관측 계약을 검증한다."""

import ast
import asyncio
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "src/kor_travel_docker_manager/services/metrics_collector.py"


def load_collector(source=None):
    """제품 import 중 외부 부작용만 제거하고 실제 함수·클래스 본문을 실행한다."""
    tree = ast.parse(SOURCE.read_text() if source is None else source)
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Import) and any(a.name == "docker" for a in node.names):
            continue
        if isinstance(node, ast.ImportFrom) and (
            node.module.startswith("docker") or node.module.startswith("kor_travel_docker_manager")
        ):
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "metrics_collector" for t in node.targets
        ):
            continue
        nodes.append(node)
    ns = {
        "docker": SimpleNamespace(DockerClient=object),
        "NotFound": type("NotFound", (Exception,), {}),
        "MANAGED_CONTAINERS": {},
        "TARGETS_CONFIG_ERRORS": (ValueError,),
        "metrics_service": SimpleNamespace(save_metric=lambda **kw: None),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


class ResolvingMapping(Mapping):
    def __init__(self, specs):
        self.specs = specs
        self.resolves = 0
        self.spec_visits = 0

    def _resolve(self):
        self.resolves += 1
        self.spec_visits += len(self.specs)
        return {key: dict(spec) for key, spec in self.specs.items()}

    def __getitem__(self, key):
        return self._resolve()[key]

    def __iter__(self):
        return iter(self.specs)

    def __len__(self):
        return len(self.specs)

    def items(self):
        return self._resolve().items()


class Container:
    def __init__(self, state="running", config_image="repo:tag", image_id="sha256:fixture"):
        self.status = state
        self.image_reads = 0
        self.stats_reads = 0
        self.stats_error = None
        self.attrs = {
            "Id": "fixture-id",
            "Image": image_id,
            "Config": {"Image": config_image},
            "State": {"Status": state, "Running": state == "running", "ExitCode": 0,
                      "Health": {"Status": "healthy"}},
            "HostConfig": {"PortBindings": {}},
            "NetworkSettings": {"Networks": {}},
        }

    @property
    def image(self):
        self.image_reads += 1
        return SimpleNamespace(tags=["fallback:tag"], short_id="fallback-id")

    def stats(self, stream=False):
        self.stats_reads += 1
        if self.stats_error is not None:
            raise self.stats_error
        return {}


def collector_fixture():
    ns = load_collector()
    mapping = ResolvingMapping({"app": {
        "name": "app-container", "display_name": "앱", "role": "backend",
        "compose_service": "app", "expected_ports": [12345],
    }})
    ns["MANAGED_CONTAINERS"] = mapping
    collector = ns["MetricsCollector"]()
    mapping.resolves = mapping.spec_visits = 0
    ns["time"] = SimpleNamespace(time=lambda: 1234.0, monotonic=lambda: 1234.0)
    return ns, mapping, collector


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("state", ["running", "exited"])
def test_one_registry_derivation_per_observation(cached, state):
    ns, mapping, collector = collector_fixture()
    old = deepcopy(collector._container_observations)
    if cached:
        collector._container_observations["app"]["collection_errors_total"] = 7
    else:
        collector._container_observations.clear()
    metric = ns["_empty_metric"]()
    result = collector._observation_from_container(
        "app", Container(state), metric=metric, collection_success=True, stats_available=False
    )
    assert mapping.resolves == 1
    assert result["collection_errors_total"] == (7 if cached else 0)
    assert result["container_name"] == "app-container"
    assert result["expected_ports"] == 1
    assert result["state"] == state
    assert result["running"] == (state == "running")
    assert result["last_observation_timestamp"] == 1234.0
    assert result["metrics"] is metric
    assert old["app"]["collection_errors_total"] == 0
    if cached:
        assert collector._container_observations["app"]["collection_errors_total"] == 7


def test_next_observation_reflects_registry_changes_without_stale_cache():
    ns, mapping, collector = collector_fixture()
    first = collector._observation_from_container(
        "app", Container(), metric={}, collection_success=True, stats_available=True
    )
    mapping.specs["app"].update(name="reconfigured", expected_ports=[1, 2])
    second = collector._observation_from_container(
        "app", Container(), metric={}, collection_success=True, stats_available=True
    )
    assert first["container_name"] == "app-container"
    assert second["container_name"] == "reconfigured"
    assert second["expected_ports"] == 2
    assert mapping.resolves == 2


def test_registry_failure_still_propagates_at_observation_time():
    ns, mapping, collector = collector_fixture()

    def broken():
        raise ValueError("fixture invalid registry")

    mapping._resolve = broken
    with pytest.raises(ValueError, match="fixture invalid registry"):
        collector._observation_from_container(
            "app", Container(), metric={}, collection_success=False, stats_available=False
        )


@pytest.mark.parametrize("mode", ["running", "exited", "stats-error", "not-found", "offline"])
def test_collect_preserves_success_degraded_and_offline_contracts(mode):
    ns, mapping, collector = collector_fixture()
    container = Container("exited" if mode == "exited" else "running")
    if mode == "stats-error":
        container.stats_error = RuntimeError("fixture")
    collector._container_observations["app"]["collection_errors_total"] = 4
    collector._prev_io["app"] = (1, 2)

    def get(name):
        if mode == "not-found":
            raise ns["NotFound"]("fixture")
        return container

    def provider():
        if mode == "offline":
            raise RuntimeError("fixture")
        return SimpleNamespace(containers=SimpleNamespace(get=get))

    collector.set_docker_client_provider(provider)
    asyncio.run(collector.collect_metrics())
    result = collector.get_container_observation("app")
    expected_state = {"exited": "exited", "not-found": "not_created", "offline": "offline"}.get(mode, "running")
    assert result["state"] == expected_state
    assert result["collection_success"] == (mode in {"running", "exited", "not-found"})
    assert result["stats_available"] == (mode == "running")
    assert result["collection_errors_total"] == (5 if mode == "stats-error" else 4)
    assert container.stats_reads == (1 if mode in {"running", "stats-error"} else 0)
    assert collector._collection_runs_total == 1
    if mode != "running":
        assert "app" not in collector._prev_io
