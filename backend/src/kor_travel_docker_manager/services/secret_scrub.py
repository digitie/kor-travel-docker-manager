"""실패 출력과 조회 응답에서 비밀을 가리는 스크러버 하나(ADR-51 잃는 보장 G).

실패는 원인 원문을 싣고 다니고, 비밀은 출력 경계(CLI stderr, API 오류 본문, M05 driver)에서 한 번
가린다. 규칙은 셋이다.

1. 민감한 key 이름(아래 조각)으로 선언된 값 — 길이 4 이상. SQL 리터럴로 들어간 `'`→`''` 변형도 함께
   가린다(psql의 `LINE 1:` 에코).
2. 값 안의 URL userinfo 비밀번호(`scheme://user:password@`). DSN 이름은 위 목록에 걸리지 않는다.
3. 호출자가 넘기는 추가 값(M05가 실행마다 만드는 비밀처럼 `.env`에 없는 것).

원천은 프로세스 환경과 `.env`다. `.env`를 읽지 못하면 원문을 내지 않는다. 이 규칙에 걸리지 않는
비밀 — `.env`·환경에 없거나, 이름이 목록에 걸리지 않거나, 변형(JSON escape, percent-encoding, base64,
compose `$$`)되었거나, 4자 미만인 값 — 은 빠져나갈 수 있다. 그것이 잃는 보장 G의 남은 모양이다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

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
    environment: Mapping[str, str | None],
    extra_values: Iterable[str] = (),
) -> str:
    """임의 텍스트에서 비밀 값과 URL userinfo 비밀번호를 가린다(규칙은 모듈 docstring)."""

    candidates = {
        value
        for key, value in environment.items()
        if value and len(value) >= _MINIMUM_SECRET_LENGTH and is_sensitive_key(key)
    }
    candidates.update(
        value for value in extra_values if value and len(value) >= _MINIMUM_SECRET_LENGTH
    )
    variants = candidates | {value.replace("'", "''") for value in candidates if "'" in value}
    for secret in sorted(variants, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return redact_value_credentials(text)


def load_secret_environment(env_path: str | Path) -> dict[str, str | None]:
    """프로세스 환경과 `.env`를 합친다.

    `.env`가 없으면 가릴 것도 없다(개발 checkout). 있는데 읽지 못하면(권한, 인코딩) 예외를 그대로
    올린다 — 호출자는 원문을 내지 않는다. 보간된 값도 함께 본다(`A_PASSWORD=${B}`).
    """

    return {**os.environ, **dotenv_values(env_path)}


def scrub_failure_text(
    text: str,
    env_path: str | Path,
    extra_values: Iterable[str] = (),
) -> str:
    """실패 원문에서 비밀을 가린다. `.env`를 읽지 못하면 원문 대신 그 사실만 돌려준다."""

    try:
        environment = load_secret_environment(env_path)
    except (OSError, UnicodeError):
        return "failure detail withheld: .env could not be read for redaction"
    return redact_secret_text(text, environment, extra_values)
