"""Dagster family와 pinned runtime slot의 서비스 이름을 **렌더된 모델에서** 파생한다(ADR-54).

공용 Dagster 제어 평면(ADR-54) 전환은 target의 옛 webserver·daemon(과 그것에 기대는 gateway)을
`profiles: [legacy-dagster]`로 내리고 code-server만 공용 plane에 합류시킨다. 그 서비스 이름을 코드가
literal로 들고 있으면 전환된 target에서 frozen render(`--profile bootstrap`만)는 그 서비스를 모르는데
pinned 재구축은 여전히 빌드·`up`·검사하려 하고, 명시적 `up <서비스>`는 꺼진 profile의 서비스도 띄워
옛 daemon이 되살아난다. 그래서 이름은 여기서 한 번 파생한다.

- **모양으로 찾는다.** code-server는 target의 `services` 중 `dagster api grpc`를 실행하는 서비스, 옛
  webserver·daemon은 `dagster-webserver`/`dagster-daemon`을 실행하면서 그 code-server에 `depends_on`하는
  서비스, gateway는 그것들에 `depends_on`하는 서비스다. 모양은 스위치와 무관하다 — 전환 뒤에도 옛 서비스는
  compose에 profile로 남는다(되돌리기와, 옛 override 같은 과거 모양을 알아보는 데 쓴다).
- **스위치로 고른다.** `dagster.control_plane`이 `own`이면 오늘의 이름 그대로다. `shared`면 옛
  webserver·daemon·gateway는 어떤 실행 집합에도 없고, target Dagster 이미지의 운반자(carrier)는
  code-server다 — pinned 재구축은 그것을 빌드·`up`·검사한다.

원본은 이 코드와 함께 설치된 release다. trusted 설치본에서는 설치 root의 reference compose
(`.ktdm-release-compose.yml`)와 고정 경로의 `docker-targets.yml`, 개발 checkout에서는 이 checkout의 두 파일이다.
env로 옮기지 않는다 — 이 이름들은 코드 상수를 대신하므로 코드와 같은 release에서 와야 한다. UI가 제자리에서
고치는 `docker-compose.yml`은 원본이 아니다(ADR-51 결정 5와 같은 이유).

import 시점에는 아무것도 읽지 않는다 — 모듈 상수 자리에는 `Lazy*` 컨테이너를 둔다(registry의
`_LazyMapping`과 같은 이유: 설정이 깨져도 `--help`는 산다).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence, Set
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, TypeVar

from kor_travel_docker_manager.services.compose_references import reference_compose_path
from kor_travel_docker_manager.services.errors import DeploymentContractError
from kor_travel_docker_manager.services.registry import (
    DAGSTER_CONTROL_PLANES,
    load_targets_config,
)
from kor_travel_docker_manager.services.trusted_install import (
    TRUSTED_INSTALL_ROOT,
    running_from_trusted_install_root,
)
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

#: 이 파일이 놓인 개발 checkout의 root(`backend/src/kor_travel_docker_manager/services`의 네 단계 위).
_CHECKOUT_ROOT: Final = Path(__file__).resolve().parents[4]

ControlPlane = Literal["own", "shared"]


# ── Dagster family ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DagsterFamily:
    """target 하나의 Dagster 프로세스 서비스와 그 합류 스위치."""

    target: str
    control_plane: ControlPlane
    code_server: str
    webserver: str
    daemon: str
    #: 옛 webserver·daemon에 기대는 서비스(weather의 gateway). 대부분 비어 있다.
    gateways: tuple[str, ...]

    @property
    def shared(self) -> bool:
        return self.control_plane == "shared"

    @property
    def legacy(self) -> tuple[str, ...]:
        """전환하면 `legacy-dagster`로 내려가는 서비스(스위치와 무관한 모양)."""

        return (self.webserver, self.daemon, *self.gateways)

    @property
    def names(self) -> tuple[str, ...]:
        """이 family의 모든 서비스 이름(스위치와 무관) — code-server·webserver·daemon·gateway."""

        return (self.code_server, *self.legacy)

    @property
    def processes(self) -> tuple[str, ...]:
        """이 target의 Dagster 프로세스 서비스 전부(webserver·code-server·daemon, 스위치와 무관)."""

        return (self.webserver, self.code_server, self.daemon)

    @property
    def carrier(self) -> str:
        """target Dagster 이미지를 대표하는 실행 서비스 — `own`이면 webserver, `shared`면 code-server."""

        return self.code_server if self.shared else self.webserver

    @property
    def active_daemon(self) -> str | None:
        """이 target이 스스로 띄우는 daemon — `shared`면 없다(공용 daemon이 돈다)."""

        return None if self.shared else self.daemon

    @property
    def retired(self) -> tuple[str, ...]:
        """지금 어떤 실행 집합에도 있어서는 안 되는 서비스 — `shared`의 옛 서비스, `own`이면 없다."""

        return self.legacy if self.shared else ()


def _words(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(part) for part in value]
    if isinstance(value, str):
        return value.split()
    return []


def _runs(service: Mapping[str, Any], program: str) -> bool:
    return program in " ".join(_words(service.get("command")) + _words(service.get("entrypoint")))


def _depends(service: Mapping[str, Any]) -> set[str]:
    depends = service.get("depends_on") or {}
    if isinstance(depends, Mapping | list):
        return {str(name) for name in depends}
    return set()


def _one(names: set[str], *, what: str, target: str) -> str:
    if len(names) != 1:
        raise DeploymentContractError(
            f"Dagster target {target} must have exactly one {what}, found {sorted(names)}"
        )
    return next(iter(names))


#: 공용 plane의 webserver·daemon이 붙이는 파생 workspace. 이것을 붙인 서비스는 어느 target의 옛 서비스도 아니다 —
#: 공용 webserver가 합류한 code-server에 `depends_on`해도 그 target의 webserver로 세지 않는다.
_SHARED_WORKSPACE_SOURCE: Final = "./config/dagster-shared/workspace.yaml"


def _mounts_shared_workspace(service: Mapping[str, Any]) -> bool:
    for volume in service.get("volumes") or []:
        source = volume.get("source") if isinstance(volume, Mapping) else str(volume).split(":", 1)[0]
        if source == _SHARED_WORKSPACE_SOURCE:
            return True
    return False


def _split_documents(
    compose: Mapping[str, Any], targets: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    services = compose.get("services")
    target_specs = targets.get("targets")
    if not isinstance(services, Mapping) or not isinstance(target_specs, Mapping):
        raise DeploymentContractError("Dagster topology documents are invalid")
    return services, target_specs


def _is_dagster_target(spec: object) -> bool:
    return isinstance(spec, Mapping) and "dagster" in spec and not spec.get("external_project")


def derive_dagster_family(
    compose: Mapping[str, Any],
    targets: Mapping[str, Any],
    target_id: str,
) -> DagsterFamily:
    """target **하나**의 family를 모양으로 찾는다. 다른 target의 모양은 보지 않는다 — geo·weather의 compose가
    어긋나도 Map·PinVi의 파생은 막히지 않는다. 이 target의 모양이 어긋나면 거부한다."""

    services, target_specs = _split_documents(compose, targets)
    spec = target_specs.get(target_id)
    if not isinstance(spec, Mapping) or not _is_dagster_target(spec):
        raise DeploymentContractError(f"Dagster target {target_id} is not declared")
    block = spec.get("dagster")
    control_plane = block.get("control_plane") if isinstance(block, Mapping) else None
    if control_plane not in DAGSTER_CONTROL_PLANES:
        raise DeploymentContractError(f"Dagster target {target_id} control plane is invalid")
    declared = [str(name) for name in spec.get("services") or []]
    code_server = _one(
        {
            name
            for name in declared
            if isinstance(services.get(name), Mapping) and _runs(services[name], "api grpc")
        },
        what="code-server",
        target=target_id,
    )
    # 이 target의 code-server에 기대는 서비스만 본다 — 공용 plane(공용 workspace를 붙인 것)은 뺀다.
    dependents = {
        str(name): service
        for name, service in services.items()
        if isinstance(service, Mapping)
        and code_server in _depends(service)
        and not _mounts_shared_workspace(service)
    }

    def runners(program: str) -> set[str]:
        return {name for name, service in dependents.items() if _runs(service, program)}

    webserver = _one(runners("dagster-webserver"), what="webserver", target=target_id)
    daemon = _one(runners("dagster-daemon"), what="daemon", target=target_id)
    # gateway는 옛 webserver·daemon에 기대는 **다른** 서비스다(Map daemon은 Map webserver에 기대지만 gateway가 아니다).
    gateways = sorted(
        str(name)
        for name, service in services.items()
        if isinstance(service, Mapping)
        and name not in (webserver, daemon)
        and _depends(service) & {webserver, daemon}
        and not _mounts_shared_workspace(service)
    )
    return DagsterFamily(
        target=target_id,
        control_plane=control_plane,
        code_server=code_server,
        webserver=webserver,
        daemon=daemon,
        gateways=tuple(gateways),
    )


def derive_dagster_families(
    compose: Mapping[str, Any],
    targets: Mapping[str, Any],
) -> Mapping[str, DagsterFamily]:
    """`dagster` 절을 가진 target 전부의 family(하나라도 어긋나면 거부 — 계약 검사·테스트용)."""

    _, target_specs = _split_documents(compose, targets)
    return MappingProxyType(
        {
            str(target_id): derive_dagster_family(compose, targets, str(target_id))
            for target_id, spec in target_specs.items()
            if _is_dagster_target(spec)
        }
    )


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        document = load_yaml_rejecting_duplicate_keys(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise DeploymentContractError(f"Dagster topology source cannot be read: {path}") from exc
    if not isinstance(document, Mapping):
        raise DeploymentContractError(f"Dagster topology source is not a mapping: {path}")
    return document


@lru_cache(maxsize=1)
def _installed_documents() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """이 코드와 함께 설치된 release의 (reference compose, targets). 프로세스당 한 번 읽는다."""

    if running_from_trusted_install_root():
        root = TRUSTED_INSTALL_ROOT
        targets: Mapping[str, Any] = load_targets_config()
    else:
        root = _CHECKOUT_ROOT
        targets = _load_yaml(root / "config" / "docker-targets.yml")
    compose = _load_yaml(reference_compose_path(root / "docker-compose.yml"))
    return compose, targets


@cache
def installed_dagster_family(target: str) -> DagsterFamily:
    """설치된 release의 compose·targets에서 target 하나의 family. 실패는 캐시하지 않는다."""

    return derive_dagster_family(*_installed_documents(), target)


def installed_dagster_families() -> Mapping[str, DagsterFamily]:
    """설치된 release의 모든 family(진단·테스트용 — 실행 경로는 target별로 부른다)."""

    return derive_dagster_families(*_installed_documents())


def installed_container_name(service: str) -> str:
    """설치된 targets의 `containers`에서 compose 서비스의 컨테이너 이름. 하나가 아니면 거부한다."""

    _, targets = _installed_documents()
    containers = targets.get("containers")
    names = {
        str(spec["name"])
        for spec in (containers.values() if isinstance(containers, Mapping) else ())
        if isinstance(spec, Mapping) and spec.get("compose_service") == service and spec.get("name")
    }
    if len(names) != 1:
        raise DeploymentContractError(f"compose service {service} has no single managed container")
    return next(iter(names))


def dagster_family(target: str) -> DagsterFamily:
    return installed_dagster_family(target)


# ── pinned runtime slot ───────────────────────────────────────────────────

#: pinned generation이 고정하는 이미지 slot. 이름은 generation payload의 `<slot>_image_id` 필드다 —
#: compose 서비스 이름이 아니다. slot이 어느 서비스로 도는지는 `RuntimeTopology`가 파생한다.
RuntimeSlot = Literal[
    "map_api",
    "map_ui",
    "map_dagster",
    "map_dagster_daemon",
    "pinvi_api",
    "pinvi_web",
    "pinvi_dagster",
]
RUNTIME_SLOTS: Final[tuple[RuntimeSlot, ...]] = (
    "map_api",
    "map_ui",
    "map_dagster",
    "map_dagster_daemon",
    "pinvi_api",
    "pinvi_web",
    "pinvi_dagster",
)
#: Manager가 compose `build`로 만드는 slot. Map API·Dagster 이미지는 Map builder가 만든다.
COMPOSE_BUILT_RUNTIME_SLOTS: Final[tuple[RuntimeSlot, ...]] = (
    "map_ui",
    "pinvi_api",
    "pinvi_web",
    "pinvi_dagster",
)
#: Map builder가 짝으로 만드는 slot.
MAP_PAIRED_BUILD_SLOTS: Final[tuple[RuntimeSlot, ...]] = ("map_api", "map_dagster")
#: candidate tag가 붙는 slot — Map builder 짝과 compose build.
CANDIDATE_TAG_SLOTS: Final[tuple[RuntimeSlot, ...]] = (
    "map_api",
    "map_ui",
    "map_dagster",
    "pinvi_api",
    "pinvi_web",
    "pinvi_dagster",
)

MAP_API_SERVICE: Final = "kor-travel-map-api"
MAP_UI_SERVICE: Final = "kor-travel-map-ui"
PINVI_API_SERVICE: Final = "pinvi-api"
PINVI_WEB_SERVICE: Final = "pinvi-web"
MAP_TARGET: Final = "map"
PINVI_TARGET: Final = "pinvi"


def slot_project(slot: RuntimeSlot) -> Literal["map", "pinvi"]:
    """slot의 source project. 이미지 revision 대조가 이것으로 고른다."""

    return "map" if slot.startswith("map_") else "pinvi"


@dataclass(frozen=True)
class RuntimeTopology:
    """slot → 그 slot 이미지로 도는 compose 서비스(없으면 ``None``).

    Dagster slot만 스위치를 따른다: ``map_dagster``·``pinvi_dagster``는 family의 carrier,
    ``map_dagster_daemon``은 family의 active daemon이다(`shared`면 없다).
    """

    slot_services: Mapping[RuntimeSlot, str | None]
    families: Mapping[str, DagsterFamily]

    def service(self, slot: RuntimeSlot) -> str | None:
        return self.slot_services[slot]

    def require_service(self, slot: RuntimeSlot) -> str:
        service = self.slot_services[slot]
        if service is None:
            raise DeploymentContractError(f"pinned runtime slot {slot} has no service")
        return service

    def services_for(self, slots: Sequence[RuntimeSlot]) -> tuple[str, ...]:
        """slot 순서대로, 서비스가 있는 것만."""

        return tuple(
            service for slot in slots if (service := self.slot_services[slot]) is not None
        )

    @property
    def runtime_services(self) -> tuple[str, ...]:
        """오늘 떠 있어야 할 slot 서비스(slot 순서)."""

        return self.services_for(RUNTIME_SLOTS)

    @property
    def retired_services(self) -> tuple[str, ...]:
        """`shared` family의 옛 서비스 — 어떤 실행 집합에도 없어야 한다."""

        return tuple(name for family in self.families.values() for name in family.retired)


def runtime_topology(families: Mapping[str, DagsterFamily] | None = None) -> RuntimeTopology:
    """설치된 모델(또는 주어진 family)에서 slot 서비스를 파생한다."""

    if families is None:
        # Map·PinVi만 파생한다 — 다른 target의 모양은 pinned runtime을 막지 못한다.
        families = {target: dagster_family(target) for target in (MAP_TARGET, PINVI_TARGET)}
    for target in (MAP_TARGET, PINVI_TARGET):
        if target not in families:
            raise DeploymentContractError(f"Dagster target {target} is not declared")
    map_family = families[MAP_TARGET]
    pinvi_family = families[PINVI_TARGET]
    return RuntimeTopology(
        slot_services=MappingProxyType(
            {
                "map_api": MAP_API_SERVICE,
                "map_ui": MAP_UI_SERVICE,
                "map_dagster": map_family.carrier,
                "map_dagster_daemon": map_family.active_daemon,
                "pinvi_api": PINVI_API_SERVICE,
                "pinvi_web": PINVI_WEB_SERVICE,
                "pinvi_dagster": pinvi_family.carrier,
            }
        ),
        families=families,
    )


# ── 지연 컨테이너 ─────────────────────────────────────────────────────────

_T = TypeVar("_T")
_K = TypeVar("_K")
_V = TypeVar("_V")


class LazySequence(Sequence[_T]):
    """최초 실제 접근 때 ``loader``를 부르는 읽기 전용 tuple. 매 접근마다 다시 부른다(loader가 캐시한다)."""

    def __init__(self, loader: Callable[[], Sequence[_T]]) -> None:
        self._loader = loader

    def __getitem__(self, index: Any) -> Any:
        return tuple(self._loader())[index]

    def __iter__(self) -> Iterator[_T]:
        return iter(tuple(self._loader()))

    def __len__(self) -> int:
        return len(self._loader())

    def __repr__(self) -> str:
        return repr(tuple(self._loader()))


class LazySet(Set[_T]):
    """최초 실제 접근 때 ``loader``를 부르는 읽기 전용 frozenset."""

    def __init__(self, loader: Callable[[], Set[_T]]) -> None:
        self._loader = loader

    def _resolve(self) -> frozenset[_T]:
        return frozenset(self._loader())

    @classmethod
    def _from_iterable(cls, it: Any) -> frozenset[Any]:
        # `Set`의 `|`·`&`·`-`가 결과를 만드는 자리 — 결과는 평범한 frozenset이다.
        return frozenset(it)

    def __contains__(self, item: object) -> bool:
        return item in self._resolve()

    def __iter__(self) -> Iterator[_T]:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def difference(self, *others: Any) -> frozenset[_T]:
        return self._resolve().difference(*others)

    def union(self, *others: Any) -> frozenset[_T]:
        return self._resolve().union(*others)

    def __repr__(self) -> str:
        return repr(self._resolve())


class LazyMapping(Mapping[_K, _V]):
    """최초 실제 접근 때 ``loader``를 부르는 읽기 전용 dict."""

    def __init__(self, loader: Callable[[], Mapping[_K, _V]]) -> None:
        self._loader = loader

    def __getitem__(self, key: _K) -> _V:
        return self._loader()[key]

    def __iter__(self) -> Iterator[_K]:
        return iter(self._loader())

    def __len__(self) -> int:
        return len(self._loader())

    def __repr__(self) -> str:
        return repr(dict(self._loader()))
