"""PinVi Google OAuth env가 **렌더된** compose의 pinvi-api에 닿는지 센다(2026-11-27).

PinVi 자체 compose(`infra/docker-compose.app.yml`, PinVi #467)는 `PINVI_GOOGLE_OAUTH_CLIENT_ID`·
`PINVI_GOOGLE_OAUTH_CLIENT_SECRET`·`PINVI_OAUTH_CALLBACK_BASE_URL`을 넘겼다. 배포가 이 Manager compose로 옮겨 오면서
세 이름이 빠져, root `.env`에 값이 있어도 n150 `pinvi-api` env에는 없었다. PinVi는 client id·secret이 비면
`/auth/oauth/providers`에서 google `enabled: false`를 주고 web 로그인 화면이 Google 버튼을 숨긴다 — 회원가입·로그인이
조용히 사라졌다.

OAuth start·callback·모바일 start는 모두 pinvi-api 라우터다(web은 API를 직접 부르고 ETL은 OAuth를 쓰지 않는다). 그래서
이 이름들은 pinvi-api에만 간다.
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

_CLIENT_ID_ENV = "PINVI_GOOGLE_OAUTH_CLIENT_ID"
_CLIENT_SECRET_ENV = "PINVI_GOOGLE_OAUTH_CLIENT_SECRET"
_CALLBACK_ENV = "PINVI_OAUTH_CALLBACK_BASE_URL"
_OAUTH_ENV = (_CLIENT_ID_ENV, _CLIENT_SECRET_ENV, _CALLBACK_ENV)
#: web 이미지에 구워지는 API origin(`NEXT_PUBLIC_PINVI_API_URL`)의 root 원천. callback은 세션 쿠키를 그 host에만 심으므로
#: 기본값을 이 값에서 유도한다.
_PUBLIC_API_ENV = "PINVI_PUBLIC_API_URL"
_PUBLIC_API_FALLBACK = "http://127.0.0.1:12801"
_PINVI_WEB = "pinvi-web"

_CLIENT_ID = "123456789012-sentinelclientid.apps.googleusercontent.com"
# 실제 Google secret 접두사(`GOCSPX-`)를 쓰지 않는다 — secret scanner 오탐 방지.
_CLIENT_SECRET = "sentinel-google-client-secret-not-real"
_CALLBACK = "https://pinvi-api.example-public.kr"
_PUBLIC_API = "https://pinvi-api.example-derived.kr"

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
        if key not in {*_OAUTH_ENV, _PUBLIC_API_ENV, "KTDM_DOCKER_NETWORK_MODE"} and not key.startswith("COMPOSE_")
    }
    environment.update({name: "contract-placeholder" for name in _REQUIRED_VAR.findall(fragment_text)})
    environment["COMPOSE_PROJECT_NAME"] = "ktdm-pinvi-google-oauth-env-contract"
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


def test_root_env_google_oauth_values_reach_the_api() -> None:
    rendered = _render(
        {_CLIENT_ID_ENV: _CLIENT_ID, _CLIENT_SECRET_ENV: _CLIENT_SECRET, _CALLBACK_ENV: _CALLBACK}
    )
    assert rendered[_CLIENT_ID_ENV] == _CLIENT_ID
    assert rendered[_CLIENT_SECRET_ENV] == _CLIENT_SECRET
    assert rendered[_CALLBACK_ENV] == _CALLBACK


def test_unset_google_oauth_values_keep_the_provider_off() -> None:
    """미설정은 기동을 막지 않는다 — 빈 client id·secret은 PinVi가 provider 꺼짐으로 읽는다."""

    rendered = _render({})
    assert rendered[_CLIENT_ID_ENV] == ""
    assert rendered[_CLIENT_SECRET_ENV] == ""
    assert rendered[_CALLBACK_ENV] == _PUBLIC_API_FALLBACK

    # `.env.example`을 그대로 복사한 빈 값도 같다.
    blank = _render({name: "" for name in _OAUTH_ENV})
    assert blank[_CLIENT_ID_ENV] == ""
    assert blank[_CLIENT_SECRET_ENV] == ""
    assert blank[_CALLBACK_ENV] == _PUBLIC_API_FALLBACK


def test_unset_callback_follows_the_public_api_origin_the_web_calls() -> None:
    """callback 쿠키는 callback host에만 붙는다 — 비운 callback은 web이 부르는 API origin을 그대로 따라야 한다."""

    assert _render({_PUBLIC_API_ENV: _PUBLIC_API})[_CALLBACK_ENV] == _PUBLIC_API
    assert _render({_PUBLIC_API_ENV: _PUBLIC_API, _CALLBACK_ENV: ""})[_CALLBACK_ENV] == _PUBLIC_API
    # 명시 재정의는 이긴다(운영자가 같은 origin을 직접 적는 경우).
    assert _render({_PUBLIC_API_ENV: _PUBLIC_API, _CALLBACK_ENV: _CALLBACK})[_CALLBACK_ENV] == _CALLBACK

    # web의 API origin(build arg·실행 env)이 같은 원천·같은 fallback을 쓴다 — 한쪽만 바뀌면 기본값에서 둘이 갈라진다.
    web = _source_services()[_PINVI_WEB]
    expected = f"${{{_PUBLIC_API_ENV}:-{_PUBLIC_API_FALLBACK}}}"
    assert web["build"]["args"]["NEXT_PUBLIC_PINVI_API_URL"] == expected
    assert web["environment"]["NEXT_PUBLIC_PINVI_API_URL"] == expected


def test_google_oauth_env_is_only_on_the_api() -> None:
    """OAuth start·callback은 pinvi-api 하나다. client secret을 다른 서비스로 넓히지 않는다."""

    services = _source_services()
    api_environment = services[_PINVI_API]["environment"]
    for name in (_CLIENT_ID_ENV, _CLIENT_SECRET_ENV):
        assert api_environment[name] == f"${{{name}:-}}", name
    assert api_environment[_CALLBACK_ENV] == (
        f"${{{_CALLBACK_ENV}:-${{{_PUBLIC_API_ENV}:-{_PUBLIC_API_FALLBACK}}}}}"
    )
    for service_name, service in services.items():
        if service_name == _PINVI_API:
            continue
        environment = service.get("environment") or {}
        names = set(environment) if isinstance(environment, dict) else {e.split("=", 1)[0] for e in environment}
        assert not names & set(_OAUTH_ENV), service_name


def test_rendered_client_secret_is_scrubbed_but_public_values_stay_readable() -> None:
    """가림 **규칙**(`is_sensitive_key`)이 이름에 맞게 적용되는지 본다 — inspect·실패 출력 경로 자체는 기존 시험이 본다."""

    from kor_travel_docker_manager.services.secret_scrub import is_sensitive_key, redact_secret_text

    assert is_sensitive_key(_CLIENT_SECRET_ENV)
    # client id는 authorize URL에 실려 브라우저로 가는 공개 식별자이고, callback base는 공개 origin이다. 가리면 "어느
    # OAuth client·어느 redirect_uri가 실렸나"라는 이번 같은 원인 진단이 망가진다.
    assert not is_sensitive_key(_CLIENT_ID_ENV)
    assert not is_sensitive_key(_CALLBACK_ENV)

    rendered = _render(
        {_CLIENT_ID_ENV: _CLIENT_ID, _CLIENT_SECRET_ENV: _CLIENT_SECRET, _CALLBACK_ENV: _CALLBACK}
    )
    text = json.dumps(rendered)
    scrubbed = redact_secret_text(
        text, {_CLIENT_ID_ENV: _CLIENT_ID, _CLIENT_SECRET_ENV: _CLIENT_SECRET, _CALLBACK_ENV: _CALLBACK}
    )
    assert _CLIENT_SECRET not in scrubbed
    assert _CLIENT_ID in scrubbed
    assert _CALLBACK in scrubbed


def test_env_example_documents_the_google_oauth_names_as_empty_placeholders() -> None:
    lines = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for name in _OAUTH_ENV:
        assert [line for line in lines if line.startswith(f"{name}=")] == [f"{name}="], name
