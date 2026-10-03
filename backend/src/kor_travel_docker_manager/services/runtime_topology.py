"""Dagster family와 pinned runtime slot의 서비스 이름을 **렌더된 모델에서** 파생한다(ADR-54).

공용 Dagster 제어 평면(ADR-54) 전환은 target의 옛 webserver·daemon(과 그것에 기대는 gateway)을
`profiles: [legacy-dagster]`로 내리고 code-server만 공용 plane에 합류시킨다. 그 서비스 이름을 코드가
literal로 들고 있으면 전환된 target에서 frozen render(`--profile bootstrap`만)는 그 서비스를 모르는데
pinned 재구축은 여전히 빌드·`up`·검사하려 하고, 명시적 `up <서비스>`는 꺼진 profile의 서비스도 띄워
옛 daemon이 되살아난다. 그래서 이름은 여기서 한 번 파생한다.

- **모양으로 찾는다.** code-server는 target의 `services` 중 `dagster code-server start`(또는 옛
  `dagster api grpc`)를 실행하는 서비스, 옛 webserver·daemon은 `dagster-webserver`/`dagster-daemon`을 실행하면서 그 code-server에 `depends_on`하는
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


#: 장기 실행 code-server의 두 모양. `code-server start`(proxy + 자식 gRPC)만 location reload에 definitions를
#: 다시 import한다 — `api grpc`는 reload를 경고만 남기고 무시한다(공용 plane 규칙은 테스트가 고정한다).
CODE_SERVER_SUBCOMMANDS: Final = (("code-server", "start"), ("api", "grpc"))


def code_server_subcommand(service: Mapping[str, Any]) -> tuple[str, str] | None:
    """서비스가 `dagster <하위 명령>`으로 code-server를 띄우면 그 하위 명령, 아니면 None."""

    argv = _words(service.get("command")) + _words(service.get("entrypoint"))
    for index, word in enumerate(argv):
        pair = tuple(argv[index + 1 : index + 3])
        if word.rsplit("/", 1)[-1] == "dagster" and pair in CODE_SERVER_SUBCOMMANDS:
            return pair[0], pair[1]
    return None


def _is_code_server(service: Mapping[str, Any]) -> bool:
    return code_server_subcommand(service) is not None


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


def _volume_source(volume: object) -> str:
    return str(volume.get("source") if isinstance(volume, Mapping) else str(volume).split(":", 1)[0])


def _is_shared_workspace_source(source: str) -> bool:
    """reference compose의 상대 경로, 또는 frozen render(`compose config`)가 푼 절대 경로."""

    return source == _SHARED_WORKSPACE_SOURCE or source.endswith(
        "/" + _SHARED_WORKSPACE_SOURCE.removeprefix("./")
    )


def _mounts_shared_workspace(service: Mapping[str, Any]) -> bool:
    return any(
        _is_shared_workspace_source(_volume_source(volume))
        for volume in service.get("volumes") or []
    )


def shared_workspace_source(service: Mapping[str, Any]) -> str:
    """plane 서비스가 붙인 파생 workspace의 호스트 경로(렌더에 적힌 그대로). 하나가 아니면 거부한다."""

    sources = {
        _volume_source(volume)
        for volume in service.get("volumes") or []
        if _is_shared_workspace_source(_volume_source(volume))
    }
    if len(sources) != 1:
        raise DeploymentContractError("shared plane service must mount exactly one workspace")
    return next(iter(sources))


def workspace_location_names(document: object) -> tuple[str, ...]:
    """파생 workspace(`load_from: [grpc_server: {location_name}]`)의 location 이름. 모양이 다르면 거부한다."""

    entries = document.get("load_from") if isinstance(document, Mapping) else None
    if not isinstance(entries, list):
        raise DeploymentContractError("shared workspace has no load_from list")
    names: list[str] = []
    for entry in entries:
        server = entry.get("grpc_server") if isinstance(entry, Mapping) else None
        name = server.get("location_name") if isinstance(server, Mapping) else None
        if not isinstance(name, str) or not name:
            raise DeploymentContractError("shared workspace entry has no location_name")
        names.append(name)
    return tuple(names)


def _flag(argv: Sequence[str], *names: str) -> str | None:
    """`--name value` 또는 `--name=value`(click이 둘 다 받는다). code-server probe의 reaper도 같은 규칙이다."""
    for index, word in enumerate(argv):
        for name in names:
            if word == name and index + 1 < len(argv):
                return argv[index + 1]
            if word.startswith(name + "="):
                return word[len(name) + 1 :]
    return None


def code_server_location_name(service: Mapping[str, Any]) -> str:
    """code-server(`dagster code-server start`·`api grpc`)가 싣는 location.

    `--location-name`, 없으면 `-m` 모듈(workspace 파생과 같은 규칙)."""

    argv = _words(service.get("command")) + _words(service.get("entrypoint"))
    name = _flag(argv, "--location-name", "-l") or _flag(argv, "-m", "--module-name")
    if not name:
        raise DeploymentContractError("code-server command names no code location")
    return name


def listen_address(service: Mapping[str, Any]) -> tuple[str, int]:
    """서비스 command의 `-h`·`-p`(webserver가 듣는 주소). literal 포트가 아니면 거부한다."""

    argv = _words(service.get("command"))
    host = _flag(argv, "-h", "--host") or "127.0.0.1"
    port = _flag(argv, "-p", "--port")
    if port is None or not port.isdigit():
        raise DeploymentContractError("shared webserver has no literal listen port")
    return ("127.0.0.1" if host in {"0.0.0.0", "::"} else host), int(port)


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
            if isinstance(services.get(name), Mapping) and _is_code_server(services[name])
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


# ── 공용 Dagster plane ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SharedDagsterPlane:
    """공용 plane의 daemon·webserver — 파생 workspace를 붙인 활성 서비스다(이름이 아니라 모양)."""

    daemon: str
    webserver: str

    @property
    def services(self) -> tuple[str, str]:
        """plane을 다시 맞출 때 부르는 순서 — webserver가 먼저 location을 싣고, daemon이 뒤따른다."""

        return (self.webserver, self.daemon)


def derive_shared_dagster_plane(compose: Mapping[str, Any]) -> SharedDagsterPlane:
    """공용 workspace를 붙인 활성(profile 없는) 서비스 중 `dagster-daemon`·`dagster-webserver`를 실행하는 것."""

    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("Dagster topology documents are invalid")
    plane = {
        str(name): service
        for name, service in services.items()
        if isinstance(service, Mapping)
        and not service.get("profiles")
        and _mounts_shared_workspace(service)
    }

    def runners(program: str) -> set[str]:
        return {name for name, service in plane.items() if _runs(service, program)}

    return SharedDagsterPlane(
        daemon=_one(runners("dagster-daemon"), what="shared daemon", target="shared plane"),
        webserver=_one(
            runners("dagster-webserver"), what="shared webserver", target="shared plane"
        ),
    )


def installed_shared_dagster_plane() -> SharedDagsterPlane:
    """설치된 release의 공용 plane(매번 파생 — 설치가 바뀌어도 옛 값을 들고 있지 않는다)."""

    return derive_shared_dagster_plane(_installed_documents()[0])


def installed_code_location(family: DagsterFamily) -> str:
    """설치된 release에서 이 family의 code-server가 싣는 location(다른 target의 모양은 보지 않는다)."""

    services = _installed_documents()[0].get("services")
    service = services.get(family.code_server) if isinstance(services, Mapping) else None
    if not isinstance(service, Mapping):
        raise DeploymentContractError(f"code-server {family.code_server} is not declared")
    return code_server_location_name(service)


def installed_location_owners() -> Mapping[str, DagsterFamily]:
    """설치된 release의 location 이름 → 그 location을 싣는 target의 family(모든 Dagster target, 스위치와 무관).

    공용 plane이 싣는 location마다 그 target의 옛(plane 밖) webserver·daemon이 멈춰 있는지 보는 데 쓴다 — target
    이름을 적지 않는 한 규칙이다(Map·PinVi만이 아니라 앞으로 합류할 target까지).
    """

    compose, targets = _installed_documents()
    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("Dagster topology documents are invalid")
    owners: dict[str, DagsterFamily] = {}
    for family in derive_dagster_families(compose, targets).values():
        location = code_server_location_name(services[family.code_server])
        if location in owners:
            raise DeploymentContractError(f"code location {location} is served by two targets")
        owners[location] = family
    return MappingProxyType(owners)


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

    @property
    def shared_dagster_slots(self) -> tuple[RuntimeSlot, ...]:
        """공용 plane에 합류한 target의 carrier slot(slot 순서). 모두 `own`이면 비어 있다."""

        return tuple(
            slot
            for slot, target in (("map_dagster", MAP_TARGET), ("pinvi_dagster", PINVI_TARGET))
            if self.families[target].shared
        )


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
