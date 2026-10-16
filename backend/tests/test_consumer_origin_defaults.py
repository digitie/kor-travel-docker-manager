"""소비자 origin 기본값(ADR-58 R2, `docs/bindings.md` B-6) — 렌더된 compose를 **스캔해** registry 기대값과 대조한다.

T-MGR-01c(2026-10-16): 형제 → 소비자 포트 표와 "소비자는 default 호스트" 가정을 테스트에서 지웠다. 기대 origin은
registry의 `consumer_origin`(R1 렌더 — 소비자 서비스를 실행하는 target의 호스트에서 본 값)이고, 스캔 대상 포트도
registry에서 읽는다(`services/consumer_origins.py`). root `.env` 재정의 거부는 `ktdctl targets validate`가 한다.
"""

from __future__ import annotations

import copy
import json
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


def test_the_scan_sees_every_known_consumer() -> None:
    compose, targets = _documents()
    found = set(consumer_origins.scan(compose, targets))
    assert _KNOWN_CONSUMERS <= found, sorted(_KNOWN_CONSUMERS - found)


def test_every_consumer_default_is_the_registry_origin() -> None:
    compose, targets = _documents()
    assert consumer_origins.default_violations(compose, targets) == []


def test_scan_ports_come_from_the_registry() -> None:
    # 포트 표는 테스트에 없다: consumer_origin의 포트와 형제 컨테이너 connection의 포트(옛 n150 포트 포함)가 스캔 대상이다.
    _compose, targets = _documents()
    ports = consumer_origins.sibling_ports(targets)
    for sibling in _siblings(targets):
        origin_port = int(targets["targets"][sibling]["consumer_origin"].rsplit(":", 1)[1])
        assert ports[origin_port] == sibling
    assert len(ports) > len(_siblings(targets))  # connection 포트까지 봤다


@pytest.mark.parametrize("sibling", ["geo", "transport", "weather"])
def test_changing_the_registry_port_is_named(sibling: str) -> None:
    """빨간 대조군 — registry의 포트를 바꾸면(compose 기본값은 그대로) 그 형제의 소비자가 빨갛다(포트의 정본이 registry다).

    빨강은 둘 중 하나로 온다: 옛 포트가 형제 컨테이너 connection에도 있으면 스캔이 그 소비자를 보고 기본값 불일치로
    이름을 대고(geo `12501`), 없으면 스캔이 그 소비자를 놓쳐 하한(`_KNOWN_CONSUMERS`)이 깨진다(transport `12301`·weather `12201`).
    """

    compose, targets = _documents()
    known = {consumer for consumer, env in consumer_origins.scan(compose, targets).items()
             if env.sibling == sibling and consumer in _KNOWN_CONSUMERS}
    assert known
    old = targets["targets"][sibling]["consumer_origin"]
    targets["targets"][sibling]["consumer_origin"] = old.rsplit(":", 1)[0] + ":1"
    named = [v for v in consumer_origins.default_violations(compose, targets) if f"`{sibling}`" in v]
    lost = known - set(consumer_origins.scan(compose, targets))
    assert named or lost


@pytest.mark.parametrize("sibling", ["geo", "transport", "weather"])
def test_moving_a_target_back_to_the_default_host_is_named(sibling: str) -> None:
    """빨간 대조군 — registry에서 `host:`를 지우면(default로 되돌리면) 기대 origin이 loopback이 되어 그 소비자가 빨갛다."""

    compose, targets = _documents()
    del targets["targets"][sibling]["host"]
    assert registry.consumer_origin(sibling, registry.default_host(targets), targets).startswith("http://127.0.0.1:")
    named = [v for v in consumer_origins.default_violations(compose, targets) if f"`{sibling}`" in v]
    assert named


def test_the_consumer_host_is_the_host_that_runs_the_consumer() -> None:
    """N-host: 소비자(map)를 형제(geo)와 같은 호스트로 옮기면 기대 origin이 loopback이 된다 — "소비자는 default 호스트"
    가정은 없다(ADR-58 잠정 시행 ⅴ)."""

    compose, targets = _documents()
    geo_host = targets["targets"]["geo"]["host"]
    targets["targets"]["map"]["host"] = geo_host
    env = consumer_origins.scan(compose, targets)[("kor-travel-map-api", "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL")]
    assert env.expected == targets["targets"]["geo"]["consumer_origin"].replace("{host}", "127.0.0.1")
    assert any(v.startswith("`kor-travel-map-api`.KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL:")
               for v in consumer_origins.default_violations(compose, targets))


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
