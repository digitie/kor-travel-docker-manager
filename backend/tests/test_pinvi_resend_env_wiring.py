"""PinVi 메일(Resend) env가 **렌더된** compose의 pinvi-api에 닿는지 센다(PinVi #720).

2026-11-08 n150: `trip_invite` 메일 7통이 Resend 403으로 5회씩 실패했다. compose가 `PINVI_RESEND_API_KEY`만 넘기고
발신 주소를 넘기지 않아, PinVi는 개발용 기본값 `Pinvi <noreply@send.pinvi.local>`으로 보냈다(`.local`은 Resend가
검증할 수 없다). 같은 읽기 전용 확인에서 `PINVI_WEB_BASE_URL`도 없어 메일 링크가 PinVi 앱 기본값(localhost)을
가리켰다.

메일은 pinvi-api의 outbox worker(FastAPI lifespan)만 보낸다 — PinVi ETL `pinvi_email_outbox`는 `app.email_queue`
집계만 한다. 그래서 이 이름들은 pinvi-api에만 간다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_PINVI_API = "pinvi-api"

_API_KEY_ENV = "PINVI_RESEND_API_KEY"
_FROM_ENV = "PINVI_RESEND_FROM_EMAIL"
_WEBHOOK_ENV = "PINVI_RESEND_WEBHOOK_SECRET"
_WEB_BASE_ENV = "PINVI_WEB_BASE_URL"
_MAIL_ENV = (_API_KEY_ENV, _FROM_ENV, _WEBHOOK_ENV, _WEB_BASE_ENV)
#: PinVi `apps/api/app/core/config.py` `pinvi_web_base_url` 기본값 — 넘기기만 하고 동작은 바꾸지 않는다.
_PINVI_WEB_BASE_DEFAULT = "http://localhost:12805"

_FROM = "Pinvi <noreply@send.pinvi.example-verified.kr>"
_WEB_BASE = "https://pinvi.example-public.kr"
_WEBHOOK_SECRET = "whsec_c2VudGluZWwtd2ViaG9vay1zZWNyZXQ="
_API_KEY = "re_sentinel_resend_key_7d1c"

_REQUIRED_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")


def _source_services() -> dict[str, Any]:
    document = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    assert isinstance(services, dict)
    return services


def _render(environment_update: dict[str, str]) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose가 없어 resolved compose를 렌더할 수 없음")
    service = deepcopy(_source_services()[_PINVI_API])
    # 조각만 렌더한다 — 조각 밖 서비스를 가리키는 의존과 빌드 문맥은 이 검사와 무관하다.
    service.pop("depends_on", None)
    service.pop("build", None)
    fragment_text = yaml.safe_dump({"services": {_PINVI_API: service}}, sort_keys=False)

    environment = {
        key: value
        for key, value in os.environ.items()
        # 호스트 값이 새어 들어와 결과를 바꾸지 않게 한다.
        if key not in {*_MAIL_ENV, "KTDM_DOCKER_NETWORK_MODE"} and not key.startswith("COMPOSE_")
    }
    environment.update({name: "contract-placeholder" for name in _REQUIRED_VAR.findall(fragment_text)})
    environment["COMPOSE_PROJECT_NAME"] = "ktdm-pinvi-resend-env-contract"
    environment.update(environment_update)
    completed = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "--file", "-", "config", "--format", "json"],
        cwd=_ROOT,
        env=environment,
        input=fragment_text,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)["services"][_PINVI_API]["environment"]
    assert isinstance(rendered, dict)
    return rendered


def test_root_env_mail_values_reach_the_api() -> None:
    rendered = _render(
        {_API_KEY_ENV: _API_KEY, _FROM_ENV: _FROM, _WEBHOOK_ENV: _WEBHOOK_SECRET, _WEB_BASE_ENV: _WEB_BASE}
    )
    assert rendered[_API_KEY_ENV] == _API_KEY
    # `<`·`>`·공백이 든 주소가 보간에서 그대로 살아야 한다.
    assert rendered[_FROM_ENV] == _FROM
    assert rendered[_WEBHOOK_ENV] == _WEBHOOK_SECRET
    assert rendered[_WEB_BASE_ENV] == _WEB_BASE


def test_unset_mail_values_keep_the_pinvi_defaults() -> None:
    """미설정은 기동을 막지 않는다 — 빈 발신 주소는 PinVi가 기본값으로 읽고, web origin은 앱 기본값과 같다."""

    rendered = _render({})
    assert rendered[_API_KEY_ENV] == ""
    assert rendered[_FROM_ENV] == ""
    assert rendered[_WEBHOOK_ENV] == ""
    assert rendered[_WEB_BASE_ENV] == _PINVI_WEB_BASE_DEFAULT

    # `.env.example`을 그대로 복사한 빈 값도 같다.
    blank = _render({name: "" for name in _MAIL_ENV})
    assert blank[_FROM_ENV] == ""
    assert blank[_WEB_BASE_ENV] == _PINVI_WEB_BASE_DEFAULT


def test_mail_env_is_only_on_the_api() -> None:
    """메일을 보내는 프로세스는 pinvi-api 하나다. key·webhook secret을 다른 서비스로 넓히지 않는다."""

    services = _source_services()
    api_environment = services[_PINVI_API]["environment"]
    for name in (_API_KEY_ENV, _FROM_ENV, _WEBHOOK_ENV):
        assert api_environment[name] == f"${{{name}:-}}", name
    assert api_environment[_WEB_BASE_ENV] == f"${{{_WEB_BASE_ENV}:-{_PINVI_WEB_BASE_DEFAULT}}}"
    for service_name, service in services.items():
        if service_name == _PINVI_API:
            continue
        environment = service.get("environment") or {}
        names = set(environment) if isinstance(environment, dict) else {e.split("=", 1)[0] for e in environment}
        assert not names & set(_MAIL_ENV), service_name


def test_rendered_mail_secrets_are_scrubbed_but_addresses_stay_readable() -> None:
    from kor_travel_docker_manager.services.secret_scrub import is_sensitive_key, redact_secret_text

    assert is_sensitive_key(_API_KEY_ENV)
    assert is_sensitive_key(_WEBHOOK_ENV)
    # 발신 주소·origin은 비밀이 아니다 — 가리면 #720 같은 원인 진단이 망가진다.
    assert not is_sensitive_key(_FROM_ENV)
    assert not is_sensitive_key(_WEB_BASE_ENV)

    rendered = _render(
        {_API_KEY_ENV: _API_KEY, _FROM_ENV: _FROM, _WEBHOOK_ENV: _WEBHOOK_SECRET, _WEB_BASE_ENV: _WEB_BASE}
    )
    text = json.dumps(rendered)
    scrubbed = redact_secret_text(text, {_API_KEY_ENV: _API_KEY, _WEBHOOK_ENV: _WEBHOOK_SECRET})
    assert _API_KEY not in scrubbed
    assert _WEBHOOK_SECRET not in scrubbed
    assert _WEB_BASE in scrubbed


def test_env_example_documents_the_mail_names_as_empty_placeholders() -> None:
    lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for name in _MAIL_ENV:
        assert [line for line in lines if line.startswith(f"{name}=")] == [f"{name}="], name
