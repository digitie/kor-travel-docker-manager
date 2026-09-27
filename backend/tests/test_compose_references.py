"""C6c 보호 참조 파생 규칙(ADR-51 결정 5).

원본은 저장소의 `docker-compose.yml`이다(개발 checkout에서는 그 파일이 git이 추적하는 원본이다). 환경은
원본이 참조하는 모든 변수에 값을 채워 만든다 — 민감한 이름은 비밀 값을, DSN은 그 비밀을 담은 값을 받는다.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from kor_travel_docker_manager.services.compose_references import (
    RELEASE_COMPOSE_NAME,
    assert_protected_references_are_derived,
    assert_resolved_secret_values_stay_at_reference_sites,
    compose_references,
    reference_compose_path,
    variable_names,
)
from kor_travel_docker_manager.services.errors import ComposeCandidateContractError
from kor_travel_docker_manager.services.secret_scrub import is_sensitive_key

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_TEXT = _COMPOSE.read_text(encoding="utf-8")
_DOCUMENT: dict[str, Any] = yaml.safe_load(_TEXT)


def _environment() -> dict[str, str]:
    names = {name for site in compose_references(_DOCUMENT).values() for name in site}
    environment = {
        name: (f"secret-{name.lower()}-9f31" if is_sensitive_key(name) else f"value-{name.lower()}")
        for name in names
        if not name.startswith("env_file:")
    }
    # DSN은 이름이 민감하지 않아도 비밀을 담는다.
    environment["KOR_TRAVEL_MAP_SERVICE_PASSWORD"] = "secret-dsn-password-4411"
    environment["KOR_TRAVEL_MAP_PG_DSN"] = "postgresql+asyncpg://ktm:secret-dsn-password-4411@db/ktm"
    # 이름이 민감해도 숫자는 비밀이 아니다.
    environment["KTDM_TEST_TOKEN_TTL_SECONDS"] = "3600"
    # 원본에 기본값으로 적힌 값은 비밀로 치지 않는다(`${GRAFANA_ADMIN_PASSWORD:-admin}`).
    environment["GRAFANA_ADMIN_PASSWORD"] = "admin"
    return environment


def _check(candidate: dict[str, Any]) -> None:
    assert_protected_references_are_derived(
        candidate, compose_path=_COMPOSE, environment=_environment()
    )


def _candidate() -> dict[str, Any]:
    return copy.deepcopy(_DOCUMENT)


def test_variable_names_follow_compose_interpolation() -> None:
    text = "${A} ${B:-x} $C $$D ${E:-${F}} ${G:?need} $$ ${9X} tail$"
    assert variable_names(text) == {"A", "B", "C", "E", "F", "G"}


def test_the_repository_compose_passes_against_itself() -> None:
    _check(_candidate())


def test_a_shared_secret_copied_into_another_service_is_rejected() -> None:
    """리터럴 표가 놓친 자리다 — 공유 PostgreSQL 비밀은 어느 표에도 없었다."""

    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["KTDM_PROBE"] = (
        "${KOR_TRAVEL_SHARED_POSTGRES_PASSWORD}"
    )

    with pytest.raises(ComposeCandidateContractError) as refused:
        _check(candidate)

    assert "grafana.environment.KTDM_PROBE -> KOR_TRAVEL_SHARED_POSTGRES_PASSWORD" in str(
        refused.value
    )


def test_a_dsn_that_carries_a_secret_is_protected_by_its_value() -> None:
    candidate = _candidate()
    candidate["services"]["kor-travel-map-ui"].setdefault("environment", {})
    environment = candidate["services"]["kor-travel-map-ui"]["environment"]
    if isinstance(environment, list):
        environment.append("LEAK=${KOR_TRAVEL_MAP_PG_DSN}")
    else:
        environment["LEAK"] = "${KOR_TRAVEL_MAP_PG_DSN}"

    with pytest.raises(ComposeCandidateContractError, match="KOR_TRAVEL_MAP_PG_DSN"):
        _check(candidate)


def test_moving_an_existing_reference_to_another_key_is_rejected() -> None:
    candidate = _candidate()
    grafana = candidate["services"]["grafana"]["environment"]
    grafana["GF_SERVER_HTTP_PORT"] = "${GRAFANA_ADMIN_PASSWORD}"

    with pytest.raises(ComposeCandidateContractError, match="GF_SERVER_HTTP_PORT"):
        _check(candidate)


def test_mounting_a_protected_secret_in_another_service_is_rejected() -> None:
    candidate = _candidate()
    candidate["services"]["grafana"]["secrets"] = ["kor-travel-shared-postgres-password"]

    with pytest.raises(ComposeCandidateContractError, match="grafana.secrets"):
        _check(candidate)


def test_an_added_env_file_is_rejected() -> None:
    candidate = _candidate()
    candidate["services"]["kor-travel-map-api"]["env_file"] = [".env"]

    with pytest.raises(ComposeCandidateContractError, match="env_file:.env"):
        _check(candidate)


def test_an_escaped_dollar_is_not_a_reference() -> None:
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["GF_NOTE"] = "$${KOR_TRAVEL_SHARED_POSTGRES_PASSWORD}"

    _check(candidate)


def test_removing_a_reference_is_allowed() -> None:
    candidate = _candidate()
    del candidate["services"]["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"]

    _check(candidate)


def test_a_non_secret_value_moves_freely() -> None:
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["GF_EXTRA"] = "${GRAFANA_PORT:-12104}"

    _check(candidate)


def test_a_secret_written_literally_is_rejected() -> None:
    environment = _environment()
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"] = environment[
        "KOR_TRAVEL_SHARED_POSTGRES_PASSWORD"
    ]

    with pytest.raises(ComposeCandidateContractError, match="literally"):
        _check(candidate)


def test_a_default_written_in_the_reference_is_not_a_secret() -> None:
    """`${GRAFANA_ADMIN_PASSWORD:-admin}`의 `admin`이 비밀이면 설치된 compose 자체가 거부된다."""

    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["GF_SECURITY_ADMIN_USER"] = "admin"

    _check(candidate)


def test_an_installed_release_uses_the_reference_copy_not_the_edited_file(tmp_path: Path) -> None:
    """UI는 docker-compose.yml을 제자리에서 고친다 — 원본은 설치기가 남긴 사본이다."""

    (tmp_path / ".ktdm-source-revision").write_text("a" * 40 + "\n", encoding="utf-8")
    edited = _candidate()
    edited["services"]["grafana"]["environment"]["KTDM_PROBE"] = "${KOR_TRAVEL_SHARED_POSTGRES_PASSWORD}"
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(yaml.safe_dump(edited), encoding="utf-8")

    with pytest.raises(ComposeCandidateContractError, match="no reference compose"):
        assert_protected_references_are_derived(
            edited, compose_path=compose, environment=_environment()
        )

    (tmp_path / RELEASE_COMPOSE_NAME).write_text(_TEXT, encoding="utf-8")
    assert reference_compose_path(compose) == tmp_path / RELEASE_COMPOSE_NAME
    with pytest.raises(ComposeCandidateContractError, match="KTDM_PROBE"):
        assert_protected_references_are_derived(
            edited, compose_path=compose, environment=_environment()
        )


def test_a_development_checkout_uses_the_compose_file_itself(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_TEXT, encoding="utf-8")

    assert reference_compose_path(compose) == compose


def test_numeric_values_under_sensitive_names_are_not_secrets() -> None:
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["GF_TIMEOUT"] = "3600"

    _check(candidate)


@pytest.mark.parametrize(
    "form",
    ["mapping", "list"],
)
def test_an_env_key_without_a_value_references_its_own_name(form: str) -> None:
    """`KEY:`·목록의 `KEY`는 compose가 같은 이름을 환경에서 끌어온다 — DSN을 흘리는 길이다(적대 리뷰)."""

    candidate = _candidate()
    grafana = candidate["services"]["grafana"]
    if form == "mapping":
        grafana["environment"]["KOR_TRAVEL_MAP_PG_DSN"] = None
    else:
        grafana["environment"] = [f"{key}={value}" for key, value in grafana["environment"].items()]
        grafana["environment"].append("KOR_TRAVEL_MAP_PG_DSN")

    with pytest.raises(
        ComposeCandidateContractError,
        match="grafana.environment.KOR_TRAVEL_MAP_PG_DSN -> KOR_TRAVEL_MAP_PG_DSN",
    ):
        _check(candidate)


def test_a_config_mount_counts_like_a_secret_mount() -> None:
    candidate = _candidate()
    candidate.setdefault("configs", {})["leak"] = {"environment": "KOR_TRAVEL_SHARED_POSTGRES_PASSWORD"}
    candidate["services"]["grafana"]["configs"] = ["leak"]

    with pytest.raises(ComposeCandidateContractError, match="KOR_TRAVEL_SHARED_POSTGRES_PASSWORD"):
        _check(candidate)


def test_the_literal_value_refusal_names_its_site() -> None:
    environment = _environment()
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["GF_EXTRA"] = environment[
        "KOR_TRAVEL_SHARED_POSTGRES_PASSWORD"
    ]

    with pytest.raises(ComposeCandidateContractError, match="literally at grafana.environment.GF_EXTRA"):
        _check(candidate)


def test_a_name_stops_at_the_first_non_ascii_character() -> None:
    """compose는 `$DSN\u00e9`에서 `DSN`을 치환하고 글자를 남긴다 — 규칙도 같게 읽어야 한다(적대 리뷰 H-1)."""

    assert variable_names("$KOR_TRAVEL_MAP_PG_DSN\u00e9") == {"KOR_TRAVEL_MAP_PG_DSN"}
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["X"] = "$KOR_TRAVEL_MAP_PG_DSN\u00e9"

    with pytest.raises(ComposeCandidateContractError, match="grafana.environment.X -> KOR_TRAVEL_MAP_PG_DSN"):
        _check(candidate)


def test_a_percent_encoded_dsn_is_still_protected() -> None:
    environment = _environment()
    environment["KOR_TRAVEL_MAP_PG_DSN"] = "postgresql://ktm:secret%2Ddsn%2Dpassword%2D4411@db/ktm"
    candidate = _candidate()
    candidate["services"]["grafana"]["environment"]["LEAK"] = "${KOR_TRAVEL_MAP_PG_DSN}"

    with pytest.raises(ComposeCandidateContractError, match="KOR_TRAVEL_MAP_PG_DSN"):
        assert_protected_references_are_derived(
            candidate, compose_path=_COMPOSE, environment=environment
        )


def _resolved_with(site_update: dict[str, Any]) -> dict[str, Any]:
    resolved = _candidate()
    for service, fields in site_update.items():
        resolved["services"][service].update(fields)
    return resolved


def test_the_resolved_backstop_accepts_secrets_at_their_reference_sites() -> None:
    environment = _environment()
    resolved = _candidate()
    resolved["services"]["kor-travel-map-api"]["environment"] = {
        "KOR_TRAVEL_MAP_PG_DSN": environment["KOR_TRAVEL_MAP_PG_DSN"]
    }

    assert_resolved_secret_values_stay_at_reference_sites(
        resolved, compose_path=_COMPOSE, environment=environment
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"labels": {"leak": "{secret}"}},
        {"environment": {"GF_EXTRA": "{secret}"}},
    ],
    ids=["label_file", "parser-disagreement"],
)
def test_the_resolved_backstop_rejects_a_secret_value_elsewhere(fields: dict[str, Any]) -> None:
    """`label_file`이나 파서 불일치로 들어온 값은 raw 규칙이 못 본다 — resolved 문서에서 잡는다."""

    environment = _environment()
    secret = environment["KOR_TRAVEL_SHARED_POSTGRES_PASSWORD"]
    rendered = {
        field: {key: value.format(secret=secret) for key, value in block.items()}
        for field, block in fields.items()
    }
    resolved = _resolved_with({"grafana": rendered})

    with pytest.raises(ComposeCandidateContractError, match="carries a protected C6c value at grafana"):
        assert_resolved_secret_values_stay_at_reference_sites(
            resolved, compose_path=_COMPOSE, environment=environment
        )


def test_the_resolved_backstop_sees_compose_dollar_escaping() -> None:
    environment = _environment()
    environment["KOR_TRAVEL_SHARED_POSTGRES_PASSWORD"] = "secret-with-$-dollar-7731"
    resolved = _resolved_with({"grafana": {"labels": {"leak": "secret-with-$$-dollar-7731"}}})

    with pytest.raises(ComposeCandidateContractError, match="grafana.labels"):
        assert_resolved_secret_values_stay_at_reference_sites(
            resolved, compose_path=_COMPOSE, environment=environment
        )
