import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from kor_travel_docker_manager.services.container_inventory import observed_container_specs
from kor_travel_docker_manager.services.metrics_collector import MetricsCollector


def test_discovery_does_not_register_actions_and_keeps_stable_bounded_ids():
    names = ['existing', 'service-with-a-long-name-' * 5]
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [SimpleNamespace(name=n) for n in names]))
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
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: running, get=lambda name: container))
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

def test_discovery_failure_invalidates_unknown_metrics_without_forgetting_inventory(monkeypatch):
    from docker.errors import DockerException

    from kor_travel_docker_manager.services import metrics_collector as module
    monkeypatch.setattr(module, 'MANAGED_CONTAINERS', {})
    monkeypatch.setattr(module.metrics_service, 'save_metric', Mock())
    container = SimpleNamespace(name='worker', status='running',
        attrs={'Id': 'one', 'State': {'Status': 'running'}},
        stats=lambda **kwargs: {})
    listing = Mock(return_value=[container])
    client = SimpleNamespace(containers=SimpleNamespace(list=listing, get=lambda _: container))
    collector = MetricsCollector()
    collector.set_docker_client_provider(lambda: client)
    key = next(iter(observed_container_specs(client, {})))
    asyncio.run(collector.collect_metrics())
    listing.side_effect = DockerException('inventory failed')
    asyncio.run(collector.collect_metrics())
    observation = collector.get_container_observation(key)
    assert observation['container_name'] == 'worker'
    assert not observation['collection_success'] and not observation['stats_available']
    assert not collector.get_latest_metric(key)['stats_available']
    collector.set_docker_client_provider(Mock(side_effect=DockerException('offline')))
    asyncio.run(collector.collect_metrics())
    assert collector.get_container_observation(key)['state'] == 'offline'


def test_recreated_container_resets_io_baseline(monkeypatch):
    from kor_travel_docker_manager.services import metrics_collector as module
    monkeypatch.setattr(module, 'MANAGED_CONTAINERS', {})
    monkeypatch.setattr(module.metrics_service, 'save_metric', Mock())
    values = {'read': 100}
    container = SimpleNamespace(name='worker', status='running',
        attrs={'Id': 'one', 'State': {'Status': 'running'}},
        stats=lambda **kwargs: {'blkio_stats': {'io_service_bytes_recursive': [
            {'op': 'Read', 'value': values['read']}]}})
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [container], get=lambda _: container))
    collector = MetricsCollector()
    collector.set_docker_client_provider(lambda: client)
    key = next(iter(observed_container_specs(client, {})))
    asyncio.run(collector.collect_metrics())
    values['read'] = 500
    container.attrs['Id'] = 'two'
    asyncio.run(collector.collect_metrics())
    assert collector.get_latest_metric(key)['io_read'] == 0
    values['read'] = 550
    asyncio.run(collector.collect_metrics())
    assert collector.get_latest_metric(key)['io_read'] == 50


def test_inventory_discovery_does_not_block_event_loop(monkeypatch):
    import threading

    from kor_travel_docker_manager.services import metrics_collector as module
    monkeypatch.setattr(module, 'MANAGED_CONTAINERS', {})
    started, release = threading.Event(), threading.Event()
    def listing(**kwargs):
        started.set()
        assert release.wait(2), 'event loop could not release Docker discovery'
        return []
    collector = MetricsCollector()
    collector.set_docker_client_provider(lambda: SimpleNamespace(containers=SimpleNamespace(list=listing)))
    async def run():
        task = asyncio.create_task(collector.collect_metrics())
        await asyncio.to_thread(started.wait, 1)
        release.set()
        await task
    asyncio.run(run())


def test_failed_inspect_preserves_read_only_and_inventory_failure_is_reported(monkeypatch):
    from docker.errors import DockerException

    from kor_travel_docker_manager.services import docker_service as module
    from kor_travel_docker_manager.services import registry
    config = registry.load_targets_config()
    key, spec = next(iter(config['containers'].items()))
    monkeypatch.setattr(module, 'MANAGED_CONTAINERS', {key: spec})
    monkeypatch.setattr(module, 'get_compose_config', lambda: {})
    listing = Mock(return_value=[SimpleNamespace(name='unregistered')])
    client = SimpleNamespace(containers=SimpleNamespace(list=listing, get=Mock(side_effect=DockerException('inspect failed'))))
    service = module.DockerService.__new__(module.DockerService)
    monkeypatch.setattr(service, '_get_client', lambda: client)
    monkeypatch.setattr(module, 'probe_remote_connections', lambda values: {name: 'remote' for name in values})
    rows = service.get_containers_status()
    unknown = next(row for row in rows if row['name'] == 'unregistered')
    assert unknown['observed_only'] and unknown['observed_host']
    assert unknown['status'] == 'error'
    listing.side_effect = DockerException('list failed')
    rows = service.get_containers_status()
    assert rows and all(row['inventory_complete'] is False for row in rows)
