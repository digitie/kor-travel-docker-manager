import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from kor_travel_docker_manager.services.container_inventory import observed_container_specs
from kor_travel_docker_manager.services.metrics_collector import MetricsCollector


def test_discovery_does_not_register_actions_and_keeps_stable_bounded_ids():
    names = ['existing', 'service-with-a-long-name-' * 5]
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda: [SimpleNamespace(name=n) for n in names]))
    managed = {'known': {'name': names[0]}}
    first = observed_container_specs(client, managed)
    second = observed_container_specs(client, managed)
    assert first == second
    assert len(first) == 1
    key, spec = next(iter(first.items()))
    assert len(key) <= 50 and key not in managed
    assert spec['name'] == names[1] and spec['observed_only'] is True
    assert managed == {'known': {'name': names[0]}}


def test_unregistered_metrics_follow_docker_identity_and_disappear_when_stopped(monkeypatch):
    from kor_travel_docker_manager.services import metrics_collector as module

    monkeypatch.setattr(module, 'MANAGED_CONTAINERS', {})
    monkeypatch.setattr(module.metrics_service, 'save_metric', Mock())
    container = SimpleNamespace(name='external-worker', status='running', attrs={
        'Id': 'generation-one', 'Config': {'Image': 'example:v1'},
        'State': {'Status': 'running'}, 'HostConfig': {},
    }, stats=lambda **kwargs: {
        'cpu_stats': {'cpu_usage': {'total_usage': 300}, 'system_cpu_usage': 1000, 'online_cpus': 2},
        'precpu_stats': {'cpu_usage': {'total_usage': 100}, 'system_cpu_usage': 500},
        'memory_stats': {'usage': 1000, 'limit': 2000, 'stats': {'inactive_file': 400}},
    })
    running = [container]
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda: running, get=lambda name: container))
    collector = MetricsCollector()
    collector.set_docker_client_provider(lambda: client)
    key = next(iter(observed_container_specs(client, {})))
    asyncio.run(collector.collect_metrics())
    metric = collector.get_latest_metric(key, docker_id='generation-one')
    assert metric['cpu_pct'] == 80
    assert metric['mem_usage'] == 600 and metric['mem_pct'] == 30
    assert collector.get_latest_metric(key, docker_id='generation-two')['stats_available'] is False
    assert 'external-worker' in collector.render_prometheus_metrics()
    running.clear()
    asyncio.run(collector.collect_metrics())
    assert collector.get_container_observation(key) is None
    assert collector.get_latest_metric(key)['stats_available'] is False
