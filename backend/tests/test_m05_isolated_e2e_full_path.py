"""M05 isolated one-shot driver의 **성공 경로 전 구간**을 실행 없이 완주시키는 시뮬레이션.

목적은 하나다: ``scripts/m05_isolated_e2e.py``의 ``main()``이 지금까지 "한 번도
실행된 적 없는 경로"(ledger claim 이후 ~ passed receipt까지)를 격리 run 1회
(1~2시간 + pinset 소각) 없이 걷게 만드는 것.

설계
----
* **fake docker**: driver가 **실제로 쓴** compose/override 파일을 읽어 Compose
  병합(``!reset``/``!override``)·profile 가시성·``${VAR}`` 보간·short→long port
  정규화를 흉내 내는 미니 렌더러를 갖는다. container/image/network inspect 출력은
  렌더 결과에서 **파생**되므로 잘못된 override가 inspect 단언에서 실제로 터진다.
* **fake HTTP**: Map ``packages/kor-travel-map-api/openapi.json``과 PinVi
  ``apps/api/app/schemas/*``의 실제 필드명·형태를 따른다. 승인 응답 ``feature_id``는
  UUID, creation-provenance ``feature_id``는 opaque TEXT다(e2e15 클래스).
* **fake attestation**: ``sys.executable`` 호출을 가로채 evidence 파일을 실제로
  쓰고, PinVi attestation이 요구하는 결박(provenance ↔ execution, receipt ↔ live
  env)을 검사한다.

전제: POSIX + ``O_NOFOLLOW``. driver는 ``os.open(..., O_NOFOLLOW|O_DIRECTORY)``로만
private leaf를 쓴다.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
import yaml

from kor_travel_docker_manager.services.pinned_runtime_release import (
    current_pinned_runtime_release,
)
from kor_travel_docker_manager.services.runtime_execution_identity import (
    ExecutionIdentityV6,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="driver의 private leaf 쓰기는 POSIX O_NOFOLLOW/O_DIRECTORY를 요구한다",
)

PINNED_RUNTIME_RELEASE = current_pinned_runtime_release()
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER_PATH = _REPO_ROOT / "scripts/m05_isolated_e2e.py"
_LAUNCHER_PATH = _REPO_ROOT / "scripts/run-m05-isolated-e2e-once"

MANAGER_REVISION = "3c" * 20
#: 일회용 체크아웃이 대조해야 할 PinVi 핀 tree. preflight가 materialize된
#: 소스에서 그대로 낸 값이며, 같은 bare에서 다시 유도하면 자기참조가 된다.
PINVI_SOURCE_TREE = "5d" * 20
PLAYWRIGHT_PINNED_VERSION = "1.62.1"

#: 승인 응답(UUID 정본)과 M02 creation-provenance의 opaque TEXT feature_id는
#: **다른 값**이다. e2e15는 이 둘을 같은 것으로 취급해 dedup 프로시저의 NOT FOUND를
#: eligibility 위반으로 위장하게 만들었다.
MANUAL_FEATURE_UUID = "9f1d4d2e-5b0c-4f6a-9d3b-1a2c3d4e5f60"
MANUAL_FEATURE_TEXT_ID = "manual:m05-isolated:0001"
PROVIDER_FEATURE_ID = "kto-festival:2026:0007"
PROVIDER_FEATURE_UUID = "60718293-a4b5-4c6d-8e9f-0a1b2c3d4e5f"
CASE_ID = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
RESOLUTION_ID = "2c3d4e5f-6071-4b8c-9dae-1f2a3b4c5d6e"
EVENT_ID = "3d4e5f60-7182-4c9d-aebf-2a3b4c5d6e7f"
REQUEST_ID = "4e5f6071-8293-4dae-bfc0-3b4c5d6e7f80"
TRIP_ID = "5f607182-93a4-4ebf-c0d1-4c5d6e7f8091".replace("c0d1", "80d1")
POI_ID = "60718293-a4b5-4fc0-91e2-5d6e7f809102"
USER_ID = "5f607182-93a4-4ebf-8c0d-4c5d6e7f8091"
IMPACT_COUNT = 3


class _HarnessBug(BaseException):
    """fake 내부 계약 위반.

    driver의 ``except Exception`` 경계는 **모든** ordinary exception을 고정 phase로
    수렴시킨다. fake의 버그가 그 경계에 걸리면 driver 결함으로 위장되므로,
    BaseException으로 올려 테스트가 원문 traceback과 함께 즉시 실패하게 한다.
    """


def _driver(*mutations: str) -> ModuleType:
    """driver를 fresh module로 로드한다.

    ``mutations``는 ``(old, new)`` 쌍이다 — 히스토리 결함을 **소스 수준에서**
    되살려 이 하네스가 실제로 잡는지 확인할 때만 쓴다. 대상이 유일하지 않으면
    즉시 실패해 mutation이 조용히 무효가 되는 것을 막는다.
    """

    source = _DRIVER_PATH.read_text(encoding="utf-8")
    for index in range(0, len(mutations), 2):
        old, new = mutations[index], mutations[index + 1]
        if source.count(old) != 1:
            raise AssertionError(f"driver mutation target is not unique: {old!r}")
        source = source.replace(old, new)
    module = ModuleType("m05_isolated_e2e_driver")
    module.__file__ = str(_DRIVER_PATH)
    exec(compile(source, str(_DRIVER_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


def _hex64(*parts: str) -> str:
    return hashlib.sha256("/".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Compose 미니 렌더러
# ---------------------------------------------------------------------------


class _Tagged:
    """``!reset`` / ``!override`` 같은 Compose 전용 YAML 태그 노드."""

    __slots__ = ("tag", "value")

    def __init__(self, tag: str, value: object) -> None:
        self.tag = tag
        self.value = value


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_tagged(loader: Any, _tag_suffix: str, node: yaml.Node) -> _Tagged:
    if isinstance(node, yaml.MappingNode):
        value: object = loader.construct_mapping(node, deep=True)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_scalar(node)
    return _Tagged(node.tag, value)


_ComposeLoader.add_multi_constructor("!", _construct_tagged)


def _reset_value(value: object) -> object:
    """``!reset``은 **중첩 값을 통째로 버린다**(e2e4 실측: 렌더 결과 ``default: null``)."""

    if isinstance(value, dict):
        return {key: None for key in value}
    if isinstance(value, list):
        return []
    return None


def _merge(base: object, overlay: object) -> object:
    if isinstance(overlay, _Tagged):
        if overlay.tag == "!reset":
            return _reset_value(overlay.value)
        if overlay.tag == "!override":
            return overlay.value
        raise AssertionError(f"unsupported compose tag: {overlay.tag}")
    if isinstance(overlay, dict):
        base_map = base if isinstance(base, dict) else {}
        merged: dict[str, object] = {
            key: value for key, value in base_map.items() if key not in overlay
        }
        for key, value in overlay.items():
            merged[key] = _merge(base_map.get(key), value)
        return merged
    if isinstance(overlay, list):
        base_list = base if isinstance(base, list) else []
        return [*base_list, *(_merge(None, item) for item in overlay)]
    return overlay


_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(value: object, environment: dict[str, str]) -> object:
    if isinstance(value, str):
        return _VARIABLE.sub(
            lambda match: environment.get(match.group(1)) or (match.group(2) or ""),
            value,
        )
    if isinstance(value, dict):
        return {key: _interpolate(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, environment) for item in value]
    return value


def _normalise_ports(ports: object) -> list[dict[str, object]]:
    """Compose ``config --format json``의 long syntax 투영."""

    result: list[dict[str, object]] = []
    for port in ports if isinstance(ports, list) else []:
        if isinstance(port, dict):
            result.append(dict(port))
            continue
        text = str(port)
        protocol = "tcp"
        if "/" in text:
            text, protocol = text.rsplit("/", 1)
        parts = text.split(":")
        if len(parts) == 3:
            host_ip, published, target = parts
        elif len(parts) == 2:
            host_ip, published, target = "", parts[0], parts[1]
        else:
            host_ip, published, target = "", "", parts[0]
        entry: dict[str, object] = {
            "mode": "ingress",
            "protocol": protocol,
            "target": int(target),
        }
        if published:
            entry["published"] = str(published)
        if host_ip:
            entry["host_ip"] = host_ip
        result.append(entry)
    return result


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value
    return values


class _ComposeModel:
    def __init__(
        self, *, project: str, files: tuple[Path, ...], environment: dict[str, str]
    ) -> None:
        document: Any = {}
        for item in files:
            loaded = yaml.load(item.read_text(encoding="utf-8"), Loader=_ComposeLoader)
            document = _merge(document, loaded or {})
        document = _interpolate(document, environment)
        self.project = project
        self.services: dict[str, dict[str, Any]] = dict(document.get("services") or {})
        self.networks: dict[str, dict[str, Any]] = dict(document.get("networks") or {})
        self.volumes: dict[str, Any] = dict(document.get("volumes") or {})
        for name, service in self.services.items():
            service["ports"] = _normalise_ports(service.get("ports"))
            if not service.get("image"):
                service["image"] = f"{project}-{name}"

    @property
    def declared_profiles(self) -> tuple[str, ...]:
        found: set[str] = set()
        for service in self.services.values():
            for profile in service.get("profiles") or ():
                found.add(str(profile))
        return tuple(sorted(found))

    def visible(self, profiles: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        """프로파일 소속 서비스는 그 프로파일이 켜졌을 때만 보인다."""

        enabled = set(profiles)
        return {
            name: service
            for name, service in self.services.items()
            if not service.get("profiles") or enabled & set(service["profiles"])
        }

    def network_name(self, key: str) -> str:
        entry = self.networks.get(key) or {}
        return str(entry.get("name") or f"{self.project}_{key}")

    def network_is_external(self, key: str) -> bool:
        return bool((self.networks.get(key) or {}).get("external"))

    def service_networks(self, name: str) -> dict[str, dict[str, Any]]:
        declared = self.services[name].get("networks")
        if not isinstance(declared, dict) or not declared:
            return {"default": {}}
        return {
            key: (value if isinstance(value, dict) else {}) for key, value in declared.items()
        }


# ---------------------------------------------------------------------------
# fake docker host
# ---------------------------------------------------------------------------


class _FakeContainer:
    def __init__(
        self,
        *,
        container_id: str,
        project: str,
        service: str,
        image_reference: str,
        image_id: str,
        labels: dict[str, str],
        networks: dict[str, dict[str, str]],
        ports: dict[str, Any],
        network_mode: str,
    ) -> None:
        self.container_id = container_id
        self.project = project
        self.service = service
        self.image_reference = image_reference
        self.image_id = image_id
        self.labels = labels
        self.networks = networks
        self.ports = ports
        self.network_mode = network_mode

    def inspect(self) -> dict[str, Any]:
        return {
            "Id": self.container_id,
            "Image": self.image_id,
            "Config": {"Labels": dict(self.labels)},
            "HostConfig": {"NetworkMode": self.network_mode},
            "NetworkSettings": {
                "Networks": {name: dict(value) for name, value in self.networks.items()},
                "Ports": dict(self.ports),
            },
            "State": {"Running": True, "Status": "running"},
        }


class _FakeDockerHost:
    """driver가 부르는 모든 외부 명령의 스텁 — 인자·순서를 전부 기록한다."""

    def __init__(self, *, map_root: Path, pinvi_root: Path, runner_image: str) -> None:
        self.map_root = map_root
        self.pinvi_root = pinvi_root
        #: 실행이 실제로 쓰는 일회용 체크아웃. 봉인된 `pinvi_root`와 **달라야** 한다 —
        #: 같으면 이 파일의 모든 실행-루트 단언이 수정을 되돌려도 통과한다.
        self.pinvi_run_root: Path | None = None
        #: (함수, role, destination) — 제거·사후 봉인검사가 실제로 불렸는지 본다.
        self.disposable_calls: list[tuple[str, str, Path]] = []
        self.runner_image = runner_image
        self.calls: list[dict[str, Any]] = []
        self.timeline: list[str] = []
        self.containers: dict[str, _FakeContainer] = {}
        self.networks: dict[str, dict[str, Any]] = {}
        self.volumes: dict[str, dict[str, Any]] = {}
        self.images: dict[str, dict[str, Any]] = {}
        self.attestations: dict[str, dict[str, str]] = {}
        self.fixture_arguments: list[str] = []
        self.image_id_references: list[str] = []
        #: 컨테이너는 cleanup에서 사라지므로 생성 시점 사실을 따로 보존한다.
        self.created: list[dict[str, Any]] = []
        self.saw_unbound_expose_metadata = False
        self.counter = 0
        # 회귀 재현용 스위치
        self.playwright_driver_version = PLAYWRIGHT_PINNED_VERSION
        self.map_api_extra_binding = False
        self.expected_admission: dict[str, object] | None = None
        self.http: _FakeHttpService | None = None
        # 호스트에 이미 있던 bridge network — `_map_network_addresses`가 읽는다.
        self.networks["bridge"] = {
            "Id": _hex64("preexisting", "bridge"),
            "Name": "bridge",
            "Driver": "bridge",
            "Internal": False,
            "Labels": {},
            "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]},
            "project": None,
            "assigned": {},
        }
        self.images[runner_image] = {
            "Id": "sha256:" + _hex64("playwright", "runner"),
            "Config": {"Labels": {}},
            "expose": (),
        }

    # -- 공통 --------------------------------------------------------------

    def _fail(
        self,
        driver: ModuleType,
        returncode: int,
        message: str,
        diagnostics: dict[int, str] | None = None,
    ) -> Any:
        diagnostic = diagnostics.get(returncode) if diagnostics else None
        raise driver._PhaseError(
            "runtime_command_failed",
            diagnostic=diagnostic,
            returncode=returncode,
            stderr=message.encode("utf-8"),
        )

    def _next_id(self, *parts: str) -> str:
        self.counter += 1
        return _hex64(*parts, str(self.counter))

    def _model(self, project: str, env_file: Path, files: tuple[Path, ...]) -> _ComposeModel:
        return _ComposeModel(project=project, files=files, environment=_read_env_file(env_file))

    @staticmethod
    def _revision_label(environment: dict[str, str]) -> str:
        return environment.get("KOR_TRAVEL_MAP_GIT_COMMIT") or environment.get(
            "PINVI_SOURCE_REVISION", ""
        )

    def _ensure_image(
        self, reference: str, *, revision: str | None, expose: tuple[int, ...] = ()
    ) -> dict[str, Any]:
        existing = self.images.get(reference)
        if existing is not None and revision is None:
            return existing
        labels = {"org.opencontainers.image.revision": revision} if revision else {}
        record = {
            "Id": "sha256:" + _hex64("image", reference, revision or "pulled"),
            "Config": {"Labels": labels},
            "expose": expose,
        }
        self.images[reference] = record
        return record

    # -- 진입점 ------------------------------------------------------------

    def command(
        self,
        driver: ModuleType,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture: bool = False,
        failure_exit_diagnostics: dict[int, str] | None = None,
        capture_failure_stderr: bool = False,
        capture_output_limit: int | None = None,
    ) -> str:
        try:
            return self._dispatch(
                driver,
                args,
                cwd=cwd,
                env=env,
                capture=capture,
                failure_exit_diagnostics=failure_exit_diagnostics,
                capture_failure_stderr=capture_failure_stderr,
                capture_output_limit=capture_output_limit,
            )
        except (driver._PhaseError, _HarnessBug):
            raise
        except Exception as error:  # noqa: BLE001 - fake 내부 결함을 driver phase로 위장시키지 않는다
            raise _HarnessBug(f"fake docker host failed on {args!r}: {error!r}") from error

    def _dispatch(
        self,
        driver: ModuleType,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
        capture: bool,
        failure_exit_diagnostics: dict[int, str] | None,
        capture_failure_stderr: bool,
        capture_output_limit: int | None,
    ) -> str:
        del capture, capture_failure_stderr, capture_output_limit
        self.calls.append(
            {
                "args": tuple(args),
                "cwd": None if cwd is None else str(cwd),
                "env": dict(env or {}),
            }
        )
        head = args[0]
        if head == "/usr/bin/ss":
            return ""
        if head == "/usr/bin/openssl":
            return self._openssl(args)
        if head == "/usr/bin/git":
            return ""
        if head == "/usr/bin/docker":
            return self._docker(
                driver, args, cwd=cwd, env=env or {}, diagnostics=failure_exit_diagnostics
            )
        if head.endswith("scripts/docker-app.sh"):
            return self._docker_app(driver, args, env=env or {})
        if head == sys.executable:
            return self._attestation(driver, args, cwd=cwd, env=env or {})
        raise AssertionError(f"unexpected external command: {args!r}")

    # -- openssl -----------------------------------------------------------

    def _openssl(self, args: tuple[str, ...]) -> str:
        assert args[1:4] == ("genpkey", "-algorithm", "Ed25519")
        target = Path(args[args.index("-out") + 1])
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
        finally:
            os.close(descriptor)
        return ""

    # -- docker ------------------------------------------------------------

    def _docker(
        self,
        driver: ModuleType,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: dict[str, str],
        diagnostics: dict[int, str] | None,
    ) -> str:
        del env, diagnostics
        verb = args[1]
        if verb == "compose":
            return self._compose(driver, args[2:], cwd=cwd)
        if verb == "pull":
            self.timeline.append("image:pull")
            self._ensure_image(args[2], revision=None)
            return ""
        if verb == "ps":
            return self._ps_all(args)
        if verb == "network":
            return self._network(driver, args)
        if verb == "volume":
            return self._volume(args)
        if verb == "container":
            return self._container_inspect(driver, args)
        if verb == "image":
            return self._image(driver, args)
        if verb == "run":
            return self._run(driver, args)
        raise AssertionError(f"unexpected docker verb: {args!r}")

    def _ps_all(self, args: tuple[str, ...]) -> str:
        project = self._filter_project(args)
        return "\n".join(
            container.container_id
            for container in self.containers.values()
            if container.project == project
        )

    @staticmethod
    def _filter_project(args: tuple[str, ...]) -> str | None:
        for index, item in enumerate(args):
            if item == "--filter" and args[index + 1].startswith(
                "label=com.docker.compose.project="
            ):
                return args[index + 1].split("=", 2)[2]
        return None

    def _network(self, driver: ModuleType, args: tuple[str, ...]) -> str:
        action = args[2]
        if action == "ls":
            project = self._filter_project(args)
            if project is None:
                return "\n".join(entry["Id"] for entry in self.networks.values())
            return "\n".join(
                entry["Id"] for entry in self.networks.values() if entry["project"] == project
            )
        if action == "inspect":
            found = []
            for item in [token for token in args[3:] if not token.startswith("-")]:
                entry = next(
                    (
                        value
                        for value in self.networks.values()
                        if value["Id"] == item or value["Name"] == item
                    ),
                    None,
                )
                if entry is None:
                    self._fail(driver, 1, f"Error: No such network: {item}")
                assert entry is not None
                found.append(
                    {
                        key: value
                        for key, value in entry.items()
                        if key not in {"project", "assigned"}
                    }
                )
            return json.dumps(found)
        raise AssertionError(f"unexpected docker network call: {args!r}")

    def _volume(self, args: tuple[str, ...]) -> str:
        assert args[2] == "ls"
        project = self._filter_project(args)
        return "\n".join(
            entry["Id"] for entry in self.volumes.values() if entry["project"] == project
        )

    def _container_inspect(self, driver: ModuleType, args: tuple[str, ...]) -> str:
        assert args[2] == "inspect"
        container = self.containers.get(args[3])
        if container is None:
            self._fail(driver, 1, f"Error: No such container: {args[3]}")
        assert container is not None
        payload = container.inspect()
        if any(value is None for value in payload["NetworkSettings"]["Ports"].values()):
            # Docker는 이미지 EXPOSE를 binding 없는 항목으로 항상 나열한다(e2e12).
            self.saw_unbound_expose_metadata = True
        return json.dumps([payload])

    def _image(self, driver: ModuleType, args: tuple[str, ...]) -> str:
        assert args[2] == "inspect"
        rest = list(args[3:])
        format_value: str | None = None
        if "--format" in rest:
            index = rest.index("--format")
            format_value = rest[index + 1]
            del rest[index : index + 2]
        reference = rest[0]
        record = self.images.get(reference)
        if record is None:
            self._fail(driver, 1, f"Error response from daemon: No such image: {reference}")
        assert record is not None
        if format_value is not None:
            assert format_value == "{{.Id}}"
            self.timeline.append("image:id")
            self.image_id_references.append(reference)
            return record["Id"] + "\n"
        return json.dumps([{"Id": record["Id"], "Config": record["Config"]}])

    def _run(self, driver: ModuleType, args: tuple[str, ...]) -> str:
        rest = list(args[2:])
        entrypoint = rest[rest.index("--entrypoint") + 1] if "--entrypoint" in rest else ""
        if entrypoint == "/bin/cat":
            self.timeline.append("run:playwright-info")
            reference = rest[rest.index("--entrypoint") + 2]
            assert reference == self.runner_image
            assert rest[-1] == "/ms-playwright/.docker-info"
            if reference not in self.images:
                self._fail(driver, 125, f"Unable to find image '{reference}' locally")
            return json.dumps({"driverVersion": self.playwright_driver_version})
        return self._run_fixture(driver, rest)

    def _run_fixture(self, driver: ModuleType, rest: list[str]) -> str:
        self.timeline.append("run:fixture")
        network = rest[rest.index("--network") + 1]
        if not any(entry["Name"] == network for entry in self.networks.values()):
            self._fail(driver, 125, f"Error response from daemon: network {network} not found")
        env_file = Path(rest[rest.index("--env-file") + 1])
        assert env_file.exists(), "fixture는 driver가 쓴 runtime env-file을 그대로 받아야 한다"
        assert set(_read_env_file(env_file)) == {"KOR_TRAVEL_MAP_PG_DSN"}
        mount = rest[rest.index("--mount") + 1]
        source = mount.split("src=", 1)[1].split(",", 1)[0]
        assert Path(source).is_file(), "fixture 스크립트 bind mount 원본이 존재해야 한다"
        reference = rest[rest.index("--entrypoint") + 2]
        if reference not in self.images:
            self._fail(driver, 125, f"Unable to find image '{reference}' locally")
        manual_feature_id = rest[-1]
        self.fixture_arguments.append(manual_feature_id)
        # e2e15: dedup 프로시저는 opaque TEXT feature_id를 기대한다. UUID를 받으면
        # 후보 Feature 증명을 찾지 못해 NOT FOUND가 eligibility 위반으로 위장된다.
        if manual_feature_id != MANUAL_FEATURE_TEXT_ID:
            self._fail(
                driver,
                1,
                "ManualProviderDedupError: candidate Feature proof is not eligible",
            )
        return json.dumps(
            {
                "case_id": CASE_ID,
                "manual_feature_id": manual_feature_id,
                "provider_feature_id": PROVIDER_FEATURE_ID,
            }
        )

    # -- docker compose ----------------------------------------------------

    def _compose(self, driver: ModuleType, tokens: tuple[str, ...], *, cwd: Path | None) -> str:
        project = ""
        env_file: Path | None = None
        files: list[Path] = []
        profiles: list[str] = []
        rest: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--project-name":
                project, index = tokens[index + 1], index + 2
            elif token == "--env-file":
                env_file, index = Path(tokens[index + 1]), index + 2
            elif token == "--file":
                files.append(Path(tokens[index + 1]))
                index += 2
            elif token == "--profile":
                profiles.append(tokens[index + 1])
                index += 2
            else:
                rest.append(token)
                index += 1
        assert project and env_file is not None and files
        assert cwd is not None and Path(cwd).is_dir()
        model = self._model(project, env_file, tuple(files))
        environment = _read_env_file(env_file)
        tag = "map" if project.startswith("m05i-map-") else "pinvi"
        suffix = "{" + ",".join(sorted(profiles)) + "}" if profiles else ""
        subcommand = rest[0]
        if subcommand == "config":
            return self._compose_config(model, rest, tuple(profiles), tag=tag, suffix=suffix)
        if subcommand == "up":
            return self._compose_up(
                driver, model, rest, tuple(profiles), environment, tag=tag, suffix=suffix
            )
        if subcommand == "run":
            return self._compose_run(driver, model, rest, tuple(profiles), tag=tag, suffix=suffix)
        if subcommand == "ps":
            return self._compose_ps(model, rest, tuple(profiles), tag=tag, suffix=suffix)
        if subcommand == "down":
            return self._compose_down(model, tuple(profiles), tag=tag, suffix=suffix)
        raise AssertionError(f"unexpected compose subcommand: {rest!r}")

    def _compose_config(
        self,
        model: _ComposeModel,
        rest: list[str],
        profiles: tuple[str, ...],
        *,
        tag: str,
        suffix: str,
    ) -> str:
        if "--profiles" in rest:
            self.timeline.append(f"compose:{tag}:config-profiles")
            return "".join(f"{profile}\n" for profile in model.declared_profiles)
        assert rest[1:] == ["--format", "json"]
        self.timeline.append(f"compose:{tag}:config-json{suffix}")
        return json.dumps(
            {
                "name": model.project,
                "services": {
                    name: {
                        "image": service["image"],
                        "ports": service["ports"],
                        "networks": service.get("networks"),
                        "labels": service.get("labels", {}),
                        "profiles": service.get("profiles", []),
                    }
                    for name, service in model.visible(profiles).items()
                },
                "networks": model.networks,
                "volumes": model.volumes,
            }
        )

    def _ensure_networks(self, model: _ComposeModel) -> None:
        for key in model.networks or {"default": {}}:
            name = model.network_name(key)
            if model.network_is_external(key):
                assert any(
                    entry["Name"] == name for entry in self.networks.values()
                ), f"external network {name} must already exist"
                continue
            if any(entry["Name"] == name for entry in self.networks.values()):
                continue
            declared = model.networks.get(key) or {}
            labels = {str(k): str(v) for k, v in (declared.get("labels") or {}).items()}
            labels["com.docker.compose.project"] = model.project
            self.networks[name] = {
                "Id": self._next_id("network", name),
                "Name": name,
                "Driver": "bridge",
                "Internal": False,
                "Labels": labels,
                "IPAM": {"Config": list((declared.get("ipam") or {}).get("config") or [])},
                "project": model.project,
                "assigned": {},
            }
        for name in model.volumes or {}:
            scoped = f"{model.project}_{name}"
            self.volumes.setdefault(
                scoped,
                {
                    "Id": self._next_id("volume", scoped),
                    "Name": scoped,
                    "project": model.project,
                },
            )

    def _allocate_address(self, network_name: str, requested: object) -> str:
        entry = self.networks[network_name]
        assigned: dict[str, str] = entry["assigned"]
        if isinstance(requested, str) and requested:
            assigned[requested] = network_name
            return requested
        config = entry["IPAM"]["Config"]
        # Compose 모델은 소문자 `subnet`, docker inspect는 `Subnet`을 쓴다.
        declared = (config[0].get("subnet") or config[0].get("Subnet")) if config else None
        if not declared:
            candidate_pool = ipaddress.ip_network("172.18.0.0/24").hosts()
        else:
            candidate_pool = ipaddress.ip_network(str(declared), strict=False).hosts()
        for index, host in enumerate(candidate_pool):
            if index == 0:
                continue  # gateway
            candidate = str(host)
            if candidate not in assigned:
                assigned[candidate] = network_name
                return candidate
        raise AssertionError("IPAM pool exhausted")

    def _create_container(
        self, model: _ComposeModel, service_name: str, environment: dict[str, str], *, build: bool
    ) -> None:
        service = model.services[service_name]
        reference = str(service["image"])
        revision = self._revision_label(environment) if (build and service.get("build")) else None
        expose = (12701,) if service_name == "api" else ()
        record = self._ensure_image(reference, revision=revision, expose=expose)
        labels = dict(record["Config"]["Labels"])
        labels.update(
            {str(key): str(value) for key, value in (service.get("labels") or {}).items()}
        )
        labels["com.docker.compose.project"] = model.project
        labels["com.docker.compose.service"] = service_name
        attached: dict[str, dict[str, str]] = {}
        for key, options in model.service_networks(service_name).items():
            name = model.network_name(key)
            address = self._allocate_address(name, (options or {}).get("ipv4_address"))
            attached[name] = {"NetworkID": self.networks[name]["Id"], "IPAddress": address}
        ports: dict[str, Any] = {f"{port}/tcp": None for port in record.get("expose", ())}
        for port in service["ports"]:
            key = f"{port['target']}/{port.get('protocol', 'tcp')}"
            ports[key] = [
                {
                    "HostIp": str(port.get("host_ip", "0.0.0.0")),
                    "HostPort": str(port.get("published", "")),
                }
            ]
        if self.map_api_extra_binding and service_name == "api":
            ports["9464/tcp"] = [{"HostIp": "0.0.0.0", "HostPort": "9464"}]
        container_id = self._next_id("container", model.project, service_name)
        self.created.append(
            {
                "project": model.project,
                "service": service_name,
                "image_reference": reference,
                "labels": dict(labels),
                "addresses": {name: value["IPAddress"] for name, value in attached.items()},
                "published": {
                    key: list(value) for key, value in ports.items() if value is not None
                },
            }
        )
        self.containers[container_id] = _FakeContainer(
            container_id=container_id,
            project=model.project,
            service=service_name,
            image_reference=reference,
            image_id=record["Id"],
            labels=labels,
            networks=attached,
            ports=ports,
            network_mode=model.network_name("default"),
        )

    def _has_container(self, project: str, service: str) -> bool:
        return any(
            container.project == project and container.service == service
            for container in self.containers.values()
        )

    def _compose_up(
        self,
        driver: ModuleType,
        model: _ComposeModel,
        rest: list[str],
        profiles: tuple[str, ...],
        environment: dict[str, str],
        *,
        tag: str,
        suffix: str,
    ) -> str:
        build = "--build" in rest
        wanted = [token for token in rest[1:] if not token.startswith("-")]
        visible = model.visible(profiles)
        if not wanted:
            wanted = list(visible)
        self.timeline.append(f"compose:{tag}:up[{','.join(wanted)}]{suffix}")
        for name in wanted:
            if name not in visible:
                self._fail(driver, 1, f"no such service: {name}")
        self._ensure_networks(model)
        for name in wanted:
            if not self._has_container(model.project, name):
                self._create_container(model, name, environment, build=build)
        return ""

    def _compose_run(
        self,
        driver: ModuleType,
        model: _ComposeModel,
        rest: list[str],
        profiles: tuple[str, ...],
        *,
        tag: str,
        suffix: str,
    ) -> str:
        service = [token for token in rest[1:] if not token.startswith("-")][0]
        self.timeline.append(f"compose:{tag}:run[{service}]{suffix}")
        if service not in model.visible(profiles):
            self._fail(driver, 1, f"no such service: {service}")
        self._ensure_networks(model)
        return ""

    def _compose_ps(
        self,
        model: _ComposeModel,
        rest: list[str],
        profiles: tuple[str, ...],
        *,
        tag: str,
        suffix: str,
    ) -> str:
        service = [token for token in rest[1:] if not token.startswith("-")][0]
        self.timeline.append(f"compose:{tag}:ps[{service}]{suffix}")
        if service not in model.visible(profiles):
            # 프로파일 밖 서비스는 ps에 보이지 않는다 — driver가 `--profile`을
            # 빠뜨리면 빈 결과가 돌아온다(적대 리뷰: app-dagster 중복 조회).
            return "\n"
        for container in self.containers.values():
            if container.project == model.project and container.service == service:
                return container.container_id + "\n"
        return "\n"

    def _compose_down(
        self, model: _ComposeModel, profiles: tuple[str, ...], *, tag: str, suffix: str
    ) -> str:
        self.timeline.append(f"compose:{tag}:down{suffix}")
        visible = set(model.visible(profiles))
        declared = set(model.services)
        removable = [
            key
            for key, container in self.containers.items()
            if container.project == model.project
            # --remove-orphans는 **모델 밖** 컨테이너만 정리한다. 프로파일 밖
            # 서비스는 모델 안에 있으므로 그대로 남는다(e2e6 실측).
            and (container.service in visible or container.service not in declared)
        ]
        for key in removable:
            del self.containers[key]
        attached = {
            name for container in self.containers.values() for name in container.networks
        }
        for name in [
            name
            for name, entry in self.networks.items()
            if entry["project"] == model.project and name not in attached
        ]:
            del self.networks[name]
        for name in [
            name for name, entry in self.volumes.items() if entry["project"] == model.project
        ]:
            del self.volumes[name]
        return ""

    # -- PinVi docker-app.sh ----------------------------------------------

    def _docker_app(
        self, driver: ModuleType, args: tuple[str, ...], *, env: dict[str, str]
    ) -> str:
        action = args[1]
        self.timeline.append(f"docker-app:{action}")
        required = {
            "PINVI_ENV_FILE",
            "PINVI_DOCKER_PROJECT",
            "PINVI_SOURCE_REVISION",
            "PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE",
            "PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH",
            "PINVI_M05_PINSET_SHA256",
            "PINVI_M05_EXECUTION_IDENTITY_SHA256",
        }
        missing = required - set(env)
        if missing:
            self._fail(driver, 78, f"pinvi admission env is incomplete: {sorted(missing)}")
        admission = json.loads(
            Path(env["PINVI_M05_ISOLATED_MANAGER_ADMISSION_PATH"]).read_text(encoding="utf-8")
        )
        if self.expected_admission is not None and admission != self.expected_admission:
            self._fail(driver, 78, "pinvi isolated manager admission mismatch")
        env_file = Path(env["PINVI_ENV_FILE"])
        files = [self.pinvi_root / "infra/docker-compose.app.yml"]
        # e2e3: override를 넘기지 않으면 app-api가 Map network에 join하지 못한다.
        extra = env.get("PINVI_DOCKER_COMPOSE_EXTRA_FILE")
        if extra:
            files.append(Path(extra))
        project = env["PINVI_DOCKER_PROJECT"]
        model = self._model(project, env_file, tuple(files))
        environment = _read_env_file(env_file)
        self._ensure_networks(model)
        if action == "build":
            for _name, service in model.visible(()).items():
                if service.get("build"):
                    self._ensure_image(
                        str(service["image"]), revision=self._revision_label(environment)
                    )
            return ""
        assert action == "up"
        for name in ("app-postgres", "app-api", "app-web"):
            if not self._has_container(project, name):
                self._create_container(model, name, environment, build=True)
        return ""

    # -- PinVi m05_activation_attestation.py -------------------------------

    def _attestation(
        self,
        driver: ModuleType,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: dict[str, str],
    ) -> str:
        assert args[1] == "-I"
        # **봉인 트리가 아니라 일회용 체크아웃에서** 돈다. 러너는 저장소 루트를
        # 컨테이너에 root RW로 마운트하므로, 봉인 트리를 주면 다음 preflight가 같은
        # pinset 재실행을 거부한다(2026-09-03·04 연속 재현).
        assert self.pinvi_run_root is not None
        assert self.pinvi_run_root != self.pinvi_root
        assert args[2] == str(self.pinvi_run_root / "scripts/m05_activation_attestation.py")
        # attestation은 자기 `__file__`에서 repo root를 유도하고 러너의 repo root를
        # 무조건 덮어쓴다. 그래서 cwd까지 일회용 루트여야 체인 전체가 따라온다.
        assert cwd is not None and Path(cwd) == self.pinvi_run_root
        mode = args[3]
        self.timeline.append(f"attest:{mode}")
        options: dict[str, str] = {}
        tail: list[str] = []
        index = 4
        while index < len(args):
            token = args[index]
            if token == "--":
                tail = list(args[index + 1 :])
                break
            if (
                token.startswith("--")
                and index + 1 < len(args)
                and not args[index + 1].startswith("--")
            ):
                options[token] = args[index + 1]
                index += 2
                continue
            options[token] = ""
            index += 1
        self.attestations[mode] = options
        assert "--require-root-owned" in options
        assert options["--playwright-runner-image"] == self.runner_image
        assert options["--scope"] == "isolated"
        assert tail and tail[0] == str(
            self.pinvi_run_root / "scripts/n150-playwright-runner.sh"
        )
        if self.runner_image not in self.images:
            self._fail(driver, 1, f"playwright runner image is unavailable: {self.runner_image}")
        for key, value in options.items():
            if key.endswith("-container") and value not in self.containers:
                self._fail(driver, 1, f"container is not live: {key}={value}")
        evidence = Path(options["--evidence-dir"])
        if mode == "m04":
            assert env["PINVI_M04_LIVE_EMAIL"] and env["PINVI_M04_LIVE_PASSWORD"]
            target = evidence / "m04-attestation.json"
            payload: dict[str, object] = {
                "kind": "pinvi-m04-activation-attestation-v1",
                "feature_request_id": options["--feature-request-id"],
                "scope": "isolated",
            }
        else:
            assert mode == "live"
            provenance = json.loads(
                Path(options["--isolated-runtime-provenance"]).read_text(encoding="utf-8")
            )
            assert provenance["kind"] == "m05-isolated-runtime-provenance-v1"
            if (
                provenance["manager_source_revision"]
                != options["--isolated-manager-source-revision"]
                or provenance["pinset_sha256"] != options["--isolated-pinset-sha256"]
                or provenance["execution_identity_sha256"]
                != options["--isolated-execution-identity-sha256"]
            ):
                self._fail(driver, 1, "isolated runtime provenance is not bound to this execution")
            # PinVi attestation은 local receipt의 TEXT feature_id와 live env를 결박한다.
            assert self.http is not None
            receipt = self.http.receipt
            if (
                env.get("PINVI_M05_LIVE_OLD_FEATURE_ID") != receipt["old_feature_id"]
                or env.get("PINVI_M05_LIVE_REPLACEMENT_FEATURE_ID")
                != receipt["replacement_feature_id"]
                or env.get("PINVI_M05_LIVE_IMPACT_COUNT") != str(receipt["impact_count"])
            ):
                self._fail(driver, 1, "live reconciliation identity does not match the receipt")
            if options["--event-id"] != EVENT_ID or options["--map-case-id"] != CASE_ID:
                self._fail(driver, 1, "live attestation event binding is invalid")
            target = evidence / "attestation.json"
            payload = {
                "kind": "pinvi-m05-activation-attestation-v1",
                "event_id": options["--event-id"],
                "scope": "isolated",
            }
        raw = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, raw)
        finally:
            os.close(descriptor)
        return ""


# ---------------------------------------------------------------------------
# fake HTTP (Map admin + PinVi)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self, _amount: int | None = None) -> bytes:
        return self._payload


def _meta() -> dict[str, object]:
    return {"duration_ms": 3, "request_id": "m05-isolated", "page": None, "cluster": None}


def _envelope(data: dict[str, object]) -> _FakeResponse:
    return _FakeResponse(json.dumps({"data": data, "meta": _meta()}).encode("utf-8"))


class _FakeHttpService:
    """Map ``openapi.json`` / PinVi router의 실제 필드명·형태를 그대로 쓰는 스텁."""

    def __init__(self, *, bootstrap_path: Path) -> None:
        self.bootstrap_path = bootstrap_path
        self.timeline: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.map_ports: set[int] = set()
        self.pinvi_ports: set[int] = set()
        self.approved = False
        self.decided = False
        self.seeded_trip: str | None = None
        self.seeded_feature_id: str | None = None
        self.receipt: dict[str, Any] = {
            "event_id": EVENT_ID,
            "event_sequence": 1,
            "event_sha256": "a" * 64,
            "action": "rebind",
            "old_feature_id": MANUAL_FEATURE_TEXT_ID,
            "old_feature_uuid": MANUAL_FEATURE_UUID,
            "replacement_feature_id": PROVIDER_FEATURE_ID,
            "replacement_feature_uuid": PROVIDER_FEATURE_UUID,
            "impact_root_sha256": "b" * 64,
            "impact_count": IMPACT_COUNT,
            "receipt_sha256": "c" * 64,
            "applied_at": "2026-09-01T00:00:03Z",
        }

    def open(self, request: Any, timeout: float | None = None) -> _FakeResponse:
        try:
            return self._route(request, timeout)
        except (HTTPError, _HarnessBug):
            raise
        except Exception as error:  # noqa: BLE001 - fake 결함을 driver phase로 위장시키지 않는다
            raise _HarnessBug(
                f"fake http service failed on {request.full_url}: {error!r}"
            ) from error

    def _route(self, request: Any, timeout: float | None) -> _FakeResponse:
        parsed = urlsplit(request.full_url)
        assert parsed.hostname == "127.0.0.1"
        assert timeout is not None
        headers = {key.lower(): value for key, value in request.headers.items()}
        body = json.loads(request.data) if request.data else None
        path = parsed.path
        self.requests.append(
            {"method": request.get_method(), "path": path, "headers": headers, "body": body}
        )
        if path.startswith("/v1/admin/") or path == "/health":
            self.map_ports.add(int(parsed.port))
            return self._map(request, path, headers, body)
        self.pinvi_ports.add(int(parsed.port))
        return self._pinvi(request, path, body)

    # -- Map admin ---------------------------------------------------------

    def _map(
        self, request: Any, path: str, headers: dict[str, str], body: Any
    ) -> _FakeResponse:
        if path == "/health":
            self.timeline.append("map:health")
            return _FakeResponse(json.dumps({"status": "ok", "version": "300"}).encode("utf-8"))
        assert headers.get("x-kor-travel-map-admin-proxy-secret"), "admin proxy secret is required"
        assert headers.get("x-kor-travel-map-actor") == "m05-isolated-harness"
        if path == "/v1/admin/feature-reference-reconciliation-subscriptions":
            self.timeline.append("map:subscription")
            assert headers.get("idempotency-key")
            assert body == {"initial_event_sequence": 0}
            return _envelope(
                {
                    "outcome": "provisioned",
                    "principal_id": "service:feature-reference-reconciliation",
                    "initial_event_sequence": 0,
                }
            )
        if path == f"/v1/admin/feature-requests/{REQUEST_ID}/approve":
            self.timeline.append("map:approve")
            assert headers.get("idempotency-key")
            assert headers.get("x-kor-travel-map-admin-feature-create-token")
            assert body == {
                "category": "01070300",
                "marker_color": "P-01",
                "marker_icon": "marker",
            }
            self.approved = True
            # T-VN-32C: 승인 응답의 feature_id는 **UUID 정본**이다.
            return _envelope(
                {
                    "request_id": REQUEST_ID,
                    "status": "approved",
                    "kind": "place",
                    "name": "M05 isolated manual",
                    "coord": {"lon": 127.111111, "lat": 37.511111},
                    "categories": ["M05 isolated"],
                    "note": "M05 isolated signed E2E fixture",
                    "submitted_at": "2026-09-01T00:00:00Z",
                    "resolved_at": "2026-09-01T00:00:01Z",
                    "resolved_by_actor": "m05-isolated-harness",
                    "feature_id": MANUAL_FEATURE_UUID,
                    "rejection_reason": None,
                }
            )
        if path == f"/v1/admin/features/{MANUAL_FEATURE_UUID}/creation-provenance":
            self.timeline.append("map:creation-provenance")
            assert self.approved, "creation-provenance는 승인 이후에만 조회한다"
            # M02 provenance는 opaque TEXT feature_id와 UUID를 함께 싣는다.
            return _envelope(
                {
                    "feature_id": MANUAL_FEATURE_TEXT_ID,
                    "feature_uuid": MANUAL_FEATURE_UUID,
                    "claim": {
                        "feature_id": MANUAL_FEATURE_UUID,
                        "feature_kind": "place",
                        "name_key": "m05-isolated-manual",
                        "lon_e6": 127111111,
                        "lat_e6": 37511111,
                        "claim_basis": "manual_create",
                        "claimed_at": "2026-09-01T00:00:01Z",
                        "claimed_by_command_id": 11,
                    },
                    "origin": {
                        "origin_kind": "manual_request",
                        "creation_command_id": 11,
                        "creator_principal_id": "admin:m05-isolated-harness",
                        "created_by_actor": "m05-isolated-harness",
                        "created_at": "2026-09-01T00:00:01Z",
                        "invoker_role": "ktm_feature_api_runtime",
                        "procedure_definer": "ktm_feature_owner",
                    },
                }
            )
        case = f"/v1/admin/manual-provider-dedup-cases/{CASE_ID}"
        if path == case:
            self.timeline.append("map:case")
            return _envelope(
                {
                    "case_id": CASE_ID,
                    "status": "pending",
                    "created_at": "2026-09-01T00:00:02Z",
                    "evidence_fingerprint": "d" * 64,
                    "manual_feature": {
                        "feature_id": MANUAL_FEATURE_TEXT_ID,
                        "feature_uuid": MANUAL_FEATURE_UUID,
                        "row_revision": 1,
                    },
                    "provider_feature": {
                        "feature_id": PROVIDER_FEATURE_ID,
                        "feature_uuid": PROVIDER_FEATURE_UUID,
                        "row_revision": 1,
                    },
                    "scores": {"name": 0.98, "distance_m": 4.0},
                    "resolution": None,
                    "event": None,
                    "subscriptions": [],
                }
            )
        if path == f"{case}/decisions":
            self.timeline.append("map:decision")
            assert headers.get("idempotency-key")
            assert body == {
                "decision": "merged",
                "expected_case_fingerprint": "d" * 64,
                "expected_manual_row_revision": 1,
                "expected_provider_row_revision": 1,
                "survivor_feature_id": PROVIDER_FEATURE_ID,
                "reason": "M05 isolated signed E2E rebind",
            }
            self.decided = True
            return _envelope(
                {
                    "outcome": "merged",
                    "resolution_id": RESOLUTION_ID,
                    "event_id": EVENT_ID,
                    "manual_feature_id": MANUAL_FEATURE_TEXT_ID,
                    "manual_feature_row_revision": 2,
                }
            )
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    # -- PinVi -------------------------------------------------------------

    def _pinvi(self, request: Any, path: str, body: Any) -> _FakeResponse:
        if path == "/auth/login":
            self.timeline.append("pinvi:login")
            expected = json.loads(self.bootstrap_path.read_text(encoding="utf-8"))
            if body != {"email": expected["email"], "password": expected["password"]}:
                raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
            return _envelope(
                {
                    "user_id": USER_ID,
                    "email": expected["email"],
                    "nickname": "m05-isolated",
                    "avatar_url": None,
                    "avatar_kind": "default",
                    "has_avatar": False,
                    "status": "active",
                    "roles": ["user", "admin"],
                    "email_verified_at": "2026-09-01T00:00:00Z",
                    "has_password": True,
                    "oauth_identities": [],
                }
            )
        if path == "/trips":
            self.timeline.append("pinvi:seed-trip")
            assert body["title"], "seed trip에는 제목이 있어야 한다"
            self.seeded_trip = TRIP_ID
            return _envelope({"trip_id": TRIP_ID, "title": body["title"]})
        if path == f"/trips/{TRIP_ID}/pois":
            self.timeline.append("pinvi:seed-poi")
            # 리바인드가 고쳐 쓸 참조는 Map 결정 **전에** 있어야 한다.
            assert not self.decided, "seed는 Map 결정 이전이어야 한다"
            assert body["feature_id"], "seed POI는 feature 참조를 가져야 한다"
            self.seeded_feature_id = str(body["feature_id"])
            return _envelope(
                {
                    "attachment_id": POI_ID,
                    "day_index": body["day_index"],
                    "sort_order": body["sort_order"],
                    "feature_id": body["feature_id"],
                }
            )
        if path == "/features/requests":
            self.timeline.append("pinvi:feature-request")
            assert body["type"] == "new_place"
            assert body["kind"] == "place"
            assert body["coord_source"] == "map_pick"
            return _envelope(
                {
                    "request_id": REQUEST_ID,
                    "status": "pending",
                    "type": "new_place",
                    "kind": "place",
                    "title": body["title"],
                    "coord": body["coord"],
                    "categories": body["categories"],
                    "note": body["note"],
                    "target_feature_id": None,
                    "source": "user",
                    "external_ref": None,
                    "created_at": "2026-09-01T00:00:00Z",
                    "resolved_at": None,
                }
            )
        if path == f"/admin/feature-reference-reconciliations/{EVENT_ID}":
            self.timeline.append("pinvi:receipt")
            assert self.decided, "PinVi receipt는 Map 결정 이후에만 존재한다"
            return _envelope(
                {
                    "event_id": EVENT_ID,
                    "status": "applied",
                    "receipt": dict(self.receipt),
                    "attempts": [
                        {
                            "event_id": EVENT_ID,
                            "attempt_sequence": 1,
                            "event_sequence": 1,
                            "event_sha256": "a" * 64,
                            "status": "applied",
                            "block_fingerprint_sha256": None,
                            "observation_root_sha256": "e" * 64,
                            "observed_at": "2026-09-01T00:00:03Z",
                        }
                    ],
                    "impacts": [],
                }
            )
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# fake source tree
# ---------------------------------------------------------------------------


_MAP_COMPOSE = """
services:
  postgres:
    image: postgis/postgis:17-3.5
    ports:
      - "127.0.0.1:${KOR_TRAVEL_MAP_POSTGRES_HOST_PORT}:5432"
  rustfs:
    image: rustfs/rustfs:1.0
    ports:
      - "127.0.0.1:${KOR_TRAVEL_MAP_RUSTFS_API_PORT}:9000"
  rustfs-init:
    image: rustfs/rustfs:1.0
  db-application-schema-fresh-300:
    profiles:
      - fresh-init
    build:
      context: .
      dockerfile: docker/api.Dockerfile
  api:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    env_file:
      - .env
    ports:
      - "127.0.0.1:${KOR_TRAVEL_MAP_ADMIN_WEB_PORT}:13701"
    networks:
      default: {}
  frontend:
    build:
      context: .
      dockerfile: docker/frontend.Dockerfile
    ports:
      - "127.0.0.1:${KOR_TRAVEL_MAP_RUSTFS_CONSOLE_PORT}:3000"
    networks:
      default: {}
  dagster:
    profiles:
      - etl
    build:
      context: .
      dockerfile: docker/dagster.Dockerfile
volumes:
  map-postgres: {}
"""

_MAP_COMPOSE_LOCAL_DEV = """
services:
  api:
    environment:
      KOR_TRAVEL_MAP_API_PROFILE: local-dev
"""

_PINVI_COMPOSE = """
services:
  app-postgres:
    image: postgres:17-alpine
  app-api:
    image: ${PINVI_API_IMAGE:-pinvi-api}:local
    build:
      context: ${PINVI_API_BUILD_CONTEXT:-..}
    ports:
      - "127.0.0.1:${PINVI_API_PORT}:8000"
    networks:
      default: {}
  app-web:
    image: ${PINVI_WEB_IMAGE:-pinvi-web}:local
    build:
      context: ${PINVI_APP_BUILD_CONTEXT:-..}
    ports:
      - "127.0.0.1:${PINVI_WEB_PORT}:3000"
  app-dagster:
    image: ${PINVI_DAGSTER_IMAGE:-pinvi-dagster}:local
    profiles:
      - etl
    build:
      context: ${PINVI_APP_BUILD_CONTEXT:-..}
    ports:
      - "127.0.0.1:${PINVI_DAGSTER_DEV_PORT}:3070"
volumes:
  pinvi-postgres: {}
"""


def _materialise_sources(root: Path) -> tuple[Path, Path]:
    map_root = root / "map"
    pinvi_root = root / "pinvi"
    (map_root / "src/kortravelmap").mkdir(parents=True)
    (map_root / "docker-compose.yml").write_text(_MAP_COMPOSE, encoding="utf-8")
    (map_root / "docker-compose.local-dev.yml").write_text(
        _MAP_COMPOSE_LOCAL_DEV, encoding="utf-8"
    )
    (map_root / "src/kortravelmap/_application_migration_graph.json").write_text(
        json.dumps(
            {
                "revisions": [
                    {"revision": "300", "down_revision": None},
                    {"revision": "301_manual_provider_dedup", "down_revision": ["300"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    (pinvi_root / "infra").mkdir(parents=True)
    (pinvi_root / "scripts").mkdir(parents=True)
    (pinvi_root / "infra/docker-compose.app.yml").write_text(_PINVI_COMPOSE, encoding="utf-8")
    for name in ("docker-app.sh", "m05_activation_attestation.py", "n150-playwright-runner.sh"):
        (pinvi_root / "scripts" / name).write_text("#!/bin/sh\n", encoding="utf-8")
    (pinvi_root / "package-lock.json").write_text(
        json.dumps(
            {"packages": {"node_modules/playwright-core": {"version": PLAYWRIGHT_PINNED_VERSION}}}
        ),
        encoding="utf-8",
    )
    return map_root, pinvi_root


# ---------------------------------------------------------------------------
# harness fixture
# ---------------------------------------------------------------------------


def _relaxed_root_file(driver: ModuleType, path: Path, mode: int) -> os.stat_result:
    """root 소유 단언만 완화한 ``_root_file`` — 나머지 계약은 그대로 확인한다."""

    data = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(data.st_mode)
        or stat.S_IMODE(data.st_mode) != mode
        or data.st_nlink != 1
    ):
        driver._fail("trusted_release_invalid")
    return data


def _relaxed_secure_read(
    driver: ModuleType, path: Path, *, mode: int, encoding: str, limit: int
) -> str:
    _relaxed_root_file(driver, path, mode)
    raw = path.read_bytes()
    if len(raw) > limit:
        driver._fail("trusted_release_invalid")
    return raw.decode(encoding)


class _ProcRange:
    def __init__(self, text: str) -> None:
        self._text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        del encoding
        return self._text


def _patch_proc_port_range(
    monkeypatch: pytest.MonkeyPatch, driver: ModuleType, text: str
) -> None:
    real = Path

    def factory(*args: object) -> Any:
        if args and str(args[0]) == "/proc/sys/net/ipv4/ip_local_port_range":
            return _ProcRange(text)
        return real(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(driver, "Path", factory)


class _Harness:
    def __init__(
        self,
        *,
        driver: ModuleType,
        host: _FakeDockerHost,
        http: _FakeHttpService,
        output: Path,
        ledger: Path,
        expected_identity: str,
    ) -> None:
        self.driver = driver
        self.host = host
        self.http = http
        self.output = output
        self.ledger = ledger
        self.expected_identity = expected_identity
        self.unlinked: list[Path] = []
        self.claims: list[str] = []

    def run(self) -> int:
        return self.driver.main(MANAGER_REVISION, self.output)

    @property
    def result(self) -> dict[str, Any]:
        return json.loads((self.output / "result.json").read_text(encoding="utf-8"))

    def calls_matching(self, *tokens: str) -> list[tuple[str, ...]]:
        return [
            call["args"]
            for call in self.host.calls
            if all(token in call["args"] for token in tokens)
        ]

    def created_for(self, service: str) -> dict[str, Any]:
        return next(item for item in self.host.created if item["service"] == service)


@pytest.fixture
def harness_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., _Harness]]:
    previous_umask = os.umask(0o077)
    try:
        yield lambda *mutations: _build_harness(tmp_path, monkeypatch, mutations)
    finally:
        os.umask(previous_umask)


@pytest.fixture
def harness(harness_factory: Callable[..., _Harness]) -> _Harness:
    return harness_factory()


def _build_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutations: tuple[str, ...]
) -> _Harness:
    driver = _driver(*mutations)
    map_root, pinvi_root = _materialise_sources(tmp_path / "sources")
    output = tmp_path / "out"
    output.mkdir(mode=0o700)
    ledger = tmp_path / "ledger"

    http = _FakeHttpService(bootstrap_path=output / "runtime/pinvi-admin.json")
    host = _FakeDockerHost(
        map_root=map_root,
        pinvi_root=pinvi_root,
        runner_image=driver._PLAYWRIGHT_RUNNER_IMAGE,
    )
    host.http = http

    identity = ExecutionIdentityV6.build(
        source_pinset_sha256=PINNED_RUNTIME_RELEASE.pinset_sha256,
        manager_source_revision=MANAGER_REVISION,
    ).execution_identity_sha256
    pair = driver.M05IsolatedPairEvidence(
        map_full_openapi_sha256="1" * 64,
        map_source_revision=PINNED_RUNTIME_RELEASE.source_for("map").revision,
        pinvi_full_openapi_sha256="1" * 64,
        pinvi_source_revision=PINNED_RUNTIME_RELEASE.source_for("pinvi").revision,
    )
    state = _Harness(
        driver=driver,
        host=host,
        http=http,
        output=output,
        ledger=ledger,
        expected_identity=identity,
    )

    monkeypatch.setattr(driver, "_LEDGER", ledger)
    monkeypatch.setattr(driver, "_ROOT", _REPO_ROOT)
    monkeypatch.setattr(driver, "_validate_trusted_release", lambda _expected: None)
    monkeypatch.setattr(
        driver,
        "_assert_current_m05_execution_is_runnable",
        lambda _expected: SimpleNamespace(
            current=SimpleNamespace(execution_identity_sha256=identity)
        ),
    )
    monkeypatch.setattr(
        driver,
        "_source_pair_preflight",
        # `state_paths`/`values`도 함께 온다 — body가 일회용 실행 체크아웃을 만들 때
        # 쓴다. 이 하네스는 그 생성을 스텁하므로 값 자체는 통과용이면 된다.
        lambda: (
            map_root,
            pinvi_root,
            pair,
            "2" * 64,
            pair.map_source_revision,
            SimpleNamespace(),
            {},
            PINVI_SOURCE_TREE,
        ),
    )

    # 실행은 일회용 체크아웃에서 한다(봉인 트리 오염 방지). 여기서는 진짜 git worktree를
    # 만들지 않지만, 스텁이 **봉인 루트와 다른 경로**를 돌려주고 실행에 필요한 스크립트를
    # 거기에 만든다. 초판 스텁은 `pinvi_root`를 그대로 돌려줘서 실행-루트 치환을
    # 통째로 되돌려도 전 테스트가 green이었다(적대 리뷰 BLOCKER-2).
    def _materialise_run(**kwargs: Any) -> Path:
        assert kwargs["role"] == "pinvi"
        # tree는 호출자가 materialize된 핀 소스에서 가져온 값이어야 한다 — 같은 bare에서
        # 다시 유도하면 자기참조가 된다.
        assert kwargs["expected_tree"] == PINVI_SOURCE_TREE
        destination = Path(kwargs["destination"])
        assert destination != pinvi_root
        (destination / "scripts").mkdir(parents=True)
        for name in ("m05_activation_attestation.py", "n150-playwright-runner.sh"):
            (destination / "scripts" / name).write_text("#!/bin/sh\n", encoding="utf-8")
        host.pinvi_run_root = destination
        return destination

    def _remove_run(**kwargs: Any) -> None:
        host.disposable_calls.append(
            ("remove", kwargs["role"], Path(kwargs["destination"]))
        )

    def _assert_sealed(**kwargs: Any) -> None:
        assert host.pinvi_run_root is not None
        host.disposable_calls.append(("sealed", kwargs["role"], host.pinvi_run_root))

    monkeypatch.setattr(driver, "materialize_disposable_run_worktree", _materialise_run)
    monkeypatch.setattr(driver, "remove_disposable_run_worktree", _remove_run)
    monkeypatch.setattr(driver, "assert_pinned_worktree_is_still_sealed", _assert_sealed)
    monkeypatch.setattr(driver, "_root_directory", lambda _path, mode=0o700: None)
    monkeypatch.setattr(
        driver, "_root_file", lambda path, mode=0o600: _relaxed_root_file(driver, path, mode)
    )
    monkeypatch.setattr(
        driver,
        "_secure_read_root_file",
        lambda path, *, mode, encoding, limit: _relaxed_secure_read(
            driver, path, mode=mode, encoding=encoding, limit=limit
        ),
    )

    def unlink_private(path: Path) -> None:
        if path.exists():
            metadata = path.lstat()
            assert not path.is_symlink() and stat.S_ISREG(metadata.st_mode)
            path.unlink()
        state.unlinked.append(path)

    monkeypatch.setattr(driver, "_unlink_private", unlink_private)

    def claim(*, ledger_root: Path, plan: Any) -> Path:
        assert ledger_root == ledger
        assert stat.S_IMODE(ledger_root.lstat().st_mode) == 0o700
        state.claims.append(plan.ledger_filename)
        host.timeline.append("ledger-claim")
        host.expected_admission = json.loads(
            json.dumps(dict(driver.build_m05_isolated_manager_admission(plan=plan, pair=pair)))
        )
        target = ledger_root / plan.ledger_filename
        target.write_bytes(plan.claim_bytes)
        return target

    monkeypatch.setattr(driver, "claim_m05_isolated_harness_ledger", claim)
    monkeypatch.setattr(driver, "_block_terminal_m05_execution", lambda *_a, **_k: True)
    monkeypatch.setattr(
        driver, "_command", lambda *args, **kwargs: host.command(driver, *args, **kwargs)
    )
    _patch_proc_port_range(monkeypatch, driver, "32768\t60999\n")
    monkeypatch.setattr(driver._LOOPBACK_OPENER, "open", http.open)
    monkeypatch.setattr(
        driver, "build_opener", lambda *_handlers: SimpleNamespace(open=http.open)
    )
    return state


# ---------------------------------------------------------------------------
# 성공 경로 완주
# ---------------------------------------------------------------------------


def test_full_happy_path_reaches_a_passed_receipt(harness: _Harness) -> None:
    """``main()``이 status=passed / phase=completed / driver_phase=completed로 끝난다."""

    assert harness.run() == 0
    result = harness.result

    assert result["status"] == "passed"
    assert result["phase"] == "completed"
    # 적대 리뷰: passed 경로의 driver_phase가 "completed"가 아니면 launcher가
    # 첫 PASS를 무효 receipt로 판정해 무조건 소각으로 승격시킨다.
    assert result["driver_phase"] == "completed"
    assert result["cleanup_failed"] is False
    assert result["disposable_run_worktree_retained"] is False
    assert result["harness"] == "m05-isolated-bridge-v1"
    assert result["manager_source_revision"] == MANAGER_REVISION
    assert result["pinset_sha256"] == PINNED_RUNTIME_RELEASE.pinset_sha256
    assert result["execution_identity_sha256"] == harness.expected_identity
    assert len(result["transaction_id"]) == 32
    assert set(result) == {
        "harness",
        "manager_source_revision",
        "phase",
        "driver_phase",
        "cleanup_failed",
        "disposable_run_worktree_retained",
        "pinset_sha256",
        "execution_identity_sha256",
        "status",
        "transaction_id",
        "m04_attestation_sha256",
        "m05_attestation_sha256",
        "runtime_provenance_sha256",
    }

    runtime = harness.output / "runtime"
    for key, path in {
        "m04_attestation_sha256": runtime / "m04/m04-attestation.json",
        "m05_attestation_sha256": runtime / "m05/attestation.json",
        "runtime_provenance_sha256": runtime / "isolated-runtime-provenance.json",
    }.items():
        assert result[key] == hashlib.sha256(path.read_bytes()).hexdigest()

    provenance = json.loads(
        (runtime / "isolated-runtime-provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["kind"] == "m05-isolated-runtime-provenance-v1"
    assert provenance["execution_identity_sha256"] == harness.expected_identity
    assert set(provenance["map"]) == {
        "admin_image_id",
        "api_image_id",
        "frontend_image_id",
        "full_openapi_sha256",
        "source_revision",
    }
    assert len(harness.claims) == 1


def test_full_happy_path_calls_external_commands_in_the_expected_order(
    harness: _Harness,
) -> None:
    """ledger claim은 정적 판정 **뒤**, Docker mutation **앞**에 정확히 한 번이다."""

    assert harness.run() == 0

    assert harness.host.timeline == [
        # 실행권 소비 전: rendered topology + runner digest 정합만 확인한다.
        "compose:map:config-json",
        "run:playwright-info",
        "ledger-claim",
        # Map runtime
        "compose:map:config-profiles",
        "compose:map:up[postgres]",
        "compose:map:run[db-application-schema-fresh-300]{fresh-init}",
        "compose:map:up[rustfs,rustfs-init,api,frontend]",
        "compose:map:ps[api]",
        "compose:map:ps[frontend]",
        # PinVi runtime
        "compose:pinvi:config-profiles",
        "docker-app:build",
        "docker-app:up",
        "compose:pinvi:up[app-dagster]{etl}",
        "compose:pinvi:ps[app-api]",
        "compose:map:config-json",
        "compose:pinvi:config-json{etl}",
        "image:id",
        "image:id",
        # acceptance 본문
        "compose:pinvi:ps[app-web]",
        "compose:pinvi:ps[app-dagster]{etl}",
        "compose:map:ps[frontend]",
        "attest:m04",
        "run:fixture",
        "attest:live",
        # cleanup — 모델의 전체 프로파일을 켜고 down한다(e2e6)
        "compose:pinvi:down{etl}",
        "compose:map:down{etl,fresh-init}",
    ]


def test_full_happy_path_http_contract_order(harness: _Harness) -> None:
    assert harness.run() == 0
    assert harness.http.timeline == [
        "map:health",
        "map:subscription",
        "pinvi:login",
        "pinvi:feature-request",
        "map:approve",
        "map:creation-provenance",
        # 리바인드가 고쳐 쓸 사용자 참조는 Map 결정 **이전에** 있어야 한다 —
        # 없으면 impact_count가 0이 되고 live spec의 중심 단언이 공허해진다.
        "pinvi:login",
        "pinvi:seed-trip",
        "pinvi:seed-poi",
        "map:case",
        "map:decision",
        "pinvi:login",
        "pinvi:receipt",
    ]
    # Map admin / PinVi 모두 loopback publish 포트 하나로만 접근한다.
    assert len(harness.http.map_ports) == 1
    assert len(harness.http.pinvi_ports) == 1


def test_full_happy_path_uses_rendered_image_references(harness: _Harness) -> None:
    """e2e8: 이미지 참조는 ``{project}-{service}`` 추측이 아니라 rendered 값이다."""

    assert harness.run() == 0
    references = harness.host.image_id_references
    assert len(references) == 2
    # PinVi compose는 명시 ``image:``를 쓴다 — 추측 이름은 존재하지 않는다.
    assert any(re.fullmatch(r"m05i-pinvi-[0-9a-f]{32}-api:local", item) for item in references)
    assert not any(item.endswith("-app-api") for item in references)
    # Map compose에는 명시 image가 없으므로 Compose 기본 규칙을 따른다.
    assert any(re.fullmatch(r"m05i-map-[0-9a-f]{32}-api", item) for item in references)


def test_full_happy_path_publishes_only_non_ephemeral_loopback_ports(
    harness: _Harness,
) -> None:
    """e2e5: **모든** host publish는 loopback이고 ephemeral 대역(32768+) 밖이다."""

    assert harness.run() == 0
    published: set[int] = set()
    for record in harness.host.created:
        for bindings in record["published"].values():
            for binding in bindings:
                assert binding["HostIp"] == "127.0.0.1", record["service"]
                published.add(int(binding["HostPort"]))
    assert published
    assert all(20000 <= port < 30000 for port in published)
    # HTTP로 실제 도달한 포트도 같은 대역이어야 한다.
    assert (harness.http.map_ports | harness.http.pinvi_ports) <= published


def test_full_happy_path_applies_the_static_bridge_topology(harness: _Harness) -> None:
    """override의 정적 IP가 실제로 적용되고 app-api가 두 network에 join한다."""

    assert harness.run() == 0
    map_network = next(
        name for name in harness.created_for("api")["addresses"] if name.startswith("m05i-map-")
    )
    api_address = harness.created_for("api")["addresses"][map_network]
    frontend_address = harness.created_for("frontend")["addresses"][map_network]
    subnet = ipaddress.ip_network(f"{api_address}/28", strict=False)
    hosts = list(subnet.hosts())
    # driver는 정적 주소를 범위 **상단**에서 고른다(동적 할당과의 충돌 회피).
    assert api_address == str(hosts[-1])
    assert frontend_address == str(hosts[-2])
    # e2e3: app-api는 PinVi default와 Map external network 양쪽에 join해야 한다.
    app_api = harness.created_for("app-api")
    assert set(app_api["addresses"]) == {
        map_network,
        map_network.replace("m05i-map-", "m05i-pinvi-"),
    }


def test_full_happy_path_tolerates_expose_only_port_metadata(harness: _Harness) -> None:
    """e2e12: EXPOSE 메타데이터(binding 없는 항목)는 published 집합이 아니다."""

    assert harness.run() == 0
    # fake는 Map API 이미지에 EXPOSE 12701을 싣는다. 종전 계약(Ports 키 정확일치)은
    # 이 상황에서 원리적으로 통과 불가였다.
    assert harness.host.saw_unbound_expose_metadata is True


def test_full_happy_path_looks_up_app_dagster_once_with_its_profile(
    harness: _Harness,
) -> None:
    """적대 리뷰: 프로파일 없는 중복 app-dagster 조회는 본문 진입 후 무조건 소각이다."""

    assert harness.run() == 0
    lookups = harness.calls_matching("ps", "-q", "app-dagster")
    assert len(lookups) == 1
    assert "--profile" in lookups[0]
    assert lookups[0][lookups[0].index("--profile") + 1] == "etl"


def test_full_happy_path_leaves_no_private_material(harness: _Harness) -> None:
    assert harness.run() == 0
    assert sorted(path.name for path in harness.unlinked) == [
        "m05-private-key.pem",
        "map-fixture.env",
        "map.env",
        "map.override.yml",
        "pinvi-admin.json",
        "pinvi-isolated-manager-admission.json",
        "pinvi.env",
        "pinvi.override.yml",
    ]
    assert all(not path.exists() for path in harness.unlinked)
    assert not harness.host.containers
    assert not [entry for entry in harness.host.networks.values() if entry["project"]]
    assert not harness.host.volumes


def test_full_happy_path_receipt_is_accepted_by_the_root_launcher(harness: _Harness) -> None:
    """launcher(run-m05-isolated-e2e-once)의 result 검증기가 그대로 수용한다."""

    assert harness.run() == 0
    completed = _run_launcher_validator(harness.output / "result.json", driver_status=0)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("driver_phase", "m04_m05_e2e"),
        ("phase", "m04_m05_e2e"),
        ("cleanup_failed", True),
    ],
)
def test_launcher_rejects_a_tampered_passed_receipt(
    harness: _Harness, tmp_path: Path, key: str, value: object
) -> None:
    """passed receipt의 phase/driver_phase/cleanup 계약은 launcher가 강제한다."""

    assert harness.run() == 0
    receipt = harness.result
    receipt[key] = value
    tampered = tmp_path / "tampered-result.json"
    _write_0600_json(tampered, receipt)
    assert _run_launcher_validator(tampered, driver_status=0).returncode != 0


# ---------------------------------------------------------------------------
# 회귀 재현 (히스토리 결함을 되살렸다고 가정한 변형)
# ---------------------------------------------------------------------------


def test_regression_e2e3_missing_compose_override_for_pinvi(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e3: docker-app.sh에 override를 넘기지 않으면 app-api가 Map network에 없다."""

    driver = harness.driver
    original = driver._pinvi_manager_admission_environment

    def without_override(**kwargs: object) -> dict[str, str]:
        value = dict(original(**kwargs))
        value.pop("PINVI_DOCKER_COMPOSE_EXTRA_FILE")
        return value

    monkeypatch.setattr(driver, "_pinvi_manager_admission_environment", without_override)

    assert harness.run() == 1
    result = harness.result
    assert result["status"] == "blocked"
    assert result["phase"] == "pinvi_runtime"
    assert "attest:m04" not in harness.host.timeline


def test_regression_e2e4_reset_swallows_the_static_ipv4_address(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e4: ``networks: !reset``은 중첩 ipv4_address를 삼켜 정적 IP가 적용되지 않는다."""

    driver = harness.driver
    original = driver._write_private_text

    def sabotage(path: Path, value: str) -> None:
        if path.name == "map.override.yml":
            value = value.replace("networks: !override", "networks: !reset")
        original(path, value)

    monkeypatch.setattr(driver, "_write_private_text", sabotage)

    assert harness.run() == 1
    result = harness.result
    assert result["phase"] == "runtime_container_identity_invalid"
    assert result["status"] == "blocked"


def test_regression_e2e5_ephemeral_range_overlap_is_closed_before_mutation(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e5: ephemeral 하한이 publish 대역과 겹치면 mutation 전에 결정적으로 닫는다."""

    _patch_proc_port_range(monkeypatch, harness.driver, "20000\t60999\n")

    assert harness.run() == 1
    result = harness.result
    assert result["phase"] == "ports_unavailable"
    assert result["status"] == "preflight_rejected"
    assert harness.claims == []
    assert harness.host.timeline == []


def test_regression_e2e6_cleanup_must_enable_every_model_profile(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e6: 프로파일 밖 down은 app-dagster를 남겨 cleanup 검증을 깨뜨린다."""

    monkeypatch.setattr(harness.driver, "_compose_model_profiles", lambda **_kwargs: ())

    assert harness.run() == 1
    result = harness.result
    assert result["cleanup_failed"] is True
    assert result["status"] == "blocked"
    # 실제 실행 표면(본문 완주)은 driver_phase가 보존한다 — e2e6의 "가림" 회귀 방지.
    assert result["driver_phase"] == "completed"


def test_regression_e2e8_guessing_image_names_breaks_on_explicit_image(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e8: ``{project}-{service}`` 추측은 명시 ``image:``를 쓰는 PinVi에서 깨진다."""

    def guessed(
        *,
        root: Path,
        project: str,
        env_file: Path,
        files: tuple[Path, ...],
        services: tuple[str, ...],
        profiles: tuple[str, ...] = (),
    ) -> dict[str, str]:
        del root, env_file, files, profiles
        return {service: f"{project}-{service}" for service in services}

    monkeypatch.setattr(harness.driver, "_rendered_service_images", guessed)

    assert harness.run() == 1
    result = harness.result
    assert result["phase"] == "runtime_command_failed"
    assert result["driver_phase"] == "runtime_command_failed"
    assert "attest:m04" not in harness.host.timeline


def test_regression_e2e12_an_extra_host_binding_is_still_rejected(harness: _Harness) -> None:
    """e2e12 수정은 EXPOSE만 허용한다 — 실제 추가 host binding은 여전히 거절한다."""

    harness.host.map_api_extra_binding = True

    assert harness.run() == 1
    assert harness.result["phase"] == "pinvi_runtime"


def test_regression_e2e13_runner_digest_must_match_the_pinned_lockfile(
    harness: _Harness,
) -> None:
    """e2e13: runner digest의 driverVersion != pinned playwright-core는 실행권 전에 닫는다."""

    harness.host.playwright_driver_version = "1.60.0"

    assert harness.run() == 1
    assert harness.result["phase"] == "runtime_setup_playwright_runner_image"
    assert harness.claims == []
    assert "ledger-claim" not in harness.host.timeline


def test_regression_e2e15_dedup_needs_the_opaque_text_feature_id(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e15: 승인 UUID를 그대로 dedup 프로시저에 넘기면 NOT FOUND가 난다."""

    monkeypatch.setattr(
        harness.driver,
        "_resolve_manual_feature_text_id",
        lambda *, admin_url, proxy_secret, feature_uuid: feature_uuid,
    )

    assert harness.run() == 1
    assert harness.host.fixture_arguments == [MANUAL_FEATURE_UUID]
    result = harness.result
    assert result["phase"] == "runtime_command_failed"
    assert result["driver_phase"] == "runtime_command_failed"


def test_regression_live_attestation_identity_must_be_the_text_feature_id(
    harness_factory: Callable[..., _Harness],
) -> None:
    """적대 리뷰: PinVi attestation은 local receipt의 TEXT feature_id와 결박된다.

    dedup fixture는 TEXT id로 성공해도, live attestation env에 승인 UUID를 실으면
    receipt와 identity가 어긋나 본문에서 소각된다.
    """

    harness = harness_factory(
        '"PINVI_M05_LIVE_OLD_FEATURE_ID": manual_feature_id,',
        '"PINVI_M05_LIVE_OLD_FEATURE_ID": manual_feature_uuid,',
    )

    assert harness.run() == 1
    assert harness.host.fixture_arguments == [MANUAL_FEATURE_TEXT_ID]
    result = harness.result
    assert result["phase"] == "runtime_command_failed"
    assert "attest:live" in harness.host.timeline


def test_regression_passed_receipt_needs_a_completed_driver_phase(
    harness_factory: Callable[..., _Harness],
) -> None:
    """적대 리뷰: passed 경로의 driver_phase가 마지막 body phase면 첫 PASS가 소각된다.

    수정 전 계약(``driver_phase = phase``)을 driver 소스에 그대로 되살려, 이 하네스가
    **launcher 검증까지 태워** 실제로 잡는지 확인한다.
    """

    harness = harness_factory(
        'driver_phase = "completed" if completed else phase',
        "driver_phase = phase",
    )

    assert harness.run() == 0
    result = harness.result
    assert result["status"] == "passed"
    assert result["driver_phase"] == "m04_m05_e2e"
    completed = _run_launcher_validator(harness.output / "result.json", driver_status=0)
    assert completed.returncode != 0, "launcher가 무효 driver_phase를 수용하면 안 된다"


def test_regression_cleanup_failure_must_not_mask_the_real_phase(
    harness_factory: Callable[..., _Harness],
) -> None:
    """e2e6: driver_phase 대입이 강등 가드 **뒤**로 가면 실제 실패 표면이 가려진다."""

    harness = harness_factory(
        'driver_phase = "completed" if completed else phase',
        "driver_phase = phase",
    )
    harness.driver._compose_model_profiles = lambda **_kwargs: ()  # type: ignore[attr-defined]

    assert harness.run() == 1
    result = harness.result
    assert result["cleanup_failed"] is True
    # 수정 전 코드에서는 본문 완주 사실이 사라지고 마지막 phase만 남는다.
    assert result["driver_phase"] != "completed"


def test_regression_profileless_app_dagster_lookup_burns_the_body(
    harness_factory: Callable[..., _Harness],
) -> None:
    """적대 리뷰: 프로파일 없는 app-dagster ``ps``는 빈 결과 → 본문 진입 후 무조건 소각.

    driver 소스에서 ``profiles=("etl",)``를 그대로 떼어내 재현한다.
    """

    harness = harness_factory(
        '            profiles=("etl",),\n        )\n        map_frontend = _container_id(',
        "        )\n        map_frontend = _container_id(",
    )

    assert harness.run() == 1
    result = harness.result
    assert result["phase"] == "runtime_container_identity_invalid"
    assert result["status"] == "blocked"
    assert harness.calls_matching("ps", "-q", "app-dagster")


# ---------------------------------------------------------------------------
# 이 하네스가 드러낸 계약 구멍의 회귀 가드 (#295에서 수리됨)
# ---------------------------------------------------------------------------


def test_preclaim_scoped_rejection_is_accepted_by_the_launcher(
    harness: _Harness,
) -> None:
    """claim 이전의 scoped 실패는 launcher가 exit 4(무소비)로 받아야 한다.

    이 테스트는 이 하네스가 처음 적발한 구멍이다. driver는 자유형 진단을
    receipt에 실었고 launcher는 그 필드를 닫힌 enum으로만 받아, 보정 가능한
    실패가 `pin block-execution`으로 흘러 실행권을 무조건 소각했다. #295가
    launcher 검증을 권위/진단 두 계층으로 갈라 수리했다."""

    harness.host.playwright_driver_version = "1.60.0"

    assert harness.run() == 1
    assert harness.result["status"] == "preflight_rejected"
    completed = _run_launcher_validator(harness.output / "result.json", driver_status=1)
    assert completed.returncode == 4, completed.stderr


# ---------------------------------------------------------------------------
# launcher receipt 검증기 재사용
# ---------------------------------------------------------------------------


def _launcher_receipt_validator_source() -> str:
    launcher = _LAUNCHER_PATH.read_text(encoding="utf-8")
    marker = '"$driver_status" <<\'PY\'\n'
    start = launcher.index(marker) + len(marker)
    end = launcher.index("\nPY\n", start)
    source = launcher[start:end]
    # non-root 테스트에서 재사용할 수 있게 **root 소유 단언만** 완화한다.
    # 정규 파일 / 모드 0600 / nlink / 스키마 검증은 launcher 원문 그대로 남는다.
    assert "metadata.st_uid != 0" in source
    return source.replace("metadata.st_uid != 0", "False", 1)


def _run_launcher_validator(
    result_path: Path, *, driver_status: int
) -> subprocess.CompletedProcess[str]:
    receipt = json.loads(result_path.read_text(encoding="utf-8"))
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _launcher_receipt_validator_source(),
            str(result_path),
            receipt["manager_source_revision"],
            receipt["pinset_sha256"],
            receipt["execution_identity_sha256"],
            str(driver_status),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _write_0600_json(path: Path, value: dict[str, Any]) -> None:
    raw = (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, raw)
    finally:
        os.close(descriptor)


def test_seeded_reference_is_created_before_the_map_decision(harness: _Harness) -> None:
    """리바인드가 고쳐 쓸 사용자 참조가 Map 결정 이전에 심어져야 한다.

    심지 않으면 impact_count가 구조적으로 0이 되고, live spec의 중심 단언
    ``expect(impacts).toHaveLength(0)``이 공허하게 참이 된다 — per-impact 단언
    본문이 한 줄도 실행되지 않은 채 게이트가 green이 난다. 즉 배관이 도는 것만
    증명하고 "사용자 참조를 고쳐 쓴다"는 M05의 존재 이유는 증명하지 못한다."""

    assert harness.run() == 0
    timeline = harness.http.timeline
    assert "pinvi:seed-poi" in timeline, "참조를 심지 않으면 리바인드가 공허해진다"
    assert timeline.index("pinvi:seed-poi") < timeline.index("map:decision")
    # 일상적인 사용자 경로로 심어야 legacy 축만 있는 행(UUID shadow가 NULL)이
    # 만들어지고, 리바인드가 두 축을 함께 복구하는지까지 증명된다.
    seed = next(
        item
        for item in harness.http.requests
        if item["path"].endswith("/pois") and item["method"] == "POST"
    )
    assert seed["body"]["feature_id"] == MANUAL_FEATURE_TEXT_ID
    assert "feature_uuid" not in seed["body"]


def test_zero_impact_receipt_is_rejected_instead_of_passing_vacuously(
    harness: _Harness,
) -> None:
    """참조를 심었는데 receipt가 impact 0을 보고하면 실패해야 한다.

    이 대조가 없으면 리바인드가 아무것도 고쳐 쓰지 않아도 게이트가 통과한다."""

    harness.http.receipt = {**harness.http.receipt, "impact_count": 0}
    assert harness.run() == 1
    assert harness.result["phase"] == "m05_pinvi_impact_missing"
    assert harness.result["status"] == "blocked"

def test_the_harness_default_receipt_is_not_vacuous() -> None:
    """하네스 자신의 기본 receipt가 impact 0이면 모든 happy-path 검증이 공허해진다.

    (live spec 환경으로의 전달은 _FakeDockerHost가 이미 결박한다 —
    ``env["PINVI_M05_LIVE_IMPACT_COUNT"] != str(receipt["impact_count"])`` 이면
    fake attestation이 실패한다.)"""

    assert IMPACT_COUNT >= 1
