"""실패 출력과 조회 응답에서 비밀을 가리는 스크러버 하나(ADR-51 잃는 보장 G).

실패는 원인 원문을 싣고 다니고, 비밀은 출력 경계(CLI stderr, API 오류 본문, M05 driver)에서 한 번
가린다. 규칙은 셋이다.

1. 민감한 key 이름(아래 조각)으로 선언된 값 — 길이 4 이상. SQL 리터럴로 들어간 `'`→`''` 변형도 함께
   가린다(psql의 `LINE 1:` 에코).
2. 값 안의 URL userinfo 비밀번호(`scheme://user:password@`). DSN 이름은 위 목록에 걸리지 않는다.
3. 호출자가 넘기는 추가 값(M05가 실행마다 만드는 비밀처럼 `.env`에 없는 것).

원천은 프로세스 환경과 `.env`다. 한 key가 여러 값을 가질 수 있어 모두 가린다 — compose는 프로세스
환경을 `.env`보다 앞세우고, 재구축은 `.env`를 보간하지 않은 원문으로 읽는다. `.env`를 읽지 못하면 원문을
내지 않는다. 이 규칙에 걸리지 않는 비밀 — `.env`·환경에 없거나, 이름이 목록에 걸리지 않거나, 변형(JSON
escape, percent-encoding, base64, compose `$$`)되었거나, 잘린 조각(psql이 긴 문장을 줄여 되풀이하는
`LINE 1: ...`, tail 경계에서 잘린 여러 줄 값)이거나, 4자 미만인 값 — 은 빠져나갈 수 있다. 그것이 잃는
보장 G의 남은 모양이다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypeAlias

from dotenv import dotenv_values

REDACTED = "<redacted>"

# `API_KEY`는 `ACCESS_KEY`에 걸리지 않는다 — provider API 키가 여럿 있다
# (`KOR_TRAVEL_MAP_OPINET_API_KEY`, `KOR_TRAVEL_GEO_VWORLD_API_KEY` 등). `SERVICE_KEY`는 data.go.kr
# 계열(`*_DATA_GO_KR_SERVICE_KEY`)이다. 과다 가림은 안전한 방향이므로 의심스러우면 포함한다
# (`..._API_KEY_CACHE_TTL_S` 같은 숫자나 공개용 `NEXT_PUBLIC_*_API_KEY`도 함께 가려진다).
SENSITIVE_KEY_PARTS = (
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "TOKEN",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "API_KEY",
    "APIKEY",
    "SERVICE_KEY",
    "CREDENTIAL",
)

# key 전체를 가리는 대신 userinfo의 비밀번호 구간만 치환한다. `..._BASE_URL`처럼 비밀이 아닌 URL은
# 그대로 읽을 수 있어야 한다.
URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/?#@\s]+):(?P<password>[^@/?#\s]+)@"
)
_MINIMUM_SECRET_LENGTH = 4

#: 가릴 원천. key 하나가 여러 값을 가질 수 있어(`load_secret_environment`) 쌍의 목록도 받는다.
SecretEnvironment: TypeAlias = Mapping[str, str | None] | list[tuple[str, str | None]]


def is_sensitive_key(key: str) -> bool:
    upper_key = key.upper()
    return any(part in upper_key for part in SENSITIVE_KEY_PARTS)


def redact_value_credentials(value: str) -> str:
    """값 안의 `scheme://user:password@` 비밀번호 구간을 가린다."""

    return URL_USERINFO_RE.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:{REDACTED}@", value
    )


def redact_secret_text(
    text: str,
    environment: SecretEnvironment,
    extra_values: Iterable[str] = (),
) -> str:
    """임의 텍스트에서 비밀 값과 URL userinfo 비밀번호를 가린다(규칙은 모듈 docstring)."""

    pairs = environment.items() if isinstance(environment, Mapping) else environment
    candidates = {
        value
        for key, value in pairs
        if value and len(value) >= _MINIMUM_SECRET_LENGTH and is_sensitive_key(key)
    }
    candidates.update(
        value for value in extra_values if value and len(value) >= _MINIMUM_SECRET_LENGTH
    )
    variants = candidates | {value.replace("'", "''") for value in candidates if "'" in value}
    for secret in sorted(variants, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return redact_value_credentials(text)


def load_secret_environment(env_path: str | Path) -> list[tuple[str, str | None]]:
    """프로세스 환경과 `.env`의 key·값 쌍을 **합치지 않고** 모은다.

    합치면 한쪽 값이 사라진다. compose는 프로세스 환경을 앞세우고 스크러버가 `.env`를 앞세우면, 둘이
    다를 때 compose가 실제로 쓴 값이 가려지지 않는다. `.env`는 보간한 값(`A_PASSWORD=${B}`)과 원문을
    둘 다 본다 — 재구축은 보간하지 않은 원문을 psql·컨테이너에 넘긴다(적대 리뷰 G-2 F2·F3).

    `.env`가 없으면 가릴 것도 없다(개발 checkout). 있는데 읽지 못하면(권한, 인코딩) 예외를 그대로
    올린다 — 호출자는 원문을 내지 않는다.
    """

    return [
        *os.environ.items(),
        *dotenv_values(env_path).items(),
        *dotenv_values(env_path, interpolate=False).items(),
    ]


_WITHHELD = (
    "failure detail withheld: .env could not be read for redaction "
    "(run as a user who can read it, usually root, to see the cause)"
)


def redact_structure(
    value: object,
    environment: SecretEnvironment,
    extra_values: Iterable[str] = (),
) -> object:
    """dict·list·tuple 안의 모든 문자열 잎을 가린다. 모양(키·중첩)은 그대로 둔다."""

    extras = tuple(extra_values)
    if isinstance(value, str):
        return redact_secret_text(value, environment, extras)
    if isinstance(value, Mapping):
        return {key: redact_structure(item, environment, extras) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_structure(item, environment, extras) for item in value]
    return value


def scrub_failure_structure(
    value: object,
    env_path: str | Path,
    extra_values: Iterable[str] = (),
) -> object:
    """오류 본문·명령 결과 같은 구조 전체를 한 번 읽은 환경으로 가린다.

    `.env`를 읽지 못하면 문자열 잎마다 원문 대신 그 사실을 둔다(모양은 유지).
    """

    try:
        environment = load_secret_environment(env_path)
    except (OSError, UnicodeError):
        return _withhold_structure(value)
    return redact_structure(value, environment, extra_values)


def _withhold_structure(value: object) -> object:
    if isinstance(value, str):
        return _WITHHELD
    if isinstance(value, Mapping):
        return {key: _withhold_structure(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_withhold_structure(item) for item in value]
    return value


def scrub_failure_text(
    text: str,
    env_path: str | Path,
    extra_values: Iterable[str] = (),
) -> str:
    """실패 원문에서 비밀을 가린다. `.env`를 읽지 못하면 원문 대신 그 사실만 돌려준다."""

    try:
        environment = load_secret_environment(env_path)
    except (OSError, UnicodeError):
        return _WITHHELD
    return redact_secret_text(text, environment, extra_values)
