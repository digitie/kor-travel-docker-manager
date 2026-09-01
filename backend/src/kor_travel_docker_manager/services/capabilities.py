"""Compose/pinned-runtime mutation 허가를 나타내는 identity 기반 capability 센티널.

GM-20: 이전에는 c6c_deployment.py(7,800줄대) 안에 있어서 compose_service.py·
docker_service.py가 이 두 상수 하나만 필요한데도 거대 모듈 전체를 import했다.
게이트는 ``is`` identity 비교이므로(c6c_deployment.py의 mutation 허가 검사 참고)
여기서도 반드시 모듈 로드 시 한 번만 만든 싱글턴을 유지해야 한다 — 두 번째
인스턴스를 새로 만들면 ``is`` 비교가 항상 거짓이 되어 조용히 거부된다.
``c6c_deployment.py``는 하위 호환을 위해 이 모듈에서 재수출한다.
"""

from __future__ import annotations


class _ManagedComposeMutationCapability:
    __slots__ = ()


class _PinnedRuntimeRebuildMutationCapability:
    __slots__ = ()


_MANAGED_COMPOSE_MUTATION_CAPABILITY = _ManagedComposeMutationCapability()
_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY = _PinnedRuntimeRebuildMutationCapability()
