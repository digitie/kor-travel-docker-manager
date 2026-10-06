"""공용 Dagster 제어 평면의 **파생물**과 target별 합류 스위치를 결박한다(ADR-54, platform-topology.md §7 3단계).

정본은 둘이다. `config/docker-targets.yml`의 target별 `dagster.control_plane`(`own`|`shared`)이 스위치이고,
`docker-compose.yml`이 그 스위치가 렌더된 모양이다. 나머지는 파생물이다.

- **workspace** — `config/dagster-shared/workspace.yaml`은 `shared` target의 code-server command(`-p`·`-m`,
  `--location-name`)에서 만든다. 파일이 파생과 다르면 빨개지고, 넣어야 할 내용 전체를 보여 준다.
- **G3-b** — workspace의 `location_name`마다 공용 `dagster.yaml`에 `dagster/code_location` 상한이 있고,
  `shared` target의 location은 전부 workspace에 있다. 이름이 어긋나면 그 location의 상한이 조용히 사라진다.
- **렌더 계약** — `shared`면 (a) code-server가 공용 URL 앵커와 공용 `dagster.yaml`을 받고 loopback에서만
  듣는다, (b) target이 선언한 소비자 env가 공용 webserver·공개 host를 가리키고 어떤 활성 서비스도 옛
  webserver·gateway의 loopback 주소를 부르지 않는다, (c) 옛 webserver·daemon(과 그것에 기대는 gateway)은
  `profiles: [legacy-dagster]`이고 어느 target의 `services`·`runtime_services`에도 없으며 활성 서비스가 그것에
  `depends_on`하지 않는다. `own`이면 그 어느 것도 없다.

**렌더러를 두지 않는다.** compose는 단일 정본 파일이고(ADR-20의 단일 파일 경계 — override·`include`·
`COMPOSE_FILE`을 거부한다) 주석이 계약의 일부라, 파일을 기계로 다시 쓰면 둘 다 잃는다. 전환은 네 번뿐이고
각각 검토되는 PR이다. 그래서 전환 PR이 compose를 손으로 바꾸고, 이 테스트가 스위치에서 **파생한 기대**와
대조해 빠진 단계를 이름으로 말한다. 아래 `_flip`은 그 편집을 모델 위에서 그대로 하는 참조 구현이다 —
네 target 모두 뒤집으면 계약이 초록이고, 한 단계라도 빼면 빨갛다는 것을 보인다.

**이름이 아니라 모양에서 찾는다.** code-server는 `dagster code-server start`(또는 `api grpc`)를 실행하는
target의 서비스, 옛
webserver·daemon은 `dagster-webserver`/`dagster-daemon`을 실행하면서 그 code-server에 `depends_on`하는
서비스, gateway는 그것들에 `depends_on`하는 서비스다. 공용 webserver는 공용 workspace를 붙인 webserver이고
공개 host env는 그 앞 gateway 컨테이너의 `prod_url_env`다. 소비자 env만 선언이다 — PinVi·geo API는 앱
코드의 기본값에 기대어 compose에 그 env가 아직 없으므로 compose만으로는 찾을 수 없다.
"""

from __future__ import annotations

import copy
import hashlib
import http.server
import json
import re
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_TARGETS = _REPO_ROOT / "config" / "docker-targets.yml"
_WORKSPACE = _REPO_ROOT / "config" / "dagster-shared" / "workspace.yaml"
_INSTANCE_CONFIG = _REPO_ROOT / "config" / "dagster-shared" / "dagster.yaml"

_ANCHOR = "x-dagster-shared-control-env"
_LEGACY_PROFILE = "legacy-dagster"
_LOCATION_TAG = "dagster/code_location"
#: 공개 host env에 넣는 탐침 값. 해석된 소비자 값이 이것으로 시작하면 공개 host를 가리킨다.
_PUBLIC_PROBE = "https://dagster-shared.probe.invalid"

_WORKSPACE_SOURCE = f"./{_WORKSPACE.relative_to(_REPO_ROOT).as_posix()}"
_INSTANCE_SOURCE = f"./{_INSTANCE_CONFIG.relative_to(_REPO_ROOT).as_posix()}"
_GATEWAY_SOURCE = "./config/dagster-shared/gateway.conf"

#: 공용 plane 설정 파일 → 그것을 붙인 상시 서비스가 실어야 할 내용 digest env(적대 리뷰 H1). bind source가
#: 설치본 symlink를 거친 경로라 compose config hash는 경로 문자열만 본다 — 내용이 바뀌어도 재생성되지 않는다.
#: digest env가 hash를 내용에 묶는다. one-shot(`restart: "no"`)은 매번 새로 돌므로 빠진다.
_DIGEST_ENV = {
    _INSTANCE_SOURCE: "KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST",
    _WORKSPACE_SOURCE: "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST",
    _GATEWAY_SOURCE: "DAGSTER_GATEWAY_CONF_DIGEST",
}

#: 옛 프로젝트별 webserver·daemon·gateway의 서비스 이름을 **literal로** 든 코드(적대 리뷰 M2, 재리뷰 MED-2).
#: 여기 든 서비스가 `legacy-dagster`로 내려가면 frozen render(`--profile bootstrap`만)에서 사라지는데 literal
#: 집합(pinned 재구축의 slot·build 목록, C6c 보호 집합, 명시적 `compose_up("<서비스>")`, 이미지 보존)은 그것을
#: 여전히 요구하고, 명시적 `up <서비스>`는 꺼진 profile의 서비스도 띄워 옛 daemon이 되살아난다. 그 참조는 이제
#: 전부 `runtime_topology`가 렌더된 모델과 스위치에서 파생한다. 이 검사는 **되돌아옴을 막는 문**으로 남는다:
#: 옮기는 target의 옛 서비스 이름(compose에서 파생)이 `backend/src`·`scripts`의 어디든 온전한 토큰으로 다시
#: 나타나면 그 target은 `(pinned)`로 빨갛다. compose·targets(렌더된 모양 자체)와 테스트는 보지 않는다.
_CODE_ROOTS = (_REPO_ROOT / "backend" / "src", _REPO_ROOT / "scripts")


def _code_files() -> list[Path]:
    files: list[Path] = []
    for root in _CODE_ROOTS:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".md"}:
                files.append(path)
    return files


def _hardcoded(names: set[str]) -> dict[str, list[str]]:
    """이름마다 그것을 온전한 토큰으로 든 파일(저장소 기준 경로, 저장소 밖이면 절대 경로)."""

    found: dict[str, list[str]] = {}
    patterns = {name: re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])") for name in names}
    for path in _code_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        shown = path.relative_to(_REPO_ROOT) if path.is_relative_to(_REPO_ROOT) else path
        for name, pattern in patterns.items():
            if pattern.search(text):
                found.setdefault(name, []).append(shown.as_posix())
    return found


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


def _file_bytes(source: str, files: Mapping[str, bytes] | None) -> bytes:
    if files and source in files:
        return files[source]
    return (_REPO_ROOT / source[2:]).read_bytes()


def _digest_violations(compose: dict[str, Any], files: Mapping[str, bytes] | None = None) -> list[str]:
    violations: list[str] = []
    for name, service in compose["services"].items():
        if service.get("restart") == "no":
            continue
        environment = service.get("environment") or {}
        for volume in service.get("volumes") or []:
            source = str(volume.get("source")) if isinstance(volume, dict) else _split_top(str(volume))[0]
            if source not in _DIGEST_ENV:
                # 공용 plane 설정을 모르는 모양(긴 형식·절대 경로·디렉터리·다른 파일)으로 붙이면 digest가 보지
                # 못한다 — 조용히 건너뛰지 않고 멈춘다(재리뷰 LOW-2).
                if "config/dagster-shared" in str(volume):
                    violations.append(f"(d) `{name}`: 공용 plane 설정을 모르는 모양으로 붙였다: {volume!r}")
                continue
            key = _DIGEST_ENV[source]
            expected = _digest(_file_bytes(source, files))
            if str(environment.get(key)) != expected:
                violations.append(
                    f"(d) `{name}`: `{key}`가 `{environment.get(key)}`다 — 붙인 `{source}`의 내용은 `{expected}`"
                )
    return violations


# ── compose 보간 ─────────────────────────────────────────────────────────


def _resolve(text: str, env: Mapping[str, str]) -> str:
    """compose 보간을 `env`로 푼다 — 중첩 기본값(`${A:-${B:-x}}`)과 `$$`까지."""

    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "$":
            out.append(char)
            index += 1
            continue
        following = text[index + 1 : index + 2]
        if following == "$":
            out.append("$")
            index += 2
            continue
        if following != "{":
            bare = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index + 1 :])
            if bare is None:
                out.append("$")
                index += 1
                continue
            out.append(env.get(bare.group(), ""))
            index += 1 + bare.end()
            continue
        depth, end = 0, index + 1
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        assert end < len(text), f"닫히지 않은 보간: {text!r}"
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(:?[-?+])?(.*)", text[index + 2 : end], re.S)
        assert match, f"못 읽는 보간: {text!r}"
        name, operator, word = match.groups()
        value = env.get(name)
        present = value is not None and (value != "" or not (operator or "").startswith(":"))
        if operator in (":-", "-"):
            out.append(value if present and value is not None else _resolve(word, env))
        elif operator in (":+", "+"):
            out.append(_resolve(word, env) if present else "")
        else:  # 연산자 없음, `:?`·`?` — 값이 없으면 빈 문자열(테스트는 기본값만 본다)
            out.append(value or "")
        index = end + 1
    return "".join(out)


# ── 렌더된 모델 읽기 ──────────────────────────────────────────────────────


def _documents() -> tuple[dict[str, Any], dict[str, Any]]:
    compose = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    targets = load_yaml_rejecting_duplicate_keys(_TARGETS.read_text(encoding="utf-8"))
    assert isinstance(compose, dict) and isinstance(targets, dict)
    return compose, targets


def _words(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(part) for part in value]
    if isinstance(value, str):
        return value.split()
    return []


def _runs(service: Mapping[str, Any], program: str) -> bool:
    text = " ".join(_words(service.get("command")) + _words(service.get("entrypoint")))
    return program in text


#: 장기 실행 code-server의 두 모양 — `code-server start`만 location reload에 definitions를 다시 import한다.
_RELOADABLE = ("code-server", "start")
_NON_RELOADABLE = ("api", "grpc")


def _code_server_subcommand(service: Mapping[str, Any]) -> tuple[str, ...] | None:
    argv = _words(service.get("command")) + _words(service.get("entrypoint"))
    for index, word in enumerate(argv):
        pair = tuple(argv[index + 1 : index + 3])
        if word.rsplit("/", 1)[-1] == "dagster" and pair in (_RELOADABLE, _NON_RELOADABLE):
            return pair
    return None


def _is_code_server(service: Mapping[str, Any]) -> bool:
    return _code_server_subcommand(service) is not None


def _flag(argv: list[str], *names: str) -> str | None:
    for index, word in enumerate(argv[:-1]):
        if word in names:
            return argv[index + 1]
    return None


def _depends(service: Mapping[str, Any]) -> set[str]:
    depends = service.get("depends_on") or {}
    return set(depends) if isinstance(depends, (dict, list)) else set()


def _environment(service: Mapping[str, Any]) -> dict[str, Any]:
    environment = service.get("environment") or {}
    assert isinstance(environment, dict), "environment는 mapping 형태여야 이 파생이 읽는다"
    return environment


def _build_args(service: Mapping[str, Any]) -> dict[str, Any]:
    build = service.get("build")
    args = build.get("args") if isinstance(build, dict) else None
    return args if isinstance(args, dict) else {}


def _split_top(text: str) -> list[str]:
    """`:`로 자르되 보간(`${A:-x}`) 안의 `:`에서는 자르지 않는다."""

    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        depth += char == "{"
        depth -= char == "}"
        if char == ":" and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    return [*parts, text[start:]]


def _mount_target(volume: object) -> str | None:
    parts = _split_top(str(volume))
    return parts[1] if len(parts) >= 2 else None


def _is_external(spec: Mapping[str, Any]) -> bool:
    return bool(spec.get("external_project"))


def _code_servers(compose: dict[str, Any], spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    services = compose["services"]
    return {
        name: services[name]
        for name in spec.get("services") or []
        if name in services and _is_code_server(services[name])
    }


def _location(service: Mapping[str, Any]) -> str:
    argv = _words(service.get("command"))
    location = _flag(argv, "--location-name", "-l") or _flag(argv, "-m", "--module-name")
    assert location, f"code-server command에 `-m`도 `--location-name`도 없다: {argv}"
    return location


#: healthcheck argv에서 포트로 읽히는 낱말 — literal 숫자 또는 기본값이 숫자인 `${VAR:-N}`.
_PORT_WORD = re.compile(r"\d{2,5}|\$\{[A-Za-z_][A-Za-z0-9_]*:-\d{2,5}\}")


def _health_argv(service: Mapping[str, Any]) -> list[Any]:
    test = (service.get("healthcheck") or {}).get("test")
    return test if isinstance(test, list) else []


def _port(argv: list[str]) -> int:
    raw = _flag(argv, "-p", "--port")
    assert raw, f"command에 `-p`가 없다: {argv}"
    return int(_resolve(raw, {}))


def _legacy(compose: dict[str, Any], spec: Mapping[str, Any]) -> set[str]:
    """target의 옛 webserver·daemon과 그것에 기대는 서비스(gateway). 이름이 아니라 모양으로 찾는다."""

    services = compose["services"]
    code_servers = set(_code_servers(compose, spec))
    runners = {
        name
        for name, service in services.items()
        if (_runs(service, "dagster-webserver") or _runs(service, "dagster-daemon"))
        and _depends(service) & code_servers
    }
    return runners | {name for name, service in services.items() if _depends(service) & runners}


def _legacy_ports(compose: dict[str, Any], legacy: set[str]) -> set[int]:
    """옛 서비스가 듣는 포트 — webserver의 `-p`와 `ports:`의 컨테이너 쪽."""

    ports: set[int] = set()
    for name in legacy:
        service = compose["services"][name]
        if _runs(service, "dagster-webserver"):
            ports.add(_port(_words(service.get("command"))))
        for mapping in service.get("ports") or []:
            ports.add(int(_resolve(str(mapping), {}).rsplit(":", 1)[-1].split("/")[0]))
    return ports


def _plane(compose: dict[str, Any], targets: dict[str, Any]) -> dict[str, Any]:
    """공용 plane — 공용 workspace를 붙인 webserver·daemon, 그 앞 gateway, 그것을 가진 target."""

    services = compose["services"]
    mounted = {
        name
        for name, service in services.items()
        if any(str(volume).startswith(f"{_WORKSPACE_SOURCE}:") for volume in service.get("volumes") or [])
    }
    webservers = [name for name in mounted if _runs(services[name], "dagster-webserver")]
    daemons = [name for name in mounted if _runs(services[name], "dagster-daemon")]
    assert len(webservers) == 1 and len(daemons) == 1, (webservers, daemons)
    webserver = webservers[0]
    gateways = [name for name, service in services.items() if webserver in _depends(service)]
    assert len(gateways) == 1, f"공용 webserver 앞의 gateway를 하나로 못 찾았다: {gateways}"
    gateway = gateways[0]
    owners = [
        target_id
        for target_id, spec in targets["targets"].items()
        if webserver in (spec.get("services") or []) and target_id != "all"
    ]
    assert len(owners) == 1, owners
    containers = targets["containers"]
    public_env = [
        spec["prod_url_env"]
        for spec in containers.values()
        if spec.get("compose_service") == gateway and spec.get("prod_url_env")
    ]
    assert len(public_env) == 1, f"gateway 컨테이너의 prod_url_env를 하나로 못 찾았다: {public_env}"
    webserver_argv = _words(services[webserver]["command"])
    gateway_port = _split_top(str(services[gateway]["ports"][0]))[0]
    return {
        "target": owners[0],
        "webserver": webserver,
        "daemon": daemons[0],
        "gateway": gateway,
        "public_env": public_env[0],
        "internal_raw": f"http://127.0.0.1:{_flag(webserver_argv, '-p')}",
        "public_raw": f"${{{public_env[0]}:-http://127.0.0.1:{gateway_port}}}",
        "internal": f"http://127.0.0.1:{_port(webserver_argv)}",
    }


def _ordered_targets(targets: dict[str, Any]) -> list[str]:
    order = list(targets["dependency_order"])
    return order + [name for name in targets["targets"] if name not in order]


# ── 파생 ─────────────────────────────────────────────────────────────────


def _external_location(spec: Mapping[str, Any]) -> tuple[str, int] | None:
    """형제 프로젝트가 선언한 (location 이름, 포트) — 그 compose는 이 저장소 밖이라 선언이 정본이다."""

    external = (spec.get("dagster") or {}).get("external")
    if not _is_external(spec) or not isinstance(external, dict):
        return None
    return str(external["location_name"]), int(external["port"])


def _shared_locations(compose: dict[str, Any], spec: Mapping[str, Any]) -> list[tuple[str, int]]:
    """target이 공용 plane에 싣는 (location, 포트) — Manager target은 code-server command, 형제 프로젝트는 선언."""

    declared = _external_location(spec)
    if declared is not None:
        return [declared]
    return [
        (_location(service), _port(_words(service.get("command"))))
        for _, service in sorted(_code_servers(compose, spec).items())
    ]


def _derived_workspace(compose: dict[str, Any], targets: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for target_id in _ordered_targets(targets):
        spec = targets["targets"].get(target_id) or {}
        if (spec.get("dagster") or {}).get("control_plane") != "shared":
            continue
        for location, port in _shared_locations(compose, spec):
            entries.append({"grpc_server": {"host": "127.0.0.1", "port": port, "location_name": location}})
    return {"load_from": entries}


def _location_caps() -> set[str]:
    config = load_yaml_rejecting_duplicate_keys(_INSTANCE_CONFIG.read_text(encoding="utf-8"))
    limits = config["concurrency"]["runs"]["tag_concurrency_limits"]
    return {str(entry["value"]) for entry in limits if entry["key"] == _LOCATION_TAG}


def _g3b_violations(
    workspace: Mapping[str, Any], compose: dict[str, Any], targets: dict[str, Any], caps: set[str]
) -> list[str]:
    names = [entry["grpc_server"]["location_name"] for entry in workspace.get("load_from") or []]
    violations = [f"workspace에 location `{n}`이 두 번 있다" for n in sorted(set(names)) if names.count(n) > 1]
    violations += [
        f"workspace location `{name}`에 `{_LOCATION_TAG}` 상한이 없다 — 그 location은 전역 12만 받는다"
        for name in names
        if name not in caps
    ]
    for target_id, spec in targets["targets"].items():
        if (spec.get("dagster") or {}).get("control_plane") != "shared":
            continue
        for location, _ in _shared_locations(compose, spec):
            if location not in names:
                violations.append(f"shared target `{target_id}`의 location `{location}`이 workspace에 없다")
            if location not in caps:
                violations.append(f"shared target `{target_id}`의 location `{location}`에 상한이 없다")
    return violations


def _contract_violations(
    compose: dict[str, Any], targets: dict[str, Any], files: Mapping[str, bytes] | None = None
) -> list[str]:
    """스위치(`dagster.control_plane`)에서 파생한 기대와 렌더된 compose의 차이. 빈 목록이면 일치.

    `files`는 디스크 대신 쓸 설정 파일 내용(참조 전환이 만든 workspace)이다.
    """

    services = compose["services"]
    anchor = compose[_ANCHOR]
    plane = _plane(compose, targets)
    probe_env = {plane["public_env"]: _PUBLIC_PROBE}
    violations: list[str] = _digest_violations(compose, files)
    any_shared = False

    for target_id, spec in targets["targets"].items():
        if _is_external(spec) or target_id == "all":
            continue
        code_servers = _code_servers(compose, spec)
        block = spec.get("dagster")
        if block is None:
            if code_servers:
                violations.append(
                    f"`{target_id}`: code-server {sorted(code_servers)}가 있는데 `dagster.control_plane`이 없다"
                )
            continue
        if not code_servers:
            violations.append(f"`{target_id}`: `dagster`를 선언했는데 code-server가 없다")
            continue
        shared = block["control_plane"] == "shared"
        any_shared = any_shared or shared
        legacy = _legacy(compose, spec)
        if not legacy and not shared:
            violations.append(f"`{target_id}`: 옛 webserver·daemon을 모양으로 찾지 못했다")

        # (a) code-server
        for name, service in code_servers.items():
            environment = _environment(service)
            home = environment.get("DAGSTER_HOME")
            instance_path = f"{home}/dagster.yaml"
            mount = f"{_INSTANCE_SOURCE}:{instance_path}:ro"
            volumes = [str(volume) for volume in service.get("volumes") or []]
            carries = {key for key in anchor if key in environment}
            if shared:
                for key, value in anchor.items():
                    if environment.get(key) != value:
                        violations.append(f"(a) `{name}`: 공용 URL 앵커(`<<: *{_ANCHOR[2:]}`)의 `{key}`가 없다")
                if mount not in volumes:
                    violations.append(f"(a) `{name}`: 공용 dagster.yaml 마운트 `{mount}`가 없다")
                others = [v for v in volumes if _mount_target(v) == instance_path and v != mount]
                if others:
                    violations.append(f"(a) `{name}`: `{instance_path}`에 다른 마운트가 남았다: {others}")
                host = _flag(_words(service.get("command")), "-h", "--host")
                if host != "127.0.0.1":
                    violations.append(f"(a) `{name}`: gRPC가 `{host}`에서 듣는다 — 공용 plane은 loopback만")
                # workspace는 정적 파일이라 `.env`의 포트 override를 모른다 — `-p`는 literal이어야 둘이 갈리지 않는다.
                port = _flag(_words(service.get("command")), "-p", "--port") or ""
                if not port.isdigit():
                    violations.append(f"(a) `{name}`: `-p {port}`는 literal 포트여야 한다 — workspace가 그 값을 싣는다")
                # healthcheck가 포트를 따로 들면 `-p`와 같은 literal이다 — env override가 probe만 옮기면 healthy가 거짓이다.
                probe_ports = [str(w) for w in _health_argv(service) if _PORT_WORD.fullmatch(str(w))]
                if any(w != port for w in probe_ports):
                    violations.append(f"(a) `{name}`: healthcheck가 `-p {port}`와 다른 포트 {probe_ports}를 부른다")
            elif carries or mount in volumes:
                violations.append(f"(a) `{name}`: `own`인데 공용 URL·마운트를 받았다")

        # (c) 옛 webserver·daemon·gateway
        if shared:
            for name, paths in sorted(_hardcoded(legacy).items()):
                violations.append(
                    f"(pinned) `{name}`가 코드에 literal로 있다({', '.join(paths)}) — 그 참조를 스위치에서 "
                    f"파생하기 전에는 `{target_id}`를 옮길 수 없다(ADR-54, 적대 리뷰 M2)"
                )
        for name in sorted(legacy):
            profiles = services[name].get("profiles")
            if shared:
                if profiles != [_LEGACY_PROFILE]:
                    violations.append(f"(c) `{name}`: `profiles: [{_LEGACY_PROFILE}]`가 아니다({profiles})")
                for other_id, other in targets["targets"].items():
                    for field in ("services", "runtime_services"):
                        if name in (other.get(field) or []):
                            violations.append(f"(c) `{name}`: target `{other_id}`의 `{field}`에 남았다")
            elif profiles:
                violations.append(f"(c) `{name}`: `own`인데 profile {profiles}에 있다 — ensure가 띄우지 않는다")

        # (b) 소비자
        for consumer, variables in (block.get("consumers") or {}).items():
            if consumer not in (spec.get("services") or []):
                violations.append(f"(b) 소비자 `{consumer}`는 `{target_id}`의 서비스가 아니다")
                continue
            service = services[consumer]
            for variable, kind in variables.items():
                base, _, path = str(kind).partition("/")
                expected = (plane["internal"] if base == "internal" else _PUBLIC_PROBE) + (
                    f"/{path}" if path else ""
                )
                raw_values = [
                    source[variable]
                    for source in (_environment(service), _build_args(service))
                    if variable in source
                ]
                resolved = [_resolve(str(value), probe_env) for value in raw_values]
                if shared:
                    if not raw_values:
                        violations.append(f"(b) `{consumer}`: `{variable}`가 없다 — {kind}를 가리켜야 한다")
                    for value in resolved:
                        if value != expected:
                            violations.append(
                                f"(b) `{consumer}`: `{variable}`가 `{value}`로 풀린다 — `{expected}`여야 한다"
                            )
                else:
                    for value in resolved:
                        if value.startswith((plane["internal"], _PUBLIC_PROBE)):
                            violations.append(f"(b) `{consumer}`: `own`인데 `{variable}`가 공용 plane을 가리킨다")

        if shared:
            old_public = sorted(
                str(container["prod_url_env"])
                for container in targets["containers"].values()
                if container.get("compose_service") in legacy and container.get("prod_url_env")
            )
            for name, service in services.items():
                if service.get("profiles"):
                    continue
                for source in (_environment(service), _build_args(service)):
                    for variable, value in source.items():
                        for env_name in old_public:
                            if re.search(rf"\$\{{{env_name}[:}}?+-]", str(value)):
                                violations.append(
                                    f"(b) `{name}`: `{variable}`가 옛 공개 host env `{env_name}`를 부른다"
                                )
            ports = _legacy_ports(compose, legacy)
            alternatives = "|".join(map(str, sorted(ports)))
            pattern = re.compile(rf"(?:127\.0\.0\.1|localhost):({alternatives})(?!\d)")
            for name, service in services.items():
                if service.get("profiles"):
                    continue
                for source in (_environment(service), _build_args(service)):
                    for variable, value in source.items():
                        if ports and pattern.search(_resolve(str(value), probe_env)):
                            violations.append(
                                f"(b) `{name}`: `{variable}`가 옛 plane(`{target_id}`)의 포트를 부른다"
                            )

    # 활성 서비스는 옛 plane에 기대지 않는다 — compose는 꺼진 profile의 서비스를 끌어오지 못한다.
    for name, service in services.items():
        if service.get("profiles"):
            continue
        for dependency in sorted(_depends(service)):
            if (services.get(dependency) or {}).get("profiles") == [_LEGACY_PROFILE]:
                violations.append(f"(c) 활성 `{name}`이 `{_LEGACY_PROFILE}`의 `{dependency}`에 기댄다")

    plane_spec = targets["targets"][plane["target"]]
    if any_shared and (
        plane_spec.get("excluded_from_all") is True
        or plane["target"] not in (targets["targets"]["all"].get("include") or [])
    ):
        violations.append(f"공용 plane target `{plane['target']}`이 `all`에 없다 — 합류한 target이 있으면 넣는다")
    return violations


# ── 전환의 참조 구현(모델 위에서) ─────────────────────────────────────────

#: `_flip`이 하는 단계. `skip`으로 하나를 빼면 계약이 그 단계를 이름으로 말해야 한다.
_STEPS = (
    "env", "mount", "loopback", "port", "profile", "services", "depends", "consumers", "all", "digest",
)


def _flip(
    compose: dict[str, Any], targets: dict[str, Any], target_id: str, *, skip: str = ""
) -> dict[str, bytes]:
    """target 하나를 `shared`로 — 전환 PR이 compose·targets에 하는 편집 그대로(주석만 빼고).

    전환 PR이 새로 쓰는 파일(파생 workspace)의 내용을 돌려준다 — 계약은 그것으로 digest를 대조한다.
    """

    services = compose["services"]
    spec = targets["targets"][target_id]
    plane = _plane(compose, targets)
    legacy = _legacy(compose, spec)
    spec["dagster"]["control_plane"] = "shared"
    for service in _code_servers(compose, spec).values():
        environment = _environment(service)
        if skip != "env":
            environment.update(compose[_ANCHOR])
        instance_path = f"{environment['DAGSTER_HOME']}/dagster.yaml"
        if skip != "mount":
            # 그 자리의 옛 마운트(weather의 `deploy/dagster.yaml`)를 공용 파일로 바꾼다.
            service["volumes"] = [
                v for v in service.get("volumes") or [] if _mount_target(v) != instance_path
            ] + [f"{_INSTANCE_SOURCE}:{instance_path}:ro"]
        if skip not in ("mount", "digest"):
            environment[_DIGEST_ENV[_INSTANCE_SOURCE]] = _digest(_file_bytes(_INSTANCE_SOURCE, None))
        argv = service["command"]
        if skip != "loopback" and "-h" in argv:
            argv[argv.index("-h") + 1] = "127.0.0.1"
        if skip != "port":
            port = str(_port(argv))
            argv[argv.index("-p") + 1] = port
            health = _health_argv(service)
            for index, word in enumerate(health):
                if _PORT_WORD.fullmatch(str(word)) and _resolve(str(word), {}) == port:
                    health[index] = port
    for name in legacy:
        if skip != "profile":
            services[name]["profiles"] = [_LEGACY_PROFILE]
        if skip != "services":
            for other in targets["targets"].values():
                for field in ("services", "runtime_services"):
                    if name in (other.get(field) or []):
                        other[field].remove(name)
    if skip != "depends":
        for name, service in services.items():
            if name in legacy or service.get("profiles"):
                continue
            dependencies = service.get("depends_on")
            if isinstance(dependencies, dict):
                for dependency in legacy & set(dependencies):
                    del dependencies[dependency]
    if skip != "consumers":
        for consumer, variables in (spec["dagster"].get("consumers") or {}).items():
            service = services[consumer]
            for variable, kind in variables.items():
                base, _, path = str(kind).partition("/")
                value = (plane["internal_raw"] if base == "internal" else plane["public_raw"]) + (
                    f"/{path}" if path else ""
                )
                service.setdefault("environment", {})[variable] = value
                if variable in _build_args(service):
                    service["build"]["args"][variable] = value
    if skip != "all":
        plane_spec = targets["targets"][plane["target"]]
        plane_spec.pop("excluded_from_all", None)
        include = targets["targets"]["all"].setdefault("include", [])
        if plane["target"] not in include:
            include.append(plane["target"])
    rendered = yaml.safe_dump(_derived_workspace(compose, targets), sort_keys=False).encode()
    if skip != "digest":
        for name in (plane["webserver"], plane["daemon"]):
            _environment(services[name])[_DIGEST_ENV[_WORKSPACE_SOURCE]] = _digest(rendered)
    else:
        # 원문 포맷이 현재 파생 결과와 같아도 누락 대조군은 실제로 digest를 누락시킨다.
        for name in (plane["webserver"], plane["daemon"]):
            _environment(services[name]).pop(_DIGEST_ENV[_WORKSPACE_SOURCE], None)
    return {_WORKSPACE_SOURCE: rendered}


def _unflip(compose: dict[str, Any], targets: dict[str, Any], target_id: str) -> None:
    """합류한 target 하나를 **전환 전 `own` 모양**으로 되돌린다(모델 위에서) — 대조군의 출발점.

    2026-10-01 Map 전환으로 `own`인 target이 남지 않았다. `_flip`에서 한 단계를 뺀 대조군은 전환 전 모양이 있어야
    하므로 그 모양을 되살린다: 스위치 `own`, code-server에서 공용 URL 앵커·instance digest·공용 `dagster.yaml`
    마운트를 빼고 `-p`를 env 보간식으로, 옛 webserver·daemon(과 gateway)을 profile 밖으로 내어 target의 `services`·
    `runtime_services`에 돌려놓고, 소비자를 옛 plane(옛 webserver 포트, 옛 공개 host env)으로 되돌린다.
    """

    services = compose["services"]
    spec = targets["targets"][target_id]
    spec["dagster"]["control_plane"] = "own"
    anchor_keys = set(compose[_ANCHOR])
    for service in _code_servers(compose, spec).values():
        environment = _environment(service)
        for key in (*anchor_keys, _DIGEST_ENV[_INSTANCE_SOURCE]):
            environment.pop(key, None)
        service["volumes"] = [
            v for v in service.get("volumes") or [] if _split_top(str(v))[0] != _INSTANCE_SOURCE
        ]
        if not service["volumes"]:
            del service["volumes"]
        argv = service["command"]
        port = _port(argv)
        argv[argv.index("-p") + 1] = f"${{UNFLIPPED_CODE_SERVER_PORT:-{port}}}"
    legacy = _legacy(compose, spec)
    for name in sorted(legacy):
        services[name].pop("profiles", None)
        for field in ("services", "runtime_services"):
            if name not in spec.get(field, []):
                spec.setdefault(field, []).append(name)
    # 옛 webserver: 서버 쪽 소비자(`internal`)는 그 listen 포트(`-p`의 원문)로, 브라우저 쪽(`public`)은 옛 공개 host
    # env와 host에 publish한 포트(`ports:`의 host 쪽 원문)로 붙었다 — 전환 전 compose가 그렇게 적었다.
    webserver = next(services[name] for name in sorted(legacy) if _runs(services[name], "dagster-webserver"))
    listen_raw = str(_flag(_words(webserver.get("command")), "-p", "--port"))
    published = [str(mapping) for mapping in webserver.get("ports") or []]
    host_raw = _split_top(published[0])[0] if published else listen_raw
    old_public = sorted(
        str(container["prod_url_env"])
        for container in targets["containers"].values()
        if container.get("compose_service") in legacy and container.get("prod_url_env")
    )
    for consumer, variables in (spec["dagster"].get("consumers") or {}).items():
        service = services[consumer]
        for variable, kind in variables.items():
            base, _, path = str(kind).partition("/")
            if base == "public" and old_public:
                old = f"${{{old_public[0]}:-http://127.0.0.1:{host_raw}}}"
            else:
                old = f"http://127.0.0.1:{listen_raw}"
            value = old + (f"/{path}" if path else "")
            service.setdefault("environment", {})[variable] = value
            if variable in _build_args(service):
                service["build"]["args"][variable] = value


def _own_pair_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    """체크아웃 모델에서 pinned pair(Map·PinVi)의 스위치만 `own`으로 되돌린 문서 — #447 기제 테스트의 기준선.

    family 파생은 스위치와 모양만 본다. PinVi(뒤에 Map)가 실제로 합류한 뒤에도 pinned 재구축·C6c·보존 테스트는
    "모두 own" 위에 `_flip`을 얹어 본다(conftest의 `own_pinned_pair`와 같은 기준선).
    """

    compose, targets = _documents()
    # 스위치만이 아니라 compose도 전환 전 모양으로 — 둘이 함께 `own`이어야 기준선이 일관된다(Map 전환 뒤).
    for target_id in ("map", "pinvi"):
        _unflip(compose, targets, target_id)
    return compose, targets


def _dagster_targets() -> list[str]:
    """Manager 자신의 compose에 Dagster가 있는 target(모양에서 파생한다)."""

    _, targets = _documents()
    return [name for name, spec in targets["targets"].items() if "dagster" in spec and not _is_external(spec)]


def _external_dagster_targets() -> list[str]:
    """형제 프로젝트 target(transport) — compose가 밖에 있어 선언(`dagster.external`)이 모양이다."""

    _, targets = _documents()
    return [name for name, spec in targets["targets"].items() if "dagster" in spec and _is_external(spec)]


def _flip_external(targets: dict[str, Any], compose: dict[str, Any], target_id: str) -> dict[str, bytes]:
    """형제 프로젝트 하나를 `shared`로 — Manager 쪽 전환은 스위치와 파생 workspace(그 digest)뿐이다.

    code-server·옛 서비스의 compose 편집은 그 저장소의 전환 PR이 한다(그 저장소의 계약 테스트가 본다).
    """

    targets["targets"][target_id]["dagster"]["control_plane"] = "shared"
    plane = _plane(compose, targets)
    rendered = yaml.safe_dump(_derived_workspace(compose, targets), sort_keys=False).encode()
    for name in (plane["webserver"], plane["daemon"]):
        _environment(compose["services"][name])[_DIGEST_ENV[_WORKSPACE_SOURCE]] = _digest(rendered)
    return {_WORKSPACE_SOURCE: rendered}


# ── 테스트 ───────────────────────────────────────────────────────────────


def test_the_derivation_sees_what_it_derives_from() -> None:
    """추출이 낡으면 아래 검사들이 조용히 항진명제가 된다 — 본 것을 센다."""

    compose, targets = _documents()
    dagster_targets = _dagster_targets()
    assert len(dagster_targets) >= 4, dagster_targets
    for target_id in dagster_targets:
        spec = targets["targets"][target_id]
        assert _code_servers(compose, spec), target_id
        legacy = _legacy(compose, spec)
        if legacy:
            assert any(_runs(compose["services"][n], "dagster-webserver") for n in legacy), (target_id, legacy)
            assert any(_runs(compose["services"][n], "dagster-daemon") for n in legacy), (target_id, legacy)
        else:
            assert spec["dagster"]["control_plane"] == "shared", target_id
        assert spec["dagster"].get("consumers"), f"`{target_id}`가 소비자 env를 선언하지 않는다"
    plane = _plane(compose, targets)
    assert plane["internal"].startswith("http://127.0.0.1:")
    # weather의 gateway는 webserver에 기대므로 옛 plane에 든다(모양으로 찾았다).
    weather = targets["targets"]["weather"]
    assert any(name.endswith("gateway") for name in _legacy(compose, weather))


def test_the_workspace_file_is_the_derivation() -> None:
    """`workspace.yaml`은 손으로 쓰지 않는다 — `shared` target의 code-server에서 만든 것과 같아야 한다."""

    compose, targets = _documents()
    derived = _derived_workspace(compose, targets)
    actual = load_yaml_rejecting_duplicate_keys(_WORKSPACE.read_text(encoding="utf-8"))
    assert actual == derived, (
        "공용 workspace가 파생과 다르다. `config/dagster-shared/workspace.yaml`의 본문을 이것으로:\n"
        + yaml.safe_dump(derived, sort_keys=False)
    )


def test_g3b_workspace_locations_and_location_caps_agree() -> None:
    """G3-b: workspace의 location마다 상한이 있고, `shared` target의 location은 모두 workspace에 있다."""

    compose, targets = _documents()
    workspace = load_yaml_rejecting_duplicate_keys(_WORKSPACE.read_text(encoding="utf-8"))
    assert _g3b_violations(workspace, compose, targets, _location_caps()) == []


def test_the_rendered_compose_matches_every_control_plane_switch() -> None:
    compose, targets = _documents()
    assert _contract_violations(compose, targets) == []


def _pinned(violations: list[str]) -> tuple[list[str], list[str]]:
    return [v for v in violations if v.startswith("(pinned)")], [
        v for v in violations if not v.startswith("(pinned)")
    ]


@pytest.mark.parametrize("target_id", _dagster_targets())
def test_flipping_a_target_renders_a_consistent_plane(target_id: str) -> None:
    """참조 전환을 하면 compose 계약·digest·workspace 파생·G3-b가 초록이고, `(pinned)`도 없다.

    Map·PinVi·geo의 옛 webserver·daemon을 literal로 들던 pinned 재구축·C6c·이미지 보존·M05·옛 override
    이관은 이제 `runtime_topology`에서 파생한다 — 어느 target도 코드 literal에 막히지 않는다. literal이
    되돌아오면 빨갛다(`test_a_literal_old_name_in_code_blocks_the_flip`).
    """

    compose, targets = _documents()
    files = _flip(compose, targets, target_id)
    pinned, rest = _pinned(_contract_violations(compose, targets, files))
    assert rest == []
    assert pinned == []
    workspace = _derived_workspace(compose, targets)
    location = {_location(s) for s in _code_servers(compose, targets["targets"][target_id]).values()}
    # 이미 합류한 target(weather)의 location도 함께 있다 — 이 target의 것이 더해지고, 나머지는 `shared`인 target의 것이다.
    names = {e["grpc_server"]["location_name"] for e in workspace["load_from"]}
    shared = {
        location
        for spec in targets["targets"].values()
        if (spec.get("dagster") or {}).get("control_plane") == "shared"
        for location, _ in _shared_locations(compose, spec)
    }
    assert location <= names and names == shared
    assert _g3b_violations(workspace, compose, targets, _location_caps()) == []


def test_flipping_every_target_keeps_the_plane_consistent() -> None:
    """transport가 합류할 때와 같은 모양 — 네 target이 모두 `shared`여도 서로 밟지 않는다."""

    compose, targets = _documents()
    files: dict[str, bytes] = {}
    for target_id in _dagster_targets():
        files = _flip(compose, targets, target_id)
    for target_id in _external_dagster_targets():
        files = _flip_external(targets, compose, target_id)
    _, rest = _pinned(_contract_violations(compose, targets, files))
    assert rest == []
    workspace = _derived_workspace(compose, targets)
    assert len(workspace["load_from"]) == len(_dagster_targets()) + len(_external_dagster_targets())
    assert _g3b_violations(workspace, compose, targets, _location_caps()) == []


@pytest.mark.parametrize(
    ("target_id", "skip", "named"),
    [
        # 네 target 모두 합류했다(2026-10-01 Map 전환) — 대조군은 Map을 `_unflip`으로 전환 전 모양에 되돌린 뒤 한 단계를
        # 뺀 `_flip`이다. 합류한 target에만 있던 모양(옛 마운트가 남음, 활성 서비스가 옛 gateway에 기댐, `all` 누락)은
        # committed-state 대조군이 본다.
        ("map", "env", "(a) `kor-travel-map-dagster-code-server`: 공용 URL 앵커"),
        ("map", "mount", "(a) `kor-travel-map-dagster-code-server`: 공용 dagster.yaml 마운트"),
        # Map은 이미 loopback이다 — 대조군은 geo가 전환 전에 가졌던 `-h 0.0.0.0`을 먼저 입힌다(아래 테스트).
        ("map", "loopback", "gRPC가 `0.0.0.0`에서 듣는다"),
        ("map", "profile", "(c) `kor-travel-map-dagster`: `profiles: [legacy-dagster]`가 아니다"),
        ("map", "services", "(c) `kor-travel-map-dagster`: target `map`의 `services`에 남았다"),
        ("map", "consumers", "(b) `kor-travel-map-api`: `KOR_TRAVEL_MAP_API_DAGSTER_URL`가"),
        ("map", "consumers", "(b) `kor-travel-map-ui`: `NEXT_PUBLIC_KOR_TRAVEL_MAP_DAGSTER_URL`가 옛 plane(`map`)의 포트"),
        ("map", "port", "`-p ${UNFLIPPED_CODE_SERVER_PORT:-12703}`는 literal 포트여야 한다"),
        ("map", "digest", "(d) `kor-travel-dagster-daemon`: `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST`"),
        ("map", "digest", "(d) `kor-travel-map-dagster-code-server`"),
        ("map", "consumers", "옛 공개 host env `KTDM_PROD_URL_MAP_DAGSTER`"),
    ],
)
def test_a_flip_missing_a_step_is_named(target_id: str, skip: str, named: str) -> None:
    """전환 PR이 한 단계를 빠뜨리면 계약이 그 단계를 이름으로 말한다(빨간 대조군)."""

    compose, targets = _documents()
    _unflip(compose, targets, target_id)
    if skip == "loopback":
        for service in _code_servers(compose, targets["targets"][target_id]).values():
            argv = service["command"]
            argv[argv.index("-h") + 1] = "0.0.0.0"
    files = _flip(compose, targets, target_id, skip=skip)
    violations = _contract_violations(compose, targets, files)
    assert any(named in violation for violation in violations), violations


@pytest.mark.parametrize("target_id", ["map", "pinvi", "geo", "weather"])
def test_a_literal_old_name_in_code_blocks_the_flip(
    target_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """옛 서비스 이름이 코드에 literal로 되돌아오면 그 target의 전환은 `(pinned)`로 빨갛다(빨간 대조군).

    이름은 compose에서 모양으로 찾은 옛 서비스마다 하나씩 넣어 본다 — 검사가 이름 목록이 아니라 파생을 보는지.
    """

    compose, targets = _documents()
    legacy = sorted(_legacy(compose, targets["targets"][target_id]))
    files = _flip(compose, targets, target_id)
    assert _pinned(_contract_violations(compose, targets, files))[0] == []
    code = tmp_path / "scripts"
    code.mkdir()
    monkeypatch.setattr(sys.modules[__name__], "_CODE_ROOTS", (*_CODE_ROOTS, code))
    for name in legacy:
        (code / "revert.py").write_text(f'compose_up("{name}")\n', encoding="utf-8")
        pinned, _ = _pinned(_contract_violations(compose, targets, files))
        assert [v for v in pinned if f"`{name}`" in v], (name, pinned)
    # 같은 이름이 더 긴 토큰의 일부(`<이름>-code-server`, 컨테이너 `<이름>-latest`)일 때는 참조가 아니다.
    (code / "revert.py").write_text(
        "\n".join(f'"{name}-latest"' for name in legacy) + "\n", encoding="utf-8"
    )
    assert _pinned(_contract_violations(compose, targets, files))[0] == []


def test_the_manager_derivation_agrees_with_this_one() -> None:
    """Manager의 `runtime_topology`가 모양으로 찾은 family가 이 테스트의 독립 파생과 같다 — 전환 전후 모두."""

    from kor_travel_docker_manager.services.runtime_topology import derive_dagster_families

    compose, targets = _documents()
    for flipped in [None, *_dagster_targets()]:
        if flipped is not None:
            _flip(compose, targets, flipped)
        families = derive_dagster_families(compose, targets)
        assert sorted(families) == sorted(_dagster_targets())
        for target_id, family in families.items():
            spec = targets["targets"][target_id]
            assert set(family.legacy) == _legacy(compose, spec), target_id
            assert {family.code_server} == set(_code_servers(compose, spec)), target_id
            assert family.control_plane == spec["dagster"]["control_plane"], target_id


def _non_reloadable_shared_code_servers(compose: dict[str, Any], targets: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(`shared` target의 code-server 전부, 그중 `api grpc`인 것) — 이름이 아니라 스위치와 모양에서."""

    shared: set[str] = set()
    for target_id in _dagster_targets():
        spec = targets["targets"][target_id]
        if spec["dagster"].get("control_plane") == "shared":
            shared |= set(_code_servers(compose, spec))
    stuck = {
        name
        for name in shared
        if _code_server_subcommand(compose["services"][name]) == _NON_RELOADABLE
    }
    return shared, stuck


def test_every_shared_plane_code_server_reloads_its_definitions() -> None:
    """공용 webserver의 location reload(`ReloadCode`)가 definitions를 다시 import해야 한다.

    `dagster api grpc`는 reload를 "not currently supported" 경고만 남기고 무시한다 — 2026-10-01 n150에서 Map의
    C7 schedule override(definitions import 때 읽는다)가 그래서 반영되지 않았다. `dagster code-server start`는
    proxy가 자식 gRPC 프로세스를 새로 띄워 다시 import한다(같은 n150 이미지로 실측). 예외는 없다.
    """

    compose, targets = _documents()
    shared, stuck = _non_reloadable_shared_code_servers(compose, targets)
    # 본 것에 하한을 건다 — 개수가 아니라 **본 location**이 공용 webserver가 싣는 workspace의 location 전부여야
    # 한다. 추출이 낡아 code-server 하나라도 놓치면 여기서 빨갛다(아래 단언이 항진이 되지 않는다).
    workspace = load_yaml_rejecting_duplicate_keys(_WORKSPACE.read_text(encoding="utf-8"))
    loaded = {entry["grpc_server"]["location_name"] for entry in workspace["load_from"]}
    assert loaded, "공용 workspace에 location이 없다 — 이 검사가 공허하다"
    # 형제 프로젝트(transport)의 code-server는 그 저장소의 compose에 있어 여기서 볼 수 없다 — 같은 규칙(`code-server
    # start`, 공용 probe, heartbeat, init)을 창 스크립트의 derive가 그 렌더에 건다(`test_dagster_shared_cutover_script.py`
    # 의 빨간 대조군). 그 location을 빼면 나머지는 이 compose의 code-server가 전부 싣는다.
    external = {
        location
        for target_id in _external_dagster_targets()
        if targets["targets"][target_id]["dagster"].get("control_plane") == "shared"
        for location, _ in _shared_locations(compose, targets["targets"][target_id])
    }
    assert {_location(compose["services"][name]) for name in shared} == loaded - external, sorted(shared)
    assert stuck == set(), (
        f"공용 plane code-server가 `dagster api grpc`다 — `dagster code-server start`로: {sorted(stuck)}"
    )


def test_a_shared_code_server_reverted_to_api_grpc_is_named() -> None:
    """빨간 대조군: 합류한 code-server 하나를 `api grpc`로 되돌리면 이름으로 잡힌다."""

    compose, targets = _documents()
    shared, stuck = _non_reloadable_shared_code_servers(compose, targets)
    victim = sorted(shared - stuck)[0]
    argv = compose["services"][victim]["command"]
    start = argv.index("code-server")
    argv[start : start + 2] = ["api", "grpc"]
    assert victim in _non_reloadable_shared_code_servers(compose, targets)[1]


@pytest.mark.parametrize(
    ("undo", "named"),
    [
        ("mount", "`/opt/dagster/home/dagster.yaml`에 다른 마운트가 남았다"),
        ("depends", "활성 `kor-travel-weather-web`이 `legacy-dagster`의 `kor-travel-weather-dagster-gateway`"),
        ("profile", "(c) `kor-travel-weather-dagster-gateway`: `profiles: [legacy-dagster]`가 아니다"),
        ("all", "공용 plane target `dagster`이 `all`에 없다"),
        ("consumer", "(b) `kor-travel-weather-web`: `DAGSTER_UI_INTERNAL_URL`가 `http://127.0.0.1:14107`로 풀린다"),
    ],
)
def test_undoing_one_part_of_the_committed_weather_flip_is_named(undo: str, named: str) -> None:
    """합류한 weather의 렌더에서 한 단계를 되돌리면 계약이 그 단계를 이름으로 말한다(빨간 대조군)."""

    compose, targets = _documents()
    assert targets["targets"]["weather"]["dagster"]["control_plane"] == "shared"
    services = compose["services"]
    if undo == "mount":
        services["kor-travel-weather-dagster-code-server"]["volumes"].append(
            "${KOR_TRAVEL_WEATHER_REPO_DIR:-../kor-travel-weather}/deploy/dagster.yaml:/opt/dagster/home/dagster.yaml:ro"
        )
    elif undo == "depends":
        services["kor-travel-weather-web"]["depends_on"]["kor-travel-weather-dagster-gateway"] = {
            "condition": "service_started"
        }
    elif undo == "profile":
        del services["kor-travel-weather-dagster-gateway"]["profiles"]
    elif undo == "all":
        targets["targets"]["all"]["include"].remove("dagster")
    elif undo == "consumer":
        services["kor-travel-weather-web"]["environment"]["DAGSTER_UI_INTERNAL_URL"] = "http://127.0.0.1:14107"
    violations = _contract_violations(compose, targets)
    assert any(named in violation for violation in violations), violations


def test_the_committed_digests_follow_the_files() -> None:
    """설치본 symlink 너머의 파일 내용이 바뀌면 상시 서비스가 재생성되도록 digest가 내용과 같다(H1)."""

    compose, _ = _documents()
    assert _digest_violations(compose) == []
    bound = [
        (name, source)
        for name, service in compose["services"].items()
        for source in (_split_top(str(v))[0] for v in service.get("volumes") or [])
        if source in _DIGEST_ENV and service.get("restart") != "no"
    ]
    # daemon·webserver가 dagster.yaml과 workspace를, gateway가 gateway.conf를 붙인다.
    assert len(bound) >= 5, bound
    changed = {_WORKSPACE_SOURCE: _WORKSPACE.read_bytes() + b"# changed\n"}
    assert len(_digest_violations(compose, changed)) == 2


@pytest.mark.parametrize(
    "volume",
    [
        "./config/dagster-shared:/opt/dagster/dagster_home:ro",
        "/opt/kor-travel-docker-manager/config/dagster-shared/workspace.yaml:/w.yaml:ro",
        {"type": "bind", "source": "${PWD}/config/dagster-shared/dagster.yaml", "target": "/d.yaml"},
    ],
)
def test_a_shared_config_mount_the_digest_cannot_read_is_named(volume: object) -> None:
    """digest가 모르는 모양(디렉터리·절대 경로·긴 형식)으로 붙이면 건너뛰지 않고 빨갛다(재리뷰 LOW-2)."""

    compose, _ = _documents()
    compose["services"]["kor-travel-dagster-daemon"]["volumes"].append(volume)
    assert any("모르는 모양" in v for v in _digest_violations(compose)), volume


def test_a_switch_without_its_rendering_is_named_and_the_workspace_drifts() -> None:
    """스위치만 뒤집고 compose를 그대로 두면 (a)·(b)·(c)가 모두 빨갛고, workspace가 파생과 어긋난다."""

    compose, targets = _documents()
    _unflip(compose, targets, "map")
    # 커밋된 workspace는 Map을 싣는다 — `own` 모델의 파생과 어긋난다(전환 전 모양 + 전환 후 workspace).
    actual = load_yaml_rejecting_duplicate_keys(_WORKSPACE.read_text(encoding="utf-8"))
    assert actual != _derived_workspace(compose, targets)
    targets["targets"]["map"]["dagster"]["control_plane"] = "shared"
    violations = _contract_violations(compose, targets)
    for step in ("(a) `kor-travel-map-dagster-code-server`", "(b) `kor-travel-map-api`", "(c) `kor-travel-map-dagster`"):
        assert any(v.startswith(step) for v in violations), (step, violations)


def test_own_targets_carrying_shared_parts_are_named() -> None:
    """`own`인데 공용 URL을 받았거나 옛 서비스가 profile로 내려갔거나 소비자가 공용을 가리키면 빨갛다."""

    compose, targets = _documents()
    _unflip(compose, targets, "map")
    services = compose["services"]
    services["kor-travel-map-dagster-code-server"]["environment"].update(compose[_ANCHOR])
    services["kor-travel-map-dagster-daemon"]["profiles"] = [_LEGACY_PROFILE]
    services["kor-travel-map-api"]["environment"]["KOR_TRAVEL_MAP_API_DAGSTER_URL"] = (
        _plane(compose, targets)["internal_raw"]
    )
    violations = _contract_violations(compose, targets)
    for named in (
        "(a) `kor-travel-map-dagster-code-server`: `own`인데",
        "(c) `kor-travel-map-dagster-daemon`: `own`인데",
        "(b) `kor-travel-map-api`: `own`인데",
    ):
        assert any(named in v for v in violations), (named, violations)


def test_g3b_names_a_location_without_a_cap() -> None:
    compose, targets = _documents()
    _flip(compose, targets, "map")
    workspace = _derived_workspace(compose, targets)
    caps = _location_caps() - {"kortravelmap.dagster.definitions"}
    violations = _g3b_violations(workspace, compose, targets, caps)
    assert any("상한이 없다" in v for v in violations), violations
    stale = {"load_from": []}
    assert any("workspace에 없다" in v for v in _g3b_violations(stale, compose, targets, _location_caps()))


def test_a_code_server_target_without_the_switch_is_named() -> None:
    """code-server가 생긴 target(stage T의 transport)은 스위치를 적어야 한다."""

    compose, targets = _documents()
    del targets["targets"]["geo"]["dagster"]
    assert any("`dagster.control_plane`이 없다" in v for v in _contract_violations(compose, targets))


# ── 형제 프로젝트(transport): compose가 밖에 있어 선언이 모양이다 ──────────────────


def test_external_targets_declare_a_shape_the_manager_derives() -> None:
    """선언한 형제 프로젝트를 본다(항진명제 방지) — Manager의 family·location 소유와 상한이 그 선언을 싣는다."""

    from kor_travel_docker_manager.services.runtime_topology import (
        derive_external_dagster_family,
        external_dagster_locations,
        installed_location_owners,
    )

    compose, targets = _documents()
    externals = _external_dagster_targets()
    assert externals == ["transport"], externals
    locations = external_dagster_locations(targets)
    owners = installed_location_owners()
    internal_locations = {
        location for target_id in _dagster_targets() for location, _ in _shared_locations(compose, targets["targets"][target_id])
    }
    for target_id in externals:
        spec = targets["targets"][target_id]
        family = derive_external_dagster_family(targets, target_id)
        project = spec["external_project"]["project"]
        assert family.external_project == project
        assert family.control_plane == spec["dagster"]["control_plane"]
        # 옛 서비스의 컨테이너는 그 project의 compose 기본 이름이다(Manager `containers` 레지스트리에 없다).
        assert {family.container_name(name) for name in family.legacy} == {
            f"{project}-{name}-1" for name in (family.webserver, family.daemon, *family.gateways)
        }
        location, port = locations[target_id]
        assert location not in internal_locations
        # 상한은 스위치와 무관하다(Manager target의 상한이 stage 2부터 있던 것과 같다).
        assert location in _location_caps()
        # 펜스 검사(`_require_plane_location_owners_fenced`)가 이 location의 옛 서비스를 찾는다.
        assert owners[location].target == target_id
        assert owners[location].legacy == family.legacy
        assert 1 <= port <= 65535


def test_flipping_an_external_target_adds_exactly_its_declared_location() -> None:
    compose, targets = _documents()
    for target_id in _external_dagster_targets():
        targets["targets"][target_id]["dagster"]["control_plane"] = "own"
        before = _derived_workspace(compose, targets)["load_from"]
        files = _flip_external(targets, compose, target_id)
        after = _derived_workspace(compose, targets)["load_from"]
        location, port = _external_location(targets["targets"][target_id]) or ("", 0)
        added = [entry for entry in after if entry not in before]
        assert added == [{"grpc_server": {"host": "127.0.0.1", "port": port, "location_name": location}}]
        assert yaml.safe_load(files[_WORKSPACE_SOURCE]) == {"load_from": after}
        assert _g3b_violations({"load_from": after}, compose, targets, _location_caps()) == []
        assert _digest_violations(compose, files) == []


def test_an_external_target_on_the_plane_without_its_workspace_entry_or_cap_is_named() -> None:
    """형제 프로젝트를 `shared`로 두고 workspace·상한을 빠뜨리면 G3-b가 그 location을 이름으로 말한다(빨간 대조군)."""

    compose, targets = _documents()
    for target_id in _external_dagster_targets():
        targets["targets"][target_id]["dagster"]["control_plane"] = "shared"
        location, _ = _external_location(targets["targets"][target_id]) or ("", 0)
        workspace = _derived_workspace(compose, targets)
        stale = {"load_from": [e for e in workspace["load_from"] if e["grpc_server"]["location_name"] != location]}
        named = f"shared target `{target_id}`의 location `{location}`"
        assert any(v.startswith(named) and "workspace에 없다" in v for v in _g3b_violations(stale, compose, targets, _location_caps()))
        uncapped = _g3b_violations(workspace, compose, targets, _location_caps() - {location})
        assert any(f"`{location}`에 `{_LOCATION_TAG}` 상한이 없다" in v for v in uncapped), uncapped


def test_the_resolver_reads_nested_defaults() -> None:
    assert _resolve("${A:-${B:-x}}/y", {}) == "x/y"
    assert _resolve("${A:-${B:-x}}", {"B": "b"}) == "b"
    assert _resolve("${A:-z}", {"A": ""}) == "z"
    assert _resolve("${A-z}", {"A": ""}) == ""
    assert _resolve("[1${H:+,\"${H}\"}]", {"H": "h"}) == '[1,"h"]'
    assert _resolve("$$x", {}) == "$x"


# ── 공용 webserver probe(모든 location) ─────────────────────────────────


def _probe_argv() -> list[str]:
    compose, targets = _documents()
    plane = _plane(compose, targets)
    webserver = compose["services"][plane["webserver"]]
    test = webserver["healthcheck"]["test"]
    return [str(part) for part in test]


def test_the_shared_probe_reads_the_mounted_workspace_and_the_listen_port() -> None:
    """probe의 인자는 렌더된 모델에서 온다 — 붙인 workspace 경로와 webserver의 `-p`."""

    compose, targets = _documents()
    plane = _plane(compose, targets)
    webserver = compose["services"][plane["webserver"]]
    argv = _probe_argv()
    assert argv[:4] == ["CMD", "python", "-I", "-c"], "exec 형식, dagster import 없는 python"
    command = _words(webserver["command"])
    assert argv[5] == _flag(command, "-p")
    workspace_mount = next(
        v for v in webserver["volumes"] if str(v).startswith(f"{_WORKSPACE_SOURCE}:")
    )
    assert argv[6] == _mount_target(workspace_mount) == _flag(command, "-w")
    assert "import dagster" not in argv[4] and "from dagster" not in argv[4]
    assert webserver.get("init") is True
    # probe의 자체 timeout이 healthcheck timeout보다 짧다 — 스스로 끝난다.
    timeout = int(re.search(r"timeout=(\d+)", argv[4]).group(1))  # type: ignore[union-attr]
    assert timeout < int(str(webserver["healthcheck"]["timeout"]).rstrip("s"))


class _GraphQL(http.server.BaseHTTPRequestHandler):
    reply: dict[str, Any] = {}

    def do_POST(self) -> None:  # noqa: N802 - http.server 규약
        length = int(self.headers.get("Content-Length", "0"))
        query = json.loads(self.rfile.read(length))["query"]
        assert "workspaceOrError" in query and "locationOrLoadError" in query
        body = json.dumps(self.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def graphql_server() -> Iterator[tuple[int, type[_GraphQL]]]:
    handler = type("Handler", (_GraphQL,), {"reply": {}})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], handler
    finally:
        server.shutdown()
        server.server_close()


#: 버전 상한 판정의 "호스트" — 테스트는 가짜 `dagster` 배포판(dist-info)을 PYTHONPATH에 올린다. probe는
#: `importlib.metadata`로 설치 버전을 읽을 뿐 dagster를 import하지 않으므로 이 대역이 실제 판정과 같은 것을 잰다.
_HOST_DAGSTER = "1.13.24"


def _entry(
    name: str, typename: str | None, versions: list[dict[str, str]] | None | str = "default"
) -> dict[str, Any]:
    if typename is None:
        return {"name": name, "locationOrLoadError": None}
    body: dict[str, Any] = {"__typename": typename}
    if typename == "RepositoryLocation":
        body["dagsterLibraryVersions"] = (
            [{"name": "dagster", "version": _HOST_DAGSTER}] if versions == "default" else versions
        )
    return {"name": name, "locationOrLoadError": body}


def _workspace_text(names: list[str]) -> str:
    return yaml.safe_dump(
        {
            "load_from": [
                {"grpc_server": {"host": "127.0.0.1", "port": 4000 + i, "location_name": name}}
                for i, name in enumerate(names)
            ]
        }
    )


def _run_probe(
    tmp_path: Path, port: int, workspace_text: str, *, stale: bool = False, host_dagster: bool = True
) -> subprocess.CompletedProcess[str]:
    """컨테이너와 같은 모양으로 probe를 돌린다: `$DAGSTER_HOME`의 두 파일과 그 digest env, 호스트의 배포판."""

    home = tmp_path / "home"
    home.mkdir()
    (home / "workspace.yaml").write_text(workspace_text, encoding="utf-8")
    (home / "dagster.yaml").write_bytes(_INSTANCE_CONFIG.read_bytes())
    site = tmp_path / "site"
    if host_dagster:
        dist = site / f"dagster-{_HOST_DAGSTER}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: dagster\nVersion: {_HOST_DAGSTER}\n", encoding="utf-8"
        )
    else:
        site.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(site),
        "DAGSTER_HOME": str(home),
        _DIGEST_ENV[_WORKSPACE_SOURCE]: _digest((home / "workspace.yaml").read_bytes()),
        _DIGEST_ENV[_INSTANCE_SOURCE]: "0" * 16 if stale else _digest((home / "dagster.yaml").read_bytes()),
    }
    return subprocess.run(  # noqa: S603 - 고정 인터프리터, compose에서 꺼낸 원문
        # `-I`는 PYTHONPATH를 지운다 — 가짜 호스트 배포판을 보이려고 여기서만 뺀다(probe 원문은 그대로).
        [sys.executable, "-c", _probe_argv()[4], str(port), str(home / "workspace.yaml")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


_OK = [{"name": "dagster", "version": _HOST_DAGSTER}]


@pytest.mark.parametrize(
    ("expected", "reply", "healthy", "said"),
    [
        # 빈 plane(모든 target이 own) — 기대 location이 0개라 healthy다.
        ([], {"__typename": "Workspace", "locationEntries": []}, True, ""),
        (["a"], {"__typename": "Workspace", "locationEntries": [_entry("a", "RepositoryLocation")]}, True, ""),
        # 한 location이 실패하면 다른 location이 떠 있어도 빨갛다(repositoriesOrError는 초록이었다).
        (
            ["a", "b"],
            {
                "__typename": "Workspace",
                "locationEntries": [_entry("a", "RepositoryLocation"), _entry("b", "PythonError")],
            },
            False,
            "['b']",
        ),
        (["a"], {"__typename": "Workspace", "locationEntries": [_entry("a", None)]}, False, "['a']"),
        (["a"], {"__typename": "Workspace", "locationEntries": []}, False, "['a']"),
        ([], {"__typename": "PythonError"}, False, "workspace not loaded"),
        # 버전 상한(plan §2.1): code-server의 dagster가 호스트보다 높으면 빨갛다.
        (
            ["a"],
            {"__typename": "Workspace", "locationEntries": [
                _entry("a", "RepositoryLocation", [{"name": "dagster", "version": "1.13.25"}])]},
            False,
            "above the host version ceiling",
        ),
        # 호스트에 없는 라이브러리는 비교하지 않는다 — dagster 자신이 비교됐으면 초록.
        (
            ["a"],
            {"__typename": "Workspace", "locationEntries": [
                _entry("a", "RepositoryLocation", [*_OK, {"name": "not-on-host", "version": "999.0.0"}])]},
            True,
            "",
        ),
        # 버전을 모르거나(null) dagster 자신을 보고하지 않으면 통과시키지 않는다(재리뷰 LOW-3).
        (
            ["a"],
            {"__typename": "Workspace", "locationEntries": [_entry("a", "RepositoryLocation", None)]},
            False,
            "did not report its dagster version: ['a']",
        ),
        (
            ["a"],
            {"__typename": "Workspace", "locationEntries": [
                _entry("a", "RepositoryLocation", [{"name": "dagster-postgres", "version": "0.29.24"}])]},
            False,
            "did not report its dagster version: ['a']",
        ),
    ],
)
def test_the_shared_probe_is_green_only_when_every_workspace_location_loaded(
    tmp_path: Path,
    graphql_server: tuple[int, type[_GraphQL]],
    expected: list[str],
    reply: dict[str, Any],
    healthy: bool,
    said: str,
) -> None:
    """compose의 probe 원문을 그대로 실행한다 — 문자열이 아니라 판정을 센다."""

    port, handler = graphql_server
    handler.reply = {"data": {"workspaceOrError": reply}}
    completed = _run_probe(tmp_path, port, _workspace_text(expected))
    assert (completed.returncode == 0) is healthy, completed.stderr
    assert said in completed.stderr


def test_the_shared_probe_is_red_when_the_mounted_files_drift_from_their_digests(
    tmp_path: Path, graphql_server: tuple[int, type[_GraphQL]]
) -> None:
    """호스트에서 붙인 파일을 고치면 컨테이너는 옛 내용을 들고 있다 — probe가 빨개져 재생성을 요구한다(LOW-1)."""

    port, handler = graphql_server
    handler.reply = {"data": {"workspaceOrError": {"__typename": "Workspace", "locationEntries": []}}}
    completed = _run_probe(tmp_path, port, _workspace_text([]), stale=True)
    assert completed.returncode != 0
    assert "differ from the digests" in completed.stderr and "dagster.yaml" in completed.stderr


def test_the_shared_probe_needs_the_host_dagster_version(
    tmp_path: Path, graphql_server: tuple[int, type[_GraphQL]]
) -> None:
    port, handler = graphql_server
    handler.reply = {"data": {"workspaceOrError": {
        "__typename": "Workspace", "locationEntries": [_entry("a", "RepositoryLocation")]}}}
    completed = _run_probe(tmp_path, port, _workspace_text(["a"]), host_dagster=False)
    assert completed.returncode != 0 and "host dagster version is unknown" in completed.stderr


def test_the_committed_workspace_is_loadable_by_the_probe(
    tmp_path: Path, graphql_server: tuple[int, type[_GraphQL]]
) -> None:
    """실제 workspace 파일(지금은 빈 plane)이 probe의 입력으로 읽힌다 — 빈 plane은 healthy다."""

    port, handler = graphql_server
    handler.reply = {"data": {"workspaceOrError": {"__typename": "Workspace", "locationEntries": []}}}
    completed = _run_probe(tmp_path, port, _WORKSPACE.read_text(encoding="utf-8"))
    workspace = load_yaml_rejecting_duplicate_keys(_WORKSPACE.read_text(encoding="utf-8"))
    assert (completed.returncode == 0) is (not workspace["load_from"]), completed.stderr


def test_flip_helper_does_not_mutate_the_source_documents() -> None:
    compose, targets = _documents()
    before = copy.deepcopy((compose, targets))
    _flip(copy.deepcopy(compose), copy.deepcopy(targets), "map")
    assert (compose, targets) == before


def test_the_map_api_calls_the_loopback_and_only_reports_the_public_graphql() -> None:
    """Map 전환 준비: Map API가 **부르는** GraphQL은 공용 webserver의 loopback, **보고하는** 것은 공개 gateway다.

    공개 gateway는 Basic Auth와 브라우저 Origin 검사를 하므로 서버 쪽 호출이 그리로 가면 전환 뒤 401·403이다. 전환
    PR은 두 env를 이 종류대로 싣는다 — `_flip`이 그렇게 쓰고 계약이 값을 대조한다.
    """

    _, targets = _documents()
    consumers = targets["targets"]["map"]["dagster"]["consumers"]["kor-travel-map-api"]
    kinds = {str(kind) for kind in consumers.values()}
    assert "internal/graphql" in kinds and "public/graphql" in kinds, consumers
    compose, targets = _documents()
    files = _flip(compose, targets, "map")
    rest = [v for v in _pinned(_contract_violations(compose, targets, files))[1] if "kor-travel-map-api" in v]
    assert rest == []
    environment = _environment(compose["services"]["kor-travel-map-api"])
    internal = [name for name, kind in consumers.items() if kind == "internal/graphql"]
    assert [_resolve(str(environment[name]), {}) for name in internal] == [
        f"{_plane(compose, targets)['internal']}/graphql"
    ]


def test_unflip_then_flip_is_the_committed_map_rendering() -> None:
    """대조군의 출발점이 참이다: `_unflip` 뒤 `_flip`이 되돌린 모양을 계약 위반 없이 다시 만든다."""

    compose, targets = _documents()
    _unflip(compose, targets, "map")
    assert targets["targets"]["map"]["dagster"]["control_plane"] == "own"
    files = _flip(compose, targets, "map")
    _, rest = _pinned(_contract_violations(compose, targets, files))
    assert rest == []


def test_the_shared_map_api_allows_only_the_loopback_dagster_hosts() -> None:
    """Map 전환: Map API가 **부르는** Dagster는 공용 webserver의 loopback뿐이다 — host allowlist도 loopback만.

    옛 allowlist는 Map 전용 webserver의 서비스 이름과 공개 host(`KTDM_PROD_MAP_DAGSTER_HOST`)를 실었다. 보고용 공개 URL은
    Map #1290부터 allowlist를 지나지 않는다(모양만 본다). URL이 아니라 host 목록이라 `consumers` 계약 밖이어서 따로 본다.
    """

    compose, targets = _documents()
    assert targets["targets"]["map"]["dagster"]["control_plane"] == "shared"
    raw = _environment(compose["services"]["kor-travel-map-api"])["KOR_TRAVEL_MAP_API_DAGSTER_ALLOWED_HOSTS"]
    assert "${" not in str(raw), raw
    assert set(json.loads(str(raw))) == {"127.0.0.1", "localhost", "::1"}
