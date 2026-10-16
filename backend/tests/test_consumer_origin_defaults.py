"""소비자 origin 기본값(ADR-58 R2, `docs/bindings.md` B-6) — 렌더된 compose를 **스캔해** registry 기대값과 대조한다.

T-MGR-01c(2026-10-16): 형제 → 소비자 포트 표를 테스트에서 지웠다. 기대 origin은 registry의 `consumer_origin`(R1 렌더 —
소비자 서비스가 도는 호스트에서 본 값: 실행 target, 없으면 Dagster family target, 그 밖은 unresolved로 보고)이고, 스캔
대상 포트도 registry가 명시한 것(`consumer_origin`·`retired_consumer_ports`)만이다(`services/consumer_origins.py`). root
`.env` 재정의 거부는 `ktdctl targets validate`가 한다.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import consumer_origins, registry
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_TARGETS = _ROOT / "config" / "docker-targets.yml"

#: 하한 — 스캔이 적어도 이것은 봐야 한다(추출이 낡아 아무것도 못 보면 아래 값 검사가 항진이 된다).
_KNOWN_CONSUMERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("kor-travel-map-api", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster-code-server", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster-daemon", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL"),
        ("kor-travel-map-dagster-code-server", "KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_BASE_URL"),
        ("pinvi-dagster-code-server", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL"),
        ("pinvi-api", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL"),
        ("pinvi-api", "PINVI_KOR_TRAVEL_GEO_BASE_URL"),
    }
)


def _documents() -> tuple[dict[str, Any], dict[str, Any]]:
    compose = load_yaml_rejecting_duplicate_keys(_COMPOSE.read_text(encoding="utf-8"))
    targets = load_yaml_rejecting_duplicate_keys(_TARGETS.read_text(encoding="utf-8"))
    return compose, targets


def _siblings(targets: dict[str, Any]) -> list[str]:
    return sorted(name for name, spec in targets["targets"].items() if spec.get("consumer_origin"))


#: 파라미터는 registry에서 — 형제를 더하거나 빼도 테스트를 고치지 않는다.
_SIBLINGS = _siblings(load_yaml_rejecting_duplicate_keys(_TARGETS.read_text(encoding="utf-8")))


def test_the_scan_sees_every_known_consumer() -> None:
    compose, targets = _documents()
    found = set(consumer_origins.scan(compose, targets))
    assert _KNOWN_CONSUMERS <= found, sorted(_KNOWN_CONSUMERS - found)


def test_every_consumer_default_is_the_registry_origin() -> None:
    compose, targets = _documents()
    assert consumer_origins.default_violations(compose, targets) == []


def test_scan_ports_are_exactly_what_the_registry_declares() -> None:
    # 포트 표는 테스트에 없다: 형제의 `consumer_origin` 포트와 `retired_consumer_ports`만 스캔 대상이다.
    _compose, targets = _documents()
    expected: dict[int, str] = {}
    for sibling in _siblings(targets):
        spec = targets["targets"][sibling]
        for port in [int(spec["consumer_origin"].rsplit(":", 1)[1]), *(spec.get("retired_consumer_ports") or [])]:
            expected[port] = sibling
    assert consumer_origins.sibling_ports(targets) == expected
    assert any(targets["targets"][s].get("retired_consumer_ports") for s in _SIBLINGS)


def test_unrelated_sibling_ports_are_not_consumer_origins() -> None:
    """리뷰 1번: 형제의 다른 포트(S3·UI·Dagster)는 소비자 origin이 아니다 — 잡으면 무관한 env가 형제 소비자로 분류돼
    validate·설치기 flip이 막힌다. 형제 컨테이너 connection의 `{host}` URL 포트 중 소비자 포트가 아닌 것 전부로 확인한다."""

    compose, targets = _documents()
    # 명시 포트는 registry 선언에서 직접 센다 — 스캐너의 `sibling_ports`로 세면 스캐너가 넓어질 때 이 검사도 함께 넓어진다.
    declared = {
        int(port)
        for sibling in _siblings(targets)
        for port in [targets["targets"][sibling]["consumer_origin"].rsplit(":", 1)[1],
                     *(targets["targets"][sibling].get("retired_consumer_ports") or [])]
    }
    other_ports = sorted({
        int(port)
        for spec in targets["containers"].values()
        for port in re.findall(r"https?://\{host\}:(\d+)", str(spec.get("connection") or ""))
    } - declared)
    assert other_ports  # 무언가를 넣어 본다
    consumer = next(iter(sorted(_KNOWN_CONSUMERS)))[0]
    environment = compose["services"][consumer].setdefault("environment", {})
    for port in other_ports:
        environment[f"UNRELATED_{port}"] = f"${{UNRELATED_{port}:-http://127.0.0.1:{port}}}"
    found = consumer_origins.scan(compose, targets)
    assert [key for (_service, key) in found if key.startswith("UNRELATED_")] == []
    assert consumer_origins.default_violations(compose, targets) == []


@pytest.mark.parametrize("sibling", _SIBLINGS)
def test_moving_the_registry_port_and_retiring_the_old_one_names_every_consumer(sibling: str) -> None:
    """빨간 대조군 — 플립 PR이 하듯 포트를 바꾸고 옛 포트를 `retired_consumer_ports`에 두면(compose 기본값은 그대로)
    그 형제의 알려진 소비자 **전부**가 이름으로 빨갛다(포트의 정본이 registry다)."""

    compose, targets = _documents()
    known = {consumer for consumer, env in consumer_origins.scan(compose, targets).items()
             if env.sibling == sibling and consumer in _KNOWN_CONSUMERS}
    assert known
    spec = targets["targets"][sibling]
    old_port = int(spec["consumer_origin"].rsplit(":", 1)[1])
    spec["consumer_origin"] = spec["consumer_origin"].rsplit(":", 1)[0] + ":1"
    spec["retired_consumer_ports"] = [*(spec.get("retired_consumer_ports") or []), old_port]
    violations = consumer_origins.default_violations(compose, targets)
    assert {(n, k) for n, k in known if not any(v.startswith(f"`{n}`.{k}:") for v in violations)} == set()


@pytest.mark.parametrize("sibling", _SIBLINGS)
def test_moving_the_port_without_retiring_the_old_one_breaks_the_floor(sibling: str) -> None:
    """옛 포트를 퇴역 목록에 두지 않으면 스캔이 그 소비자를 놓친다 — 그것을 잡는 것은 하한(`_KNOWN_CONSUMERS`)이다."""

    compose, targets = _documents()
    spec = targets["targets"][sibling]
    spec["consumer_origin"] = spec["consumer_origin"].rsplit(":", 1)[0] + ":1"
    spec.pop("retired_consumer_ports", None)
    lost = {c for c in _KNOWN_CONSUMERS if c not in consumer_origins.scan(compose, targets)}
    assert lost


@pytest.mark.parametrize("sibling", _SIBLINGS)
def test_moving_a_target_back_to_the_default_host_is_named(sibling: str) -> None:
    """빨간 대조군 — registry에서 `host:`를 지우면(default로 되돌리면) 기대 origin이 loopback이 되어 그 소비자가 빨갛다."""

    compose, targets = _documents()
    targets["targets"][sibling]["host"] = registry.default_host(targets)
    assert registry.consumer_origin(sibling, registry.default_host(targets), targets).startswith("http://127.0.0.1:")
    named = [v for v in consumer_origins.default_violations(compose, targets) if f"`{sibling}`" in v]
    assert named


def test_the_consumer_host_is_the_host_that_runs_the_consumer() -> None:
    """N-host: 소비자(map)를 형제(geo)와 같은 호스트로 옮기면 기대 origin이 loopback이 된다 — 옛 profile 서비스(map
    Dagster family의 webserver·daemon)도 family target의 호스트를 따르므로 함께 빨갛다(default로 조용히 풀리지 않는다)."""

    compose, targets = _documents()
    geo_host = targets["targets"]["geo"]["host"]
    targets["targets"]["map"]["host"] = geo_host
    violations = consumer_origins.default_violations(compose, targets)
    for service in ("kor-travel-map-api", "kor-travel-map-dagster-code-server", "kor-travel-map-dagster",
                    "kor-travel-map-dagster-daemon"):
        env = consumer_origins.scan(compose, targets)[(service, "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL")]
        assert env.expected == targets["targets"]["geo"]["consumer_origin"].replace("{host}", "127.0.0.1"), service
        assert any(v.startswith(f"`{service}`.KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL:") for v in violations), service


def test_a_consumer_no_target_runs_is_reported_unresolved() -> None:
    """리뷰 2번: 실행 target도 Dagster family도 없는 서비스는 default 호스트로 조용히 풀지 않는다."""

    compose, targets = _documents()
    geo = targets["targets"]["geo"]
    compose["services"]["orphan-consumer"] = {
        "environment": {"GEO": "${ORPHAN_GEO:-" + geo["consumer_origin"].replace("{host}", "127.0.0.1") + "}"}
    }
    env = consumer_origins.scan(compose, targets)[("orphan-consumer", "GEO")]
    assert env.expected is None and "unresolved" in (env.problem or "")
    assert any(v.startswith("`orphan-consumer`.GEO:") and "unresolved" in v
               for v in consumer_origins.default_violations(compose, targets))


def test_a_sibling_on_several_hosts_is_a_clear_error() -> None:
    """형제가 소비자 밖의 여러 호스트에 있으면 주소가 하나가 아니다 — `expected None`이 아니라 그 사실을 말한다.

    `host:` 목록(T-MGR-NH NH-1)으로 실제 registry에서 만든다: geo를 소비자(n150) 밖의 두 호스트 `[odroid, mars]`에 두면
    소비자 origin이 하나로 풀리지 않는다. 목록 자체는 허용된다 — 소비자 호스트가 목록에 들면 loopback으로 풀린다."""
    compose, targets = _documents()
    targets["hosts"]["mars"] = {"addr": "192.0.2.7"}
    targets["targets"]["geo"]["host"] = ["odroid", "mars"]
    registry._validate_targets_config(targets, label="test")
    violations = [v for v in consumer_origins.default_violations(compose, targets) if "GEO" in v]
    assert violations and all("several hosts" in v and "no single address" in v for v in violations)


def test_a_trailing_slash_is_not_a_difference() -> None:
    compose, targets = _documents()
    name, key = ("kor-travel-map-api", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL")
    env = consumer_origins.scan(compose, targets)[(name, key)]
    compose["services"][name]["environment"][key] = f"${{{env.source_variable}:-{env.expected}/}}"
    assert consumer_origins.default_violations(compose, targets) == []
    assert consumer_origins.env_override_violations(
        compose, targets, lambda variable: env.expected + "/" if variable == env.source_variable else ""
    ) == []


@pytest.mark.parametrize("consumer", sorted(_KNOWN_CONSUMERS))
def test_reverting_any_one_consumer_to_loopback_is_named(consumer: tuple[str, str]) -> None:
    """빨간 대조군 — 한 곳만 loopback으로 되돌려도 그 곳이 이름으로 빨갛다."""

    compose, targets = _documents()
    name, key = consumer
    env = consumer_origins.scan(compose, targets)[consumer]
    port = env.expected.rsplit(":", 1)[1]
    compose["services"][name]["environment"][key] = f"${{{key}:-http://127.0.0.1:{port}}}"
    violations = consumer_origins.default_violations(compose, targets)
    assert [v for v in violations if v.startswith(f"`{name}`.{key}:")], violations


def test_a_literal_consumer_url_is_named() -> None:
    """`.env`로 재정의할 수 없는 리터럴(옛 PinVi ETL `http://127.0.0.1:14101`의 모양)은 빨갛다."""

    compose, targets = _documents()
    expected = consumer_origins.scan(compose, targets)[("pinvi-dagster-code-server", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL")].expected
    compose["services"]["pinvi-dagster-code-server"]["environment"]["PINVI_KOR_TRAVEL_WEATHER_BASE_URL"] = expected
    assert any("꼴이어야 한다" in v for v in consumer_origins.default_violations(compose, targets))


def test_env_example_does_not_shadow_a_consumer_default() -> None:
    """`.env.example`이 소비자 origin의 원천 변수에 값을 주면 그것을 베낀 root `.env`가 compose 기본값을 조용히 가린다."""

    compose, targets = _documents()
    sources = {env.source_variable for env in consumer_origins.scan(compose, targets).values()} - {None}
    assert sources, "스캔이 원천 변수를 하나도 못 찾았다 — 이 검사가 공허하다"
    lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assigned = sorted(
        line for line in lines if not line.lstrip().startswith("#") and line.partition("=")[0].strip() in sources
    )
    assert assigned == [], assigned


@pytest.mark.parametrize("value", ["http://{host}:12501/", "http://192.168.1.13:12501", "{host}:12501", 12501])
def test_registry_rejects_a_malformed_consumer_origin(value: Any) -> None:
    _compose, targets = _documents()
    targets["targets"]["geo"]["consumer_origin"] = value
    with pytest.raises(registry.TargetsConfigError, match="consumer_origin"):
        registry._validate_targets_config(targets, label="test")


# --- `targets validate`의 root `.env` 재정의 거부 ------------------------------------------------------------


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """실제 registry·compose를 가진 개발 checkout 모양의 project root(비운영 — 주소 검사 없음)."""

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "docker-targets.yml").write_bytes(_TARGETS.read_bytes())
    (tmp_path / "docker-compose.yml").write_bytes(_COMPOSE.read_bytes())
    # compose가 마운트하는 공용 Dagster 설정도 둔다 — validate가 workspace 파일 digest를 함께 본다(T-MGR-02).
    import shutil

    shutil.copytree(_ROOT / "config" / "dagster-shared", tmp_path / "config" / "dagster-shared")
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    for name in (registry.TARGETS_FILE_ENV, registry.HOST_ENV, "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
                 "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE"):
        monkeypatch.delenv(name, raising=False)
    registry.load_targets_config.cache_clear()
    yield tmp_path
    registry.load_targets_config.cache_clear()


def _validate(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    from kor_travel_docker_manager.cli import main

    try:
        code = main(["targets", "validate", "--json"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _expected(variable: str) -> str:
    compose, targets = _documents()
    return next(env.expected for env in consumer_origins.scan(compose, targets).values() if env.source_variable == variable)


def test_validate_rejects_a_root_env_override_that_differs(project: Path, capsys) -> None:
    variable = "KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL"
    (project / ".env").write_text(f"OTHER=x\n{variable}=http://127.0.0.1:12501\n", encoding="utf-8")
    code, out, err = _validate(capsys)
    assert code != 0 and out == ""
    assert variable in err and _expected(variable) in err
    assert "http://127.0.0.1:12501" not in err  # 재정의된 값 자체는 싣지 않는다


@pytest.mark.parametrize("line", ["", "KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL=\n", "KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL={expected}\n"])
def test_validate_accepts_unset_empty_or_equal_overrides(project: Path, capsys, line: str) -> None:
    (project / ".env").write_text(line.format(expected=_expected("KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL")), encoding="utf-8")
    code, out, err = _validate(capsys)
    assert code == 0, err
    assert json.loads(out)["status"] == "ok"


def test_validate_names_every_overridden_consumer_variable(project: Path, capsys) -> None:
    compose, targets = _documents()
    variables = sorted({env.source_variable for env in consumer_origins.scan(compose, targets).values()} - {None})
    (project / ".env").write_text("".join(f"{v}=http://10.9.9.9:1\n" for v in variables), encoding="utf-8")
    code, _out, err = _validate(capsys)
    assert code != 0
    assert [v for v in variables if v not in err] == []


def test_override_check_is_quiet_without_a_compose_file(project: Path, capsys) -> None:
    (project / "docker-compose.yml").unlink()
    (project / ".env").write_text("KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL=http://10.9.9.9:1\n", encoding="utf-8")
    code, _out, err = _validate(capsys)
    assert code == 0, err


def test_a_third_host_consumer_sees_the_sibling_address() -> None:
    """세 번째 호스트(mars)로 소비자(pinvi)를 옮기면 기대 origin은 형제 호스트의 addr 그대로 — loopback이 아니다."""

    compose, targets = _documents()
    targets = copy.deepcopy(targets)
    targets["hosts"]["mars"] = {"addr": "192.0.2.7"}
    targets["targets"]["pinvi"]["host"] = "mars"
    env = consumer_origins.scan(compose, targets)[("pinvi-api", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL")]
    weather = targets["targets"]["weather"]
    assert env.expected == weather["consumer_origin"].replace("{host}", targets["hosts"][weather["host"]]["addr"])


@pytest.mark.parametrize("payload", [b"services: [unclosed\n", b"\xff\xfe not utf-8\n"])
def test_an_unreadable_compose_is_one_line_not_a_traceback(project: Path, capsys, payload: bytes) -> None:
    (project / "docker-compose.yml").write_bytes(payload)
    code, out, err = _validate(capsys)
    assert code == 1 and out == ""
    assert err.startswith("consumer origin check:") and "Traceback" not in err
