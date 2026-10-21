"""수집기 실제 AST를 격리해 Docker·DB·앱 초기화 없이 관측 계약을 검증한다."""

import ast
import asyncio
from collections.abc import Callable, ItemsView, Iterator, Mapping, ValuesView
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kor_travel_docker_manager.services.container_inventory import observed_container_specs

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
        "docker": SimpleNamespace(DockerClient=object, errors=SimpleNamespace(DockerException=type("DockerException", (Exception,), {}))),
        "observed_container_specs": observed_container_specs,
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
        return SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [], get=get))

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

@pytest.mark.parametrize(
    ("configured", "image_id", "expected", "reads"),
    [
        ("repo:tag", "sha256:fixture", ("repo:tag", "sha256:fixture"), 0),
        ("repo:tag", "", ("repo:tag", "fallback-id"), 1),
        ("", "sha256:fixture", ("fallback:tag", "sha256:fixture"), 1),
        ("", "", ("fallback:tag", "fallback-id"), 1),
    ],
)
def test_image_lookup_only_when_inspect_metadata_is_incomplete(
    configured, image_id, expected, reads
):
    ns = load_collector()
    container = Container(config_image=configured, image_id=image_id)
    assert ns["_image_metadata"](container, container.attrs) == expected
    assert container.image_reads == reads


@pytest.mark.parametrize(
    ("configured", "image_id", "expected"),
    [
        ("repo:tag", "", ("repo:tag", "")),
        ("", "sha256:fixture", ("sha256:fixture", "sha256:fixture")),
        ("", "", ("unknown", "")),
    ],
)
def test_image_lookup_failure_keeps_existing_fallback(configured, image_id, expected):
    ns = load_collector()

    class UnavailableImage(Container):
        @property
        def image(self):
            raise RuntimeError("fixture unavailable image")

    container = UnavailableImage(config_image=configured, image_id=image_id)
    assert ns["_image_metadata"](container, container.attrs) == expected


@pytest.mark.parametrize("count", [1, 4, 16])
@pytest.mark.parametrize(
    "mode", ["running", "exited", "stats-error", "get-error", "not-found", "offline"]
)
def test_one_snapshot_derivation_for_entire_cycle(count, mode):
    ns, template, _ = collector_fixture()
    specs = {
        f"app-{i}": {**template.specs["app"], "name": f"container-{i}"}
        for i in range(count)
    }
    mapping = ResolvingMapping(specs)
    ns["MANAGED_CONTAINERS"] = mapping
    collector = ns["MetricsCollector"]()
    mapping.resolves = mapping.spec_visits = 0

    def get(name):
        if mode == "not-found":
            raise ns["NotFound"]("fixture")
        if mode == "get-error":
            raise RuntimeError("fixture")
        container = Container("exited" if mode == "exited" else "running")
        if mode == "stats-error":
            container.stats_error = RuntimeError("fixture")
        return container

    def provider():
        if mode == "offline":
            raise RuntimeError("fixture")
        return SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [], get=get))

    collector.set_docker_client_provider(provider)
    asyncio.run(collector.collect_metrics())
    assert mapping.resolves == 1
    assert mapping.spec_visits == count
    assert len(collector._container_observations) == count
    for key in specs:
        observation = collector.get_container_observation(key)
        assert observation["container_name"] == specs[key]["name"]
        assert observation["collection_success"] == (mode in {"running", "exited", "not-found"})


def test_cycle_snapshot_consistency_and_next_cycle_invalidation():
    ns, mapping, collector = collector_fixture()
    mapping.specs["other"] = {**mapping.specs["app"], "name": "other-container"}

    def get(name):
        mapping.specs["app"]["display_name"] = "다음 cycle"
        mapping.specs["other"]["display_name"] = "다음 cycle"
        return Container("exited")

    collector.set_docker_client_provider(
        lambda: SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [], get=get))
    )
    asyncio.run(collector.collect_metrics())
    assert collector.get_container_observation("app")["display_name"] == "앱"
    assert collector.get_container_observation("other")["display_name"] == "앱"
    asyncio.run(collector.collect_metrics())
    assert collector.get_container_observation("app")["display_name"] == "다음 cycle"
    assert collector.get_container_observation("other")["display_name"] == "다음 cycle"
    assert mapping.resolves == 2


def test_explicit_empty_spec_is_not_treated_as_missing_snapshot():
    ns, mapping, collector = collector_fixture()
    result = collector._default_observation("app", spec={})
    assert result["container_name"] == "app"
    assert result["expected_ports"] == 0
    assert mapping.resolves == 0

def test_cycle_uses_actual_registry_lazy_mapping_once():
    registry_source = SOURCE.with_name("registry.py")
    node = next(
        n for n in ast.parse(registry_source.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "_LazyMapping"
    )
    scope = {
        "Mapping": Mapping, "Any": Any, "Callable": Callable, "Iterator": Iterator,
        "ItemsView": ItemsView, "ValuesView": ValuesView,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(registry_source), "exec"), scope)
    ns = load_collector()
    calls = []

    def loader():
        calls.append(1)
        return {f"app-{i}": {"name": f"container-{i}"} for i in range(16)}

    ns["MANAGED_CONTAINERS"] = scope["_LazyMapping"](loader)
    collector = ns["MetricsCollector"]()
    calls.clear()
    collector.set_docker_client_provider(
        lambda: SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [], get=lambda name: Container("exited")))
    )
    asyncio.run(collector.collect_metrics())
    assert len(calls) == 1
    assert len(collector._container_observations) == 16
