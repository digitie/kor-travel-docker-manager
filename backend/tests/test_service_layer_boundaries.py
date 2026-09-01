"""GM-20: services/errors.py·services/capabilities.py 신설과
metrics_collector↔docker_service 순환 해소를 검증한다.

이 회귀는 "값이 같다"가 아니라 "같은 객체다"를 확인해야 의미가 있다 — 특히
capability 센티널은 `is` identity 비교로 게이트되므로, 재수출이 아니라
독립적으로 다시 선언된 두 번째 인스턴스가 생기면 그 즉시 모든 mutation이
조용히 거부된다."""

import pytest


def test_error_hierarchy_is_re_exported_not_redeclared():
    """c6c_deployment.py·compose_service.py·main.py가 각각 가져오는
    DeploymentContractError 계열이 전부 services/errors.py의 같은 클래스
    객체여야 한다 — 어느 한쪽이 독립적으로 다시 선언하면 `except
    DeploymentContractError`가 다른 모듈이 던진 예외를 못 잡는다."""

    from kor_travel_docker_manager import main
    from kor_travel_docker_manager.services import c6c_deployment, compose_service, errors

    for name in (
        "DeploymentContractError",
        "ComposeCandidateContractError",
        "ComposePostMutationContractError",
    ):
        canonical = getattr(errors, name)
        assert getattr(c6c_deployment, name) is canonical
        assert getattr(compose_service, name) is canonical
        assert getattr(main, name) is canonical


def test_mutation_capability_sentinels_are_re_exported_not_redeclared():
    """capability 게이트는 `is` identity 비교다(c6c_deployment.py) — 여기서
    하나라도 별도 인스턴스가 되면 정상 mutation이 전부 조용히 거부된다."""

    from kor_travel_docker_manager.services import c6c_deployment, capabilities, compose_service
    from kor_travel_docker_manager.services import docker_service as docker_service_module

    for name in (
        "_MANAGED_COMPOSE_MUTATION_CAPABILITY",
        "_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY",
    ):
        canonical = getattr(capabilities, name)
        assert getattr(c6c_deployment, name) is canonical
        assert getattr(compose_service, name) is canonical

    assert (
        docker_service_module._MANAGED_COMPOSE_MUTATION_CAPABILITY
        is capabilities._MANAGED_COMPOSE_MUTATION_CAPABILITY
    )


def test_metrics_collector_module_does_not_import_docker_service():
    """GM-20: 이전에는 metrics_collector.py가 docker_service를 모듈 레벨로
    import해서 docker_service.py가 반대 방향 import를 함수 안 지연 import로
    우회해야 했다. 이 테스트는 metrics_collector.py 소스에 그 import 문이
    다시 등장하면 실패한다 — 순환이 재도입돼도 지연 import로 다시 숨겨지면
    평소 테스트로는 안 잡히기 때문에 소스 자체를 검사한다."""

    import ast
    import inspect

    from kor_travel_docker_manager.services import metrics_collector

    tree = ast.parse(inspect.getsource(metrics_collector))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("docker_service" in module for module in imported_modules)


def test_metrics_collector_gets_managed_containers_from_registry_directly():
    from kor_travel_docker_manager.services import metrics_collector, registry

    assert metrics_collector.MANAGED_CONTAINERS is registry.MANAGED_CONTAINERS


def test_docker_service_wires_the_docker_client_provider_at_import_time():
    """docker_service.py 모듈 로드 자체가 배선을 완료해야 한다 — main.py
    lifespan이 별도로 이 배선을 호출하지 않으므로, import 시점에 빠지면
    실제 백그라운드 수집 루프가 조용히 매 10초 RuntimeError로 죽는다."""

    from kor_travel_docker_manager.services.docker_service import docker_service
    from kor_travel_docker_manager.services.metrics_collector import metrics_collector

    provider = metrics_collector._docker_client_provider
    assert provider is not None
    assert provider.__self__ is docker_service
    assert provider.__func__ is type(docker_service)._get_client


def test_collect_metrics_raises_clearly_when_provider_was_never_wired():
    """docker 자체가 죽은 것과 배선 누락을 구분해야 한다 — 배선 누락을
    "offline"으로 조용히 뭉개면 실제로는 코드 결함인데 인프라 장애처럼
    보인다."""

    import asyncio

    from kor_travel_docker_manager.services.metrics_collector import MetricsCollector

    collector = MetricsCollector()
    assert collector._docker_client_provider is None

    with pytest.raises(RuntimeError, match="set_docker_client_provider"):
        asyncio.run(collector.collect_metrics())


def test_compose_service_transaction_capture_methods_are_public():
    """GM-20: docker_service.py가 자기 lock 아래에서 직접 호출해야 하는
    메서드라 밑줄을 뗐다 — 프라이빗 크로스 모듈 호출이 다시 생기지 않았는지
    확인한다."""

    from kor_travel_docker_manager.services.compose_service import ComposeService

    assert hasattr(ComposeService, "capture_transaction_unlocked")
    assert hasattr(ComposeService, "capture_candidate_transaction_unlocked")
    assert not hasattr(ComposeService, "_capture_transaction_unlocked")
    assert not hasattr(ComposeService, "_capture_candidate_transaction_unlocked")
